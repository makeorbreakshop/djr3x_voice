//! The vision service: a capture thread (camera read + face recognition, blocking) feeding an
//! async task that runs [`Presence`], publishes `vision.*` on the bus and asks Claude for scene
//! descriptions. [`Vision`] is the handle: camera list/status/select, the latest scene for the
//! brain's turn context, and [`Vision::analyze_scene`] for the `analyze_scene` tool.

use std::sync::mpsc as std_mpsc;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use base64::Engine as _;
use r3x_bus::Bus;
use r3x_contracts::{Event, ServiceStatus, Source, VisionEvent};
use r3x_llm::{LlmClient, Message, MessagesRequest};
use serde::Serialize;
use tokio::sync::mpsc;
use tokio::task::JoinHandle;

use crate::cadence::{Cadence, Seen, Thumb};
use crate::camera::{pick, Camera, CameraInfo, FrameSource};
use crate::face::{align, iou, FaceEngine};
use crate::frame::Frame;
use crate::gallery::Gallery;
use crate::presence::{Presence, PresenceConfig, Transition};
use crate::VisionError;

/// One analysed frame -> who (if anyone enrolled) is in it, with a confidence.
pub trait Recognizer: Send {
    fn recognize(&mut self, frame: &Frame) -> Option<(String, f32)>;
    /// Centre of the largest face in the last recognised frame, 0..1 of width and height
    /// (enrolled or not): the vision gaze target.
    fn face_centre(&self) -> Option<[f32; 2]> {
        None
    }
}

/// The real recognizer: largest face, nearest gallery match above the threshold.
///
/// The embedding (the costly half) runs when a face appears or tracking is lost; while the
/// largest face stays where it was (box overlap, [`TRACK_IOU`]) the identity carries over,
/// re-checked every [`REVERIFY`].
pub struct GalleryRecognizer {
    pub engine: FaceEngine,
    pub gallery: Gallery,
    last_face: Option<[f32; 2]>,
    track: Option<Track>,
    /// Embeddings computed (for measurement and tests).
    pub embeddings: u64,
}

struct Track {
    bbox: [f32; 4],
    who: Option<(String, f32)>,
    at: Instant,
}

/// Minimum box overlap between consecutive analyses to count as the same face.
pub const TRACK_IOU: f32 = 0.3;
/// A tracked identity is re-embedded at least this often.
pub const REVERIFY: Duration = Duration::from_secs(10);

impl GalleryRecognizer {
    pub fn new(engine: FaceEngine, gallery: Gallery) -> Self {
        Self { engine, gallery, last_face: None, track: None, embeddings: 0 }
    }
}

impl Recognizer for GalleryRecognizer {
    fn face_centre(&self) -> Option<[f32; 2]> {
        self.last_face
    }

    fn recognize(&mut self, frame: &Frame) -> Option<(String, f32)> {
        self.last_face = None;
        let faces = match self.engine.detector.detect(frame) {
            Ok(f) => f,
            Err(e) => {
                tracing::warn!("face detection failed: {e}");
                self.track = None;
                return None;
            }
        };
        let Some(f) = faces.into_iter().max_by(|a, b| a.area().total_cmp(&b.area())) else {
            self.track = None;
            return None;
        };
        let [x, y, w, h] = f.bbox;
        self.last_face = Some([(x + w / 2.0) / frame.width as f32, (y + h / 2.0) / frame.height as f32]);
        let now = Instant::now();
        if let Some(t) = self.track.as_mut().filter(|t| iou(&t.bbox, &f.bbox) >= TRACK_IOU && now.duration_since(t.at) < REVERIFY) {
            t.bbox = f.bbox;
            return t.who.clone();
        }
        self.embeddings += 1;
        let who = match self.engine.embedder.embed(&align(frame, &f)) {
            Ok(e) => self.gallery.identify(&e).map(|m| (m.name, m.similarity)),
            Err(e) => {
                tracing::warn!("face embedding failed: {e}");
                self.track = None;
                return None;
            }
        };
        self.track = Some(Track { bbox: f.bbox, who: who.clone(), at: now });
        who
    }
}

#[derive(Debug, Clone)]
pub struct VisionConfig {
    pub fps: f64,
    pub presence: PresenceConfig,
    /// `R3X_CAMERA_INDEX`; otherwise the first non-Continuity camera.
    pub camera: Option<u32>,
    pub jpeg_quality: u8,
    /// Scene photos are scaled to at most this wide before they go to Claude.
    pub scene_max_width: u32,
    /// `analyze_scene` refuses a frame older than this.
    pub frame_max_age: Duration,
    pub scene_max_tokens: u32,
    /// Horizontal field of view (deg), for the face -> gaze mapping (`R3X_CAMERA_HFOV`).
    pub hfov_deg: f64,
    /// A camera that failed to open (busy, unplugged) is tried again this often.
    pub camera_retry: Duration,
}

impl Default for VisionConfig {
    fn default() -> Self {
        Self { fps: 5.0, presence: PresenceConfig::default(), camera: None, jpeg_quality: 85, scene_max_width: 768, frame_max_age: Duration::from_secs(2), scene_max_tokens: 200, hfov_deg: 70.0, camera_retry: Duration::from_secs(10) }
    }
}

impl VisionConfig {
    /// `R3X_CAMERA_INDEX`, `R3X_VISION_FPS`, `R3X_VISION_SCENES=0` (no automatic, paid, scene
    /// captures; `analyze_scene` still works).
    pub fn from_env() -> Self {
        let mut c = Self::default();
        let get = |k: &str| std::env::var(k).ok().filter(|v| !v.is_empty());
        c.camera = get("R3X_CAMERA_INDEX").and_then(|v| v.parse().ok());
        if let Some(f) = get("R3X_VISION_FPS").and_then(|v| v.parse().ok()) {
            c.fps = f;
        }
        if let Some(f) = get("R3X_CAMERA_HFOV").and_then(|v| v.parse().ok()) {
            c.hfov_deg = f;
        }
        c.presence.scenes = get("R3X_VISION_SCENES").is_none_or(|v| !matches!(v.as_str(), "0" | "false" | "no" | "off"));
        c
    }
}

#[derive(Debug, Clone, Default, Serialize)]
pub struct CameraStatus {
    pub cameras: Vec<CameraInfo>,
    pub selected: Option<u32>,
    pub streaming: bool,
    pub frames: u64,
    /// Frames that went through face recognition (the rest only updated the latest frame).
    pub analysed: u64,
    pub error: Option<String>,
    pub person: Option<String>,
    #[serde(skip)]
    present: bool,
}

enum Control {
    Select(u32),
    /// `true` closes the camera (the ffmpeg child goes with it) until `false` reopens it.
    Pause(bool),
    Stop,
}

/// Who recognition matched (name, similarity) and the largest face's centre.
type Analysis = (Option<(String, f32)>, Option<[f32; 2]>);

/// A captured frame, and what recognition saw in it if it was analysed (see `cadence`).
struct Observation {
    frame: Arc<Frame>,
    analysed: Option<Analysis>,
}

/// A face centre (0..1 of the frame) -> a gaze target (pan, tilt) in degrees, for a camera
/// that looks where R3X faces at rest: a face right of centre in the image is on R3X's right
/// (negative pan); lower in the image tilts the head down (positive tilt). Pinhole model.
pub fn face_to_gaze(centre: [f32; 2], width: u32, height: u32, hfov_deg: f64) -> (f64, f64) {
    let half = (hfov_deg / 2.0).to_radians().tan();
    let x = (f64::from(centre[0]) - 0.5) * 2.0 * half;
    let y = (f64::from(centre[1]) - 0.5) * 2.0 * half * f64::from(height) / f64::from(width.max(1));
    (-x.atan().to_degrees(), y.atan().to_degrees())
}

struct Inner {
    bus: Bus,
    llm: Option<LlmClient>,
    cfg: VisionConfig,
    latest: Mutex<Option<(Arc<Frame>, Instant)>>,
    /// Latest description and when (unix seconds, the clock `Memory::turn_context` uses).
    scene: Mutex<Option<(String, f64)>>,
    status: Arc<Mutex<CameraStatus>>,
    control: Mutex<std_mpsc::Sender<Control>>,
    /// The panel's Vision switch; the presence loop follows it.
    enabled: tokio::sync::watch::Sender<bool>,
}

#[derive(Clone)]
pub struct Vision {
    inner: Arc<Inner>,
}

fn unix_now() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map_or(0.0, |d| d.as_secs_f64())
}

fn lock<T>(m: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|p| p.into_inner())
}

impl Vision {
    /// Start the capture thread and the presence task. `llm: None` = no scene descriptions.
    pub fn spawn(bus: &Bus, cfg: VisionConfig, camera: Box<dyn Camera>, recognizer: Box<dyn Recognizer>, llm: Option<LlmClient>) -> (Vision, JoinHandle<()>) {
        let (ctl_tx, ctl_rx) = std_mpsc::channel();
        let (obs_tx, obs_rx) = mpsc::channel::<Observation>(2);
        let status = Arc::new(Mutex::new(CameraStatus::default()));
        let inner = Arc::new(Inner {
            bus: bus.clone(),
            llm,
            cfg: cfg.clone(),
            latest: Mutex::new(None),
            scene: Mutex::new(None),
            status: status.clone(),
            control: Mutex::new(ctl_tx),
            enabled: tokio::sync::watch::Sender::new(true),
        });
        let capture_cfg = cfg.clone();
        std::thread::Builder::new()
            .name("r3x-vision-capture".into())
            .spawn(move || capture_loop(camera, recognizer, &capture_cfg, ctl_rx, obs_tx, status))
            .expect("spawn vision capture thread");
        let v = Vision { inner };
        let task = tokio::spawn(v.clone().presence_loop(obs_rx));
        r3x_ops::report(bus, "vision", ServiceStatus::Running, None);
        (v, task)
    }

    pub fn status(&self) -> CameraStatus {
        lock(&self.inner.status).clone()
    }

    pub fn cameras(&self) -> Vec<CameraInfo> {
        self.status().cameras
    }

    /// Switch cameras (the capture thread reopens). Unknown indexes are refused.
    pub fn select_camera(&self, index: u32) -> Result<(), VisionError> {
        if !self.cameras().iter().any(|c| c.index == index) {
            return Err(VisionError::Camera(format!("no camera {index}")));
        }
        lock(&self.inner.control).send(Control::Select(index)).map_err(|_| VisionError::Camera("capture stopped".into()))
    }

    /// The Vision switch: off closes the camera, ends the current visit, forgets the last
    /// frame and refuses `analyze_scene` (no paid call); on reopens the selected camera.
    pub fn set_enabled(&self, on: bool) {
        if *self.inner.enabled.borrow() == on {
            return;
        }
        if !on {
            *lock(&self.inner.latest) = None;
        }
        let _ = lock(&self.inner.control).send(Control::Pause(!on));
        self.inner.enabled.send_replace(on);
        tracing::info!("vision {}", if on { "on" } else { "off" });
        r3x_ops::report(&self.inner.bus, "vision", if on { ServiceStatus::Running } else { ServiceStatus::Stopped }, (!on).then(|| "switched off".into()));
    }

    pub fn enabled(&self) -> bool {
        *self.inner.enabled.borrow()
    }

    pub fn shutdown(&self) {
        let _ = lock(&self.inner.control).send(Control::Stop);
    }

    /// The last scene description and when (unix s), for `Memory::turn_context(scene, ..)`.
    pub fn scene(&self) -> Option<(String, f64)> {
        lock(&self.inner.scene).clone()
    }

    /// CantinaOS `camera list|status|select N`, as a console reply.
    pub fn console(&self, line: &str) -> Option<String> {
        let args: Vec<&str> = line.split_whitespace().collect();
        let s = self.status();
        match args.as_slice() {
            ["camera", "list"] => Some(
                s.cameras
                    .iter()
                    .map(|c| {
                        let tag = if Some(c.index) == s.selected { " (selected)" } else if c.is_continuity() { " (skipped: Continuity)" } else { "" };
                        format!("[{}] {}{tag}", c.index, c.name)
                    })
                    .collect::<Vec<_>>()
                    .join("\n"),
            )
            .map(|t| if t.is_empty() { "No cameras found".into() } else { t }),
            ["camera", "status"] => Some(format!(
                "camera: {} | streaming: {} | frames: {} | person: {}{}",
                s.selected.map_or("none".into(), |i| i.to_string()),
                s.streaming,
                s.frames,
                s.person.as_deref().unwrap_or("none"),
                s.error.map(|e| format!(" | error: {e}")).unwrap_or_default()
            )),
            ["camera", "select", i] => Some(match i.parse::<u32>().map_err(|_| VisionError::Camera(format!("bad index {i}"))).and_then(|i| self.select_camera(i)) {
                Ok(()) => format!("Switching to camera {i}"),
                Err(e) => e.to_string(),
            }),
            ["camera", ..] => Some("usage: camera list | camera status | camera select <index>".into()),
            ["vision", "on"] => {
                self.set_enabled(true);
                Some("Vision on".into())
            }
            ["vision", "off"] => {
                self.set_enabled(false);
                Some("Vision off: camera closed, no scene photos".into())
            }
            ["vision", "status"] => Some(format!(
                "vision: {} | camera streaming: {} | person: {}",
                if self.enabled() { "on" } else { "off" },
                s.streaming,
                s.person.as_deref().unwrap_or("none")
            )),
            ["vision", ..] => Some("usage: vision on | vision off | vision status".into()),
            _ => None,
        }
    }

    /// The `analyze_scene` tool: describe the current frame, answering `question`. Publishes
    /// `vision.scene_captured` (reason `on_demand`) on `turn`. This is what `r3x-brain`'s
    /// `analyze_scene` handler should await instead of "Vision is not available yet".
    pub async fn analyze_scene(&self, question: &str, turn: Option<String>) -> Result<String, VisionError> {
        if !self.enabled() {
            return Err(VisionError::Camera("vision is switched off".into()));
        }
        let frame = {
            let l = lock(&self.inner.latest);
            match &*l {
                Some((f, at)) if at.elapsed() <= self.inner.cfg.frame_max_age => f.clone(),
                _ => return Err(VisionError::Camera("no recent camera frame".into())),
            }
        };
        let q = if question.trim().is_empty() { "What do you see?" } else { question };
        let description = self.describe(frame, q).await?;
        self.publish_scene(turn, &description, "on_demand", None);
        Ok(description)
    }

    async fn describe(&self, frame: Arc<Frame>, prompt: &str) -> Result<String, VisionError> {
        let llm = self.inner.llm.as_ref().ok_or(VisionError::Llm(r3x_llm::LlmError::Unavailable))?;
        let (q, w) = (self.inner.cfg.jpeg_quality, self.inner.cfg.scene_max_width);
        let jpeg = tokio::task::spawn_blocking(move || frame.to_jpeg_scaled(q, w)).await.map_err(|e| VisionError::Image(e.to_string()))??;
        let b64 = base64::engine::general_purpose::STANDARD.encode(jpeg);
        let req = MessagesRequest::new(self.inner.cfg.scene_max_tokens).messages(vec![Message::user_image("image/jpeg", b64, prompt)]);
        let t0 = Instant::now();
        let reply = llm.create(&req).await?;
        let text = description(&reply);
        match &text {
            Ok(_) => tracing::debug!(ms = t0.elapsed().as_millis() as u64, "scene described"),
            Err(e) => tracing::warn!(ms = t0.elapsed().as_millis() as u64, "scene not described: {e}"),
        }
        text
    }

    fn publish_scene(&self, turn: Option<String>, description: &str, reason: &str, person: Option<String>) {
        *lock(&self.inner.scene) = Some((description.to_string(), unix_now()));
        let e = VisionEvent::SceneCaptured { description: description.into(), reason: reason.into(), person };
        self.inner.bus.publish(Source::System, turn, Event::Vision(e));
    }

    async fn presence_loop(self, mut rx: mpsc::Receiver<Observation>) {
        let mut presence = Presence::new(self.inner.cfg.presence.clone());
        let clock = self.inner.bus.clock();
        let mut gaze: Option<(f64, f64)> = None;
        let mut enabled = self.inner.enabled.subscribe();
        loop {
            let obs = tokio::select! {
                o = rx.recv() => o,
                r = enabled.changed() => {
                    if r.is_err() { break }
                    if !*enabled.borrow_and_update() {
                        // Switched off: nobody is in view any more, as far as anyone knows.
                        for t in presence.clear(clock.t_mono()) {
                            if let Transition::Exited { name, duration_s } = t {
                                tracing::info!("person exited: {name} ({duration_s:.1}s, vision off)");
                                self.inner.bus.publish(Source::System, None, Event::Vision(VisionEvent::PersonExited { name, duration_s }));
                            }
                        }
                        if gaze.take().is_some() {
                            self.inner.bus.publish(Source::System, None, Event::Vision(VisionEvent::FaceLost));
                        }
                        let mut st = lock(&self.inner.status);
                        (st.person, st.present) = (None, false);
                    }
                    continue;
                }
            };
            let Some(Observation { frame, analysed }) = obs else { break };
            if !*enabled.borrow() {
                continue; // a frame read just before the pause
            }
            *lock(&self.inner.latest) = Some((frame.clone(), Instant::now()));
            let Some((seen, face)) = analysed else { continue };
            let at = face.map(|c| face_to_gaze(c, frame.width, frame.height, self.inner.cfg.hfov_deg));
            let moved = match (gaze, at) {
                (Some(a), Some(b)) => (a.0 - b.0).abs().max((a.1 - b.1).abs()) > 1.0,
                (a, b) => a.is_some() != b.is_some(),
            };
            if moved {
                gaze = at;
                let e = match at {
                    Some((pan, tilt)) => VisionEvent::FaceAt { pan, tilt },
                    None => VisionEvent::FaceLost,
                };
                self.inner.bus.publish(Source::System, None, Event::Vision(e));
            }
            let now = clock.t_mono();
            let step = presence.observe(now, seen.as_ref().map(|(n, c)| (n.as_str(), *c)));
            for t in step.transitions {
                let e = match t {
                    Transition::Detected { name, confidence } => {
                        tracing::info!("person detected: {name} ({confidence:.2})");
                        VisionEvent::PersonDetected { name, confidence: f64::from(confidence) }
                    }
                    Transition::Exited { name, duration_s } => {
                        tracing::info!("person exited: {name} ({duration_s:.1}s)");
                        VisionEvent::PersonExited { name, duration_s }
                    }
                };
                self.inner.bus.publish(Source::System, None, Event::Vision(e));
            }
            let person = presence.current().map(|(n, c)| (n.to_string(), c));
            {
                let mut st = lock(&self.inner.status);
                st.person = person.as_ref().map(|p| p.0.clone());
                st.present = person.is_some();
            }
            if let (Some(reason), true) = (step.capture, self.inner.llm.is_some()) {
                presence.captured(now, person.as_ref().map(|p| p.0.as_str()));
                let prompt = match &person {
                    Some((n, c)) => format!(
                        "Describe the scene you see. Face recognition detected {n} (confidence: {c:.2}). Focus on the environment, objects, and activities. Keep it concise (under 100 words)."
                    ),
                    None => "Describe the scene you see. No known person detected in frame. Focus on the environment and any notable objects. Keep it concise (under 100 words).".into(),
                };
                let me = self.clone();
                tokio::spawn(async move {
                    tracing::info!("capturing scene ({reason})");
                    match me.describe(frame, &prompt).await {
                        Ok(d) => me.publish_scene(None, &d, &reason, person.map(|p| p.0)),
                        Err(e) => tracing::warn!("scene capture failed: {e}"),
                    }
                });
            }
        }
        r3x_ops::report(&self.inner.bus, "vision", ServiceStatus::Stopped, None);
    }
}

fn capture_loop(
    camera: Box<dyn Camera>,
    mut recognizer: Box<dyn Recognizer>,
    cfg: &VisionConfig,
    ctl: std_mpsc::Receiver<Control>,
    tx: mpsc::Sender<Observation>,
    status: Arc<Mutex<CameraStatus>>,
) {
    let (fps, initial, retry) = (cfg.fps, cfg.camera, cfg.camera_retry);
    let cams = camera.list();
    let mut want = pick(&cams, initial);
    {
        let mut s = lock(&status);
        s.cameras = cams;
        if want.is_none() {
            s.error = Some("no physical camera (Continuity cameras are skipped)".into());
        }
    }
    let period = if fps > 0.0 { Duration::from_secs_f64(1.0 / fps) } else { Duration::ZERO };
    let mut cadence = Cadence::new(fps);
    let mut source: Option<Box<dyn FrameSource>> = None;
    let mut next = Instant::now();
    let mut paused = false;
    // After a failed open: when to try the same camera again.
    let mut retry_at: Option<Instant> = None;
    loop {
        match ctl.try_recv() {
            Ok(Control::Select(i)) => {
                source = None;
                want = Some(i);
            }
            Ok(Control::Pause(p)) => {
                paused = p;
                if p {
                    source = None; // drops the capture: the ffmpeg child is killed
                    let mut st = lock(&status);
                    st.streaming = false;
                    tracing::info!("camera closed (vision off)");
                }
            }
            Ok(Control::Stop) | Err(std_mpsc::TryRecvError::Disconnected) => break,
            Err(std_mpsc::TryRecvError::Empty) => {}
        }
        if paused {
            match ctl.recv_timeout(Duration::from_millis(500)) {
                Ok(Control::Pause(p)) => paused = p,
                Ok(Control::Select(i)) => want = Some(i),
                Ok(Control::Stop) | Err(std_mpsc::RecvTimeoutError::Disconnected) => break,
                Err(std_mpsc::RecvTimeoutError::Timeout) => {}
            }
            continue;
        }
        let Some(src) = source.as_mut() else {
            if let Some(at) = retry_at.filter(|at| *at > Instant::now()) {
                match ctl.recv_timeout((at - Instant::now()).min(Duration::from_millis(500))) {
                    Ok(Control::Select(i)) => (want, retry_at) = (Some(i), None),
                    Ok(Control::Pause(p)) => paused = p,
                    Ok(Control::Stop) | Err(std_mpsc::RecvTimeoutError::Disconnected) => break,
                    Err(std_mpsc::RecvTimeoutError::Timeout) => {}
                }
                continue;
            }
            retry_at = None;
            match want {
                Some(i) => match camera.open(i, fps) {
                    Ok(s) => {
                        source = Some(s);
                        let mut st = lock(&status);
                        (st.selected, st.streaming, st.error) = (Some(i), true, None);
                        tracing::info!("camera {i} open");
                    }
                    Err(e) => {
                        tracing::warn!("{e}; retrying in {:.0} s", retry.as_secs_f64());
                        lock(&status).error = Some(e.to_string());
                        retry_at = Some(Instant::now() + retry);
                    }
                },
                // Nothing to read: wait for a select (or stop).
                None => match ctl.recv_timeout(Duration::from_millis(500)) {
                    Ok(Control::Select(i)) => want = Some(i),
                    Ok(Control::Pause(p)) => paused = p,
                    Ok(Control::Stop) | Err(std_mpsc::RecvTimeoutError::Disconnected) => break,
                    Err(std_mpsc::RecvTimeoutError::Timeout) => {}
                },
            }
            continue;
        };
        match src.next_frame() {
            Ok(Some(frame)) => {
                let now = Instant::now();
                let thumb = Thumb::of(&frame);
                let analysed = cadence.due(now, &thumb).then(|| {
                    let seen = recognizer.recognize(&frame);
                    let face = recognizer.face_centre();
                    // Presence (the async side) says who is present; this frame's own result is
                    // the best guess until it has seen it.
                    let present = seen.is_some() || lock(&status).present;
                    cadence.analysed(now, thumb, Seen { face, name: seen.as_ref().map(|s| s.0.clone()) }, present);
                    (seen, face)
                });
                {
                    let mut st = lock(&status);
                    st.frames += 1;
                    st.analysed += u64::from(analysed.is_some());
                }
                if tx.blocking_send(Observation { frame: Arc::new(frame), analysed }).is_err() {
                    break;
                }
                next += period;
                let now = Instant::now();
                if next > now {
                    std::thread::sleep(next - now);
                } else {
                    next = now;
                }
            }
            Ok(None) | Err(_) => {
                let mut st = lock(&status);
                (st.streaming, st.error) = (false, Some("camera stream ended".into()));
                tracing::warn!("camera stream ended");
                source = None;
                want = None;
            }
        }
    }
    lock(&status).streaming = false;
}

/// Bus -> memory: `vision.person_detected|exited` -> `Memory::person_detected|exited`
/// (the brain wiring `r3x-memory` documents).
pub fn link_memory(bus: &Bus, memory: Arc<r3x_memory::Memory>) -> JoinHandle<()> {
    let mut rx = bus.subscribe(r3x_contracts::Domain::Vision);
    tokio::spawn(async move {
        while let Some(msg) = rx.recv().await {
            let r3x_bus::Received::Message(env) = msg else { continue };
            let r3x_contracts::Body::Event(Event::Vision(e)) = &env.body else { continue };
            let r = match e {
                VisionEvent::PersonDetected { name, .. } => memory.person_detected(name).map(|_| ()),
                VisionEvent::PersonExited { name, .. } => memory.person_exited(name),
                VisionEvent::SceneCaptured { .. } | VisionEvent::FaceAt { .. } | VisionEvent::FaceLost => Ok(()),
            };
            if let Err(e) = r {
                tracing::warn!("memory: {e}");
            }
        }
    })
}

/// A scene reply's text, or why there is none (a refusal and its category, a cut-off): an
/// empty "success" left the brain silent and the log blank.
fn description(reply: &r3x_llm::FinalMessage) -> Result<String, VisionError> {
    let text = reply.text().trim().to_string();
    if !text.is_empty() {
        return Ok(text);
    }
    let why = match reply.stop_reason.as_deref() {
        Some("refusal") => {
            let category = reply.stop_details.as_ref().and_then(|d| d["category"].as_str()).unwrap_or("unspecified");
            format!("Claude declined to describe the image (refusal: {category})")
        }
        Some(r) => format!("Claude returned no description (stop_reason: {r})"),
        None => "Claude returned no description".to_string(),
    };
    Err(VisionError::Model(why))
}

#[cfg(test)]
mod tests {
    use super::{description, face_to_gaze};
    use r3x_llm::FinalMessage;

    fn reply(v: serde_json::Value) -> FinalMessage {
        serde_json::from_value(v).unwrap()
    }

    /// A reply with no text is a failed look, never an empty "success": the 2026-09-30 dragon
    /// turns got `description: ""` four times with nothing in the log to say why.
    #[test]
    fn a_reply_without_text_is_an_error_that_says_why() {
        let ok = reply(serde_json::json!({"content": [{"type": "text", "text": "  A green plush dragon.  "}], "stop_reason": "end_turn"}));
        assert_eq!(description(&ok).unwrap(), "A green plush dragon.");

        let refused = reply(serde_json::json!({"content": [], "stop_reason": "refusal",
            "stop_details": {"type": "refusal", "category": "general_harms", "explanation": "..."}}));
        let e = description(&refused).unwrap_err().to_string();
        assert!(e.contains("declined") && e.contains("general_harms"), "{e}");

        let cut = reply(serde_json::json!({"content": [{"type": "thinking", "thinking": ""}], "stop_reason": "max_tokens"}));
        let e = description(&cut).unwrap_err().to_string();
        assert!(e.contains("max_tokens"), "{e}");
    }

    #[test]
    fn face_centre_maps_to_gaze_through_the_fov() {
        let (pan, tilt) = face_to_gaze([0.5, 0.5], 1280, 720, 70.0);
        assert!(pan.abs() < 1e-9 && tilt.abs() < 1e-9, "centre = straight ahead");
        let (pan, _) = face_to_gaze([1.0, 0.5], 1280, 720, 70.0);
        assert!((pan + 35.0).abs() < 1e-9, "the image's right edge is half the FOV to R3X's right");
        let (_, tilt) = face_to_gaze([0.5, 1.0], 1280, 720, 70.0);
        let vhalf = ((35f64).to_radians().tan() * 720.0 / 1280.0).atan().to_degrees();
        assert!((tilt - vhalf).abs() < 1e-9 && tilt > 0.0, "lower in the frame tilts down");
    }
}

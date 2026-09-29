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

use crate::camera::{pick, Camera, CameraInfo, FrameSource};
use crate::face::FaceEngine;
use crate::frame::Frame;
use crate::gallery::Gallery;
use crate::presence::{Presence, PresenceConfig, Transition};
use crate::VisionError;

/// One analysed frame -> who (if anyone enrolled) is in it, with a confidence.
pub trait Recognizer: Send {
    fn recognize(&mut self, frame: &Frame) -> Option<(String, f32)>;
}

/// The real recognizer: largest face, nearest gallery match above the threshold.
pub struct GalleryRecognizer {
    pub engine: FaceEngine,
    pub gallery: Gallery,
}

impl Recognizer for GalleryRecognizer {
    fn recognize(&mut self, frame: &Frame) -> Option<(String, f32)> {
        match self.engine.largest(frame) {
            Ok(Some((_, e))) => self.gallery.identify(&e).map(|m| (m.name, m.similarity)),
            Ok(None) => None,
            Err(e) => {
                tracing::warn!("face recognition failed: {e}");
                None
            }
        }
    }
}

#[derive(Debug, Clone)]
pub struct VisionConfig {
    pub fps: f64,
    pub presence: PresenceConfig,
    /// `R3X_CAMERA_INDEX`; otherwise the first non-Continuity camera.
    pub camera: Option<u32>,
    pub jpeg_quality: u8,
    /// `analyze_scene` refuses a frame older than this.
    pub frame_max_age: Duration,
    pub scene_max_tokens: u32,
}

impl Default for VisionConfig {
    fn default() -> Self {
        Self { fps: 5.0, presence: PresenceConfig::default(), camera: None, jpeg_quality: 85, frame_max_age: Duration::from_secs(2), scene_max_tokens: 200 }
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
    pub error: Option<String>,
    pub person: Option<String>,
}

enum Control {
    Select(u32),
    Stop,
}

type Observation = (Arc<Frame>, Option<(String, f32)>);

struct Inner {
    bus: Bus,
    llm: Option<LlmClient>,
    cfg: VisionConfig,
    latest: Mutex<Option<(Arc<Frame>, Instant)>>,
    /// Latest description and when (unix seconds, the clock `Memory::turn_context` uses).
    scene: Mutex<Option<(String, f64)>>,
    status: Arc<Mutex<CameraStatus>>,
    control: Mutex<std_mpsc::Sender<Control>>,
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
        });
        let (fps, initial) = (cfg.fps, cfg.camera);
        std::thread::Builder::new()
            .name("r3x-vision-capture".into())
            .spawn(move || capture_loop(camera, recognizer, fps, initial, ctl_rx, obs_tx, status))
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
            _ => None,
        }
    }

    /// The `analyze_scene` tool: describe the current frame, answering `question`. Publishes
    /// `vision.scene_captured` (reason `on_demand`) on `turn`. This is what `r3x-brain`'s
    /// `analyze_scene` handler should await instead of "Vision is not available yet".
    pub async fn analyze_scene(&self, question: &str, turn: Option<String>) -> Result<String, VisionError> {
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
        let q = self.inner.cfg.jpeg_quality;
        let jpeg = tokio::task::spawn_blocking(move || frame.to_jpeg(q)).await.map_err(|e| VisionError::Image(e.to_string()))??;
        let b64 = base64::engine::general_purpose::STANDARD.encode(jpeg);
        let req = MessagesRequest::new(self.inner.cfg.scene_max_tokens).messages(vec![Message::user_image("image/jpeg", b64, prompt)]);
        let t0 = Instant::now();
        let text = llm.create(&req).await?.text();
        tracing::debug!(ms = t0.elapsed().as_millis() as u64, "scene described");
        Ok(text)
    }

    fn publish_scene(&self, turn: Option<String>, description: &str, reason: &str, person: Option<String>) {
        *lock(&self.inner.scene) = Some((description.to_string(), unix_now()));
        let e = VisionEvent::SceneCaptured { description: description.into(), reason: reason.into(), person };
        self.inner.bus.publish(Source::System, turn, Event::Vision(e));
    }

    async fn presence_loop(self, mut rx: mpsc::Receiver<Observation>) {
        let mut presence = Presence::new(self.inner.cfg.presence.clone());
        let clock = self.inner.bus.clock();
        while let Some((frame, seen)) = rx.recv().await {
            *lock(&self.inner.latest) = Some((frame.clone(), Instant::now()));
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
            lock(&self.inner.status).person = person.as_ref().map(|p| p.0.clone());
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
    fps: f64,
    initial: Option<u32>,
    ctl: std_mpsc::Receiver<Control>,
    tx: mpsc::Sender<Observation>,
    status: Arc<Mutex<CameraStatus>>,
) {
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
    let mut source: Option<Box<dyn FrameSource>> = None;
    let mut next = Instant::now();
    loop {
        match ctl.try_recv() {
            Ok(Control::Select(i)) => {
                source = None;
                want = Some(i);
            }
            Ok(Control::Stop) | Err(std_mpsc::TryRecvError::Disconnected) => break,
            Err(std_mpsc::TryRecvError::Empty) => {}
        }
        let Some(src) = source.as_mut() else {
            match want {
                Some(i) => match camera.open(i, fps) {
                    Ok(s) => {
                        source = Some(s);
                        let mut st = lock(&status);
                        (st.selected, st.streaming, st.error) = (Some(i), true, None);
                        tracing::info!("camera {i} open");
                    }
                    Err(e) => {
                        tracing::warn!("camera {i}: {e}");
                        lock(&status).error = Some(e.to_string());
                        want = None;
                    }
                },
                // Nothing to read: wait for a select (or stop).
                None => match ctl.recv_timeout(Duration::from_millis(500)) {
                    Ok(Control::Select(i)) => want = Some(i),
                    Ok(Control::Stop) | Err(std_mpsc::RecvTimeoutError::Disconnected) => break,
                    Err(std_mpsc::RecvTimeoutError::Timeout) => {}
                },
            }
            continue;
        };
        match src.next_frame() {
            Ok(Some(frame)) => {
                let seen = recognizer.recognize(&frame);
                lock(&status).frames += 1;
                if tx.blocking_send((Arc::new(frame), seen)).is_err() {
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
                VisionEvent::SceneCaptured { .. } => Ok(()),
            };
            if let Err(e) = r {
                tracing::warn!("memory: {e}");
            }
        }
    })
}

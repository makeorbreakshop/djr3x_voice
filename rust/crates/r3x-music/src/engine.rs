//! The music engine: one actor task owning what plays on the mixer's music bus.
//!
//! Behaviour is CantinaOS `MusicControllerService`'s (plan §7a) with the §7b fixes:
//! `TRACK_ENDING_SOON` comes from the playback position, ducking is a short ramp, crossfades
//! are sample-accurate equal-power, and `next` works outside DJ mode (music owns it).

use std::path::PathBuf;
use std::sync::{Arc, RwLock};
use std::time::{Duration, Instant};

use r3x_audio::mixer::{BusId, MixerHandle};
use r3x_audio::music::MusicStream;
use r3x_beats::BeatInfo;
use tokio::sync::{broadcast, mpsc, oneshot, watch};

use crate::library::{LibTrack, Library};
use crate::semantic::{looks_semantic, parse_semantic_request, Search};

#[derive(Debug, Clone)]
pub struct EngineConfig {
    /// `TRACK_ENDING_SOON` fires this long before the end (CantinaOS: 30 s).
    pub ending_threshold_s: f64,
    /// Crossfade used when a play request arrives in DJ mode (CantinaOS: 3 s; the DJ's own
    /// transitions pass 8 s).
    pub crossfade_s: f64,
    /// Default duck level when a request names none (profile `audio.ducking`).
    pub duck_level: f32,
    pub duck_ramp_ms: f64,
    /// Fade on stop (click-free; CantinaOS stopped dead).
    pub stop_fade_ms: f64,
    /// How often the position is sampled for `TRACK_ENDING_SOON`, end detection and state.
    pub tick: Duration,
}

impl Default for EngineConfig {
    fn default() -> Self {
        Self { ending_threshold_s: 30.0, crossfade_s: 3.0, duck_level: 0.5, duck_ramp_ms: 80.0, stop_fade_ms: 150.0, tick: Duration::from_millis(250) }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum EngineEvent {
    /// `track` is now the current track (a play, or the incoming side of a crossfade).
    Started { track: LibTrack, source: String, via_crossfade: bool },
    /// Playback stopped (`None` = nothing was playing).
    Stopped { track: Option<LibTrack> },
    /// The current track ran out by itself.
    Finished { track: LibTrack },
    EndingSoon { track: LibTrack, remaining_s: f64 },
    CrossfadeStarted { id: String, from: LibTrack, to: LibTrack, duration_s: f64 },
    CrossfadeComplete { id: String, ok: bool, track: Option<LibTrack>, message: Option<String> },
    Ducked { level: f32 },
    Unducked,
    Library { tracks: Vec<LibTrack> },
    DjMode { active: bool },
}

/// Retained engine state (the bus's `state.music` is built from it).
#[derive(Debug, Clone, PartialEq)]
pub struct Status {
    pub playing: bool,
    pub paused: bool,
    pub track: Option<LibTrack>,
    /// Position of `track` at `at`.
    pub position_s: f64,
    pub at: Instant,
    pub ducked: bool,
    pub duck_level: f32,
    pub dj_active: bool,
    pub crossfading: bool,
}

impl Default for Status {
    fn default() -> Self {
        Self { playing: false, paused: false, track: None, position_s: 0.0, at: Instant::now(), ducked: false, duck_level: 1.0, dj_active: false, crossfading: false }
    }
}

/// Chooses the track for a play that names none, from the candidate keys (`None` = the
/// engine's own random pick). Replays install the recorded picks.
pub type Picker = Arc<dyn Fn(&[String]) -> Option<String> + Send + Sync>;

/// Reply text for the console (`cli.response`); `Err` is an error reply.
pub type Reply = Result<String, String>;

enum Cmd {
    Play { query: Option<String>, source: String },
    Stop,
    Pause(bool),
    Next { source: String },
    Crossfade { track: String, secs: f64, id: String, source: String },
    Duck(Option<f32>),
    Unduck,
    Dj(bool),
    List,
    Beats(PathBuf, BeatInfo),
    SetSearch(Arc<dyn Search>),
    SetPicker(Picker),
    CrossfadeDone(String),
}

#[derive(Clone)]
pub struct Engine {
    tx: mpsc::UnboundedSender<(Cmd, Option<oneshot::Sender<Reply>>)>,
    events: broadcast::Sender<EngineEvent>,
    status: watch::Receiver<Status>,
    library: Arc<RwLock<Library>>,
}

impl Engine {
    /// Start the actor on `mixer`'s music bus with a scanned `library`.
    pub fn spawn(mixer: MixerHandle, library: Library, cfg: EngineConfig) -> Engine {
        let (tx, rx) = mpsc::unbounded_channel();
        let (events, _) = broadcast::channel(256);
        let (status_tx, status) = watch::channel(Status::default());
        let library = Arc::new(RwLock::new(library));
        let engine = Engine { tx: tx.clone(), events: events.clone(), status, library: library.clone() };
        let actor = Actor {
            mixer,
            cfg,
            library,
            events,
            status: status_tx,
            me: tx,
            current: None,
            crossfading: None,
            ducked: None,
            dj: false,
            last_semantic: Vec::new(),
            search: None,
            picker: None,
            rng: seed(),
        };
        tokio::spawn(actor.run(rx));
        engine
    }

    pub fn subscribe(&self) -> broadcast::Receiver<EngineEvent> {
        self.events.subscribe()
    }

    pub fn status(&self) -> watch::Receiver<Status> {
        self.status.clone()
    }

    pub fn library(&self) -> Library {
        self.library.read().unwrap().clone()
    }

    async fn ask(&self, cmd: Cmd) -> Reply {
        let (tx, rx) = oneshot::channel();
        self.tx.send((cmd, Some(tx))).map_err(|_| "music engine stopped".to_string())?;
        rx.await.map_err(|_| "music engine stopped".to_string())?
    }

    fn tell(&self, cmd: Cmd) {
        let _ = self.tx.send((cmd, None));
    }

    /// Play a track number, name, `@semantic` request or mood words; `None` = any track.
    pub async fn play(&self, query: Option<String>, source: &str) -> Reply {
        self.ask(Cmd::Play { query, source: source.into() }).await
    }

    pub async fn stop(&self) -> Reply {
        self.ask(Cmd::Stop).await
    }

    pub async fn pause(&self, on: bool) -> Reply {
        self.ask(Cmd::Pause(on)).await
    }

    /// Next ranked semantic result, else the next library track (works outside DJ mode).
    pub async fn next(&self, source: &str) -> Reply {
        self.ask(Cmd::Next { source: source.into() }).await
    }

    /// Crossfade to `track` over `secs`; completion arrives as `CrossfadeComplete{id}`.
    pub async fn crossfade(&self, track: &str, secs: f64, id: &str, source: &str) -> Reply {
        self.ask(Cmd::Crossfade { track: track.into(), secs, id: id.into(), source: source.into() }).await
    }

    pub async fn duck(&self, level: Option<f32>) -> Reply {
        self.ask(Cmd::Duck(level)).await
    }

    pub async fn unduck(&self) -> Reply {
        self.ask(Cmd::Unduck).await
    }

    pub async fn set_dj(&self, active: bool) -> Reply {
        self.ask(Cmd::Dj(active)).await
    }

    pub async fn list(&self) -> Reply {
        self.ask(Cmd::List).await
    }

    /// A beat analysis result for `path` (attached to its library entry).
    pub fn beats(&self, path: PathBuf, info: BeatInfo) {
        self.tell(Cmd::Beats(path, info));
    }

    pub fn set_search(&self, s: Arc<dyn Search>) {
        self.tell(Cmd::SetSearch(s));
    }

    pub fn set_picker(&self, p: Picker) {
        self.tell(Cmd::SetPicker(p));
    }
}

fn seed() -> u64 {
    std::env::var("R3X_SEED").ok().and_then(|s| s.parse().ok()).unwrap_or_else(|| {
        std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(1234, |d| d.as_nanos() as u64)
    }) | 1
}

struct Current {
    track: LibTrack,
    stream: MusicStream,
    ending_sent: bool,
}

struct Actor {
    mixer: MixerHandle,
    cfg: EngineConfig,
    library: Arc<RwLock<Library>>,
    events: broadcast::Sender<EngineEvent>,
    status: watch::Sender<Status>,
    me: mpsc::UnboundedSender<(Cmd, Option<oneshot::Sender<Reply>>)>,
    current: Option<Current>,
    crossfading: Option<String>,
    /// Duck level while ducked.
    ducked: Option<f32>,
    dj: bool,
    last_semantic: Vec<String>,
    search: Option<Arc<dyn Search>>,
    picker: Option<Picker>,
    rng: u64,
}

impl Actor {
    async fn run(mut self, mut rx: mpsc::UnboundedReceiver<(Cmd, Option<oneshot::Sender<Reply>>)>) {
        let mut tick = tokio::time::interval(self.cfg.tick);
        tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
        loop {
            tokio::select! {
                m = rx.recv() => {
                    let Some((cmd, reply)) = m else { break };
                    let r = self.handle(cmd).await;
                    if let Some(tx) = reply {
                        let _ = tx.send(r);
                    }
                    self.publish();
                }
                _ = tick.tick() => {
                    self.tick();
                    self.publish();
                }
            }
        }
    }

    fn emit(&self, e: EngineEvent) {
        let _ = self.events.send(e);
    }

    fn publish(&self) {
        let (track, position_s, paused) = match &self.current {
            Some(c) => (Some(c.track.clone()), c.stream.position_s(), c.stream.is_paused()),
            None => (None, 0.0, false),
        };
        let st = Status {
            playing: track.is_some() && !paused,
            paused,
            track,
            position_s,
            at: Instant::now(),
            ducked: self.ducked.is_some(),
            duck_level: self.ducked.unwrap_or(1.0),
            dj_active: self.dj,
            crossfading: self.crossfading.is_some(),
        };
        self.status.send_if_modified(|s| {
            // Position moves every tick; only republish when something else changed or the
            // anchor drifted (the bus state is extrapolated between updates).
            let drift = (s.position_s + st.at.duration_since(s.at).as_secs_f64() * if s.playing { 1.0 } else { 0.0 } - st.position_s).abs();
            let changed = s.playing != st.playing || s.paused != st.paused || s.track != st.track || s.ducked != st.ducked
                || s.duck_level != st.duck_level || s.dj_active != st.dj_active || s.crossfading != st.crossfading || drift > 0.05;
            if changed {
                *s = st.clone();
            }
            changed
        });
    }

    fn tick(&mut self) {
        let Some(c) = &mut self.current else { return };
        if c.stream.is_finished() {
            let track = c.track.clone();
            self.current = None;
            tracing::info!(track = %track.key, "track finished");
            self.emit(EngineEvent::Finished { track });
            return;
        }
        if c.ending_sent {
            return;
        }
        if let Some(d) = c.stream.duration_s() {
            let remaining = d - c.stream.position_s();
            if d > self.cfg.ending_threshold_s && remaining <= self.cfg.ending_threshold_s {
                c.ending_sent = true;
                let track = c.track.clone();
                tracing::info!(track = %track.key, remaining, "track ending soon");
                self.emit(EngineEvent::EndingSoon { track, remaining_s: remaining });
            }
        }
    }

    async fn handle(&mut self, cmd: Cmd) -> Reply {
        match cmd {
            Cmd::Play { query, source } => self.play_query(query, &source).await,
            Cmd::Stop => Ok(self.stop()),
            Cmd::Pause(on) => match &self.current {
                Some(c) => {
                    c.stream.set_paused(on);
                    Ok(if on { format!("Paused {}", c.track.title) } else { format!("Resumed {}", c.track.title) })
                }
                None => Err("No music is currently playing".into()),
            },
            Cmd::Next { source } => self.next(&source).await,
            Cmd::Crossfade { track, secs, id, source } => self.crossfade(&track, secs, id, &source).await,
            Cmd::Duck(level) => {
                let level = level.unwrap_or(self.cfg.duck_level).clamp(0.0, 1.0);
                self.mixer.duck(level, self.cfg.duck_ramp_ms);
                self.ducked = Some(level);
                self.emit(EngineEvent::Ducked { level });
                Ok(format!("Music ducked to {:.0}%", level * 100.0))
            }
            Cmd::Unduck => {
                self.unduck();
                Ok("Music volume restored".into())
            }
            Cmd::Dj(active) => {
                if self.dj != active {
                    self.dj = active;
                    self.emit(EngineEvent::DjMode { active });
                    if !active {
                        self.stop(); // CantinaOS: DJ off stops the music
                    }
                }
                Ok(format!("DJ mode {}", if active { "on" } else { "off" }))
            }
            Cmd::List => Ok(self.library.read().unwrap().listing()),
            Cmd::Beats(path, info) => {
                let mut lib = self.library.write().unwrap();
                if let Some(t) = lib.tracks.iter_mut().find(|t| t.path == path) {
                    t.bpm = Some(info.bpm);
                    t.first_beat_s = Some(info.first_beat_s);
                }
                if let Some(c) = &mut self.current {
                    if c.track.path == path {
                        c.track.bpm = Some(info.bpm);
                        c.track.first_beat_s = Some(info.first_beat_s);
                    }
                }
                let tracks = lib.tracks.clone();
                drop(lib);
                self.emit(EngineEvent::Library { tracks });
                Ok(String::new())
            }
            Cmd::SetSearch(s) => {
                self.search = Some(s);
                Ok(String::new())
            }
            Cmd::SetPicker(p) => {
                self.picker = Some(p);
                Ok(String::new())
            }
            Cmd::CrossfadeDone(id) => {
                if self.crossfading.as_deref() == Some(id.as_str()) {
                    self.crossfading = None;
                    let track = self.current.as_ref().map(|c| c.track.clone());
                    self.emit(EngineEvent::CrossfadeComplete { id, ok: true, track, message: None });
                }
                Ok(String::new())
            }
        }
    }

    fn unduck(&mut self) {
        self.mixer.unduck(self.cfg.duck_ramp_ms);
        if self.ducked.take().is_some() {
            self.emit(EngineEvent::Unducked);
        }
    }

    fn stop(&mut self) -> String {
        let Some(c) = self.current.take() else {
            self.emit(EngineEvent::Stopped { track: None });
            return "No music is currently playing".into();
        };
        self.mixer.fade_out(BusId::Music, self.cfg.stop_fade_ms);
        if let Some(id) = self.crossfading.take() {
            self.emit(EngineEvent::CrossfadeComplete { id, ok: false, track: None, message: Some("stopped".into()) });
        }
        self.unduck(); // CantinaOS resets ducking on stop
        self.emit(EngineEvent::Stopped { track: Some(c.track) });
        "Stopped music playback".into()
    }

    fn pick_random(&mut self) -> Option<String> {
        let lib = self.library.read().unwrap();
        let cur = self.current.as_ref().map(|c| c.track.key.clone());
        let mut choices: Vec<&LibTrack> = lib.tracks.iter().collect();
        if choices.len() > 1 {
            choices.retain(|t| Some(&t.key) != cur.as_ref());
        }
        if choices.is_empty() {
            return None;
        }
        if let Some(p) = &self.picker {
            let keys: Vec<String> = choices.iter().map(|t| t.key.clone()).collect();
            if let Some(k) = p(&keys).filter(|k| keys.contains(k)) {
                return Some(k);
            }
        }
        self.rng ^= self.rng << 13;
        self.rng ^= self.rng >> 7;
        self.rng ^= self.rng << 17;
        Some(choices[(self.rng % choices.len() as u64) as usize].key.clone())
    }

    async fn play_query(&mut self, query: Option<String>, source: &str) -> Reply {
        let q = query.map(|q| q.trim().to_owned()).filter(|q| !q.is_empty());
        let Some(q) = q else {
            let key = self.pick_random().ok_or("No music tracks available. Please install music first.")?;
            tracing::info!("no track named; playing {key}");
            return self.play_key(&key, source).await;
        };
        if let Some((pos, neg)) = parse_semantic_request(&q) {
            return match self.semantic(&pos, neg.as_deref()).await {
                Some(k) => self.play_key(&k, source).await,
                None => Err(format!("No music found matching '{pos}'")),
            };
        }
        let resolved = {
            let lib = self.library.read().unwrap();
            if lib.is_empty() {
                return Err(format!("No music found matching '{q}'"));
            }
            if let Ok(n) = q.parse::<usize>() {
                if n == 0 || n > lib.len() {
                    return Err(format!("Track number {q} out of range. Must be 1-{}.", lib.len()));
                }
                Some(lib.tracks[n - 1].key.clone())
            } else if let Some(t) = lib.get(&q) {
                Some(t.key.clone())
            } else {
                lib.find_named(&q).map(|t| t.key.clone())
            }
        };
        if let Some(k) = resolved {
            return self.play_key(&k, source).await;
        }
        if looks_semantic(&q) {
            if let Some(k) = self.semantic(&q, None).await {
                return self.play_key(&k, source).await;
            }
        }
        tracing::warn!("no music found matching {q:?}");
        Err(format!("No music found matching '{q}'"))
    }

    /// Best semantic match that is in the library and not already playing.
    async fn semantic(&mut self, q: &str, neg: Option<&str>) -> Option<String> {
        let search = self.search.clone()?;
        let (q2, n2) = (q.to_owned(), neg.map(str::to_owned));
        let matches = match tokio::task::spawn_blocking(move || search.search(&q2, n2.as_deref(), 5)).await {
            Ok(Ok(m)) => m,
            Ok(Err(e)) => {
                tracing::warn!("semantic search failed: {e}");
                return None;
            }
            Err(_) => return None,
        };
        let lib = self.library.read().unwrap();
        let cur = self.current.as_ref().map(|c| c.track.key.clone());
        self.last_semantic = matches.iter().filter(|m| lib.get(&m.key).is_some()).map(|m| m.key.clone()).collect();
        let winner = self.last_semantic.iter().find(|k| Some(*k) != cur.as_ref()).or(self.last_semantic.first()).cloned();
        tracing::info!(
            query = q,
            top = %matches.iter().take(3).map(|m| format!("{}:{:.3}", m.key, m.score)).collect::<Vec<_>>().join(", "),
            "semantic music search selected {winner:?}"
        );
        winner
    }

    async fn next(&mut self, source: &str) -> Reply {
        let cur = self.current.as_ref().map(|c| c.track.key.clone());
        let key = {
            let lib = self.library.read().unwrap();
            let ranked: Vec<&String> = self.last_semantic.iter().filter(|k| lib.get(k).is_some() && Some(*k) != cur.as_ref()).collect();
            if let Some(k) = ranked.first() {
                (*k).clone()
            } else {
                let names = lib.keys();
                if names.is_empty() {
                    return Err("No music tracks available. Please install music first.".into());
                }
                match cur.as_ref().and_then(|c| names.iter().position(|n| n == c)) {
                    Some(i) if names.len() > 1 => names[(i + 1) % names.len()].clone(),
                    _ => names.iter().find(|n| Some(*n) != cur.as_ref()).unwrap_or(&names[0]).clone(),
                }
            }
        };
        self.play_key(&key, source).await
    }

    async fn open(&self, t: &LibTrack) -> Result<(MusicStream, r3x_audio::music::MusicSource), String> {
        let (path, rate, ch) = (t.path.clone(), self.mixer.sample_rate(), self.mixer.channels());
        tokio::task::spawn_blocking(move || MusicStream::open(&path, rate, ch))
            .await
            .map_err(|e| e.to_string())?
            .map_err(|e| format!("Failed to play track: {} ({e})", t.key))
    }

    /// `_play_track_by_name`: crossfade in DJ mode when something plays, else stop and play.
    async fn play_key(&mut self, name: &str, source: &str) -> Reply {
        let t = self.library.read().unwrap().by_name(name).cloned().ok_or_else(|| format!("No track found matching '{name}'"))?;
        if self.current.is_some() && self.dj {
            let id = uuid::Uuid::new_v4().to_string();
            return self.crossfade(&t.key, self.cfg.crossfade_s, id, source).await;
        }
        self.start_fresh(t, source).await
    }

    /// Stop whatever plays and start `t` from the top.
    async fn start_fresh(&mut self, t: LibTrack, source: &str) -> Reply {
        let (stream, src) = self.open(&t).await?;
        if self.current.is_some() {
            self.stop();
        }
        self.mixer.add(BusId::Music, Box::new(src));
        tracing::info!(track = %t.key, source, "now playing");
        self.current = Some(Current { track: t.clone(), stream, ending_sent: false });
        self.emit(EngineEvent::Started { track: t.clone(), source: source.into(), via_crossfade: false });
        Ok(format!("Now playing: {}", t.title))
    }

    async fn crossfade(&mut self, name: &str, secs: f64, id: String, source: &str) -> Reply {
        let fail = |me: &Self, msg: String| {
            me.emit(EngineEvent::CrossfadeComplete { id: id.clone(), ok: false, track: None, message: Some(msg.clone()) });
            Err(msg)
        };
        if self.crossfading.is_some() {
            return fail(self, "Already crossfading".into());
        }
        let Some(t) = self.library.read().unwrap().by_name(name).cloned() else {
            return fail(self, format!("Crossfade failed: Track '{name}' not found."));
        };
        let Some(from) = self.current.as_ref().map(|c| c.track.clone()) else {
            // Nothing to fade from: just play (and still complete, so a waiting plan moves on).
            let r = self.start_fresh(t.clone(), source).await;
            let ok = r.is_ok();
            self.emit(EngineEvent::CrossfadeComplete { id, ok, track: ok.then_some(t), message: r.as_ref().err().cloned() });
            return r;
        };
        let (stream, src) = match self.open(&t).await {
            Ok(s) => s,
            Err(e) => return fail(self, e),
        };
        let secs = secs.max(0.0);
        self.mixer.crossfade(BusId::Music, Box::new(src), secs);
        tracing::info!(from = %from.key, to = %t.key, secs, "crossfade");
        self.current = Some(Current { track: t.clone(), stream, ending_sent: false });
        self.crossfading = Some(id.clone());
        self.emit(EngineEvent::CrossfadeStarted { id: id.clone(), from, to: t.clone(), duration_s: secs });
        self.emit(EngineEvent::Started { track: t.clone(), source: source.into(), via_crossfade: true });
        let me = self.me.clone();
        tokio::spawn(async move {
            tokio::time::sleep(Duration::from_secs_f64(secs)).await;
            let _ = me.send((Cmd::CrossfadeDone(id), None));
        });
        Ok(format!("Crossfading to {}", t.title))
    }
}

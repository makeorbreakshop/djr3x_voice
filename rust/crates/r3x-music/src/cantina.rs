//! Bridge mode (Phase 4, `--music rust`): r3x plays the music, CantinaOS's brain and timeline
//! still decide what. CantinaOS runs with `R3X_EXTERNAL_MUSIC=1`, so its MusicController and
//! mode-change sound are off; this adapter speaks their bus language over the tap.
//!
//! in (CantinaOS -> r3x): `music.command` (action `play|stop|crossfade`, or CLI-shaped
//!   `play|stop|next|list music`), `audio.ducking.start|stop`, `speech.cache.playback.completed`
//!   (DJ unduck), `dj.mode.changed`, `system.mode.change` (IDLE stops the music).
//! out (r3x -> CantinaOS), exactly what MusicController emitted: `music.library.updated`,
//!   `music.playback.started` + `track.playing`, `music.playback.stopped` + `track.stopped`,
//!   `track.ending.soon` (from the playback position), `crossfade.started|complete`,
//!   `memory.set current_track` (DJ), `cli.response`.

use std::sync::{Arc, Mutex};

use serde_json::{json, Map, Value};

use crate::engine::{Engine, EngineEvent, Reply};
use crate::library::LibTrack;

/// Fire-and-forget emit onto the CantinaOS bus (the runtime's tap link).
pub trait TapEmit: Send + Sync + 'static {
    fn emit(&self, topic: &str, payload: Value);
}

impl<F: Fn(&str, Value) + Send + Sync + 'static> TapEmit for F {
    fn emit(&self, topic: &str, payload: Value) {
        self(topic, payload)
    }
}

/// Tap `source` tag of our emits (the bridge skips their echo).
pub const TAP_SOURCE: &str = "r3x-music";
const SERVICE: &str = "MusicController";

pub struct CantinaMusic {
    engine: Engine,
    tap: Arc<dyn TapEmit>,
    mode: Mutex<String>,
}

/// `TrackDataPayload`.
pub fn track_data(t: &LibTrack) -> Value {
    json!({
        "track_id": t.key, "title": t.title, "artist": t.artist, "album": null, "genre": null,
        "duration": t.duration_s, "bpm": t.bpm, "first_beat_s": t.first_beat_s,
    })
}

/// `MusicTrack.dict()` for `music.library.updated`.
fn music_track(t: &LibTrack) -> Value {
    json!({
        "name": t.key, "path": t.path.to_string_lossy(), "duration": t.duration_s, "track_id": t.key,
        "title": t.title, "artist": t.artist, "album": null, "genre": null, "bpm": t.bpm,
        "first_beat_s": t.first_beat_s, "provider": "local",
    })
}

fn s<'a>(p: &'a Value, k: &str) -> Option<&'a str> {
    p.get(k).and_then(Value::as_str)
}

impl CantinaMusic {
    /// Start forwarding `engine`'s events to CantinaOS. Feed tap events to [`Self::on_tap`]
    /// and call [`Self::on_connect`] whenever the tap (re)connects.
    pub fn attach(engine: &Engine, tap: Arc<dyn TapEmit>) -> Arc<Self> {
        let me = Arc::new(Self { engine: engine.clone(), tap, mode: Mutex::new("IDLE".into()) });
        let mut rx = engine.subscribe();
        let out = me.clone();
        tokio::spawn(async move {
            loop {
                match rx.recv().await {
                    Ok(e) => out.forward(e),
                    Err(tokio::sync::broadcast::error::RecvError::Lagged(n)) => tracing::warn!("cantina music adapter lagged {n}"),
                    Err(_) => break,
                }
            }
        });
        me
    }

    fn emit(&self, topic: &str, payload: Value) {
        self.tap.emit(topic, payload);
    }

    fn reply(&self, r: &Reply) {
        match r {
            Ok(m) if m.is_empty() => {}
            Ok(m) => self.emit("cli.response", json!({"message": m, "is_error": false, "service": SERVICE})),
            Err(m) => self.emit("cli.response", json!({"message": m, "is_error": true, "service": SERVICE})),
        }
    }

    /// The tap came up: announce the library (BrainService needs it for DJ mode).
    pub fn on_connect(&self) {
        self.library_updated(&self.engine.library().tracks);
    }

    fn library_updated(&self, tracks: &[LibTrack]) {
        let map: Map<String, Value> = tracks.iter().map(|t| (t.key.clone(), music_track(t))).collect();
        self.emit("music.library.updated", json!({"track_count": tracks.len(), "tracks": map}));
    }

    fn started(&self, t: &LibTrack, source: &str) {
        let data = track_data(t);
        let mode = self.mode.lock().unwrap().clone();
        self.emit("music.playback.started", json!({"track": data, "source": source, "mode": mode}));
        self.emit("track.playing", json!({}));
        if self.engine.status().borrow().dj_active {
            self.emit("memory.set", json!({"key": "current_track", "value": data}));
        }
    }

    fn forward(&self, e: EngineEvent) {
        match e {
            EngineEvent::Started { track, source, via_crossfade: false } => self.started(&track, &source),
            EngineEvent::Started { .. } => {} // announced on crossfade.complete, as CantinaOS did
            EngineEvent::Stopped { track: Some(t) } => {
                self.emit("music.playback.stopped", json!({"track_name": t.key}));
                self.emit("track.stopped", json!({}));
            }
            EngineEvent::Stopped { track: None } => {
                self.emit("music.playback.stopped", json!({"track_name": null, "already_stopped": true}));
            }
            EngineEvent::Finished { track } => {
                self.emit("music.playback.stopped", json!({"track_name": track.key, "reason": "finished"}));
                self.emit("track.stopped", json!({}));
            }
            EngineEvent::EndingSoon { track, remaining_s } => {
                let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0.0, |d| d.as_secs_f64());
                self.emit("track.ending.soon", json!({"timestamp": now, "current_track": track_data(&track), "time_remaining": remaining_s}));
            }
            EngineEvent::CrossfadeStarted { id, from, to, duration_s } => {
                self.emit(
                    "crossfade.started",
                    json!({"crossfade_id": id, "from_track": track_data(&from), "to_track": track_data(&to), "duration_ms": (duration_s * 1000.0).round()}),
                );
            }
            EngineEvent::CrossfadeComplete { id, ok: true, track, .. } => {
                self.emit("crossfade.complete", json!({"crossfade_id": id, "status": "success", "current_track": track.as_ref().map(track_data)}));
                if let Some(t) = track {
                    self.started(&t, "timeline");
                }
            }
            EngineEvent::CrossfadeComplete { id, ok: false, message, .. } => {
                self.emit("crossfade.complete", json!({"crossfade_id": id, "status": "error", "message": message}));
            }
            EngineEvent::Library { tracks } => self.library_updated(&tracks),
            EngineEvent::Ducked { .. } | EngineEvent::Unducked | EngineEvent::DjMode { .. } => {}
        }
    }

    /// One CantinaOS bus event (from the tap). Never blocks: engine calls run on tasks.
    pub fn on_tap(self: &Arc<Self>, topic: &str, p: &Value) {
        let me = self.clone();
        match topic {
            "music.command" => {
                let p = p.clone();
                tokio::spawn(async move { me.music_command(&p).await });
            }
            "audio.ducking.start" => {
                let level = p.get("level").and_then(Value::as_f64).map(|l| l as f32);
                tokio::spawn(async move { me.engine.duck(level).await });
            }
            "audio.ducking.stop" => {
                tokio::spawn(async move { me.engine.unduck().await });
            }
            "speech.cache.playback.completed" => {
                // CantinaOS "FIX 1": unduck as soon as DJ commentary ends.
                let completed = s(p, "completion_status").unwrap_or("completed") == "completed";
                let st = self.engine.status().borrow().clone();
                if completed && st.dj_active && st.ducked {
                    tokio::spawn(async move { me.engine.unduck().await });
                }
            }
            "dj.mode.changed" => {
                let active = p.get("is_active").and_then(Value::as_bool).unwrap_or(false);
                tokio::spawn(async move { me.engine.set_dj(active).await });
            }
            "system.mode.change" => {
                if let Some(m) = s(p, "new_mode") {
                    *self.mode.lock().unwrap() = m.to_owned();
                    if m == "IDLE" && self.engine.status().borrow().track.is_some() {
                        tokio::spawn(async move { me.engine.stop().await });
                    }
                }
            }
            _ => {}
        }
    }

    async fn music_command(&self, p: &Value) {
        let source = if p.get("conversation_id").is_some_and(|c| !c.is_null()) { "voice" } else { "cli" };
        let r = match s(p, "action") {
            Some("play") => self.engine.play(s(p, "song_query").map(str::to_owned), source).await,
            Some("stop") => self.engine.stop().await,
            Some("next") => self.engine.next(source).await,
            Some("crossfade") => {
                let id = s(p, "crossfade_id").map(str::to_owned).unwrap_or_else(|| uuid::Uuid::new_v4().to_string());
                let secs = p.get("fade_duration").and_then(Value::as_f64).unwrap_or(3.0);
                let track = s(p, "song_query").unwrap_or_default();
                self.engine.crossfade(track, secs, &id, "timeline").await
            }
            Some(other) => Err(format!("Unknown music action: {other}")),
            None => {
                let cmd = s(p, "command").unwrap_or("");
                let sub = s(p, "subcommand").unwrap_or("");
                let args: Vec<&str> = p.get("args").and_then(Value::as_array).map(|a| a.iter().filter_map(Value::as_str).collect()).unwrap_or_default();
                let q = args.join(" ");
                match (cmd, sub) {
                    ("play", "music") => self.engine.play(Some(q).filter(|q| !q.trim().is_empty()), "cli").await,
                    ("stop", "music") => self.engine.stop().await,
                    ("next", "music") => self.engine.next("cli").await,
                    ("list", "music") => self.engine.list().await,
                    ("pause", "music") => self.engine.pause(true).await,
                    ("resume", "music") => self.engine.pause(false).await,
                    (c, "music") => Err(format!("'{c} music' is not available with r3x music")),
                    _ => {
                        tracing::debug!("unhandled music.command payload {p}");
                        return;
                    }
                }
            }
        };
        self.reply(&r);
    }
}

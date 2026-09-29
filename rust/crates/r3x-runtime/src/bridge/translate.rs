//! CantinaOS tap topics -> typed events, retained state and (Phase 3) perf commands.
//!
//! Payload shapes are from `cantina_os/cantina_os/tap/topic_schema.json` (hand-written from
//! the emit sites). Payloads are untyped dicts, so every read is tolerant: a missing or
//! mistyped field degrades to a default instead of dropping the event.

use r3x_bus::Bus;
use r3x_contracts::{
    Ack, Command, ConversationEvent, ConversationPhase, ConversationState, DjEvent, DjState,
    Engagement, Event, LightsState, MusicEvent, MusicState, OpsEvent, PerfCommand, PerfLayer,
    ServiceStatus, Source, StageCommand, StopTarget, Track,
};
use serde_json::Value;

/// Bridge-side memory the tap does not carry.
#[derive(Default)]
pub struct TapState {
    /// Last freeze state CantinaOS saw, so the stage watcher does not echo it back.
    pub cantina_frozen: bool,
}

/// Send a command on the bus as the bridge would (fire-and-forget; refusals are logged).
fn command(bus: &Bus, source: Source, cmd: Command) {
    let Ok(rt) = tokio::runtime::Handle::try_current() else { return };
    let bus = bus.clone();
    rt.spawn(async move {
        let what = format!("{cmd:?}");
        if let Ack::Rejected { reason } = bus.command(source, None, cmd).await {
            tracing::info!("{what} refused: {reason}");
        }
    });
}

fn f_or(p: &Value, k: &str, default: f64) -> f64 {
    f(p, k).unwrap_or(default)
}

/// `eye.command`: `{pattern, duration?}`, or the CLI shape `{command: "eye", args: ["pattern", X]}`.
fn eye_pattern(p: &Value) -> Option<String> {
    if let Some(pat) = s(p, "pattern") {
        return Some(pat.to_owned());
    }
    let args: Vec<&str> = p.get("args")?.as_array()?.iter().filter_map(Value::as_str).collect();
    match args.as_slice() {
        ["pattern", x, ..] => Some((*x).to_owned()),
        _ => None,
    }
}

const SRC: Source = Source::Bridge;

fn s<'a>(p: &'a Value, k: &str) -> Option<&'a str> {
    p.get(k).and_then(Value::as_str)
}

fn f(p: &Value, k: &str) -> Option<f64> {
    p.get(k).and_then(Value::as_f64)
}

fn b(p: &Value, k: &str) -> Option<bool> {
    p.get(k).and_then(Value::as_bool)
}

fn conv_id(p: &Value) -> Option<String> {
    s(p, "conversation_id").filter(|c| !c.is_empty() && *c != "unknown").map(str::to_owned)
}

fn phase(bus: &Bus, to: ConversationPhase, id: Option<&String>) {
    bus.update(SRC, |c: &mut ConversationState| {
        c.phase = to;
        if let Some(id) = id {
            c.conversation_id = Some(id.clone());
        }
    });
}

fn track(p: &Value) -> Track {
    // music.playback.started: {track: {title, artist, duration, filepath, bpm, first_beat_s}}
    let t = p.get("track").unwrap_or(p);
    let title = s(t, "title").or_else(|| s(t, "name")).or_else(|| t.as_str()).unwrap_or("Unknown track");
    Track {
        title: title.into(),
        artist: s(t, "artist").filter(|a| !a.is_empty()).map(Into::into),
        path: s(t, "filepath").map(Into::into),
        duration_s: f(t, "duration"),
        bpm: f(t, "bpm"),
        first_beat_s: f(t, "first_beat_s"),
    }
}

/// `"ServiceStatus.RUNNING"`, `"RUNNING"`, `"running"` -> Running.
fn service_status(raw: &str) -> ServiceStatus {
    match raw.rsplit('.').next().unwrap_or(raw).to_ascii_lowercase().as_str() {
        "running" | "online" => ServiceStatus::Running,
        "degraded" => ServiceStatus::Degraded,
        "error" | "failed" => ServiceStatus::Error,
        "stopped" | "offline" => ServiceStatus::Stopped,
        _ => ServiceStatus::Starting,
    }
}

pub fn engagement(raw: &str) -> Option<Engagement> {
    Some(match raw.to_ascii_uppercase().as_str() {
        "STARTUP" => Engagement::Startup,
        "IDLE" => Engagement::Idle,
        "AMBIENT" => Engagement::Ambient,
        "INTERACTIVE" => Engagement::Interactive,
        _ => return None,
    })
}

/// A `show.perform` source: its tier ceiling applies (unknown = the timeline's).
fn source(raw: Option<&str>) -> Source {
    match raw.unwrap_or("") {
        "jev" => Source::Jev,
        "claude" => Source::Claude,
        "idle" => Source::Idle,
        "ui" => Source::Ui,
        "cli" => Source::Cli,
        _ => Source::Timeline,
    }
}

/// Track titles out of `music.library.updated` (`{tracks: {name: {...}}}` or a list).
fn library(p: &Value) -> Option<Vec<String>> {
    let mut v: Vec<String> = match p.get("tracks")? {
        Value::Object(m) => m.iter().map(|(k, t)| s(t, "title").unwrap_or(k).to_owned()).collect(),
        Value::Array(a) => a.iter().filter_map(|t| t.as_str().or_else(|| s(t, "title")).map(Into::into)).collect(),
        _ => return None,
    };
    v.sort();
    v.dedup();
    Some(v)
}

/// `list music` replies "Available tracks:\n1. A\n2. B" - the only way to learn the library
/// when the bridge connects after CantinaOS finished booting.
pub fn library_from_listing(msg: &str) -> Option<Vec<String>> {
    let rest = msg.strip_prefix("Available tracks:")?;
    let mut v: Vec<String> = rest
        .lines()
        .filter_map(|l| l.split_once(". ").map(|(_, t)| t.trim().to_owned()))
        .filter(|t| !t.is_empty())
        .collect();
    v.sort();
    Some(v)
}

/// Translate one tap event. Unknown topics are ignored. `ours`: an echo of the bridge's own
/// emit (it must not come back in as a command).
pub fn translate(bus: &Bus, st: &mut TapState, topic: &str, p: &Value, ours: bool) {
    let id = conv_id(p);
    let ev = |e: Event| {
        bus.publish(SRC, id.clone(), e);
    };
    match topic {
        "voice.listening.started" => {
            phase(bus, ConversationPhase::Listening, id.as_ref());
            ev(Event::Conversation(ConversationEvent::ListeningStarted));
        }
        "voice.listening.stopped" => {
            phase(bus, ConversationPhase::Thinking, id.as_ref());
            let transcript = s(p, "transcript").unwrap_or_default().to_owned();
            ev(Event::Conversation(ConversationEvent::ListeningStopped { transcript }));
        }
        "transcription.final" | "transcription.interim" => {
            let text = s(p, "text").unwrap_or_default().to_owned();
            ev(Event::Conversation(ConversationEvent::Transcript { text, is_final: topic == "transcription.final" }));
        }
        "intent.detected" => {
            let tool = s(p, "intent_name").unwrap_or("?").to_owned();
            ev(Event::Conversation(ConversationEvent::IntentDetected { tool, confidence: f(p, "confidence") }));
        }
        "llm.response" => {
            let text = s(p, "text").unwrap_or_default().to_owned();
            if b(p, "is_complete").unwrap_or(false) {
                ev(Event::Conversation(ConversationEvent::Reply { text }));
            } else if !text.is_empty() {
                ev(Event::Conversation(ConversationEvent::ReplyDelta { text }));
            }
        }
        "speech.generation.started" => {
            phase(bus, ConversationPhase::Speaking, id.as_ref());
            ev(Event::Conversation(ConversationEvent::SpeechStarted));
        }
        "speech.generation.complete" => {
            phase(bus, ConversationPhase::Idle, None);
            ev(Event::Conversation(ConversationEvent::SpeechEnded));
        }
        "music.playback.started" => {
            let t = track(p);
            bus.update(SRC, |m: &mut MusicState| {
                m.playing = true;
                m.track = Some(t.clone());
            });
            ev(Event::Music(MusicEvent::TrackStarted { track: t }));
        }
        "music.playback.stopped" => {
            // CantinaOS double-emits stops; only a real transition is an event.
            if bus.update(SRC, |m: &mut MusicState| {
                m.playing = false;
                m.track = None;
            }) {
                ev(Event::Music(MusicEvent::TrackStopped));
            }
        }
        "music.library.updated" => {
            if let Some(lib) = library(p) {
                bus.update(SRC, |m: &mut MusicState| m.library = lib);
            }
        }
        "dj.mode.changed" => {
            let active = b(p, "is_active").unwrap_or(false);
            if bus.update(SRC, |d: &mut DjState| d.active = active) {
                ev(Event::Dj(if active { DjEvent::Started } else { DjEvent::Stopped }));
            }
        }
        // CantinaOS asks, the performer plays (Phase 3).
        "show.perform" if !ours => {
            let Some(item) = s(p, "id") else { return };
            let params = p.get("params").unwrap_or(&Value::Null);
            let play = PerfCommand::Play {
                id: item.to_owned(),
                intensity: f_or(params, "intensity", 1.0),
                speed: f_or(params, "speed", 1.0),
                layer: None,
            };
            command(bus, source(s(p, "source")), Command::Perf(play));
        }
        "show.stop" if !ours => {
            let target = if let Some(item) = s(p, "id") {
                StopTarget::Id { id: item.to_owned() }
            } else {
                match s(p, "layer") {
                    Some("show") => StopTarget::Layer { layer: PerfLayer::Show },
                    Some("gesture") => StopTarget::Layer { layer: PerfLayer::Gesture },
                    Some("background") => StopTarget::Layer { layer: PerfLayer::Background },
                    _ => StopTarget::All,
                }
            };
            command(bus, SRC, Command::Perf(PerfCommand::Stop(target)));
        }
        "motion.freeze" => {
            let on = b(p, "on").unwrap_or(false);
            st.cantina_frozen = on;
            if !ours {
                command(bus, SRC, Command::Stage(StageCommand::Freeze { on }));
            }
        }
        "speech.synthesis.amplitude" if !ours => {
            let level = f(p, "amplitude").unwrap_or(0.0);
            ev(Event::Conversation(ConversationEvent::Mouth { level }));
        }
        "eye.command" => {
            let Some(pattern) = eye_pattern(p) else { return };
            let color = s(p, "color").map(str::to_owned);
            bus.update(SRC, |l: &mut LightsState| {
                l.eye_pattern = Some(pattern.clone());
                if color.is_some() {
                    l.eye_color = color;
                }
            });
            if !ours {
                // A live eye command (Jev/Claude `set_eye_color`, the CLI): the performer shows it.
                let duration = f(p, "duration").filter(|d| *d > 0.0);
                command(bus, SRC, Command::Perf(PerfCommand::Eyes { pattern, duration }));
            }
        }
        "chest.command" => {
            if let Some(c) = s(p, "command") {
                bus.update(SRC, |l: &mut LightsState| l.chest_mode = Some(c.to_owned()));
            }
        }
        "system.mode.change" => {
            if let Some(e) = s(p, "new_mode").and_then(engagement) {
                r3x_stage::set_engagement(bus, e);
            }
        }
        "service_status" | "service.status.update" => {
            let Some(name) = s(p, "service_name").or_else(|| s(p, "service")) else { return };
            let detail = s(p, "message").filter(|m| !m.is_empty()).map(str::to_owned);
            let status = service_status(s(p, "status").unwrap_or(""));
            ev(Event::Ops(OpsEvent::ServiceStatus { service: format!("cantina/{name}"), status, detail }));
        }
        "cli.response" => {
            let message = s(p, "message").unwrap_or_default().to_owned();
            if let Some(lib) = library_from_listing(&message) {
                bus.update(SRC, |m: &mut MusicState| {
                    if m.library.is_empty() {
                        m.library = lib
                    }
                });
            }
            ev(Event::Ops(OpsEvent::Console { message, is_error: b(p, "is_error").unwrap_or(false) }));
        }
        _ => {}
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use r3x_bus::Received;
    use r3x_contracts::{Body, Domain, EngagementState, MessageClass};
    use serde_json::json;

    fn drain(rx: &mut r3x_bus::EventReceiver) -> Vec<(Event, Option<String>)> {
        let mut out = vec![];
        while let Some(Some(Received::Message(env))) = rx_try(rx) {
            if let Body::Event(e) = &env.body {
                out.push((e.clone(), env.conversation_id.clone()));
            }
        }
        out
    }

    fn rx_try(rx: &mut r3x_bus::EventReceiver) -> Option<Option<Received>> {
        futures_util::FutureExt::now_or_never(rx.recv())
    }

    #[test]
    fn a_voice_turn() {
        let bus = Bus::default();
        let mut rx = bus.subscribe(Domain::Conversation);
        let mut st = TapState::default();
        let cid = json!("abc");
        translate(&bus, &mut st, "voice.listening.started", &json!({"conversation_id": cid, "timestamp": 1.0}), false);
        assert_eq!(bus.get::<ConversationState>().phase, ConversationPhase::Listening);
        translate(&bus, &mut st, "voice.listening.stopped", &json!({"conversation_id": cid, "transcript": "play something"}), false);
        translate(&bus, &mut st, "intent.detected", &json!({"conversation_id": cid, "intent_name": "play_music", "confidence": null}), false);
        translate(&bus, &mut st, "llm.response", &json!({"conversation_id": cid, "text": "On it", "is_complete": false}), false);
        translate(&bus, &mut st, "llm.response", &json!({"conversation_id": cid, "text": "On it.", "is_complete": true}), false);
        translate(&bus, &mut st, "speech.generation.started", &json!({"conversation_id": cid, "text": "On it.", "audio_t0": 1.0}), false);
        let evs = drain(&mut rx);
        assert!(evs.iter().all(|(_, c)| c.as_deref() == Some("abc")), "turn id adopted");
        let kinds: Vec<_> = evs.iter().map(|(e, _)| e.topic()).collect();
        assert_eq!(
            kinds,
            [
                "conversation.listening_started",
                "conversation.listening_stopped",
                "conversation.intent_detected",
                "conversation.reply_delta",
                "conversation.reply",
                "conversation.speech_started"
            ]
        );
        let c = bus.get::<ConversationState>();
        assert_eq!((c.phase, c.conversation_id.as_deref()), (ConversationPhase::Speaking, Some("abc")));
    }

    #[test]
    fn music_dj_shows_mode_and_status() {
        let bus = Bus::default();
        let mut st = TapState::default();
        let track = json!({"track": {"track_id": "1", "title": "Cantina Band", "artist": "Figrin", "duration": 160.0, "bpm": 120.0}});
        translate(&bus, &mut st, "music.playback.started", &track, false);
        let m = bus.get::<MusicState>();
        assert!(m.playing);
        assert_eq!(m.track.unwrap().bpm, Some(120.0));
        translate(&bus, &mut st, "music.playback.stopped", &json!({}), false);
        assert!(!bus.get::<MusicState>().playing);

        translate(&bus, &mut st, "music.library.updated", &json!({"tracks": {"b": {"title": "B"}, "a": {}}}), false);
        assert_eq!(bus.get::<MusicState>().library, ["B", "a"]);

        translate(&bus, &mut st, "dj.mode.changed", &json!({"is_active": true}), false);
        assert!(bus.get::<DjState>().active);

        translate(&bus, &mut st, "system.mode.change", &json!({"old_mode": "IDLE", "new_mode": "INTERACTIVE"}), false);
        assert_eq!(bus.get::<EngagementState>().engagement, Engagement::Interactive);
        translate(&bus, &mut st, "system.mode.change", &json!({"mode": "dj"}), false);
        assert_eq!(bus.get::<EngagementState>().engagement, Engagement::Interactive, "dj/standard variant ignored");
    }

    #[test]
    fn library_listing() {
        assert_eq!(library_from_listing("Available tracks:\n1. Zed\n2. Alpha"), Some(vec!["Alpha".into(), "Zed".into()]));
        assert_eq!(library_from_listing("Playing X"), None);
        assert_eq!(service_status("ServiceStatus.RUNNING"), ServiceStatus::Running);
    }

    /// CantinaOS's show requests and eye commands become perf commands; our own echoes do not.
    #[tokio::test]
    async fn requests_become_perf_commands() {
        let bus = Bus::default();
        let mut perf = bus.take_commands(MessageClass::Perf).unwrap();
        let mut st = TapState::default();
        let claude = json!({"id": "excited", "source": "claude", "params": {"intensity": 1.2}});
        translate(&bus, &mut st, "show.perform", &claude, true); // echo: ignored
        translate(&bus, &mut st, "show.perform", &claude, false);
        translate(&bus, &mut st, "eye.command", &json!({"pattern": "angry", "color": "red"}), false);
        translate(&bus, &mut st, "show.stop", &json!({"layer": "show"}), false);
        let mut got = vec![];
        for _ in 0..3 {
            let req = perf.recv().await.unwrap();
            got.push((req.source(), req.command.clone()));
            req.ack(Ack::Accepted);
        }
        assert!(got.contains(&(Source::Claude, Command::Perf(PerfCommand::Play { id: "excited".into(), intensity: 1.2, speed: 1.0, layer: None }))));
        assert!(got.contains(&(Source::Bridge, Command::Perf(PerfCommand::Eyes { pattern: "angry".into(), duration: None }))));
        assert!(got.contains(&(Source::Bridge, Command::Perf(PerfCommand::Stop(StopTarget::Layer { layer: PerfLayer::Show })))));
        assert_eq!(bus.get::<LightsState>().eye_color.as_deref(), Some("red"));
        assert_eq!(eye_pattern(&json!({"command": "eye", "args": ["pattern", "happy"]})).as_deref(), Some("happy"));
    }
}

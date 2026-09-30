//! Standalone acceptance (plan §9 Phases 5-7): the whole robot in one process, no CantinaOS,
//! no network, no sound. `boot()` wires the real crates exactly as `r3x-runtime` does by
//! default: voice (scripted STT, TTS replayed from the recorded ElevenLabs audio, a null
//! output device at 6x), the Rust brain (Claude + Jev replayed), the Rust music engine on
//! the voice's mixer with the real library, the performer with mock LED/servo drivers, and
//! the gateway. The §8 smoke corpus is spoken turn by turn through push-to-talk and each turn
//! is compared with the CantinaOS recording by the shared normaliser (`common/parity.rs`):
//! intent, actions, reply, spoken lines, and the latency legs (stop -> intent, -> first reply
//! chunk, -> first audible sample) against CantinaOS's (the Phase 0 baseline turns).
//!
//! On top of the corpus: the DJ set runs into a real transition (commentary cached, played
//! through the voice with mouth movement, crossfade to the recorded next pick), and the
//! first turn's `{cue:beat_bop}` tag is performed by the performer.
//! (One test per binary: it sets process env.)

#[path = "../../r3x-brain/tests/common/parity.rs"]
mod parity;

use std::sync::{Arc, Mutex};
use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use r3x_bus::{Bus, Received};
use r3x_contracts::{
    Ack, Body, ClientInfo, Command, ConversationEvent, Engagement, Envelope, Event, MusicEvent, RobotProfile, Source,
    StageCommand,
};
use r3x_gateway::ClientAuth;
use r3x_runtime::{all_classes, brain::BrainMode, performer::DriverOptions, RuntimeConfig};
use serde_json::json;
use tokio::time::Instant;
use tokio_tungstenite::tungstenite::{client::IntoClientRequest, http::HeaderValue, Message};

type Log = Arc<Mutex<Vec<parity::Logged>>>;

/// Differences from the recording that the real engine causes on purpose.
const ALLOWED: &[(&str, &str)] = &[(
    r"turn [12] say (play the next track|stop the music): (reply: expected .*, got \[\]|spoken: expected .*, got \[\]|timing (first_chunk|speech): missing.*)$",
    "the Rust engine's `next` works outside DJ mode (§7b), so a track really starts and `stop` then stops *that* one: the router's \
     outcome - part of Claude's prompt - differs from what CantinaOS sent, and the corpus has no reply recorded for it",
)];

/// Until `pred` holds for a logged envelope after index `from` (or `secs` pass).
async fn wait_for(log: &Log, from: usize, secs: u64, pred: impl Fn(&Envelope) -> bool) -> bool {
    let until = Instant::now() + Duration::from_secs(secs);
    while Instant::now() < until {
        if log.lock().unwrap()[from..].iter().any(|(_, e)| pred(e)) {
            return true;
        }
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
    false
}

// ~90 s (the corpus replays at recorded pace): kept out of `cargo test --workspace`. Run it
// before merging runtime/brain/voice/music changes and before the live check:
// `cargo test -p r3x-runtime --test standalone_acceptance -- --ignored --nocapture`.
#[ignore = "slow acceptance (~90 s): cargo test -p r3x-runtime --test standalone_acceptance -- --ignored"]
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn standalone_runs_the_corpus_end_to_end() {
    if std::env::var_os("R3X_TEST_LOG").is_some() {
        let _ = r3x_ops::init_tracing("info");
    }
    let repo = parity::repo();
    let tmp = std::env::temp_dir().join(format!("r3x-standalone-{}", std::process::id()));
    std::fs::create_dir_all(&tmp).unwrap();
    for (k, v) in [
        ("R3X_FIXTURES", "replay".to_string()),
        ("R3X_FIXTURE_DIR", repo.join("fixtures/smoke-voice").display().to_string()),
        ("R3X_FIXTURE_PACE", "1".into()), // recorded Jev/Claude/TTS timing: the legs are honest
        ("R3X_MEMORY_DB", tmp.join("memory.sqlite").display().to_string()),
        ("R3X_MUSIC", "rust".into()),
        ("R3X_VISION", "0".into()),
        ("R3X_AUDIO", "null".into()),
        // 6x: the DJ's 15 s lookahead caches the transition line before the intro track (193 s)
        // reaches its ending-soon mark 27 s later, as it would live.
        ("R3X_NULL_AUDIO_SPEED", "6".into()),
        ("MUSIC_DIR", repo.join("audio/music").display().to_string()),
        ("R3X_BEAT_CACHE_DIR", tmp.join("beats").display().to_string()),
        ("ENABLE_BEAT_ANALYSIS", "0".into()),
        ("R3X_SEMANTIC", "0".into()),
        ("FORCE_MOCK_LED_CONTROLLER", "1".into()),
        ("FORCE_MOCK_CHEST", "1".into()),
        ("FORCE_MOCK_SERVO", "1".into()),
    ] {
        std::env::set_var(k, v);
    }

    let bus = Bus::default();
    let log: Log = Arc::default();
    let mut all = bus.subscribe_all();
    let l = log.clone();
    tokio::spawn(async move {
        while let Some(Received::Message(env)) = all.recv().await {
            l.lock().unwrap().push((Instant::now(), env));
        }
    });

    let voice_cfg = r3x_voice::VoiceSettings::from_env().unwrap();
    assert!(voice_cfg.replay.is_some() && voice_cfg.null_audio == Some(6.0));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    let cfg = RuntimeConfig {
        bind: listener.local_addr().unwrap(),
        clients: vec![ClientAuth { token: "t0k".into(), info: ClientInfo { name: "panel".into(), source: Source::Ui, classes: all_classes() } }],
        origins: vec!["http://localhost:5391".into()],
        profile: Some(Arc::new(RobotProfile::load(r3x_runtime::default_profile_path()).unwrap())),
        bridge: None,
        session_log: None,
        logs: None,
        voice: Some(voice_cfg),
        mouse: false,
        show_dir: r3x_runtime::performer::default_show_dir(),
        drivers: Some(DriverOptions { leds: true }),
    };
    let rt = r3x_runtime::boot(bus.clone(), cfg, None, BrainMode::Rust).await.unwrap();
    let stack = rt.voice.as_ref().unwrap();
    let (voice, script) = (stack.voice.clone(), stack.script.clone().expect("replay STT is scripted"));
    assert!(rt.music.is_some() && rt.brain.is_some());
    tokio::spawn(rt.serve(listener));

    // The panel's path: engage through the gateway (token + Origin), acked by the stage.
    let mut req = format!("ws://127.0.0.1:{port}/").into_client_request().unwrap();
    req.headers_mut().insert("Authorization", HeaderValue::from_static("Bearer t0k"));
    req.headers_mut().insert("Origin", HeaderValue::from_static("http://localhost:5391"));
    let mut ws = None;
    for _ in 0..50 {
        if let Ok((w, _)) = tokio_tungstenite::connect_async(req.clone()).await {
            ws = Some(w);
            break;
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    let mut ws = ws.expect("gateway up");
    let engage = Command::Stage(StageCommand::SetEngagement { engagement: Engagement::Interactive });
    ws.send(Message::Text(json!({"kind": "command", "id": "e", "body": engage}).to_string().into())).await.unwrap();
    let engaged = async {
        while let Some(Ok(Message::Text(t))) = ws.next().await {
            let env: Envelope = serde_json::from_str(t.as_str()).unwrap();
            if let (Body::Ack(a), Some("e")) = (&env.body, env.re.as_deref()) {
                return a.clone();
            }
        }
        Ack::rejected("socket closed")
    };
    assert_eq!(tokio::time::timeout(Duration::from_secs(5), engaged).await.unwrap(), Ack::Accepted);
    drop(ws);

    // Speak the corpus, one turn at a time, through push-to-talk.
    let trace = parity::load_trace("smoke-voice");
    let said: Vec<String> = trace
        .iter()
        .filter(|r| r["topic"] == "voice.listening.stopped")
        .filter_map(|r| r["payload"]["transcript"].as_str().map(str::to_owned))
        .collect();
    let mut starts = Vec::new();
    for text in &said {
        // Idle: nothing speaking and nothing queued for a moment.
        let mut quiet = 0;
        let until = Instant::now() + Duration::from_secs(30);
        while quiet < 15 && Instant::now() < until {
            quiet = if voice.speaker().is_speaking() { 0 } else { quiet + 1 };
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
        let from = log.lock().unwrap().len();
        let marker = bus.stamp(Source::System, None, Body::Ack(Ack::Accepted)).seq;
        script.hear(text.clone());
        assert_eq!(voice.ptt_start("test").await, Ack::Accepted, "{text}");
        tokio::time::sleep(Duration::from_millis(80)).await; // the silent mic streams
        let stop = Instant::now();
        assert_eq!(voice.ptt_stop(None).await, Ack::Accepted);
        starts.push((marker, format!("say {text}"), Some(stop)));

        // Wait for the reply to be heard to the end.
        let turn = {
            assert!(wait_for(&log, from, 5, |e| matches!(e.body, Body::Event(Event::Conversation(ConversationEvent::ListeningStopped { .. })))).await);
            let l = log.lock().unwrap();
            l[from..]
                .iter()
                .find(|(_, e)| matches!(e.body, Body::Event(Event::Conversation(ConversationEvent::ListeningStopped { .. }))))
                .and_then(|(_, e)| e.conversation_id.clone())
                .unwrap()
        };
        // Heard to the end - unless there is no reply to hear (see ALLOWED).
        let mine = |e: &Envelope| e.conversation_id.as_deref() == Some(turn.as_str());
        if wait_for(&log, from, 6, |e| mine(e) && matches!(e.body, Body::Event(Event::Conversation(ConversationEvent::Reply { .. })))).await {
            let heard = wait_for(&log, from, 20, |e| mine(e) && matches!(e.body, Body::Event(Event::Conversation(ConversationEvent::SpeechEnded)))).await;
            assert!(heard, "{text}: the reply was never heard");
        }
        if text == "start dj mode" {
            // Let the set run into its first transition (6x: ending-soon ~27 s after the start).
            let crossfaded = wait_for(&log, from, 60, |e| matches!(e.body, Body::Event(Event::Music(MusicEvent::CrossfadeComplete { .. })))).await;
            assert!(crossfaded, "the DJ transition never completed");
        }
    }
    tokio::time::sleep(Duration::from_secs(3)).await;
    let log = log.lock().unwrap().clone();

    // Per-turn parity with CantinaOS. The DJ turn also contains the transition the recording
    // stopped short of: split it off and check it on its own.
    let exp = parity::cantina_turns(&trace);
    let mut act = parity::rust_turns(&log, &starts);
    let dj = act.iter().position(|t| t.label == "say start dj mode").unwrap();
    let mut transition = Vec::new();
    for kind in ["music.crossfade(", "play_cached", "cache_speech"] {
        let want = exp[dj].actions.iter().filter(|a| a.starts_with(kind)).count();
        let mut seen = 0;
        act[dj].actions.retain(|a| {
            if !a.starts_with(kind) {
                return true;
            }
            seen += 1;
            if seen > want {
                transition.push(a.clone());
            }
            seen <= want
        });
    }
    eprintln!("latency (ms)            cantina -> standalone");
    for (e, a) in exp.iter().zip(&act) {
        let legs: Vec<String> = e
            .timing
            .iter()
            .map(|(leg, ms)| format!("{leg} {ms:.0}->{}", a.timing.iter().find(|(l, _)| l == leg).map_or("-".into(), |(_, m)| format!("{m:.0}"))))
            .collect();
        eprintln!("  {:<42} {}", e.label, legs.join("  "));
    }
    parity::check_allowing("standalone smoke-voice", exp, act, ALLOWED);

    assert!(transition.contains(&"music.crossfade(Aloogahoo)".to_string()), "transition: {transition:?}");
    assert!(transition.contains(&"play_cached".to_string()), "transition commentary: {transition:?}");
    let bodies = || log.iter().map(|(_, e)| e);
    let cached_ok = bodies().filter(|e| matches!(e.body, Body::Event(Event::Conversation(ConversationEvent::CachedPlaybackEnded { ok: true, .. })))).count();
    assert!(cached_ok >= 2, "intro + transition commentary played to the end ({cached_ok})");
    let cached_mouth = bodies()
        .filter(|e| matches!(e.body, Body::Event(Event::Conversation(ConversationEvent::Mouth { .. }))))
        .filter(|e| e.conversation_id.as_deref().is_some_and(|c| c.starts_with("cached-")))
        .count();
    assert!(cached_mouth > 10, "cached commentary moves the mouth ({cached_mouth} levels)");
    // The DJ intro's own line (a show `speak`, no turn) went through the voice.
    let show_line = bodies().any(|e| matches!(e.body, Body::Event(Event::Conversation(ConversationEvent::SpeechStarted))) && e.conversation_id.is_none());
    assert!(show_line, "the DJ intro's line was spoken");
    let _ = std::fs::remove_dir_all(&tmp);
}

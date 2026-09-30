//! `dj test` end to end, offline: the standalone runtime (replayed Claude + ElevenLabs, the
//! real music engine and library on a 4x null audio device, performer on mock drivers) runs
//! `dj test` from the console and one full DJ cycle - seek to just before the ending-soon mark,
//! duck, cached commentary, crossfade, unduck, next track playing - and the order is checked
//! in the session log (the JSONL `./r3x` writes to `logs/`).
//! (One test per binary: it sets process env.)

#[path = "../../r3x-brain/tests/common/parity.rs"]
mod parity;

use std::sync::Arc;
use std::time::Duration;

use r3x_bus::Bus;
use r3x_contracts::{Command, CommentaryStatus, DjState, IntentCommand, MusicState, RobotProfile, Source};
use r3x_runtime::{brain::BrainMode, performer::DriverOptions, RuntimeConfig};
use tokio::time::Instant;

// ~15 s: kept out of `cargo test --workspace`.
#[ignore = "slow acceptance (~15 s): cargo test -p r3x-runtime --test dj_test_acceptance -- --ignored"]
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn dj_test_runs_a_full_transition_offline() {
    if std::env::var_os("R3X_TEST_LOG").is_some() {
        let _ = r3x_ops::init_tracing("info");
    }
    let repo = parity::repo();
    let tmp = std::env::temp_dir().join(format!("r3x-djtest-{}", std::process::id()));
    std::fs::create_dir_all(&tmp).unwrap();
    for (k, v) in [
        ("R3X_FIXTURES", "replay".to_string()),
        ("R3X_FIXTURE_DIR", repo.join("fixtures/smoke-voice").display().to_string()),
        ("R3X_FIXTURE_PACE", "0".into()),
        ("R3X_MEMORY_DB", tmp.join("memory.sqlite").display().to_string()),
        ("R3X_MUSIC", "rust".into()),
        ("R3X_VISION", "0".into()),
        ("R3X_AUDIO", "null".into()),
        ("R3X_NULL_AUDIO_SPEED", "4".into()),
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
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let cfg = RuntimeConfig {
        bind: listener.local_addr().unwrap(),
        clients: vec![],
        origins: vec![],
        profile: Some(Arc::new(RobotProfile::load(r3x_runtime::default_profile_path()).unwrap())),
        bridge: None,
        session_log: Some(tmp.clone()),
        logs: None,
        voice: Some(r3x_voice::VoiceSettings::from_env().unwrap()),
        mouse: false,
        show_dir: r3x_runtime::performer::default_show_dir(),
        drivers: Some(DriverOptions { leds: true }),
    };
    let rt = r3x_runtime::boot(bus.clone(), cfg, None, BrainMode::Rust).await.unwrap();
    assert!(rt.music.is_some() && rt.brain.is_some());

    let t0 = Instant::now();
    let line = Command::Intent(IntentCommand::Console { line: "dj test".into() });
    assert!(bus.command(Source::Cli, None, line).await.is_accepted());

    // The recorded picks: Bai Tee Tee first, Aloogahoo next (the transition line is recorded).
    let mut dj = bus.watch::<DjState>();
    let ready = tokio::time::timeout(Duration::from_secs(10), dj.wait_for(|d| d.commentary == CommentaryStatus::Ready)).await.is_ok_and(|r| r.is_ok());
    // (Each wait's `Ref` is dropped at once: holding it would block the bus's writers.)
    assert!(ready, "the transition line was cached ahead: {:?}", bus.get::<DjState>());
    let mut music = bus.watch::<MusicState>();
    let landed = tokio::time::timeout(
        Duration::from_secs(40),
        music.wait_for(|m| m.playing && m.track.as_ref().is_some_and(|t| t.title == "Aloogahoo")),
    )
    .await
    .is_ok_and(|r| r.is_ok());
    assert!(landed, "the next track never started: {:?}", bus.get::<MusicState>());
    let mut dj = bus.watch::<DjState>();
    let settled = tokio::time::timeout(Duration::from_secs(30), dj.wait_for(|d| d.step.is_none())).await.is_ok_and(|r| r.is_ok());
    assert!(settled, "the transition plan never finished: {:?}", bus.get::<DjState>());
    tokio::time::sleep(Duration::from_millis(500)).await; // session log flush
    eprintln!("dj test: a full cycle in {:.1} s", t0.elapsed().as_secs_f64());
    let d = bus.get::<DjState>();
    assert_eq!(d.current.map(|t| t.title).as_deref(), Some("Aloogahoo"));
    assert!(bus.get::<MusicState>().playing);
    drop(rt);

    // The cycle, in order, from the session log.
    let file = std::fs::read_dir(&tmp).unwrap().filter_map(Result::ok).map(|e| e.path()).find(|p| p.extension().is_some_and(|x| x == "jsonl")).unwrap();
    let rows: Vec<serde_json::Value> =
        std::fs::read_to_string(&file).unwrap().lines().filter_map(|l| serde_json::from_str(l).ok()).collect();
    let title = |r: &serde_json::Value| r["payload"]["track"]["title"].as_str().unwrap_or_default().to_owned();
    let seen: Vec<String> = rows
        .iter()
        .filter_map(|r| {
            let topic = r["topic"].as_str()?;
            Some(match topic {
                "music.track_started" => format!("{topic} {}", title(r)),
                "music.seek" => topic.to_owned(),
                "dj.started" | "conversation.cache_speech" | "conversation.speech_cached" | "music.track_ending_soon" | "music.ducked"
                | "conversation.play_cached" | "music.crossfade" | "music.crossfade_complete" | "conversation.cached_playback_ended"
                | "music.unducked" => topic.to_owned(),
                _ => return None,
            })
        })
        .collect();
    eprintln!("{seen:#?}");
    let want = [
        "dj.started",
        "music.track_started Bai Tee Tee",
        "music.seek",
        "music.track_ending_soon",
        "music.ducked",
        "conversation.play_cached",
        "music.crossfade",
        "music.track_started Aloogahoo",
        "music.crossfade_complete",
        "music.unducked",
    ];
    // `want` as a subsequence of what happened (other events interleave).
    let mut it = seen.iter();
    for w in want {
        assert!(it.any(|s| s == w), "{w} missing or out of order in {seen:#?}");
    }
    let pos = |w: &str| seen.iter().position(|s| s == w).unwrap();
    assert!(pos("conversation.speech_cached") < pos("music.track_ending_soon"), "the line was ready before the mark");
    assert!(pos("conversation.cached_playback_ended") < pos("music.unducked"), "unduck waits for the line");
    assert_eq!(seen.iter().filter(|s| *s == "conversation.cache_speech").count(), 1, "one commentary line (no intro)");
    let _ = std::fs::remove_dir_all(&tmp);
}

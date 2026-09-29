//! Engine behaviour and the Phase 4 acceptance checks, on an in-memory mixer rendered in real
//! time by a fake device (no speakers, no network, no paid calls).

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use r3x_audio::decode::wav_bytes;
use r3x_audio::mixer::{mixer, BusId, MixerHandle, RenderTime};
use r3x_audio::speech_cache::SpeechCache;
use r3x_music::cantina::CantinaMusic;
use r3x_music::engine::{Engine, EngineConfig, EngineEvent};
use r3x_music::library::Library;
use r3x_music::semantic::{Match, Search};
use serde_json::{json, Value};

const RATE: u32 = 8000;

/// (t, music, speech, sfx) bus RMS after each buffer.
type Level = (f64, f32, f32, f32);

/// Renders the mixer in 10 ms buffers at real-time pace; samples bus levels.
struct FakeDevice {
    stop: Arc<AtomicBool>,
    levels: Arc<Mutex<Vec<Level>>>,
}

impl FakeDevice {
    fn start(mut m: r3x_audio::mixer::Mixer, h: MixerHandle) -> Self {
        let stop = Arc::new(AtomicBool::new(false));
        let levels = Arc::new(Mutex::new(Vec::new()));
        let (s, l) = (stop.clone(), levels.clone());
        std::thread::spawn(move || {
            let t0 = Instant::now();
            let mut out = vec![0.0f32; (RATE / 100) as usize * 2];
            let mut n = 0u32;
            while !s.load(Ordering::Relaxed) {
                m.render(&mut out, &RenderTime::now());
                n += 1;
                l.lock().unwrap().push((
                    t0.elapsed().as_secs_f64(),
                    h.level(BusId::Music).rms,
                    h.level(BusId::Speech).rms,
                    h.level(BusId::Sfx).rms,
                ));
                let due = t0 + Duration::from_millis(10 * n as u64);
                std::thread::sleep(due.saturating_duration_since(Instant::now()));
            }
        });
        Self { stop, levels }
    }

    fn window(&self, from: f64, to: f64) -> Vec<Level> {
        self.levels.lock().unwrap().iter().filter(|l| l.0 >= from && l.0 < to).cloned().collect()
    }

    fn now(&self) -> f64 {
        self.levels.lock().unwrap().last().map_or(0.0, |l| l.0)
    }
}

impl Drop for FakeDevice {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
    }
}

fn tone(dir: &Path, name: &str, hz: f64, secs: f64) -> PathBuf {
    let n = (RATE as f64 * secs) as usize;
    let s: Vec<f32> = (0..n).map(|i| (0.4 * (std::f64::consts::TAU * hz * i as f64 / RATE as f64).sin()) as f32).collect();
    let p = dir.join(name);
    std::fs::write(&p, wav_bytes(RATE, 1, &s)).unwrap();
    p
}

fn library(tag: &str, tracks: &[(&str, f64, f64)]) -> (PathBuf, Library) {
    let dir = std::env::temp_dir().join(format!("r3x-music-{tag}-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    for (name, hz, secs) in tracks {
        tone(&dir, name, *hz, *secs);
    }
    let lib = Library::scan(&dir, true);
    (dir, lib)
}

fn cfg() -> EngineConfig {
    EngineConfig { ending_threshold_s: 1.0, tick: Duration::from_millis(50), ..Default::default() }
}

fn rms_tone() -> f32 {
    0.4 / 2f32.sqrt()
}

fn mean(v: &[f32]) -> f32 {
    v.iter().sum::<f32>() / v.len().max(1) as f32
}

struct FakeSearch(Vec<&'static str>);
impl Search for FakeSearch {
    fn search(&self, _: &str, _: Option<&str>, limit: usize) -> anyhow::Result<Vec<Match>> {
        Ok(self.0.iter().take(limit).enumerate().map(|(i, k)| Match { key: k.to_string(), score: 1.0 - i as f32 * 0.1, positive: 0.0, negative: None }).collect())
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn next_outside_dj_ending_soon_from_position_and_semantic_walk() {
    let (dir, lib) = library("next", &[("Alpha.wav", 440.0, 20.0), ("Beta.wav", 660.0, 2.5), ("Band - Gamma.wav", 550.0, 20.0)]);
    assert_eq!(lib.keys(), ["Alpha", "Gamma", "Beta"], "sorted by file name, key = title");
    let (m, h) = mixer(RATE, 2);
    let _dev = FakeDevice::start(m, h.clone());
    let e = Engine::spawn(h, lib, cfg());
    let mut ev = e.subscribe();

    assert_eq!(e.play(Some("2".into()), "cli").await, Ok("Now playing: Gamma".into()));
    assert_eq!(e.next("cli").await, Ok("Now playing: Beta".into()), "next walks the library outside DJ mode");
    // Beta is 2.5 s with a 1 s threshold: ending-soon comes from the position, then it finishes.
    let t0 = Instant::now();
    let (mut ending, mut finished) = (None, None);
    while finished.is_none() && t0.elapsed() < Duration::from_secs(5) {
        match tokio::time::timeout(Duration::from_secs(5), ev.recv()).await.unwrap().unwrap() {
            EngineEvent::EndingSoon { track, remaining_s } => ending = Some((track.key, remaining_s, t0.elapsed())),
            EngineEvent::Finished { track } => finished = Some(track.key),
            _ => {}
        }
    }
    let (k, remaining, at) = ending.expect("ending soon");
    assert_eq!(k, "Beta");
    assert!(remaining <= 1.0 && remaining > 0.7, "{remaining}");
    assert!(at > Duration::from_millis(1300), "not a wall-clock guess at start: {at:?}");
    assert_eq!(finished.as_deref(), Some("Beta"));
    assert!(!e.status().borrow().playing);

    // Semantic: the winner skips the current track; `next` walks the ranking.
    e.set_search(Arc::new(FakeSearch(vec!["Alpha", "Gamma", "Beta"])));
    assert_eq!(e.play(Some("@semantic upbeat".into()), "jev").await, Ok("Now playing: Alpha".into()));
    assert_eq!(e.next("cli").await, Ok("Now playing: Gamma".into()));
    assert_eq!(e.play(Some("something funky".into()), "claude").await, Ok("Now playing: Alpha".into()), "mood words go semantic");
    assert!(e.play(Some("zzz qqq".into()), "cli").await.unwrap_err().starts_with("No music found"));
    assert_eq!(e.stop().await, Ok("Stopped music playback".into()));
    std::fs::remove_dir_all(dir).ok();
}

/// Plan §9 Phase 4 acceptance, part 1: the DJ transition CantinaOS's BrainService plans
/// (`_create_commentary_transition_steps`: duck -> cached commentary -> crossfade while it
/// plays -> unduck when it ends), driven through the CantinaOS adapter with the exact tap
/// payloads the Python timeline emits, the commentary from recorded fixtures.
#[tokio::test(flavor = "multi_thread")]
async fn dj_transition_on_the_rust_engine() {
    let (dir, lib) = library("dj", &[("Alpha.wav", 440.0, 40.0), ("Beta.wav", 660.0, 40.0)]);
    let (m, h) = mixer(RATE, 2);
    let dev = FakeDevice::start(m, h.clone());
    let e = Engine::spawn(h.clone(), lib, cfg());
    let tap: Arc<Mutex<Vec<(String, Value)>>> = Arc::default();
    let sink = tap.clone();
    let cm = CantinaMusic::attach(&e, Arc::new(move |t: &str, p: Value| sink.lock().unwrap().push((t.to_owned(), p))));
    cm.on_connect();
    let wait_for = |topic: &'static str, pred: fn(&Value) -> bool| {
        let tap = tap.clone();
        async move {
            let t0 = Instant::now();
            loop {
                if let Some(p) = tap.lock().unwrap().iter().find(|(t, p)| t == topic && pred(p)).map(|x| x.1.clone()) {
                    return p;
                }
                assert!(t0.elapsed() < Duration::from_secs(10), "no {topic}");
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        }
    };
    let lib_ev = wait_for("music.library.updated", |_| true).await;
    assert_eq!(lib_ev["track_count"], 2);

    // Commentary from the recorded fixture (Python CachedSpeechService's job; r3x-audio's cache).
    let fx = PathBuf::from(concat!(env!("CARGO_MANIFEST_DIR"), "/../../../fixtures/smoke-voice/tts/1.pcm"));
    let pcm: Vec<i16> = std::fs::read(&fx).unwrap().chunks_exact(2).map(|c| i16::from_le_bytes([c[0], c[1]])).collect();
    let cache = SpeechCache::new(h.clone());
    let pcm = &pcm[..pcm.len().min(24_000 * 4)]; // first 4 s of the line
    let line_s = cache.insert_pcm24("dj-intro-beta", pcm);

    cm.on_tap("dj.mode.changed", &json!({"is_active": true}));
    cm.on_tap("music.command", &json!({"action": "play", "song_query": "Alpha", "conversation_id": null}));
    let started = wait_for("music.playback.started", |p| p["track"]["track_id"] == "Alpha").await;
    assert_eq!(started["track"]["title"], "Alpha");
    tokio::time::sleep(Duration::from_millis(500)).await;
    let t_full = dev.now();

    // 1. music_duck (level 0.5), 2. 0.5 s pre-duck delay
    cm.on_tap("audio.ducking.start", &json!({"level": 0.5, "fade_ms": 1500}));
    tokio::time::sleep(Duration::from_millis(500)).await;
    // 3. play_cached_speech (non-blocking)
    let t_speech = dev.now();
    let done = cache.play("dj-intro-beta", 1.0).unwrap();
    // 4. commentary establish delay, 5. music_crossfade (2 s here; 8 s in DJ mode)
    tokio::time::sleep(Duration::from_millis(500)).await;
    let t_xf = dev.now();
    cm.on_tap("music.command", &json!({"action": "crossfade", "song_query": "Beta", "fade_duration": 2.0, "crossfade_id": "xf-1"}));
    let xs = wait_for("crossfade.started", |p| p["crossfade_id"] == "xf-1").await;
    assert_eq!((xs["from_track"]["track_id"].as_str(), xs["to_track"]["track_id"].as_str()), (Some("Alpha"), Some("Beta")));
    let xc = wait_for("crossfade.complete", |p| p["crossfade_id"] == "xf-1").await;
    assert_eq!(xc["status"], "success");
    let t_xf_end = dev.now();
    wait_for("music.playback.started", |p| p["track"]["track_id"] == "Beta").await;
    // 6. wait for speech end, 7. unduck (speech.cache.playback.completed + audio.ducking.stop)
    assert!(done.await.unwrap(), "commentary played to the end");
    let t_speech_end = dev.now();
    cm.on_tap("speech.cache.playback.completed", &json!({"completion_status": "completed", "cache_key": "dj-intro-beta"}));
    cm.on_tap("audio.ducking.stop", &json!({"fade_ms": 1500}));
    tokio::time::sleep(Duration::from_millis(400)).await;
    let t_after = dev.now();
    tokio::time::sleep(Duration::from_millis(300)).await;

    let music = |a: f64, b: f64| dev.window(a, b).iter().map(|l| l.1).collect::<Vec<_>>();
    let full = mean(&music(t_full - 0.3, t_full));
    assert!((full - rms_tone()).abs() < 0.02, "full level {full}");
    let during_speech = dev.window(t_speech + 0.1, t_xf);
    assert!(during_speech.iter().all(|l| l.2 > 0.0) || mean(&during_speech.iter().map(|l| l.2).collect::<Vec<_>>()) > 0.01, "commentary audible");
    let ducked = mean(&during_speech.iter().map(|l| l.1).collect::<Vec<_>>());
    assert!((ducked / full - 0.5).abs() < 0.05, "ducked to half: {}", ducked / full);
    // Equal-power: the two uncorrelated tones keep the music level flat through the fade
    // (a linear crossfade would dip ~3 dB at the midpoint).
    let xf = music(t_xf + 0.1, t_xf_end - 0.1);
    let (lo, hi) = xf.iter().fold((f32::MAX, 0.0f32), |(a, b), &v| (a.min(v), b.max(v)));
    assert!(lo > ducked * 0.85 && hi < ducked * 1.15, "flat through the crossfade: {lo}..{hi} around {ducked}");
    assert!(t_speech_end > t_xf_end, "the crossfade ran during the commentary ({line_s:.1} s)");
    let restored = mean(&music(t_after, t_after + 0.3));
    assert!((restored - rms_tone()).abs() < 0.02, "unducked: {restored}");
    let st = e.status().borrow().clone();
    assert_eq!((st.track.unwrap().key.as_str(), st.ducked, st.crossfading), ("Beta", false, false));
    let topics: Vec<String> = tap.lock().unwrap().iter().map(|x| x.0.clone()).collect();
    assert!(topics.iter().any(|t| t == "memory.set"), "DJ current_track to MemoryService");
    drop(dev);
    std::fs::remove_dir_all(dir).ok();
}

/// Plan §9 Phase 4 acceptance, part 2: SFX are audible with no sim open - a show `perf.sfx`
/// cue and a mode change reach the sfx bus of the host mixer (checked by its meter).
#[tokio::test(flavor = "multi_thread")]
async fn sfx_are_audible_without_the_sim() {
    use r3x_contracts::{Engagement, EngagementState, Event, PerfEvent, Source};
    let (m, h) = mixer(RATE, 2);
    let dev = FakeDevice::start(m, h.clone());
    let bus = r3x_bus::Bus::default();
    let kit = r3x_music::service::default_sfx_dirs();
    let dirs = if kit.iter().any(|d| d.join("Air Horn.mp3").is_file()) {
        kit
    } else {
        // The kit is gitignored; fall back to a generated clip with a kit-style name.
        let d = std::env::temp_dir().join(format!("r3x-sfx-kit-{}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        tone(&d, "Air Horn.wav", 700.0, 0.5);
        vec![d]
    };
    let bank = Arc::new(r3x_audio::sfx::SfxBank::new(h.clone(), dirs));
    let ding = r3x_music::service::default_mode_sound();
    assert!(ding.is_file(), "{}", ding.display());
    r3x_music::service::spawn_sfx(&bus, bank, Some(ding));
    tokio::task::yield_now().await;

    let before = h.level(BusId::Sfx).audible_buffers;
    bus.publish(Source::Bridge, None, Event::Perf(PerfEvent::Sfx { id: "air_horn".into() }));
    let t0 = Instant::now();
    while h.level(BusId::Sfx).audible_buffers < before + 10 {
        assert!(t0.elapsed() < Duration::from_secs(5), "show sfx never reached the sfx bus");
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
    let peak = dev.window(0.0, 1e9).iter().map(|l| l.3).fold(0.0f32, f32::max);
    assert!(peak > 0.05, "air horn level {peak}");
    tokio::time::sleep(Duration::from_secs(3)).await; // let it end

    let before = h.level(BusId::Sfx).audible_buffers;
    bus.update(Source::Bridge, |s: &mut EngagementState| s.engagement = Engagement::Interactive);
    let t0 = Instant::now();
    while h.level(BusId::Sfx).audible_buffers < before + 10 {
        assert!(t0.elapsed() < Duration::from_secs(5), "mode-change ding never reached the sfx bus");
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
}

/// r3x-brain's contract: music requests and the commentary cache over the r3x bus.
#[tokio::test(flavor = "multi_thread")]
async fn brain_requests_over_the_bus() {
    use r3x_contracts::{Body, ConversationEvent, Domain, Event, MusicEvent, MusicState, Source};
    let (dir, lib) = library("bus", &[("Alpha.wav", 440.0, 30.0), ("Beta.wav", 660.0, 30.0)]);
    let (m, h) = mixer(RATE, 2);
    let _dev = FakeDevice::start(m, h.clone());
    let bus = r3x_bus::Bus::default();
    let e = Engine::spawn(h.clone(), lib, cfg());
    r3x_music::service::spawn(&bus, &e);
    let fx = r3x_music::commentary::FixtureSynth::load(Path::new(concat!(env!("CARGO_MANIFEST_DIR"), "/../../../fixtures/smoke-voice"))).unwrap();
    let text = fx.texts().min_by_key(|t| t.len()).unwrap().clone(); // shortest recorded line
    r3x_music::commentary::spawn(&bus, Arc::new(SpeechCache::new(h.clone())), Arc::new(fx));
    let mut music = bus.subscribe(Domain::Music);
    let mut conv = bus.subscribe(Domain::Conversation);
    tokio::task::yield_now().await;
    async fn next_matching<T>(rx: &mut r3x_bus::EventReceiver, f: impl Fn(&Event) -> Option<T>) -> T {
        tokio::time::timeout(Duration::from_secs(20), async {
            loop {
                if let Some(r3x_bus::Received::Message(env)) = rx.recv().await {
                    if let Body::Event(ev) = &env.body {
                        if let Some(t) = f(ev) {
                            return t;
                        }
                    }
                }
            }
        })
        .await
        .expect("event")
    }

    bus.publish(Source::Timeline, None, Event::Music(MusicEvent::Play { query: Some("Alpha".into()) }));
    let t = next_matching(&mut music, |e| match e { Event::Music(MusicEvent::TrackStarted { track }) => Some(track.clone()), _ => None }).await;
    assert_eq!(t.title, "Alpha");
    let st: MusicState = bus.get();
    assert!(st.playing && st.library == ["Alpha", "Beta"]);
    bus.publish(Source::Timeline, None, Event::Music(MusicEvent::Duck { level: 0.5, fade_ms: 500.0 }));
    next_matching(&mut music, |e| matches!(e, Event::Music(MusicEvent::Ducked { .. })).then_some(())).await;
    bus.publish(Source::Timeline, None, Event::Conversation(ConversationEvent::CacheSpeech { key: "k1".into(), text }));
    let secs = next_matching(&mut conv, |e| match e { Event::Conversation(ConversationEvent::SpeechCached { key, duration_s }) if key == "k1" => Some(*duration_s), _ => None }).await;
    assert!(secs > 1.0);
    bus.publish(Source::Timeline, None, Event::Conversation(ConversationEvent::PlayCached { key: "k1".into(), playback_id: "p1".into() }));
    bus.publish(Source::Timeline, None, Event::Music(MusicEvent::Crossfade { track: "Beta".into(), duration_s: 1.0, id: "x1".into() }));
    next_matching(&mut music, |e| matches!(e, Event::Music(MusicEvent::CrossfadeComplete { id }) if id == "x1").then_some(())).await;
    let st: MusicState = bus.get();
    assert_eq!(st.track.unwrap().title, "Beta");
    assert!(st.ducked && st.position_s > 0.5 && st.position_t > 0.0, "beat-clock anchor published");
    let ok = next_matching(&mut conv, |e| match e { Event::Conversation(ConversationEvent::CachedPlaybackEnded { playback_id, ok }) if playback_id == "p1" => Some(*ok), _ => None }).await;
    assert!(ok);
    bus.publish(Source::Timeline, None, Event::Music(MusicEvent::Unduck { fade_ms: 500.0 }));
    bus.publish(Source::Timeline, None, Event::Music(MusicEvent::Stop));
    next_matching(&mut music, |e| matches!(e, Event::Music(MusicEvent::TrackStopped)).then_some(())).await;
    std::fs::remove_dir_all(dir).ok();
}

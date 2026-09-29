//! DJ mode end to end on stubs: start (intro line cached and played with ducking, `dj_intro`
//! sequence, lookahead requested at once), a transition with the cached line under a
//! crossfade, a transition whose commentary failed (urgent caching, then crossfade only),
//! recent-track avoidance, and stop cancelling everything.

use std::sync::{Arc, Mutex};
use std::time::Duration;

use r3x_brain::dj::{commentary_prompt, Context};
use r3x_brain::{Brain, BrainConfig, BrainDeps, Chooser};
use r3x_bus::{Bus, Received};
use r3x_contracts::{
    Ack, Body, Command, ConversationEvent, DjState, Domain, Envelope, Event, IntentCommand, MessageClass, MusicEvent, MusicState, OpsEvent,
    Source, StageState, Track,
};
use r3x_intent::{IntentRouter, JevClient, RouterConfig};
use r3x_llm::{ClaudeFixtures, LlmClient, Message, MessagesRequest};
use serde_json::json;

fn t(title: &str) -> Track {
    Track { title: title.into(), ..Default::default() }
}

fn fixtures() -> std::path::PathBuf {
    let dir = std::env::temp_dir().join(format!("r3x-brain-dj-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let rec = |prompt: String, text: &str| {
        let req = MessagesRequest::new(150).messages(vec![Message::user(prompt)]);
        json!({"key": ClaudeFixtures::key_for("create", &req), "method": "create", "wait": 1.0,
               "final": {"content": [{"type": "text", "text": text}]}})
        .to_string()
            + "\n"
    };
    // intro for A, transition A -> B; nothing for B -> C (that commentary fails)
    let body = rec(commentary_prompt(Context::Intro, &t("A"), None), "Here comes A!")
        + &rec(commentary_prompt(Context::Transition, &t("A"), Some(&t("B"))), "From A to B!");
    std::fs::write(dir.join("claude.jsonl"), body).unwrap();
    dir
}

#[tokio::test(start_paused = true)]
async fn dj_start_transition_fallback_and_stop() {
    let bus = Bus::default();
    bus.update(Source::System, |s: &mut StageState| {
        s.brain = true;
        s.autonomy = true; // Show mode: DJ transitions are autonomy
    });
    bus.update(Source::System, |m: &mut MusicState| m.library = vec!["A".into(), "B".into(), "C".into()]);
    let rec = Arc::new(Mutex::new(Vec::<Arc<Envelope>>::new()));
    let (r, mut all) = (rec.clone(), bus.subscribe_all());
    tokio::spawn(async move {
        while let Some(Received::Message(e)) = all.recv().await {
            r.lock().unwrap().push(e);
        }
    });
    // speech cache + music engine + performer stubs
    let (b, mut conv) = (bus.clone(), bus.subscribe(Domain::Conversation));
    tokio::spawn(async move {
        while let Some(Received::Message(e)) = conv.recv().await {
            match &e.body {
                Body::Event(Event::Conversation(ConversationEvent::CacheSpeech { key, .. })) => {
                    b.publish(Source::System, None, Event::Conversation(ConversationEvent::SpeechCached { key: key.clone(), duration_s: 4.0 }));
                }
                Body::Event(Event::Conversation(ConversationEvent::PlayCached { playback_id, .. })) => {
                    let (b, id) = (b.clone(), playback_id.clone());
                    tokio::spawn(async move {
                        tokio::time::sleep(Duration::from_secs(4)).await;
                        b.publish(Source::System, None, Event::Conversation(ConversationEvent::CachedPlaybackEnded { playback_id: id, ok: true }));
                    });
                }
                _ => {}
            }
        }
    });
    let (b, mut music) = (bus.clone(), bus.subscribe(Domain::Music));
    tokio::spawn(async move {
        while let Some(Received::Message(e)) = music.recv().await {
            let started = |title: &str| {
                b.update(Source::System, |m: &mut MusicState| {
                    m.playing = true;
                    m.track = Some(t(title));
                });
                b.publish(Source::System, None, Event::Music(MusicEvent::TrackStarted { track: t(title) }));
            };
            match &e.body {
                Body::Event(Event::Music(MusicEvent::Play { query: Some(q) })) => started(q),
                Body::Event(Event::Music(MusicEvent::Crossfade { track, id, .. })) => {
                    started(track);
                    b.publish(Source::System, None, Event::Music(MusicEvent::CrossfadeComplete { id: id.clone() }));
                }
                _ => {}
            }
        }
    });
    let mut perf = bus.take_commands(MessageClass::Perf).unwrap();
    tokio::spawn(async move {
        while let Some(req) = perf.recv().await {
            req.ack(Ack::Accepted);
        }
    });

    let first: Chooser = Arc::new(|_, o: &[String]| o.first().cloned());
    let deps = BrainDeps {
        llm: Some(LlmClient::replay(Arc::new(ClaudeFixtures::load(fixtures(), 1.0).unwrap()), "m")),
        router: IntentRouter::new(JevClient::new("", Duration::from_millis(800)), RouterConfig::default()),
        memory: None,
        latency: None,
        ptt: None,
        chooser: first,
    };
    let _brain = Brain::spawn(&bus, BrainConfig::default(), deps).unwrap();
    let console = |line: &str| Command::Intent(IntentCommand::Console { line: line.into() });

    assert!(bus.command(Source::Cli, None, console("dj start")).await.is_accepted());
    tokio::time::sleep(Duration::from_secs(8)).await;
    assert_eq!(bus.get::<DjState>(), DjState { active: true, current: Some(t("A")), next: Some(t("B")) });

    bus.publish(Source::System, None, Event::Music(MusicEvent::TrackEndingSoon { remaining_s: 30.0 }));
    tokio::time::sleep(Duration::from_secs(12)).await;
    // A and B were played recently, so the lookahead after the transition picks C
    assert_eq!(bus.get::<DjState>(), DjState { active: true, current: Some(t("B")), next: Some(t("C")) });

    // B -> C commentary has no fixture: urgent caching fails, 3 s grace, crossfade only
    bus.publish(Source::System, None, Event::Music(MusicEvent::TrackEndingSoon { remaining_s: 30.0 }));
    tokio::time::sleep(Duration::from_secs(12)).await;
    assert_eq!(bus.get::<MusicState>().track, Some(t("C")));

    assert!(bus.command(Source::Cli, None, console("dj stop")).await.is_accepted());
    tokio::time::sleep(Duration::from_secs(1)).await;
    assert!(!bus.get::<DjState>().active);

    let log = rec.lock().unwrap();
    let music: Vec<String> = log
        .iter()
        .filter_map(|e| match &e.body {
            Body::Event(Event::Music(m)) => match m {
                MusicEvent::Play { query } => Some(format!("play {}", query.clone().unwrap_or_default())),
                MusicEvent::Crossfade { track, .. } => Some(format!("xfade {track}")),
                MusicEvent::Duck { .. } => Some("duck".into()),
                MusicEvent::Unduck { .. } => Some("unduck".into()),
                MusicEvent::Stop => Some("stop".into()),
                _ => None,
            },
            Body::Event(Event::Conversation(ConversationEvent::PlayCached { .. })) => Some("say cached".into()),
            _ => None,
        })
        .collect();
    assert_eq!(
        music,
        ["play A", "duck", "say cached", "unduck", "duck", "say cached", "xfade B", "unduck", "xfade C", "stop"],
        "intro line with ducking; transition line under the crossfade; fallback crossfade; stop"
    );
    let plans: Vec<(String, String)> = log
        .iter()
        .filter_map(|e| match &e.body {
            Body::Event(Event::Ops(OpsEvent::PlanStarted { plan_id, layer })) => Some((plan_id.clone(), layer.clone())),
            _ => None,
        })
        .collect();
    assert!(plans[0].0.starts_with("dj-intro-") && plans[0].1 == "show");
    assert!(log.iter().any(|e| matches!(&e.body, Body::Command(Command::Perf(r3x_contracts::PerfCommand::Play { id, .. })) if id == "dj_intro")));
    assert!(log.iter().any(|e| matches!(&e.body, Body::Event(Event::Ops(OpsEvent::Console { message, .. })) if message == "DJ mode deactivated")));
}

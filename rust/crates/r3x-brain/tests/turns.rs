//! Turn behaviours the Phase 0 corpus does not exercise: a Claude-called tool dispatched as its
//! block closes, streaming tag stripping across chunks, the verbal-feedback second call, the
//! per-turn duplicate-reply drop, typed turns, brain-off refusal and `debug latency`.

use std::sync::{Arc, Mutex};
use std::time::Duration;

use r3x_brain::{random_chooser, Brain, BrainConfig, BrainDeps};
use r3x_bus::{Bus, Received};
use r3x_contracts::{
    Ack, Body, Command, ConversationEvent, Domain, Envelope, Event, IntentCommand, MessageClass, MusicEvent, MusicState, OpsEvent,
    PerfCommand, Source, StageState, Track,
};
use r3x_intent::{IntentRouter, JevClient, RouterConfig};
use r3x_llm::{prompt, request, ClaudeFixtures, LlmClient, Message, MessagesRequest};
use serde_json::json;

fn fixture_dir() -> std::path::PathBuf {
    let dir = std::env::temp_dir().join(format!("r3x-brain-turns-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let turn = prompt::turn_request("sys", vec![Message::user("play something funky")], request::default_tools(), false);
    let result = json!({"success": true, "track": "Funky Town", "requested": "something funky",
        "selected": r3x_intent::catalogue::naming_phrase("something funky"), "action": "play", "message": "Now playing: Funky Town"});
    let params = json!({"track": "something funky"});
    let fb: MessagesRequest = prompt::verbal_feedback_request(None, "play_music", &params, &result, true);
    let lines = [
        json!({"key": ClaudeFixtures::key_for("stream", &turn), "method": "stream",
               "chunks": [{"wait": 0.1, "text": "{cl"}, {"wait": 0.1, "text": "ip:nod} On it"}, {"wait": 0.1, "text": "!"}],
               "final": {"content": [{"type": "text", "text": "{clip:nod} On it!"},
                                     {"type": "tool_use", "id": "t1", "name": "play_music", "input": params}]}}),
        json!({"key": ClaudeFixtures::key_for("create", &fb), "method": "create", "wait": 0.5,
               "final": {"content": [{"type": "text", "text": "Funky Town is rolling!"}]}}),
    ];
    std::fs::write(dir.join("claude.jsonl"), lines.iter().map(|l| l.to_string() + "\n").collect::<String>()).unwrap();
    dir
}

fn stubs(bus: &Bus) {
    // voice: every line plays for 1 s
    let mut rx = bus.subscribe(Domain::Conversation);
    let b = bus.clone();
    tokio::spawn(async move {
        while let Some(Received::Message(env)) = rx.recv().await {
            if let Body::Event(Event::Conversation(ConversationEvent::Speak { .. })) = &env.body {
                let (b, cid) = (b.clone(), env.conversation_id.clone());
                tokio::spawn(async move {
                    b.publish(Source::System, cid.clone(), Event::Conversation(ConversationEvent::SpeechStarted));
                    tokio::time::sleep(Duration::from_secs(1)).await;
                    b.publish(Source::System, cid, Event::Conversation(ConversationEvent::SpeechEnded));
                });
            }
        }
    });
    // music: any play starts "Funky Town"
    let mut rx = bus.subscribe(Domain::Music);
    let b = bus.clone();
    tokio::spawn(async move {
        while let Some(Received::Message(env)) = rx.recv().await {
            if let Body::Event(Event::Music(MusicEvent::Play { .. })) = &env.body {
                b.publish(Source::System, None, Event::Music(MusicEvent::TrackStarted { track: Track { title: "Funky Town".into(), ..Default::default() } }));
            }
        }
    });
    // performer: accept everything
    let mut rx = bus.take_commands(MessageClass::Perf).unwrap();
    tokio::spawn(async move {
        while let Some(req) = rx.recv().await {
            req.ack(Ack::Accepted);
        }
    });
}

fn log(bus: &Bus) -> Arc<Mutex<Vec<Arc<Envelope>>>> {
    let out = Arc::new(Mutex::new(Vec::new()));
    let (o, mut rx) = (out.clone(), bus.subscribe_all());
    tokio::spawn(async move {
        while let Some(Received::Message(env)) = rx.recv().await {
            o.lock().unwrap().push(env);
        }
    });
    out
}

fn brain(bus: &Bus, dir: &std::path::Path) -> Brain {
    let fx = Arc::new(ClaudeFixtures::load(dir, 1.0).unwrap());
    let deps = BrainDeps {
        llm: Some(LlmClient::replay(fx, "claude-sonnet-5-5")),
        router: IntentRouter::new(JevClient::new("", Duration::from_millis(800)), RouterConfig::default()),
        memory: None,
        latency: Some(r3x_ops::latency::LatencyTracker::spawn(bus)),
        ptt: None,
        chooser: random_chooser(),
    };
    Brain::spawn(bus, BrainConfig::default(), deps).unwrap()
}

#[tokio::test(start_paused = true)]
async fn claude_tool_turn_with_verbal_feedback_and_dedup() {
    let dir = fixture_dir();
    let bus = Bus::default();
    bus.update(Source::System, |s: &mut StageState| s.brain = true);
    bus.update(Source::System, |m: &mut MusicState| m.library = vec!["Funky Town".into()]);
    stubs(&bus);
    let rec = log(&bus);
    let _brain = brain(&bus, &dir);

    let say = Command::Intent(IntentCommand::Say { text: "play something funky".into() });
    assert!(bus.command(Source::Cli, None, say).await.is_accepted());
    tokio::time::sleep(Duration::from_secs(10)).await;

    let log = rec.lock().unwrap().clone();
    let turn = log
        .iter()
        .find_map(|e| matches!(e.body, Body::Event(Event::Conversation(ConversationEvent::ListeningStopped { .. }))).then(|| e.conversation_id.clone().unwrap()))
        .expect("typed turn captured with a minted id");
    let events: Vec<(Option<String>, Event)> =
        log.iter().filter_map(|e| if let Body::Event(ev) = &e.body { Some((e.conversation_id.clone(), ev.clone())) } else { None }).collect();
    let deltas: String = events
        .iter()
        .filter_map(|(_, e)| if let Event::Conversation(ConversationEvent::ReplyDelta { text }) = e { Some(text.as_str()) } else { None })
        .collect();
    assert_eq!(deltas, "On it!", "no tag text ever leaves the stream");
    let spoken: Vec<(&str, Option<&str>)> = events
        .iter()
        .filter_map(|(c, e)| if let Event::Conversation(ConversationEvent::Speak { text, reply: true, .. }) = e { Some((text.as_str(), c.as_deref())) } else { None })
        .collect();
    assert_eq!(spoken, [("On it!", Some(turn.as_str())), ("Funky Town is rolling!", Some(turn.as_str()))]);
    assert!(events.iter().any(|(c, e)| matches!(e, Event::Music(MusicEvent::Play { query: Some(_) })) && c.as_deref() == Some(turn.as_str())));
    let tag_play = log.iter().position(|e| matches!(&e.body, Body::Command(Command::Perf(PerfCommand::Play { id, .. })) if id == "nod") && e.source == Source::Claude);
    let speech = log.iter().position(|e| matches!(e.body, Body::Event(Event::Conversation(ConversationEvent::SpeechStarted))));
    assert!(tag_play.unwrap() > speech.unwrap(), "the tag fires on the word, after speech starts");
    let result = events.iter().find_map(|(_, e)| if let Event::Conversation(ConversationEvent::ToolResult { result, .. }) = e { Some(result.clone()) } else { None });
    assert_eq!(result.unwrap()["track"], "Funky Town");

    // The same turn id again: the replies are the same texts, so nothing new is spoken.
    bus.publish(Source::System, Some(turn.clone()), Event::Conversation(ConversationEvent::ListeningStopped { transcript: "play something funky".into() }));
    tokio::time::sleep(Duration::from_secs(10)).await;
    let speaks = rec.lock().unwrap().iter().filter(|e| matches!(e.body, Body::Event(Event::Conversation(ConversationEvent::Speak { .. })))).count();
    let replies = rec.lock().unwrap().iter().filter(|e| matches!(e.body, Body::Event(Event::Conversation(ConversationEvent::Reply { .. })))).count();
    assert_eq!((speaks, replies), (2, 4), "replies published again, but duplicate texts are not spoken");
}

#[tokio::test(start_paused = true)]
async fn refusals_and_debug_latency() {
    let bus = Bus::default();
    let log = log(&bus);
    let _brain = brain(&bus, &fixture_dir());
    assert_eq!(bus.command(Source::Cli, None, Command::Intent(IntentCommand::Say { text: "hi".into() })).await, Ack::rejected("the brain is off in this mode"));
    bus.update(Source::System, |s: &mut StageState| s.brain = true);
    assert!(!bus.command(Source::Ui, None, Command::Intent(IntentCommand::PttStart)).await.is_accepted(), "no voice hook");
    assert!(bus.command(Source::Cli, None, Command::Intent(IntentCommand::Console { line: "debug latency".into() })).await.is_accepted());
    tokio::time::sleep(Duration::from_millis(50)).await;
    let consoles: Vec<String> = log
        .lock()
        .unwrap()
        .iter()
        .filter_map(|e| if let Body::Event(Event::Ops(OpsEvent::Console { message, .. })) = &e.body { Some(message.clone()) } else { None })
        .collect();
    assert_eq!(consoles.len(), 1);
    assert!(consoles[0].starts_with("No latency data"), "{consoles:?}");
}

/// Vision attached: the turn carries the latest scene, `analyze_scene` asks the camera, and the
/// description comes back into the conversation for a spoken answer (tools not callable).
#[tokio::test(start_paused = true)]
async fn vision_scene_context_and_analyze_scene() {
    struct Eyes(f64);
    impl r3x_brain::SceneSource for Eyes {
        fn scene(&self) -> Option<(String, f64)> {
            Some(("a droid workshop".into(), self.0))
        }
        fn analyze<'a>(&'a self, q: &'a str, _: Option<String>) -> std::pin::Pin<Box<dyn std::future::Future<Output = Result<String, String>> + Send + 'a>> {
            Box::pin(async move { Ok(format!("{q}: a Wookiee waving")) })
        }
    }
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64();
    let memory = Arc::new(r3x_memory::Memory::open_in_memory().unwrap());
    let ctx = memory.turn_context(Some(("a droid workshop", now)), 5).unwrap();
    let dir = std::env::temp_dir().join(format!("r3x-brain-vision-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let turn = prompt::turn_request("sys", vec![Message::user(prompt::user_message("what do you see", None, &ctx))], request::default_tools(), false);
    let answer = prompt::turn_request("sys", vec![Message::user("[Vision system response to 'who is here']: who is here: a Wookiee waving")], vec![], true);
    let lines = [
        json!({"key": ClaudeFixtures::key_for("stream", &turn), "method": "stream", "chunks": [{"wait": 0.1, "text": "Let me look."}],
               "final": {"content": [{"type": "text", "text": "Let me look."},
                                     {"type": "tool_use", "id": "v1", "name": "analyze_scene", "input": {"question": "who is here"}}]}}),
        json!({"key": ClaudeFixtures::key_for("create", &answer), "method": "create", "wait": 0.2,
               "final": {"content": [{"type": "text", "text": "A Wookiee! Hi there!"}]}}),
    ];
    std::fs::write(dir.join("claude.jsonl"), lines.iter().map(|l| l.to_string() + "\n").collect::<String>()).unwrap();

    let bus = Bus::default();
    bus.update(Source::System, |s: &mut StageState| s.brain = true);
    stubs(&bus);
    let rec = log(&bus);
    let deps = BrainDeps {
        llm: Some(LlmClient::replay(Arc::new(ClaudeFixtures::load(&dir, 1.0).unwrap()), "claude-sonnet-5-5")),
        router: IntentRouter::new(JevClient::new("", Duration::from_millis(800)), RouterConfig::default()),
        memory: Some(memory),
        latency: None,
        ptt: None,
        chooser: random_chooser(),
    };
    let brain = Brain::spawn(&bus, BrainConfig::default(), deps).unwrap();
    brain.attach_vision(Arc::new(Eyes(now)));
    let say = Command::Intent(IntentCommand::Say { text: "what do you see".into() });
    assert!(bus.command(Source::Cli, None, say).await.is_accepted());
    tokio::time::sleep(Duration::from_secs(10)).await;

    let spoken: Vec<String> = rec
        .lock()
        .unwrap()
        .iter()
        .filter_map(|e| if let Body::Event(Event::Conversation(ConversationEvent::Speak { text, .. })) = &e.body { Some(text.clone()) } else { None })
        .collect();
    assert_eq!(spoken, ["Let me look.", "A Wookiee! Hi there!"], "scene in the turn (fixture hit), then the vision answer");
}

/// A look that fails must never end the turn in silence. R3X has already said "let me look";
/// the dragon report (2026-09-30): the camera answered with an empty description four times
/// and R3X went quiet each time, then promised to look again, because nothing recorded that
/// the look had failed.
mod failed_looks {
    use super::*;

    struct Eyes(Result<String, String>);
    impl r3x_brain::SceneSource for Eyes {
        fn scene(&self) -> Option<(String, f64)> {
            None
        }
        fn analyze<'a>(&'a self, _: &'a str, _: Option<String>) -> std::pin::Pin<Box<dyn std::future::Future<Output = Result<String, String>> + Send + 'a>> {
            let r = self.0.clone();
            Box::pin(async move { r })
        }
    }

    const ASK: &str = "what is this dragon";
    const Q: &str = "What is the toy being held up?";

    /// Run one "look at this" turn: Claude says "Let me look." and calls `analyze_scene`; the
    /// eyes answer `eyes`; `follow_up` is (the vision note the brain must send back, Claude's
    /// reply to it). Returns what R3X said, and the fixture key misses.
    async fn look(eyes: Result<String, String>, follow_up: Option<(String, serde_json::Value)>) -> Vec<String> {
        let dir = std::env::temp_dir().join(format!("r3x-brain-look-{}-{}", std::process::id(), uuid()));
        std::fs::create_dir_all(&dir).unwrap();
        let memory = Arc::new(r3x_memory::Memory::open_in_memory().unwrap());
        let ctx = memory.turn_context(None, 5).unwrap();
        let turn = prompt::turn_request("sys", vec![Message::user(prompt::user_message(ASK, None, &ctx))], request::default_tools(), false);
        let mut lines = vec![json!({"key": ClaudeFixtures::key_for("stream", &turn), "method": "stream",
            "chunks": [{"wait": 0.1, "text": "Let me look."}],
            "final": {"content": [{"type": "text", "text": "Let me look."},
                                  {"type": "tool_use", "id": "v1", "name": "analyze_scene", "input": {"question": Q}}]}})];
        if let Some((note, reply)) = follow_up {
            let answer = prompt::turn_request("sys", vec![Message::user(note)], vec![], true);
            lines.push(json!({"key": ClaudeFixtures::key_for("create", &answer), "method": "create", "wait": 0.2, "final": {"content": reply}}));
        }
        std::fs::write(dir.join("claude.jsonl"), lines.iter().map(|l| l.to_string() + "\n").collect::<String>()).unwrap();

        let bus = Bus::default();
        bus.update(Source::System, |s: &mut StageState| s.brain = true);
        stubs(&bus);
        let rec = log(&bus);
        let deps = BrainDeps {
            llm: Some(LlmClient::replay(Arc::new(ClaudeFixtures::load(&dir, 1.0).unwrap()), "claude-sonnet-5-5")),
            router: IntentRouter::new(JevClient::new("", Duration::from_millis(800)), RouterConfig::default()),
            memory: Some(memory),
            latency: None,
            ptt: None,
            chooser: random_chooser(),
        };
        let brain = Brain::spawn(&bus, BrainConfig::default(), deps).unwrap();
        brain.attach_vision(Arc::new(Eyes(eyes)));
        let say = Command::Intent(IntentCommand::Say { text: ASK.into() });
        assert!(bus.command(Source::Cli, None, say).await.is_accepted());
        tokio::time::sleep(Duration::from_secs(10)).await;
        let spoken = rec
            .lock()
            .unwrap()
            .iter()
            .filter_map(|e| if let Body::Event(Event::Conversation(ConversationEvent::Speak { text, .. })) = &e.body { Some(text.clone()) } else { None })
            .collect();
        spoken
    }

    fn uuid() -> u64 {
        use std::sync::atomic::{AtomicU64, Ordering};
        static N: AtomicU64 = AtomicU64::new(0);
        N.fetch_add(1, Ordering::Relaxed)
    }

    #[tokio::test(start_paused = true)]
    async fn an_empty_description_is_answered_in_character() {
        let note = r3x_brain::vision_failure_note(Q, "the camera image came back with no description");
        let reply = json!([{"type": "text", "text": "My optics are fuzzy on that one. Hold it up again?"}]);
        let spoken = look(Ok(String::new()), Some((note, reply))).await;
        assert_eq!(spoken, ["Let me look.", "My optics are fuzzy on that one. Hold it up again?"]);
    }

    #[tokio::test(start_paused = true)]
    async fn a_failed_look_is_answered_in_character() {
        let note = r3x_brain::vision_failure_note(Q, "no recent camera frame");
        let reply = json!([{"type": "text", "text": "My camera just blinked out."}]);
        let spoken = look(Err("no recent camera frame".into()), Some((note, reply))).await;
        assert_eq!(spoken, ["Let me look.", "My camera just blinked out."]);
    }

    #[tokio::test(start_paused = true)]
    async fn a_silent_follow_up_still_gets_a_line() {
        // The description arrives, but Claude's answer to it comes back empty (or fails).
        let note = format!("[Vision system response to '{Q}']: a green plush dragon");
        let spoken = look(Ok("a green plush dragon".into()), Some((note, json!([])))).await;
        assert_eq!(spoken.len(), 2, "never silent after 'let me look': {spoken:?}");
        assert_eq!(spoken[1], r3x_brain::VISION_FALLBACK_LINE);
    }
}

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
    // One folder per call: these tests run in parallel in one process (one pid).
    let dir = std::env::temp_dir().join(format!("r3x-brain-turns-{}-{}", std::process::id(), uuid()));
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

/// Every caller gets its own fixture folder: the tests here run in parallel in one process, and
/// two of them sharing `r3x-brain-turns-<pid>` could load the recorded Claude lines while the
/// other rewrites them (the race that made the DJ tests fail ~1 run in 4).
#[test]
fn every_fixture_dir_is_its_own() {
    assert_ne!(fixture_dir(), fixture_dir());
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

/// Vision attached: the turn carries the latest scene, and `analyze_scene` (Claude's fallback
/// when Jev did not ask to look) answers again with the camera frame attached (tools not
/// callable) - no separate describe call.
#[tokio::test(start_paused = true)]
async fn vision_scene_context_and_analyze_scene() {
    struct Eyes(f64);
    impl r3x_brain::SceneSource for Eyes {
        fn scene(&self) -> Option<(String, f64)> {
            Some(("a droid workshop".into(), self.0))
        }
        fn snapshot(&self) -> std::pin::Pin<Box<dyn std::future::Future<Output = Result<String, String>> + Send + '_>> {
            Box::pin(async { Ok("/9j/frame".to_string()) })
        }
    }
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64();
    let memory = Arc::new(r3x_memory::Memory::open_in_memory().unwrap());
    let ctx = memory.turn_context(Some(("a droid workshop", now)), 5).unwrap();
    let dir = std::env::temp_dir().join(format!("r3x-brain-vision-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let turn = prompt::turn_request("sys", vec![Message::user(prompt::user_message("what do you see", None, &ctx))], request::default_tools(), false);
    let answer = prompt::turn_request("sys", vec![Message::user(r3x_brain::look_tool_text("who is here"))], vec![], true);
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
/// the dragon report (2026-09-30): four looks came back empty and R3X went quiet each time,
/// then promised to look again, because nothing recorded that the look had failed.
mod failed_looks {
    use super::*;

    struct Eyes(Result<String, String>);
    impl r3x_brain::SceneSource for Eyes {
        fn scene(&self) -> Option<(String, f64)> {
            None
        }
        fn snapshot(&self) -> std::pin::Pin<Box<dyn std::future::Future<Output = Result<String, String>> + Send + '_>> {
            let r = self.0.clone();
            Box::pin(async move { r })
        }
    }

    const ASK: &str = "what is this dragon";
    const Q: &str = "What is the toy being held up?";

    /// Run one "look at this" turn: Claude says "Let me look." and calls `analyze_scene`; the
    /// eyes answer `eyes`; `follow_ups` are (the user text the brain must send next, Claude's
    /// final message). Returns what R3X said.
    async fn look(eyes: Result<String, String>, follow_ups: Vec<(String, serde_json::Value)>) -> Vec<String> {
        let dir = std::env::temp_dir().join(format!("r3x-brain-look-{}-{}", std::process::id(), uuid()));
        std::fs::create_dir_all(&dir).unwrap();
        let memory = Arc::new(r3x_memory::Memory::open_in_memory().unwrap());
        let ctx = memory.turn_context(None, 5).unwrap();
        let turn = prompt::turn_request("sys", vec![Message::user(prompt::user_message(ASK, None, &ctx))], request::default_tools(), false);
        let mut lines = vec![json!({"key": ClaudeFixtures::key_for("stream", &turn), "method": "stream",
            "chunks": [{"wait": 0.1, "text": "Let me look."}],
            "final": {"content": [{"type": "text", "text": "Let me look."},
                                  {"type": "tool_use", "id": "v1", "name": "analyze_scene", "input": {"question": Q}}]}})];
        for (text, fin) in follow_ups {
            let answer = prompt::turn_request("sys", vec![Message::user(text)], vec![], true);
            lines.push(json!({"key": ClaudeFixtures::key_for("create", &answer), "method": "create", "wait": 0.2, "final": fin}));
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
        spoken(&rec)
    }

    #[tokio::test(start_paused = true)]
    async fn a_failed_look_is_answered_in_character() {
        let note = r3x_brain::vision_failure_note(Q, "no recent camera frame");
        let reply = json!({"content": [{"type": "text", "text": "My camera just blinked out."}]});
        assert_eq!(look(Err("no recent camera frame".into()), vec![(note, reply)]).await, ["Let me look.", "My camera just blinked out."]);
    }

    #[tokio::test(start_paused = true)]
    async fn a_refused_image_is_retried_without_it() {
        // Claude declines the frame (a safety refusal: HTTP 200, no text); R3X still answers.
        let refused = json!({"content": [], "stop_reason": "refusal", "stop_details": {"type": "refusal", "category": "general_harms"}});
        let retry = r3x_brain::vision_failure_note(Q, "Claude declined the camera image (refusal: general_harms)");
        let reply = json!({"content": [{"type": "text", "text": "My optics can't make that one out."}]});
        let said = look(Ok("/9j/frame".into()), vec![(r3x_brain::look_tool_text(Q), refused), (retry, reply)]).await;
        assert_eq!(said, ["Let me look.", "My optics can't make that one out."]);
    }

    #[tokio::test(start_paused = true)]
    async fn a_silent_follow_up_still_gets_a_line() {
        let empty = json!({"content": [], "stop_reason": "end_turn"});
        let said = look(Ok("/9j/frame".into()), vec![(r3x_brain::look_tool_text(Q), empty)]).await;
        assert_eq!(said, ["Let me look.", r3x_brain::VISION_FALLBACK_LINE]);
    }
}

fn spoken(rec: &Arc<Mutex<Vec<Arc<Envelope>>>>) -> Vec<String> {
    rec.lock()
        .unwrap()
        .iter()
        .filter_map(|e| if let Body::Event(Event::Conversation(ConversationEvent::Speak { text, .. })) = &e.body { Some(text.clone()) } else { None })
        .collect()
}

fn uuid() -> u64 {
    use std::sync::atomic::{AtomicU64, Ordering};
    static N: AtomicU64 = AtomicU64::new(0);
    N.fetch_add(1, Ordering::Relaxed)
}

/// Jev asks "should R3X look?" alongside the main classification; a yes attaches the camera
/// frame to Claude's turn, so R3X answers from what he sees in one call: no "let me take a
/// picture", no describe call, no second answer.
mod jev_looks {
    use super::*;
    use r3x_intent::catalogue::{build_look_questions, build_state};
    use r3x_intent::{JevFixtures, JEV_MODEL};
    use std::sync::atomic::{AtomicU32, Ordering};

    struct Eyes(Arc<AtomicU32>);
    impl r3x_brain::SceneSource for Eyes {
        fn scene(&self) -> Option<(String, f64)> {
            None
        }
        fn snapshot(&self) -> std::pin::Pin<Box<dyn std::future::Future<Output = Result<String, String>> + Send + '_>> {
            self.0.fetch_add(1, Ordering::SeqCst);
            Box::pin(async { Ok("/9j/frame".to_string()) })
        }
    }

    const ASK: &str = "check out my dragon";

    /// One turn with Jev answering `look` = `p`; `claude` = (user text, final) stream fixtures.
    /// Returns what R3X said and how many frames were taken.
    async fn turn(p: f64, claude: Vec<(String, serde_json::Value)>) -> (Vec<String>, u32) {
        let dir = std::env::temp_dir().join(format!("r3x-brain-jevlook-{}-{}", std::process::id(), uuid()));
        std::fs::create_dir_all(&dir).unwrap();
        let jev = json!({"key": JevFixtures::key_for(JEV_MODEL, &build_state(ASK), &build_look_questions()), "status": 200,
                         "body": {"answers": {"look": {"type": "noul", "noul": p}}}, "latency_ms": 0.0});
        std::fs::write(dir.join("jev.jsonl"), jev.to_string() + "\n").unwrap();
        let lines: Vec<serde_json::Value> = claude
            .into_iter()
            .map(|(text, fin)| {
                let req = prompt::turn_request("sys", vec![Message::user(text)], request::default_tools(), false);
                json!({"key": ClaudeFixtures::key_for("stream", &req), "method": "stream", "chunks": [], "final": fin})
            })
            .collect();
        std::fs::write(dir.join("claude.jsonl"), lines.iter().map(|l| l.to_string() + "\n").collect::<String>()).unwrap();

        let bus = Bus::default();
        bus.update(Source::System, |s: &mut StageState| s.brain = true);
        stubs(&bus);
        let rec = log(&bus);
        let jev = Arc::new(JevFixtures::load(&dir, 0.0).unwrap());
        let deps = BrainDeps {
            llm: Some(LlmClient::replay(Arc::new(ClaudeFixtures::load(&dir, 1.0).unwrap()), "claude-sonnet-5-5")),
            router: IntentRouter::new(JevClient::replay(jev), RouterConfig { api_key: "replay".into(), ..Default::default() }),
            memory: None,
            latency: None,
            ptt: None,
            chooser: random_chooser(),
        };
        let brain = Brain::spawn(&bus, BrainConfig::default(), deps).unwrap();
        let frames = Arc::new(AtomicU32::new(0));
        brain.attach_vision(Arc::new(Eyes(frames.clone())));
        let say = Command::Intent(IntentCommand::Say { text: ASK.into() });
        assert!(bus.command(Source::Cli, None, say).await.is_accepted());
        tokio::time::sleep(Duration::from_secs(10)).await;
        (spoken(&rec), frames.load(Ordering::SeqCst))
    }

    fn says(t: &str) -> serde_json::Value {
        json!({"content": [{"type": "text", "text": t}], "stop_reason": "end_turn"})
    }

    #[tokio::test(start_paused = true)]
    async fn a_look_answers_from_the_frame_in_one_call() {
        let (said, frames) = turn(0.95, vec![(r3x_brain::look_turn_text(ASK), says("A green dragon! Does it breathe fire?"))]).await;
        assert_eq!(said, ["A green dragon! Does it breathe fire?"], "one answer, no 'let me look'");
        assert_eq!(frames, 1);
    }

    #[tokio::test(start_paused = true)]
    async fn no_look_keeps_the_turn_blind() {
        let (said, frames) = turn(0.1, vec![(ASK.to_string(), says("Dragons are my favourite."))]).await;
        assert_eq!(said, ["Dragons are my favourite."]);
        assert_eq!(frames, 0, "no frame taken, nothing attached");
    }

    #[tokio::test(start_paused = true)]
    async fn a_refused_frame_is_retried_blind() {
        let refused = json!({"content": [], "stop_reason": "refusal", "stop_details": {"type": "refusal", "category": "general_harms"}});
        let (said, _) = turn(
            0.95,
            vec![(r3x_brain::look_turn_text(ASK), refused), (r3x_brain::look_refused_text(ASK), says("My optics can't make that out."))],
        )
        .await;
        assert_eq!(said, ["My optics can't make that out."]);
    }
}

/// Talking again supersedes the last turn: its reply (and any follow-up line) is never spoken
/// once a newer turn has started. The 2026-09-30 session: four quick presses ("Was orange.",
/// "Alexa, stop.", "Stop.", and an empty one) each got a reply, and R3X then talked for ~40 s
/// answering things the speaker had already moved past.
#[tokio::test(start_paused = true)]
async fn a_newer_turn_silences_older_replies() {
    let dir = std::env::temp_dir().join(format!("r3x-brain-supersede-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let stream = |text: &str, wait: f64, reply: &str| {
        let req = prompt::turn_request("sys", vec![Message::user(text)], request::default_tools(), false);
        json!({"key": ClaudeFixtures::key_for("stream", &req), "method": "stream",
               "chunks": [{"wait": wait, "text": reply}], "final": {"content": [{"type": "text", "text": reply}]}})
    };
    let lines = [stream("tell me a long story", 3.0, "Once upon a time, in a cantina far away..."), stream("never mind", 0.1, "Okay!")];
    std::fs::write(dir.join("claude.jsonl"), lines.iter().map(|l| l.to_string() + "\n").collect::<String>()).unwrap();

    let bus = Bus::default();
    bus.update(Source::System, |s: &mut StageState| s.brain = true);
    stubs(&bus);
    let rec = log(&bus);
    let deps = BrainDeps {
        llm: Some(LlmClient::replay(Arc::new(ClaudeFixtures::load(&dir, 1.0).unwrap()), "claude-sonnet-5-5")),
        router: IntentRouter::new(JevClient::new("", Duration::from_millis(800)), RouterConfig::default()),
        memory: None,
        latency: None,
        ptt: None,
        chooser: random_chooser(),
    };
    let _brain = Brain::spawn(&bus, BrainConfig::default(), deps).unwrap();
    let say = |t: &str| Command::Intent(IntentCommand::Say { text: t.into() });
    assert!(bus.command(Source::Cli, None, say("tell me a long story")).await.is_accepted());
    tokio::time::sleep(Duration::from_secs(1)).await;
    assert!(bus.command(Source::Cli, None, say("never mind")).await.is_accepted());
    tokio::time::sleep(Duration::from_secs(10)).await;
    assert_eq!(spoken(&rec), ["Okay!"], "the story's reply arrived after the newer turn began: dropped");
}

/// Adaptive thinking acts first and says nothing before a tool. The follow-up is then a turn of
/// the conversation itself (the persona, the history, the tool's result; tools not callable),
/// so "what's my name? then play something funky" gets both, from what R3X already knows.
#[tokio::test(start_paused = true)]
async fn a_silent_command_is_followed_up_in_the_conversation() {
    let dir = std::env::temp_dir().join(format!("r3x-brain-silent-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let ask = "what's my name? then play something funky";
    let params = json!({"track": "something funky"});
    let turn = prompt::turn_request("sys", vec![Message::user(ask)], request::default_tools(), false);
    let result = json!({"success": true, "track": "Funky Town", "requested": "something funky",
        "selected": r3x_intent::catalogue::naming_phrase("something funky"), "action": "play", "message": "Now playing: Funky Town"});
    let note = r3x_brain::silent_tool_note("play_music", &r3x_llm::pyjson::dumps(&result, false, false));
    let follow = prompt::turn_request("sys", vec![Message::user(note)], vec![], true);
    let lines = [
        json!({"key": ClaudeFixtures::key_for("stream", &turn), "method": "stream", "chunks": [],
               "final": {"content": [{"type": "tool_use", "id": "t1", "name": "play_music", "input": params}], "stop_reason": "tool_use"}}),
        json!({"key": ClaudeFixtures::key_for("create", &follow), "method": "create", "wait": 0.3,
               "final": {"content": [{"type": "text", "text": "You're Brandon, and Funky Town is rolling!"}]}}),
    ];
    std::fs::write(dir.join("claude.jsonl"), lines.iter().map(|l| l.to_string() + "\n").collect::<String>()).unwrap();
    let bus = Bus::default();
    bus.update(Source::System, |s: &mut StageState| s.brain = true);
    stubs(&bus);
    let rec = log(&bus);
    let deps = BrainDeps {
        llm: Some(LlmClient::replay(Arc::new(ClaudeFixtures::load(&dir, 1.0).unwrap()), "claude-sonnet-5-5")),
        router: IntentRouter::new(JevClient::new("", Duration::from_millis(800)), RouterConfig::default()),
        memory: None,
        latency: None,
        ptt: None,
        chooser: random_chooser(),
    };
    let _brain = Brain::spawn(&bus, BrainConfig::default(), deps).unwrap();
    assert!(bus.command(Source::Cli, None, Command::Intent(IntentCommand::Say { text: ask.into() })).await.is_accepted());
    tokio::time::sleep(Duration::from_secs(10)).await;
    assert_eq!(spoken(&rec), ["You're Brandon, and Funky Town is rolling!"]);
}

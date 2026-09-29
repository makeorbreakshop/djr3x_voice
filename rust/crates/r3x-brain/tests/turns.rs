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

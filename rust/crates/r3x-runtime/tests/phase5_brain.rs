//! `--brain rust`, in-process and offline: a gateway client types a line; the Rust brain (not
//! CantinaOS) runs the turn against the recorded Jev + Claude corpus (`R3X_FIXTURES=replay`),
//! and the client sees the turn's reply and the line queued for the voice. No network, no
//! audio. (One test per binary: it sets process env.)

use std::sync::Arc;
use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use r3x_bus::Bus;
use r3x_contracts::{Body, ClientInfo, Command, ConversationEvent, Envelope, Event, IntentCommand, RobotProfile, Source};
use r3x_gateway::ClientAuth;
use r3x_runtime::{all_classes, brain::BrainMode, RuntimeConfig};
use serde_json::json;
use tokio_tungstenite::tungstenite::{client::IntoClientRequest, http::HeaderValue, Message};

#[tokio::test]
async fn typed_turn_through_the_rust_brain() {
    let repo = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..");
    let db = std::env::temp_dir().join(format!("r3x-phase5-{}.sqlite", std::process::id()));
    // SAFETY: set before any runtime thread reads the environment; the only test here.
    unsafe {
        std::env::set_var("R3X_FIXTURES", "replay");
        std::env::set_var("R3X_FIXTURE_DIR", repo.join("fixtures/smoke-voice"));
        std::env::set_var("R3X_FIXTURE_PACE", "0");
        std::env::set_var("R3X_MEMORY_DB", &db);
    }
    let profile = RobotProfile::load(r3x_runtime::default_profile_path()).unwrap();
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    let cfg = RuntimeConfig {
        bind: listener.local_addr().unwrap(),
        clients: vec![ClientAuth { token: "t0k".into(), info: ClientInfo { name: "cli".into(), source: Source::Cli, classes: all_classes() } }],
        origins: vec!["http://localhost:5391".into()],
        bridge: None,
        profile: Some(Arc::new(profile)),
        session_log: None,
        logs: None,
        voice: None,
        mouse: false,
        show_dir: r3x_runtime::performer::default_show_dir(),
        drivers: None,
    };
    tokio::spawn(r3x_runtime::run_with_brain(Bus::default(), cfg, None, listener, BrainMode::Rust));

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
    let say = Command::Intent(IntentCommand::Say { text: "what is your favourite cantina band".into() });
    ws.send(Message::Text(json!({"kind": "command", "id": "q", "body": say}).to_string().into())).await.unwrap();

    let (mut acked, mut reply, mut spoken) = (false, None, None);
    let deadline = tokio::time::Instant::now() + Duration::from_secs(10);
    while (reply.is_none() || spoken.is_none() || !acked) && tokio::time::Instant::now() < deadline {
        let Ok(Some(Ok(Message::Text(t)))) = tokio::time::timeout(Duration::from_secs(5), ws.next()).await else { continue };
        let env: Envelope = serde_json::from_str(t.as_str()).unwrap();
        match env.body {
            Body::Ack(a) if env.re.as_deref() == Some("q") => acked = a.is_accepted(),
            Body::Event(Event::Conversation(ConversationEvent::Reply { text })) => reply = Some((text, env.conversation_id)),
            Body::Event(Event::Conversation(ConversationEvent::Speak { text, reply: true, .. })) => spoken = Some(text),
            _ => {}
        }
    }
    assert!(acked, "the brain owns the intent class");
    let (text, turn) = reply.expect("a reply");
    assert!(text.starts_with("Ooh, tough one!"), "{text}");
    assert!(turn.is_some_and(|t| t.starts_with("typed-")), "typed turns get a minted id");
    assert_eq!(spoken.as_deref(), Some(text.as_str()), "the reply is routed to the voice");
    let _ = std::fs::remove_file(db);
}

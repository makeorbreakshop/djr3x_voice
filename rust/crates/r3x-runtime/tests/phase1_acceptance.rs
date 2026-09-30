//! Phase 1 acceptance, in-process: runtime in bridge mode against a fake CantinaOS tap.
//! A gateway client reads retained state, switches to Bench and triggers an emote, which
//! must arrive at the tap as `show.perform`; an unauthenticated or wrong-origin client is refused.

use std::sync::Arc;
use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use r3x_bus::Bus;
use r3x_contracts::{
    Ack, Body, ClientInfo, Command, Envelope, OperatingMode, PerfCommand, RobotProfile, Source,
    StageCommand, StateUpdate,
};
use r3x_gateway::ClientAuth;
use r3x_runtime::{all_classes, bridge::BridgeConfig, RuntimeConfig};
use serde_json::{json, Value};
use tokio::net::TcpListener;
use tokio::sync::mpsc;
use tokio_tungstenite::tungstenite::{client::IntoClientRequest, http::HeaderValue, Error, Message};

type Ws = tokio_tungstenite::WebSocketStream<tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>>;

/// Acks every emit, echoes it as an event (as the real tap does) and reports it on `seen`.
async fn fake_tap() -> (String, mpsc::UnboundedReceiver<Value>) {
    let l = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("ws://{}/", l.local_addr().unwrap());
    let (seen, rx) = mpsc::unbounded_channel();
    tokio::spawn(async move {
        while let Ok((sock, _)) = l.accept().await {
            let seen = seen.clone();
            tokio::spawn(async move {
                let mut ws = tokio_tungstenite::accept_async(sock).await.unwrap();
                ws.send(Message::Text(json!({"v": 1, "kind": "hello", "seq": 0}).to_string().into())).await.unwrap();
                while let Some(Ok(Message::Text(t))) = ws.next().await {
                    let m: Value = serde_json::from_str(t.as_str()).unwrap();
                    let ack = json!({"v": 1, "kind": "ack", "re": m["id"], "ok": true, "seq": 1});
                    let echo = json!({"v": 1, "kind": "event", "topic": m["topic"], "payload": m["payload"], "source": "tap:r3x"});
                    ws.send(Message::Text(ack.to_string().into())).await.unwrap();
                    ws.send(Message::Text(echo.to_string().into())).await.unwrap();
                    let _ = seen.send(m);
                }
            });
        }
    });
    (url, rx)
}

async fn start_runtime(tap_url: String) -> String {
    let profile = RobotProfile::load(r3x_runtime::default_profile_path()).unwrap();
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("ws://{}/", listener.local_addr().unwrap());
    let cfg = RuntimeConfig {
        bind: listener.local_addr().unwrap(),
        clients: vec![ClientAuth {
            token: "t0k".into(),
            info: ClientInfo { name: "test".into(), source: Source::Ui, classes: all_classes() },
        }],
        origins: vec!["http://localhost:5391".into()],
        bridge: Some(BridgeConfig::new(tap_url, "tap".into(), &profile)),
        profile: Some(Arc::new(profile)),
        session_log: None,
        logs: None,
        voice: None,
        mouse: false,
        show_dir: r3x_runtime::performer::default_show_dir(),
        drivers: None,
    };
    tokio::spawn(r3x_runtime::run_on(Bus::default(), cfg, None, listener));
    url
}

async fn connect(url: &str, token: Option<&str>, origin: Option<&str>) -> Result<Ws, u16> {
    let mut req = url.into_client_request().unwrap();
    if let Some(t) = token {
        req.headers_mut().insert("Authorization", HeaderValue::from_str(&format!("Bearer {t}")).unwrap());
    }
    if let Some(o) = origin {
        req.headers_mut().insert("Origin", HeaderValue::from_str(o).unwrap());
    }
    match tokio_tungstenite::connect_async(req).await {
        Ok((ws, _)) => Ok(ws),
        Err(Error::Http(r)) => Err(r.status().as_u16()),
        Err(e) => panic!("{e}"),
    }
}

async fn next(ws: &mut Ws) -> Envelope {
    loop {
        let m = tokio::time::timeout(Duration::from_secs(5), ws.next()).await.expect("timed out").unwrap().unwrap();
        if let Message::Text(t) = m {
            return serde_json::from_str(t.as_str()).unwrap();
        }
    }
}

/// Send a command; returns its ack and the latest stage state seen while waiting.
async fn command(ws: &mut Ws, id: &str, cmd: Command) -> (Ack, Option<r3x_contracts::StageState>) {
    let msg = json!({ "kind": "command", "id": id, "body": cmd });
    ws.send(Message::Text(msg.to_string().into())).await.unwrap();
    let mut stage = None;
    loop {
        let env = next(ws).await;
        match (env.body, env.re.as_deref()) {
            (Body::Ack(a), Some(re)) if re == id => return (a, stage),
            (Body::State(StateUpdate::Stage(s)), _) => stage = Some(s),
            _ => {}
        }
    }
}

#[tokio::test]
async fn read_state_switch_to_bench_trigger_emote() {
    let (tap_url, mut seen) = fake_tap().await;
    let url = start_runtime(tap_url).await;

    assert_eq!(connect(&url, None, None).await.err(), Some(401));
    assert_eq!(connect(&url, Some("t0k"), Some("http://evil.example")).await.err(), Some(403));

    let mut ws = connect(&url, Some("t0k"), Some("http://localhost:5391")).await.unwrap();
    let Body::Hello(h) = next(&mut ws).await.body else { panic!("hello first") };
    assert_eq!(h.state.stage.mode, OperatingMode::Show);
    assert!(h.state.stage.outputs.values().all(|v| *v), "Show enables every output");
    assert_eq!(h.profile.as_ref().unwrap().emotes[0], "yes");

    let bench = Command::Stage(StageCommand::SetMode { mode: OperatingMode::Bench });
    let (ack, stage) = command(&mut ws, "m1", bench).await;
    assert_eq!(ack, Ack::Accepted);
    // The state delta for the switch reaches the client (before its ack).
    let s = stage.expect("stage delta");
    assert_eq!(s.mode, OperatingMode::Bench);
    assert!(!s.brain && s.outputs.values().all(|v| !*v));

    // The performer plays the emote; the bridge tells CantinaOS once it is on the tap (its
    // first message on connect asks for the music library).
    let first = tokio::time::timeout(Duration::from_secs(5), seen.recv()).await.unwrap().unwrap();
    assert_eq!(first["topic"], "cli.command");
    let mut ack = Ack::rejected("never tried");
    for i in 0..40 {
        ack = command(&mut ws, &format!("e{i}"), Command::Perf(PerfCommand::Emote { slot: 0 })).await.0;
        if ack.is_accepted() {
            break;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    assert_eq!(ack, Ack::Accepted);
    loop {
        let m = tokio::time::timeout(Duration::from_secs(5), seen.recv()).await.unwrap().unwrap();
        if m["topic"] == "show.started" {
            assert_eq!(m["payload"]["id"], "yes");
            assert_eq!(m["payload"]["source"], "ui");
            break;
        }
    }

    // Brain off in Bench: a typed turn is refused with a reason.
    let say = Command::Intent(r3x_contracts::IntentCommand::Say { text: "hello".into() });
    assert!(!command(&mut ws, "s1", say).await.0.is_accepted());
}

use futures_util::{SinkExt, StreamExt};
use r3x_bus::Bus;
use r3x_contracts::{
    Ack, Body, ClientInfo, Command, Envelope, IntentCommand, MessageClass, OperatingMode,
    PerfCommand, Source, StageState,
};
use r3x_gateway::{ClientAuth, GatewayConfig};
use tokio_tungstenite::tungstenite::{client::IntoClientRequest, http::HeaderValue, Error, Message};

type Ws = tokio_tungstenite::WebSocketStream<tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>>;

fn client(token: &str, name: &str, source: Source, classes: &[MessageClass]) -> ClientAuth {
    ClientAuth { token: token.into(), info: ClientInfo { name: name.into(), source, classes: classes.to_vec() } }
}

/// A gateway on an ephemeral port, with a perf + intent handler that accepts everything.
async fn start() -> (Bus, String) {
    let bus = Bus::default();
    for class in [MessageClass::Perf, MessageClass::Intent] {
        let mut rx = bus.take_commands(class).unwrap();
        tokio::spawn(async move {
            while let Some(req) = rx.recv().await {
                req.ack(Ack::Accepted);
            }
        });
    }
    let cfg = GatewayConfig {
        clients: vec![
            client("ui-secret", "panel", Source::Ui, &[MessageClass::Intent, MessageClass::Perf, MessageClass::Stage]),
            client("pub-secret", "visitor", Source::Public, &[MessageClass::Intent, MessageClass::Perf]),
        ],
        origins: vec!["http://localhost:5391".into()],
        ..Default::default()
    };
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("ws://{}/", listener.local_addr().unwrap());
    tokio::spawn(r3x_gateway::serve(listener, bus.clone(), cfg));
    (bus, url)
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
        Err(Error::Http(resp)) => Err(resp.status().as_u16()),
        Err(e) => panic!("{e}"),
    }
}

async fn next(ws: &mut Ws) -> Envelope {
    loop {
        let m = tokio::time::timeout(std::time::Duration::from_secs(5), ws.next()).await.unwrap().unwrap().unwrap();
        if let Message::Text(t) = m {
            return serde_json::from_str(t.as_str()).unwrap();
        }
    }
}

async fn ack_for(ws: &mut Ws, id: &str) -> Ack {
    loop {
        let env = next(ws).await;
        if let (Body::Ack(a), Some(re)) = (&env.body, &env.re) {
            if re == id {
                return a.clone();
            }
        }
    }
}

async fn send(ws: &mut Ws, id: &str, cmd: Command) {
    let msg = serde_json::json!({ "kind": "command", "id": id, "body": cmd });
    ws.send(Message::Text(msg.to_string().into())).await.unwrap();
}

#[tokio::test]
async fn refuses_missing_token_bad_token_and_wrong_origin() {
    let (_bus, url) = start().await;
    assert_eq!(connect(&url, None, None).await.err(), Some(401));
    assert_eq!(connect(&url, Some("guess"), None).await.err(), Some(401));
    assert_eq!(connect(&url, Some("ui-secret"), Some("http://evil.example")).await.err(), Some(403));
    // Loopback is not a trust boundary: a page on another local port is refused too.
    assert_eq!(connect(&url, Some("ui-secret"), Some("http://localhost:9999")).await.err(), Some(403));
    // Browsers pass the token in the query.
    let q = format!("{url}?token=ui-secret");
    assert!(connect(&q, None, Some("http://localhost:5391")).await.is_ok());
}

#[tokio::test]
async fn hello_carries_client_and_state_then_deltas() {
    let (bus, url) = start().await;
    bus.update(Source::System, |s: &mut StageState| s.mode = OperatingMode::Bench);
    let mut ws = connect(&url, Some("ui-secret"), None).await.unwrap();
    let Body::Hello(h) = next(&mut ws).await.body else { panic!("first message must be hello") };
    assert_eq!(h.client.source, Source::Ui);
    assert_eq!(h.state.stage.mode, OperatingMode::Bench);

    bus.update(Source::System, |s: &mut StageState| s.mode = OperatingMode::Studio);
    let env = next(&mut ws).await;
    let Body::State(r3x_contracts::StateUpdate::Stage(s)) = env.body else { panic!("{env:?}") };
    assert_eq!(s.mode, OperatingMode::Studio);
}

#[tokio::test]
async fn command_ack_round_trip_with_stamped_source() {
    let (bus, url) = start().await;
    let mut all = bus.subscribe_all();
    let mut ws = connect(&url, Some("ui-secret"), None).await.unwrap();
    send(&mut ws, "c1", Command::Perf(PerfCommand::Emote { slot: 1 })).await;
    assert_eq!(ack_for(&mut ws, "c1").await, Ack::Accepted);
    // The bus saw it as the ui source, whatever the client might claim.
    loop {
        if let Some(r3x_bus::Received::Message(env)) = all.recv().await {
            if matches!(env.body, Body::Command(_)) {
                assert_eq!(env.source, Source::Ui);
                assert_eq!(env.id.as_deref(), Some("c1"));
                break;
            }
        }
    }
}

#[tokio::test]
async fn tier_and_class_rejections() {
    let (_bus, url) = start().await;
    let mut ws = connect(&url, Some("pub-secret"), None).await.unwrap();
    // public <= cheap + intent.say only
    send(&mut ws, "p1", Command::Perf(PerfCommand::Play { id: "hype_drop".into(), intensity: 1.0, speed: 1.0, layer: None })).await;
    assert!(!ack_for(&mut ws, "p1").await.is_accepted());
    send(&mut ws, "p2", Command::Intent(IntentCommand::Say { text: "hi".into() })).await;
    assert!(ack_for(&mut ws, "p2").await.is_accepted());
    // Stage is not in the visitor's class allow-list.
    let stage = Command::Stage(r3x_contracts::StageCommand::SetMode { mode: OperatingMode::Bench });
    send(&mut ws, "p3", stage).await;
    let Ack::Rejected { reason } = ack_for(&mut ws, "p3").await else { panic!() };
    assert!(reason.contains("Stage"), "{reason}");
}

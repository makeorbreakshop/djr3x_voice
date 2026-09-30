//! `--public` (plan Phase 10), offline: replayed Claude + TTS (`R3X_FIXTURES=replay`), scripted
//! STT, no audio device, no drivers. Tier enforcement, budget refusal, rate limits, token
//! expiry and origin, and per-visitor isolation (history, audio, spend, teardown).

use std::sync::Arc;
use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use r3x_contracts::{
    Ack, Body, Command, ConversationEvent, Envelope, Event, IntentCommand, MusicCommand, OperatingMode, PerfCommand, RobotProfile,
    Source, StageCommand, TelemetryCommand,
};
use r3x_runtime::public::{budget, token, Limits, PublicConfig, PublicServer};
use serde_json::json;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio_tungstenite::tungstenite::{client::IntoClientRequest, http::HeaderValue, Error, Message};

type Ws = tokio_tungstenite::WebSocketStream<tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>>;

const SECRET: &[u8] = b"test-secret-test-secret-test-secret!";
const ORIGIN: &str = "https://r3x.example";
const CHAT: &str = "what is your favourite cantina band";

fn fixtures() {
    static ONCE: std::sync::Once = std::sync::Once::new();
    ONCE.call_once(|| {
        let repo = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..");
        // SAFETY: every test sets the same values, once, before any runtime reads them.
        unsafe {
            std::env::set_var("R3X_FIXTURES", "replay");
            std::env::set_var("R3X_FIXTURE_DIR", repo.join("fixtures/smoke-voice"));
            std::env::set_var("R3X_FIXTURE_PACE", "0");
        }
    });
}

async fn server(limits: Limits, voice: bool) -> (Arc<PublicServer>, u16) {
    fixtures();
    let cfg = PublicConfig {
        secret: SECRET.to_vec(),
        admin_token: Some("admin-t0k".into()),
        origins: vec![ORIGIN.into()],
        token_ttl: Duration::from_secs(600),
        limits,
        trust_proxy: false,
        profile: Arc::new(RobotProfile::load(r3x_runtime::default_profile_path()).unwrap()),
        show_dir: r3x_runtime::performer::default_show_dir(),
        voice,
    };
    let sv = PublicServer::new(cfg);
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    tokio::spawn(sv.clone().serve(listener));
    (sv, port)
}

async fn connect(port: u16, token: &str, origin: Option<&str>) -> Result<Ws, u16> {
    let mut req = format!("ws://127.0.0.1:{port}/?token={token}").into_client_request().unwrap();
    if let Some(o) = origin {
        req.headers_mut().insert("Origin", HeaderValue::from_str(o).unwrap());
    }
    match tokio_tungstenite::connect_async(req).await {
        Ok((ws, _)) => Ok(ws),
        Err(Error::Http(r)) => Err(r.status().as_u16()),
        Err(e) => panic!("{e}"),
    }
}

async fn send(ws: &mut Ws, id: &str, cmd: Command) {
    ws.send(Message::Text(json!({"kind": "command", "id": id, "body": cmd}).to_string().into())).await.unwrap();
}

/// Next text envelope, if one arrives within `secs` (binary frames are counted into `audio`).
async fn next(ws: &mut Ws, secs: f64, audio: &mut usize) -> Option<Envelope> {
    let deadline = tokio::time::Instant::now() + Duration::from_secs_f64(secs);
    loop {
        match tokio::time::timeout_at(deadline, ws.next()).await {
            Ok(Some(Ok(Message::Text(t)))) => return Some(serde_json::from_str(t.as_str()).unwrap()),
            Ok(Some(Ok(Message::Binary(b)))) => *audio += usize::from(!b.is_empty()),
            Ok(Some(Ok(_))) => {}
            _ => return None,
        }
    }
}

async fn ack_for(ws: &mut Ws, id: &str) -> Ack {
    let mut n = 0;
    while let Some(env) = next(ws, 5.0, &mut n).await {
        if let (Body::Ack(a), Some(re)) = (env.body, env.re.as_deref()) {
            if re == id {
                return a;
            }
        }
    }
    panic!("no ack for {id}");
}

fn say(text: &str) -> Command {
    Command::Intent(IntentCommand::Say { text: text.into() })
}

fn rejected(a: Ack) -> String {
    match a {
        Ack::Rejected { reason } => reason,
        Ack::Accepted => panic!("expected a rejection"),
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn tokens_origin_and_expiry() {
    let (sv, port) = server(Limits::default(), false).await;
    let now = token::now_unix();
    let (expired, _) = token::mint(SECRET, now - 1);
    assert_eq!(connect(port, &expired, Some(ORIGIN)).await.err(), Some(401), "expired token");
    let (forged, _) = token::mint(b"someone-elses-secret-someone-elses", now + 60);
    assert_eq!(connect(port, &forged, Some(ORIGIN)).await.err(), Some(401), "wrong key");
    let (good, _) = sv.mint();
    assert_eq!(connect(port, &good, Some("https://evil.example")).await.err(), Some(403), "origin allow-list");

    // The admin endpoint mints a working token; without the admin bearer it refuses.
    let post = |auth: &'static str| async move {
        let mut s = tokio::net::TcpStream::connect(("127.0.0.1", port)).await.unwrap();
        let req = format!("POST /token HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {auth}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
        s.write_all(req.as_bytes()).await.unwrap();
        let mut out = String::new();
        s.read_to_string(&mut out).await.unwrap();
        out
    };
    assert!(post("nope").await.starts_with("HTTP/1.1 401"));
    let resp = post("admin-t0k").await;
    let body: serde_json::Value = serde_json::from_str(resp.split("\r\n\r\n").nth(1).unwrap()).unwrap();
    let mut ws = connect(port, body["token"].as_str().unwrap(), Some(ORIGIN)).await.expect("minted token works");
    let mut n = 0;
    let Some(Envelope { body: Body::Hello(h), .. }) = next(&mut ws, 5.0, &mut n).await else { panic!("hello") };
    assert_eq!(h.client.source, Source::Public);
    assert!(h.state.stage.brain, "the visitor's own stage has its brain on");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn public_tier_is_enforced() {
    let (sv, port) = server(Limits::default(), false).await;
    let mut ws = connect(port, &sv.mint().0, Some(ORIGIN)).await.unwrap();
    let refused = [
        Command::Stage(StageCommand::SetMode { mode: OperatingMode::Bench }),
        Command::Intent(IntentCommand::Music(MusicCommand::Play { query: None })),
        Command::Intent(IntentCommand::Dj { active: true }),
        Command::Intent(IntentCommand::Console { line: "reset".into() }),
        Command::Perf(PerfCommand::Freeze { on: true }),
        Command::Telemetry(TelemetryCommand::SetLogLevel { level: "debug".into() }),
    ];
    for (i, c) in refused.into_iter().enumerate() {
        let id = format!("r{i}");
        send(&mut ws, &id, c).await;
        rejected(ack_for(&mut ws, &id).await);
    }
    // Perf plays reach the visitor's performer, which holds them to `cheap`.
    let play = |id: &str| Command::Perf(PerfCommand::Play { id: id.into(), intensity: 1.0, speed: 1.0, layer: None });
    send(&mut ws, "show", play("dj_intro")).await;
    assert!(rejected(ack_for(&mut ws, "show").await).contains("tier"));
    send(&mut ws, "cheap", play("fist_pump")).await;
    assert!(ack_for(&mut ws, "cheap").await.is_accepted());
    send(&mut ws, "frames", Command::Telemetry(TelemetryCommand::Frames { enabled: true })).await;
    assert!(ack_for(&mut ws, "frames").await.is_accepted());
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn spent_budget_is_refused_in_character() {
    let (sv, port) = server(Limits { visitor_llm_tokens: 0, ..Limits::default() }, false).await;
    let mut ws = connect(port, &sv.mint().0, Some(ORIGIN)).await.unwrap();
    send(&mut ws, "q", say(CHAT)).await;
    let (mut reason, mut reply, mut n) = (None, None, 0);
    while let Some(env) = next(&mut ws, 3.0, &mut n).await {
        match env.body {
            Body::Ack(a) if env.re.as_deref() == Some("q") => reason = Some(rejected(a)),
            Body::Event(Event::Conversation(ConversationEvent::Reply { text })) => reply = Some(text),
            _ => {}
        }
        if reason.is_some() && reply.is_some() {
            break;
        }
    }
    assert_eq!(reason.as_deref(), Some(budget::VISITOR_SPENT));
    assert_eq!(reply.as_deref(), Some(budget::VISITOR_SPENT), "shown as R3X's reply");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn turn_and_connect_rate_limits() {
    let (sv, port) = server(Limits { turns_per_min: 2, connects_per_min: 3, ..Limits::default() }, false).await;
    let mut ws = connect(port, &sv.mint().0, Some(ORIGIN)).await.unwrap();
    for i in 0..2 {
        send(&mut ws, &format!("t{i}"), say("hello there")).await;
        assert!(ack_for(&mut ws, &format!("t{i}")).await.is_accepted());
    }
    send(&mut ws, "t2", say("hello again")).await;
    assert!(rejected(ack_for(&mut ws, "t2").await).contains("rate limited"));
    // Three connects a minute from one address (one used above).
    let _b = connect(port, &sv.mint().0, Some(ORIGIN)).await.unwrap();
    let _c = connect(port, &sv.mint().0, Some(ORIGIN)).await.err(); // 503: two sessions per IP
    assert_eq!(connect(port, &sv.mint().0, Some(ORIGIN)).await.err(), Some(429));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn visitors_are_isolated() {
    let (sv, port) = server(Limits::default(), true).await;
    let (ta, va) = sv.mint();
    let (tb, vb) = sv.mint();
    let mut a = connect(port, &ta, Some(ORIGIN)).await.unwrap();
    let mut b = connect(port, &tb, Some(ORIGIN)).await.unwrap();
    assert_eq!(sv.live_sessions(), 2);

    send(&mut a, "q", say(CHAT)).await;
    let (mut reply, mut audio_a) = (None, 0);
    let deadline = tokio::time::Instant::now() + Duration::from_secs(20);
    while (reply.is_none() || audio_a == 0) && tokio::time::Instant::now() < deadline {
        if let Some(Envelope { body: Body::Event(Event::Conversation(ConversationEvent::Reply { text })), .. }) = next(&mut a, 1.0, &mut audio_a).await {
            reply = Some(text);
        }
    }
    let reply = reply.expect("visitor A gets a reply");
    assert!(reply.starts_with("Ooh, tough one!"), "{reply}");
    assert!(audio_a > 0, "A hears its reply");

    // B saw none of it: no conversation events, no audio.
    let mut audio_b = 0;
    while let Some(env) = next(&mut b, 1.0, &mut audio_b).await {
        assert!(!matches!(env.body, Body::Event(Event::Conversation(_))), "B saw A's turn: {env:?}");
    }
    assert_eq!(audio_b, 0);
    let (ha, hb) = (sv.history(&va.id), sv.history(&vb.id));
    assert!(ha.len() >= 2 && ha[0].content.contains(CHAT), "{ha:?}");
    assert!(hb.is_empty(), "B's brain has its own, empty history");
    let (sa, sb) = (sv.spend(&va.id), sv.spend(&vb.id));
    assert!(sa.llm_tokens > 0 && sa.tts_chars as usize >= reply.chars().count(), "{sa:?}");
    assert_eq!((sb.llm_tokens, sb.tts_chars), (0, 0));

    // Disconnecting ends the session and frees its slot.
    drop(a);
    for _ in 0..50 {
        if sv.live_sessions() == 1 {
            break;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    assert_eq!(sv.live_sessions(), 1);
    assert!(sv.history(&va.id).is_empty(), "A's brain is gone");
}

//! Phase 2 acceptance, in-process and headless: a browser-style gateway client holds
//! push-to-talk, streams 16 kHz PCM, releases, and hears R3X's reply as 24 kHz PCM with
//! character timings and mouth levels. CantinaOS (tap), Deepgram and ElevenLabs are fakes
//! speaking their real protocols; the runtime, bridge, gateway and r3x-voice are real.

use std::sync::Arc;
use std::time::Duration;

use base64::Engine;
use futures_util::{SinkExt, StreamExt};
use r3x_bus::Bus;
use r3x_contracts::{Ack, Body, ClientInfo, Command, ConversationEvent, Envelope, Event, IntentCommand, RobotProfile, Source};
use r3x_gateway::ClientAuth;
use r3x_runtime::{all_classes, bridge::BridgeConfig, RuntimeConfig};
use r3x_voice::deepgram::DeepgramConfig;
use r3x_voice::eleven::ElevenConfig;
use r3x_voice::VoiceSettings;
use serde_json::{json, Value};
use tokio::net::TcpListener;
use tokio::sync::mpsc;
use tokio_tungstenite::tungstenite::{client::IntoClientRequest, http::HeaderValue, Message};

async fn listen() -> (TcpListener, u16) {
    let l = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = l.local_addr().unwrap().port();
    (l, port)
}

/// CantinaOS with voice external: acks and echoes emits; answers a mode request, a transcript
/// (with a reply) and a speak plan (with a TTS request), like its services would.
async fn fake_cantina() -> (String, mpsc::UnboundedReceiver<Value>) {
    let (l, port) = listen().await;
    let (seen, rx) = mpsc::unbounded_channel();
    tokio::spawn(async move {
        while let Ok((sock, _)) = l.accept().await {
            let seen = seen.clone();
            tokio::spawn(async move {
                let mut ws = tokio_tungstenite::accept_async(sock).await.unwrap();
                let ev = |topic: &str, payload: Value| Message::Text(json!({"v": 1, "kind": "event", "topic": topic, "payload": payload, "source": "cantina"}).to_string().into());
                while let Some(Ok(Message::Text(t))) = ws.next().await {
                    let m: Value = serde_json::from_str(t.as_str()).unwrap();
                    if m["id"].is_string() {
                        ws.send(Message::Text(json!({"v": 1, "kind": "ack", "re": m["id"], "ok": true}).to_string().into())).await.unwrap();
                    }
                    let (topic, p) = (m["topic"].as_str().unwrap_or("").to_owned(), m["payload"].clone());
                    ws.send(ev(&topic, p.clone())).await.unwrap();
                    match topic.as_str() {
                        "system.set.mode.request" => ws.send(ev("system.mode.change", json!({"old_mode": "IDLE", "new_mode": p["mode"]}))).await.unwrap(),
                        "voice.listening.stopped" if p["has_transcript"] == true => {
                            let r = json!({"conversation_id": p["conversation_id"], "text": "Coming right up!", "is_complete": true});
                            ws.send(ev("llm.response", r)).await.unwrap();
                        }
                        "plan.ready" => {
                            let step = &p["plan"]["steps"][0];
                            let r = json!({"text": step["text"], "clip_id": step["id"], "step_id": step["id"], "plan_id": p["plan_id"], "conversation_id": null});
                            ws.send(ev("tts.generate.request", r)).await.unwrap();
                        }
                        _ => {}
                    }
                    let _ = seen.send(m);
                }
            });
        }
    });
    (format!("ws://127.0.0.1:{port}/"), rx)
}

async fn fake_deepgram() -> u16 {
    let (l, port) = listen().await;
    tokio::spawn(async move {
        while let Ok((s, _)) = l.accept().await {
            tokio::spawn(async move {
                let mut ws = tokio_tungstenite::accept_async(s).await.unwrap();
                let mut bytes = 0;
                while let Some(Ok(m)) = ws.next().await {
                    match m {
                        Message::Binary(b) => bytes += b.len(),
                        Message::Text(t) if t.contains("Finalize") => {
                            let text = if bytes >= 6400 { "play some music" } else { "" };
                            let r = json!({"type": "Results", "is_final": true, "from_finalize": true,
                                "channel": {"alternatives": [{"transcript": text, "confidence": 0.9}]}});
                            ws.send(Message::Text(r.to_string().into())).await.unwrap();
                            bytes = 0;
                        }
                        _ => {}
                    }
                }
            });
        }
    });
    port
}

/// Dialogue socket: two 100 ms chunks per flush, each with timing for one character.
async fn fake_eleven() -> u16 {
    let (l, port) = listen().await;
    tokio::spawn(async move {
        while let Ok((s, _)) = l.accept().await {
            tokio::spawn(async move {
                let mut ws = tokio_tungstenite::accept_async(s).await.unwrap();
                while let Some(Ok(Message::Text(t))) = ws.next().await {
                    if !t.contains("flush") {
                        continue;
                    }
                    for c in ["C", "o"] {
                        let pcm: Vec<u8> = (0..2400).flat_map(|i| (((i as f32 * 0.3).sin() * 9000.0) as i16).to_le_bytes()).collect();
                        let audio = base64::engine::general_purpose::STANDARD.encode(pcm);
                        let align = json!({"chars": [c], "char_start_times_ms": [20], "char_durations_ms": [60]});
                        ws.send(Message::Text(json!({"audio": audio, "alignment": align}).to_string().into())).await.unwrap();
                    }
                    ws.send(Message::Text(json!({"is_final_audio_for_turn": true}).to_string().into())).await.unwrap();
                }
            });
        }
    });
    port
}

#[tokio::test]
async fn browser_hold_to_talk_and_hear_r3x() {
    let (tap_url, mut seen) = fake_cantina().await;
    let mut deepgram = DeepgramConfig::new("dg");
    deepgram.url = format!("ws://127.0.0.1:{}/v1/listen", fake_deepgram().await);
    let mut eleven = ElevenConfig::new("el");
    eleven.ws_base = format!("ws://127.0.0.1:{}", fake_eleven().await);
    let voice = VoiceSettings {
        deepgram,
        eleven,
        local_audio: false, // headless: speech goes only to gateway clients
        mic_device: None,
        output_device: None,
        mouth_hz: 30.0,
        client_buffer: Duration::from_millis(100),
    };

    let profile = RobotProfile::load(r3x_runtime::default_profile_path()).unwrap();
    let (listener, port) = listen().await;
    let cfg = RuntimeConfig {
        bind: listener.local_addr().unwrap(),
        clients: vec![ClientAuth { token: "t0k".into(), info: ClientInfo { name: "lobot".into(), source: Source::Ui, classes: all_classes() } }],
        origins: vec!["http://localhost:5391".into()],
        bridge: Some(BridgeConfig { tap_url, tap_token: "tap".into(), emotes: profile.emotes.clone() }),
        profile: Some(Arc::new(profile)),
        session_log: None,
        logs: None,
        voice: Some(voice),
        mouse: false,
    };
    tokio::spawn(r3x_runtime::run_on(Bus::default(), cfg, None, listener));

    let mut req = format!("ws://127.0.0.1:{port}/").into_client_request().unwrap();
    req.headers_mut().insert("Authorization", HeaderValue::from_static("Bearer t0k"));
    req.headers_mut().insert("Origin", HeaderValue::from_static("http://localhost:5391"));
    let (mut ws, _) = tokio_tungstenite::connect_async(req).await.unwrap();

    let send_cmd = |id: &str, c: IntentCommand| Message::Text(json!({"kind": "command", "id": id, "body": Command::Intent(c)}).to_string().into());
    // Hold: retried while the bridge's tap link comes up.
    let mut acked = None;
    for i in 0..40 {
        let id = format!("p{i}");
        ws.send(send_cmd(&id, IntentCommand::PttStart)).await.unwrap();
        let ack = wait_ack(&mut ws, &id).await;
        if ack.is_accepted() {
            acked = Some(ack);
            break;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    assert_eq!(acked, Some(Ack::Accepted), "push-to-talk start");

    // Talk: 400 ms of 16 kHz PCM in 20 ms frames, as the browser page sends it.
    let meta = json!({"kind": "audio", "body": {"direction": "in", "sample_rate": 16000, "channels": 1}});
    ws.send(Message::Text(meta.to_string().into())).await.unwrap();
    for _ in 0..20 {
        ws.send(Message::Binary(vec![1u8; 640].into())).await.unwrap();
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
    ws.send(send_cmd("s", IntentCommand::PttStop)).await.unwrap();
    assert_eq!(wait_ack(&mut ws, "s").await, Ack::Accepted);

    // Hear: 24 kHz PCM frames plus timing and mouth events, until the line ends.
    let (mut pcm_bytes, mut timings, mut mouths, mut spoke) = (0usize, 0, 0, false);
    let deadline = tokio::time::Instant::now() + Duration::from_secs(10);
    let mut out_meta = None;
    while tokio::time::Instant::now() < deadline {
        let Ok(Some(Ok(m))) = tokio::time::timeout(Duration::from_secs(5), ws.next()).await else { break };
        match m {
            Message::Binary(b) => {
                if b.is_empty() {
                    break; // line end
                }
                pcm_bytes += b.len();
            }
            Message::Text(t) => {
                let env: Envelope = serde_json::from_str(t.as_str()).unwrap();
                match env.body {
                    Body::Audio(meta) => out_meta = Some(meta),
                    Body::Event(Event::Conversation(ConversationEvent::SpeechTiming { start_ms, .. })) => {
                        timings += 1;
                        assert!(start_ms[0] == 20.0 || start_ms[0] == 120.0, "rebased: {start_ms:?}");
                    }
                    Body::Event(Event::Conversation(ConversationEvent::Mouth { .. })) => mouths += 1,
                    Body::Event(Event::Conversation(ConversationEvent::SpeechStarted)) => spoke = true,
                    _ => {}
                }
            }
            _ => {}
        }
    }
    let meta = out_meta.expect("audio meta");
    assert_eq!((meta.sample_rate, meta.channels), (24_000, 1));
    assert_eq!(pcm_bytes, 2 * 4800, "the whole 200 ms reply reached the browser");
    assert_eq!(timings, 2);
    assert!(mouths >= 3, "mouth levels while speaking: {mouths}");
    assert!(spoke, "speech_started came back through the bridge");

    // CantinaOS saw the turn exactly as its own mic and TTS services used to emit it.
    let mut topics = Vec::new();
    let mut turn = None;
    while let Ok(m) = seen.try_recv() {
        let topic = m["topic"].as_str().unwrap_or("").to_owned();
        match topic.as_str() {
            "voice.listening.started" => turn = m["payload"]["conversation_id"].as_str().map(str::to_owned),
            "voice.listening.stopped" => {
                assert_eq!(m["payload"]["transcript"], "play some music");
                assert_eq!(m["payload"]["conversation_id"].as_str(), turn.as_deref(), "turn id carried");
            }
            "speech.generation.started" => {
                assert_eq!(m["payload"]["conversation_id"].as_str(), turn.as_deref(), "reply spoken under the turn id");
                assert!(m["payload"]["audio_t0"].as_f64().is_some());
            }
            "speech.generation.complete" => assert_eq!(m["payload"]["success"], true),
            _ => {}
        }
        topics.push(topic);
    }
    for t in ["voice.listening.started", "mouse.recording.stopped", "voice.listening.stopped", "plan.ready", "speech.generation.started", "speech.alignment", "speech.synthesis.amplitude"] {
        assert!(topics.iter().any(|x| x == t), "{t} missing from {topics:?}");
    }
    for _ in 0..50 {
        if topics.iter().any(|x| x == "speech.generation.complete") {
            break;
        }
        tokio::time::sleep(Duration::from_millis(20)).await;
        while let Ok(m) = seen.try_recv() {
            topics.push(m["topic"].as_str().unwrap_or("").to_owned());
        }
    }
    assert!(topics.iter().any(|x| x == "speech.generation.complete"));
}

async fn wait_ack(ws: &mut tokio_tungstenite::WebSocketStream<tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>>, id: &str) -> Ack {
    loop {
        let m = tokio::time::timeout(Duration::from_secs(8), ws.next()).await.expect("ack timed out").unwrap().unwrap();
        if let Message::Text(t) = m {
            let env: Envelope = serde_json::from_str(t.as_str()).unwrap();
            if let (Body::Ack(a), Some(re)) = (env.body, env.re.as_deref()) {
                if re == id {
                    return a;
                }
            }
        }
    }
}

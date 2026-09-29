//! `r3x-voice` - voice tools and a standalone bridge-mode voice.
//!
//! ```text
//! r3x-voice devices                 list audio devices
//! r3x-voice say "text"              speak through the default output (real ElevenLabs)
//! r3x-voice roundtrip "text"        ElevenLabs -> Deepgram, headless; prints transcript + timings
//! r3x-voice listen [secs]           one turn from the local mic (real Deepgram)
//! r3x-voice bridge [--tap-url URL] [--mouse]
//!                                   voice for a running CantinaOS (R3X_EXTERNAL_VOICE=1):
//!                                   the runtime's --bridge does the same once it hosts voice
//! ```
//! Keys come from the environment or the repo-root `.env`.

use std::sync::Arc;
use std::time::{Duration, Instant};

use anyhow::{bail, Context, Result};
use futures_util::{SinkExt, StreamExt};
use r3x_bus::Bus;
use r3x_contracts::{Engagement, EngagementState, Source};
use r3x_voice::cantina::CantinaVoice;
use r3x_voice::speaker::{SpeechEvent, SpeechRequest};
use r3x_voice::{VoiceEvent, VoiceSettings};
use serde_json::{json, Value};
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::http::HeaderValue;
use tokio_tungstenite::tungstenite::Message;

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt().with_env_filter(tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into())).init();
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.first().map(String::as_str) {
        Some("devices") => {
            for d in r3x_audio::device::list_devices()? {
                println!("{}{}{} {}", if d.input { "in " } else { "   " }, if d.output { "out" } else { "   " }, if d.default { " *" } else { "  " }, d.name);
            }
            Ok(())
        }
        Some("say") => say(args.get(1).context("say needs text")?).await,
        Some("roundtrip") => roundtrip(args.get(1).map_or("Hello from the cantina. Play some music.", String::as_str)).await,
        Some("listen") => listen(args.get(1).and_then(|s| s.parse().ok()).unwrap_or(4.0)).await,
        Some("bridge") => bridge(&args[1..]).await,
        _ => bail!("usage: r3x-voice devices | say TEXT | roundtrip [TEXT] | listen [SECS] | bridge [--tap-url URL] [--mouse]"),
    }
}

fn interactive(bus: &Bus) {
    bus.set(Source::System, EngagementState { engagement: Engagement::Interactive });
}

async fn say(text: &str) -> Result<()> {
    let bus = Bus::default();
    let stack = r3x_voice::start(&bus, VoiceSettings::from_env()?)?;
    let mut rx = stack.voice.subscribe();
    let t0 = Instant::now();
    stack.voice.say(SpeechRequest::reply(text, Some("cli".into()), Source::Cli));
    let mut mouths = 0;
    loop {
        match rx.recv().await? {
            VoiceEvent::Speech(SpeechEvent::Started { .. }) => println!("first audible sample at {} ms", t0.elapsed().as_millis()),
            VoiceEvent::Speech(SpeechEvent::Mouth { .. }) => mouths += 1,
            VoiceEvent::Speech(SpeechEvent::Ended { audio_s, error, .. }) => {
                println!("ended after {} ms: {audio_s:.2} s of audio, {mouths} mouth updates, error={error:?}", t0.elapsed().as_millis());
                return Ok(());
            }
            _ => {}
        }
    }
}

/// One real ElevenLabs line, fed to one real Deepgram turn, no devices.
async fn roundtrip(text: &str) -> Result<()> {
    use r3x_voice::eleven::{open_speech, Eleven};
    let s = VoiceSettings::from_env()?;
    let eleven = Eleven::start(s.eleven.clone());
    let t0 = Instant::now();
    let (first, mut rest) = open_speech(eleven.as_ref(), text).await?;
    let first = first.context("no audio")?;
    println!("tts first chunk after {} ms", t0.elapsed().as_millis());
    let mut pcm = first.pcm;
    let mut timed_chars = first.alignment.map_or(0, |a| a.chars.len());
    while let Some(c) = rest.recv().await {
        let c = c?;
        timed_chars += c.alignment.as_ref().map_or(0, |a| a.chars.len());
        pcm.extend(c.pcm);
    }
    println!("tts done after {} ms: {:.2} s of audio, {timed_chars} timed chars", t0.elapsed().as_millis(), pcm.len() as f64 / 24_000.0);

    let bus = Bus::default();
    interactive(&bus);
    let stt = r3x_voice::deepgram::Stt::spawn(s.deepgram.clone(), r3x_voice::voice::interactive(&bus));
    let mut up = stt.connected();
    tokio::time::timeout(Duration::from_secs(10), up.wait_for(|u| *u)).await??;
    stt.begin("roundtrip").await?;
    let mut rs = r3x_audio::resample::Resampler::new(24_000, 16_000);
    let mut f16 = Vec::new();
    let f: Vec<f32> = pcm.iter().map(|&s| r3x_audio::i16_to_f32(s)).collect();
    rs.process(&f, &mut f16);
    let pcm16: Vec<i16> = f16.iter().map(|&s| r3x_audio::f32_to_i16(s)).collect();
    // Real time, 20 ms chunks, as the mic would.
    let mut tick = tokio::time::interval(Duration::from_millis(20));
    for c in pcm16.chunks(r3x_audio::MIC_CHUNK) {
        tick.tick().await;
        stt.audio(c.to_vec());
    }
    let stop = Instant::now();
    let transcript = stt.finish().await;
    println!("stt: stop -> final transcript {} ms: {transcript:?}", stop.elapsed().as_millis());
    Ok(())
}

async fn listen(secs: f64) -> Result<()> {
    let bus = Bus::default();
    interactive(&bus);
    let stack = r3x_voice::start(&bus, VoiceSettings::from_env()?)?;
    let mut rx = stack.voice.subscribe();
    let ack = stack.voice.ptt_start("cli").await;
    println!("ptt_start: {ack:?} - speak now");
    let until = tokio::time::Instant::now() + Duration::from_secs_f64(secs);
    loop {
        tokio::select! {
            _ = tokio::time::sleep_until(until) => break,
            e = rx.recv() => if let Ok(VoiceEvent::Transcript(t)) = e { println!("{} {}", if t.is_final { "final  " } else { "interim" }, t.text) },
        }
    }
    let stop = Instant::now();
    stack.voice.ptt_stop(Some("cli")).await;
    while let Ok(e) = rx.recv().await {
        if let VoiceEvent::ListeningStopped { transcript, .. } = e {
            println!("transcript after {} ms: {transcript:?}", stop.elapsed().as_millis());
            break;
        }
    }
    Ok(())
}

/// Standalone bridge-mode voice: connects to the CantinaOS tap, mirrors its engagement, and
/// runs mic/STT/TTS locally.
async fn bridge(args: &[String]) -> Result<()> {
    let mut url = std::env::var("R3X_TAP_URL").unwrap_or_else(|_| "ws://127.0.0.1:8766/".into());
    let mut mouse = false;
    let mut it = args.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--tap-url" => url = it.next().context("--tap-url needs a value")?.clone(),
            "--mouse" => mouse = true,
            other => bail!("unknown argument {other}"),
        }
    }
    let token = match std::env::var("R3X_TAP_TOKEN").ok().filter(|t| !t.is_empty()) {
        Some(t) => t,
        None => std::fs::read_to_string(r3x_gateway::tokens::config_dir().join("tap_token")).context("tap token")?.trim().to_owned(),
    };
    let bus = Bus::default();
    let stack = r3x_voice::start(&bus, VoiceSettings::from_env()?)?;
    r3x_voice::voice::spawn_bus_adapter(&stack.voice, bus.clone(), true);
    if mouse {
        #[cfg(feature = "mouse")]
        r3x_voice::mouse::spawn(bus.clone(), stack.voice.clone())?;
        #[cfg(not(feature = "mouse"))]
        bail!("--mouse needs the `mouse` feature");
    }
    let (tx, mut out) = tokio::sync::mpsc::unbounded_channel::<String>();
    let emit = move |topic: &str, payload: Value| {
        let _ = tx.send(json!({"topic": topic, "payload": payload, "source": "r3x-voice"}).to_string());
    };
    let cantina = CantinaVoice::attach(&stack.voice, Arc::new(emit));
    loop {
        let mut req = url.as_str().into_client_request()?;
        req.headers_mut().insert("Authorization", HeaderValue::from_str(&format!("Bearer {token}"))?);
        let ws = match tokio_tungstenite::connect_async(req).await {
            Ok((ws, _)) => ws,
            Err(e) => {
                tracing::warn!(error = %e, "tap unreachable; retrying");
                tokio::time::sleep(Duration::from_secs(2)).await;
                continue;
            }
        };
        tracing::info!(%url, "connected to the CantinaOS tap");
        let (mut sink, mut stream) = ws.split();
        loop {
            tokio::select! {
                m = out.recv() => match m {
                    Some(m) => if sink.send(Message::Text(m.into())).await.is_err() { break },
                    None => return Ok(()),
                },
                m = stream.next() => match m {
                    Some(Ok(Message::Text(t))) => {
                        let Ok(v) = serde_json::from_str::<Value>(t.as_str()) else { continue };
                        if v["kind"] != "event" { continue }
                        let topic = v["topic"].as_str().unwrap_or("");
                        if topic == "system.mode.change" {
                            if let Some(e) = v["payload"]["new_mode"].as_str().and_then(engagement) {
                                bus.set(Source::Bridge, EngagementState { engagement: e });
                            }
                        }
                        cantina.on_tap(topic, &v["payload"]);
                    }
                    Some(Ok(_)) => {}
                    _ => break,
                },
            }
        }
        tracing::warn!("tap connection lost; reconnecting");
    }
}

fn engagement(raw: &str) -> Option<Engagement> {
    Some(match raw.to_ascii_uppercase().as_str() {
        "STARTUP" => Engagement::Startup,
        "IDLE" => Engagement::Idle,
        "AMBIENT" => Engagement::Ambient,
        "INTERACTIVE" => Engagement::Interactive,
        _ => return None,
    })
}

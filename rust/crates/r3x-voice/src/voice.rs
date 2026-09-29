//! Turns: push-to-talk ownership, mic input (local device or a remote client), STT, and the
//! speech FIFO, published as [`VoiceEvent`]s.
//!
//! - The turn id is minted here, at capture, and carried by every event of the turn.
//! - `state.conversation.ptt_owner` is the single owner of push-to-talk (replaces the
//!   "panel announces itself so the mouse yields" handshake). Another owner is refused.
//! - A start is refused while R3X speaks (the mic would record R3X). Outside INTERACTIVE it
//!   asks for INTERACTIVE first, then waits (bounded) for STT to connect; refused only if
//!   engaging is refused or STT does not come up.
//! - Input: the local mic opens at once, but its first `remote_grace` is held back; if a
//!   remote client starts streaming in that window the local mic is closed and the turn is
//!   the client's. So one `ptt.start` works for the Mac panel and a browser elsewhere alike.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use r3x_bus::Bus;
use r3x_contracts::{
    Ack, Command, ConversationEvent, ConversationPhase, ConversationState, Engagement, EngagementState, Event, Source,
    StageCommand,
};
use tokio::sync::{broadcast, mpsc, watch};
use tokio::task::JoinHandle;
use tokio::time::Instant;

use crate::deepgram::{Stt, Transcript};
use crate::speaker::{Speaker, SpeechEvent, SpeechRequest};

/// Something that can open a microphone: 16 kHz mono, 20 ms chunks.
pub trait MicOpener: Send + Sync + 'static {
    fn open(&self) -> anyhow::Result<MicStream>;
}

pub struct MicStream {
    pub chunks: mpsc::Receiver<Vec<i16>>,
    /// Keeps the device open; dropped when the turn ends.
    pub guard: Box<dyn Send>,
}

#[cfg(feature = "device")]
pub struct DeviceMic(pub Option<String>);

#[cfg(feature = "device")]
impl MicOpener for DeviceMic {
    fn open(&self) -> anyhow::Result<MicStream> {
        let (chunks, guard) = r3x_audio::device::MicCapture::open(self.0.as_deref())?.split();
        Ok(MicStream { chunks, guard: Box::new(guard) })
    }
}

/// A mic that hears silence (`--audio null`): 20 ms chunks in real time until closed.
pub struct SilentMic;

impl MicOpener for SilentMic {
    fn open(&self) -> anyhow::Result<MicStream> {
        let (tx, chunks) = mpsc::channel(64);
        tokio::spawn(async move {
            let mut tick = tokio::time::interval(Duration::from_millis(u64::from(r3x_audio::CHUNK_MS)));
            while tx.send(vec![0i16; r3x_audio::MIC_CHUNK]).await.is_ok() {
                tick.tick().await;
            }
        });
        Ok(MicStream { chunks, guard: Box::new(()) })
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum VoiceEvent {
    ListeningStarted { turn: String, owner: String },
    Transcript(Transcript),
    /// Push-to-talk released; the final transcript follows (after `Finalize`).
    Released { turn: String },
    ListeningStopped { turn: String, transcript: String },
    Speech(SpeechEvent),
}

#[derive(Debug, Clone)]
pub struct VoiceOptions {
    /// How long local mic audio is held back waiting to see if a remote client streams.
    pub remote_grace: Duration,
    /// How long a start waits for STT to come up (e.g. right after engaging).
    pub connect_wait: Duration,
}

impl Default for VoiceOptions {
    fn default() -> Self {
        Self { remote_grace: Duration::from_millis(200), connect_wait: Duration::from_secs(3) }
    }
}

struct ActiveTurn {
    id: String,
    owner: String,
    remote: Arc<AtomicBool>,
    pump: Option<JoinHandle<()>>,
}

struct Inner {
    bus: Bus,
    stt: Stt,
    speaker: Speaker,
    mic: Option<Arc<dyn MicOpener>>,
    opts: VoiceOptions,
    turn: tokio::sync::Mutex<Option<ActiveTurn>>,
    /// Remote PCM not yet a whole 20 ms chunk.
    remote_rest: Mutex<Vec<i16>>,
    events: broadcast::Sender<VoiceEvent>,
}

/// The voice service. Cheap to clone.
#[derive(Clone)]
pub struct Voice {
    inner: Arc<Inner>,
}

/// `state.engagement == INTERACTIVE` as a watch (gates the STT socket).
pub fn interactive(bus: &Bus) -> watch::Receiver<bool> {
    let mut eng = bus.watch::<EngagementState>();
    let (tx, rx) = watch::channel(eng.borrow().engagement == Engagement::Interactive);
    tokio::spawn(async move {
        while eng.changed().await.is_ok() {
            let on = eng.borrow().engagement == Engagement::Interactive;
            tx.send_if_modified(|v| std::mem::replace(v, on) != on);
        }
    });
    rx
}

impl Voice {
    pub fn new(bus: Bus, stt: Stt, speaker: Speaker, mic: Option<Arc<dyn MicOpener>>, opts: VoiceOptions) -> Self {
        let events = broadcast::channel(1024).0;
        let v = Self {
            inner: Arc::new(Inner {
                bus,
                stt: stt.clone(),
                speaker: speaker.clone(),
                mic,
                opts,
                turn: tokio::sync::Mutex::new(None),
                remote_rest: Mutex::default(),
                events: events.clone(),
            }),
        };
        let mut t = stt.subscribe();
        let tx = events.clone();
        tokio::spawn(async move {
            loop {
                match t.recv().await {
                    Ok(tr) => {
                        let _ = tx.send(VoiceEvent::Transcript(tr));
                    }
                    Err(broadcast::error::RecvError::Lagged(_)) => {}
                    Err(_) => break,
                }
            }
        });
        let mut s = speaker.subscribe();
        tokio::spawn(async move {
            loop {
                match s.recv().await {
                    Ok(e) => {
                        let _ = events.send(VoiceEvent::Speech(e));
                    }
                    Err(broadcast::error::RecvError::Lagged(n)) => tracing::warn!(n, "voice: speech events lagged"),
                    Err(_) => break,
                }
            }
        });
        v
    }

    pub fn subscribe(&self) -> broadcast::Receiver<VoiceEvent> {
        self.inner.events.subscribe()
    }

    pub fn speaker(&self) -> &Speaker {
        &self.inner.speaker
    }

    pub fn stt(&self) -> &Stt {
        &self.inner.stt
    }

    pub fn say(&self, req: SpeechRequest) -> bool {
        self.inner.speaker.say(req)
    }

    /// Current turn id and owner, if listening.
    pub async fn listening(&self) -> Option<(String, String)> {
        self.inner.turn.lock().await.as_ref().map(|t| (t.id.clone(), t.owner.clone()))
    }

    pub async fn ptt_start(&self, owner: &str) -> Ack {
        let inner = &self.inner;
        let mut turn = inner.turn.lock().await;
        if let Some(t) = turn.as_ref() {
            return if t.owner == owner { Ack::Accepted } else { Ack::rejected(format!("push-to-talk is held by {}", t.owner)) };
        }
        if inner.speaker.is_speaking() {
            return Ack::rejected("R3X is speaking; the mic would record R3X");
        }
        // Starting a recording engages (CantinaOS did the same): STT only connects while
        // INTERACTIVE, so ask the StageManager, then wait (bounded) for the socket below.
        if inner.bus.get::<EngagementState>().engagement != Engagement::Interactive {
            let engage = Command::Stage(StageCommand::SetEngagement { engagement: Engagement::Interactive });
            let ack = inner.bus.command(Source::System, None, engage).await;
            if !ack.is_accepted() {
                return ack;
            }
        }
        if !inner.stt.is_up() {
            let mut up = inner.stt.connected();
            let _ = tokio::time::timeout(inner.opts.connect_wait, up.wait_for(|u| *u)).await;
        }
        let id = uuid::Uuid::new_v4().to_string();
        if let Err(e) = inner.stt.begin(&id).await {
            return Ack::rejected(e.to_string());
        }
        let remote = Arc::new(AtomicBool::new(false));
        inner.remote_rest.lock().unwrap().clear();
        let pump = inner.mic.as_ref().and_then(|m| match m.open() {
            Ok(mic) => Some(tokio::spawn(pump(mic, inner.stt.clone(), remote.clone(), inner.opts.remote_grace))),
            Err(e) => {
                tracing::warn!(error = %e, "local mic did not open; waiting for remote audio");
                None
            }
        });
        inner.bus.update(Source::System, |c: &mut ConversationState| {
            c.ptt_owner = Some(owner.to_owned());
        });
        tracing::info!(turn = %id, owner, "listening");
        let _ = inner.events.send(VoiceEvent::ListeningStarted { turn: id.clone(), owner: owner.to_owned() });
        *turn = Some(ActiveTurn { id, owner: owner.to_owned(), remote, pump });
        Ack::Accepted
    }

    /// Release push-to-talk. `owner: None` releases whoever holds it.
    pub async fn ptt_stop(&self, owner: Option<&str>) -> Ack {
        let inner = &self.inner;
        let t = {
            let mut turn = inner.turn.lock().await;
            match turn.as_ref() {
                None => return Ack::Accepted,
                Some(t) if owner.is_some_and(|o| o != t.owner) => {
                    return Ack::rejected(format!("push-to-talk is held by {}", t.owner));
                }
                Some(_) => turn.take().expect("checked"),
            }
        };
        if let Some(p) = &t.pump {
            p.abort(); // drops the mic stream and closes the device
        }
        let rest = std::mem::take(&mut *inner.remote_rest.lock().unwrap());
        if !rest.is_empty() {
            inner.stt.audio(rest);
        }
        let _ = inner.events.send(VoiceEvent::Released { turn: t.id.clone() });
        inner.bus.update(Source::System, |c: &mut ConversationState| c.ptt_owner = None);
        let transcript = inner.stt.finish().await;
        tracing::info!(turn = %t.id, %transcript, "turn transcript");
        let _ = inner.events.send(VoiceEvent::ListeningStopped { turn: t.id, transcript });
        Ack::Accepted
    }

    /// Toggle for a click-style trigger (the global mouse). Honours ownership.
    pub async fn toggle(&self, owner: &str) -> Ack {
        let held = self.listening().await;
        match held {
            Some((_, o)) if o == owner => self.ptt_stop(Some(owner)).await,
            Some((_, o)) => Ack::rejected(format!("push-to-talk is held by {o}")),
            None => self.ptt_start(owner).await,
        }
    }

    /// 16 kHz mono PCM from a remote client for the current turn.
    pub async fn remote_audio(&self, client: &str, pcm: &[i16]) {
        let turn = self.inner.turn.lock().await;
        let Some(t) = turn.as_ref() else { return };
        if !t.remote.swap(true, Ordering::SeqCst) {
            tracing::info!(turn = %t.id, client, owner = %t.owner, "turn audio is remote");
        }
        let mut rest = self.inner.remote_rest.lock().unwrap();
        rest.extend_from_slice(pcm);
        let whole = rest.len() / r3x_audio::MIC_CHUNK * r3x_audio::MIC_CHUNK;
        for c in rest.drain(..whole).collect::<Vec<_>>().chunks(r3x_audio::MIC_CHUNK) {
            self.inner.stt.audio(c.to_vec());
        }
    }
}

/// Forward local mic audio, held back for `grace` in case the turn turns out to be remote.
async fn pump(mut mic: MicStream, stt: Stt, remote: Arc<AtomicBool>, grace: Duration) {
    let until = Instant::now() + grace;
    let mut held: Vec<Vec<i16>> = Vec::new();
    while let Some(chunk) = mic.chunks.recv().await {
        if remote.load(Ordering::SeqCst) {
            tracing::debug!("remote audio took the turn; closing the local mic");
            return;
        }
        if Instant::now() < until {
            held.push(chunk);
            continue;
        }
        for c in held.drain(..) {
            stt.audio(c);
        }
        stt.audio(chunk);
    }
}

/// Publish voice events on the typed bus.
///
/// `lifecycle`: also the turn/speech lifecycle (`listening_*`, `transcript`, `speech_*`,
/// conversation phase). Off in bridge mode, where those come back from CantinaOS through the
/// tap translation; mouth and timing are always published (the bridge does not carry them).
pub fn spawn_bus_adapter(voice: &Voice, bus: Bus, lifecycle: bool) -> JoinHandle<()> {
    let mut rx = voice.subscribe();
    let clock = bus.clock();
    tokio::spawn(async move {
        loop {
            let e = match rx.recv().await {
                Ok(e) => e,
                Err(broadcast::error::RecvError::Lagged(_)) => continue,
                Err(_) => return,
            };
            let conv = |e: ConversationEvent| Event::Conversation(e);
            let phase = |to: ConversationPhase, id: Option<String>| {
                bus.update(Source::System, |c: &mut ConversationState| {
                    c.phase = to;
                    if id.is_some() {
                        c.conversation_id = id.clone();
                    }
                });
            };
            match e {
                VoiceEvent::Speech(SpeechEvent::Mouth { req, level }) => {
                    bus.publish(Source::System, req.conversation_id.clone(), conv(ConversationEvent::Mouth { level: level as f64 }));
                }
                VoiceEvent::Speech(SpeechEvent::Timing { req, timings, t0 }) => {
                    let ago = t0.at.elapsed().as_secs_f64();
                    bus.publish(
                        Source::System,
                        req.conversation_id.clone(),
                        conv(ConversationEvent::SpeechTiming {
                            chars: timings.chars,
                            start_ms: timings.start_ms,
                            duration_ms: timings.duration_ms,
                            audio_t0: clock.t_mono() - ago,
                        }),
                    );
                }
                _ if !lifecycle => {}
                VoiceEvent::ListeningStarted { turn, .. } => {
                    phase(ConversationPhase::Listening, Some(turn.clone()));
                    bus.publish(Source::System, Some(turn), conv(ConversationEvent::ListeningStarted));
                }
                VoiceEvent::Transcript(t) => {
                    bus.publish(Source::System, Some(t.turn), conv(ConversationEvent::Transcript { text: t.text, is_final: t.is_final }));
                }
                VoiceEvent::Released { .. } => {}
                VoiceEvent::ListeningStopped { turn, transcript } => {
                    let next = if transcript.is_empty() { ConversationPhase::Idle } else { ConversationPhase::Thinking };
                    phase(next, Some(turn.clone()));
                    bus.publish(Source::System, Some(turn), conv(ConversationEvent::ListeningStopped { transcript }));
                }
                VoiceEvent::Speech(SpeechEvent::Started { req, .. }) => {
                    phase(ConversationPhase::Speaking, req.conversation_id.clone());
                    bus.publish(Source::System, req.conversation_id.clone(), conv(ConversationEvent::SpeechStarted));
                }
                VoiceEvent::Speech(SpeechEvent::Ended { req, .. }) => {
                    phase(ConversationPhase::Idle, None);
                    bus.publish(Source::System, req.conversation_id.clone(), conv(ConversationEvent::SpeechEnded));
                }
                VoiceEvent::Speech(SpeechEvent::Dropped { .. }) => {}
            }
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::deepgram::DeepgramConfig;
    use crate::eleven::{ChunkRx, TtsBackend, TtsChunk};
    use futures_util::{SinkExt, StreamExt};
    use r3x_audio::sink::NullSink;
    use serde_json::json;
    use tokio::net::TcpListener;
    use tokio_tungstenite::tungstenite::Message;

    /// Answers every `Finalize` with a final; counts binary bytes.
    async fn mock_deepgram() -> (u16, Arc<std::sync::atomic::AtomicUsize>) {
        let l = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = l.local_addr().unwrap().port();
        let bytes = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let b = bytes.clone();
        tokio::spawn(async move {
            let (s, _) = l.accept().await.unwrap();
            let mut ws = tokio_tungstenite::accept_async(s).await.unwrap();
            while let Some(Ok(m)) = ws.next().await {
                match m {
                    Message::Binary(d) => { b.fetch_add(d.len(), Ordering::SeqCst); }
                    Message::Text(t) if t.contains("Finalize") => {
                        let r = json!({"type": "Results", "is_final": true, "from_finalize": true,
                            "channel": {"alternatives": [{"transcript": "hello there", "confidence": 0.9}]}});
                        ws.send(Message::Text(r.to_string().into())).await.unwrap();
                    }
                    _ => {}
                }
            }
        });
        (port, bytes)
    }

    struct Beep;
    impl TtsBackend for Beep {
        fn dialogue(&self, _: &str) -> Option<ChunkRx> {
            let (tx, rx) = mpsc::channel(4);
            tx.try_send(Ok(TtsChunk { pcm: vec![3000; 7200], alignment: None })).unwrap(); // 300 ms
            Some(rx)
        }
        fn http(&self, _: &str) -> ChunkRx {
            mpsc::channel(1).1
        }
    }

    /// A mic that produces 20 ms chunks until the turn drops it.
    struct FakeMic(Arc<AtomicBool>);
    impl MicOpener for FakeMic {
        fn open(&self) -> anyhow::Result<MicStream> {
            let (tx, chunks) = mpsc::channel(16);
            let closed = self.0.clone();
            tokio::spawn(async move {
                while tx.send(vec![0i16; 320]).await.is_ok() {
                    tokio::time::sleep(Duration::from_millis(20)).await;
                }
                closed.store(true, Ordering::SeqCst);
            });
            Ok(MicStream { chunks, guard: Box::new(()) })
        }
    }

    #[tokio::test]
    async fn ptt_ownership_remote_takeover_and_speaking_refusal() {
        let (port, dg_bytes) = mock_deepgram().await;
        let bus = Bus::default();
        bus.set(Source::System, EngagementState { engagement: Engagement::Interactive });
        let mut cfg = DeepgramConfig::new("k");
        cfg.url = format!("ws://127.0.0.1:{port}/v1/listen");
        let stt = Stt::spawn(cfg, interactive(&bus));
        let speaker = Speaker::spawn(Arc::new(Beep), Arc::new(NullSink::new(1.0)), 30.0);
        let mic_closed = Arc::new(AtomicBool::new(false));
        let voice = Voice::new(bus.clone(), stt, speaker, Some(Arc::new(FakeMic(mic_closed.clone()))), VoiceOptions::default());
        spawn_bus_adapter(&voice, bus.clone(), true);
        let mut ev = voice.subscribe();

        assert!(voice.ptt_start("panel").await.is_accepted());
        assert!(!voice.ptt_start("mouse").await.is_accepted(), "one owner");
        assert!(!voice.toggle("mouse").await.is_accepted());
        assert_eq!(bus.get::<ConversationState>().ptt_owner.as_deref(), Some("panel"));

        voice.remote_audio("panel", &vec![5i16; 800]).await; // 50 ms from a browser
        tokio::time::sleep(Duration::from_millis(80)).await;
        assert!(mic_closed.load(Ordering::SeqCst), "remote audio took the turn from the local mic");
        assert!(voice.ptt_stop(Some("panel")).await.is_accepted());
        assert_eq!(dg_bytes.load(Ordering::SeqCst), 800 * 2, "only remote audio reached STT (local was held back)");

        let VoiceEvent::ListeningStarted { turn, .. } = ev.recv().await.unwrap() else { panic!() };
        let mut stopped = None;
        while stopped.is_none() {
            if let VoiceEvent::ListeningStopped { turn: t, transcript } = ev.recv().await.unwrap() {
                stopped = Some((t, transcript));
            }
        }
        assert_eq!(stopped.unwrap(), (turn.clone(), "hello there".to_string()), "turn id minted at capture, carried to the end");
        let conv = bus.get::<ConversationState>();
        assert_eq!((conv.ptt_owner, conv.conversation_id.as_deref()), (None, Some(turn.as_str())));

        voice.say(SpeechRequest::reply("hi", Some("t2".into()), Source::Claude));
        voice.speaker().speaking().wait_for(|s| *s).await.unwrap();
        let ack = voice.ptt_start("panel").await;
        assert!(!ack.is_accepted(), "mic refuses while R3X speaks: {ack:?}");
        voice.speaker().speaking().wait_for(|s| !*s).await.unwrap();
        assert!(voice.ptt_start("mouse").await.is_accepted(), "released on completion");
    }

    /// A StageManager stand-in: engagement requests succeed unless `allow` is false.
    fn stage_stub(bus: &Bus, allow: bool) {
        let mut rx = bus.take_commands(r3x_contracts::MessageClass::Stage).unwrap();
        let bus = bus.clone();
        tokio::spawn(async move {
            while let Some(req) = rx.recv().await {
                match (&req.command, allow) {
                    (Command::Stage(StageCommand::SetEngagement { engagement }), true) => {
                        bus.set(Source::System, EngagementState { engagement: *engagement });
                        req.ack(Ack::Accepted);
                    }
                    _ => req.ack(Ack::rejected("the brain is off in this mode")),
                }
            }
        });
    }

    #[tokio::test]
    async fn ptt_from_idle_engages_and_waits_for_stt() {
        let (port, _) = mock_deepgram().await;
        let bus = Bus::default();
        bus.set(Source::System, EngagementState { engagement: Engagement::Idle });
        stage_stub(&bus, true);
        let mut cfg = DeepgramConfig::new("k");
        cfg.url = format!("ws://127.0.0.1:{port}/v1/listen");
        let stt = Stt::spawn(cfg, interactive(&bus));
        tokio::time::sleep(Duration::from_millis(50)).await;
        assert!(!stt.is_up(), "no socket while IDLE");
        let speaker = Speaker::spawn(Arc::new(Beep), Arc::new(NullSink::new(1.0)), 30.0);
        let voice = Voice::new(bus.clone(), stt, speaker, None, VoiceOptions::default());
        let ack = voice.ptt_start("panel").await;
        assert!(ack.is_accepted(), "{ack:?}");
        assert_eq!(bus.get::<EngagementState>().engagement, Engagement::Interactive);
        assert!(voice.stt().is_up());
        assert!(voice.ptt_stop(Some("panel")).await.is_accepted());
    }

    #[tokio::test]
    async fn ptt_refused_when_engaging_is_refused_or_stt_never_connects() {
        let bus = Bus::default();
        bus.set(Source::System, EngagementState { engagement: Engagement::Idle });
        stage_stub(&bus, false);
        let dead = TcpListener::bind("127.0.0.1:0").await.unwrap().local_addr().unwrap().port(); // closed again
        let mut cfg = DeepgramConfig::new("k");
        cfg.url = format!("ws://127.0.0.1:{dead}/v1/listen");
        let speaker = Speaker::spawn(Arc::new(Beep), Arc::new(NullSink::new(1.0)), 30.0);
        let opts = VoiceOptions { connect_wait: Duration::from_millis(300), ..Default::default() };
        let voice = Voice::new(bus.clone(), Stt::spawn(cfg, interactive(&bus)), speaker, None, opts);
        let ack = voice.ptt_start("panel").await;
        assert_eq!(ack, Ack::rejected("the brain is off in this mode"));

        bus.set(Source::System, EngagementState { engagement: Engagement::Interactive });
        let ack = voice.ptt_start("panel").await;
        assert!(!ack.is_accepted(), "STT down: refused after the bounded wait");
        assert!(voice.listening().await.is_none());
    }
}

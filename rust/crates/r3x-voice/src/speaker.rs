//! The speech FIFO (plan §7a Speech).
//!
//! - One FIFO, one line at a time, played through a [`SpeechSink`].
//! - `Started` fires when the line's **first sample is audible** (the sink's clock, device
//!   output latency included), not when synthesis begins.
//! - Character timings are rebased from per-chunk to line time (ms from the first sample) and
//!   carry the absolute `audio_t0`.
//! - Mouth amplitude: fixed 20 ms windows with the CantinaOS AGC rule, emitted at one rate
//!   (profile `audio.mouth_hz`) in step with playback.
//! - Duplicate texts within a conversation are dropped; a Claude-sourced line that is not the
//!   reply is dropped too (one voice per turn: the reply *is* the voice).
//! - [`Speaker::stop`] drops the current line (and its socket) and everything queued.
//! - `speaking` is true from `Started` to `Ended`, and released by *any* completion.

use std::collections::VecDeque;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use r3x_audio::amplitude::{Agc, MouthTrack};
use r3x_audio::sink::{Progress, SpeechSink};
use r3x_audio::TTS_RATE;
use r3x_contracts::Source;
use tokio::sync::{broadcast, mpsc, watch, Notify};
use tokio::time::MissedTickBehavior;

use crate::eleven::{open_speech, Alignment, TtsBackend};

#[derive(Debug, Clone, PartialEq)]
pub struct SpeechRequest {
    pub text: String,
    /// The turn this line belongs to (dup-drop key).
    pub conversation_id: Option<String>,
    /// Plan/show correlation ids, echoed on every event.
    pub clip_id: Option<String>,
    pub step_id: Option<String>,
    pub plan_id: Option<String>,
    pub source: Source,
    /// The turn's reply (as opposed to a show or plan line).
    pub reply: bool,
}

impl SpeechRequest {
    pub fn reply(text: impl Into<String>, conversation_id: Option<String>, source: Source) -> Self {
        Self { text: text.into(), conversation_id, clip_id: None, step_id: None, plan_id: None, source, reply: true }
    }
}

/// Character timing on the line's timeline: ms from its first audible sample.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct CharTimings {
    pub chars: Vec<String>,
    pub start_ms: Vec<f64>,
    pub duration_ms: Vec<f64>,
}

/// Rebase one chunk's alignment (relative to the chunk) onto the line: `offset_ms` is the
/// audio already produced before this chunk.
pub fn rebase(a: &Alignment, offset_ms: f64) -> CharTimings {
    CharTimings {
        chars: a.chars.clone(),
        start_ms: a.start_ms.iter().map(|t| offset_ms + t).collect(),
        duration_ms: a.duration_ms.clone(),
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct AudioT0 {
    pub at: Instant,
    /// Unix seconds.
    pub wall: f64,
}

impl AudioT0 {
    fn from_instant(at: Instant) -> Self {
        let now_i = Instant::now();
        let now_w = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0);
        let wall = if at >= now_i { now_w + (at - now_i).as_secs_f64() } else { now_w - (now_i - at).as_secs_f64() };
        Self { at, wall }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum SpeechEvent {
    Started { req: Arc<SpeechRequest>, t0: AudioT0 },
    Mouth { req: Arc<SpeechRequest>, level: f32 },
    Timing { req: Arc<SpeechRequest>, timings: CharTimings, t0: AudioT0 },
    /// Every accepted line ends exactly once. `error` is `Some` on failure or stop.
    Ended { req: Arc<SpeechRequest>, audio_s: f64, error: Option<String> },
    /// Never queued (duplicate, or a second voice in the turn).
    Dropped { req: Arc<SpeechRequest>, reason: &'static str },
}

#[derive(Default)]
struct Dedup {
    order: VecDeque<String>,
    seen: std::collections::HashMap<String, Vec<String>>,
}

impl Dedup {
    /// True if new (and records it).
    fn admit(&mut self, conv: &str, text: &str) -> bool {
        let text = text.trim().to_owned();
        let entry = self.seen.entry(conv.to_owned()).or_insert_with(|| {
            self.order.push_back(conv.to_owned());
            Vec::new()
        });
        if entry.contains(&text) {
            return false;
        }
        entry.push(text);
        while self.order.len() > 64 {
            if let Some(old) = self.order.pop_front() {
                self.seen.remove(&old);
            }
        }
        true
    }
}

/// Handle to the FIFO. Cheap to clone.
#[derive(Clone)]
pub struct Speaker {
    tx: mpsc::UnboundedSender<(u64, Arc<SpeechRequest>)>,
    gen: Arc<AtomicU64>,
    stop: Arc<Notify>,
    sink: Arc<dyn SpeechSink>,
    dedup: Arc<Mutex<Dedup>>,
    events: broadcast::Sender<SpeechEvent>,
    speaking: watch::Receiver<bool>,
}

impl Speaker {
    pub fn spawn(backend: Arc<dyn TtsBackend>, sink: Arc<dyn SpeechSink>, mouth_hz: f64) -> Self {
        let (tx, rx) = mpsc::unbounded_channel();
        let events = broadcast::channel(1024).0;
        let (speaking_tx, speaking) = watch::channel(false);
        let s = Self {
            tx,
            gen: Arc::default(),
            stop: Arc::new(Notify::new()),
            sink,
            dedup: Arc::default(),
            events,
            speaking,
        };
        tokio::spawn(worker(s.clone(), backend, rx, speaking_tx, mouth_hz));
        s
    }

    pub fn subscribe(&self) -> broadcast::Receiver<SpeechEvent> {
        self.events.subscribe()
    }

    pub fn speaking(&self) -> watch::Receiver<bool> {
        self.speaking.clone()
    }

    pub fn is_speaking(&self) -> bool {
        *self.speaking.borrow()
    }

    /// Queue a line. Returns false (and emits `Dropped`) if it was dropped.
    pub fn say(&self, req: SpeechRequest) -> bool {
        let req = Arc::new(req);
        let reason = if req.source == Source::Claude && !req.reply {
            Some("the reply is this turn's voice")
        } else if let Some(c) = &req.conversation_id {
            (!self.dedup.lock().unwrap().admit(c, &req.text)).then_some("duplicate text in this conversation")
        } else {
            None
        };
        if let Some(reason) = reason {
            tracing::info!(text = %req.text, reason, "speech dropped");
            let _ = self.events.send(SpeechEvent::Dropped { req, reason });
            return false;
        }
        self.tx.send((self.gen.load(Ordering::SeqCst), req)).is_ok()
    }

    /// Stop now: drop the line playing and everything queued.
    pub fn stop(&self) {
        self.gen.fetch_add(1, Ordering::SeqCst);
        self.sink.abort();
        self.stop.notify_waiters();
    }

    fn emit(&self, e: SpeechEvent) {
        let _ = self.events.send(e);
    }
}

async fn worker(
    sp: Speaker,
    backend: Arc<dyn TtsBackend>,
    mut rx: mpsc::UnboundedReceiver<(u64, Arc<SpeechRequest>)>,
    speaking: watch::Sender<bool>,
    mouth_hz: f64,
) {
    while let Some((gen, req)) = rx.recv().await {
        if gen != sp.gen.load(Ordering::SeqCst) {
            sp.emit(SpeechEvent::Ended { req, audio_s: 0.0, error: Some("stopped".into()) });
            continue;
        }
        let (audio_s, error) = speak(&sp, backend.as_ref(), &req, gen, &speaking, mouth_hz).await;
        let _ = speaking.send(false);
        sp.emit(SpeechEvent::Ended { req, audio_s, error });
    }
}

struct LineState {
    track: MouthTrack,
    t0: Option<AudioT0>,
    pending: Vec<CharTimings>,
}

/// Play one line; returns (seconds of audio, error).
async fn speak(
    sp: &Speaker,
    backend: &dyn TtsBackend,
    req: &Arc<SpeechRequest>,
    gen: u64,
    speaking: &watch::Sender<bool>,
    mouth_hz: f64,
) -> (f64, Option<String>) {
    if req.text.trim().is_empty() {
        return (0.0, None);
    }
    let stopped = || sp.gen.load(Ordering::SeqCst) != gen;
    let opened = tokio::select! {
        r = open_speech(backend, &req.text) => r,
        _ = sp.stop.notified() => return (0.0, Some("stopped".into())),
    };
    let (first, mut rest) = match opened {
        Ok(x) => x,
        Err(e) => return (0.0, Some(e.to_string())),
    };
    if stopped() {
        return (0.0, Some("stopped".into()));
    }
    let Some(first) = first else { return (0.0, None) };

    let line = sp.sink.open_line();
    let state = Arc::new(Mutex::new(LineState { track: MouthTrack::new(mouth_hz), t0: None, pending: Vec::new() }));
    let follower = tokio::spawn(follow(sp.clone(), req.clone(), gen, line.progress, state.clone(), speaking.clone()));

    let mut agc = Agc::new(TTS_RATE);
    let mut windows = Vec::new();
    let mut samples: u64 = 0;
    let mut error = None;
    let mut chunk = Some(Ok(first));
    loop {
        let c = match chunk.take() {
            Some(c) => c,
            None => {
                let next = tokio::select! {
                    n = rest.recv() => n,
                    _ = sp.stop.notified() => { error = Some("stopped".into()); break }
                };
                match next {
                    Some(c) => c,
                    None => break,
                }
            }
        };
        let c = match c {
            Ok(c) => c,
            Err(e) => {
                // After audio: report, never replay. What was queued still plays out.
                tracing::warn!(error = %e, "speech failed mid-line");
                error = Some(e.to_string());
                break;
            }
        };
        windows.clear();
        agc.push(&c.pcm, &mut windows);
        {
            let mut st = state.lock().unwrap();
            st.track.extend(&windows);
            if let Some(a) = &c.alignment {
                let timings = rebase(a, samples as f64 * 1000.0 / TTS_RATE as f64);
                match st.t0 {
                    Some(t0) => sp.emit(SpeechEvent::Timing { req: req.clone(), timings, t0 }),
                    None => st.pending.push(timings),
                }
            }
        }
        samples += c.pcm.len() as u64;
        if line.pcm.send(c.pcm).await.is_err() {
            break;
        }
    }
    windows.clear();
    agc.flush(&mut windows);
    state.lock().unwrap().track.extend(&windows);
    drop(rest); // stops the socket turn if we broke out early
    drop(line.pcm);
    let aborted = follower.await.unwrap_or(true);
    if aborted && error.is_none() {
        error = Some("stopped".into());
    }
    (samples as f64 / TTS_RATE as f64, error)
}

/// Follow playback: `Started` at the first audible sample, mouth at the profile rate,
/// timings once `audio_t0` is known. Returns true if the line was aborted.
async fn follow(
    sp: Speaker,
    req: Arc<SpeechRequest>,
    gen: u64,
    mut progress: mpsc::UnboundedReceiver<Progress>,
    state: Arc<Mutex<LineState>>,
    speaking: watch::Sender<bool>,
) -> bool {
    let period = state.lock().unwrap().track.period_s();
    let mut tick = tokio::time::interval(Duration::from_secs_f64(period));
    tick.set_missed_tick_behavior(MissedTickBehavior::Skip);
    // (sample, when heard) of the latest playback report.
    let mut map: Option<(u64, Instant)> = None;
    let mut last_k: Option<u64> = None;
    let mut finish: Option<Instant> = None;
    loop {
        tokio::select! {
            p = progress.recv(), if finish.is_none() => match p {
                None | Some(Progress::Aborted) => return true,
                Some(Progress::Audible { at }) => {
                    tokio::time::sleep_until(at.into()).await;
                    let t0 = AudioT0::from_instant(at);
                    let _ = speaking.send(true);
                    sp.emit(SpeechEvent::Started { req: req.clone(), t0 });
                    let pending = {
                        let mut st = state.lock().unwrap();
                        st.t0 = Some(t0);
                        std::mem::take(&mut st.pending)
                    };
                    for timings in pending {
                        sp.emit(SpeechEvent::Timing { req: req.clone(), timings, t0 });
                    }
                    map = Some((0, at));
                }
                Some(Progress::Played { samples, at }) => map = Some((samples, at)),
                // Keep ticking the mouth until the last sample has been heard.
                Some(Progress::Finished { at }) => finish = Some(at),
            },
            _ = async { tokio::time::sleep_until(finish.unwrap_or_else(Instant::now).into()).await }, if finish.is_some() => {
                return false;
            }
            _ = sp.stop.notified() => return true,
            _ = tick.tick(), if map.is_some() => {
                if sp.gen.load(Ordering::SeqCst) != gen {
                    return true;
                }
                let (s, at) = map.expect("guarded");
                let now = Instant::now();
                let pos = s as f64 / TTS_RATE as f64 + if now >= at { (now - at).as_secs_f64() } else { -(at - now).as_secs_f64() };
                if pos < 0.0 {
                    continue;
                }
                let k = (pos / period).floor() as u64;
                if last_k.is_some_and(|l| l >= k) {
                    continue;
                }
                last_k = Some(k);
                if let Some(level) = state.lock().unwrap().track.tick(k) {
                    sp.emit(SpeechEvent::Mouth { req: req.clone(), level });
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::eleven::{ChunkRx, TtsChunk};
    use r3x_audio::sink::NullSink;

    /// Emits `n` chunks of 100 ms tone per line, with one alignment char per chunk.
    struct Tone {
        chunks: usize,
        fail_after: Option<usize>,
    }

    impl TtsBackend for Tone {
        fn dialogue(&self, text: &str) -> Option<ChunkRx> {
            let (tx, rx) = mpsc::channel(64);
            let (n, fail) = (self.chunks, self.fail_after);
            let ch = text.chars().next().unwrap_or('?').to_string();
            tokio::spawn(async move {
                for i in 0..n {
                    if fail == Some(i) {
                        let _ = tx.send(Err(anyhow::anyhow!("socket died"))).await;
                        return;
                    }
                    let pcm: Vec<i16> = (0..2400).map(|j| ((j as f32 * 0.2).sin() * 8000.0) as i16).collect();
                    let a = Alignment { chars: vec![ch.clone()], start_ms: vec![10.0], duration_ms: vec![50.0] };
                    if tx.send(Ok(TtsChunk { pcm, alignment: Some(a) })).await.is_err() {
                        return;
                    }
                }
            });
            Some(rx)
        }
        fn http(&self, _: &str) -> ChunkRx {
            let (tx, rx) = mpsc::channel(1);
            let _ = tx.try_send(Err(anyhow::anyhow!("no http in tests")));
            rx
        }
    }

    fn speaker(chunks: usize, fail_after: Option<usize>) -> Speaker {
        Speaker::spawn(Arc::new(Tone { chunks, fail_after }), Arc::new(NullSink::new(1.0)), 30.0)
    }

    async fn until_ended(rx: &mut broadcast::Receiver<SpeechEvent>, n: usize) -> Vec<SpeechEvent> {
        let mut v = Vec::new();
        let mut ended = 0;
        while ended < n {
            let e = rx.recv().await.unwrap();
            if matches!(e, SpeechEvent::Ended { .. } | SpeechEvent::Dropped { .. }) {
                ended += 1;
            }
            v.push(e);
        }
        v
    }

    #[test]
    fn rebase_adds_chunk_offset() {
        let a = Alignment { chars: vec!["a".into(), "b".into()], start_ms: vec![0.0, 40.0], duration_ms: vec![40.0, 40.0] };
        assert_eq!(rebase(&a, 1500.0).start_ms, vec![1500.0, 1540.0]);
    }

    #[tokio::test]
    async fn fifo_order_dup_drop_and_one_voice() {
        let sp = speaker(2, None);
        let mut rx = sp.subscribe();
        let c = Some("turn".to_string());
        assert!(sp.say(SpeechRequest::reply("A reply", c.clone(), Source::Claude)));
        assert!(!sp.say(SpeechRequest::reply("A reply ", c.clone(), Source::Claude)), "duplicate in the same turn");
        let show_line = SpeechRequest { reply: false, ..SpeechRequest::reply("Make some noise!", c.clone(), Source::Claude) };
        assert!(!sp.say(show_line), "a Claude show must not talk over the reply");
        assert!(sp.say(SpeechRequest::reply("B second", Some("other".into()), Source::Claude)));
        let ev = until_ended(&mut rx, 4).await;
        let order: Vec<String> = ev
            .iter()
            .filter_map(|e| match e {
                SpeechEvent::Started { req, .. } => Some(format!("start {}", req.text)),
                SpeechEvent::Ended { req, error, .. } => Some(format!("end {} {:?}", req.text, error)),
                SpeechEvent::Dropped { req, .. } => Some(format!("drop {}", req.text.trim())),
                _ => None,
            })
            .collect();
        assert_eq!(order, ["drop A reply", "drop Make some noise!", "start A reply", "end A reply None", "start B second", "end B second None"]);
        assert!(!sp.is_speaking());
    }

    #[tokio::test]
    async fn started_at_first_audible_sample_with_rebased_timings_and_mouth_rate() {
        let sp = speaker(5, None); // 500 ms
        let mut rx = sp.subscribe();
        let mut speaking = sp.speaking();
        sp.say(SpeechRequest::reply("hello", Some("c".into()), Source::Claude));
        let ev = until_ended(&mut rx, 1).await;
        let SpeechEvent::Started { t0, .. } = ev.iter().find(|e| matches!(e, SpeechEvent::Started { .. })).unwrap() else { unreachable!() };
        let idx_started = ev.iter().position(|e| matches!(e, SpeechEvent::Started { .. })).unwrap();
        let timings: Vec<f64> = ev.iter().filter_map(|e| match e { SpeechEvent::Timing { timings, .. } => Some(timings.start_ms[0]), _ => None }).collect();
        assert_eq!(timings, vec![10.0, 110.0, 210.0, 310.0, 410.0], "chunk-relative times rebased onto the line");
        assert!(ev.iter().position(|e| matches!(e, SpeechEvent::Timing { .. })).unwrap() > idx_started, "timings follow Started");
        let mouths = ev.iter().filter(|e| matches!(e, SpeechEvent::Mouth { .. })).count();
        assert!((13..=17).contains(&mouths), "≈30 Hz over 0.5 s, got {mouths}");
        let SpeechEvent::Ended { audio_s, error, .. } = ev.last().unwrap() else { panic!() };
        assert_eq!((*audio_s, error.clone()), (0.5, None));
        assert!(t0.at.elapsed() >= Duration::from_millis(480));
        assert!(!*speaking.borrow_and_update());
    }

    #[tokio::test]
    async fn failure_after_audio_is_an_error_and_stop_releases() {
        let sp = speaker(3, Some(1));
        let mut rx = sp.subscribe();
        sp.say(SpeechRequest::reply("x", None, Source::Claude));
        let ev = until_ended(&mut rx, 1).await;
        assert!(ev.iter().any(|e| matches!(e, SpeechEvent::Started { .. })), "the audio already produced still plays");
        let SpeechEvent::Ended { error, audio_s, .. } = ev.last().unwrap() else { panic!() };
        assert!(error.as_deref().unwrap().contains("socket died"));
        assert_eq!(*audio_s, 0.1, "no replay from the start");

        let sp = speaker(50, None); // 5 s line
        let mut rx = sp.subscribe();
        sp.say(SpeechRequest::reply("long", None, Source::Claude));
        sp.say(SpeechRequest::reply("queued", None, Source::Claude));
        let mut speaking = sp.speaking();
        speaking.wait_for(|s| *s).await.unwrap();
        sp.stop();
        let ev = until_ended(&mut rx, 2).await;
        let ends: Vec<_> = ev.iter().filter_map(|e| match e { SpeechEvent::Ended { req, error, .. } => Some((req.text.clone(), error.clone())), _ => None }).collect();
        assert_eq!(ends, vec![("long".into(), Some("stopped".into())), ("queued".into(), Some("stopped".into()))]);
        assert!(!sp.is_speaking(), "released on any completion");
    }
}

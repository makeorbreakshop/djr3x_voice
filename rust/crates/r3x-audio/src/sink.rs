//! Speech sinks: where a spoken line's 24 kHz PCM goes, and when each part of it is heard.
//!
//! A sink plays one line at a time (the voice layer is a FIFO). [`SpeechSink::open_line`]
//! returns a PCM sender (drop it to end the line) and a [`Progress`] stream. `Progress` times
//! are *when the sample is audible*, so "speaking" can fire at the first audible sample and
//! character timings can be rebased onto it (plan §7a Speech, §7b `audio_t0`).

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use tokio::sync::{broadcast, mpsc};
use tokio::time::MissedTickBehavior;

use crate::mixer::{BusId, MixerHandle, RenderTime, Source};
use crate::resample::Resampler;
use crate::{i16_to_f32, TTS_RATE};

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Progress {
    /// The line's first sample is heard at `at`.
    Audible { at: Instant },
    /// Sample `samples` (24 kHz, from the line's start) is heard at `at`. Monotonic.
    Played { samples: u64, at: Instant },
    /// The last sample finishes at `at`.
    Finished { at: Instant },
    /// Dropped by [`SpeechSink::abort`].
    Aborted,
}

pub struct Line {
    /// 24 kHz mono PCM. Drop to end the line.
    pub pcm: mpsc::Sender<Vec<i16>>,
    pub progress: mpsc::UnboundedReceiver<Progress>,
}

pub trait SpeechSink: Send + Sync + 'static {
    fn open_line(&self) -> Line;
    /// Drop everything queued or playing (stop mid-turn).
    fn abort(&self);
}

fn line_channels() -> (Line, mpsc::Receiver<Vec<i16>>, mpsc::UnboundedSender<Progress>) {
    let (pcm, rx) = mpsc::channel(64);
    let (ptx, progress) = mpsc::unbounded_channel();
    (Line { pcm, progress }, rx, ptx)
}

/// Tokio's clock as a std `Instant` (follows paused time in tests).
fn now() -> Instant {
    tokio::time::Instant::now().into_std()
}

fn offset(t: Instant, secs: f64) -> Instant {
    if secs >= 0.0 {
        t + Duration::from_secs_f64(secs)
    } else {
        t.checked_sub(Duration::from_secs_f64(-secs)).unwrap_or(t)
    }
}

// ------------------------------------------------------------------------------------ local

/// Callback-written playback clock (seqlock: the callback never waits).
#[derive(Default)]
struct SeqClock {
    version: AtomicU64,
    c0: AtomicU64,
    at_ns: AtomicU64,
    latency_ns: AtomicU64,
}

impl SeqClock {
    fn write(&self, c0: u64, at_ns: u64, latency_ns: u64) {
        self.version.fetch_add(1, Ordering::SeqCst);
        self.c0.store(c0, Ordering::SeqCst);
        self.at_ns.store(at_ns, Ordering::SeqCst);
        self.latency_ns.store(latency_ns, Ordering::SeqCst);
        self.version.fetch_add(1, Ordering::SeqCst);
    }

    fn read(&self) -> Option<(u64, u64, u64)> {
        for _ in 0..8 {
            let v1 = self.version.load(Ordering::SeqCst);
            if v1 == 0 {
                return None;
            }
            if v1 % 2 == 1 {
                std::hint::spin_loop();
                continue;
            }
            let r = (self.c0.load(Ordering::SeqCst), self.at_ns.load(Ordering::SeqCst), self.latency_ns.load(Ordering::SeqCst));
            if self.version.load(Ordering::SeqCst) == v1 {
                return Some(r);
            }
        }
        None
    }
}

struct Shared {
    epoch: Instant,
    /// Frames (device rate) taken out of the ring, flushed ones included.
    consumed: AtomicU64,
    /// Frames put into the ring.
    pushed: AtomicU64,
    flush: AtomicBool,
    abort_gen: AtomicU64,
    clock: SeqClock,
    rate: u32,
}

impl Shared {
    /// When device-rate frame `idx` is (or was) heard, from the last callback's clock.
    fn time_of(&self, idx: u64) -> Option<Instant> {
        let (c0, at_ns, lat_ns) = self.clock.read()?;
        let t = self.epoch + Duration::from_nanos(at_ns + lat_ns);
        Some(offset(t, (idx as f64 - c0 as f64) / self.rate as f64))
    }
}

/// The speech bus's source: drains the ring, stamps the playback clock.
struct SpeechSource {
    rx: rtrb::Consumer<f32>,
    shared: Arc<Shared>,
}

impl Source for SpeechSource {
    fn mix(&mut self, out: &mut [f32], ch: usize, when: &RenderTime) -> bool {
        let sh = &self.shared;
        if sh.flush.swap(false, Ordering::SeqCst) {
            let n = self.rx.slots();
            if let Ok(c) = self.rx.read_chunk(n) {
                c.commit_all();
            }
            sh.consumed.fetch_add(n as u64, Ordering::SeqCst);
        }
        let c0 = sh.consumed.load(Ordering::SeqCst);
        let at_ns = when.at.saturating_duration_since(sh.epoch).as_nanos() as u64;
        sh.clock.write(c0, at_ns, when.latency.as_nanos() as u64);
        let frames = (out.len() / ch).min(self.rx.slots());
        if let Ok(chunk) = self.rx.read_chunk(frames) {
            let (a, b) = chunk.as_slices();
            for (frame, &s) in out.chunks_mut(ch).zip(a.iter().chain(b)) {
                frame.iter_mut().for_each(|o| *o += s);
            }
            chunk.commit_all();
        }
        sh.consumed.fetch_add(frames as u64, Ordering::SeqCst);
        true
    }
}

/// Plays speech on the mixer's speech bus (a local device).
#[derive(Clone)]
pub struct LocalSink {
    shared: Arc<Shared>,
    tx: Arc<tokio::sync::Mutex<rtrb::Producer<f32>>>,
}

impl LocalSink {
    /// Attach a speech source to `mixer`'s speech bus.
    pub fn new(mixer: &MixerHandle) -> Self {
        let rate = mixer.sample_rate();
        let (tx, rx) = rtrb::RingBuffer::new(rate as usize * 30);
        let shared = Arc::new(Shared {
            epoch: Instant::now(),
            consumed: AtomicU64::new(0),
            pushed: AtomicU64::new(0),
            flush: AtomicBool::new(false),
            abort_gen: AtomicU64::new(0),
            clock: SeqClock::default(),
            rate,
        });
        mixer.add(BusId::Speech, Box::new(SpeechSource { rx, shared: shared.clone() }));
        Self { shared, tx: Arc::new(tokio::sync::Mutex::new(tx)) }
    }
}

impl SpeechSink for LocalSink {
    fn open_line(&self) -> Line {
        let (line, mut rx, progress) = line_channels();
        let sh = self.shared.clone();
        let tx = self.tx.clone();
        tokio::spawn(async move {
            // One line at a time: the FIFO holds this lock until the line is done.
            let mut ring = tx.lock().await;
            let gen = sh.abort_gen.load(Ordering::SeqCst);
            let start = sh.pushed.load(Ordering::SeqCst);
            let mut rs = Resampler::new(TTS_RATE, sh.rate);
            let mut pending: Vec<f32> = Vec::new();
            let mut ended = false;
            let mut audible = false;
            let mut last_c0 = u64::MAX;
            let mut tick = tokio::time::interval(Duration::from_millis(5));
            tick.set_missed_tick_behavior(MissedTickBehavior::Skip);
            loop {
                tokio::select! {
                    pcm = rx.recv(), if !ended && pending.is_empty() => match pcm {
                        Some(v) => {
                            let f: Vec<f32> = v.iter().map(|&s| i16_to_f32(s)).collect();
                            rs.process(&f, &mut pending);
                        }
                        None => ended = true,
                    },
                    _ = tick.tick() => {}
                }
                if sh.abort_gen.load(Ordering::SeqCst) != gen {
                    sh.flush.store(true, Ordering::SeqCst);
                    let _ = progress.send(Progress::Aborted);
                    return;
                }
                if !pending.is_empty() {
                    let (done, _) = ring.push_partial_slice(&pending);
                    let n = done.len();
                    pending.drain(..n);
                    sh.pushed.fetch_add(n as u64, Ordering::SeqCst);
                }
                let consumed = sh.consumed.load(Ordering::SeqCst);
                let end = sh.pushed.load(Ordering::SeqCst);
                if ended && pending.is_empty() && end == start {
                    let _ = progress.send(Progress::Finished { at: Instant::now() });
                    return;
                }
                if !audible && consumed > start {
                    if let Some(at) = sh.time_of(start) {
                        audible = true;
                        let _ = progress.send(Progress::Audible { at });
                    }
                }
                if let Some((c0, _, _)) = sh.clock.read() {
                    if audible && c0 != last_c0 && c0 >= start {
                        last_c0 = c0;
                        if let Some(at) = sh.time_of(c0) {
                            let samples = (c0.min(end) - start) * TTS_RATE as u64 / sh.rate as u64;
                            let _ = progress.send(Progress::Played { samples, at });
                        }
                    }
                }
                if ended && pending.is_empty() && consumed >= end {
                    let at = sh.time_of(end).unwrap_or_else(Instant::now);
                    let _ = progress.send(Progress::Finished { at });
                    return;
                }
            }
        });
        line
    }

    fn abort(&self) {
        self.shared.abort_gen.fetch_add(1, Ordering::SeqCst);
        self.shared.flush.store(true, Ordering::SeqCst);
    }
}

// ----------------------------------------------------------------------------------- remote

/// Runtime -> client speech audio (plan §3b): 24 kHz mono PCM in 20 ms frames.
#[derive(Debug, Clone, PartialEq)]
pub enum RemoteAudio {
    Pcm { line: u64, samples: Vec<i16> },
    LineEnd { line: u64 },
    /// Stop playback now and drop anything buffered.
    Abort,
}

/// Paces a line to gateway clients in real time, `client_buffer` ahead of playback.
///
/// The client is assumed to start each line `client_buffer` after its first frame arrives
/// and play continuously; `Progress` times follow that model (underruns re-anchor it).
#[derive(Clone)]
pub struct RemoteSink {
    tx: broadcast::Sender<RemoteAudio>,
    client_buffer: Duration,
    lines: Arc<AtomicU64>,
    abort_gen: Arc<AtomicU64>,
    busy: Arc<tokio::sync::Mutex<()>>,
}

impl RemoteSink {
    pub fn new(client_buffer: Duration) -> Self {
        Self {
            tx: broadcast::channel(1024).0,
            client_buffer,
            lines: Arc::default(),
            abort_gen: Arc::default(),
            busy: Arc::default(),
        }
    }

    /// Frames for the gateway to forward (as `audio` meta + binary) to listening clients.
    pub fn subscribe(&self) -> broadcast::Receiver<RemoteAudio> {
        self.tx.subscribe()
    }
}

impl Default for RemoteSink {
    fn default() -> Self {
        Self::new(Duration::from_millis(150))
    }
}

const FRAME: usize = (TTS_RATE / 50) as usize; // 20 ms

impl SpeechSink for RemoteSink {
    fn open_line(&self) -> Line {
        let (line, mut rx, progress) = line_channels();
        let me = self.clone();
        let id = self.lines.fetch_add(1, Ordering::SeqCst) + 1;
        tokio::spawn(async move {
            let _one_at_a_time = me.busy.lock().await;
            let gen = me.abort_gen.load(Ordering::SeqCst);
            let mut buf: Vec<i16> = Vec::new();
            let mut ended = false;
            let mut sent: u64 = 0;
            let mut next_play: Option<Instant> = None;
            loop {
                while !ended && buf.len() < FRAME {
                    match rx.recv().await {
                        Some(v) => buf.extend_from_slice(&v),
                        None => ended = true,
                    }
                }
                if me.abort_gen.load(Ordering::SeqCst) != gen {
                    let _ = progress.send(Progress::Aborted);
                    return;
                }
                if buf.is_empty() {
                    let _ = me.tx.send(RemoteAudio::LineEnd { line: id });
                    let at = next_play.unwrap_or_else(Instant::now);
                    let _ = progress.send(Progress::Finished { at });
                    return;
                }
                let n = buf.len().min(FRAME);
                let now = now();
                let play_at = next_play.map_or(now + me.client_buffer, |t| t.max(now + me.client_buffer / 2));
                // Keep `client_buffer` of audio in flight, no more.
                let send_at = play_at.checked_sub(me.client_buffer).unwrap_or(now);
                if send_at > now {
                    tokio::time::sleep_until(send_at.into()).await;
                    if me.abort_gen.load(Ordering::SeqCst) != gen {
                        let _ = progress.send(Progress::Aborted);
                        return;
                    }
                }
                let samples: Vec<i16> = buf.drain(..n).collect();
                let _ = me.tx.send(RemoteAudio::Pcm { line: id, samples });
                if sent == 0 {
                    let _ = progress.send(Progress::Audible { at: play_at });
                }
                let _ = progress.send(Progress::Played { samples: sent, at: play_at });
                sent += n as u64;
                next_play = Some(play_at + Duration::from_secs_f64(n as f64 / TTS_RATE as f64));
            }
        });
        line
    }

    fn abort(&self) {
        self.abort_gen.fetch_add(1, Ordering::SeqCst);
        let _ = self.tx.send(RemoteAudio::Abort);
    }
}

// -------------------------------------------------------------------------------- tee, null

/// Plays every line on `primary` (which owns timing) and mirrors it to `mirrors`, e.g. the
/// room speaker plus a remote push-to-talk client.
pub struct TeeSink {
    primary: Box<dyn SpeechSink>,
    mirrors: Mutex<Vec<Box<dyn SpeechSink>>>,
}

impl TeeSink {
    pub fn new(primary: Box<dyn SpeechSink>, mirrors: Vec<Box<dyn SpeechSink>>) -> Self {
        Self { primary, mirrors: Mutex::new(mirrors) }
    }
}

impl SpeechSink for TeeSink {
    fn open_line(&self) -> Line {
        let main = self.primary.open_line();
        let mirrors: Vec<_> = self.mirrors.lock().unwrap().iter().map(|m| m.open_line().pcm).collect();
        let (pcm, mut rx) = mpsc::channel::<Vec<i16>>(64);
        let out = main.pcm;
        tokio::spawn(async move {
            while let Some(v) = rx.recv().await {
                for m in &mirrors {
                    let _ = m.try_send(v.clone());
                }
                if out.send(v).await.is_err() {
                    break;
                }
            }
        });
        Line { pcm, progress: main.progress }
    }

    fn abort(&self) {
        self.primary.abort();
        self.mirrors.lock().unwrap().iter().for_each(|m| m.abort());
    }
}

/// Discards audio and reports it as played in real time from when it arrives (headless and
/// tests). `speed` > 1 plays faster than real time.
#[derive(Clone)]
pub struct NullSink {
    speed: f64,
    abort_gen: Arc<AtomicU64>,
}

impl NullSink {
    pub fn new(speed: f64) -> Self {
        Self { speed, abort_gen: Arc::default() }
    }
}

impl SpeechSink for NullSink {
    fn open_line(&self) -> Line {
        let (line, mut rx, progress) = line_channels();
        let speed = self.speed;
        let gen_now = self.abort_gen.load(Ordering::SeqCst);
        let gen = self.abort_gen.clone();
        tokio::spawn(async move {
            let mut t: Option<Instant> = None;
            let mut sent = 0u64;
            while let Some(v) = rx.recv().await {
                if gen.load(Ordering::SeqCst) != gen_now {
                    let _ = progress.send(Progress::Aborted);
                    return;
                }
                let at = *t.get_or_insert_with(|| {
                    let at = now();
                    let _ = progress.send(Progress::Audible { at });
                    at
                });
                let _ = progress.send(Progress::Played { samples: sent, at: offset(at, sent as f64 / TTS_RATE as f64 / speed) });
                sent += v.len() as u64;
            }
            if gen.load(Ordering::SeqCst) != gen_now {
                let _ = progress.send(Progress::Aborted);
                return;
            }
            let end = t.map_or_else(Instant::now, |at| offset(at, sent as f64 / TTS_RATE as f64 / speed));
            let _ = progress.send(Progress::Finished { at: end });
        });
        line
    }

    fn abort(&self) {
        self.abort_gen.fetch_add(1, Ordering::SeqCst);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::mixer::mixer;

    async fn collect(mut p: mpsc::UnboundedReceiver<Progress>) -> Vec<Progress> {
        let mut v = Vec::new();
        while let Some(x) = p.recv().await {
            let done = matches!(x, Progress::Finished { .. } | Progress::Aborted);
            v.push(x);
            if done {
                break;
            }
        }
        v
    }

    /// Drive the mixer like a device: 10 ms buffers with a fixed 30 ms output latency.
    #[tokio::test]
    async fn local_sink_times_include_output_latency() {
        let (mut m, h) = mixer(48_000, 2);
        let sink = LocalSink::new(&h);
        let latency = Duration::from_millis(30);
        let dev = tokio::spawn(async move {
            let mut out = vec![0.0f32; 960];
            let mut peak = 0.0f32;
            let mut tick = tokio::time::interval(Duration::from_millis(10));
            for _ in 0..60 {
                tick.tick().await;
                let when = RenderTime { at: Instant::now(), latency };
                m.render(&mut out, &when);
                peak = out.iter().fold(peak, |a, &b| a.max(b));
            }
            peak
        });
        let line = sink.open_line();
        let opened = Instant::now();
        line.pcm.send(vec![16384; 4800]).await.unwrap(); // 200 ms at 24 kHz
        drop(line.pcm);
        let p = collect(line.progress).await;
        let Progress::Audible { at: a } = p[0] else { panic!("{p:?}") };
        let Some(Progress::Finished { at: f }) = p.last().copied() else { panic!("{p:?}") };
        assert!(a >= opened + latency - Duration::from_millis(1), "latency added to audio_t0");
        let dur = f.duration_since(a).as_secs_f64();
        assert!((dur - 0.2).abs() < 0.02, "line lasts 200 ms of device time: {dur}");
        assert!(p.iter().any(|x| matches!(x, Progress::Played { samples, .. } if *samples > 0)));
        assert!((dev.await.unwrap() - 0.5).abs() < 0.01, "audio reached the device");
    }

    #[tokio::test]
    async fn local_sink_abort_flushes() {
        let (mut m, h) = mixer(24_000, 1);
        let sink = LocalSink::new(&h);
        let line = sink.open_line();
        line.pcm.send(vec![1000; 24_000]).await.unwrap();
        tokio::time::sleep(Duration::from_millis(20)).await;
        sink.abort();
        let p = collect(line.progress).await;
        assert_eq!(p.last(), Some(&Progress::Aborted));
        let mut out = vec![0.0; 480];
        m.render(&mut out, &RenderTime::now());
        assert!(out.iter().all(|&s| s == 0.0), "queued speech dropped");
    }

    #[tokio::test(start_paused = true)]
    async fn remote_sink_paces_frames() {
        let sink = RemoteSink::new(Duration::from_millis(100));
        let mut rx = sink.subscribe();
        let line = sink.open_line();
        let t0 = now();
        line.pcm.send(vec![7; 24_000]).await.unwrap(); // 1 s
        drop(line.pcm);
        let p = collect(line.progress).await;
        let mut frames = 0;
        while let Ok(RemoteAudio::Pcm { samples, .. }) = rx.try_recv() {
            assert_eq!(samples.len(), FRAME);
            frames += 1;
        }
        assert_eq!(frames, 50);
        let Progress::Audible { at: a } = p[0] else { panic!() };
        let Some(Progress::Finished { at: f }) = p.last().copied() else { panic!() };
        assert_eq!(a.duration_since(t0), Duration::from_millis(100));
        assert_eq!(f.duration_since(a), Duration::from_secs(1));
        // Paced: the last frame went out ~client_buffer before it plays, not all at once.
        assert!(now().duration_since(t0) >= Duration::from_millis(880));
    }
}

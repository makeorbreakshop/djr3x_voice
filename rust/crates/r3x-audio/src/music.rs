//! Streaming music playback on the mixer's music bus.
//!
//! [`MusicStream::open`] decodes on its own thread into a lock-free ring (about 1.5 s ahead),
//! resampled to the device rate; the returned [`MusicSource`] is handed to the mixer (plain
//! add, or [`crate::mixer::MixerHandle::crossfade`]). The position is counted in frames the
//! mixer actually took, so `TRACK_ENDING_SOON` can be derived from it (plan §7b) instead of a
//! wall-clock sleep.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use anyhow::{anyhow, Result};

use crate::decode::{interleave_for_device, FileInfo, PlanarStream};
use crate::mixer::{RenderTime, Source};

/// How far ahead the decoder runs.
const RING_SECS: f64 = 1.5;
/// Buffered before [`MusicStream::open`] returns, so a crossfade starts on real audio.
const PREFILL_SECS: f64 = 0.25;
const PREFILL_TIMEOUT: Duration = Duration::from_secs(3);

#[derive(Default)]
struct Shared {
    /// Device frames the mixer has taken (the playback position).
    played: AtomicU64,
    /// Device frames the decoder has produced.
    decoded: AtomicU64,
    eof: AtomicBool,
    finished: AtomicBool,
    paused: AtomicBool,
    cancel: AtomicBool,
    underruns: AtomicU64,
    error: Mutex<Option<String>>,
}

/// Control/observe side of one playing file.
#[derive(Clone)]
pub struct MusicStream {
    shared: Arc<Shared>,
    rate: u32,
    /// Where in the file this stream started (a seek).
    start_s: f64,
    info: FileInfo,
    path: PathBuf,
}

impl MusicStream {
    /// Open `path` for the mixer (`rate`, `channels`). Blocks until the first
    /// [`PREFILL_SECS`] are decoded: call it off the async runtime.
    pub fn open(path: &Path, rate: u32, channels: usize) -> Result<(MusicStream, MusicSource)> {
        Self::open_at(path, rate, channels, 0.0)
    }

    /// [`Self::open`] from `start_s` into the file (a seek; `position_s` counts from there).
    pub fn open_at(path: &Path, rate: u32, channels: usize, start_s: f64) -> Result<(MusicStream, MusicSource)> {
        let (mut dec, start_s) = PlanarStream::open_at(path, rate, 2, start_s)?;
        let info = dec.info();
        let cap = ((rate as f64 * RING_SECS) as usize).max(4096) * channels;
        let (mut tx, rx) = rtrb::RingBuffer::<f32>::new(cap);
        let shared = Arc::new(Shared::default());
        let sh = shared.clone();
        let name = path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
        std::thread::Builder::new().name("r3x-music-decode".into()).spawn(move || {
            let mut inter = Vec::new();
            'outer: loop {
                let block = match dec.next_block() {
                    Ok(Some(b)) => b,
                    Ok(None) => break,
                    Err(e) => {
                        tracing::warn!(track = %name, "decode failed: {e}");
                        *sh.error.lock().unwrap() = Some(e.to_string());
                        break;
                    }
                };
                inter.clear();
                interleave_for_device(&block, channels, &mut inter);
                let mut off = 0;
                while off < inter.len() {
                    if sh.cancel.load(Ordering::Relaxed) || tx.is_abandoned() {
                        break 'outer;
                    }
                    let n = tx.slots().min(inter.len() - off);
                    if n == 0 {
                        std::thread::sleep(Duration::from_millis(10));
                        continue;
                    }
                    let n = n - n % channels;
                    if n == 0 {
                        std::thread::sleep(Duration::from_millis(2));
                        continue;
                    }
                    let mut chunk = tx.write_chunk_uninit(n).expect("slots checked");
                    let (a, b) = chunk.as_mut_slices();
                    let (ha, hb) = inter[off..off + n].split_at(a.len());
                    for (d, s) in a.iter_mut().zip(ha) {
                        d.write(*s);
                    }
                    for (d, s) in b.iter_mut().zip(hb) {
                        d.write(*s);
                    }
                    // SAFETY: all n slots were written above.
                    unsafe { chunk.commit_all() };
                    off += n;
                    sh.decoded.fetch_add((n / channels) as u64, Ordering::Relaxed);
                }
            }
            sh.eof.store(true, Ordering::Release);
        })?;
        let want = (rate as f64 * PREFILL_SECS) as u64;
        let t0 = Instant::now();
        while shared.decoded.load(Ordering::Relaxed) < want && !shared.eof.load(Ordering::Acquire) {
            if t0.elapsed() > PREFILL_TIMEOUT {
                shared.cancel.store(true, Ordering::Relaxed);
                return Err(anyhow!("decoder too slow for {}", path.display()));
            }
            std::thread::sleep(Duration::from_millis(2));
        }
        if let Some(e) = shared.error.lock().unwrap().clone() {
            if shared.decoded.load(Ordering::Relaxed) == 0 {
                return Err(anyhow!(e));
            }
        }
        let stream = MusicStream { shared: shared.clone(), rate, start_s, info, path: path.to_owned() };
        Ok((stream, MusicSource { rx, shared, channels }))
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn info(&self) -> FileInfo {
        self.info
    }

    /// Position in the file: the start plus what the mixer has played (paused time excluded).
    pub fn position_s(&self) -> f64 {
        self.start_s + self.shared.played.load(Ordering::Relaxed) as f64 / self.rate as f64
    }

    /// Duration: the container's, else what the decoder found once it reached the end.
    pub fn duration_s(&self) -> Option<f64> {
        self.info.duration_s.or_else(|| {
            self.shared.eof.load(Ordering::Acquire).then(|| self.start_s + self.shared.decoded.load(Ordering::Relaxed) as f64 / self.rate as f64)
        })
    }

    pub fn is_finished(&self) -> bool {
        self.shared.finished.load(Ordering::Acquire)
    }

    pub fn set_paused(&self, on: bool) {
        self.shared.paused.store(on, Ordering::Relaxed);
    }

    pub fn is_paused(&self) -> bool {
        self.shared.paused.load(Ordering::Relaxed)
    }

    pub fn underruns(&self) -> u64 {
        self.shared.underruns.load(Ordering::Relaxed)
    }

    /// Hard stop: the mixer drops the source on its next buffer (no fade).
    pub fn cancel(&self) {
        self.shared.cancel.store(true, Ordering::Relaxed);
    }
}

/// The mixer side: pulls decoded frames from the ring.
pub struct MusicSource {
    rx: rtrb::Consumer<f32>,
    shared: Arc<Shared>,
    channels: usize,
}

impl Source for MusicSource {
    fn mix(&mut self, out: &mut [f32], channels: usize, _: &RenderTime) -> bool {
        debug_assert_eq!(channels, self.channels);
        if self.shared.cancel.load(Ordering::Relaxed) {
            return false; // hard stop; a normal stop is a mixer fade-out
        }
        if self.shared.paused.load(Ordering::Relaxed) {
            return true;
        }
        let want = out.len() - out.len() % channels;
        let avail = self.rx.slots() - self.rx.slots() % channels;
        let n = want.min(avail);
        if n > 0 {
            let chunk = self.rx.read_chunk(n).expect("slots checked");
            let (a, b) = chunk.as_slices();
            for (o, s) in out.iter_mut().zip(a.iter().chain(b)) {
                *o += *s;
            }
            chunk.commit_all();
            self.shared.played.fetch_add((n / channels) as u64, Ordering::Relaxed);
        }
        if n < want {
            if self.shared.eof.load(Ordering::Acquire) && self.rx.is_empty() {
                self.shared.finished.store(true, Ordering::Release);
                return false;
            }
            self.shared.underruns.fetch_add(1, Ordering::Relaxed);
        }
        true
    }
}

impl Drop for MusicSource {
    fn drop(&mut self) {
        // Dropped by the mixer (end, fade-out or clear): the stream is over either way.
        self.shared.finished.store(true, Ordering::Release);
        self.shared.cancel.store(true, Ordering::Relaxed);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::decode::wav_bytes;
    use crate::mixer::{mixer, BusId};

    fn write_dc(dir: &Path, name: &str, level: f32, secs: f64) -> PathBuf {
        let p = dir.join(name);
        let n = (8000.0 * secs) as usize;
        std::fs::write(&p, wav_bytes(8000, 1, &vec![level; n])).unwrap();
        p
    }

    fn render(m: &mut crate::mixer::Mixer, frames: usize) -> Vec<f32> {
        std::thread::sleep(Duration::from_millis(30)); // let the decoder refill, as real time would
        let mut out = vec![0.0; frames * 2];
        m.render(&mut out, &RenderTime::now());
        out
    }

    #[test]
    fn plays_to_the_end_with_position_and_crossfades_equal_power() {
        let dir = std::env::temp_dir().join(format!("r3x-music-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let a = write_dc(&dir, "a.wav", 0.5, 1.0);
        let b = write_dc(&dir, "b.wav", 0.25, 2.0);
        let (mut m, h) = mixer(8000, 2);

        let (sa, src) = MusicStream::open(&a, 8000, 2).unwrap();
        h.add(BusId::Music, Box::new(src));
        let out = render(&mut m, 4000);
        assert!((out[100] - 0.5).abs() < 0.01, "{}", out[100]);
        assert!((sa.position_s() - 0.5).abs() < 1e-9);

        sa.set_paused(true);
        render(&mut m, 800);
        assert!((sa.position_s() - 0.5).abs() < 1e-9, "paused time is not position");
        sa.set_paused(false);

        // 0.25 s equal-power crossfade to b: both start on the same frame.
        let (sb, src) = MusicStream::open(&b, 8000, 2).unwrap();
        h.crossfade(BusId::Music, Box::new(src), 0.25);
        let out = render(&mut m, 2000);
        let mid = 1000 * 2; // halfway: cos(π/4)·0.5 + sin(π/4)·0.25
        let want = std::f32::consts::FRAC_1_SQRT_2 * 0.75;
        assert!((out[mid] - want).abs() < 0.01, "{} vs {want}", out[mid]);
        assert!((out[2 * 1990] - 0.25).abs() < 0.01, "landed on b: {}", out[2 * 1990]);
        assert!(sa.is_finished(), "a dropped after its fade-out");
        assert!((sb.position_s() - 0.25).abs() < 1e-9);

        // b runs out: silence and finished, position at its length.
        for _ in 0..10 {
            render(&mut m, 2000);
        }
        assert!(sb.is_finished());
        assert!((sb.position_s() - 2.0).abs() < 0.01, "{}", sb.position_s());
        assert_eq!(sb.duration_s(), Some(2.0));

        // Seek: open b 1.5 s in; the position counts from there and the file ends 0.5 s later.
        let (sc, src) = MusicStream::open_at(&b, 8000, 2, 1.5).unwrap();
        assert!((sc.position_s() - 1.5).abs() < 0.01, "{}", sc.position_s());
        h.add(BusId::Music, Box::new(src));
        for _ in 0..5 {
            render(&mut m, 2000);
        }
        assert!(sc.is_finished());
        assert!((sc.position_s() - 2.0).abs() < 0.01, "{}", sc.position_s());
        std::fs::remove_dir_all(dir).ok();
    }
}

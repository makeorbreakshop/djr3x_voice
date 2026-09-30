//! Mouth amplitude (plan §7b).
//!
//! CantinaOS computed one value per ElevenLabs chunk, so the mouth depended on the network's
//! chunk size. Here the analysis window is fixed at 20 ms of audio; the AGC rule is the one in
//! `elevenlabs_service.py` unchanged:
//!
//! - `db = 20·log10(rms / 32768)` of the int16 samples; silence (rms 0) is 0 and is not
//!   recorded in the history.
//! - With ≥10 windows of history (max 30): `a = (db - min) / max(12, max - min)`, clamped.
//! - Before that: `a = (db + 50) / 40`, clamped.
//! - Then `a = min(1, 2a)`.
//!
//! [`MouthTrack`] turns the per-window values into one value per mouth tick (profile rate,
//! default 30 Hz): the loudest window that overlaps the tick period, so short syllables still
//! open the mouth.

use std::collections::VecDeque;

const HISTORY: usize = 30;
const MIN_HISTORY: usize = 10;
const MIN_RANGE_DB: f32 = 12.0;

/// Streaming 20 ms analyser with AGC. Feed any chunk sizes; get one value per full window.
#[derive(Debug, Clone)]
pub struct Agc {
    window: usize,
    acc: f64,
    n: usize,
    history: VecDeque<f32>,
}

impl Agc {
    pub fn new(sample_rate: u32) -> Self {
        Self::with_window(sample_rate, crate::CHUNK_MS)
    }

    pub fn with_window(sample_rate: u32, window_ms: u32) -> Self {
        Self { window: (sample_rate * window_ms / 1000) as usize, acc: 0.0, n: 0, history: VecDeque::new() }
    }

    pub fn window_len(&self) -> usize {
        self.window
    }

    /// Push int16 samples; appends one amplitude per completed window to `out`.
    pub fn push(&mut self, samples: &[i16], out: &mut Vec<f32>) {
        for &s in samples {
            self.acc += (s as f64) * (s as f64);
            self.n += 1;
            if self.n == self.window {
                let rms = (self.acc / self.n as f64).sqrt() as f32;
                out.push(self.rule(rms));
                self.acc = 0.0;
                self.n = 0;
            }
        }
    }

    /// Close a trailing partial window (end of a line).
    pub fn flush(&mut self, out: &mut Vec<f32>) {
        if self.n > 0 {
            let rms = (self.acc / self.n as f64).sqrt() as f32;
            out.push(self.rule(rms));
            self.acc = 0.0;
            self.n = 0;
        }
    }

    fn rule(&mut self, rms: f32) -> f32 {
        if rms <= 0.0 {
            return 0.0;
        }
        let db = 20.0 * (rms / 32768.0).log10();
        self.history.push_back(db);
        if self.history.len() > HISTORY {
            self.history.pop_front();
        }
        let a = if self.history.len() >= MIN_HISTORY {
            let (lo, hi) = self.history.iter().fold((f32::MAX, f32::MIN), |(lo, hi), &v| (lo.min(v), hi.max(v)));
            ((db - lo) / MIN_RANGE_DB.max(hi - lo)).clamp(0.0, 1.0)
        } else {
            ((db + 50.0) / 40.0).clamp(0.0, 1.0)
        };
        (a * 2.0).min(1.0)
    }
}

/// Per-line amplitude windows, sampled at the mouth rate.
#[derive(Debug, Clone)]
pub struct MouthTrack {
    window_s: f64,
    period_s: f64,
    windows: Vec<f32>,
}

impl MouthTrack {
    pub fn new(mouth_hz: f64) -> Self {
        Self { window_s: crate::CHUNK_MS as f64 / 1000.0, period_s: 1.0 / mouth_hz, windows: Vec::new() }
    }

    pub fn period_s(&self) -> f64 {
        self.period_s
    }

    pub fn extend(&mut self, values: &[f32]) {
        self.windows.extend_from_slice(values);
    }

    /// Audio seconds covered by analysed windows so far.
    pub fn covered_s(&self) -> f64 {
        self.windows.len() as f64 * self.window_s
    }

    /// Value of tick `k` (time `k·period` from the line's first sample): the loudest window
    /// overlapping `((k-1)·period, k·period]`. `None` once past the analysed audio.
    pub fn tick(&self, k: u64) -> Option<f32> {
        let end = k as f64 * self.period_s;
        if end > self.covered_s() + 1e-9 && k > 0 {
            return None;
        }
        let start = (end - self.period_s).max(0.0);
        let first = (start / self.window_s).floor() as usize;
        let last = ((end / self.window_s).ceil() as usize).max(first + 1).min(self.windows.len());
        self.windows.get(first..last).map(|w| w.iter().copied().fold(0.0, f32::max))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tone(amp: f32, n: usize) -> Vec<i16> {
        (0..n).map(|i| ((i as f32 * 0.3).sin() * amp * 32767.0) as i16).collect()
    }

    #[test]
    fn window_is_fixed_regardless_of_chunking() {
        let audio = tone(0.3, 24_000); // 1 s at 24 kHz
        let mut a = Agc::new(24_000);
        let mut whole = Vec::new();
        a.push(&audio, &mut whole);
        let mut b = Agc::new(24_000);
        let mut chunked = Vec::new();
        for c in audio.chunks(1234) {
            b.push(c, &mut chunked);
        }
        assert_eq!(whole.len(), 50);
        assert_eq!(whole, chunked);
    }

    #[test]
    fn agc_rule_matches_cantina() {
        let mut agc = Agc::with_window(1000, 20); // 20-sample windows
        let mut out = Vec::new();
        agc.push(&[0; 20], &mut out);
        assert_eq!(out, [0.0], "silence is dark and not recorded");
        assert!(agc.history.is_empty());

        // Warm-up: static range. rms = 32768·10^(-30/20) → -30 dB → (20/40)·2 = 1.0
        let v = (32768.0 * 10f32.powf(-30.0 / 20.0)) as i16;
        out.clear();
        agc.push(&[v; 20], &mut out);
        assert!((out[0] - 1.0).abs() < 1e-3, "{out:?}");
        // -45 dB → (5/40)·2 = 0.25
        let v = (32768.0 * 10f32.powf(-45.0 / 20.0)) as i16;
        out.clear();
        agc.push(&[v; 20], &mut out);
        assert!((out[0] - 0.25).abs() < 0.01, "{out:?}");

        // After 10 windows: relative to history with the 12 dB minimum range.
        let mut agc = Agc::with_window(1000, 20);
        let level = |db: f32| (32768.0 * 10f32.powf(db / 20.0)) as i16;
        out.clear();
        for _ in 0..9 {
            agc.push(&[level(-40.0); 20], &mut out);
        }
        out.clear();
        agc.push(&[level(-37.0); 20], &mut out); // 10th: min -40, max -37, range 12
        assert!((out[0] - 0.5).abs() < 0.02, "(3/12)·2 = 0.5, got {out:?}");
    }

    #[test]
    fn mouth_ticks_take_loudest_window() {
        let mut m = MouthTrack::new(30.0);
        m.extend(&[0.1, 0.9, 0.2, 0.0, 0.3]); // 100 ms
        assert_eq!(m.tick(0), Some(0.1));
        assert_eq!(m.tick(1), Some(0.9)); // (0, 33.3 ms] spans windows 0-1
        assert_eq!(m.tick(3), Some(0.3)); // (66.7, 100]
        assert_eq!(m.tick(4), None);
    }
}

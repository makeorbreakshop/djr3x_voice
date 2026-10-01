//! The listening reactor's ears (2026-10-01: "he needs visual cues he is listening"):
//! Deepgram's partial transcripts arrive about once a second, too slow to feel live, so
//! R3X reacts to the *sound* of the guest's voice, locally, within a frame or two:
//!
//! - [`Cue::Level`]: how loud the guest is now, 0..1 above the room (25 Hz), for the head
//!   to bob with their rhythm;
//! - [`Cue::Onset`]: they started talking (first speech of the turn, or after a long gap);
//! - [`Cue::Pause`]: they paused mid-turn ([`PAUSE_S`] of quiet after speaking) - "go on?";
//! - [`Cue::Rise`]: a phrase ended with the pitch going up (a question) - a curious cant.
//!
//! Pure and sample-driven (16 kHz mono i16 in 20 ms frames): no clock, no I/O, so every rule
//! is tested with synthetic audio. Voice activity is energy against a tracked noise floor;
//! pitch is normalised autocorrelation over a 40 ms window, 80-400 Hz.

pub const RATE: usize = 16_000;
const FRAME: usize = 320; // 20 ms
const WINDOW: usize = 640; // 40 ms, two periods of 50 Hz headroom for the 80 Hz floor
const MIN_LAG: usize = RATE / 400;
const MAX_LAG: usize = RATE / 80;
/// Voiced this many dB above the noise floor (and above an absolute floor).
const SPEECH_OVER_NOISE_DB: f64 = 12.0;
const ABS_FLOOR_DB: f64 = -55.0;
/// Speech starts after this much voiced audio, ends after this much quiet.
const START_S: f64 = 0.06;
const END_S: f64 = 0.25;
/// Quiet this long after speaking = a pause cue (once per pause).
pub const PAUSE_S: f64 = 0.6;
/// A gap this long makes the next speech a fresh onset.
const FRESH_ONSET_GAP_S: f64 = 1.5;
/// Pitch rise at a phrase end: last 250 ms over the 500 ms before, by this ratio (~2 semitones).
const RISE_RATIO: f64 = 1.12;
const LEVEL_EVERY: usize = 2; // frames: 25 Hz

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Cue {
    Level(f64),
    Onset,
    Pause,
    Rise,
}

#[derive(Debug)]
pub struct ListenCues {
    buf: Vec<i16>,
    win: Vec<f64>,
    t: f64,
    noise_db: f64,
    speaking: bool,
    voiced_run: f64,
    quiet_run: f64,
    spoke_at: Option<f64>,
    paused: bool,
    /// (time, f0) of voiced frames in the current phrase.
    pitch: Vec<(f64, f64)>,
    frames: usize,
}

impl Default for ListenCues {
    fn default() -> Self {
        ListenCues {
            buf: Vec::with_capacity(FRAME),
            win: vec![0.0; WINDOW],
            t: 0.0,
            noise_db: -60.0,
            speaking: false,
            voiced_run: 0.0,
            quiet_run: 0.0,
            spoke_at: None,
            paused: false,
            pitch: vec![],
            frames: 0,
        }
    }
}

fn db(rms: f64) -> f64 {
    20.0 * (rms.max(1e-9)).log10()
}

/// f0 in Hz when the window is clearly periodic (normalised autocorrelation > 0.5).
fn pitch(win: &[f64]) -> Option<f64> {
    let energy: f64 = win.iter().map(|x| x * x).sum();
    if energy <= 1e-9 {
        return None;
    }
    let mut best = (0.0, 0);
    for lag in MIN_LAG..=MAX_LAG.min(win.len() / 2) {
        let n = win.len() - lag;
        let (mut xy, mut xx, mut yy) = (0.0, 0.0, 0.0);
        for i in 0..n {
            xy += win[i] * win[i + lag];
            xx += win[i] * win[i];
            yy += win[i + lag] * win[i + lag];
        }
        let r = xy / (xx * yy).sqrt().max(1e-12);
        if r > best.0 {
            best = (r, lag);
        }
    }
    (best.0 > 0.5).then(|| RATE as f64 / best.1 as f64)
}

fn median(mut v: Vec<f64>) -> Option<f64> {
    if v.is_empty() {
        return None;
    }
    v.sort_by(|a, b| a.total_cmp(b));
    Some(v[v.len() / 2])
}

impl ListenCues {
    /// A new turn: forget the last one (the noise floor is kept: same room).
    pub fn reset(&mut self) {
        let noise = self.noise_db;
        *self = ListenCues { noise_db: noise, ..Default::default() };
    }

    /// Feed samples; cues in the order they happened.
    pub fn push(&mut self, pcm: &[i16]) -> Vec<Cue> {
        let mut out = vec![];
        for &s in pcm {
            self.buf.push(s);
            if self.buf.len() == FRAME {
                let frame = std::mem::take(&mut self.buf);
                self.frame(&frame, &mut out);
                self.buf = frame;
                self.buf.clear();
            }
        }
        out
    }

    fn frame(&mut self, f: &[i16], out: &mut Vec<Cue>) {
        let dt = FRAME as f64 / RATE as f64;
        self.t += dt;
        self.frames += 1;
        let x: Vec<f64> = f.iter().map(|&s| s as f64 / 32768.0).collect();
        self.win.drain(..FRAME);
        self.win.extend_from_slice(&x);
        let level_db = db((x.iter().map(|v| v * v).sum::<f64>() / x.len() as f64).sqrt());
        // Noise floor: falls fast to quiet, creeps up slowly (so speech never becomes "noise").
        if level_db < self.noise_db {
            self.noise_db += (level_db - self.noise_db) * 0.3;
        } else {
            self.noise_db += 3.0 * dt;
        }
        let voiced = level_db > self.noise_db + SPEECH_OVER_NOISE_DB && level_db > ABS_FLOOR_DB;
        if voiced {
            self.voiced_run += dt;
            self.quiet_run = 0.0;
            if let Some(f0) = pitch(&self.win) {
                self.pitch.push((self.t, f0));
            }
        } else {
            self.quiet_run += dt;
            self.voiced_run = 0.0;
        }
        if !self.speaking && self.voiced_run >= START_S {
            self.speaking = true;
            let fresh = self.spoke_at.is_none_or(|t| self.t - t >= FRESH_ONSET_GAP_S);
            if fresh {
                out.push(Cue::Onset);
            }
            self.paused = false;
        }
        if self.speaking {
            self.spoke_at = Some(self.t);
            if self.quiet_run >= END_S {
                self.speaking = false;
                if self.rose() {
                    out.push(Cue::Rise);
                }
                self.pitch.clear();
            }
        } else if !self.paused && self.spoke_at.is_some_and(|t| self.t - t >= PAUSE_S - END_S) && self.quiet_run >= PAUSE_S {
            self.paused = true;
            out.push(Cue::Pause);
        }
        if self.frames.is_multiple_of(LEVEL_EVERY) {
            let above = (level_db - self.noise_db - 6.0) / 30.0;
            out.push(Cue::Level(if voiced { above.clamp(0.0, 1.0) } else { 0.0 }));
        }
    }

    /// The phrase just ended on a rising pitch.
    fn rose(&self) -> bool {
        let Some(&(end, _)) = self.pitch.last() else { return false };
        let tail = median(self.pitch.iter().filter(|(t, _)| end - t < 0.25).map(|p| p.1).collect());
        let body = median(self.pitch.iter().filter(|(t, _)| (0.25..0.75).contains(&(end - t))).map(|p| p.1).collect());
        matches!((tail, body), (Some(a), Some(b)) if a / b >= RISE_RATIO)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A tone at `f0` Hz (or its sweep to `f1`), loud speech-like level.
    fn tone(secs: f64, f0: f64, f1: f64, amp: f64) -> Vec<i16> {
        let n = (secs * RATE as f64) as usize;
        let mut ph = 0.0f64;
        (0..n)
            .map(|i| {
                let f = f0 + (f1 - f0) * i as f64 / n as f64;
                ph += 2.0 * std::f64::consts::PI * f / RATE as f64;
                // A little harmonic content, like a voice.
                ((ph.sin() + 0.4 * (2.0 * ph).sin()) * amp * 32767.0 / 1.4) as i16
            })
            .collect()
    }

    fn quiet(secs: f64) -> Vec<i16> {
        // A low hiss: the room.
        (0..(secs * RATE as f64) as usize).map(|i| ((i * 7919) % 61) as i16 - 30).collect()
    }

    fn cues(c: &mut ListenCues, pcm: &[i16]) -> Vec<Cue> {
        c.push(pcm).into_iter().filter(|c| !matches!(c, Cue::Level(_))).collect()
    }

    #[test]
    fn speech_starting_is_an_onset_within_a_few_frames() {
        let mut c = ListenCues::default();
        assert!(cues(&mut c, &quiet(1.0)).is_empty(), "the room is not speech");
        assert_eq!(cues(&mut c, &tone(0.1, 150.0, 150.0, 0.3)), vec![Cue::Onset], "within 100 ms");
        assert!(cues(&mut c, &tone(0.5, 150.0, 150.0, 0.3)).is_empty(), "once");
    }

    #[test]
    fn a_pause_mid_turn_is_a_pause_cue_once_and_a_short_gap_is_not() {
        let mut c = ListenCues::default();
        cues(&mut c, &quiet(0.5));
        cues(&mut c, &tone(1.0, 150.0, 150.0, 0.3));
        assert!(cues(&mut c, &quiet(0.4)).is_empty(), "a breath is not a pause");
        assert_eq!(cues(&mut c, &quiet(0.4)), vec![Cue::Pause]);
        assert!(cues(&mut c, &quiet(1.0)).is_empty(), "once per pause");
        // Speech again after a long gap: a fresh onset.
        assert_eq!(cues(&mut c, &tone(0.2, 150.0, 150.0, 0.3)), vec![Cue::Onset]);
    }

    #[test]
    fn a_rising_phrase_end_is_a_question_and_a_flat_one_is_not() {
        let mut c = ListenCues::default();
        cues(&mut c, &quiet(0.5));
        let mut q = tone(0.6, 140.0, 140.0, 0.3);
        q.extend(tone(0.3, 140.0, 190.0, 0.3));
        q.extend(quiet(0.4));
        assert!(cues(&mut c, &q).contains(&Cue::Rise), "rising: a question");
        let mut c = ListenCues::default();
        cues(&mut c, &quiet(0.5));
        let mut s = tone(0.9, 140.0, 135.0, 0.3);
        s.extend(quiet(0.4));
        assert!(!cues(&mut c, &s).contains(&Cue::Rise), "flat or falling: a statement");
    }

    #[test]
    fn level_follows_the_voice_at_25_hz() {
        let mut c = ListenCues::default();
        let room: Vec<f64> = c.push(&quiet(1.0)).into_iter().filter_map(|c| if let Cue::Level(l) = c { Some(l) } else { None }).collect();
        assert_eq!(room.len(), 25);
        assert!(room.iter().all(|l| *l == 0.0));
        let loud: Vec<f64> = c.push(&tone(0.4, 150.0, 150.0, 0.3)).into_iter().filter_map(|c| if let Cue::Level(l) = c { Some(l) } else { None }).collect();
        assert!(loud.iter().skip(2).all(|l| *l > 0.3), "{loud:?}");
    }

    #[test]
    fn pitch_finds_a_voice_and_ignores_noise() {
        let w: Vec<f64> = tone(0.04, 200.0, 200.0, 0.3).iter().map(|s| *s as f64 / 32768.0).collect();
        let f = pitch(&w).unwrap();
        assert!((f - 200.0).abs() < 8.0, "{f}");
        let mut x: u32 = 0x9E37_79B9;
        let n: Vec<f64> = (0..WINDOW)
            .map(|_| {
                x ^= x << 13;
                x ^= x >> 17;
                x ^= x << 5;
                (x as f64 / u32::MAX as f64 - 0.5) * 0.3
            })
            .collect();
        assert!(pitch(&n).is_none());
    }
}

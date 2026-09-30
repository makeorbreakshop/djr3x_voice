//! librosa 0.11 `beat.beat_track` ported step for step (Ellis 2007): mel onset strength
//! (median over bands), autocorrelation tempogram with the log-normal 120 bpm prior, and the
//! dynamic-programming beat tracker with edge trimming. Same defaults: 22.05 kHz, n_fft 2048,
//! hop 512, 128 slaney mels, 8 s tempogram window, tightness 100.

use realfft::RealFftPlanner;

pub const SR: u32 = 22_050;
pub const N_FFT: usize = 2048;
pub const HOP: usize = 512;
pub const N_MELS: usize = 128;
const TOP_DB: f64 = 80.0;
const AMIN: f64 = 1e-10;
const START_BPM: f64 = 120.0;
const MAX_TEMPO: f64 = 320.0;
const TIGHTNESS: f64 = 100.0;
const AC_SIZE_S: f64 = 8.0;

fn hz_to_mel(hz: f64) -> f64 {
    let f_sp = 200.0 / 3.0;
    let (min_log_hz, min_log_mel, logstep) = (1000.0, 1000.0 / f_sp, (6.4f64).ln() / 27.0);
    if hz >= min_log_hz {
        min_log_mel + (hz / min_log_hz).ln() / logstep
    } else {
        hz / f_sp
    }
}

fn mel_to_hz(mel: f64) -> f64 {
    let f_sp = 200.0 / 3.0;
    let (min_log_hz, min_log_mel, logstep) = (1000.0, 1000.0 / f_sp, (6.4f64).ln() / 27.0);
    if mel >= min_log_mel {
        min_log_hz * (logstep * (mel - min_log_mel)).exp()
    } else {
        f_sp * mel
    }
}

/// `librosa.filters.mel(sr, n_fft, n_mels)` (slaney scale and norm), as sparse rows.
fn mel_filters(sr: u32, n_fft: usize, n_mels: usize) -> Vec<(usize, Vec<f32>)> {
    let bins = n_fft / 2 + 1;
    let fft_f: Vec<f64> = (0..bins).map(|i| i as f64 * sr as f64 / n_fft as f64).collect();
    let (lo, hi) = (hz_to_mel(0.0), hz_to_mel(sr as f64 / 2.0));
    let mel_f: Vec<f64> = (0..n_mels + 2).map(|i| mel_to_hz(lo + (hi - lo) * i as f64 / (n_mels + 1) as f64)).collect();
    (0..n_mels)
        .map(|m| {
            let enorm = 2.0 / (mel_f[m + 2] - mel_f[m]);
            let (d0, d1) = (mel_f[m + 1] - mel_f[m], mel_f[m + 2] - mel_f[m + 1]);
            let w: Vec<f64> = fft_f
                .iter()
                .map(|&f| {
                    let lower = (f - mel_f[m]) / d0;
                    let upper = (mel_f[m + 2] - f) / d1;
                    lower.min(upper).max(0.0) * enorm
                })
                .collect();
            let start = w.iter().position(|&v| v > 0.0).unwrap_or(0);
            let end = w.iter().rposition(|&v| v > 0.0).map_or(start, |e| e + 1);
            (start, w[start..end].iter().map(|&v| v as f32).collect())
        })
        .collect()
}

/// Periodic Hann (`scipy.signal.get_window('hann', n, fftbins=True)`).
fn hann(n: usize) -> Vec<f64> {
    (0..n).map(|i| 0.5 - 0.5 * (2.0 * std::f64::consts::PI * i as f64 / n as f64).cos()).collect()
}

/// `onset.onset_strength(y, sr, hop_length=512, aggregate=np.median)`.
pub fn onset_strength(y: &[f32]) -> Vec<f64> {
    let pad = N_FFT / 2;
    let mut padded = vec![0.0f32; y.len() + 2 * pad]; // center=True, pad_mode="constant"
    padded[pad..pad + y.len()].copy_from_slice(y);
    let frames = 1 + (padded.len() - N_FFT) / HOP;
    let win: Vec<f32> = hann(N_FFT).into_iter().map(|v| v as f32).collect();
    let filters = mel_filters(SR, N_FFT, N_MELS);
    let fft = RealFftPlanner::<f32>::new().plan_fft_forward(N_FFT);
    let mut buf = fft.make_input_vec();
    let mut spec = fft.make_output_vec();
    let mut db = vec![0.0f64; frames * N_MELS]; // [frame][mel]
    let mut power = vec![0.0f32; N_FFT / 2 + 1];
    for t in 0..frames {
        let s = &padded[t * HOP..t * HOP + N_FFT];
        for ((b, x), w) in buf.iter_mut().zip(s).zip(&win) {
            *b = x * w;
        }
        fft.process(&mut buf, &mut spec).expect("fft sizes");
        for (p, c) in power.iter_mut().zip(&spec) {
            *p = c.norm_sqr();
        }
        for (m, (start, w)) in filters.iter().enumerate() {
            let e: f32 = w.iter().zip(&power[*start..]).map(|(a, b)| a * b).sum();
            db[t * N_MELS + m] = 10.0 * (e as f64).max(AMIN).log10();
        }
    }
    let max = db.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
    for v in &mut db {
        *v = v.max(max - TOP_DB);
    }
    // lag 1 flux, rectified, median over bands; padded by lag + n_fft/(2 hop) = 3 frames.
    let lead = 1 + N_FFT / (2 * HOP);
    let mut env = vec![0.0f64; frames];
    let mut col = vec![0.0f64; N_MELS];
    for t in 1..frames {
        for m in 0..N_MELS {
            col[m] = (db[t * N_MELS + m] - db[(t - 1) * N_MELS + m]).max(0.0);
        }
        col.sort_by(|a, b| a.total_cmp(b));
        let med = 0.5 * (col[N_MELS / 2 - 1] + col[N_MELS / 2]);
        let i = t - 1 + lead;
        if i < frames {
            env[i] = med;
        }
    }
    env
}

/// `beat._tempo(onset_envelope, sr, hop)`: argmax of log1p(1e6·mean tempogram) + prior.
pub fn tempo(env: &[f64]) -> f64 {
    let win = ((AC_SIZE_S * SR as f64) as usize) / HOP; // time_to_frames: floor
    let n = env.len();
    let half = win / 2;
    let mut padded = Vec::with_capacity(n + 2 * half);
    let (e0, e1) = (env[0], env[n - 1]);
    padded.extend((0..half).map(|i| e0 * i as f64 / half as f64));
    padded.extend_from_slice(env);
    // np.pad linear_ramp to 0: left linspace(0, e0, half, endpoint=False), right its mirror.
    padded.extend((0..half).map(|j| e1 * (half - 1 - j) as f64 / half as f64));
    let w = hann(win);
    let nfft = (2 * win - 1).next_power_of_two();
    let mut planner = RealFftPlanner::<f64>::new();
    let fwd = planner.plan_fft_forward(nfft);
    let inv = planner.plan_fft_inverse(nfft);
    let mut inb = fwd.make_input_vec();
    let mut spec = fwd.make_output_vec();
    let mut out = inv.make_output_vec();
    let mut acc = vec![0.0f64; win];
    for t in 0..n {
        inb.fill(0.0);
        for k in 0..win {
            inb[k] = padded[t + k] * w[k];
        }
        fwd.process(&mut inb, &mut spec).expect("fft");
        for c in spec.iter_mut() {
            *c = realfft::num_complex::Complex::new(c.norm_sqr(), 0.0);
        }
        inv.process(&mut spec, &mut out).expect("ifft");
        let ac = &out[..win];
        let peak = ac.iter().fold(0.0f64, |m, v| m.max(v.abs()));
        let norm = if peak > f64::MIN_POSITIVE { peak } else { 1.0 };
        for (a, v) in acc.iter_mut().zip(ac) {
            *a += v / norm;
        }
    }
    let frame_rate = SR as f64 / HOP as f64;
    let mut best = (f64::NEG_INFINITY, 0usize);
    let mut below_max = false;
    for (k, a) in acc.iter().enumerate().skip(1) {
        let bpm = 60.0 * frame_rate / k as f64;
        below_max |= bpm < MAX_TEMPO;
        if !below_max {
            continue;
        }
        let tg = a / n as f64;
        let prior = -0.5 * (bpm.log2() - START_BPM.log2()).powi(2);
        let score = (1e6 * tg).ln_1p() + prior;
        if score > best.0 {
            best = (score, k);
        }
    }
    if best.1 == 0 {
        return 0.0;
    }
    60.0 * frame_rate / best.1 as f64
}

/// `beat.__beat_tracker(env, bpm, frame_rate, tightness=100, trim=True)`: beat frames.
pub fn track_beats(env: &[f64], bpm: f64) -> Vec<usize> {
    let n = env.len();
    if n < 2 || bpm <= 0.0 {
        return Vec::new();
    }
    let frame_rate = SR as f64 / HOP as f64;
    let fpb = (frame_rate * 60.0 / bpm).round_ties_even();
    // normalise by std (ddof=1)
    let mean = env.iter().sum::<f64>() / n as f64;
    let std = (env.iter().map(|v| (v - mean).powi(2)).sum::<f64>() / (n - 1) as f64).sqrt();
    let onsets: Vec<f64> = env.iter().map(|v| v / (std + f64::MIN_POSITIVE)).collect();
    // local score: 'same' convolution with a Gaussian of width fpb/32
    let fp = fpb as i64;
    let window: Vec<f64> = (-fp..=fp).map(|k| (-0.5 * (k as f64 * 32.0 / fpb).powi(2)).exp()).collect();
    let kk = window.len();
    let mut local = vec![0.0f64; n];
    for (i, l) in local.iter_mut().enumerate() {
        let lo = (i + kk / 2).saturating_sub(n - 1);
        let hi = (i + kk / 2).min(kk);
        for k in lo..hi {
            *l += window[k] * onsets[i + kk / 2 - k];
        }
    }
    // dynamic program
    let thresh = 0.01 * local.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
    let mut backlink = vec![-1i64; n];
    let mut cum = vec![0.0f64; n];
    cum[0] = local[0];
    let mut first_beat = true;
    let half = (fpb / 2.0).round_ties_even() as i64;
    let log_fpb = fpb.ln();
    for i in 0..n {
        let mut best = f64::NEG_INFINITY;
        let mut loc_best = -1i64;
        let mut loc = i as i64 - half;
        let stop = i as i64 - 2 * fp - 1;
        while loc > stop {
            if loc < 0 {
                break;
            }
            let s = cum[loc as usize] - TIGHTNESS * ((i as f64 - loc as f64).ln() - log_fpb).powi(2);
            if s > best {
                best = s;
                loc_best = loc;
            }
            loc -= 1;
        }
        cum[i] = if loc_best >= 0 { local[i] + best } else { local[i] };
        if first_beat && local[i] < thresh {
            backlink[i] = -1;
        } else {
            backlink[i] = loc_best;
            first_beat = false;
        }
    }
    // last beat: last local max of cum at or above half the median local max
    let is_max = |i: usize| i > 0 && cum[i] > cum[i - 1] && (i + 1 == n || cum[i] >= cum[i + 1]);
    let mut maxima: Vec<f64> = (0..n).filter(|&i| is_max(i)).map(|i| cum[i]).collect();
    maxima.sort_by(|a, b| a.total_cmp(b));
    let median = match maxima.len() {
        0 => 0.0,
        m if m % 2 == 1 => maxima[m / 2],
        m => 0.5 * (maxima[m / 2 - 1] + maxima[m / 2]),
    };
    let threshold = 0.5 * median;
    let tail = (0..n).rev().find(|&i| is_max(i) && cum[i] >= threshold).unwrap_or(n - 1);
    let mut beats = vec![false; n];
    let mut b = tail as i64;
    while b >= 0 {
        beats[b as usize] = true;
        b = backlink[b as usize];
    }
    // trim weak leading/trailing beats
    let at: Vec<f64> = (0..n).filter(|&i| beats[i]).map(|i| local[i]).collect();
    let hw = [0.0, 0.5, 1.0, 0.5, 0.0];
    let full: Vec<f64> = (0..at.len() + 4)
        .map(|j| (0..5).filter(|&k| j >= k && j - k < at.len()).map(|k| hw[k] * at[j - k]).sum())
        .collect();
    let smooth = &full[2.min(full.len())..(n + 2).min(full.len())];
    let thr = if smooth.is_empty() { 0.0 } else { 0.5 * (smooth.iter().map(|v| v * v).sum::<f64>() / smooth.len() as f64).sqrt() };
    let mut i = 0;
    while i < n && local[i] <= thr {
        beats[i] = false;
        i += 1;
    }
    let mut i = n as i64 - 1;
    while i >= 0 && local[i as usize] <= thr {
        beats[i as usize] = false;
        i -= 1;
    }
    (0..n).filter(|&i| beats[i]).collect()
}

/// `beat_track(y, sr=22050)` -> (tempo, beat times in s).
pub fn beat_track(y: &[f32]) -> (f64, Vec<f64>) {
    let env = onset_strength(y);
    if !env.iter().any(|&v| v != 0.0) {
        return (0.0, Vec::new());
    }
    let bpm = tempo(&env);
    let beats = track_beats(&env, bpm);
    (bpm, beats.into_iter().map(|f| (f * HOP) as f64 / SR as f64).collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mel_scale_round_trips_and_filters_cover_the_band() {
        for hz in [0.0, 440.0, 1000.0, 5000.0, 11_025.0] {
            assert!((mel_to_hz(hz_to_mel(hz)) - hz).abs() < 1e-6);
        }
        let f = mel_filters(SR, N_FFT, N_MELS);
        assert_eq!(f.len(), N_MELS);
        assert!(f.iter().all(|(_, w)| !w.is_empty()));
    }

    /// A click track at a known tempo comes back at that tempo, beats on the clicks.
    #[test]
    fn click_track_tempo_and_beats() {
        let bpm = 110.0;
        let secs = 30.0;
        let n = (SR as f64 * secs) as usize;
        let period = 60.0 / bpm;
        let mut y = vec![0.0f32; n];
        let mut t = 0.5;
        while t < secs - 0.1 {
            let i0 = (t * SR as f64) as usize;
            for k in 0..600 {
                let e = (-(k as f32) / 120.0).exp();
                y[i0 + k] += e * ((k as f32) * 0.35).sin() * 0.8;
            }
            t += period;
        }
        let (got, beats) = beat_track(&y);
        assert!((got - bpm).abs() / bpm < 0.03, "tempo {got}");
        assert!(beats.len() > 40, "{}", beats.len());
        // every tracked beat sits on a click (within two hops)
        for b in &beats {
            let phase = ((b - 0.5) / period).round() * period + 0.5;
            assert!((b - phase).abs() < 2.0 * HOP as f64 / SR as f64, "beat {b} off-grid");
        }
    }
}

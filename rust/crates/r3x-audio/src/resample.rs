//! Streaming mono resampler: windowed-sinc low-pass (when downsampling) + linear
//! interpolation. Adequate for speech (24 kHz TTS -> device rate, device rate -> 16 kHz STT);
//! music (Phase 4) will want a better one.

#[derive(Debug, Clone)]
pub struct Resampler {
    ratio: f64, // in / out
    pos: f64,   // fractional read position into `buf`
    buf: Vec<f32>,
    taps: Vec<f32>,
    hist: Vec<f32>,
}

impl Resampler {
    pub fn new(from: u32, to: u32) -> Self {
        let ratio = from as f64 / to as f64;
        let taps = if ratio > 1.0 { lowpass(0.45 / ratio, 31) } else { Vec::new() };
        Self { ratio, pos: 0.0, buf: Vec::new(), hist: vec![0.0; taps.len().saturating_sub(1)], taps }
    }

    pub fn is_identity(&self) -> bool {
        self.ratio == 1.0
    }

    /// Resample `input`, appending to `out`.
    pub fn process(&mut self, input: &[f32], out: &mut Vec<f32>) {
        if self.is_identity() {
            out.extend_from_slice(input);
            return;
        }
        if self.taps.is_empty() {
            self.buf.extend_from_slice(input);
        } else {
            for &x in input {
                self.hist.push(x);
                let n = self.taps.len();
                let w = &self.hist[self.hist.len() - n..];
                self.buf.push(w.iter().zip(self.taps.iter().rev()).map(|(a, b)| a * b).sum());
            }
            let keep = self.taps.len() - 1;
            let cut = self.hist.len() - keep;
            self.hist.drain(..cut);
        }
        while self.pos + 1.0 < self.buf.len() as f64 {
            let i = self.pos as usize;
            let f = (self.pos - i as f64) as f32;
            out.push(self.buf[i] * (1.0 - f) + self.buf[i + 1] * f);
            self.pos += self.ratio;
        }
        let used = (self.pos as usize).min(self.buf.len());
        self.buf.drain(..used);
        self.pos -= used as f64;
    }
}

/// Hann-windowed sinc, `cutoff` as a fraction of the input rate.
fn lowpass(cutoff: f64, n: usize) -> Vec<f32> {
    let m = (n - 1) as f64 / 2.0;
    let mut h: Vec<f64> = (0..n)
        .map(|i| {
            let x = i as f64 - m;
            let sinc = if x == 0.0 { 2.0 * cutoff } else { (2.0 * std::f64::consts::PI * cutoff * x).sin() / (std::f64::consts::PI * x) };
            let w = 0.5 - 0.5 * (2.0 * std::f64::consts::PI * i as f64 / (n - 1) as f64).cos();
            sinc * w
        })
        .collect();
    let sum: f64 = h.iter().sum();
    h.iter_mut().for_each(|v| *v /= sum);
    h.into_iter().map(|v| v as f32).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rms(v: &[f32]) -> f32 {
        (v.iter().map(|x| x * x).sum::<f32>() / v.len() as f32).sqrt()
    }

    #[test]
    fn lengths_and_level_survive_streaming() {
        let sr_in = 48_000;
        let tone: Vec<f32> = (0..sr_in).map(|i| (i as f32 * 2.0 * std::f32::consts::PI * 440.0 / sr_in as f32).sin() * 0.5).collect();
        let mut r = Resampler::new(48_000, 16_000);
        let mut out = Vec::new();
        for c in tone.chunks(960) {
            r.process(c, &mut out);
        }
        assert!((out.len() as i64 - 16_000).abs() <= 2, "{}", out.len());
        assert!((rms(&out[1000..]) - 0.5 / 2f32.sqrt()).abs() < 0.02);

        // 12 kHz is above the 16 kHz Nyquist: it must be attenuated, not aliased through.
        let hi: Vec<f32> = (0..sr_in).map(|i| (i as f32 * 2.0 * std::f32::consts::PI * 12_000.0 / sr_in as f32).sin() * 0.5).collect();
        let mut r = Resampler::new(48_000, 16_000);
        let mut out = Vec::new();
        r.process(&hi, &mut out);
        assert!(rms(&out[1000..]) < 0.05, "{}", rms(&out[1000..]));

        let mut up = Resampler::new(24_000, 48_000);
        let mut out = Vec::new();
        up.process(&vec![0.25; 2400], &mut out);
        assert!((out.len() as i64 - 4800).abs() <= 2);
    }
}

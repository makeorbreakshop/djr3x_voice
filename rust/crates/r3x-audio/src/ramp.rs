//! Sample-accurate linear gain ramp (ducking: plan §7b, default 80 ms instead of a step).

#[derive(Debug, Clone, Copy)]
pub struct GainRamp {
    current: f32,
    target: f32,
    step: f32,
    remaining: u32,
}

impl GainRamp {
    pub fn new(gain: f32) -> Self {
        Self { current: gain, target: gain, step: 0.0, remaining: 0 }
    }

    /// Ramp to `target` over `frames` frames (0 = immediately).
    pub fn set(&mut self, target: f32, frames: u32) {
        self.target = target;
        if frames == 0 {
            self.current = target;
            self.remaining = 0;
        } else {
            self.step = (target - self.current) / frames as f32;
            self.remaining = frames;
        }
    }

    pub fn current(&self) -> f32 {
        self.current
    }

    pub fn target(&self) -> f32 {
        self.target
    }

    pub fn is_ramping(&self) -> bool {
        self.remaining > 0
    }

    /// Gain for the next frame.
    #[inline]
    pub fn advance(&mut self) -> f32 {
        if self.remaining > 0 {
            self.remaining -= 1;
            self.current = if self.remaining == 0 { self.target } else { self.current + self.step };
        }
        self.current
    }
}

pub fn ms_to_frames(ms: f64, sample_rate: u32) -> u32 {
    (ms.max(0.0) * sample_rate as f64 / 1000.0).round() as u32
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ramps_linearly_and_lands_exactly() {
        let mut r = GainRamp::new(1.0);
        r.set(0.5, ms_to_frames(80.0, 1000)); // 80 frames
        let v: Vec<f32> = (0..80).map(|_| r.advance()).collect();
        assert!((v[39] - 0.75).abs() < 1e-3);
        assert_eq!(v[79], 0.5);
        assert!(!r.is_ramping());
        assert!(v.windows(2).all(|w| w[1] <= w[0]));
    }
}

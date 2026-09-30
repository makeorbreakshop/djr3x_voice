//! Seeded RNG. mulberry32, bit-identical to the JS reference used by the parity scripts
//! (`sim/web/scripts/*parity*.mjs`), so a TS run and a Rust run with the same seed draw the
//! same numbers.

#[derive(Clone, Debug)]
pub enum Rng {
    Mulberry32(u32),
    /// Always returns the same value (tests: `() => 0.5`).
    Const(f64),
}

impl Rng {
    pub fn new(seed: u32) -> Self {
        Rng::Mulberry32(seed)
    }

    /// Uniform in [0, 1), like `Math.random()`.
    pub fn next_f64(&mut self) -> f64 {
        match self {
            Rng::Const(v) => *v,
            Rng::Mulberry32(a) => {
                *a = a.wrapping_add(0x6D2B_79F5);
                let a = *a;
                let mut t = (a ^ (a >> 15)).wrapping_mul(1 | a);
                t = t.wrapping_add((t ^ (t >> 7)).wrapping_mul(61 | t)) ^ t;
                f64::from(t ^ (t >> 14)) / 4_294_967_296.0
            }
        }
    }

    /// `THREE.MathUtils.randFloat(lo, hi)`.
    pub fn range(&mut self, lo: f64, hi: f64) -> f64 {
        lo + self.next_f64() * (hi - lo)
    }

    /// `THREE.MathUtils.randFloatSpread(range)`.
    pub fn spread(&mut self, range: f64) -> f64 {
        range * (0.5 - self.next_f64())
    }
}

impl Default for Rng {
    fn default() -> Self {
        Rng::new(0x5EED)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn matches_js_mulberry32() {
        // node -e 'mulberry32(1)' first three draws
        let mut r = Rng::new(1);
        let v: Vec<f64> = (0..3).map(|_| r.next_f64()).collect();
        assert_eq!(
            v,
            vec![0.6270739405881613, 0.002735721180215478, 0.5274470399599522]
        );
    }
}

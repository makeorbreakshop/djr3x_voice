//! When to run face recognition on a captured frame.
//!
//! Frames arrive at the capture rate (5 fps), but recognition is the expensive part, and a
//! scene that has not changed does not need it five times a second:
//!
//! | scene | analysed |
//! |---|---|
//! | nobody there, nothing moving | 1 fps |
//! | a person there, still | ~2 fps (every 3rd frame at 5 fps) |
//! | motion in the frame, a face appearing, leaving, moving or changing identity, or a present person's face missing (a possible exit) | every frame, for [`BURST`] |
//!
//! So arrival is still seen on the frame it moves into view, and exits and gaze are timed at
//! the full rate. Motion is a cheap test: a 32x18 grid of block luminance means against the
//! grid of the last analysed frame.

use std::time::{Duration, Instant};

use crate::frame::Frame;

/// How long a change keeps recognition at the full capture rate.
pub const BURST: Duration = Duration::from_secs(2);
/// Analysis interval with nobody in view.
pub const EMPTY_EVERY: Duration = Duration::from_secs(1);
/// Analysis interval with a person in view and nothing changing.
pub const PRESENT_EVERY: Duration = Duration::from_millis(500);

const GRID_W: usize = 32;
const GRID_H: usize = 18;
/// A block counts as changed past this many luminance levels (0-255): above sensor noise.
const BLOCK_DELTA: f32 = 12.0;
/// Motion = at least this fraction of blocks changed (about 17 of 576).
const MOTION_FRACTION: f32 = 0.03;
/// A face centre moving this far (fraction of the frame width, ~1.4 deg at 70 deg HFOV).
const FACE_MOVE: f32 = 0.02;

/// Block-mean luminance of a frame on a 32x18 grid (every 4th pixel of each block).
#[derive(Debug, Clone, PartialEq)]
pub struct Thumb(Vec<f32>);

impl Thumb {
    pub fn of(frame: &Frame) -> Self {
        let (w, h) = (frame.width as usize, frame.height as usize);
        let mut out = Vec::with_capacity(GRID_W * GRID_H);
        for gy in 0..GRID_H {
            let (y0, y1) = (gy * h / GRID_H, ((gy + 1) * h / GRID_H).max(gy * h / GRID_H + 1));
            for gx in 0..GRID_W {
                let (x0, x1) = (gx * w / GRID_W, ((gx + 1) * w / GRID_W).max(gx * w / GRID_W + 1));
                let (mut sum, mut n) = (0u32, 0u32);
                for y in (y0..y1.min(h)).step_by(4) {
                    let row = y * w * 3;
                    for x in (x0..x1.min(w)).step_by(4) {
                        let i = row + x * 3;
                        let p = &frame.rgb[i..i + 3];
                        sum += 54 * u32::from(p[0]) + 183 * u32::from(p[1]) + 19 * u32::from(p[2]);
                        n += 256;
                    }
                }
                out.push(if n > 0 { sum as f32 / n as f32 } else { 0.0 });
            }
        }
        Thumb(out)
    }

    /// Enough of the picture changed to be worth a look.
    pub fn moved_from(&self, other: &Thumb) -> bool {
        if self.0.len() != other.0.len() {
            return true;
        }
        let changed = self.0.iter().zip(&other.0).filter(|(a, b)| (*a - *b).abs() > BLOCK_DELTA).count();
        changed as f32 >= MOTION_FRACTION * self.0.len() as f32
    }
}

/// What one analysis found, as far as the cadence cares.
#[derive(Debug, Clone, PartialEq)]
pub struct Seen {
    /// Largest face centre, 0..1 of width and height.
    pub face: Option<[f32; 2]>,
    /// Who it is (None: nobody enrolled, or no face).
    pub name: Option<String>,
}

#[derive(Debug, Default)]
pub struct Cadence {
    /// Analyse every frame (fps <= 0: tests, replay).
    every_frame: bool,
    last: Option<(Instant, Thumb, Seen)>,
    burst_until: Option<Instant>,
    /// Someone is present (presence's view; a missing face while present is a possible exit).
    present: bool,
}

impl Cadence {
    pub fn new(fps: f64) -> Self {
        Self { every_frame: fps <= 0.0, ..Default::default() }
    }

    /// Should this frame be analysed? `thumb` is the frame's [`Thumb`].
    pub fn due(&mut self, now: Instant, thumb: &Thumb) -> bool {
        let Some((at, last_thumb, seen)) = &self.last else { return true };
        if self.every_frame || self.burst_until.is_some_and(|t| now < t) {
            return true;
        }
        if thumb.moved_from(last_thumb) {
            self.burst_until = Some(now + BURST);
            return true;
        }
        let every = if seen.face.is_some() || self.present { PRESENT_EVERY } else { EMPTY_EVERY };
        // A little slack for frame timing jitter.
        now.duration_since(*at) + Duration::from_millis(40) >= every
    }

    /// Record an analysis of `thumb`'s frame; `present` = someone is present after it.
    pub fn analysed(&mut self, now: Instant, thumb: Thumb, seen: Seen, present: bool) {
        let changed = match &self.last {
            None => seen.face.is_some(),
            Some((_, _, prev)) => {
                prev.name != seen.name
                    || match (prev.face, seen.face) {
                        (Some(a), Some(b)) => (a[0] - b[0]).abs().max((a[1] - b[1]).abs()) > FACE_MOVE,
                        (a, b) => a.is_some() != b.is_some(),
                    }
            }
        };
        let maybe_exit = present && seen.face.is_none();
        if changed || maybe_exit {
            self.burst_until = Some(now + BURST);
        }
        self.present = present;
        self.last = Some((now, thumb, seen));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn frame(v: u8) -> Frame {
        Frame::new(64, 36, vec![v; 64 * 36 * 3]).unwrap()
    }

    fn nobody() -> Seen {
        Seen { face: None, name: None }
    }

    fn brandon(x: f32) -> Seen {
        Seen { face: Some([x, 0.4]), name: Some("Brandon".into()) }
    }

    /// Frames at 5 fps for `secs`; returns how many were analysed.
    fn run(c: &mut Cadence, t0: Instant, secs: f64, thumb: &Thumb, seen: impl Fn(usize) -> Seen, present: bool) -> usize {
        let mut n = 0;
        for i in 0..(secs * 5.0).round() as usize {
            let now = t0 + Duration::from_millis(200 * i as u64);
            if c.due(now, thumb) {
                c.analysed(now, thumb.clone(), seen(i), present);
                n += 1;
            }
        }
        n
    }

    #[test]
    fn thumbs_see_motion_not_noise() {
        let a = Thumb::of(&frame(100));
        assert!(!a.moved_from(&Thumb::of(&frame(106))), "a uniform shift under the block threshold");
        assert!(a.moved_from(&Thumb::of(&frame(140))));
        let mut half = frame(100);
        half.rgb[..64 * 6 * 3].fill(200); // the top 6 of 36 rows: a sixth of the blocks
        assert!(a.moved_from(&Thumb::of(&half)));
    }

    #[test]
    fn quiet_scenes_slow_down_and_changes_burst() {
        let t0 = Instant::now();
        let still = Thumb::of(&frame(100));
        let mut c = Cadence::new(5.0);
        // Nobody, nothing moving: 1 fps after the first frame.
        assert_eq!(run(&mut c, t0, 10.0, &still, |_| nobody(), false), 10);
        // Someone sitting still: 2 fps (after the 2 s burst their arrival starts).
        let t1 = t0 + Duration::from_secs(10);
        c.analysed(t1, still.clone(), brandon(0.5), true);
        let n = run(&mut c, t1 + Duration::from_millis(200), 10.0, &still, |_| brandon(0.5), true);
        assert!((20..=26).contains(&n), "{n}: 2 s burst at 5 fps, then ~2 fps");
        // Their face missing while present: every frame (exit timing at the full rate).
        let t2 = t1 + Duration::from_secs(11);
        assert_eq!(run(&mut c, t2, 2.0, &still, |_| nobody(), true), 10);
        // Motion in the frame: analysed at once, then every frame for the burst.
        let t3 = t2 + Duration::from_secs(10);
        let mut c = Cadence::new(5.0);
        c.analysed(t3, still.clone(), nobody(), false);
        let moved = Thumb::of(&frame(180));
        assert!(c.due(t3 + Duration::from_millis(200), &moved));
        c.analysed(t3 + Duration::from_millis(200), moved.clone(), nobody(), false);
        assert!(c.due(t3 + Duration::from_millis(400), &moved), "burst");
    }

    #[test]
    fn a_moving_face_bursts_and_fps_zero_analyses_everything() {
        let t0 = Instant::now();
        let still = Thumb::of(&frame(100));
        let mut c = Cadence::new(5.0);
        c.analysed(t0, still.clone(), brandon(0.5), true);
        run(&mut c, t0 + Duration::from_millis(200), 3.0, &still, |_| brandon(0.5), true);
        let t1 = t0 + Duration::from_secs(4);
        c.analysed(t1, still.clone(), brandon(0.56), true);
        assert!(c.due(t1 + Duration::from_millis(200), &still), "face moved: burst");
        let mut every = Cadence::new(0.0);
        assert_eq!(run(&mut every, t0, 4.0, &still, |_| nobody(), false), 20);
    }
}

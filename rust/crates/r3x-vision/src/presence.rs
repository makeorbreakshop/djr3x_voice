//! Who is in front of the camera, and when a scene description is worth paying for
//! (`vision_service.py` `_handle_person_detection`), as a pure state machine over
//! (time, recognition) so it tests without a camera or a clock.
//!
//! Preserved: an exit only after `exit_frames` consecutive frames without the person (10 at
//! 5 fps = 2 s); a capture on the first frame, on a new person unless they left < 120 s ago
//! (re-entry grace) or were captured < 300 s ago (per-person cooldown), on an exit after > 30 s
//! present, and when the last capture is > 300 s old.
//! Differences: CantinaOS declared but never applied its 60 s minimum between automatic
//! captures; it is applied here (startup excepted). A *different* person replacing the current
//! one now exits the old person first (CantinaOS overwrote them, so memory never closed the
//! visit).

use std::collections::HashMap;

#[derive(Debug, Clone, PartialEq)]
pub struct PresenceConfig {
    pub exit_frames: u32,
    pub reentry_grace_s: f64,
    pub person_scene_cooldown_s: f64,
    pub staleness_s: f64,
    pub min_scene_interval_s: f64,
    pub exit_capture_after_s: f64,
    /// Automatic captures at all (on-demand `analyze_scene` is unaffected).
    pub scenes: bool,
}

impl Default for PresenceConfig {
    fn default() -> Self {
        Self {
            exit_frames: 10,
            reentry_grace_s: 120.0,
            person_scene_cooldown_s: 300.0,
            staleness_s: 300.0,
            min_scene_interval_s: 60.0,
            exit_capture_after_s: 30.0,
            scenes: true,
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum Transition {
    Detected { name: String, confidence: f32 },
    Exited { name: String, duration_s: f64 },
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct Step {
    pub transitions: Vec<Transition>,
    /// Capture a scene now, for this reason.
    pub capture: Option<String>,
}

#[derive(Debug, Default)]
pub struct Presence {
    cfg: PresenceConfig,
    current: Option<(String, f32, f64)>,
    missing: u32,
    frames: u64,
    last_capture: Option<f64>,
    person_capture: HashMap<String, f64>,
    person_exit: HashMap<String, f64>,
}

impl Presence {
    pub fn new(cfg: PresenceConfig) -> Self {
        Self { cfg, ..Default::default() }
    }

    /// The person present now and the confidence they were detected with.
    pub fn current(&self) -> Option<(&str, f32)> {
        self.current.as_ref().map(|(n, c, _)| (n.as_str(), *c))
    }

    /// One analysed frame at `now` (seconds, any monotonic origin).
    pub fn observe(&mut self, now: f64, seen: Option<(&str, f32)>) -> Step {
        self.frames += 1;
        let mut step = Step::default();
        let mut want: Option<String> = None;
        match seen {
            Some((name, conf)) => {
                self.missing = 0;
                match &mut self.current {
                    Some((n, c, _)) if n == name => {
                        if (conf - *c).abs() > 0.1 {
                            *c = conf;
                        }
                    }
                    _ => {
                        let old = self.current.take();
                        if let Some((o, _, t0)) = &old {
                            self.person_exit.insert(o.clone(), now);
                            step.transitions.push(Transition::Exited { name: o.clone(), duration_s: now - t0 });
                        }
                        step.transitions.push(Transition::Detected { name: name.into(), confidence: conf });
                        self.current = Some((name.into(), conf, now));
                        let since_exit = self.person_exit.get(name).map_or(f64::INFINITY, |t| now - t);
                        let since_capture = self.person_capture.get(name).map_or(f64::INFINITY, |t| now - t);
                        if since_exit < self.cfg.reentry_grace_s {
                            tracing::info!("skipping scene capture for {name} re-entry ({since_exit:.1}s after leaving)");
                        } else if since_capture > self.cfg.person_scene_cooldown_s {
                            let from = old.as_ref().map_or("none", |o| o.0.as_str());
                            want = Some(format!("person_changed_from_{from}_to_{name}"));
                        }
                    }
                }
            }
            None => {
                if let Some((name, _, t0)) = &self.current {
                    self.missing += 1;
                    if self.missing >= self.cfg.exit_frames {
                        let (name, duration_s) = (name.clone(), now - t0);
                        self.person_exit.insert(name.clone(), now);
                        if duration_s > self.cfg.exit_capture_after_s {
                            want = Some(format!("person_exited_{name}_after_{duration_s:.0}s"));
                        }
                        step.transitions.push(Transition::Exited { name, duration_s });
                        self.current = None;
                        self.missing = 0;
                    }
                }
            }
        }
        if self.frames == 1 {
            want = Some("startup_first_frame".into());
        } else if self.last_capture.is_some_and(|t| now - t > self.cfg.staleness_s) {
            want = Some("scene_staleness_exceeded".into());
        }
        let startup = self.frames == 1;
        let spaced = self.last_capture.is_none_or(|t| now - t >= self.cfg.min_scene_interval_s);
        if self.cfg.scenes && (startup || spaced) {
            step.capture = want;
        }
        step
    }

    /// A scene was captured at `now` with `person` present. Marked when the capture is started,
    /// so a slow Claude call cannot trigger a second one.
    pub fn captured(&mut self, now: f64, person: Option<&str>) {
        self.last_capture = Some(now);
        if let Some(p) = person {
            self.person_capture.insert(p.into(), now);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn run(p: &mut Presence, t: &mut f64, n: usize, seen: Option<(&str, f32)>) -> Vec<Step> {
        (0..n)
            .map(|_| {
                *t += 0.2;
                let s = p.observe(*t, seen);
                if s.capture.is_some() {
                    p.captured(*t, p.current().map(|c| c.0.to_string()).as_deref());
                }
                s
            })
            .collect()
    }

    fn transitions(steps: &[Step]) -> Vec<Transition> {
        steps.iter().flat_map(|s| s.transitions.clone()).collect()
    }

    #[test]
    fn exit_needs_consecutive_empty_frames_and_flicker_is_absorbed() {
        let mut p = Presence::new(PresenceConfig::default());
        let mut t = 0.0;
        let s = run(&mut p, &mut t, 1, Some(("Brandon", 0.7)));
        assert_eq!(s[0].transitions, [Transition::Detected { name: "Brandon".into(), confidence: 0.7 }]);
        assert_eq!(s[0].capture.as_deref(), Some("startup_first_frame"));
        // 9 empty frames, then seen again: no exit, no second detection.
        assert!(transitions(&run(&mut p, &mut t, 9, None)).is_empty());
        assert!(transitions(&run(&mut p, &mut t, 3, Some(("Brandon", 0.72)))).is_empty());
        // 10 consecutive empty frames: exactly one exit, on the 10th.
        let s = run(&mut p, &mut t, 10, None);
        assert!(s[..9].iter().all(|s| s.transitions.is_empty()));
        assert!(matches!(&s[9].transitions[..], [Transition::Exited { name, .. }] if name == "Brandon"));
        assert!(p.current().is_none());
    }

    #[test]
    fn capture_grace_cooldown_and_spacing() {
        let mut p = Presence::new(PresenceConfig::default());
        let mut t = 0.0;
        run(&mut p, &mut t, 1, None); // startup capture at t=0.2
        // Brandon arrives 100 s later: new person, but < 60 s spacing? No: 100 s > 60 s.
        t = 100.0;
        let s = run(&mut p, &mut t, 1, Some(("Brandon", 0.7)));
        assert_eq!(s[0].capture.as_deref(), Some("person_changed_from_none_to_Brandon"));
        // Leaves (present < 30 s: no exit capture) and returns within the 120 s grace.
        let s = run(&mut p, &mut t, 10, None);
        assert!(s.iter().all(|s| s.capture.is_none()));
        let s = run(&mut p, &mut t, 1, Some(("Brandon", 0.7)));
        assert!(matches!(&s[0].transitions[..], [Transition::Detected { .. }]));
        assert!(s[0].capture.is_none());
        // Later than the grace but inside the 300 s per-person cooldown: still no capture.
        run(&mut p, &mut t, 10, None);
        t += 150.0;
        assert!(run(&mut p, &mut t, 1, Some(("Brandon", 0.7)))[0].capture.is_none());
        // Staleness: > 300 s since the last capture fires, once.
        t = 100.2 + 301.0;
        let s = run(&mut p, &mut t, 2, Some(("Brandon", 0.7)));
        assert_eq!(s[0].capture.as_deref(), Some("scene_staleness_exceeded"));
        assert!(s[1].capture.is_none());
    }

    #[test]
    fn a_different_person_exits_the_old_one_first() {
        let mut p = Presence::new(PresenceConfig { scenes: false, ..Default::default() });
        let mut t = 0.0;
        let s = run(&mut p, &mut t, 1, Some(("A", 0.6)));
        assert!(s[0].capture.is_none(), "scenes off");
        let s = run(&mut p, &mut t, 1, Some(("B", 0.6)));
        assert!(matches!(&s[0].transitions[..], [Transition::Exited { name: a, .. }, Transition::Detected { name: b, .. }] if a == "A" && b == "B"));
    }
}

//! The booth's stage-lighting desk (port of `stagelights.ts`): fixture GROUPS, named CUES
//! and a playback engine (crossfades, a beat-clocked chase, per-mode programs). Every
//! update produces one linear-light RGB flux per group. Two rig presets: `disneyland_2019`
//! (default) and `venue_cycle`.

use std::sync::OnceLock;

pub const GROUPS: [&str; 11] = [
    "droid_key",   // amber/cue key on the droid's torso, from the front
    "head_rim_l",  // blue-violet rim on the head, droid's right (-X)
    "head_rim_r",  // ... and left (+X)
    "wall_wash_l", // uplight on the side rock walls
    "wall_wash_r",
    "back_uplight", // behind the machinery panel, up the back wall
    "ceiling_down", // the blue-white spot on the ceiling centre above him
    "fill",         // room fill through the arch
    "rack_wash_l",  // cassette racks
    "rack_wash_r",
    "practicals", // readouts, pedestal ring, deck glow
];
const KEY: usize = 0;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum LightMode {
    Idle,
    Music,
    Dj,
    Speaking,
    Off,
}

impl LightMode {
    pub const ALL: [LightMode; 5] = [
        LightMode::Idle,
        LightMode::Music,
        LightMode::Dj,
        LightMode::Speaking,
        LightMode::Off,
    ];
    pub fn parse(s: &str) -> Option<LightMode> {
        LightMode::ALL.into_iter().find(|m| m.as_str() == s)
    }
    pub fn as_str(self) -> &'static str {
        match self {
            LightMode::Idle => "idle",
            LightMode::Music => "music",
            LightMode::Dj => "dj",
            LightMode::Speaking => "speaking",
            LightMode::Off => "off",
        }
    }
}

/// Linear-light RGB.
pub type Rgb = [f64; 3];
/// One flux per [`GROUPS`] entry.
pub type Output = [Rgb; 11];

/// A group's setting in a cue: sRGB hex colour and a dimmer (1 = the fixture's nominal level).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Level {
    pub color: u32,
    pub level: f64,
}

/// A cue: (group or alias, level) pairs, applied in order.
pub type CueDef = Vec<(&'static str, Level)>;

#[derive(Clone, Debug, PartialEq)]
pub struct ModeProgram {
    /// Cues to run, in order (a single cue just holds). None: hold whatever is up.
    pub list: Option<Vec<&'static str>>,
    /// Step every `bars` bars of the beat clock (music), or every `hold_s` seconds (idle).
    pub bars: Option<f64>,
    pub hold_s: Option<f64>,
    /// Crossfade time into each cue (s).
    pub fade_s: f64,
    /// Multiplier on the droid key.
    pub key_lift: Option<f64>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct RigPreset {
    pub name: &'static str,
    /// Levels every cue starts from; a cue overrides only what it names.
    pub base: CueDef,
    pub cues: Vec<(&'static str, CueDef)>,
    /// Programs for idle, music, dj, speaking, off (in [`LightMode::ALL`] order).
    pub modes: [ModeProgram; 5],
    pub initial: &'static str,
}

impl RigPreset {
    pub fn cue(&self, name: &str) -> Option<&CueDef> {
        self.cues.iter().find(|(n, _)| *n == name).map(|(_, c)| c)
    }
    pub fn mode(&self, m: LightMode) -> &ModeProgram {
        &self.modes[m as usize]
    }
}

fn alias(k: &str) -> &'static [&'static str] {
    match k {
        "room" => &["wall_wash_l", "wall_wash_r", "back_uplight"],
        "walls" => &["wall_wash_l", "wall_wash_r"],
        "rims" => &["head_rim_l", "head_rim_r"],
        "racks" => &["rack_wash_l", "rack_wash_r"],
        _ => &[],
    }
}

const fn l(color: u32, level: f64) -> Level {
    Level { color, level }
}

fn prog(
    list: Option<&[&'static str]>,
    bars: Option<f64>,
    hold_s: Option<f64>,
    fade_s: f64,
    key_lift: Option<f64>,
) -> ModeProgram {
    ModeProgram {
        list: list.map(<[_]>::to_vec),
        bars,
        hold_s,
        fade_s,
        key_lift,
    }
}

fn build_rigs() -> Vec<RigPreset> {
    let dl = RigPreset {
        name: "disneyland_2019",
        base: vec![
            ("room", l(0xff90a0, 1.0)),
            ("ceiling_down", l(0x9ea8ff, 2.2)),
            ("fill", l(0x8a86ff, 1.0)),
            ("racks", l(0xff9a70, 0.35)),
            ("practicals", l(0xffffff, 1.0)),
            ("droid_key", l(0xffc060, 0.0)),
            ("rims", l(0x6b63ff, 0.0)),
        ],
        cues: vec![
            (
                "song",
                vec![("droid_key", l(0xffc060, 1.0)), ("rims", l(0x6b63ff, 1.0))],
            ),
            (
                "song_dim",
                vec![("droid_key", l(0xffa452, 0.6)), ("rims", l(0x6b63ff, 0.9))],
            ),
            (
                "song_gold",
                vec![("droid_key", l(0xffc25a, 1.1)), ("rims", l(0x7a6cff, 1.0))],
            ),
            (
                "interlude",
                vec![
                    ("room", l(0xff90a0, 1.25)),
                    ("ceiling_down", l(0xa9b4ff, 1.2)),
                ],
            ),
            (
                "red_flash",
                vec![
                    ("room", l(0xff2a3c, 1.4)),
                    ("droid_key", l(0xff5a3a, 0.5)),
                    ("rims", l(0x6b63ff, 0.6)),
                    ("ceiling_down", l(0xa9b4ff, 0.4)),
                ],
            ),
            (
                "blackout",
                vec![
                    ("room", l(0xff90a0, 0.15)),
                    ("ceiling_down", l(0xa9b4ff, 0.1)),
                    ("fill", l(0x8a86ff, 0.3)),
                    ("racks", l(0xff9a70, 0.1)),
                ],
            ),
        ],
        modes: [
            prog(Some(&["interlude"]), None, None, 2.5, None),
            prog(
                Some(&["song", "song", "song_gold", "song", "song_dim"]),
                Some(8.0),
                None,
                1.5,
                None,
            ),
            prog(
                Some(&["song", "song_gold", "song", "song_dim"]),
                Some(4.0),
                None,
                1.0,
                None,
            ),
            prog(Some(&["song"]), None, None, 0.6, Some(1.0)),
            prog(Some(&["blackout"]), None, None, 1.5, None),
        ],
        initial: "song",
    };
    let vc = RigPreset {
        name: "venue_cycle",
        base: vec![
            ("walls", l(0xfff0dc, 0.55)),
            ("back_uplight", l(0xfff0dc, 0.5)),
            ("ceiling_down", l(0xffffff, 0.25)),
            ("fill", l(0xc8ccff, 0.5)),
            ("racks", l(0xffffff, 0.6)),
            ("practicals", l(0xffffff, 1.0)),
            ("droid_key", l(0xfff4e6, 0.6)),
            ("rims", l(0xd8dcff, 0.0)),
        ],
        cues: vec![
            (
                "yellow_green",
                vec![
                    ("walls", l(0xf2f0a0, 0.6)),
                    ("back_uplight", l(0xd8f040, 0.8)),
                    ("racks", l(0xd4f028, 1.3)),
                    ("droid_key", l(0xf6f050, 0.7)),
                ],
            ),
            (
                "blue_white",
                vec![
                    ("walls", l(0x9aa6ff, 0.9)),
                    ("back_uplight", l(0xb8c0ff, 0.7)),
                    ("ceiling_down", l(0xb8c0ff, 0.6)),
                    ("rack_wash_l", l(0xff6a22, 1.2)),
                    ("rack_wash_r", l(0xe0e040, 1.0)),
                    ("droid_key", l(0xffd860, 0.8)),
                ],
            ),
            (
                "amber",
                vec![
                    ("walls", l(0xe8e0ff, 0.55)),
                    ("back_uplight", l(0xffb040, 0.8)),
                    ("racks", l(0xffb030, 1.2)),
                    ("droid_key", l(0xffc048, 1.0)),
                ],
            ),
            (
                "orange_red",
                vec![
                    ("walls", l(0xfff0e8, 0.6)),
                    ("back_uplight", l(0xff7a28, 0.8)),
                    ("racks", l(0xff5a18, 1.2)),
                    ("droid_key", l(0xffa040, 1.1)),
                ],
            ),
            (
                "cool_white",
                vec![
                    ("walls", l(0xfff0dc, 0.6)),
                    ("back_uplight", l(0xf0f0e0, 0.5)),
                    ("racks", l(0xd8f040, 1.0)),
                    ("droid_key", l(0xf2f4ff, 0.6)),
                    ("rims", l(0xd8dcff, 0.3)),
                ],
            ),
            (
                "blackout",
                vec![
                    ("walls", l(0xfff0dc, 0.08)),
                    ("back_uplight", l(0xfff0dc, 0.05)),
                    ("ceiling_down", l(0xffffff, 0.0)),
                    ("fill", l(0xc8ccff, 0.2)),
                    ("racks", l(0xffffff, 0.05)),
                    ("droid_key", l(0xffffff, 0.0)),
                ],
            ),
        ],
        modes: [
            prog(
                Some(&["cool_white", "amber", "cool_white", "yellow_green"]),
                None,
                Some(7.0),
                2.5,
                None,
            ),
            prog(
                Some(&[
                    "yellow_green",
                    "blue_white",
                    "amber",
                    "orange_red",
                    "yellow_green",
                    "cool_white",
                ]),
                Some(2.0),
                None,
                0.3,
                None,
            ),
            prog(
                Some(&[
                    "yellow_green",
                    "blue_white",
                    "amber",
                    "orange_red",
                    "cool_white",
                ]),
                Some(2.0),
                None,
                0.25,
                None,
            ),
            prog(None, None, None, 0.4, Some(1.2)),
            prog(Some(&["blackout"]), None, None, 1.0, None),
        ],
        initial: "cool_white",
    };
    vec![dl, vc]
}

/// Both rig presets, `disneyland_2019` first.
pub fn rigs() -> &'static [RigPreset] {
    static R: OnceLock<Vec<RigPreset>> = OnceLock::new();
    R.get_or_init(build_rigs)
}

pub fn rig(name: &str) -> Option<&'static RigPreset> {
    rigs().iter().find(|r| r.name == name)
}

pub const DEFAULT_RIG: &str = "disneyland_2019";

fn srgb_to_linear(c: f64) -> f64 {
    if c <= 0.04045 {
        c / 12.92
    } else {
        libm::pow((c + 0.055) / 1.055, 2.4)
    }
}

/// A Level as linear flux (colour x dimmer).
pub fn flux(l: Level) -> Rgb {
    let ch = |s: u32| srgb_to_linear(f64::from((l.color >> s) & 255) / 255.0) * l.level;
    [ch(16), ch(8), ch(0)]
}

/// Resolve a cue against its rig's base into flux for every group.
pub fn resolve_cue(rig: &RigPreset, name: &str) -> Option<Output> {
    let def = rig.cue(name)?;
    let mut out = [[0.0; 3]; 11];
    for (k, lvl) in rig.base.iter().chain(def) {
        let a = alias(k);
        let groups: &[&str] = if a.is_empty() {
            std::slice::from_ref(k)
        } else {
            a
        };
        for g in groups {
            if let Some(i) = GROUPS.iter().position(|x| x == g) {
                out[i] = flux(*lvl);
            }
        }
    }
    Some(out)
}

#[derive(Clone, Debug)]
pub struct StageLights {
    /// Current output: linear flux per group. Read after update().
    pub out: Output,
    rig: &'static RigPreset,
    mode: Option<LightMode>,
    cue: &'static str,
    bpm: f64,
    /// Output before the key lift (what fades start from).
    raw: Output,
    from: Output,
    to: Output,
    fade_t: f64,
    fade_s: f64,
    list_idx: usize,
    /// Beats since the last step (music) or seconds since the last step (idle).
    phase: f64,
    /// Seconds into the current beat, for the internal beat clock.
    beat_clock: f64,
    external_beats: u32,
    last_external_at: f64,
    time: f64,
    key_lift: f64,
    key_lift_target: f64,
    /// A show's cue hold (s left): the mode program is paused until it runs out.
    hold_left: f64,
    pre_hold_cue: Option<&'static str>,
    /// Bumped whenever the output changed.
    pub version: u64,
}

impl Default for StageLights {
    fn default() -> Self {
        Self::new(None, None, None)
    }
}

impl StageLights {
    pub fn new(rig_name: Option<&str>, cue: Option<&str>, bpm: Option<f64>) -> Self {
        let rig = rig_name.and_then(rig).unwrap_or(&rigs()[0]);
        let cue = cue
            .and_then(|c| rig.cues.iter().find(|(n, _)| *n == c))
            .map_or(rig.initial, |(n, _)| *n);
        let c = resolve_cue(rig, cue).expect("rig initial cue exists");
        let mut s = StageLights {
            out: c,
            rig,
            mode: None,
            cue,
            bpm: 120.0,
            raw: c,
            from: c,
            to: c,
            fade_t: 0.0,
            fade_s: 0.0,
            list_idx: 0,
            phase: 0.0,
            beat_clock: 0.0,
            external_beats: 0,
            last_external_at: f64::NEG_INFINITY,
            time: 0.0,
            key_lift: 1.0,
            key_lift_target: 1.0,
            hold_left: 0.0,
            pre_hold_cue: None,
            version: 0,
        };
        if let Some(b) = bpm.filter(|b| *b != 0.0) {
            s.set_bpm(b);
        }
        s
    }

    pub fn rig_preset(&self) -> &'static str {
        self.rig.name
    }
    pub fn mode(&self) -> Option<LightMode> {
        self.mode
    }
    pub fn cue(&self) -> &'static str {
        self.cue
    }
    pub fn bpm(&self) -> f64 {
        self.bpm
    }
    /// Fade progress 0..1 (1 = arrived).
    pub fn fade(&self) -> f64 {
        if self.fade_s > 0.0 {
            (self.fade_t / self.fade_s).min(1.0)
        } else {
            1.0
        }
    }
    /// Seconds left on a show's cue hold (0 = the mode program is running).
    pub fn holding(&self) -> f64 {
        self.hold_left
    }
    pub fn group(&self, name: &str) -> Option<Rgb> {
        GROUPS.iter().position(|g| *g == name).map(|i| self.out[i])
    }

    /// Switch era. Keeps the mode; starts the new rig at its initial cue (or the mode's first).
    pub fn set_rig(&mut self, name: &str, fade_s: f64) {
        let Some(r) = rig(name).filter(|r| r.name != self.rig.name) else {
            return;
        };
        self.rig = r;
        match self.mode.take() {
            Some(m) => self.set_mode(m, Some(fade_s)),
            None => {
                self.go_cue(r.initial, fade_s);
            }
        }
    }

    pub fn set_bpm(&mut self, n: f64) {
        if n.is_finite() && n > 0.0 {
            self.bpm = n.clamp(30.0, 300.0);
        }
    }

    /// An external beat: re-phases the internal clock to it, so a live beat source and the
    /// BPM estimate never double-count.
    pub fn beat(&mut self) {
        self.external_beats += 1;
        self.last_external_at = self.time;
        self.beat_clock = 0.0;
    }

    /// Change mode: starts that mode's program (speaking holds the current cue by default).
    pub fn set_mode(&mut self, mode: LightMode, fade_s: Option<f64>) {
        if Some(mode) == self.mode {
            return;
        }
        self.mode = Some(mode);
        let p = self.rig.mode(mode);
        self.key_lift_target = p.key_lift.unwrap_or(1.0);
        self.phase = 0.0;
        if let Some(list) = p.list.as_ref().filter(|l| !l.is_empty()) {
            // Re-enter a list at the cue already up if it is in it (no jump when music resumes).
            self.list_idx = list.iter().position(|c| *c == self.cue).unwrap_or(0);
            // During a show's hold the new program takes over when the hold ends.
            if self.hold_left == 0.0 {
                self.go_cue(list[self.list_idx], fade_s.unwrap_or(p.fade_s));
            }
        }
    }

    /// A show's `lights` action (SPEC stage.lights): fade to a cue. With hold_s > 0 the mode
    /// program is paused for that long and then resumes. False when this rig has no such cue.
    pub fn show_cue(&mut self, name: &str, fade_s: f64, hold_s: f64) -> bool {
        if self.rig.cue(name).is_none() {
            return false;
        }
        if hold_s > 0.0 {
            if self.hold_left == 0.0 {
                self.pre_hold_cue = Some(self.cue);
            }
            self.hold_left = hold_s;
        }
        self.go_cue(name, fade_s)
    }

    fn resume_program(&mut self) {
        let p = self.mode.map(|m| self.rig.mode(m));
        self.phase = 0.0;
        match (
            p.and_then(|p| p.list.as_ref().filter(|l| !l.is_empty())),
            self.pre_hold_cue,
        ) {
            (Some(list), _) => {
                let c = list[self.list_idx % list.len()];
                self.go_cue(c, p.map_or(0.5, |p| p.fade_s));
            }
            (None, Some(pre)) => {
                self.go_cue(pre, p.map_or(0.5, |p| p.fade_s));
            }
            _ => {}
        }
        self.pre_hold_cue = None;
    }

    /// Fade to a named cue over fade_s seconds (0 = snap). False for an unknown cue.
    pub fn go_cue(&mut self, name: &str, fade_s: f64) -> bool {
        let Some((n, _)) = self.rig.cues.iter().find(|(n, _)| *n == name) else {
            return false;
        };
        let target = resolve_cue(self.rig, n).expect("cue exists");
        self.from = self.raw;
        self.to = target;
        self.cue = n;
        self.fade_s = fade_s.max(0.0);
        self.fade_t = 0.0;
        if self.fade_s == 0.0 {
            self.apply(1.0);
        }
        true
    }

    /// Advance the console by dt seconds and recompute the output.
    pub fn update(&mut self, dt: f64) {
        if dt.is_nan() || dt <= 0.0 {
            return;
        }
        self.time += dt;
        let p = self.mode.map(|m| self.rig.mode(m));

        // ---- a show's cue hold pauses the program
        if self.hold_left > 0.0 {
            self.hold_left = (self.hold_left - dt).max(0.0);
            if self.hold_left == 0.0 {
                self.resume_program();
            }
        }

        // ---- program stepping
        let mut stepped = false;
        if self.hold_left > 0.0 {
            self.external_beats = 0;
        } else if let Some((p, list)) =
            p.and_then(|p| p.list.as_ref().filter(|l| l.len() > 1).map(|l| (p, l)))
        {
            let mut steps = 0.0;
            if let Some(bars) = p.bars.filter(|b| *b != 0.0) {
                let beat_s = 60.0 / self.bpm;
                // An external beat source counts beats itself; while it is live (a beat within
                // the last two beat periods) the internal clock only fills gaps between them.
                let mut beats = f64::from(self.external_beats);
                self.external_beats = 0;
                if self.time - self.last_external_at > 2.0 * beat_s {
                    self.beat_clock += dt;
                    beats += (self.beat_clock / beat_s).floor();
                    self.beat_clock %= beat_s;
                }
                self.phase += beats;
                let per = bars * 4.0;
                steps = (self.phase / per).floor();
                self.phase -= steps * per;
            } else if let Some(hold) = p.hold_s.filter(|h| *h != 0.0) {
                self.phase += dt;
                let per = hold + p.fade_s;
                steps = (self.phase / per).floor();
                self.phase -= steps * per;
            }
            if steps > 0.0 {
                self.list_idx = (self.list_idx + steps as usize) % list.len();
                self.go_cue(list[self.list_idx], p.fade_s);
                // Time already spent past the step boundary counts toward the new fade.
                let into = if p.bars.is_some_and(|b| b != 0.0) {
                    self.phase * (60.0 / self.bpm)
                } else {
                    self.phase
                };
                self.fade_t = self.fade_s.min(into);
                stepped = true;
            }
        } else {
            self.external_beats = 0;
        }

        // ---- fade (clamped: never past the target however large dt is)
        if !stepped {
            self.fade_t = self.fade_s.min(self.fade_t + dt);
        }

        // ---- key lift eases in/out (~0.3 s)
        self.key_lift += (self.key_lift_target - self.key_lift) * (1.0 - libm::exp(-dt / 0.3));

        self.apply(self.fade());
    }

    fn apply(&mut self, t: f64) {
        for g in 0..GROUPS.len() {
            let k = if g == KEY { self.key_lift } else { 1.0 };
            for i in 0..3 {
                self.raw[g][i] = self.from[g][i] + (self.to[g][i] - self.from[g][i]) * t;
                self.out[g][i] = self.raw[g][i] * k;
            }
        }
        self.version += 1;
    }
}

//! The operator layer: turns raw pad snapshots into (a) the continuous input the puppeteer
//! reads and (b) discrete [`Action`]s the runtime turns into bus commands. Pure: no bus, no
//! clock of its own (`now` is passed in), so every rule here is unit-tested.
//!
//! **Layers.** The modifiers L1, R1 and R2, held or latched (double tap; a single tap
//! unlatches), pick one of eight layers in `pad.json`. A layer says what the left stick drives
//! (body, hero arm, poker arm, both arms) and what the face buttons and d-pad do.
//!
//! **Fixed in every layer:** right stick = head; L2 = push-to-talk (opens after
//! [`TALK_ARM_S`]); L3 click = pin/unpin the arm the left stick drives (body layers: send the
//! arms home); R3 click = look at guests on/off; L3+R3 = reset everything; Start = freeze;
//! Select tap = cancel, hold = menu; PS hold = motors on/off.
//!
//! **Smooth by construction** (2026-10-01: "make it smooth, not insanely complicated"):
//! - a change of modifiers settles for `chord_grace_s` before the left stick changes hands,
//!   so L1-then-R1 never passes through the L1 layer;
//! - a button press belongs to the layer held when it went down (immediately, no grace), and
//!   finishes there whatever happens to the modifiers after;
//! - the stick takes a part over only once it is brought to where that part is ("pickup"),
//!   so nothing jumps when it changes hands;
//! - a part the stick lets go of eases home (min-jerk) unless it is pinned.
//!
//! **Crane mode** (2026-10-01): an arm layer *latched* (double-tap R1 = poker, L1 = hero,
//! both = both) turns both sticks into that arm's controls, excavator style (ISO): the stick
//! sets a speed and the arm stays where it is left. Poker: left X = around (its ring), left Y
//! = reach in/out, right Y = up/down, in straight lines through the IK. Hero: left X = aim,
//! left Y = raise, right X = twist. Both: left stick = hero, right stick = poker. R2 = grip
//! (by pressure); the free bumper held = creep (30 %), double-tapped = both arms. The head watches the claw. D-pad: tap
//! ←/→ = glide to a saved spot, hold = save it there, ↑ = glide home. A rumble at the edge of
//! reach. Tap the latched bumper to leave: the arm stays where it was put (pinned).
//!
//! Face buttons fire their tap binding on release, or their hold binding once held
//! `tap_hold_s`. How hard a face button is pressed (the DS3 measures it) sets the intensity of
//! what it plays. A button whose tap binding is a held control (`grip`) acts while held.

use r3x_contracts::{PadBinding, PadControls, PadLayer, PadMapping, PadMenuView, StickTarget};
use r3x_performer_core::show::arm_ik::POKER as POKER_IK;
use r3x_performer_core::show::curve::minjerk;
use r3x_performer_core::show::puppeteer::{op_axis, PadState, GAZE_YAW, HERO_AIM, POKER_AIM, POKER_REACH_MM, POKER_SWING};

use crate::ds3::std_btn as b;

/// How long L2 must be held before the mic opens. A tap shorter than this does nothing (no
/// interrupt, no duck, no listening head): stray presses were common (2026-10-01, 10-390 ms
/// presses with no words). Short enough that nobody has started speaking yet.
pub const TALK_ARM_S: f64 = 0.25;
/// An arm the stick lets go of eases home over this long; the body over [`BODY_HOME_S`].
pub const ARM_HOME_S: f64 = 0.8;
pub const BODY_HOME_S: f64 = 0.4;
/// The stick takes over a part once within this of where the part is.
pub const PICKUP: f64 = 0.2;
/// Hero wrist twist and poker reach, per second at full d-pad (range -1..1).
pub const RATE: f64 = 1.2;
/// The stick past this while a modifier is down makes that press "use", not a tap.
const STICK_USED: f64 = 0.3;
/// Stick dead zone (the arms; the puppeteer applies its own to the body and head).
const DEAD: f64 = 0.12;
/// Crane speeds at full stick: ring aim and hero raise / twist (range per second), the poker
/// claw tip (mm per second).
pub const CRANE_RATE: f64 = 0.8;
pub const CRANE_TIP_MM_S: f64 = 120.0;
/// The free bumper held in crane mode: this fraction of the speed.
pub const CREEP: f64 = 0.3;
/// A glide to a saved spot (or home) takes this long.
pub const GLIDE_S: f64 = 1.0;
/// No more than one edge rumble per this.
const EDGE_EVERY_S: f64 = 0.35;

const FACES: [usize; 4] = [b::CROSS, b::CIRCLE, b::SQUARE, b::TRIANGLE];
const DPAD: [usize; 4] = [b::UP, b::RIGHT, b::DOWN, b::LEFT];
const MODS: [usize; 3] = [b::L1, b::R1, b::R2];
const MOD_NAMES: [&str; 3] = ["l1", "r1", "r2"];

const BODY: usize = 0;
const HERO: usize = 1;
const POKER: usize = 2;
const PART_NAMES: [&str; 3] = ["body", "hero", "poker"];

/// Something the operator asked for.
#[derive(Clone, Debug, PartialEq)]
pub enum Action {
    /// The mic opens (`true`, after [`TALK_ARM_S`]) or closes.
    Talk(bool),
    /// An emote slot at an intensity (button pressure).
    Emote { slot: u8, intensity: f64 },
    Play { id: String, intensity: f64 },
    Sfx(String),
    /// A menu action by id (from the menu, or a layer's `act` binding).
    Menu(String),
    /// Select tap: end the gesture and show layers.
    Cancel,
    /// L3+R3: stop shows, arms home, layers unlatched.
    Reset,
    /// R3: look at guests (vision gaze) on/off.
    LookToggle,
    ToggleFreeze,
    ToggleArm,
}

/// Feedback the runtime turns into rumble.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Cue {
    /// The active layer changed.
    Layer,
    /// A modifier latched (`true`) or unlatched.
    Latch(bool),
    /// An arm pinned (`true`) or unpinned.
    Pin(bool),
    /// The crane pushed against the edge of reach.
    Edge,
    /// A crane spot saved.
    Saved,
}

/// Which arms a crane drives.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Crane {
    Hero,
    Poker,
    Both,
}

impl Crane {
    fn arms(self) -> &'static [usize] {
        match self {
            Crane::Hero => &[HERO],
            Crane::Poker => &[POKER],
            Crane::Both => &[HERO, POKER],
        }
    }
    fn index(self) -> usize {
        self as usize
    }
    fn name(self) -> &'static str {
        ["hero", "poker", "both"][self as usize]
    }
}

fn dead(x: f64) -> f64 {
    if x.abs() < DEAD {
        0.0
    } else {
        (x - x.signum() * DEAD) / (1.0 - DEAD)
    }
}

/// One menu entry; the runtime rebuilds the tree from live state every step, so labels can
/// say "DJ mode: on".
#[derive(Clone, Debug, PartialEq)]
pub struct MenuItem {
    pub label: String,
    /// Leaf: the id [`Action::Menu`] carries. Ignored when `children` is non-empty.
    pub id: String,
    pub children: Vec<MenuItem>,
}

impl MenuItem {
    pub fn leaf(label: impl Into<String>, id: impl Into<String>) -> Self {
        MenuItem { label: label.into(), id: id.into(), children: vec![] }
    }
    pub fn sub(label: impl Into<String>, children: Vec<MenuItem>) -> Self {
        MenuItem { label: label.into(), id: String::new(), children }
    }
}

/// One step's result.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Step {
    /// What the puppeteer reads: an operator feed ([`op_axis`] layout, no buttons).
    pub puppet: PadState,
    pub actions: Vec<Action>,
    pub cues: Vec<Cue>,
    pub view: PadControls,
}

/// Modifier bits: l1 = 1, r1 = 2, r2 = 4.
type Mask = u8;

#[derive(Clone, Copy, Debug, Default)]
struct Mod {
    down_at: Option<f64>,
    /// Nothing else was pressed (and the stick stayed put) while it was down.
    clean: bool,
    last_tap: Option<f64>,
}

#[derive(Clone, Copy, Debug)]
struct FacePress {
    at: f64,
    mask: Mask,
    peak: f64,
}

/// Something the left stick drives: a 2-D position (body: stick x/y; arm: aim/raise) and,
/// for the arms, the d-pad extra (hero twist / poker reach) and the claw.
#[derive(Clone, Copy, Debug, Default)]
struct Part {
    pos: [f64; 2],
    extra: f64,
    grip: f64,
    grip_keep: bool,
    pinned: bool,
    /// Waiting for the stick to come to `pos` before following it.
    pickup: bool,
    /// Easing home: started at, from (pos, extra).
    home: Option<(f64, [f64; 3])>,
    /// A crane glide: started at, from, to (pos, extra).
    glide: Option<(f64, [f64; 3], [f64; 3])>,
}

impl Part {
    fn state(&self) -> [f64; 3] {
        [self.pos[0], self.pos[1], self.extra]
    }
    fn set(&mut self, s: [f64; 3]) {
        (self.pos, self.extra) = ([s[0], s[1]], s[2]);
    }
    fn at_home(&self) -> bool {
        self.pos == [0.0, 0.0] && self.extra == 0.0
    }
    fn send_home(&mut self, now: f64) {
        self.pinned = false;
        if !self.at_home() {
            self.home = Some((now, [self.pos[0], self.pos[1], self.extra]));
        }
    }
}

pub struct Controls {
    map: PadMapping,
    prev: Vec<bool>,
    last_now: Option<f64>,
    face: [Option<FacePress>; 4],
    /// The layer each held d-pad button was pressed in.
    dpad: [Option<Mask>; 4],
    ps_down: Option<f64>,
    /// When L2 went down, and whether the mic is open for this hold.
    talk_down: Option<(f64, bool)>,
    /// Select: when it went down, and whether it already did its thing.
    select_down: Option<(f64, bool)>,
    /// L3/R3 down; `chord` once both were down together (then neither fires alone).
    sticks_down: [bool; 2],
    chord: bool,
    menu: Option<(Vec<usize>, usize)>,
    mods: [Mod; 3],
    latched: [bool; 3],
    /// The layer the left stick follows, and a change waiting out the grace.
    active: Mask,
    pending: Option<(Mask, f64)>,
    parts: [Part; 3],
    lift: f64,
    visor: f64,
    roll: f64,
    /// Crane d-pad presses (tap = go, hold = save), when each went down.
    crane_dpad: [Option<f64>; 4],
    /// Saved spots per crane (hero, poker, both) and slot (←, →): every arm's state.
    spots: [[Option<[[f64; 3]; 3]>; 2]; 3],
    edge_at: f64,
    pub armed: bool,
}

impl Controls {
    pub fn new(map: PadMapping) -> Self {
        Controls {
            map,
            prev: vec![],
            last_now: None,
            face: [None; 4],
            dpad: [None; 4],
            ps_down: None,
            talk_down: None,
            select_down: None,
            sticks_down: [false; 2],
            chord: false,
            menu: None,
            mods: [Mod::default(); 3],
            latched: [false; 3],
            active: 0,
            pending: None,
            parts: [Part::default(); 3],
            lift: 0.0,
            visor: 0.0,
            roll: 0.0,
            crane_dpad: [None; 4],
            spots: [[None; 2]; 3],
            edge_at: f64::NEG_INFINITY,
            armed: true,
        }
    }

    pub fn mapping(&self) -> &PadMapping {
        &self.map
    }

    /// The pad went away: forget everything held, latched or pinned (the runtime stops talk
    /// itself and the puppeteer eases the body home).
    pub fn reset(&mut self) {
        let (armed, spots) = (self.armed, self.spots);
        *self = Controls::new(self.map.clone());
        (self.armed, self.spots) = (armed, spots);
    }

    /// The crane the latched modifiers make, if any: L1 alone = hero, R1 alone = poker, both
    /// = both (R2 latched is a sound layer, no crane).
    pub fn crane(&self) -> Option<Crane> {
        match self.latched {
            [true, false, false] => Some(Crane::Hero),
            [false, true, false] => Some(Crane::Poker),
            [true, true, false] => Some(Crane::Both),
            _ => None,
        }
    }

    fn latched_mask(&self) -> Mask {
        (0..3).filter(|&m| self.latched[m]).fold(0, |acc, m| acc | (1 << m))
    }

    /// Unpin both arms and send them home (a menu or `act` binding: `arms.home`).
    pub fn home_arms(&mut self, now: f64) {
        for p in &mut self.parts[HERO..] {
            p.send_home(now);
            p.grip_keep = false;
            p.pickup = true;
        }
    }

    fn layer(&self, mask: Mask) -> (&'static str, &PadLayer) {
        self.map.layers.get(mask & 1 != 0, mask & 2 != 0, mask & 4 != 0)
    }

    /// The arms a layer's stick target owns (grip, pin, home act on these).
    fn arms_of(t: StickTarget) -> &'static [usize] {
        match t {
            StickTarget::Body => &[],
            StickTarget::Hero => &[HERO],
            StickTarget::Poker => &[POKER],
            StickTarget::Arms => &[HERO, POKER],
        }
    }

    fn driven(t: StickTarget) -> &'static [usize] {
        match t {
            StickTarget::Body => &[BODY],
            other => Self::arms_of(other),
        }
    }

    fn intensity(peak: f64) -> f64 {
        0.5 + 0.5 * peak.clamp(0.0, 1.0)
    }

    /// A binding's discrete action (none for an empty one or a control).
    fn bound(bd: &PadBinding, intensity: f64) -> Option<Action> {
        if let Some(id) = &bd.play {
            Some(Action::Play { id: id.clone(), intensity })
        } else if let Some(id) = &bd.sfx {
            Some(Action::Sfx(id.clone()))
        } else if let Some(slot) = bd.emote {
            Some(Action::Emote { slot, intensity })
        } else {
            bd.act.as_ref().map(|a| Action::Menu(a.clone()))
        }
    }

    fn is_held_ctl(bd: &PadBinding) -> bool {
        bd.ctl.as_deref().is_some_and(|c| !matches!(c, "grip_hold" | "pin" | "home"))
    }

    fn is_bound(bd: &PadBinding) -> bool {
        bd.play.is_some() || bd.sfx.is_some() || bd.emote.is_some() || bd.act.is_some() || bd.ctl.is_some()
    }

    /// Fire a binding pressed in layer `mask`: an action, or a one-shot control.
    fn fire(&mut self, bd: &PadBinding, mask: Mask, intensity: f64, now: f64, actions: &mut Vec<Action>, cues: &mut Vec<Cue>) {
        if let Some(a) = Self::bound(bd, intensity) {
            actions.push(a);
            return;
        }
        let arms = Self::arms_of(self.layer(mask).1.stick);
        match bd.ctl.as_deref() {
            Some("grip_hold") => {
                for &i in arms {
                    self.parts[i].grip_keep = !self.parts[i].grip_keep;
                }
            }
            Some("pin") => self.toggle_pin(arms, now, cues),
            Some("home") => {
                for &i in arms {
                    self.parts[i].send_home(now);
                    self.parts[i].grip_keep = false;
                    self.parts[i].pickup = true;
                }
            }
            _ => {}
        }
    }

    /// Pinned = locked where it is: the stick no longer moves it (even in its own layer) and it
    /// does not ease home. Unpinned in its layer, the stick picks it up where it is.
    fn toggle_pin(&mut self, arms: &[usize], now: f64, cues: &mut Vec<Cue>) {
        let Some(&first) = arms.first() else { return };
        let on = !self.parts[first].pinned;
        let driven = Self::driven(self.layer(self.active).1.stick);
        for &i in arms {
            self.parts[i].pinned = on;
            if !on {
                if driven.contains(&i) {
                    self.parts[i].pickup = true;
                } else {
                    self.parts[i].send_home(now);
                }
            }
        }
        cues.push(Cue::Pin(on));
    }

    pub fn step(&mut self, now: f64, pad: &PadState, menu: &[MenuItem]) -> Step {
        let dt = self.last_now.map_or(0.0, |t| (now - t).max(0.0));
        self.last_now = Some(now);
        let down = |i: usize| pad.buttons.get(i).is_some_and(|x| x.0);
        let val = |i: usize| pad.buttons.get(i).map_or(0.0, |x| if x.0 { x.1.max(0.05) } else { 0.0 });
        let prev = std::mem::take(&mut self.prev);
        let was = |i: usize| prev.get(i).copied().unwrap_or(false);
        let pressed = |i: usize| down(i) && !was(i);
        let released = |i: usize| !down(i) && was(i);
        let axis = |i: usize| pad.axes.get(i).copied().unwrap_or(0.0);
        let stick = [axis(0), axis(1)];
        let mut actions = vec![];
        let mut cues = vec![];

        // ---------------------------------------------------------------- modifiers
        let other_pressed = (0..b::COUNT).any(|i| !MODS.contains(&i) && pressed(i));
        let stick_used = stick[0].hypot(stick[1]) > STICK_USED;
        let crane_before = self.crane();
        for (m, &i) in MODS.iter().enumerate() {
            let md = &mut self.mods[m];
            if pressed(i) {
                *md = Mod { down_at: Some(now), clean: true, last_tap: md.last_tap };
            } else if down(i) && (other_pressed || stick_used) {
                md.clean = false;
            }
            if released(i) {
                let tapped = md.clean && md.down_at.is_some_and(|t| now - t < self.map.tap_hold_s);
                md.down_at = None;
                if tapped {
                    if self.latched[m] {
                        // Leaving a crane: the arm stays where it was put.
                        if let Some(c) = crane_before {
                            for &a in c.arms() {
                                self.parts[a].pinned = true;
                                self.parts[a].glide = None;
                            }
                        }
                        self.latched[m] = false;
                        cues.push(Cue::Latch(false));
                    } else if crane_before.is_some() && m == 2 {
                        // In a crane R2 grips: never a latch. (The free bumper creeps when held,
                        // and a double tap of it makes the crane two-armed.)
                    } else if md.last_tap.is_some_and(|t| now - t <= self.map.double_tap_s) {
                        self.latched[m] = true;
                        md.last_tap = None;
                        cues.push(Cue::Latch(true));
                    } else {
                        md.last_tap = Some(now);
                    }
                }
            }
        }
        // In a crane only the latch counts: the other modifiers are its creep and grip.
        let desired: Mask = if self.crane().is_some() {
            self.latched_mask()
        } else {
            (0..3).filter(|&m| down(MODS[m]) || self.latched[m]).fold(0, |acc, m| acc | (1 << m))
        };

        // The stick's layer settles for the grace first.
        if desired == self.active {
            self.pending = None;
        } else {
            let since = match self.pending {
                Some((m, t)) if m == desired => t,
                _ => {
                    self.pending = Some((desired, now));
                    now
                }
            };
            if now - since >= self.map.chord_grace_s {
                self.switch_layer(desired, stick, now);
                cues.push(Cue::Layer);
            }
        }

        // ---------------------------------------------------------------- fixed buttons
        if pressed(b::L2) {
            self.talk_down = Some((now, false));
        }
        match self.talk_down {
            Some((at, false)) if down(b::L2) && now - at >= TALK_ARM_S => {
                self.talk_down = Some((at, true));
                actions.push(Action::Talk(true));
            }
            Some((_, open)) if !down(b::L2) => {
                self.talk_down = None;
                if open {
                    actions.push(Action::Talk(false));
                }
            }
            _ => {}
        }
        if pressed(b::START) {
            actions.push(Action::ToggleFreeze);
        }
        // PS: a hold arms/disarms, once per hold.
        match (down(b::PS), self.ps_down) {
            (true, None) => self.ps_down = Some(now),
            (true, Some(t)) if t.is_finite() && now - t >= self.map.arm_hold_s => {
                self.armed = !self.armed;
                actions.push(Action::ToggleArm);
                self.ps_down = Some(f64::INFINITY);
            }
            (false, _) => self.ps_down = None,
            _ => {}
        }
        // Select: tap = cancel, hold = menu; with the menu open a press closes it.
        if pressed(b::SELECT) {
            if self.menu.is_some() {
                self.menu = None;
                self.select_down = Some((now, true));
            } else {
                self.select_down = Some((now, false));
            }
        }
        match self.select_down {
            Some((t, false)) if down(b::SELECT) && now - t >= self.map.tap_hold_s => {
                self.menu = Some((vec![], 0));
                self.select_down = Some((t, true));
            }
            Some((_, done)) if !down(b::SELECT) => {
                if !done {
                    actions.push(Action::Cancel);
                }
                self.select_down = None;
            }
            _ => {}
        }
        // L3 / R3 fire on release, unless both were down together (= reset).
        for (k, i) in [b::L3, b::R3].into_iter().enumerate() {
            if pressed(i) {
                self.sticks_down[k] = true;
                if self.sticks_down[1 - k] {
                    self.chord = true;
                    self.reset_all(now, &mut actions, &mut cues);
                }
            }
            if released(i) {
                self.sticks_down[k] = false;
                if !self.chord {
                    if k == 0 {
                        let stick_target = self.layer(self.active).1.stick;
                        match Self::arms_of(stick_target) {
                            [] => {
                                for p in &mut self.parts[HERO..] {
                                    p.send_home(now);
                                }
                            }
                            arms => self.toggle_pin(arms, now, &mut cues),
                        }
                    } else {
                        actions.push(Action::LookToggle);
                    }
                }
                if !self.sticks_down[1 - k] {
                    self.chord = false;
                }
            }
        }

        // ---------------------------------------------------------------- face + d-pad
        let menu_open = self.menu.is_some();
        // Held controls this step: (control, layer it was pressed in, pressure).
        let mut held: Vec<(String, Mask, f64)> = vec![];
        if menu_open {
            self.navigate(menu, &pressed, &mut actions);
            // The menu owns the face buttons and d-pad: drop any half-made press.
            self.face = [None; 4];
            self.dpad = [None; 4];
        } else {
            for (k, &i) in FACES.iter().enumerate() {
                if pressed(i) {
                    self.face[k] = Some(FacePress { at: now, mask: desired, peak: val(i) });
                }
                let Some(mut fp) = self.face[k] else { continue };
                fp.peak = fp.peak.max(val(i));
                let lay = self.layer(fp.mask).1;
                let (tap, hold) = (lay.face_tap[k].clone(), lay.face_hold[k].clone());
                if Self::is_held_ctl(&tap) {
                    if down(i) {
                        held.push((tap.ctl.clone().unwrap_or_default(), fp.mask, val(i)));
                        self.face[k] = Some(fp);
                    } else {
                        self.face[k] = None;
                    }
                } else if down(i) && Self::is_bound(&hold) && now - fp.at >= self.map.tap_hold_s {
                    self.fire(&hold, fp.mask, Self::intensity(fp.peak), now, &mut actions, &mut cues);
                    // Fired: forget it, so the release does nothing.
                    self.face[k] = None;
                } else if released(i) {
                    self.fire(&tap, fp.mask, Self::intensity(fp.peak), now, &mut actions, &mut cues);
                    self.face[k] = None;
                } else {
                    self.face[k] = Some(fp);
                }
            }
            let crane = self.crane().filter(|_| self.active == self.latched_mask());
            if let Some(c) = crane {
                self.crane_dpad_step(c, now, &down, &pressed, &mut cues);
            }
            for (k, &i) in DPAD.iter().enumerate() {
                if crane.is_some() {
                    self.dpad[k] = None;
                    continue;
                }
                if pressed(i) {
                    self.dpad[k] = Some(desired);
                    let bd = self.layer(desired).1.dpad[k].clone();
                    if !Self::is_held_ctl(&bd) {
                        self.fire(&bd, desired, 1.0, now, &mut actions, &mut cues);
                    }
                }
                match self.dpad[k] {
                    Some(mask) if down(i) => {
                        let bd = &self.layer(mask).1.dpad[k];
                        if Self::is_held_ctl(bd) {
                            held.push((bd.ctl.clone().unwrap_or_default(), mask, 1.0));
                        }
                    }
                    Some(_) => self.dpad[k] = None,
                    None => {}
                }
            }
        }

        // ---------------------------------------------------------------- held controls
        let dir = |plus: &str, minus: &str| {
            let on = |c: &str| held.iter().any(|(h, _, _)| h == c);
            if on(plus) {
                1.0
            } else if on(minus) {
                -1.0
            } else {
                0.0
            }
        };
        let ease = 1.0 - (-dt / 0.15).exp();
        self.lift += (dir("lift+", "lift-") - self.lift) * ease;
        self.visor += (dir("visor+", "visor-") - self.visor) * ease;
        self.roll += (dir("roll+", "roll-") - self.roll) * ease;
        for v in [&mut self.lift, &mut self.visor, &mut self.roll] {
            if v.abs() < 1e-3 {
                *v = 0.0;
            }
        }
        let twist = dir("twist+", "twist-");
        let reach = dir("reach+", "reach-");
        let hero = &mut self.parts[HERO];
        hero.extra = (hero.extra + twist * RATE * dt).clamp(-1.0, 1.0);
        let poker = &mut self.parts[POKER];
        poker.extra = (poker.extra + reach * RATE * dt).clamp(-1.0, 1.0);
        // Grip follows the pressure while held; let go, it opens unless kept.
        let mut gripping = [false; 3];
        for (c, mask, p) in &held {
            if c == "grip" {
                for &i in Self::arms_of(self.layer(*mask).1.stick) {
                    self.parts[i].grip = *p;
                    gripping[i] = true;
                }
            }
        }
        let crane = self.crane().filter(|_| self.active == self.latched_mask());
        if let Some(c) = crane {
            // R2 grips by pressure: the poker claw in a two-armed crane, else the crane's arm.
            let r2 = val(b::R2);
            if r2 > 0.0 {
                let arms: &[usize] = if c == Crane::Both { &[POKER] } else { c.arms() };
                for &i in arms {
                    self.parts[i].grip = self.parts[i].grip.max(r2).min(1.0);
                    if !gripping[i] {
                        self.parts[i].grip = r2;
                    }
                    gripping[i] = true;
                }
            }
        }
        let open = 1.0 - (-dt / 0.08).exp();
        for (i, part) in self.parts.iter_mut().enumerate() {
            if !gripping[i] && !part.grip_keep {
                part.grip -= part.grip * open;
                if part.grip < 1e-3 {
                    part.grip = 0.0;
                }
            }
        }

        // ---------------------------------------------------------------- the left stick
        let driven: &[usize] = match crane {
            Some(c) => {
                let right = [axis(2), axis(3)];
                let creep = if (down(b::L1) && !self.latched[0]) || (down(b::R1) && !self.latched[1]) { CREEP } else { 1.0 };
                self.crane_step(c, stick, right, dt * creep, now, &mut cues);
                c.arms()
            }
            None => Self::driven(self.layer(self.active).1.stick),
        };
        for (i, part) in self.parts.iter_mut().enumerate() {
            if part.pinned || (crane.is_some() && driven.contains(&i)) {
                continue;
            }
            if driven.contains(&i) {
                let want = if i == BODY { stick } else { [dead(stick[0]), -dead(stick[1])] };
                if part.pickup && (want[0] - part.pos[0]).hypot(want[1] - part.pos[1]) < PICKUP {
                    part.pickup = false;
                }
                if !part.pickup {
                    part.pos = want;
                    part.home = None;
                    continue;
                }
            } else if part.home.is_none() && !part.at_home() {
                part.home = Some((now, [part.pos[0], part.pos[1], part.extra]));
            }
            if let Some((t0, from)) = part.home {
                let dur = if i == BODY { BODY_HOME_S } else { ARM_HOME_S };
                let w = 1.0 - minjerk(((now - t0) / dur).min(1.0));
                part.pos = [from[0] * w, from[1] * w];
                part.extra = from[2] * w;
                if w <= 0.0 {
                    part.home = None;
                }
            }
        }

        let [body, hero, poker] = self.parts;
        let mut axes = vec![0.0; op_axis::COUNT];
        axes[0] = body.pos[0];
        axes[1] = body.pos[1];
        axes[2] = axis(2);
        axes[3] = axis(3);
        // In a crane the right stick is the arm's: the head watches the claw instead (roughly:
        // toward the arm's ring, down to the poker claw), or rests for a two-armed one.
        if let Some(c) = crane {
            let (yaw, down) = match c {
                Crane::Poker => (poker.pos[0] * POKER_AIM, 0.6 - 0.4 * poker.pos[1]),
                Crane::Hero => (hero.pos[0] * HERO_AIM, 0.1 - 0.2 * hero.pos[1]),
                Crane::Both => (0.0, 0.0),
            };
            axes[2] = (yaw / GAZE_YAW).clamp(-1.0, 1.0);
            axes[3] = down.clamp(-1.0, 1.0);
        }
        axes[op_axis::ROLL] = self.roll;
        axes[op_axis::HERO_AIM] = hero.pos[0];
        axes[op_axis::HERO_RAISE] = hero.pos[1];
        axes[op_axis::HERO_TWIST] = hero.extra;
        axes[op_axis::HERO_GRIP] = hero.grip;
        axes[op_axis::POKER_AIM] = poker.pos[0];
        axes[op_axis::POKER_RAISE] = poker.pos[1];
        axes[op_axis::POKER_REACH] = poker.extra;
        axes[op_axis::POKER_GRIP] = poker.grip;
        axes[op_axis::LIFT] = self.lift;
        axes[op_axis::VISOR] = self.visor;

        self.prev = (0..b::COUNT).map(down).collect();
        let (key, lay) = self.layer(self.active);
        let view = PadControls {
            layer: key.into(),
            latched: (0..3).filter(|&m| self.latched[m]).map(|m| MOD_NAMES[m].to_string()).collect(),
            stick: lay.stick,
            pickup: driven.iter().any(|&i| self.parts[i].pickup && !self.parts[i].pinned),
            pinned: (HERO..=POKER).filter(|&i| self.parts[i].pinned).map(|i| PART_NAMES[i].to_string()).collect(),
            grip: [hero.grip, poker.grip],
            crane: crane.map(|c| c.name().to_string()),
            menu: self.menu_view(menu),
            talking: matches!(self.talk_down, Some((_, true))),
            armed: self.armed,
            last: None,
        };
        Step { puppet: PadState { axes, buttons: vec![(false, 0.0); b::COUNT] }, actions, cues, view }
    }

    /// One crane step: both sticks as speeds (`dt` already scaled for creep).
    fn crane_step(&mut self, c: Crane, left: [f64; 2], right: [f64; 2], dt: f64, now: f64, cues: &mut Vec<Cue>) {
        let (l, r) = ([dead(left[0]), -dead(left[1])], [dead(right[0]), -dead(right[1])]);
        let mut edge = false;
        let rate = CRANE_RATE * dt;
        // Add `d`, clamped to -1..1; true when the clamp held it back.
        fn bump(v: &mut f64, d: f64) -> bool {
            let n = *v + d;
            *v = n.clamp(-1.0, 1.0);
            d != 0.0 && !(-1.0..=1.0).contains(&n)
        }
        // (hero aim, hero raise, hero twist) and (poker around, poker reach, poker up) inputs.
        let (hero_in, poker_in) = match c {
            Crane::Hero => ([l[0], l[1], r[0]], [0.0; 3]),
            Crane::Poker => ([0.0; 3], [l[0], l[1], r[1]]),
            Crane::Both => ([l[0], l[1], 0.0], [r[0], 0.0, r[1]]),
        };
        let moving = |v: &[f64; 3]| v.iter().any(|x| *x != 0.0);
        for (i, input) in [(HERO, hero_in), (POKER, poker_in)] {
            if !c.arms().contains(&i) {
                continue;
            }
            let part = &mut self.parts[i];
            part.home = None;
            part.pickup = false;
            if moving(&input) {
                part.glide = None;
            } else if let Some((t0, from, to)) = part.glide {
                let w = minjerk(((now - t0) / GLIDE_S).min(1.0));
                part.set([0, 1, 2].map(|k| from[k] + (to[k] - from[k]) * w));
                if w >= 1.0 {
                    part.glide = None;
                }
                continue;
            }
            if i == HERO {
                edge |= bump(&mut part.pos[0], input[0] * rate);
                edge |= bump(&mut part.pos[1], input[1] * rate);
                edge |= bump(&mut part.extra, input[2] * rate);
            } else {
                edge |= bump(&mut part.pos[0], input[0] * rate);
                if input[1] != 0.0 || input[2] != 0.0 {
                    let mm = CRANE_TIP_MM_S * dt;
                    edge |= Self::poker_move(part, input[1] * mm, input[2] * mm);
                }
            }
        }
        if edge && now - self.edge_at >= EDGE_EVERY_S {
            self.edge_at = now;
            cues.push(Cue::Edge);
        }
    }

    /// Move the poker claw tip `fwd` / `up` mm in its plane, in a straight line, clamped to
    /// what the arm reaches. True when the clamp held it back (the edge).
    fn poker_move(part: &mut Part, fwd: f64, up: f64) -> bool {
        let (rx, ry) = POKER_IK.tip(0.0, 0.0);
        let (a0, r0) = (ry.atan2(rx), rx.hypot(ry));
        let a = a0 + (part.pos[1] * POKER_SWING).to_radians();
        let r = r0 + part.extra * POKER_REACH_MM;
        let (x, y) = (r * a.cos() + fwd, r * a.sin() + up);
        let swing = ((y.atan2(x) - a0).to_degrees() / POKER_SWING).clamp(-1.0, 1.0);
        let out_max = (POKER_IK.l1 + POKER_IK.l2 - 1.0 - r0) / POKER_REACH_MM;
        let reach = ((x.hypot(y) - r0) / POKER_REACH_MM).clamp(-1.0, out_max.min(1.0));
        // Held back if the clamp took more than a hair off the asked-for move.
        let (want_s, want_r) = ((y.atan2(x) - a0).to_degrees() / POKER_SWING, (x.hypot(y) - r0) / POKER_REACH_MM);
        let edge = (want_s - swing).abs() > 1e-6 || (want_r - reach).abs() > 1e-6;
        part.pos[1] = swing;
        part.extra = reach;
        edge
    }

    /// Crane d-pad: tap ←/→ = glide to that spot, hold = save it there; tap ↑ = glide home.
    fn crane_dpad_step(&mut self, c: Crane, now: f64, down: &dyn Fn(usize) -> bool, pressed: &dyn Fn(usize) -> bool, cues: &mut Vec<Cue>) {
        for (k, &i) in DPAD.iter().enumerate() {
            if pressed(i) {
                self.crane_dpad[k] = Some(now);
            }
            let Some(t) = self.crane_dpad[k] else { continue };
            let slot = match i {
                b::LEFT => Some(0),
                b::RIGHT => Some(1),
                _ => None,
            };
            if down(i) {
                if let Some(slot) = slot.filter(|_| t.is_finite() && now - t >= self.map.tap_hold_s) {
                    self.spots[c.index()][slot] = Some(self.parts.map(|p| p.state()));
                    self.crane_dpad[k] = Some(f64::INFINITY);
                    cues.push(Cue::Saved);
                }
                continue;
            }
            self.crane_dpad[k] = None;
            if !t.is_finite() {
                continue; // that was a save
            }
            let target = match (i, slot) {
                (b::UP, _) => Some([[0.0; 3]; 3]),
                (_, Some(slot)) => self.spots[c.index()][slot],
                _ => None,
            };
            if let Some(to) = target {
                for &a in c.arms() {
                    let p = &mut self.parts[a];
                    p.glide = Some((now, p.state(), to[a]));
                }
            }
        }
    }

    /// The stick changes hands: parts it lets go of ease home (unless pinned), parts it takes
    /// wait for it to come to them.
    fn switch_layer(&mut self, to: Mask, stick: [f64; 2], now: f64) {
        let before = Self::driven(self.layer(self.active).1.stick);
        let after = Self::driven(self.layer(to).1.stick);
        self.active = to;
        self.pending = None;
        for i in 0..3 {
            let part = &mut self.parts[i];
            if after.contains(&i) && !before.contains(&i) {
                let want = if i == BODY { stick } else { [stick[0], -stick[1]] };
                part.pickup = (want[0] - part.pos[0]).hypot(want[1] - part.pos[1]) >= PICKUP;
            } else if before.contains(&i) && !after.contains(&i) {
                part.pickup = false;
                if !part.pinned {
                    part.send_home(now);
                }
            }
        }
    }

    /// L3+R3: unlatch, unpin, everything home, and tell the runtime.
    fn reset_all(&mut self, now: f64, actions: &mut Vec<Action>, cues: &mut Vec<Cue>) {
        self.latched = [false; 3];
        for p in &mut self.parts {
            p.send_home(now);
            p.grip_keep = false;
            p.pickup = true;
        }
        actions.push(Action::Reset);
        cues.push(Cue::Latch(false));
    }

    fn level<'a>(root: &'a [MenuItem], path: &[usize]) -> Option<(&'a [MenuItem], Vec<String>)> {
        let mut items = root;
        let mut titles = vec!["Menu".to_string()];
        for &i in path {
            let it = items.get(i)?;
            titles.push(it.label.clone());
            items = &it.children;
        }
        Some((items, titles))
    }

    fn navigate(&mut self, root: &[MenuItem], pressed: &dyn Fn(usize) -> bool, actions: &mut Vec<Action>) {
        let Some((path, cursor)) = self.menu.as_mut() else { return };
        // The tree can change under us (a label, a list): fall back to the root if the path broke.
        let (items, _) = match Self::level(root, path) {
            Some(l) => l,
            None => {
                path.clear();
                *cursor = 0;
                (root, vec![])
            }
        };
        let n = items.len().max(1);
        *cursor = (*cursor).min(n - 1);
        if pressed(b::UP) {
            *cursor = (*cursor + n - 1) % n;
        }
        if pressed(b::DOWN) {
            *cursor = (*cursor + 1) % n;
        }
        if pressed(b::CROSS) || pressed(b::RIGHT) {
            if let Some(it) = items.get(*cursor) {
                if it.children.is_empty() {
                    actions.push(Action::Menu(it.id.clone()));
                } else {
                    path.push(*cursor);
                    *cursor = 0;
                }
            }
        } else if pressed(b::CIRCLE) || pressed(b::LEFT) {
            match path.pop() {
                Some(i) => *cursor = i,
                None => self.menu = None,
            }
        }
    }

    fn menu_view(&self, root: &[MenuItem]) -> Option<PadMenuView> {
        let (path, cursor) = self.menu.as_ref()?;
        let (items, titles) = Self::level(root, path).unwrap_or((root, vec!["Menu".into()]));
        Some(PadMenuView {
            path: titles,
            items: items.iter().map(|i| if i.children.is_empty() { i.label.clone() } else { format!("{} ›", i.label) }).collect(),
            cursor: (*cursor).min(items.len().saturating_sub(1)),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use op_axis as ax;

    fn mapping() -> PadMapping {
        let text = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/../../../profiles/r3x/pad.json")).unwrap();
        serde_json::from_str(&text).unwrap()
    }

    /// Buttons pressed this hard; the right stick a little off, to see it pass through.
    fn pad_at(held: &[usize], left: [f64; 2], pressure: f64) -> PadState {
        let mut buttons = vec![(false, 0.0); b::COUNT];
        for &i in held {
            buttons[i] = (true, pressure);
        }
        PadState { axes: vec![left[0], left[1], -0.5, 0.0], buttons }
    }

    struct Run {
        c: Controls,
        t: f64,
        menu: Vec<MenuItem>,
    }
    impl Run {
        fn new() -> Self {
            let menu = vec![
                MenuItem::leaf("DJ mode", "dj"),
                MenuItem::sub("Music", vec![MenuItem::leaf("Next", "music.next"), MenuItem::leaf("Stop", "music.stop")]),
            ];
            Run { c: Controls::new(mapping()), t: 0.0, menu }
        }
        fn at(&mut self, dt: f64, held: &[usize]) -> Step {
            self.with(dt, held, [0.0, 0.0], 1.0)
        }
        fn with(&mut self, dt: f64, held: &[usize], left: [f64; 2], pressure: f64) -> Step {
            self.t += dt;
            self.c.step(self.t, &pad_at(held, left, pressure), &self.menu)
        }
        /// Hold `held` with the stick at `left` for `secs` at 100 Hz; the last step.
        fn hold(&mut self, secs: f64, held: &[usize], left: [f64; 2]) -> Step {
            let mut s = Step::default();
            for _ in 0..(secs * 100.0).round() as usize {
                s = self.with(0.01, held, left, 1.0);
            }
            s
        }
    }

    #[test]
    fn the_shipped_mapping_parses_and_grip_buttons_have_no_hold() {
        let m = mapping();
        assert_eq!(m.layers.l1.stick, StickTarget::Hero);
        assert_eq!(m.layers.r1.stick, StickTarget::Poker);
        assert_eq!(m.layers.l1r1.stick, StickTarget::Arms);
        assert_eq!(m.layers.r2.face_tap[0].sfx.as_deref(), Some("hi_there"));
        for (_, l) in [m.layers.get(false, false, false), m.layers.get(true, false, false), m.layers.get(false, true, false), m.layers.get(true, true, false)] {
            for k in 0..4 {
                if Controls::is_held_ctl(&l.face_tap[k]) {
                    assert!(!Controls::is_bound(&l.face_hold[k]), "{}: a held control's button has no hold", l.name);
                }
            }
        }
    }

    #[test]
    fn tap_fires_on_release_hold_fires_once_and_pressure_sets_intensity() {
        let mut r = Run::new();
        assert!(r.with(0.01, &[b::CROSS], [0.0, 0.0], 0.2).actions.is_empty(), "nothing until release or hold");
        assert_eq!(r.at(0.1, &[]).actions, vec![Action::Emote { slot: 0, intensity: 0.6 }], "a light tap = a small yes");
        r.at(0.01, &[b::TRIANGLE]);
        assert_eq!(r.at(0.4, &[b::TRIANGLE]).actions, vec![Action::Emote { slot: 7, intensity: 1.0 }], "hold = slot 8, full press");
        assert!(r.at(0.5, &[b::TRIANGLE]).actions.is_empty(), "once per hold");
        assert!(r.at(0.01, &[]).actions.is_empty(), "no tap after a hold");
    }

    #[test]
    fn a_press_belongs_to_the_layer_held_when_it_went_down_without_waiting_for_the_grace() {
        let mut r = Run::new();
        r.at(0.01, &[b::L1]);
        r.at(0.01, &[b::L1, b::SQUARE]); // inside the chord grace: still L1's binding
        assert_eq!(r.at(0.05, &[]).actions, vec![Action::Play { id: "arm_wave".into(), intensity: 1.0 }], "L1 let go first: still the wave");
        let s = r.at(0.01, &[b::R2, b::CROSS]);
        assert!(s.actions.is_empty());
        assert_eq!(r.at(0.01, &[b::R2]).actions, vec![Action::Sfx("hi_there".into())]);
        let s = r.at(0.01, &[b::R2, b::R1, b::RIGHT]);
        assert_eq!(s.actions, vec![Action::Menu("music.next".into())], "R2+R1 = DJ layer, d-pad → next track");
    }

    #[test]
    fn the_stick_changes_hands_only_after_the_chord_settles() {
        let mut r = Run::new();
        let s = r.at(0.01, &[b::L1]);
        assert_eq!(s.view.stick, StickTarget::Body, "not yet");
        let s = r.at(0.03, &[b::L1, b::R1]); // R1 joins inside the grace
        assert_eq!(s.view.stick, StickTarget::Body);
        let s = r.hold(0.1, &[b::L1, b::R1], [0.0, 0.0]);
        assert_eq!((s.view.stick, s.view.layer.as_str()), (StickTarget::Arms, "l1r1"), "straight to both arms");
        assert!(s.cues.is_empty() || s.cues == vec![Cue::Layer]);
        let mut layers = 0;
        for _ in 0..20 {
            layers += r.at(0.01, &[]).cues.iter().filter(|c| **c == Cue::Layer).count();
        }
        assert_eq!(layers, 1, "one buzz back to base");
    }

    #[test]
    fn taking_a_part_waits_for_the_stick_to_come_to_it() {
        let mut r = Run::new();
        // Pushing the body right, then L1 with the stick still over: the hero arm stays put.
        r.hold(0.2, &[], [0.9, 0.0]);
        let s = r.hold(0.2, &[b::L1], [0.9, 0.0]);
        assert_eq!(s.view.stick, StickTarget::Hero);
        assert!(s.view.pickup, "waiting for the stick");
        assert_eq!(s.puppet.axes[ax::HERO_AIM], 0.0, "no jump");
        assert!(s.puppet.axes[0] < 0.9, "the body it let go of eases home");
        let s = r.hold(0.5, &[b::L1], [0.9, 0.0]);
        assert_eq!(s.puppet.axes[0], 0.0, "body home");
        // Bring the stick back to centre: the arm is picked up and follows.
        r.hold(0.05, &[b::L1], [0.05, 0.0]);
        let s = r.hold(0.05, &[b::L1], [0.4, -0.6]);
        assert!(!s.view.pickup);
        assert!((s.puppet.axes[ax::HERO_AIM] - dead(0.4)).abs() < 1e-9 && (s.puppet.axes[ax::HERO_RAISE] - dead(0.6)).abs() < 1e-9, "stick up = raise");
    }

    #[test]
    fn an_arm_let_go_eases_home_smoothly_unless_pinned() {
        let mut r = Run::new();
        r.hold(0.2, &[b::L1], [0.0, 0.0]);
        r.hold(0.2, &[b::L1], [0.0, -1.0]);
        // Let go of L1 first (the stick still up through the chord grace), then the stick.
        r.hold(0.1, &[], [0.0, -1.0]);
        let mut last = 1.0;
        let mut steps = vec![];
        for _ in 0..100 {
            let v = r.with(0.01, &[], [0.0, 0.0], 1.0).puppet.axes[ax::HERO_RAISE];
            assert!(v <= last + 1e-12, "monotone home");
            steps.push(last - v);
            last = v;
        }
        assert_eq!(last, 0.0, "home within ARM_HOME_S");
        assert!(steps[1] < steps[40], "starts gently (min-jerk), no snap");
        // Pinned (L3 in the arm layer): it stays where it was left.
        r.hold(0.2, &[b::L1], [0.0, 0.0]);
        r.hold(0.2, &[b::L1], [0.0, -1.0]);
        r.with(0.01, &[b::L1, b::L3], [0.0, -1.0], 1.0);
        let s = r.with(0.01, &[b::L1], [0.0, -1.0], 1.0);
        assert_eq!(s.cues, vec![Cue::Pin(true)]);
        let s = r.hold(1.5, &[], [0.0, 0.0]);
        assert_eq!(s.view.pinned, vec!["hero"]);
        assert!((s.puppet.axes[ax::HERO_RAISE] - 1.0).abs() < 1e-9, "pinned up");
        // Retake its layer: locked, the centred stick does not move it.
        let s = r.hold(0.2, &[b::L1], [0.0, 0.0]);
        assert!((s.puppet.axes[ax::HERO_RAISE] - 1.0).abs() < 1e-9);
        // Unpin (L3 again): the stick picks it up where it is, no drop.
        r.at(0.01, &[b::L1, b::L3]);
        let s = r.at(0.01, &[b::L1]);
        assert!(s.view.pickup && (s.puppet.axes[ax::HERO_RAISE] - 1.0).abs() < 1e-9);
        // Let go: home.
        let s = r.hold(1.0, &[], [0.0, 0.0]);
        assert!(s.view.pinned.is_empty());
        assert_eq!(s.puppet.axes[ax::HERO_RAISE], 0.0);
    }

    #[test]
    fn double_tap_latches_a_layer_and_a_single_tap_unlatches() {
        let mut r = Run::new();
        r.at(0.01, &[b::R1]);
        r.at(0.05, &[]);
        r.at(0.1, &[b::R1]);
        let s = r.at(0.05, &[]);
        assert_eq!(s.cues, vec![Cue::Latch(true)]);
        let s = r.hold(0.2, &[], [0.0, 0.0]);
        assert_eq!((s.view.stick, s.view.latched.clone()), (StickTarget::Poker, vec!["r1".to_string()]), "latched: hands free");
        assert_eq!(s.view.crane.as_deref(), Some("poker"), "a latched arm layer is the crane");
        // In the crane R2 is the grip, not a layer.
        let s = r.hold(0.2, &[b::R2], [0.0, 0.0]);
        assert_eq!(s.view.layer, "r1");
        assert_eq!(s.view.grip[1], 1.0);
        r.hold(0.2, &[], [0.0, 0.0]);
        r.at(0.01, &[b::R1]);
        let s = r.at(0.05, &[]);
        assert_eq!(s.cues, vec![Cue::Latch(false)]);
        assert_eq!(r.hold(0.2, &[], [0.0, 0.0]).view.layer, "base");
    }

    #[test]
    fn using_a_modifier_is_not_a_tap() {
        let mut r = Run::new();
        // Two quick L1 presses, each moving the arm: no latch.
        r.at(0.01, &[b::L1]);
        r.with(0.05, &[b::L1], [0.0, -0.8], 1.0);
        r.at(0.02, &[]);
        r.at(0.05, &[b::L1]);
        r.with(0.05, &[b::L1], [0.0, -0.8], 1.0);
        assert!(r.at(0.02, &[]).cues.iter().all(|c| !matches!(c, Cue::Latch(_))));
        // Nor a long hold.
        r.hold(0.5, &[], [0.0, 0.0]);
        r.at(0.01, &[b::L1]);
        r.hold(0.5, &[b::L1], [0.0, 0.0]);
        r.at(0.01, &[]);
        r.at(0.05, &[b::L1]);
        assert!(r.at(0.05, &[]).cues.iter().all(|c| !matches!(c, Cue::Latch(_))));
    }

    #[test]
    fn grip_follows_pressure_opens_on_release_and_can_be_kept() {
        let mut r = Run::new();
        r.hold(0.2, &[b::L1], [0.0, 0.0]);
        let s = r.with(0.01, &[b::L1, b::CROSS], [0.0, 0.0], 0.4);
        assert!((s.view.grip[0] - 0.4).abs() < 1e-9, "a light squeeze, a light grip");
        assert!(s.actions.is_empty(), "grip is not an emote");
        let s = r.with(0.01, &[b::L1, b::CROSS], [0.0, 0.0], 1.0);
        assert_eq!(s.puppet.axes[ax::HERO_GRIP], 1.0);
        assert_eq!(s.puppet.axes[ax::POKER_GRIP], 0.0, "only the hero claw");
        let s = r.hold(0.6, &[b::L1], [0.0, 0.0]);
        assert_eq!(s.view.grip[0], 0.0, "released: opens");
        // ○ keeps the grip: squeeze, tap ○, let go of ✕.
        r.with(0.01, &[b::L1, b::CROSS], [0.0, 0.0], 0.8);
        r.with(0.01, &[b::L1, b::CROSS, b::CIRCLE], [0.0, 0.0], 0.8);
        r.with(0.01, &[b::L1, b::CROSS], [0.0, 0.0], 0.8);
        let s = r.hold(0.6, &[b::L1], [0.0, 0.0]);
        assert!((s.view.grip[0] - 0.8).abs() < 1e-9, "kept");
    }

    #[test]
    fn dpad_twists_the_hero_wrist_and_reaches_the_poker_claw() {
        let mut r = Run::new();
        r.hold(0.2, &[b::L1], [0.0, 0.0]);
        let s = r.hold(0.5, &[b::L1, b::RIGHT], [0.0, 0.0]);
        assert!((s.puppet.axes[ax::HERO_TWIST] - 0.6).abs() < 0.02, "RATE per second: {}", s.puppet.axes[ax::HERO_TWIST]);
        let s = r.hold(0.2, &[b::L1], [0.0, 0.0]);
        assert!((s.puppet.axes[ax::HERO_TWIST] - 0.6).abs() < 0.02, "stays while the arm is held");
        r.hold(0.2, &[b::R1], [0.0, 0.0]);
        let s = r.hold(2.0, &[b::R1, b::UP], [0.0, 0.0]);
        assert_eq!(s.puppet.axes[ax::POKER_REACH], 1.0, "clamped");
        assert_eq!(s.puppet.axes[ax::HERO_TWIST], 0.0, "the hero arm, let go, went home twist and all");
    }

    #[test]
    fn base_dpad_eases_lift_and_visor_and_the_both_arms_dpad_rolls() {
        let mut r = Run::new();
        let s = r.hold(0.5, &[b::UP], [0.0, 0.0]);
        assert!(s.puppet.axes[ax::LIFT] > 0.9);
        let s = r.hold(1.0, &[], [0.0, 0.0]);
        assert!(s.puppet.axes[ax::LIFT] < 0.01, "eases back");
        r.hold(0.2, &[b::L1, b::R1], [0.0, 0.0]);
        let s = r.hold(0.5, &[b::L1, b::R1, b::LEFT], [0.0, 0.0]);
        assert!(s.puppet.axes[ax::ROLL] < -0.9);
        assert_eq!(s.puppet.axes[ax::LIFT], 0.0, "the d-pad belongs to the layer");
        assert_eq!(s.puppet.axes[2], -0.5, "the right stick is always the head");
    }

    #[test]
    fn select_tap_cancels_and_hold_opens_the_menu() {
        let mut r = Run::new();
        r.at(0.01, &[b::SELECT]);
        assert_eq!(r.at(0.1, &[]).actions, vec![Action::Cancel]);
        r.at(0.01, &[b::SELECT]);
        let s = r.at(0.4, &[b::SELECT]);
        assert_eq!(s.view.menu.as_ref().unwrap().items, vec!["DJ mode", "Music ›"]);
        assert!(r.at(0.01, &[]).actions.is_empty(), "no cancel after the hold");
        r.at(0.01, &[b::DOWN]);
        r.at(0.01, &[]);
        let s = r.at(0.01, &[b::CROSS]);
        assert!(s.actions.is_empty(), "entering a submenu fires nothing, and ✕ is not an emote here");
        r.at(0.01, &[]);
        r.at(0.01, &[b::DOWN]);
        r.at(0.01, &[]);
        assert_eq!(r.at(0.01, &[b::CROSS]).actions, vec![Action::Menu("music.stop".into())]);
        r.at(0.01, &[]);
        let s = r.at(0.01, &[b::SELECT]);
        assert!(s.view.menu.is_none(), "a press closes it");
        assert!(r.at(0.01, &[]).actions.is_empty(), "and does not cancel");
    }

    #[test]
    fn r3_looks_l3_r3_together_resets_everything() {
        let mut r = Run::new();
        r.at(0.01, &[b::R3]);
        assert_eq!(r.at(0.01, &[]).actions, vec![Action::LookToggle]);
        // Latch L1, pin the hero arm up, then L3+R3.
        r.at(0.01, &[b::L1]);
        r.at(0.05, &[]);
        r.at(0.1, &[b::L1]);
        r.at(0.05, &[]);
        r.hold(0.2, &[], [0.0, 0.0]);
        r.hold(0.2, &[], [0.0, -1.0]);
        r.at(0.01, &[b::L3]);
        r.at(0.01, &[]);
        let s = r.at(0.01, &[b::L3, b::R3]);
        assert_eq!(s.actions, vec![Action::Reset]);
        let s = r.at(0.01, &[]);
        assert!(s.actions.is_empty(), "neither fires alone after the chord");
        let s = r.hold(1.0, &[], [0.0, 0.0]);
        assert!(s.view.latched.is_empty() && s.view.pinned.is_empty() && s.view.layer == "base");
        assert_eq!(s.puppet.axes[ax::HERO_RAISE], 0.0);
    }

    #[test]
    fn l3_in_a_body_layer_sends_pinned_arms_home() {
        let mut r = Run::new();
        r.hold(0.2, &[b::R1], [0.0, 0.0]);
        r.hold(0.2, &[b::R1], [0.5, 0.0]);
        r.with(0.01, &[b::R1, b::L3], [0.5, 0.0], 1.0);
        r.with(0.01, &[b::R1], [0.5, 0.0], 1.0);
        let s = r.hold(0.3, &[], [0.0, 0.0]);
        assert_eq!(s.puppet.axes[ax::POKER_AIM], dead(0.5), "pinned");
        r.at(0.01, &[b::L3]);
        r.at(0.01, &[]);
        assert_eq!(r.hold(1.0, &[], [0.0, 0.0]).puppet.axes[ax::POKER_AIM], 0.0);
    }

    #[test]
    fn talk_follows_l2_in_any_layer() {
        let mut r = Run::new();
        r.at(0.01, &[b::L2, b::L1]);
        let s = r.at(TALK_ARM_S, &[b::L2, b::L1]);
        assert!(s.actions.contains(&Action::Talk(true)));
        assert!(s.view.talking);
        assert!(r.at(0.01, &[b::L1]).actions.contains(&Action::Talk(false)));
    }

    /// A tap on L2 (a kid on the pad, 2026-10-01: presses of 10-390 ms that caught nothing)
    /// opens nothing: no interrupt, no duck, no listening head. The mic opens once L2 has been
    /// held [`TALK_ARM_S`]; the pad buzzes then, so the operator knows to speak.
    #[test]
    fn a_tap_on_l2_never_opens_the_mic() {
        let mut r = Run::new();
        let s = r.at(0.01, &[b::L2]);
        assert!(!s.actions.contains(&Action::Talk(true)), "not on the press");
        assert!(!s.view.talking);
        r.at(TALK_ARM_S * 0.5, &[b::L2]);
        let s = r.at(0.01, &[]);
        assert!(s.actions.iter().all(|a| !matches!(a, Action::Talk(_))), "released early: nothing at all");
    }

    #[test]
    fn holding_l2_opens_the_mic_once_after_the_arm_time() {
        let mut r = Run::new();
        r.at(0.01, &[b::L2]);
        assert!(r.at(TALK_ARM_S - 0.05, &[b::L2]).actions.is_empty());
        let s = r.at(0.06, &[b::L2]);
        assert_eq!(s.actions, vec![Action::Talk(true)]);
        assert!(r.at(0.5, &[b::L2]).actions.is_empty(), "once per hold");
        assert_eq!(r.at(0.01, &[]).actions, vec![Action::Talk(false)]);
        r.at(0.01, &[b::L2]);
        r.c.reset();
        assert!(r.at(1.0, &[b::L2]).actions.is_empty(), "a reset (pad lost) forgets the press");
    }

    #[test]
    fn ps_hold_toggles_arming_once_and_a_reset_keeps_it() {
        let mut r = Run::new();
        assert!(r.c.armed);
        r.at(0.01, &[b::PS]);
        assert!(r.at(0.5, &[b::PS]).actions.is_empty());
        assert_eq!(r.at(0.6, &[b::PS]).actions, vec![Action::ToggleArm]);
        assert!(r.at(1.0, &[b::PS]).actions.is_empty());
        assert!(!r.at(0.01, &[]).view.armed);
        r.c.reset();
        assert!(!r.c.armed, "losing the pad does not re-arm the motors");
    }

    fn latch(r: &mut Run, held: &[usize]) {
        r.at(0.01, held);
        r.at(0.05, &[]);
        r.at(0.1, held);
        r.at(0.05, &[]);
        r.hold(0.2, &[], [0.0, 0.0]);
    }

    /// Both sticks are speeds in the crane, and the arm stays where it is left (2026-10-01:
    /// "a crane mode so I can use both sticks").
    #[test]
    fn the_poker_crane_moves_at_a_speed_and_stays_where_it_is_left() {
        let mut r = Run::new();
        latch(&mut r, &[b::R1]);
        // Left X: around, at CRANE_RATE per second.
        let s = r.hold(0.5, &[], [1.0, 0.0]);
        assert!((s.puppet.axes[ax::POKER_AIM] - 0.4).abs() < 0.02, "{}", s.puppet.axes[ax::POKER_AIM]);
        let s = r.hold(1.0, &[], [0.0, 0.0]);
        assert!((s.puppet.axes[ax::POKER_AIM] - 0.4).abs() < 0.02, "let go: it stays");
        assert!(s.puppet.axes[2] > 0.0, "the head turns to watch the claw");
        // Right Y up: the claw tip rises, the right stick no longer moves the head.
        let s = r.c.step(r.t + 0.5, &PadState { axes: vec![0.0, 0.0, 0.0, -1.0], buttons: vec![(false, 0.0); b::COUNT] }, &r.menu);
        r.t += 0.5;
        assert!(s.puppet.axes[ax::POKER_RAISE] > 0.1, "up: {}", s.puppet.axes[ax::POKER_RAISE]);
        assert!(s.puppet.axes[3] < 0.6, "the head follows the claw up");
    }

    #[test]
    fn the_poker_crane_moves_the_tip_in_a_straight_line_and_rumbles_at_the_edge() {
        let mut r = Run::new();
        latch(&mut r, &[b::R1]);
        let tip = |s: &Step| {
            let (sh, wr) = POKER_IK.solve(s.puppet.axes[ax::POKER_RAISE] * POKER_SWING, s.puppet.axes[ax::POKER_REACH] * POKER_REACH_MM);
            POKER_IK.tip(sh, wr)
        };
        let s0 = r.hold(0.05, &[], [0.0, 0.0]);
        let (x0, y0) = tip(&s0);
        // Up for 0.25 s at 120 mm/s: 30 mm straight up, no drift in or out.
        let mut s = Step::default();
        for _ in 0..25 {
            r.t += 0.01;
            s = r.c.step(r.t, &PadState { axes: vec![0.0, 0.0, 0.0, -1.0], buttons: vec![(false, 0.0); b::COUNT] }, &r.menu);
        }
        let (x, y) = tip(&s);
        assert!((y - y0 - 30.0).abs() < 2.0 && (x - x0).abs() < 2.0, "({:.1}, {:.1})", x - x0, y - y0);
        // Reach out (left stick up) until the arm is straight: an edge rumble, and it stops.
        let mut edges = 0;
        for _ in 0..200 {
            edges += r.with(0.01, &[], [0.0, -1.0], 1.0).cues.iter().filter(|c| **c == Cue::Edge).count();
        }
        assert!(edges >= 1, "the edge is felt");
        assert!(edges < 10, "but not as a constant buzz: {edges}");
    }

    #[test]
    fn creep_slows_the_crane_and_r2_grips() {
        let mut r = Run::new();
        latch(&mut r, &[b::R1]);
        let s = r.hold(0.5, &[b::L1], [1.0, 0.0]);
        assert!((s.puppet.axes[ax::POKER_AIM] - 0.4 * CREEP).abs() < 0.02, "creep: {}", s.puppet.axes[ax::POKER_AIM]);
        assert_eq!(s.view.layer, "r1", "L1 held is the creep, not the both-arms layer");
        let s = r.with(0.01, &[b::R2], [0.0, 0.0], 0.6);
        assert!((s.view.grip[1] - 0.6).abs() < 1e-9);
    }

    #[test]
    fn crane_spots_save_on_hold_and_glide_back_on_tap() {
        let mut r = Run::new();
        latch(&mut r, &[b::R1]);
        r.hold(0.5, &[], [1.0, 0.0]);
        let s = r.hold(0.5, &[b::RIGHT], [0.0, 0.0]);
        assert_eq!(s.cues, vec![], "saved earlier in the hold");
        r.at(0.01, &[]);
        let s = r.hold(1.0, &[], [-1.0, 0.0]);
        assert!(s.puppet.axes[ax::POKER_AIM] < 0.0);
        // ↑ = home, → = the saved spot, both as glides.
        r.at(0.01, &[b::UP]);
        r.at(0.01, &[]);
        let s = r.hold(GLIDE_S + 0.1, &[], [0.0, 0.0]);
        assert!(s.puppet.axes[ax::POKER_AIM].abs() < 1e-9, "home: {}", s.puppet.axes[ax::POKER_AIM]);
        r.at(0.01, &[b::RIGHT]);
        let mid = r.at(0.01, &[]);
        let s = r.hold(GLIDE_S + 0.1, &[], [0.0, 0.0]);
        assert!((s.puppet.axes[ax::POKER_AIM] - 0.4).abs() < 0.02, "back at the spot");
        assert!(mid.puppet.axes[ax::POKER_AIM] < 0.1, "glided, not jumped");
    }

    #[test]
    fn leaving_the_crane_leaves_the_arm_where_it_was_put() {
        let mut r = Run::new();
        latch(&mut r, &[b::R1]);
        r.hold(0.5, &[], [1.0, 0.0]);
        r.at(0.01, &[b::R1]);
        let s = r.at(0.05, &[]);
        assert!(s.cues.contains(&Cue::Latch(false)));
        let s = r.hold(1.5, &[], [0.0, 0.0]);
        assert_eq!((s.view.crane.clone(), s.view.layer.as_str()), (None, "base"));
        assert!((s.puppet.axes[ax::POKER_AIM] - 0.4).abs() < 0.02, "pinned where it was put");
        assert_eq!(s.view.pinned, vec!["poker"]);
    }

    #[test]
    fn a_two_armed_crane_puts_the_hero_on_the_left_stick_and_the_poker_on_the_right() {
        let mut r = Run::new();
        latch(&mut r, &[b::L1]);
        latch(&mut r, &[b::R1]);
        let s = r.hold(0.1, &[], [0.0, 0.0]);
        assert_eq!(s.view.crane.as_deref(), Some("both"));
        r.t += 0.5;
        let s = r.c.step(r.t, &PadState { axes: vec![0.0, -1.0, 1.0, 0.0], buttons: vec![(false, 0.0); b::COUNT] }, &r.menu);
        let _ = s;
        let s = r.c.step(r.t + 0.5, &PadState { axes: vec![0.0, -1.0, 1.0, 0.0], buttons: vec![(false, 0.0); b::COUNT] }, &r.menu);
        assert!(s.puppet.axes[ax::HERO_RAISE] > 0.3, "left up: hero raises");
        assert!(s.puppet.axes[ax::POKER_AIM] > 0.3, "right X: poker turns");
        assert_eq!(s.puppet.axes[ax::HERO_AIM], 0.0);
    }

    #[test]
    fn the_puppeteer_sees_an_operator_feed_and_no_buttons() {
        let mut r = Run::new();
        let s = r.with(0.01, &[b::L2, b::R2, b::START, b::CROSS], [0.1, 0.2], 1.0);
        assert_eq!(s.puppet.axes.len(), op_axis::COUNT);
        assert!(s.puppet.buttons.iter().all(|x| !x.0), "no raw buttons: triggers would move the arm");
        assert_eq!(&s.puppet.axes[..4], &[0.1, 0.2, -0.5, 0.0]);
    }
}

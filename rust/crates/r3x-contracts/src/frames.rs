use std::collections::BTreeMap;

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use ts_rs::TS;

pub type Rgb = [u8; 3];

/// 50 Hz body snapshot for the sim, the virtual driver and logs (plan §3b, D6).
/// Hardware servo controllers receive goals at event time, not frames.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct Frames {
    pub t_mono: f64,
    /// Joint name -> value in the joint's unit (deg or mm).
    pub joints: BTreeMap<String, f64>,
    /// Light group name -> one colour per pixel.
    pub lights: BTreeMap<String, Vec<Rgb>>,
    /// The operator's gamepad while one drives the puppeteer (the panel's controller overlay).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub pad: Option<PadFrame>,
}

/// A gamepad as the puppeteer read it this frame, and what it made of it.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct PadFrame {
    /// Standard gamepad layout: left x, left y, right x, right y in [-1, 1], down = +.
    pub axes: Vec<f64>,
    /// Standard layout, one per button: 0 = up, else how far in (pressure where the pad
    /// measures it, else 1).
    pub buttons: Vec<f64>,
    /// The puppeteer's smoothed intents in [-1, 1] (gaze_yaw, gaze_pitch, lift, body_yaw,
    /// lean, visor, arm_raise, energy).
    pub intents: BTreeMap<String, f64>,
    /// idle | engaged | dj
    pub mode: String,
    /// The runtime's operator layer (`r3x-pad` controls): absent from the sim's own pad.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub controls: Option<PadControls>,
}

/// What the operator layer is doing, for the overlay.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct PadControls {
    /// The active layer's key in `pad.json` (`base`, `l1`, `r1`, `l1r1`, `r2`, `r2l1`, `r2r1`,
    /// `r2l1r1`): the held and latched modifiers, settled for a moment.
    #[serde(default)]
    pub layer: String,
    /// Modifiers latched by a double tap (`l1`, `r1`, `r2`); a single tap unlatches.
    #[serde(default)]
    pub latched: Vec<String>,
    /// What the left stick drives right now.
    #[serde(default)]
    pub stick: StickTarget,
    /// The left stick just changed hands and waits to be brought back to where its new target
    /// is before it moves it (no jump).
    #[serde(default)]
    pub pickup: bool,
    /// Arms left where they are (L3 in an arm layer) instead of easing home.
    #[serde(default)]
    pub pinned: Vec<String>,
    /// Claw grip 0..1: hero, poker.
    #[serde(default)]
    pub grip: [f64; 2],
    /// Crane mode (an arm layer latched): `hero` | `poker` | `both`. Both sticks drive the
    /// arm(s) at a speed, and they stay where they are left.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub crane: Option<String>,
    /// The open menu, if any.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub menu: Option<PadMenuView>,
    /// L2 held: push-to-talk.
    pub talking: bool,
    /// Motion outputs enabled (PS hold toggles).
    pub armed: bool,
    /// The last thing a button did, for a moment ("Nod", "DJ mode on", "refused: ...").
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub last: Option<String>,
}

/// What a layer's left stick drives.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize, TS, JsonSchema)]
#[serde(rename_all = "lowercase")]
pub enum StickTarget {
    /// Turn (x) and lean (y).
    #[default]
    Body,
    /// The hero arm: x spins the top ring to aim it, y raises it.
    Hero,
    /// The poker arm: x spins the lower ring to aim it, y moves the claw tip up/down (IK).
    Poker,
    /// Both arms together: x aims, y raises.
    Arms,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct PadMenuView {
    /// Titles from the root to the open level.
    pub path: Vec<String>,
    pub items: Vec<String>,
    pub cursor: usize,
}

fn default_double_tap() -> f64 {
    0.3
}
fn default_chord_grace() -> f64 {
    0.08
}

/// The operator mapping (`profiles/<robot>/pad.json`): one layer per combination of the held
/// (or latched) modifiers L1, R1 and R2. The right stick (head), L2 (talk), L3/R3, Start,
/// Select and PS are the same in every layer.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct PadMapping {
    /// A button held this long fires its hold binding instead of its tap one.
    pub tap_hold_s: f64,
    /// PS held this long toggles motion arming.
    pub arm_hold_s: f64,
    /// Two taps of a modifier inside this latch its layer.
    #[serde(default = "default_double_tap")]
    pub double_tap_s: f64,
    /// A change of modifiers must hold this long before the left stick changes hands, so
    /// pressing L1 then R1 never passes through the L1 layer.
    #[serde(default = "default_chord_grace")]
    pub chord_grace_s: f64,
    pub layers: PadLayers,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct PadLayers {
    pub base: PadLayer,
    pub l1: PadLayer,
    pub r1: PadLayer,
    pub l1r1: PadLayer,
    pub r2: PadLayer,
    pub r2l1: PadLayer,
    pub r2r1: PadLayer,
    pub r2l1r1: PadLayer,
}

impl PadLayers {
    /// The layer for the modifiers (l1, r1, r2), and its key.
    pub fn get(&self, l1: bool, r1: bool, r2: bool) -> (&'static str, &PadLayer) {
        match (l1, r1, r2) {
            (false, false, false) => ("base", &self.base),
            (true, false, false) => ("l1", &self.l1),
            (false, true, false) => ("r1", &self.r1),
            (true, true, false) => ("l1r1", &self.l1r1),
            (false, false, true) => ("r2", &self.r2),
            (true, false, true) => ("r2l1", &self.r2l1),
            (false, true, true) => ("r2r1", &self.r2r1),
            (true, true, true) => ("r2l1r1", &self.r2l1r1),
        }
    }
}

/// Face order: cross, circle, square, triangle. D-pad order: up, right, down, left.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct PadLayer {
    pub name: String,
    #[serde(default)]
    pub stick: StickTarget,
    pub face_tap: [PadBinding; 4],
    pub face_hold: [PadBinding; 4],
    pub dpad: [PadBinding; 4],
}

/// One button's job. Exactly one of `play`, `sfx`, `emote`, `act`, `ctl` (none = unbound).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct PadBinding {
    pub label: String,
    /// A clip, cue or sequence id; how hard the button is pressed sets its intensity.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub play: Option<String>,
    /// A sound id (file stem in the sfx kit, case/space/underscore-insensitive).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub sfx: Option<String>,
    /// An emote slot (0-7); pressure sets its intensity.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub emote: Option<u8>,
    /// A menu action id (`dj`, `music.next`, `music.stop`, `energy.up`, `alive`, ...).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub act: Option<String>,
    /// A built-in control, active while held: `lift+`/`lift-`, `visor+`/`visor-`,
    /// `roll+`/`roll-`, `twist+`/`twist-` (hero wrist), `reach+`/`reach-` (poker claw tip),
    /// `grip` (the claw closes as hard as the button is pressed), `grip_hold` (keep the grip),
    /// `pin` (leave this arm where it is), `home` (send it home).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub ctl: Option<String>,
}

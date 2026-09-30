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
    /// The bank held: `l1` | `r1`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub bank: Option<String>,
    /// The open menu, if any.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub menu: Option<PadMenuView>,
    /// L2 held: push-to-talk.
    pub talking: bool,
    /// Motion outputs enabled (PS hold toggles).
    pub armed: bool,
    /// L1+R1 held: the right stick's X rolls the head instead of turning the gaze.
    #[serde(default)]
    pub rolling: bool,
    /// The last thing a button did, for a moment ("Nod", "DJ mode on", "refused: ...").
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub last: Option<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct PadMenuView {
    /// Titles from the root to the open level.
    pub path: Vec<String>,
    pub items: Vec<String>,
    pub cursor: usize,
}

/// The operator mapping (`profiles/<robot>/pad.json`): the two hold banks.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct PadMapping {
    /// A face button held this long fires its hold binding instead of its tap one.
    pub tap_hold_s: f64,
    /// PS held this long toggles motion arming.
    pub arm_hold_s: f64,
    pub banks: PadBanks,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct PadBanks {
    pub l1: PadBank,
    pub r1: PadBank,
}

/// Face order: cross, circle, square, triangle. D-pad order: up, right, down, left.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct PadBank {
    pub name: String,
    pub face_tap: [PadBinding; 4],
    pub face_hold: [PadBinding; 4],
    pub dpad: [PadBinding; 4],
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct PadBinding {
    pub label: String,
    /// A clip, cue or sequence id.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub play: Option<String>,
    /// A sound id (file stem in the sfx kit, case/space/underscore-insensitive).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub sfx: Option<String>,
    /// An emote slot (0-7).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub emote: Option<u8>,
}

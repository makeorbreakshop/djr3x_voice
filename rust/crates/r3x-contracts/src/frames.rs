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
}

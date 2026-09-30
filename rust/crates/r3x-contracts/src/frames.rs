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
}

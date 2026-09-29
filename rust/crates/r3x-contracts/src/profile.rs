//! Robot Profile (plan §3a, D8): the root config. `profiles/<name>/robot.json`.

use std::collections::{BTreeMap, HashSet};
use std::path::Path;

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use ts_rs::TS;

#[derive(Debug, thiserror::Error)]
pub enum ProfileError {
    #[error("reading profile: {0}")]
    Io(#[from] std::io::Error),
    #[error("parsing profile: {0}")]
    Parse(#[from] serde_json::Error),
    #[error("invalid profile:\n  {}", .0.join("\n  "))]
    Invalid(Vec<String>),
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct RobotProfile {
    pub name: String,
    #[serde(default)]
    pub label: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub servo_controller: Option<ServoController>,
    pub joints: Vec<Joint>,
    #[serde(default)]
    pub actuators: Vec<Actuator>,
    #[serde(default)]
    pub lights: Vec<LightGroup>,
    #[serde(default)]
    pub audio: AudioConfig,
    /// Emote slot index -> cue id.
    #[serde(default)]
    pub emotes: Vec<String>,
    /// Procedural alive layer -> enabled by default.
    #[serde(default)]
    pub alive: BTreeMap<String, bool>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct ServoController {
    pub channels: u16,
    pub frame_hz: f64,
    pub resolution_us: f64,
    pub supply_volts: f64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, TS, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum JointKind {
    Revolute,
    Prismatic,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, TS, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum Unit {
    Deg,
    Mm,
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct Range {
    pub min: f64,
    pub max: f64,
}

impl Range {
    pub fn contains(&self, inner: &Range) -> bool {
        self.min <= inner.min && inner.max <= self.max
    }

    pub fn clamp(&self, v: f64) -> f64 {
        v.clamp(self.min, self.max)
    }
}

/// A joint. Ranges nest: `hard ⊇ soft ⊇ animation` (motion-control.md §3).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct Joint {
    pub name: String,
    pub kind: JointKind,
    pub unit: Unit,
    #[serde(default)]
    #[ts(optional = nullable)]
    pub parent: Option<String>,
    /// Mechanical stop.
    pub hard: Range,
    /// Where the follower brakes.
    pub soft: Range,
    /// What authored motion may use.
    pub animation: Range,
    /// Per-second limits in the joint's unit.
    pub v_max: f64,
    pub a_max: f64,
    pub j_max: f64,
    /// Only present on the extended build.
    #[serde(default)]
    pub extended: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, TS, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum DriverKind {
    Virtual,
    R3xServo,
    Pca9685,
    Maestro,
    /// Stub.
    FeetechSts,
    /// Stub.
    Dynamixel,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize, TS, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum CalibrationStatus {
    #[default]
    Assumed,
    Measured,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct Calibration {
    /// Pulse at which the joint sits at `center_value`.
    pub center_us: f64,
    #[serde(default)]
    pub center_value: f64,
    #[serde(default)]
    pub trim_us: f64,
    #[serde(default)]
    pub invert: bool,
    /// Servo degrees per joint degree (revolute).
    pub gear: f64,
    /// Prismatic joints: mm of travel per servo degree.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub mm_per_deg: Option<f64>,
    pub pulse_min_us: f64,
    pub pulse_max_us: f64,
    /// Servo travel over the full pulse range.
    pub range_deg: f64,
    #[serde(default)]
    pub status: CalibrationStatus,
}

/// A physical actuator driving one or more joints (coupling factor per joint).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct Actuator {
    pub name: String,
    pub joints: BTreeMap<String, f64>,
    pub driver: DriverKind,
    pub channel: u16,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub servo: Option<String>,
    pub calibration: Calibration,
    #[serde(default)]
    pub extended: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub note: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, TS, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum LedProtocol {
    /// Existing firmware named commands (`SI SE ...`, `Mnnn`, `Bnnn`, `Xn`, `Hxxx`).
    NamedV1,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum LightDriver {
    Virtual,
    SerialLed {
        /// Which board (`face`, `chest`); the identity check expects it.
        board: String,
        /// Env var or path hint for the serial port.
        #[serde(default, skip_serializing_if = "Option::is_none")]
        #[ts(optional)]
        port_hint: Option<String>,
        protocol: LedProtocol,
    },
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct LightPixel {
    pub kind: String,
    /// Model space, metres, Y-up, front = +Z.
    pub pos: [f64; 3],
    pub normal: [f64; 3],
    pub w: f64,
    pub h: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct LightGroup {
    pub name: String,
    pub pixels: u16,
    /// Per-pixel placement, when known. Length must equal `pixels`.
    #[serde(default)]
    pub layout: Vec<LightPixel>,
    /// Named channels (stage fixtures). Length must equal `pixels` when present.
    #[serde(default)]
    pub channels: Vec<String>,
    pub driver: LightDriver,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct AudioOutput {
    pub name: String,
    /// Device name; `None` = system default.
    #[serde(default)]
    #[ts(optional = nullable)]
    pub device: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct Ducking {
    /// Music gain while ducked, 0..1.
    pub level: f64,
    pub ramp_ms: f64,
}

impl Default for Ducking {
    fn default() -> Self {
        Self { level: 0.5, ramp_ms: 80.0 }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct AudioConfig {
    #[serde(default)]
    pub outputs: Vec<AudioOutput>,
    #[serde(default)]
    pub ducking: Ducking,
    /// Mouth amplitude rate (plan §7b: one rate, here).
    #[serde(default = "default_mouth_hz")]
    pub mouth_hz: f64,
}

fn default_mouth_hz() -> f64 {
    30.0
}

impl Default for AudioConfig {
    fn default() -> Self {
        Self { outputs: Vec::new(), ducking: Ducking::default(), mouth_hz: default_mouth_hz() }
    }
}

impl RobotProfile {
    /// Parse and validate.
    pub fn from_json(s: &str) -> Result<Self, ProfileError> {
        let p: Self = serde_json::from_str(s)?;
        p.validate()?;
        Ok(p)
    }

    pub fn load(path: impl AsRef<Path>) -> Result<Self, ProfileError> {
        Self::from_json(&std::fs::read_to_string(path)?)
    }

    pub fn joint(&self, name: &str) -> Option<&Joint> {
        self.joints.iter().find(|j| j.name == name)
    }

    /// Report every problem, not just the first.
    pub fn validate(&self) -> Result<(), ProfileError> {
        let mut errs = Vec::new();
        let mut names = HashSet::new();
        for j in &self.joints {
            if !names.insert(j.name.as_str()) {
                errs.push(format!("duplicate joint {}", j.name));
            }
        }
        for j in &self.joints {
            let n = &j.name;
            for (label, r) in [("hard", j.hard), ("soft", j.soft), ("animation", j.animation)] {
                if !(r.min.is_finite() && r.max.is_finite() && r.min < r.max) {
                    errs.push(format!("{n}: {label} range {}..{} is empty", r.min, r.max));
                }
            }
            if !j.hard.contains(&j.soft) {
                errs.push(format!("{n}: soft range not inside hard range"));
            }
            if !j.soft.contains(&j.animation) {
                errs.push(format!("{n}: animation range not inside soft range"));
            }
            if !(j.v_max > 0.0 && j.a_max > 0.0 && j.j_max > 0.0) {
                errs.push(format!("{n}: v_max/a_max/j_max must be > 0"));
            }
            let unit_ok = matches!(
                (j.kind, j.unit),
                (JointKind::Revolute, Unit::Deg) | (JointKind::Prismatic, Unit::Mm)
            );
            if !unit_ok {
                errs.push(format!("{n}: unit {:?} does not match kind {:?}", j.unit, j.kind));
            }
            if let Some(p) = &j.parent {
                if !names.contains(p.as_str()) {
                    errs.push(format!("{n}: unknown parent {p}"));
                }
            }
        }
        // Parent chains must terminate (no cycles).
        for j in &self.joints {
            let mut cur = j.parent.as_deref();
            let mut steps = 0;
            while let Some(p) = cur {
                steps += 1;
                if steps > self.joints.len() {
                    errs.push(format!("{}: parent chain is cyclic", j.name));
                    break;
                }
                cur = self.joint(p).and_then(|pj| pj.parent.as_deref());
            }
        }

        let mut driven = HashSet::new();
        let mut channels = HashSet::new();
        for a in &self.actuators {
            let n = &a.name;
            if a.joints.is_empty() {
                errs.push(format!("actuator {n}: drives no joints"));
            }
            for joint in a.joints.keys() {
                if !names.contains(joint.as_str()) {
                    errs.push(format!("actuator {n}: unknown joint {joint}"));
                }
                if !driven.insert(joint.as_str()) {
                    errs.push(format!("actuator {n}: joint {joint} already driven"));
                }
            }
            if a.driver != DriverKind::Virtual && !channels.insert((a.driver, a.channel)) {
                errs.push(format!("actuator {n}: {:?} channel {} already used", a.driver, a.channel));
            }
            let c = &a.calibration;
            if c.pulse_min_us >= c.pulse_max_us {
                errs.push(format!("actuator {n}: pulse range empty"));
            } else if !(c.pulse_min_us..=c.pulse_max_us).contains(&c.center_us) {
                errs.push(format!("actuator {n}: center_us outside pulse range"));
            }
            if !(c.gear > 0.0 && c.range_deg > 0.0) {
                errs.push(format!("actuator {n}: gear and range_deg must be > 0"));
            }
            if let Some(ctl) = &self.servo_controller {
                if a.driver == DriverKind::R3xServo && a.channel >= ctl.channels {
                    errs.push(format!("actuator {n}: channel {} >= controller channels", a.channel));
                }
            }
        }

        let mut groups = HashSet::new();
        for g in &self.lights {
            if !groups.insert(g.name.as_str()) {
                errs.push(format!("duplicate light group {}", g.name));
            }
            if !g.layout.is_empty() && g.layout.len() != g.pixels as usize {
                errs.push(format!("light {}: layout has {} entries for {} pixels", g.name, g.layout.len(), g.pixels));
            }
            if !g.channels.is_empty() && g.channels.len() != g.pixels as usize {
                errs.push(format!("light {}: {} channels for {} pixels", g.name, g.channels.len(), g.pixels));
            }
        }
        if self.emotes.iter().any(|e| e.is_empty()) {
            errs.push("emotes: empty cue id".into());
        }
        if !(0.0..=1.0).contains(&self.audio.ducking.level) {
            errs.push("audio.ducking.level must be in 0..1".into());
        }
        if !(1.0..=200.0).contains(&self.audio.mouth_hz) {
            errs.push("audio.mouth_hz must be in 1..200".into());
        }

        if errs.is_empty() {
            Ok(())
        } else {
            Err(ProfileError::Invalid(errs))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn r3x_path() -> std::path::PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../profiles/r3x/robot.json")
    }

    #[test]
    fn r3x_profile_loads_and_validates() {
        let p = RobotProfile::load(r3x_path()).unwrap();
        assert_eq!(p.name, "r3x");
        assert_eq!(p.joints.len(), 21);
        assert_eq!(p.actuators.len(), 17);
        let chest = p.lights.iter().find(|g| g.name == "chest").unwrap();
        assert_eq!((chest.pixels, chest.layout.len()), (33, 33));
        assert_eq!(p.emotes.len(), 8);
        // every joint has an actuator in the extended build
        let driven: HashSet<_> = p.actuators.iter().flat_map(|a| a.joints.keys()).collect();
        assert!(p.joints.iter().all(|j| driven.contains(&j.name)));
    }

    #[test]
    fn validation_reports_every_problem() {
        let mut p = RobotProfile::load(r3x_path()).unwrap();
        p.joints[0].soft.max = p.joints[0].hard.max + 1.0; // soft escapes hard
        p.joints[1].animation.min = p.joints[1].soft.min - 1.0; // animation escapes soft
        p.actuators[0].joints.insert("nope".into(), 1.0); // unknown joint
        p.actuators[1].channel = p.actuators[0].channel; // duplicate channel
        p.joints[2].parent = Some("ghost".into());
        let ProfileError::Invalid(errs) = p.validate().unwrap_err() else { panic!() };
        assert_eq!(errs.len(), 5, "{errs:#?}");
    }

    #[test]
    fn parent_cycle_is_rejected() {
        let mut p = RobotProfile::load(r3x_path()).unwrap();
        let first = p.joints[0].name.clone();
        let second = p.joints[1].name.clone();
        p.joints[0].parent = Some(second);
        p.joints[1].parent = Some(first);
        assert!(matches!(p.validate(), Err(ProfileError::Invalid(e)) if e.iter().any(|m| m.contains("cyclic"))));
    }
}

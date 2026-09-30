//! Electronics packages: `profiles/electronics/<id>.json`, selected by a Robot Profile's
//! `electronics` field. A package is the droid's boards (model, size, mount, connectors,
//! power), its LED layout per light group (pixel count, positions in the body frame, chain
//! order), which profile light groups and actuators it drives with which driver/firmware,
//! a power budget, a wiring list and a BOM.
//!
//! The selected package's `lights` become the profile's light groups (see
//! [`crate::profile::RobotProfile::resolve_electronics`]), so the performer, the drivers and
//! the sim all see the package's real LED counts and positions.
//!
//! Units: positions in metres in the body frame at the zero pose (Y up, front = +Z, +X the
//! droid's left), the same space as [`LightPixel`]; board sizes in mm; currents in mA.

use std::collections::HashSet;

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use ts_rs::TS;

use crate::profile::{LightDriver, LightGroup, LightPixel};

/// The packages shipped with the repo, for readers without a filesystem (the sim's wasm
/// performer). `RobotProfile::load` prefers the files next to the profile.
pub const BUILTIN: [(&str, &str); 3] = [
    ("r3x_native", include_str!("../../../../profiles/electronics/r3x_native.json")),
    ("grnwave_full_led", include_str!("../../../../profiles/electronics/grnwave_full_led.json")),
    ("community_morton", include_str!("../../../../profiles/electronics/community_morton.json")),
];

/// How far our runtime drives something.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, TS, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum Support {
    /// A driver and firmware exist in this repo.
    Driven,
    /// Some of it is driven (a package whose boards are a mix).
    Partial,
    /// Described (dimensions, wiring, BOM) but nothing here talks to it.
    Listed,
}

/// Which LED board emulator the performer runs for this package's lights.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize, TS, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum LedEmulator {
    /// `rex_face_v3_clean` + `rex_chest_v1` (eyes 14, mouth 8, chest 33).
    #[default]
    Native,
    /// `firmware/grnwave_nano`: 3 body boards (96) + eyes (2) + mouth (8).
    Grnwave,
    /// No LED board we drive: the performer keeps the native emulators for the preview.
    None,
}

/// A board's place in the body: which bracket holds it and its pose in the body frame.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct BoardMount {
    /// The frame bracket / printed part it is fastened to.
    pub bracket: String,
    /// Rig link it rides on (`torso_middle`, `head_tilt`, ...; `torso_lower` = the base).
    pub link: String,
    /// Board centre, metres, body frame at zero pose.
    pub t: [f64; 3],
    /// Orientation, unit quaternion `[x, y, z, w]`; board +Z = its component side.
    #[serde(default = "identity_q")]
    pub q: [f64; 4],
    #[serde(default)]
    pub inferred: bool,
    #[serde(default)]
    pub inferred_note: String,
}

fn identity_q() -> [f64; 4] {
    [0.0, 0.0, 0.0, 1.0]
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct Connector {
    pub id: String,
    /// e.g. `dupont_3p_2.54`, `usb_mini_b`, `screw_2p_5.08`, `jst_sm_3p`.
    pub kind: String,
    /// Pin names in connector order.
    pub pins: Vec<String>,
    #[serde(default)]
    pub note: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct Current {
    pub idle_ma: f64,
    pub typical_ma: f64,
    pub max_ma: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct Board {
    pub id: String,
    pub model: String,
    /// What it does in this droid, one line.
    pub role: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub url: Option<String>,
    /// Length x width x height, mm.
    pub dims_mm: [f64; 3],
    pub mount: BoardMount,
    pub connectors: Vec<Connector>,
    pub volts: f64,
    /// The board's own draw plus what it powers directly (LEDs on it).
    pub current: Current,
    /// Repo path of the firmware we flash, if we own it.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub firmware: Option<String>,
    /// `r3x-drivers` service name (`driver.face`, `driver.grnwave`, ...).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub driver: Option<String>,
    pub support: Support,
    #[serde(default)]
    pub inferred: bool,
    #[serde(default)]
    pub inferred_note: String,
}

/// One light group of the package: becomes a profile [`LightGroup`].
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct PackageLight {
    /// Profile light group name (`eyes`, `mouth`, `chest`, `body`).
    pub name: String,
    pub pixels: u16,
    /// Per pixel, in chain order. Length = `pixels`.
    pub layout: Vec<LightPixel>,
    pub driver: LightDriver,
    /// The board the LEDs are on.
    pub board: String,
    /// Rig link the group rides on (positions are given at the zero pose).
    pub link: String,
    /// The data line that feeds it (`nano.D4`) and this group's first index on that line.
    pub data_line: String,
    pub chain_start: u16,
    /// LED part (`WS2812B 5050`, `8 mm PL9823`).
    pub led: String,
    /// One LED at full white (all three channels at 255), mA.
    pub ma_per_led: f64,
    /// The semantic outputs it shows (`eyes`, `mouth`, `chest_status`, `tempo`, ...).
    #[serde(default)]
    pub serves: Vec<String>,
    #[serde(default)]
    pub inferred: bool,
    #[serde(default)]
    pub inferred_note: String,
}

impl PackageLight {
    pub fn to_light_group(&self) -> LightGroup {
        LightGroup {
            name: self.name.clone(),
            pixels: self.pixels,
            layout: self.layout.clone(),
            channels: Vec::new(),
            driver: self.driver.clone(),
        }
    }
}

/// Which of the profile's actuators (or `*` = all servo channels) a board drives.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct ActuatorDrive {
    pub actuators: Vec<String>,
    pub board: String,
    /// Profile `DriverKind` (`r3x_servo`, `maestro`, ...).
    pub driver: String,
    pub support: Support,
    #[serde(default)]
    pub note: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct Rail {
    pub name: String,
    pub volts: f64,
    /// Everything on the rail at once at its worst (LEDs full white, servos stalled), mA.
    pub max_ma: f64,
    /// A show at the firmware's brightness, mA.
    pub typical_ma: f64,
    /// Board ids on the rail.
    pub loads: Vec<String>,
    /// The supply this package calls for.
    pub psu: String,
    pub psu_amps: f64,
    #[serde(default)]
    pub note: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct Wire {
    /// `board.connector[.pin]`, a BOM item, or `host`.
    pub from: String,
    pub to: String,
    pub signal: String,
    pub connector: String,
    pub awg: u8,
    #[serde(default)]
    pub note: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct BomItem {
    pub item: String,
    pub qty: u32,
    /// `board`, `led`, `cable`, `power`, `hardware`, `optics`.
    pub category: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub url: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub unit_usd: Option<f64>,
    #[serde(default)]
    pub note: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, TS, JsonSchema)]
pub struct ElectronicsPackage {
    pub id: String,
    pub label: String,
    #[serde(default)]
    pub vendor: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub url: Option<String>,
    pub description: String,
    pub support: Support,
    #[serde(default)]
    pub emulator: LedEmulator,
    pub boards: Vec<Board>,
    #[serde(default)]
    pub lights: Vec<PackageLight>,
    #[serde(default)]
    pub actuators: Vec<ActuatorDrive>,
    pub power: Vec<Rail>,
    #[serde(default)]
    pub wiring: Vec<Wire>,
    #[serde(default)]
    pub bom: Vec<BomItem>,
    #[serde(default)]
    pub notes: Vec<String>,
}

impl ElectronicsPackage {
    pub fn from_json(s: &str) -> Result<Self, crate::ProfileError> {
        let p: Self = serde_json::from_str(s)?;
        p.validate()?;
        Ok(p)
    }

    /// A package shipped with the repo, by id.
    pub fn builtin(id: &str) -> Option<Result<Self, crate::ProfileError>> {
        BUILTIN.iter().find(|(k, _)| *k == id).map(|(_, s)| Self::from_json(s))
    }

    pub fn board(&self, id: &str) -> Option<&Board> {
        self.boards.iter().find(|b| b.id == id)
    }

    pub fn light(&self, name: &str) -> Option<&PackageLight> {
        self.lights.iter().find(|l| l.name == name)
    }

    /// Full-white current of a light group, mA.
    pub fn light_max_ma(&self, name: &str) -> f64 {
        self.light(name).map_or(0.0, |l| f64::from(l.pixels) * l.ma_per_led)
    }

    /// Report every problem, not just the first.
    pub fn validate(&self) -> Result<(), crate::ProfileError> {
        let mut errs = Vec::new();
        let id = &self.id;
        if id.is_empty() || !id.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_') {
            errs.push(format!("package id {id:?} must be [a-z0-9_]+"));
        }
        let mut boards = HashSet::new();
        for b in &self.boards {
            if !boards.insert(b.id.as_str()) {
                errs.push(format!("{id}: duplicate board {}", b.id));
            }
            if !b.dims_mm.iter().all(|d| d.is_finite() && *d > 0.0) {
                errs.push(format!("{id}: board {}: dims_mm must be > 0", b.id));
            }
            let qn: f64 = b.mount.q.iter().map(|v| v * v).sum::<f64>().sqrt();
            if (qn - 1.0).abs() > 1e-3 {
                errs.push(format!("{id}: board {}: mount.q is not a unit quaternion", b.id));
            }
            let c = b.current;
            if !(0.0 <= c.idle_ma && c.idle_ma <= c.typical_ma && c.typical_ma <= c.max_ma) {
                errs.push(format!("{id}: board {}: current must be idle <= typical <= max", b.id));
            }
            if b.inferred && b.inferred_note.is_empty() || b.mount.inferred && b.mount.inferred_note.is_empty() {
                errs.push(format!("{id}: board {}: inferred without an inferred_note", b.id));
            }
            let mut conns = HashSet::new();
            for cn in &b.connectors {
                if !conns.insert(cn.id.as_str()) {
                    errs.push(format!("{id}: board {}: duplicate connector {}", b.id, cn.id));
                }
            }
        }
        let mut names = HashSet::new();
        let mut spans: Vec<(&str, u16, u16, &str)> = Vec::new();
        for l in &self.lights {
            let n = &l.name;
            if !names.insert(n.as_str()) {
                errs.push(format!("{id}: duplicate light group {n}"));
            }
            if !boards.contains(l.board.as_str()) {
                errs.push(format!("{id}: light {n}: unknown board {}", l.board));
            }
            if l.layout.len() != l.pixels as usize {
                errs.push(format!("{id}: light {n}: layout has {} entries for {} pixels", l.layout.len(), l.pixels));
            }
            if l.ma_per_led.is_nan() || l.ma_per_led <= 0.0 {
                errs.push(format!("{id}: light {n}: ma_per_led must be > 0"));
            }
            if l.inferred && l.inferred_note.is_empty() {
                errs.push(format!("{id}: light {n}: inferred without an inferred_note"));
            }
            spans.push((l.data_line.as_str(), l.chain_start, l.chain_start + l.pixels, n.as_str()));
        }
        // Groups sharing a data line must not overlap in the chain.
        for (i, a) in spans.iter().enumerate() {
            for b in &spans[i + 1..] {
                if a.0 == b.0 && a.1 < b.2 && b.1 < a.2 {
                    errs.push(format!("{id}: lights {} and {} overlap on {}", a.3, b.3, a.0));
                }
            }
        }
        for a in &self.actuators {
            if !boards.contains(a.board.as_str()) {
                errs.push(format!("{id}: actuator drive: unknown board {}", a.board));
            }
        }
        for r in &self.power {
            for load in &r.loads {
                if !boards.contains(load.as_str()) {
                    errs.push(format!("{id}: rail {}: unknown load {load}", r.name));
                }
            }
            if r.typical_ma.is_nan() || r.typical_ma > r.max_ma {
                errs.push(format!("{id}: rail {}: typical_ma > max_ma", r.name));
            }
            if r.psu_amps * 1000.0 < r.typical_ma {
                errs.push(format!("{id}: rail {}: {} A PSU is below the typical {} mA", r.name, r.psu_amps, r.typical_ma));
            }
            // LED groups on the rail: the rail's max must cover them at full white.
            let led_ma: f64 = self
                .lights
                .iter()
                .filter(|l| r.loads.contains(&l.board))
                .map(|l| f64::from(l.pixels) * l.ma_per_led)
                .sum();
            if r.max_ma + 1e-6 < led_ma {
                errs.push(format!("{id}: rail {}: max_ma {} below its LEDs at full white ({led_ma} mA)", r.name, r.max_ma));
            }
        }
        let board_of = |end: &str| end.split('.').next().unwrap_or("").to_string();
        let bom_items: HashSet<&str> = self.bom.iter().map(|b| b.item.as_str()).collect();
        for w in &self.wiring {
            for end in [&w.from, &w.to] {
                if end == "host" || bom_items.contains(end.as_str()) {
                    continue;
                }
                let b = board_of(end);
                let conn = end.split('.').nth(1);
                match self.board(&b) {
                    None => errs.push(format!("{id}: wire {} -> {}: unknown end {end}", w.from, w.to)),
                    Some(board) => {
                        if let Some(c) = conn {
                            if !board.connectors.iter().any(|x| x.id == c) {
                                errs.push(format!("{id}: wire {} -> {}: board {b} has no connector {c}", w.from, w.to));
                            }
                        }
                    }
                }
            }
        }
        if errs.is_empty() {
            Ok(())
        } else {
            Err(crate::ProfileError::Invalid(errs))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn builtin_packages_validate() {
        for (id, _) in BUILTIN {
            let p = ElectronicsPackage::builtin(id).unwrap().unwrap_or_else(|e| panic!("{id}: {e}"));
            assert_eq!(p.id, id);
        }
    }

    #[test]
    fn builtin_matches_the_files() {
        let dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../profiles/electronics");
        let mut on_disk: Vec<String> = std::fs::read_dir(&dir)
            .unwrap()
            .filter_map(|e| e.ok()?.file_name().to_str()?.strip_suffix(".json").map(str::to_string))
            .collect();
        on_disk.sort();
        let mut built: Vec<String> = BUILTIN.iter().map(|(k, _)| k.to_string()).collect();
        built.sort();
        assert_eq!(on_disk, built, "add new package files to BUILTIN");
    }

    #[test]
    fn grnwave_layout_is_the_sample_sketch_chain() {
        let p = ElectronicsPackage::builtin("grnwave_full_led").unwrap().unwrap();
        let body = p.light("body").unwrap();
        let eyes = p.light("eyes").unwrap();
        // DJLEDNanoV2.ino: NUM_LEDS 98 on D4 - 3 x 32 body, then the eyes at 96-97.
        assert_eq!((body.pixels, body.chain_start), (96, 0));
        assert_eq!((eyes.pixels, eyes.chain_start, eyes.data_line.as_str()), (2, 96, body.data_line.as_str()));
        assert_eq!(p.emulator, LedEmulator::Grnwave);
        // 25 light pipes for 24 small LEDs: the BOM carries the pipes.
        assert!(p.bom.iter().any(|b| b.item.contains("VLP-600-R") && b.qty == 25));
    }

    #[test]
    fn validation_reports_every_problem() {
        let mut p = ElectronicsPackage::builtin("grnwave_full_led").unwrap().unwrap();
        p.lights[0].pixels += 1; // layout length mismatch
        p.lights[1].chain_start = 0; // overlaps the body on D4
        p.boards[1].id = p.boards[0].id.clone(); // duplicate board
        p.power[0].psu_amps = 0.01; // PSU below typical
        let crate::ProfileError::Invalid(errs) = p.validate().unwrap_err() else { panic!() };
        assert!(errs.len() >= 4, "{errs:#?}");
    }
}

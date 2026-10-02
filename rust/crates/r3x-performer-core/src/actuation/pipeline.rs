//! The actuation pipeline (port of `pipeline.ts`) - also the hardware contract.
//!
//! ```text
//! behaviour targets (joint deg, or mm for the head lift)
//!   -> JerkLimitedFollower per channel @ 200 Hz (v/a/j limits, soft-limit braking)
//!      ...or a direct target (Maestro script) through the Maestro's speed/accel ramp
//!   -> output stage @ 50 Hz: joint value -> servo deg (gear/pinion, invert)
//!      -> calibrated pulse -> controller resolution            <- Frame
//!   -> servo plant @ 1 kHz (sim only): latency, deadband, saturating P loop,
//!      load-derated speed, torque/inertia accel limit, gear backlash
//! ```

use super::servos::{servo, ServoModel};
use super::trajectory::{JerkLimitedFollower, MotionLimits};
use crate::rng::Rng;
use indexmap::IndexMap;
use r3x_contracts::profile::{CalibrationStatus, JointCoupling, JointKind, RobotProfile};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashMap};

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ChannelConfig {
    pub ch: usize,
    pub name: String,
    pub servo: String,
    pub gear: f64,
    /// Driven joints and their coupling factor; the first is the primary joint.
    pub joints: IndexMap<String, f64>,
    pub margin: f64,
    pub v_max: f64,
    pub a_max: f64,
    pub j_max: f64,
    #[serde(default)]
    pub note: Option<String>,
    #[serde(default)]
    pub calibration: Option<String>,
    /// Pulse at which the joint sits at center_value (the model's rest pose by default).
    #[serde(default)]
    pub center_us: Option<f64>,
    #[serde(default)]
    pub center_value: Option<f64>,
    #[serde(default)]
    pub trim_us: Option<f64>,
    #[serde(default)]
    pub invert: Option<bool>,
    /// Prismatic joints: mm of travel per servo degree (rack and pinion).
    #[serde(default)]
    pub mm_per_deg: Option<f64>,
    /// Maestro channel settings (0 = unlimited): speed in 0.25us/10ms, accel in 0.25us/10ms/80ms.
    #[serde(default)]
    pub maestro_speed: Option<f64>,
    #[serde(default)]
    pub maestro_accel: Option<f64>,
    /// Explicit soft range `[min, max]` (a Robot Profile's `soft`); None = computed from
    /// the joint's hard range, the servo's reach and `margin`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub soft: Option<[f64; 2]>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ControllerType {
    Custom,
    Maestro,
    Pca9685,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ControllerConfig {
    #[serde(rename = "type")]
    pub kind: ControllerType,
    pub channels: usize,
    #[serde(default)]
    pub frame_hz: Option<f64>,
    #[serde(default)]
    pub resolution_us: Option<f64>,
    #[serde(default)]
    pub period_ms: Option<f64>,
    #[serde(default)]
    pub oscillator_hz: Option<f64>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ProfileDoc {
    pub label: String,
    pub controller: ControllerConfig,
    pub supply_volts: f64,
    #[serde(default)]
    pub inherit: Option<String>,
    pub channels: Vec<ChannelConfig>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ServoMap {
    pub default: String,
    pub profiles: IndexMap<String, ProfileDoc>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum JointType {
    Revolute,
    Prismatic,
}

/// What the pipeline needs to know about a rig joint: its hard range and type.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct JointSpec {
    pub name: String,
    pub min: f64,
    pub max: f64,
    #[serde(rename = "type")]
    pub kind: JointType,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct JointDynamics {
    pub mass_kg: f64,
    pub gravity_torque_worst_kgcm: f64,
    pub inertia_kgm2: f64,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct Frame {
    pub frame: u64,
    /// s
    pub t: f64,
    /// Per controller channel, in controller units (us, quarter-us or counts); 0 = off.
    pub targets: Vec<i64>,
}

/// Rig joints from a Robot Profile (hard ranges).
pub fn profile_joints(p: &RobotProfile) -> Vec<JointSpec> {
    p.joints
        .iter()
        .map(|j| JointSpec {
            name: j.name.clone(),
            min: j.hard.min,
            max: j.hard.max,
            kind: if j.kind == JointKind::Prismatic {
                JointType::Prismatic
            } else {
                JointType::Revolute
            },
        })
        .collect()
}

/// A Robot Profile's actuators as a pipeline profile (the servo controller is ours:
/// whole microseconds at `frame_hz`). Soft ranges and motion limits come from the primary
/// joint; the primary joint is the actuator's joint listed first in the profile's `joints`.
pub fn profile_doc(p: &RobotProfile) -> Result<ProfileDoc, String> {
    let ctl = p.servo_controller.as_ref();
    let order = |j: &String| {
        p.joints
            .iter()
            .position(|x| &x.name == j)
            .unwrap_or(usize::MAX)
    };
    let channels = p
        .actuators
        .iter()
        .map(|a| {
            let mut joints: Vec<(String, f64)> =
                a.joints.iter().map(|(k, v)| (k.clone(), *v)).collect();
            joints.sort_by_key(|(j, _)| order(j));
            let primary = p
                .joint(
                    &joints
                        .first()
                        .ok_or_else(|| format!("actuator {} drives no joint", a.name))?
                        .0,
                )
                .ok_or("unknown joint")?;
            let c = &a.calibration;
            Ok(ChannelConfig {
                ch: usize::from(a.channel),
                name: a.name.clone(),
                servo: a
                    .servo
                    .clone()
                    .ok_or_else(|| format!("actuator {}: no servo model", a.name))?,
                gear: c.gear,
                joints: joints.into_iter().collect(),
                margin: 0.0,
                v_max: primary.v_max,
                a_max: primary.a_max,
                j_max: primary.j_max,
                note: a.note.clone(),
                calibration: Some(
                    if c.status == CalibrationStatus::Measured {
                        "measured"
                    } else {
                        "assumed"
                    }
                    .into(),
                ),
                center_us: Some(c.center_us),
                center_value: Some(c.center_value),
                trim_us: Some(c.trim_us),
                invert: Some(c.invert),
                mm_per_deg: c.mm_per_deg,
                maestro_speed: None,
                maestro_accel: None,
                soft: Some([primary.soft.min, primary.soft.max]),
            })
        })
        .collect::<Result<Vec<_>, String>>()?;
    Ok(ProfileDoc {
        label: p.label.clone(),
        controller: ControllerConfig {
            kind: ControllerType::Custom,
            channels: ctl.map_or(18, |c| usize::from(c.channels)),
            frame_hz: Some(ctl.map_or(50.0, |c| c.frame_hz)),
            resolution_us: Some(ctl.map_or(1.0, |c| c.resolution_us)),
            period_ms: None,
            oscillator_hz: None,
        },
        supply_volts: ctl.map_or(6.0, |c| c.supply_volts),
        inherit: None,
        channels,
    })
}

/// The committed actuation profiles (`sim/web/src/actuation/servo_map.json`).
pub fn servo_map() -> ServoMap {
    serde_json::from_str(include_str!(
        "../../../../../sim/web/src/actuation/servo_map.json"
    ))
    .expect("servo_map.json parses")
}

/// The committed rig joint table (`sim/web/src/show/rig_limits.json`).
pub fn rig_joints() -> Vec<JointSpec> {
    #[derive(Deserialize)]
    struct Lim {
        min: f64,
        max: f64,
        #[serde(rename = "type")]
        kind: JointType,
    }
    #[derive(Deserialize)]
    struct Doc {
        joints: IndexMap<String, Lim>,
    }
    let d: Doc = serde_json::from_str(include_str!(
        "../../../../../sim/web/src/show/rig_limits.json"
    ))
    .expect("rig_limits.json parses");
    d.joints
        .into_iter()
        .map(|(name, l)| JointSpec {
            name,
            min: l.min,
            max: l.max,
            kind: l.kind,
        })
        .collect()
}

impl ServoMap {
    /// A profile with its `inherit` chain's channels prepended.
    pub fn resolve(&self, name: &str) -> Result<ProfileDoc, String> {
        let p = self
            .profiles
            .get(name)
            .ok_or_else(|| format!("unknown actuation profile {name}"))?;
        let Some(base) = &p.inherit else {
            return Ok(p.clone());
        };
        let base = self.resolve(base)?;
        let mut out = p.clone();
        out.channels = base
            .channels
            .into_iter()
            .chain(p.channels.iter().cloned())
            .collect();
        Ok(out)
    }
}

const KGCM_TO_NM: f64 = 0.0980665;
const PLANT_HZ: f64 = 1000.0;
/// 200 Hz follower.
const TRAJ_EVERY: u64 = 5;

#[derive(Clone, Debug)]
pub struct Channel {
    pub cfg: ChannelConfig,
    pub model: ServoModel,
    pub follower: JerkLimitedFollower,
    pub us_per_deg: f64,
    pub center_us: f64,
    pub center_value: f64,
    pub dir: f64,
    pub prismatic: bool,
    pub unit: &'static str,
    pub primary_joint: String,
    /// Load at the servo horn, kg.cm (worst-case gravity over driven joints, through the gearing).
    pub load_kgcm: f64,
    pub stall_kgcm: f64,
    pub utilisation: f64,
    /// servo deg/s
    pub speed_no_load: f64,
    pub speed_loaded: f64,
    /// servo deg/s^2 from spare torque / reflected inertia
    pub accel_max: f64,
    pub warnings: Vec<String>,
    /// Direct pulse target (script/raw command) in us, or None when the follower drives.
    pub direct_us: Option<f64>,
    ramp_us: f64,
    ramp_v: f64,
    pub us: f64,
    /// Controller units.
    pub target: i64,
    cmd: f64,
    motor_v: f64,
    pub motor: f64,
    pub out: f64,
}

impl Channel {
    pub fn new(
        cfg: ChannelConfig,
        joints: &HashMap<String, JointSpec>,
        dynamics: &BTreeMap<String, JointDynamics>,
        volts: f64,
    ) -> Result<Channel, String> {
        let model = servo(&cfg.servo).ok_or_else(|| format!("unknown servo {}", cfg.servo))?;
        let primary_joint = cfg
            .joints
            .keys()
            .next()
            .cloned()
            .ok_or("channel drives no joint")?;
        let spec = joints
            .get(&primary_joint)
            .ok_or_else(|| format!("channel {} drives unknown joint {primary_joint}", cfg.ch))?;
        let prismatic = spec.kind == JointType::Prismatic;
        let mut ch = Channel {
            us_per_deg: model.us_per_deg(),
            center_us: cfg.center_us.unwrap_or((model.min_us + model.max_us) / 2.0),
            center_value: cfg.center_value.unwrap_or(0.0),
            dir: if cfg.invert.unwrap_or(false) {
                -1.0
            } else {
                1.0
            },
            prismatic,
            unit: if prismatic { "mm" } else { "deg" },
            primary_joint,
            follower: JerkLimitedFollower::new(
                MotionLimits {
                    v_max: cfg.v_max,
                    a_max: cfg.a_max,
                    j_max: cfg.j_max,
                },
                0.0,
                0.0,
                0.0,
            ),
            load_kgcm: 0.0,
            stall_kgcm: 0.0,
            utilisation: 0.0,
            speed_no_load: 0.0,
            speed_loaded: 0.0,
            accel_max: 0.0,
            warnings: Vec::new(),
            direct_us: None,
            ramp_us: 1500.0,
            ramp_v: 0.0,
            us: 1500.0,
            target: 0,
            cmd: 0.0,
            motor_v: 0.0,
            motor: 0.0,
            out: 0.0,
            model,
            cfg,
        };

        // Reach of the servo, in joint units, around the centre pulse.
        let lo_servo = (ch.model.min_us - ch.center_us) / ch.us_per_deg;
        let hi_servo = (ch.model.max_us - ch.center_us) / ch.us_per_deg;
        let (a, b) = (ch.servo_to_value(lo_servo), ch.servo_to_value(hi_servo));
        let reach = (a.min(b), a.max(b));
        let (lo, hi) = match ch.cfg.soft {
            Some([lo, hi]) => (lo, hi),
            None => (
                spec.min.max(reach.0) + ch.cfg.margin,
                spec.max.min(reach.1) - ch.cfg.margin,
            ),
        };
        if spec.min < reach.0 - 0.5 || spec.max > reach.1 + 0.5 {
            ch.warnings.push(format!(
                "joint range {}..{} {} exceeds servo reach {:.0}..{:.0}",
                spec.min, spec.max, ch.unit, reach.0, reach.1
            ));
        }
        let rest = if 0.0 < lo {
            lo
        } else if 0.0 > hi {
            hi
        } else {
            0.0
        };
        ch.follower = JerkLimitedFollower::new(ch.follower.limits, lo, hi, rest);
        ch.cmd = ch.value_to_servo(rest);
        ch.motor = ch.cmd;
        ch.out = ch.cmd;
        ch.us = ch.value_to_us(rest);
        ch.ramp_us = ch.us;

        let mut load = 0.0;
        let mut inertia = 0.0;
        for j in ch.cfg.joints.keys() {
            let Some(d) = dynamics.get(j) else { continue };
            if prismatic {
                // Lifting force m*g through a pinion of radius r = mmPerDeg * 180/pi.
                let r_cm = ch.cfg.mm_per_deg.unwrap_or(0.2) * 180.0 / std::f64::consts::PI / 10.0;
                load += d.mass_kg * r_cm;
            } else {
                load += d.gravity_torque_worst_kgcm / ch.cfg.gear;
                inertia += d.inertia_kgm2 / (ch.cfg.gear * ch.cfg.gear);
            }
        }
        ch.load_kgcm = load;
        ch.stall_kgcm = ch.model.stall_torque(volts);
        ch.utilisation = ch.load_kgcm / ch.stall_kgcm;
        ch.speed_no_load = ch.model.no_load_speed(volts);
        ch.speed_loaded = ch.speed_no_load * (1.0 - ch.utilisation).max(0.05);
        let spare_nm = (ch.stall_kgcm - ch.load_kgcm).max(0.05) * KGCM_TO_NM;
        ch.accel_max = (spare_nm / (inertia + 1e-5)) * (180.0 / std::f64::consts::PI);
        if ch.utilisation > 0.5 {
            ch.warnings.push(format!(
                "worst-case load {:.0}% of stall (keep under 30-50%)",
                ch.utilisation * 100.0
            ));
        }
        let cap = (ch.servo_to_value(ch.speed_loaded * 0.7) - ch.servo_to_value(0.0)).abs();
        if ch.cfg.v_max > cap {
            ch.warnings.push(format!(
                "vMax {} {}/s above 70% of loaded speed ({cap:.0})",
                ch.cfg.v_max, ch.unit
            ));
        }
        Ok(ch)
    }

    fn mm_per_deg(&self) -> f64 {
        self.cfg.mm_per_deg.unwrap_or(0.2)
    }

    /// Joint value (deg or mm) -> servo degrees from the centre pulse.
    pub fn value_to_servo(&self, v: f64) -> f64 {
        let d = v - self.center_value;
        self.dir
            * if self.prismatic {
                d / self.mm_per_deg()
            } else {
                d * self.cfg.gear
            }
    }

    pub fn servo_to_value(&self, servo_deg: f64) -> f64 {
        let s = servo_deg * self.dir;
        self.center_value
            + if self.prismatic {
                s * self.mm_per_deg()
            } else {
                s / self.cfg.gear
            }
    }

    /// Joint value -> pulse width, as the servo sink will compute it.
    pub fn value_to_us(&self, v: f64) -> f64 {
        let us = self.center_us
            + self.cfg.trim_us.unwrap_or(0.0)
            + self.value_to_servo(v) * self.us_per_deg;
        us.clamp(self.model.min_us, self.model.max_us)
    }

    pub fn us_to_servo(&self, us: f64) -> f64 {
        (us - self.center_us - self.cfg.trim_us.unwrap_or(0.0)) / self.us_per_deg
    }

    /// Where the pulse is heading this tick: the follower, or a direct target through the
    /// Maestro's speed/accel ramp (speed in 0.25us/10ms, accel in 0.25us/10ms/80ms).
    pub fn pulse_now(&mut self, dt: f64) -> f64 {
        let Some(direct) = self.direct_us else {
            return self.value_to_us(self.follower.x);
        };
        let target = direct.clamp(self.model.min_us, self.model.max_us);
        let v_max = self.cfg.maestro_speed.unwrap_or(0.0) * 25.0; // us/s
        let a_max = self.cfg.maestro_accel.unwrap_or(0.0) * 312.5; // us/s^2
        if v_max == 0.0 && a_max == 0.0 {
            self.ramp_us = target;
            self.ramp_v = 0.0;
            return target;
        }
        let e = target - self.ramp_us;
        let sign = if e > 0.0 {
            1.0
        } else if e < 0.0 {
            -1.0
        } else {
            0.0
        };
        let mut v = if a_max != 0.0 {
            self.ramp_v + (sign * a_max * dt).clamp(-a_max * dt, a_max * dt)
        } else {
            sign * v_max
        };
        if v_max != 0.0 {
            v = v.clamp(-v_max, v_max);
        }
        if a_max != 0.0 {
            let brake = (2.0 * a_max * e.abs()).sqrt();
            v = v.clamp(-brake, brake);
        }
        self.ramp_v = v;
        let step = v * dt;
        self.ramp_us = if step.abs() >= e.abs() {
            target
        } else {
            self.ramp_us + step
        };
        self.ramp_us
    }

    /// Hand control back to the follower without a jump.
    pub fn release_direct(&mut self) {
        if self.direct_us.take().is_some() {
            let x = self.servo_to_value(self.us_to_servo(self.ramp_us));
            self.follower.reset(x);
        }
    }

    pub fn apply_pulse(&mut self, us: f64) {
        self.cmd = self.us_to_servo(us);
    }

    pub fn plant_step(&mut self, dt: f64) {
        let err = self.cmd - self.motor;
        let db = self.model.deadband_us / self.us_per_deg;
        let mut w_des = 0.0;
        if err.abs() > db / 2.0 {
            w_des = (err / self.model.tau_s).clamp(-self.speed_loaded, self.speed_loaded);
        }
        let dw = (w_des - self.motor_v).clamp(-self.accel_max * dt, self.accel_max * dt);
        self.motor_v += dw;
        self.motor += self.motor_v * dt;
        let b = self.model.backlash_deg / 2.0;
        if self.motor - self.out > b {
            self.out = self.motor - b;
        } else if self.out - self.motor > b {
            self.out = self.motor + b;
        }
    }

    pub fn snap(&mut self) {
        self.motor = self.cmd;
        self.out = self.cmd;
        self.motor_v = 0.0;
    }

    /// The joint value the rig shows (deg or mm).
    pub fn value(&self) -> f64 {
        self.servo_to_value(self.out)
    }
}

#[derive(Clone, Debug)]
struct Pending {
    at: f64,
    targets: Vec<i64>,
}

#[derive(Clone, Debug)]
pub struct Actuation {
    pub profile_name: String,
    pub label: String,
    pub controller: ControllerConfig,
    pub channels: Vec<Channel>,
    /// Joint -> index into `channels`.
    pub by_joint: IndexMap<String, usize>,
    /// Controller channel number -> index into `channels`.
    pub by_number: BTreeMap<usize, usize>,
    pub frequency_hz: f64,
    /// Controller resolution in us per unit of Frame.targets.
    pub us_per_unit: f64,
    pub plant_enabled: bool,
    pub frame_no: u64,
    pub sim_time: f64,
    pub last_frame: Frame,
    log: std::collections::VecDeque<Frame>,
    pending: std::collections::VecDeque<Pending>,
    acc: f64,
    tick: u64,
    rng: Rng,
    /// Coupled joint limits (a generated profile's `mech.couplings`): the safety layer clamps
    /// every command to them, and re-clamps a dependent joint when the joint it depends on moves.
    pub couplings: Vec<JointCoupling>,
}

impl Actuation {
    /// The pipeline a Robot Profile describes.
    pub fn from_profile(
        profile: &RobotProfile,
        dynamics: &BTreeMap<String, JointDynamics>,
        rng: Rng,
    ) -> Result<Actuation, String> {
        let mut a = Self::build(
            &profile_joints(profile),
            dynamics,
            profile_doc(profile)?,
            &profile.name,
            rng,
        )?;
        a.couplings = profile.mech.as_ref().map(|m| m.couplings.clone()).unwrap_or_default();
        Ok(a)
    }

    /// A named profile of the sim's servo map (`r3x_animation`, `extended`, Maestro, PCA9685).
    pub fn new(
        joints: &[JointSpec],
        dynamics: &BTreeMap<String, JointDynamics>,
        map: &ServoMap,
        profile: &str,
        rng: Rng,
    ) -> Result<Actuation, String> {
        Self::build(joints, dynamics, map.resolve(profile)?, profile, rng)
    }

    fn build(
        joints: &[JointSpec],
        dynamics: &BTreeMap<String, JointDynamics>,
        p: ProfileDoc,
        profile: &str,
        rng: Rng,
    ) -> Result<Actuation, String> {
        let (frequency_hz, us_per_unit) = match p.controller.kind {
            ControllerType::Pca9685 => {
                let osc = p.controller.oscillator_hz.unwrap_or(25_000_000.0);
                let prescale = (osc / (4096.0 * 50.0) + 0.5).floor() - 1.0;
                let hz = osc / (4096.0 * (prescale + 1.0));
                (hz, 1e6 / hz / 4096.0)
            }
            // Maestro targets are quarter-microseconds.
            ControllerType::Maestro => (1000.0 / p.controller.period_ms.unwrap_or(20.0), 0.25),
            ControllerType::Custom => (
                p.controller.frame_hz.unwrap_or(50.0),
                p.controller.resolution_us.unwrap_or(1.0),
            ),
        };
        let specs: HashMap<String, JointSpec> =
            joints.iter().map(|j| (j.name.clone(), j.clone())).collect();
        let channels = p
            .channels
            .iter()
            .map(|c| Channel::new(c.clone(), &specs, dynamics, p.supply_volts))
            .collect::<Result<Vec<_>, _>>()?;
        let mut by_joint = IndexMap::new();
        let mut by_number = BTreeMap::new();
        for (i, ch) in channels.iter().enumerate() {
            by_number.insert(ch.cfg.ch, i);
            for j in ch.cfg.joints.keys() {
                by_joint.insert(j.clone(), i);
            }
        }
        Ok(Actuation {
            profile_name: profile.into(),
            label: p.label,
            controller: p.controller,
            channels,
            by_joint,
            by_number,
            frequency_hz,
            us_per_unit,
            plant_enabled: true,
            frame_no: 0,
            sim_time: 0.0,
            last_frame: Frame {
                frame: 0,
                t: 0.0,
                targets: vec![],
            },
            log: Default::default(),
            pending: Default::default(),
            acc: 0.0,
            tick: 0,
            rng,
            couplings: Vec::new(),
        })
    }

    pub fn channel_for(&self, joint: &str) -> Option<&Channel> {
        self.by_joint.get(joint).map(|&i| &self.channels[i])
    }
    pub fn channel(&self, number: usize) -> Option<&Channel> {
        self.by_number.get(&number).map(|&i| &self.channels[i])
    }

    /// Behaviour/manual input: desired joint value. Coupled joints follow their primary; a joint
    /// several channels drive at once (a visor with a servo each side) sets every one of them.
    /// The coupled limits clamp it (the safety layer), and a joint that depends on this one is
    /// pulled back inside its range if this move narrowed it.
    pub fn command(&mut self, joint: &str, value: f64) {
        let mut value = value;
        for c in &self.couplings {
            if c.joint == joint {
                if let Some(a) = self.target_of(&c.depends_on) {
                    value = c.clamp(a, value);
                }
            }
        }
        self.set_joint_target(joint, value);
        let deps: Vec<(String, f64)> = self
            .couplings
            .iter()
            .filter(|c| c.depends_on == joint)
            .filter_map(|c| {
                let b = self.target_of(&c.joint)?;
                let v = c.clamp(value, b);
                (v != b).then(|| (c.joint.clone(), v))
            })
            .collect();
        for (j, v) in deps {
            self.set_joint_target(&j, v);
        }
    }

    /// The commanded value of a joint (its primary channel's follower target).
    pub fn target_of(&self, joint: &str) -> Option<f64> {
        self.channels.iter().find(|c| c.primary_joint == joint).map(|c| c.follower.target)
    }

    fn set_joint_target(&mut self, joint: &str, value: f64) {
        for ch in self.channels.iter_mut() {
            if ch.primary_joint == joint && ch.direct_us.is_none() {
                ch.follower.set_target(value);
            }
        }
    }

    /// Direct target in quarter-microseconds (Maestro script units; 0 = off).
    pub fn set_target(&mut self, channel: usize, quarter_us: f64) -> bool {
        match self.by_number.get(&channel) {
            Some(&i) => {
                self.channels[i].direct_us = Some(quarter_us / 4.0);
                true
            }
            None => false,
        }
    }

    pub fn set_maestro_limits(&mut self, channel: usize, speed: Option<f64>, accel: Option<f64>) {
        if let Some(&i) = self.by_number.get(&channel) {
            let c = &mut self.channels[i].cfg;
            if speed.is_some() {
                c.maestro_speed = speed;
            }
            if accel.is_some() {
                c.maestro_accel = accel;
            }
        }
    }

    pub fn release_all(&mut self) {
        self.channels.iter_mut().for_each(Channel::release_direct);
    }

    pub fn update(&mut self, dt: f64) {
        self.acc += dt.min(0.1);
        let step = 1.0 / PLANT_HZ;
        let frame_every = ((PLANT_HZ / self.frequency_hz) + 0.5).floor().max(1.0) as u64;
        while self.acc >= step {
            self.acc -= step;
            self.sim_time += step;
            self.tick += 1;
            if self.tick.is_multiple_of(TRAJ_EVERY) {
                for ch in &mut self.channels {
                    ch.follower.step(TRAJ_EVERY as f64 * step);
                }
            }
            for ch in &mut self.channels {
                ch.us = ch.pulse_now(step);
            }
            if self.tick.is_multiple_of(frame_every) {
                self.emit_frame();
            }
            while self.pending.front().is_some_and(|f| f.at <= self.sim_time) {
                let f = self.pending.pop_front().expect("front");
                for ch in &mut self.channels {
                    let t = f.targets.get(ch.cfg.ch).copied().unwrap_or(0);
                    if t != 0 {
                        ch.apply_pulse(t as f64 * self.us_per_unit);
                    }
                }
            }
            for ch in &mut self.channels {
                if self.plant_enabled {
                    ch.plant_step(step);
                } else {
                    ch.snap();
                }
            }
        }
    }

    fn emit_frame(&mut self) {
        let mut targets = vec![0i64; self.controller.channels];
        for ch in &mut self.channels {
            ch.target = (ch.us / self.us_per_unit + 0.5).floor() as i64;
            if let Some(t) = targets.get_mut(ch.cfg.ch) {
                *t = ch.target;
            }
        }
        self.frame_no += 1;
        self.last_frame = Frame {
            frame: self.frame_no,
            t: (self.sim_time * 1000.0).round() / 1000.0,
            targets: targets.clone(),
        };
        self.log.push_back(self.last_frame.clone());
        if self.log.len() > 3000 {
            self.log.pop_front(); // last 60 s
        }
        // Servo PWM free-runs: a new target reaches the servo 0-20 ms later.
        let latency = if self.plant_enabled {
            self.rng.next_f64() / self.frequency_hz
        } else {
            0.0
        };
        self.pending.push_back(Pending {
            at: self.sim_time + latency,
            targets,
        });
    }

    /// Joint values to pose the rig with (coupled joints scaled by their factor).
    pub fn joint_values(&self) -> IndexMap<String, f64> {
        let mut out = IndexMap::new();
        for ch in &self.channels {
            for (j, k) in &ch.cfg.joints {
                out.insert(j.clone(), ch.value() * k);
            }
        }
        out
    }

    /// The frame log as JSONL: what the hardware sink would have sent, for replay.
    pub fn export_log(&self) -> String {
        let header = serde_json::json!({
            "profile": self.profile_name,
            "controller": self.controller.kind,
            "hz": (self.frequency_hz * 1000.0).round() / 1000.0,
            "usPerUnit": (self.us_per_unit * 10000.0).round() / 10000.0,
            "channels": self.channels.iter().map(|c| (c.cfg.ch.to_string(), serde_json::Value::from(c.cfg.name.clone()))).collect::<serde_json::Map<_, _>>(),
        });
        std::iter::once(header.to_string())
            .chain(
                self.log
                    .iter()
                    .map(|f| serde_json::to_string(f).unwrap_or_default()),
            )
            .collect::<Vec<_>>()
            .join("\n")
    }
}

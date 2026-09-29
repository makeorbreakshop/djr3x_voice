//! One OS thread per driver (serial I/O blocks; the performer never waits on USB), and the
//! set the runtime feeds once per performer tick.

use std::collections::BTreeMap;
use std::sync::mpsc::{self, RecvTimeoutError};
use std::sync::Arc;
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use r3x_bus::Bus;
use r3x_contracts::profile::{DriverKind, LightDriver};
use r3x_contracts::{Event, OpsEvent, RobotProfile, Source};
use r3x_performer_core::performer::Out;
use r3x_performer_core::Frames;

use crate::chest::{ChestConfig, ChestDriver};
use crate::face::{FaceConfig, FaceDriver};
use crate::link::{Opener, SerialOpener};
use crate::servo::maestro::{MaestroConfig, MaestroDriver};
use crate::servo::r3x::{R3xServoConfig, R3xServoDriver};
use crate::servo::StubDriver;
use crate::virtual_driver::VirtualDriver;
use crate::{Driver, Health};

enum Msg {
    Out(Out),
    Frames(Arc<Frames>),
    Enable(bool),
    Stop,
}

pub struct DriverHandle {
    pub name: String,
    pub outputs: Vec<String>,
    tx: mpsc::Sender<Msg>,
    join: Option<JoinHandle<()>>,
}

impl DriverHandle {
    pub fn out(&self, out: Out) {
        let _ = self.tx.send(Msg::Out(out));
    }
    pub fn frames(&self, f: Arc<Frames>) {
        let _ = self.tx.send(Msg::Frames(f));
    }
    pub fn set_enabled(&self, on: bool) {
        let _ = self.tx.send(Msg::Enable(on));
    }
}

impl Drop for DriverHandle {
    fn drop(&mut self) {
        let _ = self.tx.send(Msg::Stop);
        if let Some(j) = self.join.take() {
            let _ = j.join();
        }
    }
}

fn report(bus: Option<&Bus>, name: &str, h: &Health) {
    if let Some(bus) = bus {
        bus.publish(
            Source::System,
            None,
            Event::Ops(OpsEvent::ServiceStatus { service: name.into(), status: h.status, detail: h.detail.clone() }),
        );
    }
}

/// Run `driver` on its own thread: `start` (connect), then messages and `poll` timers.
/// Health goes to the bus as `ops.service_status` whenever it changes.
pub fn spawn(mut driver: Box<dyn Driver>, bus: Option<Bus>) -> DriverHandle {
    let (tx, rx) = mpsc::channel();
    let name = driver.name().to_string();
    let outputs = driver.outputs();
    let thread_name = name.clone();
    let join = std::thread::Builder::new()
        .name(name.clone())
        .spawn(move || {
            let t0 = Instant::now();
            let now = || t0.elapsed().as_secs_f64();
            driver.start(now());
            let mut health = driver.health();
            report(bus.as_ref(), &thread_name, &health);
            loop {
                let t = now();
                let wait = (driver.next_poll(t) - t).clamp(0.0, 0.25);
                match rx.recv_timeout(Duration::from_secs_f64(wait)) {
                    Ok(Msg::Out(o)) => driver.on_out(&o, now()),
                    Ok(Msg::Frames(f)) => driver.on_frames(&f, now()),
                    Ok(Msg::Enable(on)) => driver.set_enabled(on, now()),
                    Ok(Msg::Stop) | Err(RecvTimeoutError::Disconnected) => break,
                    Err(RecvTimeoutError::Timeout) => {}
                }
                driver.poll(now());
                let h = driver.health();
                if h != health {
                    report(bus.as_ref(), &thread_name, &h);
                    health = h;
                }
            }
            driver.stop(now());
        })
        .expect("spawn driver thread");
    DriverHandle { name, outputs, tx, join: Some(join) }
}

/// Every driver the profile asks for. Feed it each performer tick.
#[derive(Default)]
pub struct DriverSet {
    pub drivers: Vec<DriverHandle>,
}

impl DriverSet {
    /// Build from the Robot Profile and the environment (ports, force-mock flags).
    /// `servo_us_per_unit` = the performer actuation's `us_per_unit` (dumb sinks' frame units).
    pub fn from_profile(profile: &RobotProfile, bus: &Bus, servo_us_per_unit: f64) -> Self {
        Self::with_opener(profile, bus, servo_us_per_unit, Arc::new(SerialOpener))
    }

    pub fn with_opener(profile: &RobotProfile, bus: &Bus, us_per_unit: f64, opener: Arc<dyn Opener>) -> Self {
        let board = |b: &str| {
            profile.lights.iter().any(|g| matches!(&g.driver, LightDriver::SerialLed { board, .. } if board == b))
        };
        let kinds: Vec<DriverKind> = profile.actuators.iter().map(|a| a.driver).collect();
        let mut drivers: Vec<Box<dyn Driver>> = vec![Box::new(VirtualDriver::to_bus(bus.clone()))];
        let face_cfg = FaceConfig::from_env(profile);
        let mut chest_cfg = ChestConfig::from_env();
        if board("face") {
            chest_cfg.exclude.extend(face_cfg.port.clone());
            drivers.push(Box::new(FaceDriver::new(face_cfg, opener.clone())));
        }
        if board("chest") {
            // Probing opens (and so resets) boards; the face probes on its own thread, so the
            // chest must not grab its port: without explicit ports, set both env vars.
            drivers.push(Box::new(ChestDriver::new(chest_cfg, opener.clone())));
        }
        if kinds.contains(&DriverKind::R3xServo) {
            drivers.push(Box::new(R3xServoDriver::new(R3xServoConfig::from_profile(profile), opener.clone())));
        }
        if kinds.contains(&DriverKind::Maestro) {
            drivers.push(Box::new(MaestroDriver::new(MaestroConfig::from_profile(profile, us_per_unit), opener.clone())));
        }
        if kinds.contains(&DriverKind::Pca9685) {
            drivers.push(Box::new(crate::servo::pca9685::Pca9685Driver::new(None, vec![], us_per_unit)));
        }
        if kinds.contains(&DriverKind::FeetechSts) {
            drivers.push(Box::new(StubDriver("driver.feetech_sts")));
        }
        if kinds.contains(&DriverKind::Dynamixel) {
            drivers.push(Box::new(StubDriver("driver.dynamixel")));
        }
        DriverSet { drivers: drivers.into_iter().map(|d| spawn(d, Some(bus.clone()))).collect() }
    }

    /// One performer tick: its queued outputs (`Performer::take_events`) and its frame.
    pub fn tick(&self, outs: &[Out], frames: Frames) {
        let f = Arc::new(frames);
        for d in &self.drivers {
            for o in outs.iter().filter(|o| routes_to(o, &d.name)) {
                d.out(o.clone());
            }
            d.frames(f.clone());
        }
    }

    /// Apply `state.stage.outputs`: a driver is off when any output it serves is off.
    pub fn apply_outputs(&self, outputs: &BTreeMap<String, bool>) {
        for d in &self.drivers {
            d.set_enabled(!d.outputs.iter().any(|o| outputs.get(o) == Some(&false)));
        }
    }
}

fn routes_to(o: &Out, driver: &str) -> bool {
    match o {
        Out::FaceLine { .. } => driver == "driver.face",
        Out::ChestLine { .. } | Out::Freeze { .. } => driver == "driver.chest",
        Out::ServoGoal { .. } => driver == "driver.servo",
        _ => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use r3x_bus::Received;
    use r3x_contracts::{Body, Domain, ServiceStatus};
    use r3x_performer_core::show::catalog::Catalog;

    #[tokio::test]
    async fn thread_reports_health_and_publishes_frames() {
        let bus = Bus::default();
        let mut ops = bus.subscribe(Domain::Ops);
        let mut frames = bus.subscribe_frames();
        let profile = RobotProfile::load(concat!(env!("CARGO_MANIFEST_DIR"), "/../../../profiles/r3x/robot.json")).unwrap();
        let mut p = r3x_performer_core::Performer::new(Arc::new(Catalog::default()), &profile, Default::default()).unwrap();
        let h = spawn(Box::new(VirtualDriver::to_bus(bus.clone())), Some(bus.clone()));
        h.frames(Arc::new(p.tick(0.0)));
        drop(h); // joins the thread
        assert!(frames.try_recv().expect("a frame").lights.contains_key("chest"));
        let Some(Received::Message(env)) = ops.recv().await else { panic!("no health") };
        assert!(matches!(
            &env.body,
            Body::Event(Event::Ops(OpsEvent::ServiceStatus { status: ServiceStatus::Running, service, .. })) if service == "driver.virtual"
        ));
    }
}

//! The rig choice on the real bus: `stage.set_rig` swaps the performer's profile (stop runs,
//! Home, reload), so the same jog clamps to the Physical rig's neck range (the mech model's: the
//! central column's pan, +-90 in the droid) and back to robot.json's when switched back.

use std::collections::BTreeMap;
use std::sync::Arc;
use std::time::Duration;

use r3x_bus::Bus;
use r3x_contracts::{Ack, Command, Frames, OperatingMode, PerfCommand, Rig, RobotProfile, Source, StageCommand, StageState};
use r3x_runtime::performer::{self, DriverOptions, PerformerHostConfig};
use tokio::sync::broadcast;

async fn pan_after(rx: &mut broadcast::Receiver<Arc<Frames>>, d: Duration) -> f64 {
    tokio::time::sleep(d).await;
    *rx = rx.resubscribe();
    loop {
        match tokio::time::timeout(Duration::from_secs(2), rx.recv()).await.expect("frames stopped") {
            Ok(f) => return f.joints["head_pan"],
            Err(broadcast::error::RecvError::Lagged(_)) => continue,
            Err(e) => panic!("{e}"),
        }
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn rig_switch_reloads_the_performer() {
    // Frames come through the virtual driver; the servo driver must never reach a real port here.
    std::env::set_var("FORCE_MOCK_SERVO", "1");
    let bus = Bus::default();
    let profile = Arc::new(RobotProfile::load(r3x_runtime::default_profile_path()).unwrap());
    let physical = RobotProfile::load(r3x_runtime::rig_profile_path(Rig::Physical)).unwrap();
    let pan_max = |p: &RobotProfile| p.joint("head_pan").unwrap().animation.max;
    assert!((pan_max(&physical) - pan_max(&profile)).abs() > 10.0, "the two rigs differ at the neck");
    let mut sc = r3x_stage::StageConfig::from_profile(&profile);
    sc.physical_profile = Some(r3x_runtime::rig_profile_path(Rig::Physical));
    r3x_stage::spawn(&bus, sc, None).unwrap();
    let mut frames = bus.subscribe_frames();
    let cfg = PerformerHostConfig { profile: profile.clone(), show_dir: performer::default_show_dir(), drivers: Some(DriverOptions { leds: false }), catalog: None, pad: None };
    performer::spawn(&bus, cfg).unwrap();
    let cmd = |c| bus.command(Source::Ui, None, c);
    assert_eq!(cmd(Command::Stage(StageCommand::SetMode { mode: OperatingMode::Bench })).await, Ack::Accepted);
    let jog = || Command::Perf(PerfCommand::Puppet { channels: BTreeMap::from([("head_pan".to_string(), 120.0)]) });

    assert_eq!(cmd(jog()).await, Ack::Accepted);
    let orig = pan_after(&mut frames, Duration::from_millis(2500)).await;
    // 120 deg is past both rigs' pan ranges: each clamps the jog at its own
    assert!((orig - pan_max(&profile)).abs() < 0.5, "Original clamps at robot.json's range ({orig})");

    assert_eq!(cmd(Command::Stage(StageCommand::SetRig { rig: Rig::Physical })).await, Ack::Accepted);
    assert_eq!(bus.get::<StageState>().rig, Rig::Physical);
    let homed = pan_after(&mut frames, Duration::from_millis(2500)).await;
    assert!(homed.abs() < 1.0, "the switch homes and reloads ({homed})");
    assert_eq!(cmd(jog()).await, Ack::Accepted);
    let phys = pan_after(&mut frames, Duration::from_millis(2500)).await;
    // the column's pan is 1:1 on a goBILDA 2000: the plant's deadband (3 us = 0.45 deg) and backlash
    // (0.6 deg) are not divided down by a gear as the old 4:1 neck gear did, so it settles within ~0.53
    assert!((phys - pan_max(&physical)).abs() < 1.0, "Physical clamps at its pan range ({phys})");

    assert_eq!(cmd(Command::Stage(StageCommand::SetRig { rig: Rig::Original })).await, Ack::Accepted);
    tokio::time::sleep(Duration::from_millis(1500)).await;
    assert_eq!(cmd(jog()).await, Ack::Accepted);
    let back = pan_after(&mut frames, Duration::from_millis(2500)).await;
    assert!((back - pan_max(&profile)).abs() < 0.5, "back on Original ({back})");
}

//! Phase 3 acceptance on the real bus (plan §9): the native performer plays shows with tier
//! checks, reports runs, publishes 50 Hz frames through the virtual driver, shows a live eye
//! command, and its alive layers stop in Bench.

use std::sync::Arc;
use std::time::Duration;

use r3x_bus::{Bus, Received};
use r3x_contracts::{
    Ack, Body, Command, Domain, Event, Frames, OperatingMode, PerfCommand, PerfEvent, PerfState, RobotProfile,
    Source, StageCommand,
};
use r3x_runtime::performer::{self, DriverOptions, PerformerHostConfig};
use tokio::sync::broadcast;

fn play(id: &str) -> Command {
    Command::Perf(PerfCommand::Play { id: id.into(), intensity: 1.0, speed: 1.0, layer: None })
}

async fn frame(rx: &mut broadcast::Receiver<Arc<Frames>>) -> Arc<Frames> {
    loop {
        match tokio::time::timeout(Duration::from_secs(2), rx.recv()).await.expect("frames stopped") {
            Ok(f) => return f,
            Err(broadcast::error::RecvError::Lagged(_)) => continue,
            Err(e) => panic!("{e}"),
        }
    }
}

/// The newest frame after `d`.
async fn frame_after(rx: &mut broadcast::Receiver<Arc<Frames>>, d: Duration) -> Arc<Frames> {
    tokio::time::sleep(d).await;
    *rx = rx.resubscribe();
    frame(rx).await
}

#[tokio::test(flavor = "multi_thread")]
async fn performer_on_the_bus() {
    let bus = Bus::default();
    let profile = Arc::new(RobotProfile::load(r3x_runtime::default_profile_path()).unwrap());
    r3x_stage::spawn(&bus, r3x_stage::StageConfig::from_profile(&profile), None).unwrap();
    let mut frames = bus.subscribe_frames();
    let mut perf = bus.subscribe(Domain::Perf);
    let cfg = PerformerHostConfig {
        profile,
        show_dir: performer::default_show_dir(),
        drivers: Some(DriverOptions { leds: false }),
        catalog: None,
        pad: None,
    };
    performer::spawn(&bus, cfg).unwrap();

    // Frames flow at ~50 Hz with every light group.
    let a = frame(&mut frames).await;
    let b = frame(&mut frames).await;
    assert!(b.t_mono > a.t_mono && b.t_mono - a.t_mono < 0.1);
    assert!(["eyes", "mouth", "chest", "stage"].iter().all(|g| a.lights.contains_key(*g)));

    // Tiers: the router (jev) may not start a show-tier sequence; the panel may.
    let ack = bus.command(Source::Jev, None, play("crowd_hype")).await;
    assert!(matches!(&ack, Ack::Rejected { reason } if reason.contains("show-tier")), "{ack:?}");
    assert_eq!(bus.command(Source::Ui, None, play("nod")).await, Ack::Accepted);
    let started = loop {
        let Some(Received::Message(env)) = perf.recv().await else { panic!() };
        if let Body::Event(Event::Perf(PerfEvent::Started { id, source, .. })) = &env.body {
            break (id.clone(), *source);
        }
    };
    assert_eq!(started, ("nod".to_string(), Source::Ui));
    assert!(bus.get::<PerfState>().runs.iter().any(|r| r.id == "nod"));

    // A live eye command is just frames.
    let before = frame_after(&mut frames, Duration::from_millis(100)).await.lights["eyes"].clone();
    let eyes = Command::Perf(PerfCommand::Eyes { pattern: "thinking".into(), duration: None });
    assert_eq!(bus.command(Source::Bridge, None, eyes).await, Ack::Accepted);
    let after = frame_after(&mut frames, Duration::from_millis(300)).await.lights["eyes"].clone();
    assert_ne!(before, after, "the thinking pattern reached the eye pixels");
    assert_eq!(bus.get::<r3x_contracts::LightsState>().eye_pattern.as_deref(), Some("thinking"), "retained for `eye status`");

    // Bench: alive layers and idle off, so a body with nothing playing holds still.
    let stop = Command::Perf(PerfCommand::Stop(r3x_contracts::StopTarget::All));
    assert_eq!(bus.command(Source::Ui, None, stop).await, Ack::Accepted);
    let bench = Command::Stage(StageCommand::SetMode { mode: OperatingMode::Bench });
    assert_eq!(bus.command(Source::Ui, None, bench).await, Ack::Accepted);
    tokio::time::sleep(Duration::from_millis(1500)).await; // blends and followers settle
    let x = frame_after(&mut frames, Duration::ZERO).await;
    let y = frame_after(&mut frames, Duration::from_millis(500)).await;
    let moved = x.joints.iter().map(|(j, v)| (v - y.joints[j]).abs()).fold(0.0, f64::max);
    assert!(moved < 1e-6, "still in Bench (moved {moved})");

    let show = Command::Stage(StageCommand::SetMode { mode: OperatingMode::Show });
    assert_eq!(bus.command(Source::Ui, None, show).await, Ack::Accepted);
    let x = frame_after(&mut frames, Duration::from_millis(300)).await;
    let y = frame_after(&mut frames, Duration::from_millis(700)).await;
    let moved = x.joints.iter().map(|(j, v)| (v - y.joints[j]).abs()).fold(0.0, f64::max);
    assert!(moved > 1e-3, "alive again in Show (moved {moved})");
}

async fn until(what: &str, mut ok: impl FnMut() -> bool) {
    for _ in 0..200 {
        if ok() {
            return;
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    panic!("timed out waiting for {what}");
}

/// Bench Home: runs stopped, puppet released, layers and autonomy off, every joint home.
/// Show gaze: the owning panel's viewport target steers head_pan; another panel's does not.
#[tokio::test(flavor = "multi_thread")]
async fn home_resets_the_bench_and_gaze_follows_the_owning_panel() {
    use r3x_contracts::{GazeSource, StageState, StopTarget};
    let bus = Bus::default();
    let profile = Arc::new(RobotProfile::load(r3x_runtime::default_profile_path()).unwrap());
    r3x_stage::spawn(&bus, r3x_stage::StageConfig::from_profile(&profile), None).unwrap();
    let mut frames = bus.subscribe_frames();
    let cfg = PerformerHostConfig { profile, show_dir: performer::default_show_dir(), drivers: Some(DriverOptions { leds: false }), catalog: None, pad: None };
    performer::spawn(&bus, cfg).unwrap();
    let cmd = |c| bus.command(Source::Ui, None, c);
    let stage = |c| Command::Stage(c);

    // Show refuses Home.
    let home = Command::Perf(PerfCommand::Home { joints: vec![] });
    assert!(matches!(cmd(home.clone()).await, Ack::Rejected { reason } if reason.contains("Bench")));

    // Show gaze: panel-a owns the viewport; its target moves the head, panel-b's is refused.
    assert!(cmd(stage(StageCommand::SetLayer { layer: "saccades".into(), enabled: false })).await.is_accepted());
    assert!(cmd(stage(StageCommand::SetAutonomy { enabled: false })).await.is_accepted());
    let claim = stage(StageCommand::SetGaze { source: GazeSource::Viewport, owner: Some("panel-a".into()) });
    assert!(cmd(claim).await.is_accepted());
    let look = |pan: f64, owner: &str| Command::Perf(PerfCommand::Look { pan, tilt: 0.0, owner: Some(owner.into()) });
    assert!(!cmd(look(-40.0, "panel-b")).await.is_accepted(), "not the owner");
    // Engaged attends to the target (idle glances centre on it); keep it fresh.
    assert!(cmd(stage(StageCommand::SetEngagement { engagement: r3x_contracts::Engagement::Ambient })).await.is_accepted());
    assert!(cmd(stage(StageCommand::SetLayer { layer: "saccades".into(), enabled: true })).await.is_accepted());
    for _ in 0..40 {
        assert!(cmd(look(30.0, "panel-a")).await.is_accepted());
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    let pan = frame(&mut frames).await.joints["head_pan"];
    assert!(pan > 5.0, "head follows the viewport to the left (pan {pan})");
    // Off: no target; glances around straight ahead.
    assert!(cmd(stage(StageCommand::SetGaze { source: GazeSource::Off, owner: None })).await.is_accepted());
    assert!(!cmd(look(30.0, "panel-a")).await.is_accepted(), "the viewport is not the source");

    // Bench, made busy: layers + autonomy on, a show, a jogged joint.
    assert!(cmd(stage(StageCommand::SetMode { mode: OperatingMode::Bench })).await.is_accepted());
    for l in ["breathing", "saccades", "gaze_wander", "speech_bob"] {
        assert!(cmd(stage(StageCommand::SetLayer { layer: l.into(), enabled: true })).await.is_accepted());
    }
    assert!(cmd(stage(StageCommand::SetAutonomy { enabled: true })).await.is_accepted());
    assert_eq!(cmd(play("nod")).await, Ack::Accepted);
    let jog = Command::Perf(PerfCommand::Puppet { channels: [("head_pan".to_string(), 35.0)].into() });
    assert_eq!(cmd(jog).await, Ack::Accepted);
    tokio::time::sleep(Duration::from_millis(600)).await;
    assert!(!bus.get::<PerfState>().at_home);

    assert_eq!(cmd(home).await, Ack::Accepted);
    let s = bus.get::<StageState>();
    assert!(!s.autonomy && s.layers.values().all(|on| !on), "{s:?}");
    let ps = bus.get::<PerfState>();
    assert!(ps.runs.is_empty() && ps.homing, "{ps:?}");
    until("at home", || bus.get::<PerfState>().at_home).await;
    let f = frame_after(&mut frames, Duration::from_millis(500)).await; // the servo settles too
    assert!(f.joints["head_pan"].abs() < 1.0, "{}", f.joints["head_pan"]);

    // A run ends the hold.
    let _ = cmd(Command::Perf(PerfCommand::Stop(StopTarget::All))).await;
    assert_eq!(cmd(play("nod")).await, Ack::Accepted);
    until("hold released", || !bus.get::<PerfState>().homing).await;
}

/// The gamepad feed (`R3X_PAD`): the right stick turns the head, Start toggles the stage's
/// freeze (the one switch, not just the performer's copy), and a lost pad lets go.
#[tokio::test(flavor = "multi_thread")]
async fn pad_drives_the_puppeteer_and_freeze() {
    use r3x_contracts::StageState;
    use r3x_performer_core::show::puppeteer::PadState;
    let bus = Bus::default();
    let profile = Arc::new(RobotProfile::load(r3x_runtime::default_profile_path()).unwrap());
    r3x_stage::spawn(&bus, r3x_stage::StageConfig::from_profile(&profile), None).unwrap();
    let mut frames = bus.subscribe_frames();
    let (tx, rx) = tokio::sync::watch::channel(None);
    let cfg = PerformerHostConfig {
        profile,
        show_dir: performer::default_show_dir(),
        drivers: Some(DriverOptions { leds: false }),
        catalog: None,
        pad: Some(rx),
    };
    performer::spawn(&bus, cfg).unwrap();
    let pad = |right_x: f64, start: bool| {
        let mut buttons = vec![(false, 0.0); 17];
        buttons[9] = (start, f64::from(u8::from(start)));
        let state = PadState { axes: vec![0.0, 0.0, right_x, 0.0], buttons };
        Some(performer::PadInput { puppet: state.clone(), raw: state, controls: Default::default() })
    };

    let rest = frame_after(&mut frames, Duration::from_millis(300)).await.joints["head_pan"];
    tx.send_replace(pad(1.0, false));
    let turned = frame_after(&mut frames, Duration::from_millis(1500)).await.joints["head_pan"];
    assert!(turned - rest > 30.0, "right stick turns the head: {rest} -> {turned}");

    // Pulled out mid-turn: the head comes back instead of holding the last stick value.
    tx.send_replace(None);
    let released = frame_after(&mut frames, Duration::from_millis(1500)).await.joints["head_pan"];
    assert!((released - rest).abs() < 10.0, "released: {rest} -> {released}");

    // Start: press, release, press = frozen, then thawed, in the retained stage state.
    tx.send_replace(pad(0.0, true));
    until("frozen by Start", || bus.get::<StageState>().frozen).await;
    until("perf state agrees", || bus.get::<PerfState>().frozen).await;
    tx.send_replace(pad(0.0, false));
    tokio::time::sleep(Duration::from_millis(100)).await;
    assert!(bus.get::<StageState>().frozen, "releasing Start changes nothing");
    tx.send_replace(pad(0.0, true));
    until("thawed by Start", || !bus.get::<StageState>().frozen).await;
}

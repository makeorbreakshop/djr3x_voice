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

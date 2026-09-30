//! The runtime's pad operator layer on the real bus, without hardware: raw snapshots in, the
//! puppeteer feed and bus commands out.

use std::sync::Arc;
use std::time::Duration;

use r3x_bus::Bus;
use r3x_contracts::{Command, RobotProfile, Source, StageCommand, StageState};
use r3x_pad::ds3::std_btn as b;
use r3x_performer_core::show::puppeteer::PadState;

fn pad(held: &[usize]) -> Option<PadState> {
    let mut buttons = vec![(false, 0.0); b::COUNT];
    for &i in held {
        buttons[i] = (true, 1.0);
    }
    Some(PadState { axes: vec![0.0, 0.0, 0.3, 0.0], buttons })
}

async fn settle() {
    tokio::time::sleep(Duration::from_millis(80)).await;
}

/// A panel in Build claims the pad: the operator layer stands down (no puppet feed, no button
/// actions) until the claim is returned, then drives again.
#[tokio::test(flavor = "multi_thread")]
async fn a_claimed_pad_stands_down_until_returned() {
    let bus = Bus::default();
    let path = r3x_runtime::default_profile_path();
    let profile = Arc::new(RobotProfile::load(&path).unwrap());
    r3x_stage::spawn(&bus, r3x_stage::StageConfig::from_profile(&profile), None).unwrap();
    let map = r3x_runtime::pad::load_mapping(&path).unwrap();
    let (raw, raw_rx) = tokio::sync::watch::channel(None);
    let feed = r3x_runtime::pad::spawn_operator(&bus, profile, map, (vec![], vec![]), r3x_pad::FeedbackHandle::detached(), raw_rx);
    let claim = |owner: Option<&str>| Command::Stage(StageCommand::ClaimPad { owner: owner.map(Into::into) });

    raw.send_replace(pad(&[b::R2]));
    settle().await;
    let f = feed.borrow().clone().expect("driving");
    assert!(f.puppet.buttons[b::R2].0);

    assert!(bus.command(Source::Ui, None, claim(Some("panel-a"))).await.is_accepted());
    raw.send_replace(pad(&[b::START]));
    settle().await;
    assert!(feed.borrow().is_none(), "claimed: no puppet feed (the sticks release)");
    assert!(!bus.get::<StageState>().frozen, "claimed: buttons do nothing");

    assert!(bus.command(Source::Ui, None, claim(None)).await.is_accepted());
    raw.send_replace(pad(&[]));
    settle().await;
    raw.send_replace(pad(&[b::START]));
    settle().await;
    assert!(feed.borrow().is_some(), "returned: driving again");
    assert!(bus.get::<StageState>().frozen, "returned: Start freezes again");
}

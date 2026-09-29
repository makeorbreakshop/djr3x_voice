//! Global left-click push-to-talk (MouseInputService's replacement). macOS needs the
//! Accessibility permission for the app hosting the process (Terminal.app; not Warp).
//!
//! A click toggles recording, only while `state.engagement` is INTERACTIVE, and only when no
//! other client holds push-to-talk (`state.conversation.ptt_owner`). A click on the panel's
//! own talk button is the panel's, not ours: [`Voice::click`] stands down when the panel's
//! start/stop arrives with it. Off by default in `./r3x` (`--click-anywhere`).

use std::time::Duration;

use r3x_bus::Bus;
use r3x_contracts::{Engagement, EngagementState};

use crate::voice::Voice;

pub const MOUSE_OWNER: &str = "mouse";
/// How long a click waits to see whether it was on another client's talk button.
pub const SETTLE: Duration = Duration::from_millis(250);

pub fn spawn(bus: Bus, voice: Voice) -> anyhow::Result<()> {
    let rt = tokio::runtime::Handle::current();
    std::thread::Builder::new().name("r3x-mouse".into()).spawn(move || {
        let res = rdev::listen(move |ev| {
            if !matches!(ev.event_type, rdev::EventType::ButtonPress(rdev::Button::Left)) {
                return;
            }
            if bus.get::<EngagementState>().engagement != Engagement::Interactive {
                return;
            }
            let (v, pressed) = (voice.clone(), std::time::Instant::now());
            rt.spawn(async move {
                if let Some(ack) = v.click(MOUSE_OWNER, pressed, SETTLE).await.filter(|a| !a.is_accepted()) {
                    tracing::info!(?ack, "click ignored");
                }
            });
        });
        if let Err(e) = res {
            tracing::warn!(?e, "global mouse listener failed (Accessibility permission?)");
        }
    })?;
    Ok(())
}

//! Global left-click push-to-talk (MouseInputService's replacement). macOS needs the
//! Accessibility permission for the app hosting the process (Terminal.app; not Warp).
//!
//! A click toggles recording, only while `state.engagement` is INTERACTIVE, and only when no
//! other client holds push-to-talk (`state.conversation.ptt_owner`).

use r3x_bus::Bus;
use r3x_contracts::{Engagement, EngagementState};

use crate::voice::Voice;

pub const MOUSE_OWNER: &str = "mouse";

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
            let v = voice.clone();
            rt.spawn(async move {
                let ack = v.toggle(MOUSE_OWNER).await;
                if !ack.is_accepted() {
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

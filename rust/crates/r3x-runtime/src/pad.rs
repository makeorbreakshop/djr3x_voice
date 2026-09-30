//! `R3X_PAD` (on by default in the binary, `--no-pad` / `R3X_PAD=0` off): a DualShock 3 on
//! USB drives the puppeteer (`r3x_pad`, mapping in `show::puppeteer`). The latest state goes
//! to the performer through a watch channel; a lost pad clears it, which releases the sticks.
//! Reports `pad`: running while a pad streams, degraded while none is attached.

use r3x_bus::Bus;
use r3x_contracts::ServiceStatus;
use r3x_pad::{PadEvent, PadReader};

use crate::performer::PadFeed;

pub fn enabled_from_env() -> bool {
    std::env::var("R3X_PAD").is_ok_and(|v| !matches!(v.as_str(), "" | "0" | "false" | "no" | "off"))
}

/// Start the reader. Fail-open: no HID access is a warning, not an error.
pub fn start(bus: &Bus) -> Option<(PadReader, PadFeed)> {
    let (tx, rx) = tokio::sync::watch::channel(None);
    let bus2 = bus.clone();
    let mut streaming = false;
    let reader = r3x_pad::spawn(move |ev| match ev {
        PadEvent::State(r) => {
            if !streaming {
                streaming = true;
                r3x_ops::report(&bus2, "pad", ServiceStatus::Running, Some(format!("DualShock 3, battery {:?}", r.battery)));
            }
            tx.send_replace(Some(r.pad_state()));
        }
        PadEvent::Connected { name } => {
            r3x_ops::report(&bus2, "pad", ServiceStatus::Degraded, Some(format!("{name} attached; press PS")));
        }
        PadEvent::Lost { reason } => {
            streaming = false;
            tx.send_replace(None);
            r3x_ops::report(&bus2, "pad", ServiceStatus::Degraded, Some(format!("no pad ({reason})")));
        }
    });
    match reader {
        Ok(r) => {
            r3x_ops::report(bus, "pad", ServiceStatus::Degraded, Some("no pad; plug in a DualShock 3".into()));
            Some((r, rx))
        }
        Err(e) => {
            tracing::warn!("pad off: {e}");
            None
        }
    }
}

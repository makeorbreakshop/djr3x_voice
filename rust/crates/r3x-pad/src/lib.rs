//! Gamepad input for the puppeteer (`show::puppeteer`): a DualShock 3 read over HID on its
//! own thread, decoded ([`ds3`]) and handed to the host as the standard-layout
//! [`PadState`](r3x_performer_core::show::puppeteer::PadState) the browser's Gamepad API
//! would give, so the sim and the real robot share one mapping.
//!
//! Transport: USB on macOS (Sequoia refuses the DS3 over Bluetooth: it demands PIN
//! authentication for every incoming HID link and the pad cannot do it), USB or Bluetooth on
//! Linux, where `hid-sony` pairs it natively. hidapi reads both the same way.
//!
//! Safety: the pad streams ~100 reports/s whenever it is live, so silence is a lost link.
//! [`PadEvent::Lost`] fires after [`SILENCE`], and the host must release the sticks then,
//! never hold the last value.

pub mod ds3;

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use hidapi::{HidApi, HidDevice};

pub use ds3::{Battery, Ds3Report};

/// No report for this long = the pad is gone (unplugged, powered off, out of range).
pub const SILENCE: Duration = Duration::from_millis(500);
/// How often to look for a pad while none is attached.
const SCAN_EVERY: Duration = Duration::from_secs(1);
/// A pad that opened but has not streamed yet (a DS3 waits for its PS button).
const WAKE_HINT_AFTER: Duration = Duration::from_secs(2);

#[derive(Clone, Debug, PartialEq)]
pub enum PadEvent {
    Connected { name: String },
    State(Ds3Report),
    Lost { reason: String },
}

/// The reader thread; dropping it stops the thread.
pub struct PadReader {
    stop: Arc<AtomicBool>,
    thread: Option<JoinHandle<()>>,
}

impl Drop for PadReader {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(t) = self.thread.take() {
            let _ = t.join();
        }
    }
}

/// Watch for a DualShock 3 and stream its state to `on_event` (on the reader thread).
pub fn spawn(on_event: impl FnMut(PadEvent) + Send + 'static) -> std::io::Result<PadReader> {
    let stop = Arc::new(AtomicBool::new(false));
    let flag = stop.clone();
    let thread = std::thread::Builder::new().name("r3x-pad".into()).spawn(move || run(&flag, on_event))?;
    Ok(PadReader { stop, thread: Some(thread) })
}

fn run(stop: &AtomicBool, mut on_event: impl FnMut(PadEvent)) {
    let mut api = match HidApi::new() {
        Ok(a) => a,
        Err(e) => {
            tracing::warn!("pad: HID unavailable ({e}); no gamepad input");
            return;
        }
    };
    let mut announced_absent = false;
    while !stop.load(Ordering::Relaxed) {
        match open(&mut api) {
            Some((dev, name)) => {
                announced_absent = false;
                tracing::info!(%name, "pad: DualShock 3 attached");
                on_event(PadEvent::Connected { name: name.clone() });
                let reason = stream(stop, &dev, &mut on_event);
                tracing::info!(%name, %reason, "pad: lost");
                on_event(PadEvent::Lost { reason });
            }
            None => {
                if !announced_absent {
                    tracing::info!("pad: no DualShock 3 on USB yet (plug it in and press PS)");
                    announced_absent = true;
                }
                std::thread::sleep(SCAN_EVERY);
            }
        }
    }
}

fn open(api: &mut HidApi) -> Option<(HidDevice, String)> {
    let _ = api.refresh_devices();
    let info = api
        .device_list()
        .find(|d| d.vendor_id() == ds3::VENDOR && d.product_id() == ds3::PRODUCT)?
        .clone();
    let dev = info.open_device(api).map_err(|e| tracing::warn!("pad: open failed: {e}")).ok()?;
    let name = info.product_string().unwrap_or("DualShock 3").to_owned();
    wake(&dev);
    Some((dev, name))
}

/// Ask the pad to stream (USB: read feature 0xF2; Bluetooth: send 0xF4) and light LED 1 so
/// the operator can see it is live. Each is harmless on the other transport, so do both.
fn wake(dev: &HidDevice) {
    let mut f2 = [0u8; 18];
    f2[0] = ds3::USB_ENABLE_FEATURE;
    let _ = dev.get_feature_report(&mut f2);
    let _ = dev.send_feature_report(&ds3::BT_ENABLE);
    let _ = dev.write(&ds3::output_report(0b0001, false, 0));
}

/// Read until the link drops; returns why.
fn stream(stop: &AtomicBool, dev: &HidDevice, on_event: &mut impl FnMut(PadEvent)) -> String {
    let opened = Instant::now();
    let mut last: Option<Instant> = None;
    let mut hinted = false;
    let mut buf = [0u8; 64];
    loop {
        if stop.load(Ordering::Relaxed) {
            return "stopped".into();
        }
        match dev.read_timeout(&mut buf, 100) {
            Ok(n) if n > 0 => {
                if let Some(r) = ds3::parse(&buf[..n]) {
                    last = Some(Instant::now());
                    on_event(PadEvent::State(r));
                }
            }
            Ok(_) => {}
            Err(e) => return format!("read error: {e}"),
        }
        match last {
            Some(t) if t.elapsed() > SILENCE => return format!("no reports for {} ms", SILENCE.as_millis()),
            None if !hinted && opened.elapsed() > WAKE_HINT_AFTER => {
                tracing::info!("pad: attached but silent; press the PS button");
                hinted = true;
                wake(dev);
            }
            _ => {}
        }
    }
}

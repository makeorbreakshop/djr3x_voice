//! The DualShock 3 wire format (USB and Bluetooth input report 0x01, output report 0x01)
//! and its mapping onto the W3C standard gamepad layout the puppeteer reads.
//!
//! Offsets count the report id as byte 0, which is how hidapi hands the report over on
//! macOS and Linux alike. Layout per the Linux `hid-sony` driver and SDL's PS3 driver,
//! checked against a report captured from a genuine pad (054c:0268) on macOS 15:
//!
//! | Bytes | Content |
//! |---|---|
//! | 2 | select, L3, R3, start, up, right, down, left (bit 0..7) |
//! | 3 | L2, R2, L1, R1, triangle, circle, cross, square (bit 0..7) |
//! | 4 | PS (bit 0) |
//! | 6..9 | left x, left y, right x, right y (0..255, 128 = centre, down = +) |
//! | 14..25 | pressure: up, right, down, left, L2, R2, L1, R1, triangle, circle, cross, square |
//! | 29, 30 | plugged state, battery (0..5 level, 0xEE charging, 0xEF full) |
//! | 41..46 | accelerometer x, y, z, big-endian 10-bit, ~512 at rest, ~113 counts per g |

use r3x_performer_core::show::puppeteer::PadState;

pub const VENDOR: u16 = 0x054C;
pub const PRODUCT: u16 = 0x0268;
pub const REPORT_LEN: usize = 49;

/// Standard-mapping button indices (the puppeteer's).
pub mod std_btn {
    pub const CROSS: usize = 0;
    pub const CIRCLE: usize = 1;
    pub const SQUARE: usize = 2;
    pub const TRIANGLE: usize = 3;
    pub const L1: usize = 4;
    pub const R1: usize = 5;
    pub const L2: usize = 6;
    pub const R2: usize = 7;
    pub const SELECT: usize = 8;
    pub const START: usize = 9;
    pub const L3: usize = 10;
    pub const R3: usize = 11;
    pub const UP: usize = 12;
    pub const DOWN: usize = 13;
    pub const LEFT: usize = 14;
    pub const RIGHT: usize = 15;
    pub const PS: usize = 16;
    pub const COUNT: usize = 17;
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Battery {
    /// 0 (empty) ..= 5 (full).
    Level(u8),
    Charging,
    Charged,
    Unknown(u8),
}

/// One decoded input report.
#[derive(Clone, Debug, PartialEq)]
pub struct Ds3Report {
    /// Standard layout: left x, left y, right x, right y in [-1, 1], down = +.
    pub axes: [f64; 4],
    /// Standard layout `(pressed, value)`; `value` is the pressure (0..1) where the pad
    /// measures one, else 0 or 1.
    pub buttons: [(bool, f64); std_btn::COUNT],
    /// Accelerometer in g, axes as the pad reports them (~1 g on z when lying flat).
    pub accel: [f64; 3],
    pub battery: Battery,
}

impl Ds3Report {
    /// The puppeteer's input.
    pub fn pad_state(&self) -> PadState {
        PadState { axes: self.axes.to_vec(), buttons: self.buttons.to_vec() }
    }
}

fn stick(v: u8) -> f64 {
    ((f64::from(v) - 128.0) / 127.0).clamp(-1.0, 1.0)
}

fn accel(hi: u8, lo: u8) -> f64 {
    (f64::from(u16::from_be_bytes([hi, lo])) - 512.0) / 113.0
}

/// Decode input report 0x01; `None` for anything else (feature replies, short reads).
pub fn parse(r: &[u8]) -> Option<Ds3Report> {
    if r.len() < REPORT_LEN || r[0] != 0x01 {
        return None;
    }
    use std_btn::*;
    let bit = |byte: usize, b: u8| r[byte] & (1 << b) != 0;
    // (standard index, digital bit, pressure byte)
    let map: [(usize, (usize, u8), Option<usize>); std_btn::COUNT] = [
        (SELECT, (2, 0), None),
        (L3, (2, 1), None),
        (R3, (2, 2), None),
        (START, (2, 3), None),
        (UP, (2, 4), Some(14)),
        (RIGHT, (2, 5), Some(15)),
        (DOWN, (2, 6), Some(16)),
        (LEFT, (2, 7), Some(17)),
        (L2, (3, 0), Some(18)),
        (R2, (3, 1), Some(19)),
        (L1, (3, 2), Some(20)),
        (R1, (3, 3), Some(21)),
        (TRIANGLE, (3, 4), Some(22)),
        (CIRCLE, (3, 5), Some(23)),
        (CROSS, (3, 6), Some(24)),
        (SQUARE, (3, 7), Some(25)),
        (PS, (4, 0), None),
    ];
    let mut buttons = [(false, 0.0); std_btn::COUNT];
    for (i, (byte, b), pressure) in map {
        let down = bit(byte, b);
        let value = match pressure {
            // A light touch can register digitally before any pressure does.
            Some(p) => (f64::from(r[p]) / 255.0).max(if down { 1.0 / 255.0 } else { 0.0 }),
            None => f64::from(u8::from(down)),
        };
        buttons[i] = (down, value);
    }
    let battery = match r[30] {
        l @ 0..=5 => Battery::Level(l),
        0xEE => Battery::Charging,
        0xEF => Battery::Charged,
        o => Battery::Unknown(o),
    };
    Some(Ds3Report {
        axes: [stick(r[6]), stick(r[7]), stick(r[8]), stick(r[9])],
        buttons,
        accel: [accel(r[41], r[42]), accel(r[43], r[44]), accel(r[45], r[46])],
        battery,
    })
}

/// Output report 0x01: player LEDs (bit 0 = LED 1 .. bit 3 = LED 4) and rumble (0 = off).
/// The report id travels as the first payload byte on both hidapi backends.
pub fn output_report(leds: u8, rumble_weak: bool, rumble_strong: u8) -> [u8; REPORT_LEN] {
    let mut o = [0u8; REPORT_LEN];
    o[0] = 0x01;
    o[2] = if rumble_weak { 0xFF } else { 0 }; // right (weak) motor: duration
    o[3] = u8::from(rumble_weak); //                right motor: on/off
    o[4] = if rumble_strong > 0 { 0xFF } else { 0 }; // left (strong) motor: duration
    o[5] = rumble_strong; //                          left motor: force
    o[10] = (leds & 0x0F) << 1;
    for led in 0..4 {
        // Always on: time_enabled, duty_length, enabled, duty_off, duty_on.
        o[11 + led * 5..16 + led * 5].copy_from_slice(&[0xFF, 0x27, 0x10, 0x00, 0x32]);
    }
    o
}

/// USB: reading feature report 0xF2 switches the pad into streaming input reports.
pub const USB_ENABLE_FEATURE: u8 = 0xF2;
/// Bluetooth: this feature report does the same.
pub const BT_ENABLE: [u8; 5] = [0xF4, 0x42, 0x03, 0x00, 0x00];

#[cfg(test)]
mod tests {
    use super::*;

    /// A genuine pad at rest on USB, charging (macOS 15, hidapi).
    const IDLE: &str = "0100000000007f7f82800000000000000000000000000000000000000002ee1200000000122a77004001fa01e9018e0002";

    fn bytes(h: &str) -> Vec<u8> {
        (0..h.len()).step_by(2).map(|i| u8::from_str_radix(&h[i..i + 2], 16).unwrap()).collect()
    }

    #[test]
    fn idle_report() {
        let r = parse(&bytes(IDLE)).unwrap();
        assert!(r.axes.iter().all(|a| a.abs() < 0.03), "{:?}", r.axes);
        assert!(r.buttons.iter().all(|b| !b.0 && b.1 == 0.0));
        assert_eq!(r.battery, Battery::Charging);
        // Lying on the desk: gravity on one axis, the others near zero.
        assert!((r.accel[2] + 1.0).abs() < 0.1, "{:?}", r.accel);
        assert!(r.accel[0].abs() < 0.1 && r.accel[1].abs() < 0.3, "{:?}", r.accel);
    }

    #[test]
    fn buttons_sticks_and_pressure() {
        let mut b = bytes(IDLE);
        b[2] = 0b0000_1001; // select + start
        b[3] = 0b0100_0001; // L2 + cross
        b[4] = 1; // PS
        b[18] = 255; // L2 fully in
        b[24] = 64; // cross, lightly
        b[6] = 255; // left stick right
        b[9] = 0; // right stick up
        let r = parse(&b).unwrap();
        use std_btn::*;
        for i in [SELECT, START, L2, CROSS, PS] {
            assert!(r.buttons[i].0, "button {i}");
        }
        assert!(!r.buttons[CIRCLE].0 && !r.buttons[R2].0);
        assert_eq!(r.buttons[L2].1, 1.0);
        assert!((r.buttons[CROSS].1 - 64.0 / 255.0).abs() < 1e-9);
        assert_eq!(r.buttons[START].1, 1.0);
        assert_eq!(r.axes[0], 1.0);
        assert_eq!(r.axes[3], -1.0);
        let s = r.pad_state();
        assert_eq!((s.axes.len(), s.buttons.len()), (4, 17));
    }

    #[test]
    fn rejects_other_reports() {
        assert!(parse(&[0xF2; 49]).is_none());
        assert!(parse(&bytes(IDLE)[..20]).is_none());
    }

    #[test]
    fn led_report() {
        let o = output_report(0b0001, false, 0);
        assert_eq!((o[0], o[10]), (0x01, 0x02));
        assert_eq!(&o[11..16], &[0xFF, 0x27, 0x10, 0x00, 0x32]);
        assert_eq!(output_report(0b1111, true, 200)[2..6], [0xFF, 1, 0xFF, 200]);
    }
}

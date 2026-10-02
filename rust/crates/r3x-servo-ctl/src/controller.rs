//! The controller state machine. Pure: time comes in as `now_us` (monotonic microseconds),
//! bytes in through [`Controller::receive`], frames out through a callback, pulses out
//! through [`Controller::pulses`]. The firmware wires it to USB CDC, PWM and the INA219;
//! the host tests wire it to a fake clock and an in-memory serial pair.

use crate::wire::{flag, nak, t, Builder, Deframer, Reader};
use r3x_motion::calib::pulse_us;
use r3x_motion::{Calibration, JerkLimitedFollower, MotionLimits};

/// 0..=17 the kit build's channels; 18 the second visor servo (Hunter's head: one each side).
pub const CHANNELS: usize = 19;
pub const CONTROL_HZ: u32 = 200;
pub const DT: f64 = 1.0 / CONTROL_HZ as f64;
/// Heartbeat silent this long -> ramp to hold (PROTOCOL.md, D6).
pub const HEARTBEAT_TIMEOUT_US: u64 = 250_000;
/// 25 Hz (the protocol allows 20-50).
pub const TELEMETRY_PERIOD_US: u64 = 40_000;
/// Staggered power-up: channel `i` starts pulsing `(i / GROUP_SIZE) * STAGGER_US` after the
/// first enabling heartbeat (motion-control.md §2 "Power-on").
pub const STAGGER_US: u64 = 200_000;
pub const GROUP_SIZE: usize = 4;
pub const PROTOCOL_VERSION: u8 = 1;
pub const FIRMWARE: &str = concat!("r3x-servo ", env!("CARGO_PKG_VERSION"));
/// Largest frame the controller sends (telemetry: 3 + 6 + 18 * 7 + 2 bytes, COBS, 0x00).
pub const MAX_TX_FRAME: usize = 160;

/// Rail stall detection (one INA219 on the servo rail, plan D6: detects *a* stall, not
/// which channel). Thresholds are placeholders until measured on the real rail.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct StallConfig {
    /// Above this for `trip_us` -> OVERCURRENT, every channel ramps to hold.
    pub trip_ma: u16,
    pub trip_us: u64,
    /// Below this for `clear_us` -> cleared.
    pub clear_ma: u16,
    pub clear_us: u64,
}

impl Default for StallConfig {
    fn default() -> Self {
        StallConfig { trip_ma: 6000, trip_us: 300_000, clear_ma: 4000, clear_us: 1_000_000 }
    }
}

#[derive(Clone, Debug)]
struct Chan {
    cal: Calibration,
    /// Configured limits (a goal may lower them, never raise them).
    limits: MotionLimits,
    f: JerkLimitedFollower,
    configured: bool,
    powered: bool,
    holding: bool,
    parked: bool,
    soft_hit: bool,
    us: u16,
}

impl Default for Chan {
    fn default() -> Self {
        let limits = MotionLimits { v_max: 1.0, a_max: 1.0, j_max: 1.0 };
        Chan {
            cal: Calibration {
                center_us: 1500.0,
                center_value: 0.0,
                trim_us: 0.0,
                invert: false,
                gear: 1.0,
                mm_per_deg: None,
                pulse_min_us: 1000.0,
                pulse_max_us: 2000.0,
                range_deg: 90.0,
            },
            limits,
            f: JerkLimitedFollower::new(limits, 0.0, 0.0, 0.0),
            configured: false,
            powered: false,
            holding: true,
            parked: true,
            soft_hit: false,
            us: 0,
        }
    }
}

impl Chan {
    fn target(&mut self, v: f64) {
        if v < self.f.soft_min || v > self.f.soft_max {
            self.soft_hit = true;
        }
        self.f.set_target(v);
    }
}

pub struct Controller {
    rx: Deframer,
    ch: [Chan; CHANNELS],
    enabled: bool,
    /// Per-channel output enables from the heartbeat (bit n = channel n).
    mask: u32,
    last_hb_us: Option<u64>,
    /// First enabling heartbeat: the power-up schedule starts here.
    powered_at_us: Option<u64>,
    last_seq: u16,
    tx_seq: u16,
    pub stall: StallConfig,
    rail_ma: u16,
    overcurrent: bool,
    over_since: Option<u64>,
    under_since: Option<u64>,
    next_telemetry_us: u64,
    pub naks: u32,
}

impl Default for Controller {
    fn default() -> Self {
        Self::new()
    }
}

fn finite(vs: &[f32]) -> bool {
    vs.iter().all(|v| v.is_finite())
}

fn cap(requested: f32, configured: f64) -> f64 {
    if requested > 0.0 {
        (requested as f64).min(configured)
    } else {
        configured
    }
}

impl Controller {
    pub fn new() -> Self {
        Controller {
            rx: Deframer::default(),
            ch: core::array::from_fn(|_| Chan::default()),
            enabled: false,
            mask: u32::MAX,
            last_hb_us: None,
            powered_at_us: None,
            last_seq: 0,
            tx_seq: 0,
            stall: StallConfig::default(),
            rail_ma: 0,
            overcurrent: false,
            over_since: None,
            under_since: None,
            next_telemetry_us: 0,
            naks: 0,
        }
    }

    // ---- inputs ----------------------------------------------------------------------

    /// Bytes from the host. Replies (HELLO_REPLY, NAK) go to `out`, one frame per call.
    pub fn receive(&mut self, bytes: &[u8], now_us: u64, out: &mut impl FnMut(&[u8])) {
        for &b in bytes {
            let mut pkt: Option<(u8, u16, [u8; 64], usize)> = None;
            self.rx.push(b, |p| {
                let mut buf = [0u8; 64];
                let n = p.payload.len().min(64);
                buf[..n].copy_from_slice(&p.payload[..n]);
                // Longer payloads than any host message are a length error.
                pkt = Some((p.ty, p.seq, buf, if p.payload.len() > 64 { usize::MAX } else { n }));
            });
            if let Some((ty, seq, buf, n)) = pkt {
                let res = if n == usize::MAX { Err(nak::BAD_LENGTH) } else { self.handle(ty, &buf[..n], now_us, out) };
                match res {
                    Ok(()) => self.last_seq = seq,
                    Err(code) => {
                        self.naks = self.naks.wrapping_add(1);
                        let mut f = [0u8; 16];
                        let mut bld = Builder::<16>::new(t::NAK, self.next_tx_seq());
                        bld.u16(seq).u8(code);
                        if let Some(n) = bld.finish(&mut f) {
                            out(&f[..n]);
                        }
                    }
                }
            }
        }
    }

    fn handle(&mut self, ty: u8, p: &[u8], now_us: u64, out: &mut impl FnMut(&[u8])) -> Result<(), u8> {
        let mut r = Reader(p);
        let bad_len = nak::BAD_LENGTH;
        match ty {
            t::HELLO => {
                let mut f = [0u8; 64];
                let mut b = Builder::<48>::new(t::HELLO_REPLY, self.next_tx_seq());
                b.u8(PROTOCOL_VERSION).u8(CHANNELS as u8).bytes(FIRMWARE.as_bytes());
                if let Some(n) = b.finish(&mut f) {
                    out(&f[..n]);
                }
                Ok(())
            }
            t::CONFIG => {
                let ch = r.u8().ok_or(bad_len)?;
                let mut f = [0f32; 3];
                for v in &mut f {
                    *v = r.f32().ok_or(bad_len)?;
                }
                let [center_us, center_value, trim_us] = f;
                let invert = r.u8().ok_or(bad_len)? != 0;
                let (gear, mm) = (r.f32().ok_or(bad_len)?, r.f32().ok_or(bad_len)?);
                let (pmin, pmax) = (r.u16().ok_or(bad_len)?, r.u16().ok_or(bad_len)?);
                let mut g = [0f32; 6];
                for v in &mut g {
                    *v = r.f32().ok_or(bad_len)?;
                }
                if !r.is_empty() {
                    return Err(bad_len);
                }
                let [range_deg, soft_min, soft_max, v_max, a_max, j_max] = g;
                let c = self.ch.get_mut(ch as usize).ok_or(nak::BAD_CHANNEL)?;
                let ok = finite(&[center_us, center_value, trim_us, gear, mm, range_deg, soft_min, soft_max, v_max, a_max, j_max])
                    && pmin < pmax
                    && range_deg > 0.0
                    && soft_min < soft_max
                    && v_max > 0.0
                    && a_max > 0.0
                    && j_max > 0.0
                    && (if mm != 0.0 { true } else { gear != 0.0 });
                if !ok {
                    return Err(nak::BAD_VALUE);
                }
                c.cal = Calibration {
                    center_us: center_us as f64,
                    center_value: center_value as f64,
                    trim_us: trim_us as f64,
                    invert,
                    gear: gear as f64,
                    mm_per_deg: (mm != 0.0).then_some(mm as f64),
                    pulse_min_us: pmin as f64,
                    pulse_max_us: pmax as f64,
                    range_deg: range_deg as f64,
                };
                c.limits = MotionLimits { v_max: v_max as f64, a_max: a_max as f64, j_max: j_max as f64 };
                // First config: start at the park pose. Reconfig: stay where we are, at rest.
                let x0 = if c.configured { c.f.x } else { center_value as f64 };
                c.f = JerkLimitedFollower::new(c.limits, soft_min as f64, soft_max as f64, x0);
                c.configured = true;
                Ok(())
            }
            t::GOAL => {
                let ch = r.u8().ok_or(bad_len)?;
                let v = [r.f32().ok_or(bad_len)?, r.f32().ok_or(bad_len)?, r.f32().ok_or(bad_len)?, r.f32().ok_or(bad_len)?];
                if !r.is_empty() {
                    return Err(bad_len);
                }
                let allowed = self.motion_allowed(ch as usize, now_us);
                let c = self.ch.get_mut(ch as usize).ok_or(nak::BAD_CHANNEL)?;
                if !c.configured {
                    return Err(nak::UNCONFIGURED);
                }
                if !finite(&v) || v[1..].iter().any(|l| *l < 0.0) {
                    return Err(nak::BAD_VALUE);
                }
                if allowed {
                    c.f.limits = MotionLimits {
                        v_max: cap(v[1], c.limits.v_max),
                        a_max: cap(v[2], c.limits.a_max),
                        j_max: cap(v[3], c.limits.j_max),
                    };
                    c.parked = false;
                    c.target(v[0] as f64);
                }
                Ok(())
            }
            t::HEARTBEAT => {
                let flags = r.u8().ok_or(bad_len)?;
                let mask = if r.is_empty() { u32::MAX } else { r.u32().ok_or(bad_len)? };
                if !r.is_empty() {
                    return Err(bad_len);
                }
                self.enabled = flags & 1 != 0;
                self.mask = mask;
                self.last_hb_us = Some(now_us);
                Ok(())
            }
            t::DIRECT => {
                let (ch, us) = (r.u8().ok_or(bad_len)?, r.u16().ok_or(bad_len)?);
                if !r.is_empty() {
                    return Err(bad_len);
                }
                let allowed = self.motion_allowed(ch as usize, now_us);
                let c = self.ch.get_mut(ch as usize).ok_or(nak::BAD_CHANNEL)?;
                if !c.configured {
                    return Err(nak::UNCONFIGURED);
                }
                if allowed {
                    // Through the follower (never a jump), clamped to pulse range and soft limits.
                    let us = (us as f64).clamp(c.cal.pulse_min_us, c.cal.pulse_max_us);
                    c.f.limits = c.limits;
                    c.parked = false;
                    let v = c.cal.us_to_value(us);
                    c.target(v);
                }
                Ok(())
            }
            t::PARK => {
                if !r.is_empty() {
                    return Err(bad_len);
                }
                for i in 0..CHANNELS {
                    if self.motion_allowed(i, now_us) {
                        let c = &mut self.ch[i];
                        c.f.limits = c.limits;
                        c.f.set_target(c.cal.center_value);
                        c.parked = true;
                    }
                }
                Ok(())
            }
            _ => Err(nak::UNKNOWN_TYPE),
        }
    }

    /// Rail current from the INA219 (mA), with the time it was read.
    pub fn set_rail_ma(&mut self, ma: u16, now_us: u64) {
        self.rail_ma = ma;
        let s = self.stall;
        if ma >= s.trip_ma {
            self.under_since = None;
            let since = *self.over_since.get_or_insert(now_us);
            if now_us - since >= s.trip_us {
                self.overcurrent = true;
            }
        } else if ma <= s.clear_ma {
            self.over_since = None;
            if self.overcurrent {
                let since = *self.under_since.get_or_insert(now_us);
                if now_us - since >= s.clear_us {
                    self.overcurrent = false;
                    self.under_since = None;
                }
            }
        } else {
            self.over_since = None;
            self.under_since = None;
        }
    }

    // ---- control loop ----------------------------------------------------------------

    fn heartbeat_ok(&self, now_us: u64) -> bool {
        self.enabled && self.last_hb_us.is_some_and(|t| now_us.saturating_sub(t) <= HEARTBEAT_TIMEOUT_US)
    }

    /// Everything holds: heartbeat lost, outputs disabled, or a rail stall.
    pub fn holding(&self, now_us: u64) -> bool {
        !self.heartbeat_ok(now_us) || self.overcurrent
    }

    fn motion_allowed(&self, i: usize, now_us: u64) -> bool {
        i < CHANNELS && !self.holding(now_us) && self.mask & (1 << i) != 0 && self.ch[i].powered
    }

    /// One control period (call at [`CONTROL_HZ`] with `dt` = [`DT`]).
    pub fn tick(&mut self, now_us: u64, dt: f64) {
        let hold_all = self.holding(now_us);
        if !hold_all && self.powered_at_us.is_none() {
            self.powered_at_us = Some(now_us);
        }
        for i in 0..CHANNELS {
            if let Some(t0) = self.powered_at_us {
                if !self.ch[i].powered && now_us >= t0 + (i / GROUP_SIZE) as u64 * STAGGER_US {
                    self.ch[i].powered = true;
                }
            }
            let hold = hold_all || self.mask & (1 << i) == 0 || !self.ch[i].powered;
            let c = &mut self.ch[i];
            if !c.configured {
                c.us = 0;
                continue;
            }
            if hold && !c.holding {
                c.f.hold();
            }
            c.holding = hold;
            c.f.step(dt);
            c.us = if c.powered { pulse_us(c.cal.value_to_us(c.f.x)) } else { 0 };
        }
    }

    /// Servo power (a MOSFET on the rail): on from the first enabling heartbeat.
    pub fn rail_enabled(&self) -> bool {
        self.powered_at_us.is_some()
    }

    /// Commanded pulse per channel, whole microseconds (0 = off).
    pub fn pulses(&self) -> [u16; CHANNELS] {
        core::array::from_fn(|i| self.ch[i].us)
    }

    /// Follower position (joint units) and velocity.
    pub fn state(&self, ch: usize) -> (f64, f64) {
        (self.ch[ch].f.x, self.ch[ch].f.v)
    }

    pub fn soft_range(&self, ch: usize) -> (f64, f64) {
        (self.ch[ch].f.soft_min, self.ch[ch].f.soft_max)
    }

    pub fn last_seq(&self) -> u16 {
        self.last_seq
    }

    fn next_tx_seq(&mut self) -> u16 {
        self.tx_seq = self.tx_seq.wrapping_add(1);
        self.tx_seq
    }

    pub fn status_flags(&self, now_us: u64) -> u8 {
        let configured = self.ch.iter().filter(|c| c.configured);
        let mut f = 0;
        if self.holding(now_us) {
            f |= flag::HOLDING;
        }
        if self.overcurrent {
            f |= flag::OVERCURRENT;
        }
        if !self.ch.iter().any(|c| c.configured) {
            f |= flag::UNCONFIGURED;
        } else if configured.clone().all(|c| c.parked) {
            f |= flag::PARKED;
        }
        if self.ch.iter().any(|c| c.soft_hit) {
            f |= flag::SOFT_LIMIT;
        }
        f
    }

    /// Telemetry when due (every [`TELEMETRY_PERIOD_US`]).
    pub fn poll_telemetry(&mut self, now_us: u64, out: &mut impl FnMut(&[u8])) {
        if now_us < self.next_telemetry_us {
            return;
        }
        self.next_telemetry_us = now_us + TELEMETRY_PERIOD_US;
        let status = self.status_flags(now_us);
        let seq = self.next_tx_seq();
        let mut b = Builder::<{ MAX_TX_FRAME - 8 }>::new(t::TELEMETRY, seq);
        b.u8(status).u16(self.rail_ma).u16(self.last_seq).u8(CHANNELS as u8);
        for c in &mut self.ch {
            let mut f = 0;
            if !c.configured {
                f |= flag::UNCONFIGURED;
            } else {
                if c.holding {
                    f |= flag::HOLDING;
                }
                if c.parked {
                    f |= flag::PARKED;
                }
                if c.soft_hit {
                    f |= flag::SOFT_LIMIT;
                }
            }
            if self.overcurrent {
                f |= flag::OVERCURRENT;
            }
            c.soft_hit = false;
            b.u16(c.us).f32(c.f.x as f32).u8(f);
        }
        let mut frame = [0u8; MAX_TX_FRAME];
        if let Some(n) = b.finish(&mut frame) {
            out(&frame[..n]);
        }
    }
}

//! r3x_servo wire protocol v1 (see `PROTOCOL.md`): COBS-framed, 0x00-delimited packets of
//! `type u8 | seq u16 | payload | crc16`, little-endian, CRC-16/CCITT-FALSE over
//! type..payload.

pub const PROTOCOL_VERSION: u8 = 1;

pub mod t {
    pub const HELLO: u8 = 0x01;
    pub const CONFIG: u8 = 0x02;
    pub const GOAL: u8 = 0x03;
    pub const HEARTBEAT: u8 = 0x04;
    pub const DIRECT: u8 = 0x05;
    pub const PARK: u8 = 0x06;
    pub const HELLO_REPLY: u8 = 0x81;
    pub const TELEMETRY: u8 = 0x82;
    pub const NAK: u8 = 0x83;
}

/// Controller status flags (telemetry `flags`).
pub mod flag {
    pub const HOLDING: u8 = 1 << 0; // heartbeat lost / outputs disabled: ramped to hold
    pub const OVERCURRENT: u8 = 1 << 1;
    pub const UNCONFIGURED: u8 = 1 << 2;
    pub const PARKED: u8 = 1 << 3;
    pub const SOFT_LIMIT: u8 = 1 << 4; // a goal was clamped since the last telemetry
}

/// One channel's calibration and limits (profile actuator + its primary joint).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct ChannelConfig {
    pub ch: u8,
    pub center_us: f32,
    pub center_value: f32,
    pub trim_us: f32,
    pub invert: bool,
    pub gear: f32,
    /// 0 = revolute.
    pub mm_per_deg: f32,
    pub pulse_min_us: u16,
    pub pulse_max_us: u16,
    pub range_deg: f32,
    pub soft_min: f32,
    pub soft_max: f32,
    pub v_max: f32,
    pub a_max: f32,
    pub j_max: f32,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Msg {
    Hello,
    Config(ChannelConfig),
    /// Target in joint units; limits 0 = the channel's configured default.
    Goal { ch: u8, target: f32, v_max: f32, a_max: f32, j_max: f32 },
    /// `mask`: per-channel output enables, bit n = channel n (`u32::MAX` = all). Optional
    /// on the wire (a 1-byte heartbeat means all).
    Heartbeat { outputs_enabled: bool, mask: u32 },
    Direct { ch: u8, us: u16 },
    Park,
    HelloReply { version: u8, channels: u8, firmware: String },
    Telemetry(Telemetry),
    Nak { seq: u16, code: u8 },
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct Telemetry {
    pub flags: u8,
    pub rail_ma: u16,
    /// Last host seq the controller applied.
    pub last_seq: u16,
    pub channels: Vec<ChannelTelemetry>,
}

#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct ChannelTelemetry {
    /// Commanded pulse (0 = off).
    pub us: u16,
    /// Follower position, joint units.
    pub x: f32,
    pub flags: u8,
}

fn f32s(out: &mut Vec<u8>, vs: &[f32]) {
    for v in vs {
        out.extend_from_slice(&v.to_le_bytes());
    }
}

impl Msg {
    fn body(&self) -> (u8, Vec<u8>) {
        let mut p = Vec::new();
        let ty = match self {
            Msg::Hello => t::HELLO,
            Msg::Config(c) => {
                p.push(c.ch);
                f32s(&mut p, &[c.center_us, c.center_value, c.trim_us]);
                p.push(c.invert as u8);
                f32s(&mut p, &[c.gear, c.mm_per_deg]);
                p.extend_from_slice(&c.pulse_min_us.to_le_bytes());
                p.extend_from_slice(&c.pulse_max_us.to_le_bytes());
                f32s(&mut p, &[c.range_deg, c.soft_min, c.soft_max, c.v_max, c.a_max, c.j_max]);
                t::CONFIG
            }
            Msg::Goal { ch, target, v_max, a_max, j_max } => {
                p.push(*ch);
                f32s(&mut p, &[*target, *v_max, *a_max, *j_max]);
                t::GOAL
            }
            Msg::Heartbeat { outputs_enabled, mask } => {
                p.push(*outputs_enabled as u8);
                p.extend_from_slice(&mask.to_le_bytes());
                t::HEARTBEAT
            }
            Msg::Direct { ch, us } => {
                p.push(*ch);
                p.extend_from_slice(&us.to_le_bytes());
                t::DIRECT
            }
            Msg::Park => t::PARK,
            Msg::HelloReply { version, channels, firmware } => {
                p.extend_from_slice(&[*version, *channels]);
                p.extend_from_slice(firmware.as_bytes());
                t::HELLO_REPLY
            }
            Msg::Telemetry(tm) => {
                p.push(tm.flags);
                p.extend_from_slice(&tm.rail_ma.to_le_bytes());
                p.extend_from_slice(&tm.last_seq.to_le_bytes());
                p.push(tm.channels.len() as u8);
                for c in &tm.channels {
                    p.extend_from_slice(&c.us.to_le_bytes());
                    p.extend_from_slice(&c.x.to_le_bytes());
                    p.push(c.flags);
                }
                t::TELEMETRY
            }
            Msg::Nak { seq, code } => {
                p.extend_from_slice(&seq.to_le_bytes());
                p.push(*code);
                t::NAK
            }
        };
        (ty, p)
    }

    /// One wire frame, delimiter included.
    pub fn encode(&self, seq: u16) -> Vec<u8> {
        let (ty, payload) = self.body();
        let mut raw = vec![ty];
        raw.extend_from_slice(&seq.to_le_bytes());
        raw.extend_from_slice(&payload);
        raw.extend_from_slice(&crc16(&raw).to_le_bytes());
        let mut out = cobs_encode(&raw);
        out.push(0);
        out
    }

    /// Decode one frame (without its 0x00 delimiter). Returns (seq, msg).
    pub fn decode(frame: &[u8]) -> Option<(u16, Msg)> {
        let raw = cobs_decode(frame)?;
        if raw.len() < 5 {
            return None;
        }
        let (body, crc) = raw.split_at(raw.len() - 2);
        if crc16(body) != u16::from_le_bytes([crc[0], crc[1]]) {
            return None;
        }
        let seq = u16::from_le_bytes([body[1], body[2]]);
        let mut r = Reader(&body[3..]);
        let msg = match body[0] {
            t::HELLO => Msg::Hello,
            t::CONFIG => Msg::Config(ChannelConfig {
                ch: r.u8()?,
                center_us: r.f32()?,
                center_value: r.f32()?,
                trim_us: r.f32()?,
                invert: r.u8()? != 0,
                gear: r.f32()?,
                mm_per_deg: r.f32()?,
                pulse_min_us: r.u16()?,
                pulse_max_us: r.u16()?,
                range_deg: r.f32()?,
                soft_min: r.f32()?,
                soft_max: r.f32()?,
                v_max: r.f32()?,
                a_max: r.f32()?,
                j_max: r.f32()?,
            }),
            t::GOAL => Msg::Goal { ch: r.u8()?, target: r.f32()?, v_max: r.f32()?, a_max: r.f32()?, j_max: r.f32()? },
            t::HEARTBEAT => Msg::Heartbeat { outputs_enabled: r.u8()? != 0, mask: r.u32().unwrap_or(u32::MAX) },
            t::DIRECT => Msg::Direct { ch: r.u8()?, us: r.u16()? },
            t::PARK => Msg::Park,
            t::HELLO_REPLY => Msg::HelloReply {
                version: r.u8()?,
                channels: r.u8()?,
                firmware: String::from_utf8_lossy(r.0).into_owned(),
            },
            t::TELEMETRY => {
                let (flags, rail_ma, last_seq, n) = (r.u8()?, r.u16()?, r.u16()?, r.u8()?);
                let channels = (0..n)
                    .map(|_| Some(ChannelTelemetry { us: r.u16()?, x: r.f32()?, flags: r.u8()? }))
                    .collect::<Option<Vec<_>>>()?;
                Msg::Telemetry(Telemetry { flags, rail_ma, last_seq, channels })
            }
            t::NAK => Msg::Nak { seq: r.u16()?, code: r.u8()? },
            _ => return None,
        };
        Some((seq, msg))
    }
}

struct Reader<'a>(&'a [u8]);

impl Reader<'_> {
    fn take<const N: usize>(&mut self) -> Option<[u8; N]> {
        let (h, rest) = self.0.split_at_checked(N)?;
        self.0 = rest;
        h.try_into().ok()
    }
    fn u8(&mut self) -> Option<u8> {
        Some(self.take::<1>()?[0])
    }
    fn u16(&mut self) -> Option<u16> {
        Some(u16::from_le_bytes(self.take()?))
    }
    fn u32(&mut self) -> Option<u32> {
        Some(u32::from_le_bytes(self.take()?))
    }
    fn f32(&mut self) -> Option<f32> {
        Some(f32::from_le_bytes(self.take()?))
    }
}

/// CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF, no reflection, no xorout.
pub fn crc16(data: &[u8]) -> u16 {
    let mut crc = 0xFFFFu16;
    for &b in data {
        crc ^= (b as u16) << 8;
        for _ in 0..8 {
            crc = if crc & 0x8000 != 0 { (crc << 1) ^ 0x1021 } else { crc << 1 };
        }
    }
    crc
}

pub fn cobs_encode(data: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(data.len() + data.len() / 254 + 2);
    let mut code_at = 0;
    out.push(0);
    let mut code = 1u8;
    for &b in data {
        if b == 0 {
            out[code_at] = code;
            code_at = out.len();
            out.push(0);
            code = 1;
        } else {
            out.push(b);
            code += 1;
            if code == 0xFF {
                out[code_at] = code;
                code_at = out.len();
                out.push(0);
                code = 1;
            }
        }
    }
    out[code_at] = code;
    out
}

pub fn cobs_decode(data: &[u8]) -> Option<Vec<u8>> {
    let mut out = Vec::with_capacity(data.len());
    let mut i = 0;
    while i < data.len() {
        let code = data[i] as usize;
        if code == 0 || i + code > data.len() {
            return None;
        }
        out.extend_from_slice(&data[i + 1..i + code]);
        i += code;
        if code < 0xFF && i < data.len() {
            out.push(0);
        }
    }
    Some(out)
}

/// Splits a byte stream into frames at 0x00.
#[derive(Default)]
pub struct Deframer {
    buf: Vec<u8>,
}

impl Deframer {
    /// Feed bytes; returns every complete, valid message (bad frames are dropped).
    pub fn push(&mut self, data: &[u8]) -> Vec<(u16, Msg)> {
        let mut out = Vec::new();
        for &b in data {
            if b == 0 {
                if let Some(m) = Msg::decode(&self.buf) {
                    out.push(m);
                }
                self.buf.clear();
            } else if self.buf.len() < 1024 {
                self.buf.push(b);
            }
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cobs_and_crc_known_vectors() {
        assert_eq!(crc16(b"123456789"), 0x29B1);
        assert_eq!(cobs_encode(&[0x11, 0x22, 0x00, 0x33]), [0x03, 0x11, 0x22, 0x02, 0x33]);
        assert_eq!(cobs_encode(&[0x00]), [0x01, 0x01]);
        let long: Vec<u8> = (1..=254).collect();
        let enc = cobs_encode(&long);
        assert_eq!((enc[0], enc.len()), (0xFF, 256));
        assert_eq!(cobs_decode(&enc).unwrap(), long);
    }

    #[test]
    fn every_message_round_trips_and_corruption_is_dropped() {
        let msgs = [
            Msg::Hello,
            Msg::Goal { ch: 3, target: -12.5, v_max: 0.0, a_max: 400.0, j_max: 0.0 },
            Msg::Heartbeat { outputs_enabled: true, mask: 0b101 },
            Msg::Direct { ch: 1, us: 1500 },
            Msg::HelloReply { version: 1, channels: 18, firmware: "r3x-servo 0.1".into() },
            Msg::Telemetry(Telemetry {
                flags: flag::HOLDING,
                rail_ma: 850,
                last_seq: 7,
                channels: vec![ChannelTelemetry { us: 1500, x: 2.0, flags: 0 }],
            }),
            Msg::Nak { seq: 9, code: 2 },
        ];
        let mut d = Deframer::default();
        let stream: Vec<u8> = msgs.iter().enumerate().flat_map(|(i, m)| m.encode(i as u16)).collect();
        let got: Vec<Msg> = d.push(&stream).into_iter().map(|(_, m)| m).collect();
        assert_eq!(got, msgs);
        let mut bad = Msg::Hello.encode(1);
        bad[2] ^= 0x40;
        assert!(d.push(&bad).is_empty());
    }
}

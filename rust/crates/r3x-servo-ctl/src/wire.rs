//! PROTOCOL.md v1 framing without alloc: `COBS(type u8 | seq u16 | payload | crc16) 0x00`.
//! The host codec (`r3x-drivers` `servo::proto`) is the reference; the tests in
//! `tests/sim.rs` talk to this one through it.

pub const MAX_PACKET: usize = 1024;

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

/// Status / per-channel flags.
pub mod flag {
    pub const HOLDING: u8 = 1 << 0;
    pub const OVERCURRENT: u8 = 1 << 1;
    pub const UNCONFIGURED: u8 = 1 << 2;
    pub const PARKED: u8 = 1 << 3;
    pub const SOFT_LIMIT: u8 = 1 << 4;
}

pub mod nak {
    pub const BAD_CHANNEL: u8 = 1;
    pub const UNCONFIGURED: u8 = 2;
    pub const BAD_LENGTH: u8 = 3;
    pub const UNKNOWN_TYPE: u8 = 4;
    pub const BAD_VALUE: u8 = 5;
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

/// COBS-encode `src` into `dst` and append the 0x00 delimiter. Returns the frame length, or
/// `None` if `dst` is too small (needs `src.len() + src.len() / 254 + 2`).
pub fn cobs_frame(src: &[u8], dst: &mut [u8]) -> Option<usize> {
    if dst.len() < src.len() + src.len() / 254 + 2 {
        return None;
    }
    let (mut code_at, mut o, mut code) = (0usize, 1usize, 1u8);
    for &b in src {
        if b == 0 {
            dst[code_at] = code;
            code_at = o;
            o += 1;
            code = 1;
        } else {
            dst[o] = b;
            o += 1;
            code += 1;
            if code == 0xFF {
                dst[code_at] = code;
                code_at = o;
                o += 1;
                code = 1;
            }
        }
    }
    dst[code_at] = code;
    dst[o] = 0;
    Some(o + 1)
}

/// Decode a COBS frame (no delimiter) in place; returns the decoded length.
pub fn cobs_decode_in_place(buf: &mut [u8]) -> Option<usize> {
    let (mut i, mut o) = (0usize, 0usize);
    while i < buf.len() {
        let code = buf[i] as usize;
        if code == 0 || i + code > buf.len() {
            return None;
        }
        for k in 1..code {
            buf[o] = buf[i + k];
            o += 1;
        }
        i += code;
        if code < 0xFF && i < buf.len() {
            buf[o] = 0;
            o += 1;
        }
    }
    Some(o)
}

/// A validated packet (CRC checked).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Packet<'a> {
    pub ty: u8,
    pub seq: u16,
    pub payload: &'a [u8],
}

/// Byte stream -> packets. Frames failing COBS, CRC or length checks are dropped; the
/// stream resyncs at the next 0x00.
pub struct Deframer {
    buf: [u8; MAX_PACKET + 8],
    len: usize,
    overflow: bool,
    pub dropped: u32,
}

impl Default for Deframer {
    fn default() -> Self {
        Deframer { buf: [0; MAX_PACKET + 8], len: 0, overflow: false, dropped: 0 }
    }
}

impl Deframer {
    /// Feed one byte; calls `f` for a complete valid packet.
    pub fn push(&mut self, b: u8, mut f: impl FnMut(Packet<'_>)) {
        if b != 0 {
            if self.len < self.buf.len() {
                self.buf[self.len] = b;
                self.len += 1;
            } else {
                self.overflow = true;
            }
            return;
        }
        let (len, overflow) = (self.len, self.overflow);
        self.len = 0;
        self.overflow = false;
        if len == 0 {
            return;
        }
        let ok = !overflow
            && cobs_decode_in_place(&mut self.buf[..len]).and_then(|n| {
                let raw = &self.buf[..n];
                if !(5..=MAX_PACKET).contains(&n) {
                    return None;
                }
                let (body, crc) = raw.split_at(n - 2);
                (crc16(body) == u16::from_le_bytes([crc[0], crc[1]])).then(|| {
                    f(Packet { ty: body[0], seq: u16::from_le_bytes([body[1], body[2]]), payload: &body[3..] })
                })
            }).is_some();
        if !ok {
            self.dropped = self.dropped.wrapping_add(1);
        }
    }
}

/// Little-endian payload builder over a fixed buffer.
pub struct Builder<const N: usize> {
    buf: [u8; N],
    len: usize,
}

impl<const N: usize> Builder<N> {
    /// Starts a packet: `type seq`.
    pub fn new(ty: u8, seq: u16) -> Self {
        let mut b = Builder { buf: [0; N], len: 0 };
        b.u8(ty).u16(seq);
        b
    }
    pub fn u8(&mut self, v: u8) -> &mut Self {
        self.bytes(&[v])
    }
    pub fn u16(&mut self, v: u16) -> &mut Self {
        self.bytes(&v.to_le_bytes())
    }
    pub fn f32(&mut self, v: f32) -> &mut Self {
        self.bytes(&v.to_le_bytes())
    }
    /// Silently truncates at capacity (callers size `N` for their largest packet).
    pub fn bytes(&mut self, v: &[u8]) -> &mut Self {
        let n = v.len().min(N - self.len);
        self.buf[self.len..self.len + n].copy_from_slice(&v[..n]);
        self.len += n;
        self
    }
    /// Append the CRC and COBS-frame into `out`; returns the wire length.
    pub fn finish(&mut self, out: &mut [u8]) -> Option<usize> {
        let crc = crc16(&self.buf[..self.len]);
        self.u16(crc);
        cobs_frame(&self.buf[..self.len], out)
    }
}

/// Little-endian payload reader.
pub struct Reader<'a>(pub &'a [u8]);

impl Reader<'_> {
    fn take<const K: usize>(&mut self) -> Option<[u8; K]> {
        if self.0.len() < K {
            return None;
        }
        let (h, rest) = self.0.split_at(K);
        self.0 = rest;
        h.try_into().ok()
    }
    pub fn u8(&mut self) -> Option<u8> {
        Some(self.take::<1>()?[0])
    }
    pub fn u16(&mut self) -> Option<u16> {
        Some(u16::from_le_bytes(self.take()?))
    }
    pub fn u32(&mut self) -> Option<u32> {
        Some(u32::from_le_bytes(self.take()?))
    }
    pub fn f32(&mut self) -> Option<f32> {
        Some(f32::from_le_bytes(self.take()?))
    }
    pub fn is_empty(&self) -> bool {
        self.0.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn crc_cobs_vectors_and_resync() {
        assert_eq!(crc16(b"123456789"), 0x29B1);
        let mut out = [0u8; 16];
        let n = cobs_frame(&[0x11, 0x22, 0x00, 0x33], &mut out).unwrap();
        assert_eq!(&out[..n], &[0x03, 0x11, 0x22, 0x02, 0x33, 0x00]);
        let long: [u8; 254] = core::array::from_fn(|i| i as u8 + 1);
        let mut big = [0u8; 260];
        let n = cobs_frame(&long, &mut big).unwrap();
        assert_eq!((big[0], n), (0xFF, 257));
        assert_eq!(cobs_decode_in_place(&mut big[..n - 1]), Some(254));
        assert_eq!(&big[..254], &long[..]);

        let mut frame = [0u8; 32];
        let n = Builder::<16>::new(t::HEARTBEAT, 7).u8(1).finish(&mut frame).unwrap();
        let mut d = Deframer::default();
        let mut got = None;
        // Garbage, then a corrupted copy, then the good frame: only the good one lands.
        let mut bad = frame;
        bad[2] ^= 0x40;
        for &b in [0x55, 0x66, 0x00].iter().chain(&bad[..n]).chain(&frame[..n]) {
            d.push(b, |p| got = Some((p.ty, p.seq, p.payload.len())));
        }
        assert_eq!(got, Some((t::HEARTBEAT, 7, 1)));
        assert_eq!(d.dropped, 2);
    }

}

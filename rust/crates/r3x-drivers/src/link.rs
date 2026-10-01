//! Byte links (serial ports, or an in-memory fake in tests), port probing and line reading.

use std::io;
use std::time::{Duration, Instant};

/// A bidirectional byte stream to a board.
pub trait Link: Send {
    fn write_all(&mut self, data: &[u8]) -> io::Result<()>;
    /// Read what is available, waiting up to `timeout` for the first byte. `Ok(0)` = nothing.
    fn read(&mut self, buf: &mut [u8], timeout: Duration) -> io::Result<usize>;
    fn clear_input(&mut self) -> io::Result<()>;
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct PortInfo {
    pub name: String,
    pub vid: Option<u16>,
    pub pid: Option<u16>,
    pub manufacturer: Option<String>,
    /// USB product string, where the OS reports one.
    pub product: Option<String>,
}

/// Lists and opens ports; the fake in tests, [`SerialOpener`] for real.
pub trait Opener: Send + Sync {
    fn list(&self) -> Vec<PortInfo>;
    fn open(&self, port: &str, baud: u32) -> io::Result<Box<dyn Link>>;
}

pub struct SerialOpener;

impl Opener for SerialOpener {
    fn list(&self) -> Vec<PortInfo> {
        serialport::available_ports()
            .unwrap_or_default()
            .into_iter()
            .map(|p| match p.port_type {
                serialport::SerialPortType::UsbPort(u) => PortInfo {
                    name: p.port_name,
                    vid: Some(u.vid),
                    pid: Some(u.pid),
                    manufacturer: u.manufacturer,
                    product: u.product,
                },
                _ => PortInfo { name: p.port_name, ..Default::default() },
            })
            .collect()
    }

    fn open(&self, port: &str, baud: u32) -> io::Result<Box<dyn Link>> {
        let p = serialport::new(port, baud).timeout(Duration::from_millis(10)).open()?;
        Ok(Box::new(SerialLink(p)))
    }
}

struct SerialLink(Box<dyn serialport::SerialPort>);

impl Link for SerialLink {
    fn write_all(&mut self, data: &[u8]) -> io::Result<()> {
        io::Write::write_all(&mut self.0, data)
    }
    fn read(&mut self, buf: &mut [u8], timeout: Duration) -> io::Result<usize> {
        self.0.set_timeout(timeout.max(Duration::from_millis(1)))?;
        match io::Read::read(&mut self.0, buf) {
            Err(e) if e.kind() == io::ErrorKind::TimedOut => Ok(0),
            r => r,
        }
    }
    fn clear_input(&mut self) -> io::Result<()> {
        self.0.clear(serialport::ClearBuffer::Input).map_err(io::Error::from)
    }
}

/// USB VID of boards the LED firmware runs on (Arduino, clones' CH340/FTDI/CP210x bridges).
const LED_BOARD_VIDS: [u16; 5] = [0x2341, 0x2A03, 0x1A86, 0x0403, 0x10C4];
const NAME_HINTS: [&str; 5] = ["usbmodem", "usbserial", "ttyacm", "ttyusb", "wchusbserial"];

/// USB product string of the r3x_servo motion controller, on every board it runs on.
pub const SERVO_PRODUCT: &str = "r3x-servo";
/// VID:PID the motion controller enumerates with: RP2040 build (Raspberry Pi CDC) and
/// Teensy 4.1 build (PJRC's Teensy USB serial). For OSes that report no product string.
const SERVO_BOARD_IDS: [(u16, u16); 2] = [(0x2E8A, 0x000A), (0x16C0, 0x0483)];

/// The motion controller's port: never an LED board, so never probed (a probe would write
/// LED text into it and hold the port the servo driver opens by `R3X_SERVO_PORT`).
pub fn is_servo_controller(p: &PortInfo) -> bool {
    p.product.as_deref() == Some(SERVO_PRODUCT)
        || matches!((p.vid, p.pid), (Some(v), Some(d)) if SERVO_BOARD_IDS.contains(&(v, d)))
}

/// Candidate LED-board ports, best first: known VID, then name hint, then an "arduino"
/// manufacturer. `exclude` holds ports another driver owns; a macOS `tty.` twin of an
/// excluded or listed `cu.` port is dropped too (both names are the same device). The servo
/// motion controller is never a candidate, whichever board it runs on.
pub fn probe_led_ports(ports: &[PortInfo], exclude: &[String]) -> Vec<String> {
    let twin = |n: &str| n.replace("/dev/tty.", "/dev/cu.");
    let excluded = |n: &str| exclude.iter().any(|e| e == n || twin(e) == twin(n));
    let mut scored: Vec<(u8, &str)> = ports
        .iter()
        .filter(|p| !excluded(&p.name) && !is_servo_controller(p))
        .filter(|p| !(p.name.starts_with("/dev/tty.") && ports.iter().any(|q| q.name == twin(&p.name))))
        .filter_map(|p| {
            let lname = p.name.to_lowercase();
            let score = if p.vid.is_some_and(|v| LED_BOARD_VIDS.contains(&v)) {
                0
            } else if NAME_HINTS.iter().any(|h| lname.contains(h)) {
                1
            } else if p.manufacturer.as_deref().is_some_and(|m| m.to_lowercase().contains("arduino")) {
                2
            } else {
                return None;
            };
            Some((score, p.name.as_str()))
        })
        .collect();
    scored.sort_by_key(|(s, _)| *s);
    scored.into_iter().map(|(_, n)| n.to_string()).collect()
}

/// Newline-framed reader over a [`Link`].
#[derive(Default)]
pub struct Lines {
    buf: Vec<u8>,
}

impl Lines {
    /// The next line (trimmed), or `None` once `deadline` passes.
    pub fn next(&mut self, link: &mut dyn Link, deadline: Instant) -> io::Result<Option<String>> {
        let mut tmp = [0u8; 64];
        loop {
            if let Some(i) = self.buf.iter().position(|&b| b == b'\n') {
                let line: Vec<u8> = self.buf.drain(..=i).collect();
                return Ok(Some(String::from_utf8_lossy(&line).trim().to_string()));
            }
            let now = Instant::now();
            if now >= deadline {
                return Ok(None);
            }
            let n = link.read(&mut tmp, deadline - now)?;
            self.buf.extend_from_slice(&tmp[..n]);
        }
    }
}

/// Reconnect pacing after a lost link: 1 s, doubling to 30 s; reset on success.
#[derive(Debug, Clone, Copy)]
pub struct Backoff {
    next_at: f64,
    delay: f64,
}

impl Default for Backoff {
    fn default() -> Self {
        Backoff { next_at: f64::NEG_INFINITY, delay: Backoff::MIN_S }
    }
}

impl Backoff {
    pub const MIN_S: f64 = 1.0;
    pub const MAX_S: f64 = 30.0;

    pub fn due(&self, now: f64) -> bool {
        now >= self.next_at
    }
    pub fn failed(&mut self, now: f64) {
        self.next_at = now + self.delay;
        self.delay = (self.delay * 2.0).min(Self::MAX_S);
    }
    pub fn reset(&mut self) {
        *self = Self::default();
    }
}

/// A named_v1 line link that fails open: without a board every line is dropped (counted)
/// and the driver reports degraded health, never an error into the performer.
pub struct LineLink {
    pub link: Option<Box<dyn Link>>,
    pub port: Option<String>,
    /// Why there is no link (mock mode), when there is none.
    pub mock_reason: String,
    pub dropped: u64,
}

impl LineLink {
    pub fn mock(reason: impl Into<String>) -> Self {
        LineLink { link: None, port: None, mock_reason: reason.into(), dropped: 0 }
    }

    pub fn connected(link: Box<dyn Link>, port: String) -> Self {
        LineLink { link: Some(link), port: Some(port), mock_reason: String::new(), dropped: 0 }
    }

    pub fn send(&mut self, line: &str) -> bool {
        let Some(link) = self.link.as_mut() else {
            self.dropped += 1;
            return false;
        };
        let mut bytes = Vec::with_capacity(line.len() + 1);
        bytes.extend_from_slice(line.as_bytes());
        bytes.push(b'\n');
        match link.write_all(&bytes) {
            Ok(()) => true,
            Err(e) => {
                tracing::warn!(port = ?self.port, "serial write failed, falling back to mock: {e}");
                self.mock_reason = format!("write failed: {e}");
                self.link = None;
                self.dropped += 1;
                false
            }
        }
    }

    /// Discard acks (`+`/`-`) so the OS buffer never fills; they carry nothing we need.
    pub fn drain_input(&mut self) {
        if let Some(link) = self.link.as_mut() {
            let mut tmp = [0u8; 256];
            while matches!(link.read(&mut tmp, Duration::ZERO), Ok(n) if n > 0) {}
        }
    }

    pub fn health(&self, forced_mock: bool) -> crate::Health {
        use r3x_contracts::ServiceStatus;
        match (&self.link, forced_mock) {
            (Some(_), _) => crate::Health::new(ServiceStatus::Running, self.port.clone()),
            (None, true) => crate::Health::new(ServiceStatus::Running, format!("mock: {}", self.mock_reason)),
            (None, false) => crate::Health::new(ServiceStatus::Degraded, format!("mock: {}", self.mock_reason)),
        }
    }
}

#[cfg(test)]
pub mod fake {
    //! In-memory boards: a scripted responder sees every byte the host writes.
    use super::*;
    use std::collections::{HashMap, VecDeque};
    use std::sync::{Arc, Mutex};

    type Responder = Box<dyn FnMut(&[u8]) -> Vec<u8> + Send>;

    #[derive(Default)]
    pub struct Wire {
        pub to_host: VecDeque<u8>,
        pub from_host: Vec<u8>,
        pub fail_writes: bool,
        pub opens: u32,
        responder: Option<Responder>,
    }

    #[derive(Clone, Default)]
    pub struct FakeLink(pub Arc<Mutex<Wire>>);

    impl FakeLink {
        pub fn new(boot: &str, responder: impl FnMut(&[u8]) -> Vec<u8> + Send + 'static) -> Self {
            let w = Wire { to_host: boot.bytes().collect(), responder: Some(Box::new(responder)), ..Default::default() };
            FakeLink(Arc::new(Mutex::new(w)))
        }
        pub fn written(&self) -> String {
            String::from_utf8_lossy(&self.0.lock().unwrap().from_host).into_owned()
        }
        pub fn lines(&self) -> Vec<String> {
            self.written().lines().map(str::to_string).collect()
        }
        pub fn bytes(&self) -> Vec<u8> {
            self.0.lock().unwrap().from_host.clone()
        }
        pub fn clear(&self) {
            self.0.lock().unwrap().from_host.clear();
        }
        pub fn push(&self, data: &[u8]) {
            self.0.lock().unwrap().to_host.extend(data);
        }
    }

    impl Link for FakeLink {
        fn write_all(&mut self, data: &[u8]) -> io::Result<()> {
            let mut w = self.0.lock().unwrap();
            if w.fail_writes {
                return Err(io::Error::new(io::ErrorKind::BrokenPipe, "unplugged"));
            }
            w.from_host.extend_from_slice(data);
            if let Some(mut r) = w.responder.take() {
                let reply = r(data);
                w.to_host.extend(reply);
                w.responder = Some(r);
            }
            Ok(())
        }
        fn read(&mut self, buf: &mut [u8], timeout: Duration) -> io::Result<usize> {
            let n = {
                let mut w = self.0.lock().unwrap();
                let n = buf.len().min(w.to_host.len());
                for b in buf.iter_mut().take(n) {
                    *b = w.to_host.pop_front().unwrap();
                }
                n
            };
            if n == 0 && !timeout.is_zero() {
                std::thread::sleep(timeout.min(Duration::from_millis(1)));
            }
            Ok(n)
        }
        fn clear_input(&mut self) -> io::Result<()> {
            Ok(()) // like a board that prints its boot line after the port opens
        }
    }

    /// Port name -> factory making a fresh board per open (opening resets a Nano).
    #[derive(Default)]
    pub struct FakeOpener {
        pub ports: Vec<PortInfo>,
        #[allow(clippy::type_complexity)]
        pub boards: HashMap<String, Box<dyn Fn() -> FakeLink + Send + Sync>>,
        pub opened: Mutex<Vec<(String, FakeLink)>>,
    }

    impl FakeOpener {
        pub fn board(mut self, port: &str, make: impl Fn() -> FakeLink + Send + Sync + 'static) -> Self {
            self.ports.push(PortInfo { name: port.into(), vid: Some(0x2341), ..Default::default() });
            self.boards.insert(port.into(), Box::new(make));
            self
        }
        pub fn last(&self, port: &str) -> Option<FakeLink> {
            self.opened.lock().unwrap().iter().rev().find(|(p, _)| p == port).map(|(_, l)| l.clone())
        }
        pub fn open_count(&self, port: &str) -> usize {
            self.opened.lock().unwrap().iter().filter(|(p, _)| p == port).count()
        }
    }

    impl Opener for FakeOpener {
        fn list(&self) -> Vec<PortInfo> {
            self.ports.clone()
        }
        fn open(&self, port: &str, _baud: u32) -> io::Result<Box<dyn Link>> {
            let make = self.boards.get(port).ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, port.to_string()))?;
            let l = make();
            self.opened.lock().unwrap().push((port.into(), l.clone()));
            Ok(Box::new(l))
        }
    }

    /// Replies `reply` to every write containing `trigger`.
    pub fn on(trigger: &'static str, reply: &'static str) -> impl FnMut(&[u8]) -> Vec<u8> + Send {
        move |d| {
            if String::from_utf8_lossy(d).contains(trigger) {
                reply.as_bytes().to_vec()
            } else {
                vec![]
            }
        }
    }

    pub fn silent() -> impl FnMut(&[u8]) -> Vec<u8> + Send {
        |_| vec![]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn p(name: &str, vid: Option<u16>) -> PortInfo {
        PortInfo { name: name.into(), vid, ..Default::default() }
    }

    #[test]
    fn probe_ranks_and_excludes() {
        let ports = [
            p("/dev/cu.Bluetooth-Incoming-Port", None),
            p("/dev/cu.usbserial-10", None),
            p("/dev/tty.usbmodem1101", Some(0x2341)),
            p("/dev/cu.usbmodem1101", Some(0x2341)),
            p("/dev/cu.usbmodem2201", Some(0x2341)),
        ];
        assert_eq!(
            probe_led_ports(&ports, &["/dev/tty.usbmodem2201".into()]),
            vec!["/dev/cu.usbmodem1101", "/dev/cu.usbserial-10"]
        );
    }

    #[test]
    fn probe_skips_the_servo_controller_on_any_board() {
        let usb = |name: &str, vid: u16, pid: u16, product: Option<&str>| PortInfo {
            name: name.into(),
            vid: Some(vid),
            pid: Some(pid),
            product: product.map(Into::into),
            ..Default::default()
        };
        let ports = [
            usb("/dev/cu.usbmodem101", 0x2E8A, 0x000A, None), // RP2040 build
            usb("/dev/cu.usbmodem201", 0x16C0, 0x0483, Some("r3x-servo")), // Teensy 4.1 build
            usb("/dev/cu.usbmodem301", 0x1234, 0x5678, Some("r3x-servo")), // any future board
            usb("/dev/cu.usbmodem401", 0x2341, 0x0043, Some("Arduino Uno")), // an LED board
        ];
        assert_eq!(probe_led_ports(&ports, &[]), vec!["/dev/cu.usbmodem401"]);
        // The Teensy's ID alone is enough where the OS reports no product string.
        assert!(is_servo_controller(&usb("/dev/ttyACM0", 0x16C0, 0x0483, None)));
    }
}

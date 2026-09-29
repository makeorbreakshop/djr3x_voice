//! Face board (`rex_face_v3_clean`): eyes + mouth, animation on the board (D5).
//!
//! Firmware words: `SI SE SL ST SS SF` (ack `+`), `Mnnn` mouth 000-255 (no ack), `T`/`T1-T7`
//! tests, `R` reset (ack `+`), `?` help. It boots printing `READY`.
//!
//! Connect (CantinaOS SimpleEyeAdapter): open, wait for the boot, look for `READY`; else send
//! `R` and expect `+`. Three attempts per port, then fail open to mock. A board that says
//! `CHEST READY` is the chest: back off from it at once.
//!
//! Mouth (plan §7b): the performer's EMA(0.3) amplitude arrives as `Mnnn`; this driver sends
//! the latest level at one rate from the profile (`audio.mouth_hz`, default 30 Hz),
//! coalescing rather than dropping, and guarantees `M000` at speech end: any state word
//! other than `SS` is preceded by `M000` if the mouth was left open.

use std::sync::Arc;
use std::time::{Duration, Instant};

use r3x_contracts::RobotProfile;
use r3x_performer_core::performer::Out;

use crate::link::{probe_led_ports, LineLink, Lines, Link, Opener};
use crate::{env_flag, env_str, Driver, Health};

#[derive(Debug, Clone)]
pub struct HandshakeTiming {
    /// Opening resets a Nano; let it boot before listening.
    pub boot_wait: Duration,
    pub ready_wait: Duration,
    pub probe_wait: Duration,
    pub retries: u32,
    pub retry_delay: Duration,
}

impl Default for HandshakeTiming {
    fn default() -> Self {
        HandshakeTiming {
            boot_wait: Duration::from_secs(2),
            ready_wait: Duration::from_secs(5),
            probe_wait: Duration::from_millis(500),
            retries: 3,
            retry_delay: Duration::from_millis(500),
        }
    }
}

#[derive(Debug, Clone)]
pub struct FaceConfig {
    /// Explicit port (`ARDUINO_SERIAL_PORT`); None = probe.
    pub port: Option<String>,
    /// `FORCE_MOCK_LED_CONTROLLER`.
    pub force_mock: bool,
    pub baud: u32,
    pub mouth_hz: f64,
    /// Ports another driver owns.
    pub exclude: Vec<String>,
    pub timing: HandshakeTiming,
}

impl FaceConfig {
    pub fn from_env(profile: &RobotProfile) -> Self {
        FaceConfig {
            port: env_str("ARDUINO_SERIAL_PORT"),
            force_mock: env_flag("FORCE_MOCK_LED_CONTROLLER"),
            baud: 115_200,
            mouth_hz: profile.audio.mouth_hz,
            exclude: env_str("CHEST_SERIAL_PORT").into_iter().collect(),
            timing: HandshakeTiming::default(),
        }
    }
}

enum Identity {
    Face,
    Chest,
    Silent,
}

fn is_chest_line(l: &str) -> bool {
    l.starts_with("CHEST READY") || l.starts_with("Chest:")
}

fn handshake(link: &mut dyn Link, t: &HandshakeTiming) -> std::io::Result<Identity> {
    link.clear_input()?;
    std::thread::sleep(t.boot_wait);
    let mut lines = Lines::default();
    let deadline = Instant::now() + t.ready_wait;
    while let Some(l) = lines.next(link, deadline)? {
        if l == "READY" {
            return Ok(Identity::Face);
        }
        if is_chest_line(&l) {
            return Ok(Identity::Chest);
        }
    }
    link.write_all(b"R\n")?;
    let deadline = Instant::now() + t.probe_wait;
    while let Some(l) = lines.next(link, deadline)? {
        if l == "+" {
            return Ok(Identity::Face);
        }
        if is_chest_line(&l) {
            return Ok(Identity::Chest);
        }
    }
    Ok(Identity::Silent)
}

/// Find and open the face board; never fails (mock on no board).
pub fn connect(opener: &dyn Opener, cfg: &FaceConfig) -> LineLink {
    if cfg.force_mock {
        return LineLink::mock("FORCE_MOCK_LED_CONTROLLER");
    }
    let candidates = match &cfg.port {
        Some(p) => vec![p.clone()],
        None => probe_led_ports(&opener.list(), &cfg.exclude),
    };
    if candidates.is_empty() {
        return LineLink::mock("no serial port found");
    }
    for port in &candidates {
        for attempt in 1..=cfg.timing.retries {
            let result = opener.open(port, cfg.baud).and_then(|mut link| {
                let id = handshake(link.as_mut(), &cfg.timing)?;
                Ok((id, link))
            });
            match result {
                Ok((Identity::Face, link)) => {
                    tracing::info!(port, attempt, "face board connected");
                    return LineLink::connected(link, port.clone());
                }
                Ok((Identity::Chest, _)) => {
                    tracing::info!(port, "that is the chest board; backing off");
                    break;
                }
                Ok((Identity::Silent, _)) => tracing::debug!(port, attempt, "face board silent"),
                Err(e) => tracing::debug!(port, attempt, "face open failed: {e}"),
            }
            if attempt < cfg.timing.retries {
                std::thread::sleep(cfg.timing.retry_delay);
            }
        }
    }
    LineLink::mock(format!("no face board on {}", candidates.join(", ")))
}

pub struct FaceDriver {
    cfg: FaceConfig,
    opener: Arc<dyn Opener>,
    pub led: LineLink,
    enabled: bool,
    /// Last state word sent (re-asserted on enable).
    state: Option<String>,
    /// Last mouth level sent; None = unknown.
    mouth: Option<u16>,
    pending_mouth: Option<u16>,
    last_mouth_at: f64,
    last_drain: f64,
}

impl FaceDriver {
    pub fn new(cfg: FaceConfig, opener: Arc<dyn Opener>) -> Self {
        FaceDriver {
            cfg,
            opener,
            led: LineLink::mock("not started"),
            enabled: true,
            state: None,
            mouth: None,
            pending_mouth: None,
            last_mouth_at: f64::NEG_INFINITY,
            last_drain: f64::NEG_INFINITY,
        }
    }

    fn interval(&self) -> f64 {
        1.0 / self.cfg.mouth_hz.max(1.0)
    }

    fn send_mouth(&mut self, level: u16, now: f64) {
        self.led.send(&format!("M{level:03}"));
        self.mouth = Some(level);
        self.last_mouth_at = now;
    }

    /// Close the mouth if it was left open (speech end, or any non-speaking state).
    fn close_mouth(&mut self, now: f64) {
        self.pending_mouth = None;
        if self.mouth != Some(0) {
            self.send_mouth(0, now);
        }
    }

    pub fn line(&mut self, line: &str, now: f64) {
        if let Some(state) = line.strip_prefix('S').filter(|s| s.len() == 1) {
            if self.enabled {
                if state != "S" && self.mouth.is_some_and(|m| m != 0) {
                    self.close_mouth(now);
                }
                self.led.send(line);
            }
            self.state = Some(line.to_string());
        } else if let Some(level) = line.strip_prefix('M').filter(|d| d.len() == 3).and_then(|d| d.parse::<u16>().ok()) {
            if !self.enabled {
                return;
            }
            if level == 0 {
                self.close_mouth(now);
            } else {
                self.pending_mouth = (self.mouth != Some(level)).then_some(level);
                self.poll_mouth(now);
            }
        } else if self.enabled {
            self.led.send(line);
        }
    }

    fn poll_mouth(&mut self, now: f64) {
        if let Some(level) = self.pending_mouth {
            if now - self.last_mouth_at >= self.interval() - 1e-9 {
                self.pending_mouth = None;
                self.send_mouth(level, now);
            }
        }
    }
}

impl Driver for FaceDriver {
    fn name(&self) -> &str {
        "driver.face"
    }
    fn outputs(&self) -> Vec<String> {
        vec!["eyes".into(), "mouth".into()]
    }
    fn start(&mut self, _now: f64) {
        self.led = connect(self.opener.as_ref(), &self.cfg);
    }
    fn set_enabled(&mut self, on: bool, now: f64) {
        if on == self.enabled {
            return;
        }
        if !on {
            self.close_mouth(now);
        }
        self.enabled = on;
        if on {
            if let Some(s) = self.state.clone() {
                self.led.send(&s);
            }
        }
    }
    fn on_out(&mut self, out: &Out, now: f64) {
        if let Out::FaceLine { line } = out {
            self.line(line, now);
        }
    }
    fn poll(&mut self, now: f64) {
        self.poll_mouth(now);
        if now - self.last_drain >= 0.25 {
            self.last_drain = now;
            self.led.drain_input();
        }
    }
    fn next_poll(&self, now: f64) -> f64 {
        match self.pending_mouth {
            Some(_) => (self.last_mouth_at + self.interval()).max(now),
            None => now + 0.25,
        }
    }
    fn health(&self) -> Health {
        self.led.health(self.cfg.force_mock)
    }
    fn stop(&mut self, now: f64) {
        self.close_mouth(now);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::link::fake::{on, silent, FakeLink, FakeOpener};
    use r3x_contracts::ServiceStatus;

    fn cfg(port: Option<&str>) -> FaceConfig {
        FaceConfig {
            port: port.map(str::to_string),
            force_mock: false,
            baud: 115_200,
            mouth_hz: 30.0,
            exclude: vec![],
            timing: HandshakeTiming {
                boot_wait: Duration::ZERO,
                ready_wait: Duration::from_millis(20),
                probe_wait: Duration::from_millis(20),
                retries: 3,
                retry_delay: Duration::ZERO,
            },
        }
    }

    fn driver(opener: FakeOpener, port: Option<&str>) -> (FaceDriver, Arc<FakeOpener>) {
        let opener = Arc::new(opener);
        let mut d = FaceDriver::new(cfg(port), opener.clone());
        d.start(0.0);
        (d, opener)
    }

    #[test]
    fn ready_on_boot_connects() {
        let (d, _) = driver(FakeOpener::default().board("/dev/cu.usbmodem1", || FakeLink::new("READY\n", silent())), None);
        assert_eq!(d.health(), Health::new(ServiceStatus::Running, Some("/dev/cu.usbmodem1".into())));
    }

    #[test]
    fn reset_probe_connects_when_ready_was_missed() {
        let (d, o) = driver(FakeOpener::default().board("/dev/x", || FakeLink::new("", on("R", "+\n"))), Some("/dev/x"));
        assert!(d.led.link.is_some());
        assert_eq!(o.last("/dev/x").unwrap().lines(), vec!["R"]);
    }

    #[test]
    fn silent_board_retries_three_times_then_fails_open() {
        let (mut d, o) = driver(FakeOpener::default().board("/dev/x", || FakeLink::new("", silent())), Some("/dev/x"));
        assert_eq!(o.open_count("/dev/x"), 3);
        assert_eq!(d.health().status, ServiceStatus::Degraded);
        d.line("SI", 0.0); // mock: dropped, never an error
        assert_eq!(d.led.dropped, 1);
    }

    #[test]
    fn backs_off_from_the_chest_and_takes_the_next_port() {
        let o = FakeOpener::default()
            .board("/dev/cu.usbmodemA", || FakeLink::new("CHEST READY\n", silent()))
            .board("/dev/cu.usbmodemB", || FakeLink::new("READY\n", silent()));
        let (d, o) = driver(o, None);
        assert_eq!(o.open_count("/dev/cu.usbmodemA"), 1, "no retries against the chest");
        assert_eq!(d.led.port.as_deref(), Some("/dev/cu.usbmodemB"));
    }

    #[test]
    fn forced_mock_never_opens() {
        let o = Arc::new(FakeOpener::default().board("/dev/x", || FakeLink::new("READY\n", silent())));
        let mut c = cfg(Some("/dev/x"));
        c.force_mock = true;
        let mut d = FaceDriver::new(c, o.clone());
        d.start(0.0);
        assert_eq!(o.open_count("/dev/x"), 0);
        assert_eq!(d.health().status, ServiceStatus::Running);
    }

    #[test]
    fn mouth_is_rate_limited_coalesced_and_closed_at_speech_end() {
        let (mut d, o) = driver(FakeOpener::default().board("/dev/x", || FakeLink::new("READY\n", silent())), Some("/dev/x"));
        let wire = o.last("/dev/x").unwrap();
        d.line("SS", 0.0);
        d.line("M100", 0.000); // sent
        d.line("M120", 0.010); // pending
        d.line("M140", 0.020); // replaces it
        d.poll(0.030);
        assert_eq!(wire.lines(), vec!["SS", "M100"]);
        d.poll(0.034);
        d.line("M140", 0.040); // unchanged from the pending level sent above: nothing new
        d.poll(0.070);
        // The performer's M000 was lost (old 10 Hz adapter): SF still closes the mouth first.
        d.line("SF", 0.080);
        d.line("M000", 0.090); // already closed
        assert_eq!(wire.lines(), vec!["SS", "M100", "M140", "M000", "SF"]);
    }

    #[test]
    fn disable_closes_mouth_and_enable_reasserts_state() {
        let (mut d, o) = driver(FakeOpener::default().board("/dev/x", || FakeLink::new("READY\n", silent())), Some("/dev/x"));
        let wire = o.last("/dev/x").unwrap();
        d.line("SS", 0.0);
        d.line("M200", 0.0);
        d.set_enabled(false, 0.1);
        d.line("SL", 0.2);
        d.line("M050", 0.2);
        d.set_enabled(true, 0.3);
        assert_eq!(wire.lines(), vec!["SS", "M200", "M000", "SL"]);
    }
}

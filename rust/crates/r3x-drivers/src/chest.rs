//! Chest board (`rex_chest_v1`): logic panels, animation on the board (D5).
//!
//! Firmware words: `SI SE SL ST SS` (ack `+`), `SF` sparkle, `Mnnn` amplitude, `Bnnn` tempo,
//! `Xn` system state (ack `+`), `Hxxx` health mask, `R` reset (silent), `?` identify. Its
//! `rxBuf` is 8 bytes, so every line is short and sent whole. It boots printing
//! `CHEST READY`; `?` answers `Chest: ...`. The face prints `READY`: back off from it.
//!
//! What to send (boot sweep, health mask, fault latch, overrides) is the performer's
//! `ChestHost`; this driver only transports, at 20 Hz, send-on-change per channel
//! (`S M B X H`). `SF` and other words without a channel always go out, in order. While
//! frozen (or disabled) the board holds what it shows; on release every channel is re-sent.

use std::collections::BTreeMap;
use std::sync::Arc;
use std::time::{Duration, Instant};

use r3x_performer_core::performer::Out;

use crate::link::{probe_led_ports, LineLink, Lines, Link, Opener};
use crate::{env_flag, env_str, Driver, Health};

pub const LOOP_HZ: f64 = 20.0;
/// Re-send order on release: whole-chest state first, then the interaction channels.
const CHANNELS: [char; 5] = ['X', 'H', 'S', 'B', 'M'];

#[derive(Debug, Clone)]
pub struct ChestConfig {
    /// `CHEST_SERIAL_PORT`; None = probe (skipping `exclude`).
    pub port: Option<String>,
    /// `FORCE_MOCK_CHEST`, or `CHEST_ENABLED=false`.
    pub force_mock: bool,
    pub baud: u32,
    /// The face board's port(s).
    pub exclude: Vec<String>,
    pub identify_wait: Duration,
    /// Silence longer than this -> ask `?`.
    pub ask_every: Duration,
}

impl ChestConfig {
    pub fn from_env() -> Self {
        let disabled = std::env::var("CHEST_ENABLED").is_ok_and(|v| !crate::env_flag_value(&v));
        ChestConfig {
            port: env_str("CHEST_SERIAL_PORT"),
            force_mock: env_flag("FORCE_MOCK_CHEST") || disabled,
            baud: 115_200,
            exclude: env_str("ARDUINO_SERIAL_PORT").into_iter().collect(),
            identify_wait: Duration::from_secs(3),
            ask_every: Duration::from_millis(100),
        }
    }
}

/// `true` if the board identifies as the chest; `false` for the face or silence.
fn identify(link: &mut dyn Link, cfg: &ChestConfig) -> std::io::Result<bool> {
    let mut lines = Lines::default();
    let deadline = Instant::now() + cfg.identify_wait;
    loop {
        let now = Instant::now();
        if now >= deadline {
            return Ok(false);
        }
        match lines.next(link, (now + cfg.ask_every).min(deadline))? {
            Some(l) if l.starts_with("CHEST READY") || l.starts_with("Chest:") => return Ok(true),
            Some(l) if l == "READY" => return Ok(false), // the face board - not ours
            Some(_) => {}
            None => link.write_all(b"?\n")?,
        }
    }
}

pub fn connect(opener: &dyn Opener, cfg: &ChestConfig) -> LineLink {
    if cfg.force_mock {
        return LineLink::mock("FORCE_MOCK_CHEST / CHEST_ENABLED=false");
    }
    let candidates = match &cfg.port {
        Some(p) => vec![p.clone()],
        None => probe_led_ports(&opener.list(), &cfg.exclude),
    };
    for port in &candidates {
        match opener.open(port, cfg.baud) {
            Ok(mut link) => match identify(link.as_mut(), cfg) {
                Ok(true) => {
                    tracing::info!(port, "chest board connected");
                    return LineLink::connected(link, port.clone());
                }
                Ok(false) => tracing::info!(port, "not the chest board"),
                Err(e) => tracing::debug!(port, "chest identify failed: {e}"),
            },
            Err(e) => tracing::debug!(port, "chest open failed: {e}"),
        }
    }
    LineLink::mock(if candidates.is_empty() { "no serial port found".into() } else { format!("no chest board on {}", candidates.join(", ")) })
}

fn channel(line: &str) -> Option<char> {
    let c = line.chars().next()?;
    match (c, line.len()) {
        ('S', 2) if line != "SF" => Some('S'),
        ('X', 2) | ('M' | 'B' | 'H', 4) => Some(c),
        _ => None,
    }
}

pub struct ChestDriver {
    cfg: ChestConfig,
    opener: Arc<dyn Opener>,
    pub led: LineLink,
    queue: Vec<String>,
    /// Latest wanted value per channel, and what the board was last sent.
    desired: BTreeMap<char, String>,
    sent: BTreeMap<char, String>,
    frozen: bool,
    enabled: bool,
    resync: bool,
    last_flush: f64,
}

impl ChestDriver {
    pub fn new(cfg: ChestConfig, opener: Arc<dyn Opener>) -> Self {
        ChestDriver {
            cfg,
            opener,
            led: LineLink::mock("not started"),
            queue: vec![],
            desired: BTreeMap::new(),
            sent: BTreeMap::new(),
            frozen: false,
            enabled: true,
            resync: false,
            last_flush: f64::NEG_INFINITY,
        }
    }

    fn held(&self) -> bool {
        self.frozen || !self.enabled
    }

    fn set_held(&mut self, frozen: bool, enabled: bool) {
        let was = self.held();
        self.frozen = frozen;
        self.enabled = enabled;
        if was && !self.held() {
            self.resync = true;
        }
    }

    fn flush(&mut self) {
        let batch = std::mem::take(&mut self.queue);
        if self.held() {
            for l in batch {
                if let Some(c) = channel(&l) {
                    self.desired.insert(c, l);
                }
            }
            return;
        }
        if std::mem::take(&mut self.resync) {
            for c in CHANNELS {
                if let Some(l) = self.desired.get(&c).cloned() {
                    self.led.send(&l);
                    self.sent.insert(c, l);
                }
            }
        }
        for (i, l) in batch.iter().enumerate() {
            match channel(l) {
                // Amplitude is a level: only the newest in the batch matters.
                Some('M') if batch[i + 1..].iter().any(|n| channel(n) == Some('M')) => {}
                Some(c) => {
                    self.desired.insert(c, l.clone());
                    if self.sent.get(&c) != Some(l) {
                        self.led.send(l);
                        self.sent.insert(c, l.clone());
                    }
                }
                None => {
                    self.led.send(l);
                }
            }
        }
    }
}

impl Driver for ChestDriver {
    fn name(&self) -> &str {
        "driver.chest"
    }
    fn outputs(&self) -> Vec<String> {
        vec!["chest".into()]
    }
    fn start(&mut self, _now: f64) {
        self.led = connect(self.opener.as_ref(), &self.cfg);
    }
    fn set_enabled(&mut self, on: bool, _now: f64) {
        self.set_held(self.frozen, on);
    }
    fn on_out(&mut self, out: &Out, _now: f64) {
        match out {
            Out::ChestLine { line } => self.queue.push(line.clone()),
            Out::Freeze { on } => self.set_held(*on, self.enabled),
            _ => {}
        }
    }
    fn poll(&mut self, now: f64) {
        if now - self.last_flush >= 1.0 / LOOP_HZ - 1e-9 {
            self.last_flush = now;
            self.flush();
            self.led.drain_input();
        }
    }
    fn next_poll(&self, _now: f64) -> f64 {
        self.last_flush + 1.0 / LOOP_HZ
    }
    fn health(&self) -> Health {
        self.led.health(self.cfg.force_mock)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::link::fake::{on, silent, FakeLink, FakeOpener};
    use r3x_contracts::ServiceStatus;

    fn cfg(port: Option<&str>) -> ChestConfig {
        ChestConfig {
            port: port.map(str::to_string),
            force_mock: false,
            baud: 115_200,
            exclude: vec![],
            identify_wait: Duration::from_millis(30),
            ask_every: Duration::from_millis(5),
        }
    }

    fn start(o: FakeOpener, port: Option<&str>) -> (ChestDriver, Arc<FakeOpener>) {
        let o = Arc::new(o);
        let mut d = ChestDriver::new(cfg(port), o.clone());
        d.start(0.0);
        (d, o)
    }

    fn line(d: &mut ChestDriver, l: &str) {
        d.on_out(&Out::ChestLine { line: l.into() }, 0.0);
    }

    #[test]
    fn identity_boot_line_or_query() {
        let (d, _) = start(FakeOpener::default().board("/dev/a", || FakeLink::new("CHEST READY\n", silent())), None);
        assert!(d.led.link.is_some());
        // Boot line missed: `?` gets "Chest: ...".
        let (d, o) = start(FakeOpener::default().board("/dev/a", || FakeLink::new("", on("?", "Chest: rex_chest_v1\n"))), None);
        assert!(d.led.link.is_some());
        assert_eq!(o.last("/dev/a").unwrap().lines()[0], "?");
    }

    #[test]
    fn backs_off_from_the_face_and_fails_open() {
        let o = FakeOpener::default()
            .board("/dev/face", || FakeLink::new("READY\n", silent()))
            .board("/dev/chest", || FakeLink::new("CHEST READY\n", silent()));
        let (d, _) = start(o, None);
        assert_eq!(d.led.port.as_deref(), Some("/dev/chest"));
        let (d, _) = start(FakeOpener::default().board("/dev/face", || FakeLink::new("READY\n", silent())), Some("/dev/face"));
        assert_eq!(d.health().status, ServiceStatus::Degraded);
    }

    #[test]
    fn twenty_hz_send_on_change_keeps_triggers_in_order() {
        let (mut d, o) = start(FakeOpener::default().board("/dev/a", || FakeLink::new("CHEST READY\n", silent())), None);
        let wire = o.last("/dev/a").unwrap();
        for l in ["X0", "H1FF", "SS", "M010", "M020"] {
            line(&mut d, l);
        }
        d.poll(0.00);
        assert_eq!(wire.lines(), ["X0", "H1FF", "SS", "M020"]);
        line(&mut d, "X0"); // unchanged: not re-sent
        line(&mut d, "M000");
        line(&mut d, "SF");
        line(&mut d, "SE");
        d.poll(0.02); // too soon for the 20 Hz loop
        assert_eq!(wire.lines().len(), 4);
        d.poll(0.05);
        assert_eq!(wire.lines()[4..], ["M000", "SF", "SE"]);
    }

    #[test]
    fn freeze_holds_then_resends_every_channel() {
        let (mut d, o) = start(FakeOpener::default().board("/dev/a", || FakeLink::new("CHEST READY\n", silent())), None);
        let wire = o.last("/dev/a").unwrap();
        for l in ["X0", "H1FF", "SE", "B120"] {
            line(&mut d, l);
        }
        d.poll(0.0);
        wire.clear();
        d.on_out(&Out::Freeze { on: true }, 0.0);
        line(&mut d, "SL");
        line(&mut d, "SF"); // a trigger while frozen is dropped
        d.poll(0.1);
        assert!(wire.lines().is_empty());
        d.on_out(&Out::Freeze { on: false }, 0.1);
        d.poll(0.2);
        assert_eq!(wire.lines(), ["X0", "H1FF", "SL", "B120"]);
    }
}

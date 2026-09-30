//! grnwave LED set (`firmware/grnwave_nano`): body boards, eyes and mouth on one Nano.
//!
//! The sketch speaks both boards' named words, so this driver merges the performer's two
//! streams: from the face stream `S*`, `SF` and `Mnnn`; from the chest stream only `Bnnn`,
//! `Xn`, `Hxxx` (its `S*`/`M` duplicate the face's; [`from_chest_stream`]). It sends at the
//! profile's mouth rate (`audio.mouth_hz`), send-on-change per channel (`S M B X H`), newest
//! amplitude wins, and guarantees `M000` before any state other than `SS`. While frozen or
//! disabled the board holds; on release every channel is re-sent.
//!
//! Connect: open, wait for `GRNWAVE READY`, else ask `?` and expect `Grnwave: ...`. A board
//! that says `READY` (face) or `CHEST READY` is someone else's: back off. The sketch's `R` is
//! silent, so the face driver's `R` probe never mistakes it for the face.

use std::collections::BTreeMap;
use std::sync::Arc;
use std::time::{Duration, Instant};

use r3x_contracts::RobotProfile;
use r3x_performer_core::leds::grnwave::{from_chest_stream, READY};
use r3x_performer_core::performer::Out;

use crate::link::{probe_led_ports, Backoff, LineLink, Lines, Link, Opener};
use crate::{env_flag, env_str, Driver, Health};

/// Re-send order on release: whole-body state first, then the interaction channels.
const CHANNELS: [char; 5] = ['X', 'H', 'S', 'B', 'M'];

#[derive(Debug, Clone)]
pub struct GrnwaveConfig {
    /// `GRNWAVE_SERIAL_PORT`; None = probe (skipping `exclude`).
    pub port: Option<String>,
    /// `FORCE_MOCK_GRNWAVE`.
    pub force_mock: bool,
    pub baud: u32,
    /// Send rate (the profile's `audio.mouth_hz`).
    pub hz: f64,
    /// Ports other boards own (`ARDUINO_SERIAL_PORT`, `CHEST_SERIAL_PORT`).
    pub exclude: Vec<String>,
    /// Opening resets a Nano; let it boot before listening.
    pub boot_wait: Duration,
    pub identify_wait: Duration,
    pub ask_every: Duration,
}

impl GrnwaveConfig {
    pub fn from_env(profile: &RobotProfile) -> Self {
        GrnwaveConfig {
            port: env_str("GRNWAVE_SERIAL_PORT"),
            force_mock: env_flag("FORCE_MOCK_GRNWAVE"),
            baud: 115_200,
            hz: profile.audio.mouth_hz,
            exclude: ["ARDUINO_SERIAL_PORT", "CHEST_SERIAL_PORT"].into_iter().filter_map(env_str).collect(),
            boot_wait: Duration::from_millis(1500),
            identify_wait: Duration::from_secs(3),
            ask_every: Duration::from_millis(200),
        }
    }
}

/// Lines by which a board says it is the grnwave sketch.
pub fn is_grnwave_line(l: &str) -> bool {
    l.starts_with(READY) || l.starts_with("Grnwave:")
}

fn identify(link: &mut dyn Link, cfg: &GrnwaveConfig) -> std::io::Result<bool> {
    std::thread::sleep(cfg.boot_wait);
    let mut lines = Lines::default();
    let deadline = Instant::now() + cfg.identify_wait;
    loop {
        let now = Instant::now();
        if now >= deadline {
            return Ok(false);
        }
        match lines.next(link, (now + cfg.ask_every).min(deadline))? {
            Some(l) if is_grnwave_line(&l) => return Ok(true),
            Some(l) if l == "READY" || l.starts_with("CHEST READY") || l.starts_with("Chest:") => return Ok(false),
            Some(_) => {}
            None => link.write_all(b"?\n")?,
        }
    }
}

pub fn connect(opener: &dyn Opener, cfg: &GrnwaveConfig) -> LineLink {
    if cfg.force_mock {
        return LineLink::mock("FORCE_MOCK_GRNWAVE");
    }
    let candidates = match &cfg.port {
        Some(p) => vec![p.clone()],
        None => probe_led_ports(&opener.list(), &cfg.exclude),
    };
    for port in &candidates {
        match opener.open(port, cfg.baud) {
            Ok(mut link) => match identify(link.as_mut(), cfg) {
                Ok(true) => {
                    tracing::info!(port, "grnwave board connected");
                    return LineLink::connected(link, port.clone());
                }
                Ok(false) => tracing::info!(port, "not the grnwave board"),
                Err(e) => tracing::debug!(port, "grnwave identify failed: {e}"),
            },
            Err(e) => tracing::debug!(port, "grnwave open failed: {e}"),
        }
    }
    LineLink::mock(if candidates.is_empty() { "no serial port found".into() } else { format!("no grnwave board on {}", candidates.join(", ")) })
}

fn channel(line: &str) -> Option<char> {
    let c = line.chars().next()?;
    match (c, line.len()) {
        ('S', 2) if line != "SF" => Some('S'),
        ('X', 2) | ('M' | 'B' | 'H', 4) => Some(c),
        _ => None,
    }
}

pub struct GrnwaveDriver {
    cfg: GrnwaveConfig,
    opener: Arc<dyn Opener>,
    pub led: LineLink,
    queue: Vec<String>,
    desired: BTreeMap<char, String>,
    sent: BTreeMap<char, String>,
    frozen: bool,
    enabled: bool,
    resync: bool,
    last_flush: f64,
    retry_port: Option<String>,
    backoff: Backoff,
}

impl GrnwaveDriver {
    pub fn new(cfg: GrnwaveConfig, opener: Arc<dyn Opener>) -> Self {
        GrnwaveDriver {
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
            retry_port: None,
            backoff: Backoff::default(),
        }
    }

    fn interval(&self) -> f64 {
        1.0 / self.cfg.hz.max(1.0)
    }

    fn reconnect(&mut self, now: f64) {
        if self.led.link.is_some() || self.cfg.force_mock {
            return;
        }
        let Some(port) = self.retry_port.clone() else { return };
        if !self.backoff.due(now) {
            return;
        }
        let led = connect(self.opener.as_ref(), &GrnwaveConfig { port: Some(port.clone()), ..self.cfg.clone() });
        if led.link.is_none() {
            self.backoff.failed(now);
            self.led.mock_reason = format!("reconnecting to {port}: {}", led.mock_reason);
            return;
        }
        tracing::info!(port, "grnwave board reconnected");
        self.led = led;
        self.backoff.reset();
        self.sent.clear();
        self.resync = true;
    }

    fn held(&self) -> bool {
        self.frozen || !self.enabled
    }

    fn set_held(&mut self, frozen: bool, enabled: bool) {
        let was = self.held();
        self.frozen = frozen;
        self.enabled = enabled;
        if !was && self.held() && self.sent.get(&'M').is_some_and(|m| m != "M000") {
            self.put('M', "M000".into()); // never hold with the mouth open
            self.desired.insert('M', "M000".into());
        }
        if was && !self.held() {
            self.resync = true;
        }
    }

    fn put(&mut self, c: char, l: String) {
        if self.sent.get(&c) != Some(&l) {
            self.led.send(&l);
            self.sent.insert(c, l);
        }
    }

    /// Queue one performer line (either stream; see the module doc).
    pub fn line(&mut self, l: &str, from_chest: bool) {
        if from_chest && !from_chest_stream(l) {
            return;
        }
        self.queue.push(l.to_string());
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
                    self.put(c, l);
                }
            }
        }
        for (i, l) in batch.iter().enumerate() {
            match channel(l) {
                Some('M') if batch[i + 1..].iter().any(|n| channel(n) == Some('M')) => {}
                Some(c) => {
                    if c == 'S' && l != "SS" && self.sent.get(&'M').is_some_and(|m| m != "M000") {
                        self.put('M', "M000".into());
                        self.desired.insert('M', "M000".into());
                    }
                    self.desired.insert(c, l.clone());
                    self.put(c, l.clone());
                }
                None => {
                    if l == "SF" && self.sent.get(&'M').is_some_and(|m| m != "M000") {
                        self.put('M', "M000".into());
                    }
                    self.led.send(l);
                }
            }
        }
    }
}

impl Driver for GrnwaveDriver {
    fn name(&self) -> &str {
        "driver.grnwave"
    }
    fn outputs(&self) -> Vec<String> {
        vec!["body".into(), "eyes".into(), "mouth".into()]
    }
    fn start(&mut self, _now: f64) {
        self.led = connect(self.opener.as_ref(), &self.cfg);
        self.retry_port = self.led.port.clone().or_else(|| self.cfg.port.clone());
    }
    fn set_enabled(&mut self, on: bool, _now: f64) {
        self.set_held(self.frozen, on);
    }
    fn on_out(&mut self, out: &Out, _now: f64) {
        match out {
            Out::FaceLine { line } => self.line(line, false),
            Out::ChestLine { line } => self.line(line, true),
            Out::Freeze { on } => self.set_held(*on, self.enabled),
            _ => {}
        }
    }
    fn poll(&mut self, now: f64) {
        self.reconnect(now);
        if now - self.last_flush >= self.interval() - 1e-9 {
            self.last_flush = now;
            self.flush();
            self.led.drain_input();
        }
    }
    fn next_poll(&self, _now: f64) -> f64 {
        self.last_flush + self.interval()
    }
    fn health(&self) -> Health {
        self.led.health(self.cfg.force_mock)
    }
    fn stop(&mut self, _now: f64) {
        if self.sent.get(&'M').is_some_and(|m| m != "M000") {
            self.put('M', "M000".into());
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::link::fake::{on, silent, FakeLink, FakeOpener};
    use r3x_contracts::ServiceStatus;

    fn cfg(port: Option<&str>) -> GrnwaveConfig {
        GrnwaveConfig {
            port: port.map(str::to_string),
            force_mock: false,
            baud: 115_200,
            hz: 30.0,
            exclude: vec![],
            boot_wait: Duration::ZERO,
            identify_wait: Duration::from_millis(30),
            ask_every: Duration::from_millis(5),
        }
    }

    fn start(o: FakeOpener, port: Option<&str>) -> (GrnwaveDriver, Arc<FakeOpener>) {
        let o = Arc::new(o);
        let mut d = GrnwaveDriver::new(cfg(port), o.clone());
        d.start(0.0);
        (d, o)
    }

    fn board() -> (GrnwaveDriver, FakeLink) {
        let (d, o) = start(FakeOpener::default().board("/dev/g", || FakeLink::new("GRNWAVE READY\n", silent())), None);
        let wire = o.last("/dev/g").unwrap();
        (d, wire)
    }

    fn face(d: &mut GrnwaveDriver, l: &str) {
        d.on_out(&Out::FaceLine { line: l.into() }, 0.0);
    }

    fn chest(d: &mut GrnwaveDriver, l: &str) {
        d.on_out(&Out::ChestLine { line: l.into() }, 0.0);
    }

    #[test]
    fn identity_boot_line_or_query() {
        let (d, _) = board();
        assert!(d.led.link.is_some());
        let (d, o) = start(FakeOpener::default().board("/dev/g", || FakeLink::new("", on("?", "Grnwave: body 96\n"))), None);
        assert!(d.led.link.is_some());
        assert_eq!(o.last("/dev/g").unwrap().lines()[0], "?");
    }

    #[test]
    fn backs_off_from_the_face_and_the_chest_and_fails_open() {
        let o = FakeOpener::default()
            .board("/dev/a_face", || FakeLink::new("READY\n", silent()))
            .board("/dev/b_chest", || FakeLink::new("CHEST READY\n", silent()))
            .board("/dev/c_grn", || FakeLink::new("GRNWAVE READY\n", silent()));
        let (d, _) = start(o, None);
        assert_eq!(d.led.port.as_deref(), Some("/dev/c_grn"));
        let (mut d, _) = start(FakeOpener::default().board("/dev/a", || FakeLink::new("READY\n", silent())), Some("/dev/a"));
        assert_eq!(d.health().status, ServiceStatus::Degraded);
        face(&mut d, "SI");
        d.poll(0.0); // mock: dropped, never an error
        assert_eq!(d.led.dropped, 1);
    }

    #[test]
    fn merges_both_streams_without_duplicates() {
        let (mut d, wire) = board();
        chest(&mut d, "X0");
        chest(&mut d, "H1FF");
        chest(&mut d, "SL"); // the chest's copy of the state: dropped
        face(&mut d, "SL");
        chest(&mut d, "M050"); // the chest's amplitude: dropped
        chest(&mut d, "B120");
        d.poll(0.0);
        assert_eq!(wire.lines(), ["X0", "H1FF", "SL", "B120"]);
    }

    #[test]
    fn mouth_newest_wins_and_is_closed_before_any_other_state() {
        let (mut d, wire) = board();
        face(&mut d, "SS");
        face(&mut d, "M100");
        face(&mut d, "M140");
        d.poll(0.0);
        face(&mut d, "M160");
        d.poll(0.01); // inside the 30 Hz period: held
        assert_eq!(wire.lines(), ["SS", "M140"]);
        face(&mut d, "SE"); // the performer's M000 went missing
        d.poll(0.04);
        assert_eq!(wire.lines(), ["SS", "M140", "M160", "M000", "SE"]);
    }

    #[test]
    fn freeze_closes_the_mouth_holds_then_resends_every_channel() {
        let (mut d, wire) = board();
        for l in ["X0", "H1FF", "B120"] {
            chest(&mut d, l);
        }
        face(&mut d, "SS");
        face(&mut d, "M200");
        d.poll(0.0);
        wire.clear();
        d.on_out(&Out::Freeze { on: true }, 0.0);
        assert_eq!(wire.lines(), ["M000"]);
        face(&mut d, "SL");
        face(&mut d, "SF"); // a trigger while frozen is dropped
        d.poll(0.1);
        d.on_out(&Out::Freeze { on: false }, 0.1);
        d.poll(0.2);
        assert_eq!(wire.lines(), ["M000", "SL"], "X/H/B unchanged on the board: only what moved");
    }
}

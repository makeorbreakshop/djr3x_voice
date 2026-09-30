//! Port of `sim/web/src/chest.ts` (logic only; the three.js `ChestLights` renderer is not
//! ported): `ChestFirmware`, the emulator for `cantina_os/arduino/rex_chest_v1` (33 pixels
//! over three middle-ring logic panels), and `ChestHost`, the port of CantinaOS
//! `ChestLightControllerService` that turns bus events into chest serial words.

use std::f64::consts::PI;

use indexmap::IndexMap;
use serde::{Deserialize, Serialize};

use super::firmware::{is_js_space, Rgb};
use crate::rng::Rng;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ChestLightKind {
    Dot,
    Window,
}

/// One entry of rig.json `chest_lights`.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ChestLightSpec {
    pub kind: ChestLightKind,
    pub pos: [f64; 3],
    pub normal: [f64; 3],
    pub w: f64,
    pub h: f64,
    pub panel: String,
}

// Droid-panel palette: dots are indicator LEDs, windows are backlit readouts.
const DOT_COLORS: [Rgb; 5] = [
    [255, 20, 0],
    [255, 110, 0],
    [40, 255, 30],
    [255, 20, 0],
    [0, 120, 255],
];
const WIN_COLORS: [Rgb; 4] = [
    [0, 210, 255],
    [225, 240, 255],
    [0, 175, 150],
    [110, 190, 255],
];
const OFF: Rgb = [0, 0, 0];

/// JS `Math.round` (half rounds toward +inf).
fn js_round(x: f64) -> f64 {
    let f = libm::floor(x);
    if x - f >= 0.5 {
        f + 1.0
    } else {
        f
    }
}

/// A JS sort comparator's result (`a - b`) as an Ordering.
fn js_cmp(d: f64) -> std::cmp::Ordering {
    d.partial_cmp(&0.0).unwrap_or(std::cmp::Ordering::Equal)
}

fn scale(c: Rgb, k: f64) -> Rgb {
    let q = k.clamp(0.0, 1.0);
    c.map(|v| js_round(f64::from(v) * q) as u8)
}

#[derive(Clone, Debug)]
struct Panel {
    dots: Vec<usize>,
    windows: Vec<usize>,
}

#[derive(Clone, Copy, Debug)]
struct DotState {
    on: bool,
    color: usize,
    next: f64,
}

#[derive(Clone, Debug)]
pub struct ChestFirmware {
    pub specs: Vec<ChestLightSpec>,
    pub pixels: Vec<Rgb>,
    /// One of I E L T S.
    pub mode: char,
    pub amplitude: u32,
    pub bpm: u32,
    pub now: f64,
    /// 0 normal, 1 boot sweep (until told otherwise, as on the board), 2 sleep, 3 fault.
    pub sys_state: u8,
    pub health: u32,
    sparkle_until: f64,
    boot_start: f64,
    panels: Vec<Panel>,
    dot_state: Vec<DotState>,
    rx: String,
    rng: Rng,
}

fn digits(s: &[u8]) -> bool {
    s.iter().all(u8::is_ascii_digit)
}

impl ChestFirmware {
    pub fn new(specs: Vec<ChestLightSpec>, mut rng: Rng) -> Self {
        let pixels = vec![OFF; specs.len()];
        // Group by panel, ordered around the ring; dots bottom -> top. Panels are ~31 deg
        // apart; a panel's own openings span < 12 deg, so cluster by gaps in angle.
        let angle = |s: &ChestLightSpec| libm::atan2(s.pos[0], s.pos[2]);
        let mut order: Vec<usize> = (0..specs.len())
            .filter(|&i| specs[i].panel == "MS_P_1_Full")
            .collect();
        order.sort_by(|&a, &b| js_cmp(angle(&specs[a]) - angle(&specs[b])));
        let mut groups: Vec<Vec<usize>> = Vec::new();
        let mut prev = f64::NEG_INFINITY;
        for i in order {
            let deg = angle(&specs[i]) * (180.0 / PI);
            if deg - prev > 15.0 {
                groups.push(Vec::new());
            }
            groups
                .last_mut()
                .expect("first gap from -inf opens a group")
                .push(i);
            prev = deg;
        }
        let panels = groups
            .into_iter()
            .map(|idx| {
                let mut dots: Vec<usize> = idx
                    .iter()
                    .copied()
                    .filter(|&i| specs[i].kind == ChestLightKind::Dot)
                    .collect();
                dots.sort_by(|&a, &b| js_cmp(specs[a].pos[1] - specs[b].pos[1]));
                let windows = idx
                    .into_iter()
                    .filter(|&i| specs[i].kind == ChestLightKind::Window)
                    .collect();
                Panel { dots, windows }
            })
            .collect();
        let dot_state = specs
            .iter()
            .map(|_| {
                let on = rng.next_f64() < 0.5;
                let color = libm::floor(rng.next_f64() * DOT_COLORS.len() as f64) as usize;
                DotState {
                    on,
                    color,
                    next: 0.0,
                }
            })
            .collect();
        ChestFirmware {
            specs,
            pixels,
            mode: 'I',
            amplitude: 0,
            bpm: 0,
            now: 0.0,
            sys_state: 1,
            health: 0x1ff,
            sparkle_until: 0.0,
            boot_start: 0.0,
            panels,
            dot_state,
            rx: String::new(),
            rng,
        }
    }

    pub fn write(&mut self, data: &str) {
        for c in data.chars() {
            if c == '\n' || c == '\r' {
                let cmd = std::mem::take(&mut self.rx);
                self.command(cmd.trim_matches(is_js_space));
            } else {
                self.rx.push(c);
            }
        }
    }

    fn command(&mut self, cmd: &str) {
        let b = cmd.as_bytes();
        let num = |s: &[u8]| s.iter().fold(0u32, |n, d| n * 10 + u32::from(d - b'0'));
        match b {
            [b'S', m @ (b'I' | b'E' | b'L' | b'T' | b'S')] => self.mode = char::from(*m),
            b"SF" => self.sparkle_until = self.now + 350.0,
            [b'M', d @ ..] if d.len() == 3 && digits(d) => self.amplitude = num(d).min(255),
            [b'B', d @ ..] if d.len() == 3 && digits(d) => self.bpm = num(d),
            [b'X', s @ b'0'..=b'3'] => {
                let s = s - b'0';
                if s == 1 && self.sys_state != 1 {
                    self.boot_start = self.now;
                }
                self.sys_state = s;
            }
            [b'H', h @ ..] if h.len() == 3 && h.iter().all(u8::is_ascii_hexdigit) => {
                let s = std::str::from_utf8(h).expect("ascii");
                self.health = u32::from_str_radix(s, 16).expect("hex") & 0x1ff;
            }
            b"R" => {
                self.mode = 'I';
                self.amplitude = 0;
                self.bpm = 0;
                self.sys_state = 0;
                self.health = 0x1ff;
            }
            _ => {}
        }
    }

    /// Whole-chest states that override the interaction patterns (boot, fault, sleep).
    fn system_state(&mut self, ms: f64) -> bool {
        let t = ms / 1000.0;
        if self.sys_state == 1 && ms - self.boot_start > 60000.0 {
            self.sys_state = 0;
        }
        let px = &mut self.pixels;
        match self.sys_state {
            1 => {
                let cycle =
                    (((ms - self.boot_start) / 2400.0) % 1.0) * self.panels.len() as f64 * 1.25;
                for (pi, p) in self.panels.iter().enumerate() {
                    let f = (cycle - pi as f64).clamp(0.0, 1.25);
                    for (k, &i) in p.dots.iter().enumerate() {
                        px[i] = if f * p.dots.len() as f64 > k as f64 {
                            [0, 120, 255]
                        } else {
                            OFF
                        };
                    }
                    for (k, &i) in p.windows.iter().enumerate() {
                        px[i] = if f >= 1.0 {
                            scale(WIN_COLORS[(k + pi * 2) % WIN_COLORS.len()], 0.6)
                        } else {
                            OFF
                        };
                    }
                }
                true
            }
            3 => {
                let pulse = 0.25 + 0.75 * (0.5 + 0.5 * libm::sin(t * PI * 2.0));
                let pos = libm::floor(t * 6.0) % 8.0;
                for p in &self.panels {
                    for (k, &i) in p.dots.iter().enumerate() {
                        px[i] = if k as f64 == pos {
                            scale([255, 20, 0], 0.6)
                        } else {
                            OFF
                        };
                    }
                    for &i in &p.windows {
                        px[i] = scale([255, 20, 0], pulse);
                    }
                }
                true
            }
            2 => {
                let b = 0.03 + 0.05 * (0.5 + 0.5 * libm::sin(t * 0.8));
                for (pi, p) in self.panels.iter().enumerate() {
                    for &i in &p.dots {
                        px[i] = OFF;
                    }
                    for (k, &i) in p.windows.iter().enumerate() {
                        px[i] = scale(WIN_COLORS[(k + pi * 2) % WIN_COLORS.len()], b);
                    }
                }
                if libm::floor(ms / 1000.0) % 3.0 == 0.0 {
                    if let Some(&i) = self.panels.first().and_then(|p| p.dots.first()) {
                        px[i] = scale([40, 255, 30], 0.4);
                    }
                }
                true
            }
            _ => false,
        }
    }

    /// Advance to `ms` and recompute the frame (the Nano would do this at ~50 Hz).
    pub fn update(&mut self, ms: f64) {
        self.now = ms;
        if self.system_state(ms) {
            return;
        }
        let t = ms / 1000.0;
        let amp = libm::sqrt(f64::from(self.amplitude) / 255.0);
        let bpm = f64::from(self.bpm);
        let beat = if self.bpm > 0 { (t * bpm) / 60.0 } else { 0.0 };
        let beat_phase = beat - libm::floor(beat);
        let n_panels = self.panels.len() as f64;
        let engaged = self.mode == 'E';

        for (pi, p) in self.panels.iter().enumerate() {
            let pif = pi as f64;
            let n = p.dots.len() as f64;
            // ---- dots
            for (k, &i) in p.dots.iter().enumerate() {
                let kf = k as f64;
                let c: Rgb = if self.bpm > 0 {
                    // chase: one lit row sweeps up each panel per beat, panels offset
                    let row = libm::floor(((beat + pif / 3.0) % 1.0) * n);
                    if kf == row || kf == row - 1.0 {
                        DOT_COLORS[(k + pi) % DOT_COLORS.len()]
                    } else {
                        scale(DOT_COLORS[0], 0.06)
                    }
                } else if self.mode == 'S' {
                    // VU meter: level from amplitude, green -> amber -> red
                    let lit = js_round(amp * n);
                    if kf < lit {
                        if k < 4 {
                            [40, 255, 30]
                        } else if k < 6 {
                            [255, 110, 0]
                        } else {
                            [255, 20, 0]
                        }
                    } else {
                        OFF
                    }
                } else if self.mode == 'T' {
                    let st = self.dot_state[i];
                    let pos = libm::floor(t * 18.0) % (n * n_panels);
                    if pos == pif * n + kf {
                        [0, 200, 255]
                    } else {
                        scale(DOT_COLORS[st.color], if st.on { 0.15 } else { 0.0 })
                    }
                } else if self.mode == 'L' {
                    let row = libm::floor((t * 1.6) % 1.0 * (n + 2.0));
                    if kf <= row {
                        scale(
                            [0, 120, 255],
                            0.35 + 0.65 * if kf == row { 1.0 } else { 0.4 },
                        )
                    } else {
                        OFF
                    }
                } else {
                    // idle / engaged: indicator twinkle
                    let st = &mut self.dot_state[i];
                    if ms >= st.next {
                        st.on = self.rng.next_f64() < if engaged { 0.6 } else { 0.45 };
                        if self.rng.next_f64() < 0.2 {
                            st.color =
                                libm::floor(self.rng.next_f64() * DOT_COLORS.len() as f64) as usize;
                        }
                        st.next =
                            ms + 150.0 + self.rng.next_f64() * if engaged { 500.0 } else { 1100.0 };
                    }
                    if st.on {
                        DOT_COLORS[st.color]
                    } else {
                        OFF
                    }
                };
                self.pixels[i] = c;
            }
            // ---- windows
            for (k, &i) in p.windows.iter().enumerate() {
                let base = WIN_COLORS[(k + pi * 2) % WIN_COLORS.len()];
                let fi = i as f64;
                let level = if self.bpm > 0 {
                    let accent = if ((k + pi) % 2) as f64 == libm::floor(beat) % 2.0 {
                        1.0
                    } else {
                        0.4
                    };
                    0.35 + 0.65 * libm::exp(-beat_phase * 6.0) * accent
                } else if self.mode == 'S' {
                    0.35 + 0.65 * amp
                } else if self.mode == 'T' {
                    0.3 + 0.3 * libm::sin(t * 8.0 + fi)
                } else {
                    0.45 + 0.25 * libm::sin(t * (0.6 + 0.17 * k as f64) + fi * 1.7)
                };
                // A subsystem that is down blinks its window red, whatever the pattern.
                let bit = (pi * p.windows.len() + k) as u32;
                self.pixels[i] = if self.health & (1u32 << (bit & 31)) == 0 {
                    if libm::floor(ms / 250.0) % 2.0 != 0.0 {
                        scale([255, 20, 0], 0.9)
                    } else {
                        OFF
                    }
                } else {
                    scale(base, level)
                };
            }
        }
        if ms < self.sparkle_until {
            for p in &self.panels {
                for &i in &p.dots {
                    if self.rng.next_f64() < 0.55 {
                        self.pixels[i] = [40, 255, 30];
                    }
                }
            }
        }
    }
}

// ------------------------------------------------------------------ host side (offline)

/// Panel-major window order - same table as ChestLightControllerService.WINDOW_SUBSYSTEMS.
pub const WINDOW_SUBSYSTEMS: [(&str, &[&str]); 9] = [
    ("mic / speech-to-text", &["DeepgramDirectMicService"]),
    ("LLM", &["ClaudeService"]),
    ("text-to-speech", &["ElevenLabsService"]),
    (
        "intent routing",
        &["IntentRouterService", "JevIntentService"],
    ),
    ("music", &["MusicControllerService"]),
    ("memory", &["MemoryService"]),
    ("vision", &["VisionService"]),
    ("face LEDs", &["EyeLightControllerService"]),
    ("show control", &["BrainService", "TimelineExecutorService"]),
];
const CRITICAL: [&str; 2] = ["DeepgramDirectMicService", "ClaudeService"];

fn unhealthy(s: Option<&str>) -> bool {
    matches!(s, Some("error" | "degraded"))
}

/// Bit i set = window i's subsystem is healthy.
pub fn health_mask(statuses: &IndexMap<String, String>) -> u32 {
    let mut mask = 0;
    for (i, (_, services)) in WINDOW_SUBSYSTEMS.iter().enumerate() {
        if !services
            .iter()
            .any(|s| unhealthy(statuses.get(*s).map(String::as_str)))
        {
            mask |= 1 << i;
        }
    }
    mask
}

#[derive(Clone, Debug)]
struct Report {
    status: String,
    at: f64,
    latched: bool,
}

/// Port of CantinaOS ChestLightControllerService: turns bus events into chest commands,
/// queued for [`ChestHost::take_sent`].
#[derive(Clone, Debug)]
pub struct ChestHost {
    pub mode: String,
    pub target: &'static str,
    pub amplitude: f64,
    pub bpm: f64,
    pub dj_on: bool,
    pub sleeping: bool,
    pub booting: bool,
    /// Runtime errors count for this long unless repeated (services never report recovery).
    pub fault_hold_ms: f64,
    /// Set while the live link is delivering the real service's commands.
    pub muted: bool,
    pub default_bpm: f64,
    reports: IndexMap<String, Report>,
    now_ms: f64,
    booted_at: f64,
    sparkle: bool,
    last_amp: i64,
    sent: IndexMap<&'static str, String>,
    /// A show's chest.override: status output is paused until this time (ms), then resynced.
    override_until: f64,
    outbox: Vec<String>,
}

impl Default for ChestHost {
    fn default() -> Self {
        Self::new(120.0)
    }
}

impl ChestHost {
    pub fn new(default_bpm: f64) -> Self {
        ChestHost {
            mode: "STARTUP".into(),
            target: "SI",
            amplitude: 0.0,
            bpm: 0.0,
            dj_on: false,
            sleeping: false,
            booting: true,
            fault_hold_ms: 60000.0,
            muted: false,
            default_bpm,
            reports: IndexMap::new(),
            now_ms: 0.0,
            booted_at: -1.0,
            sparkle: false,
            last_amp: -1,
            sent: IndexMap::new(),
            override_until: 0.0,
            outbox: Vec::new(),
        }
    }

    /// Commands produced since the last call, in order.
    pub fn take_sent(&mut self) -> Vec<String> {
        std::mem::take(&mut self.outbox)
    }

    fn send(&mut self, cmd: String) {
        if !self.muted {
            self.outbox.push(cmd);
        }
    }

    fn send_if_changed(&mut self, key: &'static str, cmd: String) {
        if self.sent.get(key) != Some(&cmd) {
            self.sent.insert(key, cmd.clone());
            self.send(cmd);
        }
    }

    /// Re-send everything on the next tick (e.g. after live control is released).
    pub fn resync(&mut self) {
        self.sent.clear();
        self.last_amp = -1;
    }

    /// chest.override {command, hold}: send the word now; with hold > 0 s, pause the status
    /// output for that long and then return to status (resync).
    pub fn override_command(&mut self, command: &str, hold_s: f64, now_ms: f64) {
        if self.muted {
            return;
        }
        self.outbox.push(command.to_string());
        if hold_s > 0.0 {
            self.override_until = self.override_until.max(now_ms + hold_s * 1000.0);
        }
    }

    pub fn boot(&mut self, now_ms: f64) {
        self.booting = true;
        self.booted_at = now_ms;
    }

    fn interactive(&self) -> bool {
        self.mode == "INTERACTIVE"
    }

    pub fn set_mode(&mut self, m: &str) {
        self.mode = m.to_string();
        if m != "STARTUP" && self.booted_at < 0.0 {
            self.booting = false;
        }
        self.target = if m == "IDLE" || m == "STARTUP" {
            "SI"
        } else {
            "SE"
        };
    }

    pub fn listening_started(&mut self) {
        if self.interactive() {
            self.target = "SL";
        }
    }

    pub fn listening_stopped(&mut self) {
        if self.interactive() {
            self.target = "ST";
        }
    }

    pub fn llm_chunk(&mut self) {
        if self.interactive() && self.target == "ST" {
            self.target = "SS";
        }
    }

    pub fn speech_started(&mut self) {
        if !self.interactive() {
            return;
        }
        self.amplitude = 0.0;
        self.last_amp = -1;
        self.target = "SS";
    }

    pub fn amplitude_in(&mut self, a: f64) {
        if self.target == "SS" {
            self.amplitude = 0.3 * a + 0.7 * self.amplitude;
        }
    }

    pub fn speech_ended(&mut self) {
        if !self.interactive() || self.target != "SS" {
            return;
        }
        self.amplitude = 0.0;
        self.last_amp = 0;
        self.send("M000".into());
        self.sparkle = true;
        self.target = "SE";
    }

    /// `bpm` None = the default tempo.
    pub fn music(&mut self, playing: bool, bpm: Option<f64>) {
        let bpm = bpm.unwrap_or(self.default_bpm);
        self.bpm = if playing {
            bpm
        } else if self.dj_on {
            self.default_bpm
        } else {
            0.0
        };
    }

    pub fn dj(&mut self, on: bool, bpm: Option<f64>) {
        self.dj_on = on;
        self.bpm = if on {
            bpm.unwrap_or(self.default_bpm)
        } else {
            0.0
        };
    }

    /// The fault-latch rule (ChestLightControllerService): only a failure to start or
    /// initialise holds until the service reports again; runtime errors expire after the hold.
    pub fn latches(detail: Option<&str>) -> bool {
        detail.is_some_and(|m| m.starts_with("Failed to start") || m.starts_with("Failed to initialize"))
    }

    /// latched: a failure to start/initialise, or an offline toggle - holds until cleared.
    pub fn service_status(&mut self, service: &str, status: &str, latched: bool) {
        let r = Report {
            status: status.to_lowercase(),
            at: self.now_ms,
            latched,
        };
        self.reports.insert(service.to_string(), r);
    }

    pub fn statuses(&self) -> IndexMap<String, String> {
        self.reports
            .iter()
            .map(|(svc, r)| {
                let expired = unhealthy(Some(&r.status))
                    && !r.latched
                    && self.now_ms - r.at > self.fault_hold_ms;
                (
                    svc.clone(),
                    if expired {
                        "running".to_string()
                    } else {
                        r.status.clone()
                    },
                )
            })
            .collect()
    }

    pub fn system_state(&self) -> &'static str {
        if self.booting {
            return "X1";
        }
        let statuses = self.statuses();
        if CRITICAL
            .iter()
            .any(|s| statuses.get(*s).is_some_and(|v| v == "error"))
        {
            return "X3";
        }
        if self.sleeping {
            return "X2";
        }
        "X0"
    }

    pub fn tick(&mut self, now_ms: f64) {
        self.now_ms = now_ms;
        if self.override_until != 0.0 && !self.override_until.is_nan() {
            if now_ms < self.override_until {
                return;
            }
            self.override_until = 0.0;
            self.resync();
        }
        if self.booting && self.booted_at >= 0.0 && now_ms - self.booted_at > 3000.0 {
            self.booting = false;
            self.booted_at = -1.0;
        }
        self.send_if_changed("X", self.system_state().to_string());
        self.send_if_changed("H", format!("H{:03X}", health_mask(&self.statuses())));
        if self.sparkle {
            self.sparkle = false;
            self.send("SF".into());
        }
        self.send_if_changed("S", self.target.to_string());
        let bpm = js_round(self.bpm).clamp(0.0, 999.0) as i64;
        self.send_if_changed("B", format!("B{bpm:03}"));
        if self.target == "SS" {
            let level = libm::trunc(self.amplitude.clamp(0.0, 1.0) * 255.0) as i64;
            if level != self.last_amp {
                self.last_amp = level;
                self.send(format!("M{level:03}"));
            }
        }
    }
}

//! Emulator for `firmware/grnwave_nano`: our sketch for the grnwave workshop DJ Rex Full LED
//! Set (3 body boards, eye board, mouth board) on an Arduino Nano. The counterpart of
//! `firmware.rs` (face) and `chest.rs` (chest): it takes the same named commands as those two
//! boards together, so the runtime drives a grnwave build unchanged.
//!
//! Chain (grnwave's sample sketch `DJLEDNanoV2.ino`, FastLED on D4): body board A, B, C at
//! 0-31, 32-63, 64-95 - per board 8 small LEDs (light pipes in the panel's LED holes, bottom
//! to top), then 6 groups of 4 x 5050 - and the two eye LEDs at 96-97. The mouth board is on
//! its own line (D5, our choice). Each panel shows 3 of a board's 6 groups through its
//! windows ([`EXPOSED`]); the others stay dark.
//!
//! The sketch and this file are kept in step effect for effect (same names, same
//! parameters); randomness differs (Arduino `random` vs [`Rng`]), timing and levels do not.
//! Pixel values are before `FastLED.setBrightness` ([`BRIGHTNESS`]), like the face emulator's.
//!
//! Windows are units: with an opal diffuser over each (the package's `lights.body.windows`,
//! `DIFFUSED` in the sketch's config.h) a window is one colour, so window effects write one
//! colour per window ([`GrnwaveFirmware::win`], board-major = the health bits) and
//! [`present_windows`] puts it on the window's 4 LEDs. Bare windows ([`GrnwaveFirmware::diffused`]
//! false) keep the per-pixel path: the look's `blocks` effect varies the LEDs inside a window.
//!
//! Words (115200 baud, newline-terminated; lines up to 15 chars):
//! `SI SE SL ST SS` state -> `+`; `SF` flash -> `+`; `Mnnn` amplitude; `Bnnn` tempo;
//! `Xn` system state -> `+`; `Hxxx` health mask; `R` reset (silent, so the face driver's
//! `R` probe cannot mistake it); `?` -> `Grnwave: ...`. Boots printing `GRNWAVE READY`.

use std::f64::consts::PI;

use super::firmware::{is_js_space, Rgb};
use crate::rng::Rng;

pub const BODY_BOARDS: usize = 3;
pub const SMALL_PER_BOARD: usize = 8;
pub const GROUPS_PER_BOARD: usize = 6;
pub const LEDS_PER_GROUP: usize = 4;
pub const BOARD_LEDS: usize = SMALL_PER_BOARD + GROUPS_PER_BOARD * LEDS_PER_GROUP;
pub const BODY_LEDS: usize = BODY_BOARDS * BOARD_LEDS;
pub const EYE_LEDS: usize = 2;
/// Body + eyes on the D4 line.
pub const MAIN_LEDS: usize = BODY_LEDS + EYE_LEDS;
pub const MOUTH_LEDS: usize = 8;
/// Which of a board's 6 groups sit behind that panel's 3 windows (the groups the sample
/// sketch animates: A 12/24/28, B 40/44/48, C 84/88/92).
pub const EXPOSED: [[usize; 3]; BODY_BOARDS] = [[1, 4, 5], [0, 1, 2], [3, 4, 5]];
pub const WINDOWS: usize = BODY_BOARDS * 3;
/// `FastLED.setBrightness` on the board.
pub const BRIGHTNESS: u8 = 128;
pub const READY: &str = "GRNWAVE READY";
pub const IDENTITY: &str = "Grnwave: body 96 eyes 2 mouth 8 (named_v1)";

/// Chest-stream words the grnwave board takes (`B`, `X`, `H`). `S*`, `SF` and `Mnnn` come
/// from the face stream only, so a board fed both streams does not see them twice.
pub fn from_chest_stream(line: &str) -> bool {
    matches!(line.as_bytes().first(), Some(b'B' | b'X' | b'H'))
}

// ------------------------------------------------------------------ palette (effects.h)

const OFF: Rgb = [0, 0, 0];
/// Small indicator LEDs: grnwave's dim red / white / blue.
const SMALL_COLORS: [Rgb; 3] = [[80, 0, 0], [96, 136, 136], [0, 0, 85]];
/// 5050 window blocks: red, white, gold, blue.
const BLOCK_COLORS: [Rgb; 4] = [[255, 0, 0], [255, 255, 255], [255, 221, 136], [0, 0, 255]];
const GOLD: Rgb = [255, 221, 136];
const ENGAGED_BLUE: Rgb = [100, 180, 255];
const LISTEN_BLUE: Rgb = [0, 120, 255];
const THINK_CYAN: Rgb = [0, 255, 255];
const FLASH_GREEN: Rgb = [0, 255, 0];
const VU_GREEN: Rgb = [40, 255, 30];
const VU_AMBER: Rgb = [255, 110, 0];
const ALERT_RED: Rgb = [255, 20, 0];
const MOUTH_BLUE: Rgb = [0, 50, 150];
const MOUTH_ORANGE: Rgb = [255, 60, 0];
const MOUTH_SPEAK: Rgb = [255, 120, 20];

/// `c` scaled by `k` in 0..1 (FastLED `nscale8`, rounded down).
fn scale(c: Rgb, k: f64) -> Rgb {
    let s = (k.clamp(0.0, 1.0) * 255.0) as u32;
    c.map(|v| ((u32::from(v) * (s + 1)) >> 8) as u8)
}

fn frac(x: f64) -> f64 {
    x - libm::floor(x)
}

/// What every effect reads (the sketch's `Ctx`).
#[derive(Clone, Copy, Debug)]
pub struct Ctx {
    pub ms: f64,
    /// sqrt(amplitude / 255): perceptual level 0..1.
    pub level: f64,
    pub bpm: u16,
    /// Beats since t = 0 (0 without music).
    pub beat: f64,
}

#[derive(Clone, Copy, Debug, Default)]
struct Twinkle {
    on: bool,
    color: usize,
    next: f64,
}

#[derive(Clone, Copy, Debug, Default)]
struct Flicker {
    level: f64,
    target: f64,
    next: f64,
}

#[derive(Clone, Copy, Debug, Default)]
struct Blink {
    color: usize,
    on: bool,
    next: f64,
}

pub type Effect = fn(&mut GrnwaveFirmware, &Ctx);

/// The per-state look: one effect per zone (the sketch's `LOOKS` table in `effects.cpp`).
#[derive(Clone, Copy)]
pub struct Look {
    pub mode: u8,
    pub dots: Effect,
    /// Windows as units: writes [`GrnwaveFirmware::win`].
    pub windows: Effect,
    /// Per pixel inside each window, bare windows only; `None` = the window's colour flat.
    pub blocks: Option<Effect>,
    pub eyes: Effect,
    pub mouth: Effect,
}

pub const LOOKS: [Look; 5] = [
    Look { mode: b'I', dots: fx_twinkle, windows: fx_win_blink, blocks: None, eyes: fx_eyes_flicker, mouth: fx_mouth_glow },
    Look { mode: b'E', dots: fx_twinkle, windows: fx_win_blink, blocks: None, eyes: fx_eyes_solid, mouth: fx_mouth_glow },
    Look { mode: b'L', dots: fx_fill, windows: fx_win_breathe, blocks: None, eyes: fx_eyes_pulse, mouth: fx_mouth_glow },
    Look { mode: b'T', dots: fx_scan, windows: fx_win_think, blocks: Some(fx_blk_spin), eyes: fx_eyes_alternate, mouth: fx_mouth_pulse },
    Look { mode: b'S', dots: fx_vu, windows: fx_win_level, blocks: None, eyes: fx_eyes_speak, mouth: fx_mouth_vu },
];

#[derive(Clone, Debug)]
pub struct GrnwaveFirmware {
    /// D4 line: body 0-95, eyes 96-97.
    pub main: Vec<Rgb>,
    /// D5 line.
    pub mouth: [Rgb; MOUTH_LEDS],
    /// One colour per window, board-major (A0 A1 A2 B0 ... = the health bits).
    pub win: [Rgb; WINDOWS],
    /// Diffusers over the windows (the sketch's `DIFFUSED`, default on): windows are shown
    /// flat. Off, the look's `blocks` effect adds per-pixel detail.
    pub diffused: bool,
    /// One of I E L T S.
    pub mode: u8,
    pub amplitude: u8,
    pub bpm: u16,
    /// 0 normal, 1 boot sweep (until told otherwise), 2 sleep, 3 fault.
    pub sys_state: u8,
    /// Bit i = window i (board-major) healthy.
    pub health: u16,
    pub now: f64,
    flash_until: f64,
    boot_start: f64,
    twinkle: [[Twinkle; SMALL_PER_BOARD]; BODY_BOARDS],
    blink: [Blink; WINDOWS],
    flicker: [Flicker; EYE_LEDS],
    rx: String,
    tx: Vec<String>,
    rng: Rng,
}

impl GrnwaveFirmware {
    pub fn new(rng: Rng) -> Self {
        GrnwaveFirmware {
            main: vec![OFF; MAIN_LEDS],
            mouth: [OFF; MOUTH_LEDS],
            win: [OFF; WINDOWS],
            diffused: true,
            mode: b'I',
            amplitude: 0,
            bpm: 0,
            sys_state: 1,
            health: 0x1ff,
            now: 0.0,
            flash_until: 0.0,
            boot_start: 0.0,
            twinkle: [[Twinkle::default(); SMALL_PER_BOARD]; BODY_BOARDS],
            blink: [Blink::default(); WINDOWS],
            flicker: [Flicker { level: 0.8, target: 0.8, next: 0.0 }; EYE_LEDS],
            rx: String::new(),
            tx: vec![READY.to_string()],
            rng,
        }
    }

    /// Bare windows (no diffuser): the per-pixel `blocks` effects run.
    pub fn with_diffusers(mut self, on: bool) -> Self {
        self.diffused = on;
        self
    }

    pub fn body(&self) -> &[Rgb] {
        &self.main[..BODY_LEDS]
    }

    pub fn eyes(&self) -> &[Rgb] {
        &self.main[BODY_LEDS..]
    }

    /// Chain index of body board `b`'s small LED `k` (bottom = 0).
    pub fn small(b: usize, k: usize) -> usize {
        b * BOARD_LEDS + k
    }

    /// Chain index of the first LED of board `b`'s group `g`.
    pub fn group(b: usize, g: usize) -> usize {
        b * BOARD_LEDS + SMALL_PER_BOARD + g * LEDS_PER_GROUP
    }

    /// Chain index of the first LED behind window `k` (0-2) of board `b`.
    pub fn window(b: usize, k: usize) -> usize {
        Self::group(b, EXPOSED[b][k])
    }

    /// Lines the board printed since the last call.
    pub fn read_lines(&mut self) -> Vec<String> {
        std::mem::take(&mut self.tx)
    }

    pub fn write(&mut self, data: &str) {
        for c in data.chars() {
            if c == '\n' || c == '\r' {
                let cmd = std::mem::take(&mut self.rx);
                self.command(cmd.trim_matches(is_js_space));
            } else if self.rx.len() < 15 {
                self.rx.push(c);
            }
        }
    }

    fn ack(&mut self) {
        self.tx.push("+".into());
    }

    fn command(&mut self, cmd: &str) {
        let b = cmd.as_bytes();
        let digits = |s: &[u8]| s.len() == 3 && s.iter().all(u8::is_ascii_digit);
        let num = |s: &[u8]| s.iter().fold(0u32, |n, d| n * 10 + u32::from(d - b'0'));
        match b {
            [b'S', m @ (b'I' | b'E' | b'L' | b'T' | b'S')] => {
                self.mode = *m;
                self.ack();
            }
            b"SF" => {
                self.flash_until = self.now + FLASH_MS;
                self.mode = b'E';
                self.ack();
            }
            [b'M', d @ ..] if digits(d) => self.amplitude = num(d).min(255) as u8,
            [b'B', d @ ..] if digits(d) => self.bpm = num(d) as u16,
            [b'X', s @ b'0'..=b'3'] => {
                let s = s - b'0';
                if s == 1 && self.sys_state != 1 {
                    self.boot_start = self.now;
                }
                self.sys_state = s;
                self.ack();
            }
            [b'H', h @ ..] if h.len() == 3 && h.iter().all(u8::is_ascii_hexdigit) => {
                let s = std::str::from_utf8(h).expect("ascii");
                self.health = u16::from_str_radix(s, 16).expect("hex") & 0x1ff;
            }
            b"R" => {
                self.mode = b'I';
                self.amplitude = 0;
                self.bpm = 0;
                self.sys_state = 0;
                self.health = 0x1ff;
                self.flash_until = 0.0;
            }
            b"?" => self.tx.push(IDENTITY.into()),
            b"" => {}
            [b'S', ..] | [b'X', ..] => self.tx.push("-".into()),
            _ => {}
        }
    }

    /// Advance to `ms` and recompute every pixel (the sketch's 50 Hz frame).
    pub fn update(&mut self, ms: f64) {
        self.now = ms;
        let beat = if self.bpm > 0 { ms / 1000.0 * f64::from(self.bpm) / 60.0 } else { 0.0 };
        let ctx = Ctx {
            ms,
            level: libm::sqrt(f64::from(self.amplitude) / 255.0),
            bpm: self.bpm,
            beat,
        };
        if self.sys_state == 1 && ms - self.boot_start > BOOT_TIMEOUT_MS {
            self.sys_state = 0;
        }
        match self.sys_state {
            1 => return fx_boot(self, &ctx),
            2 => return fx_sleep(self, &ctx),
            3 => return fx_fault(self, &ctx),
            _ => {}
        }
        let look = LOOKS.iter().find(|l| l.mode == self.mode).copied().unwrap_or(LOOKS[0]);
        if self.bpm > 0 {
            fx_beat_chase(self, &ctx);
            fx_win_beat(self, &ctx);
        } else {
            (look.dots)(self, &ctx);
            (look.windows)(self, &ctx);
        }
        fx_health(self, &ctx);
        present_windows(self, &ctx, if self.bpm > 0 { None } else { look.blocks });
        fx_hidden_off(self, &ctx);
        if ms < self.flash_until {
            fx_flash(self, &ctx);
        } else {
            (look.eyes)(self, &ctx);
        }
        (look.mouth)(self, &ctx);
    }
}

pub const FLASH_MS: f64 = 350.0;
pub const BOOT_TIMEOUT_MS: f64 = 60000.0;

/// A window is a unit: its colour, shown on all its LEDs by [`present_windows`].
fn set_window(fw: &mut GrnwaveFirmware, b: usize, k: usize, c: Rgb) {
    fw.win[b * 3 + k] = c;
}

/// Each window's colour on its 4 LEDs; bare windows then run `blocks` (per pixel).
pub fn present_windows(fw: &mut GrnwaveFirmware, c: &Ctx, blocks: Option<Effect>) {
    for b in 0..BODY_BOARDS {
        for k in 0..3 {
            let i = GrnwaveFirmware::window(b, k);
            fw.main[i..i + LEDS_PER_GROUP].fill(fw.win[b * 3 + k]);
        }
    }
    if let (false, Some(f)) = (fw.diffused, blocks) {
        f(fw, c);
    }
}

fn window_base(b: usize, k: usize) -> Rgb {
    BLOCK_COLORS[(k + b) % BLOCK_COLORS.len()]
}

// ------------------------------------------------------------------ body dots

/// fx_twinkle: each small LED blinks on and off at random in grnwave's dim palette.
/// Params: on 45 % (engaged 60 %), hold 150 + rand(1100) ms (engaged 500).
pub fn fx_twinkle(fw: &mut GrnwaveFirmware, c: &Ctx) {
    let engaged = fw.mode == b'E';
    for b in 0..BODY_BOARDS {
        for k in 0..SMALL_PER_BOARD {
            let st = &mut fw.twinkle[b][k];
            if c.ms >= st.next {
                st.on = fw.rng.next_f64() < if engaged { 0.6 } else { 0.45 };
                st.color = (fw.rng.next_f64() * SMALL_COLORS.len() as f64) as usize % SMALL_COLORS.len();
                st.next = c.ms + 150.0 + fw.rng.next_f64() * if engaged { 500.0 } else { 1100.0 };
            }
            let st = fw.twinkle[b][k];
            fw.main[GrnwaveFirmware::small(b, k)] = if st.on { SMALL_COLORS[st.color] } else { OFF };
        }
    }
}

/// fx_fill: every bar fills bottom to top in blue, 1.6 sweeps per second.
pub fn fx_fill(fw: &mut GrnwaveFirmware, c: &Ctx) {
    let n = SMALL_PER_BOARD as f64;
    let row = libm::floor(frac(c.ms / 1000.0 * 1.6) * (n + 2.0));
    for b in 0..BODY_BOARDS {
        for k in 0..SMALL_PER_BOARD {
            let kf = k as f64;
            fw.main[GrnwaveFirmware::small(b, k)] = if kf <= row {
                scale(LISTEN_BLUE, if kf == row { 1.0 } else { 0.6 })
            } else {
                OFF
            };
        }
    }
}

/// fx_scan: one cyan dot runs up board A, B, C in turn, 18 LEDs a second.
pub fn fx_scan(fw: &mut GrnwaveFirmware, c: &Ctx) {
    let total = (BODY_BOARDS * SMALL_PER_BOARD) as f64;
    let pos = libm::floor(c.ms / 1000.0 * 18.0) % total;
    for b in 0..BODY_BOARDS {
        for k in 0..SMALL_PER_BOARD {
            let i = (b * SMALL_PER_BOARD + k) as f64;
            fw.main[GrnwaveFirmware::small(b, k)] = if i == pos {
                THINK_CYAN
            } else if i == (pos + total - 1.0) % total {
                scale(THINK_CYAN, 0.25)
            } else {
                OFF
            };
        }
    }
}

/// fx_vu: each bar is a VU meter of the speech level: green 0-3, amber 4-5, red 6-7.
pub fn fx_vu(fw: &mut GrnwaveFirmware, c: &Ctx) {
    let lit = libm::round(c.level * SMALL_PER_BOARD as f64) as usize;
    for b in 0..BODY_BOARDS {
        for k in 0..SMALL_PER_BOARD {
            fw.main[GrnwaveFirmware::small(b, k)] = if k < lit {
                match k {
                    0..=3 => VU_GREEN,
                    4..=5 => VU_AMBER,
                    _ => ALERT_RED,
                }
            } else {
                OFF
            };
        }
    }
}

/// fx_beat_chase: a two-LED bar climbs each board once per beat, boards a third apart.
pub fn fx_beat_chase(fw: &mut GrnwaveFirmware, c: &Ctx) {
    let n = SMALL_PER_BOARD as f64;
    for b in 0..BODY_BOARDS {
        let row = libm::floor(frac(c.beat + b as f64 / 3.0) * n);
        for k in 0..SMALL_PER_BOARD {
            let kf = k as f64;
            fw.main[GrnwaveFirmware::small(b, k)] = if kf == row || kf == row - 1.0 {
                BLOCK_COLORS[(k + b) % BLOCK_COLORS.len()]
            } else {
                scale(SMALL_COLORS[0], 0.3)
            };
        }
    }
}

// ------------------------------------------------------------------ windows (5050 blocks)

/// fx_win_blink: the droid's blinking blocks. Each window holds a block colour (on 75 %, at
/// 0.8) or goes dark for 300 + rand(1400) ms (engaged 150 + rand(600)), then jumps to
/// another colour - never the one it had.
pub fn fx_win_blink(fw: &mut GrnwaveFirmware, c: &Ctx) {
    let engaged = fw.mode == b'E';
    for i in 0..WINDOWS {
        let s = &mut fw.blink[i];
        if c.ms >= s.next {
            s.on = fw.rng.next_f64() < 0.75;
            s.color = (s.color + 1 + (fw.rng.next_f64() * 3.0) as usize) % BLOCK_COLORS.len();
            s.next = c.ms
                + if engaged { 150.0 + fw.rng.next_f64() * 600.0 } else { 300.0 + fw.rng.next_f64() * 1400.0 };
        }
        let s = fw.blink[i];
        fw.win[i] = if s.on { scale(BLOCK_COLORS[s.color], 0.8) } else { OFF };
    }
}

/// fx_win_breathe: each window breathes slowly in its own colour, 0.2-0.7.
pub fn fx_win_breathe(fw: &mut GrnwaveFirmware, c: &Ctx) {
    let t = c.ms / 1000.0;
    for b in 0..BODY_BOARDS {
        for k in 0..3 {
            let i = (b * 3 + k) as f64;
            let level = 0.45 + 0.25 * libm::sin(t * (0.6 + 0.17 * k as f64) + i * 1.7);
            set_window(fw, b, k, scale(window_base(b, k), level));
        }
    }
}

/// fx_win_think: a cyan "working" block steps through the 9 windows, board by board, 6 a
/// second, with a 0.35 trail; the rest hold their colour at 0.12.
pub fn fx_win_think(fw: &mut GrnwaveFirmware, c: &Ctx) {
    let pos = (libm::floor(c.ms / 1000.0 * 6.0) as u64 % WINDOWS as u64) as usize;
    for b in 0..BODY_BOARDS {
        for k in 0..3 {
            let i = b * 3 + k;
            let col = if i == pos {
                THINK_CYAN
            } else if i == (pos + WINDOWS - 1) % WINDOWS {
                scale(THINK_CYAN, 0.35)
            } else {
                scale(window_base(b, k), 0.12)
            };
            set_window(fw, b, k, col);
        }
    }
}

/// Speech level at which a panel's window k lights (fx_win_level).
pub const LEVEL_THRESH: [f64; 3] = [0.2, 0.45, 0.7];

/// fx_win_level: each panel's 3 windows are a VU meter: window k turns the speaking orange
/// (the mouth's) at 0.5 + 0.5 x the level once the level passes [`LEVEL_THRESH`]`[k]`, else its own colour at 0.15.
pub fn fx_win_level(fw: &mut GrnwaveFirmware, c: &Ctx) {
    for b in 0..BODY_BOARDS {
        for (k, &th) in LEVEL_THRESH.iter().enumerate() {
            let col = if c.level >= th { scale(MOUTH_SPEAK, 0.5 + 0.5 * c.level) } else { scale(window_base(b, k), 0.15) };
            set_window(fw, b, k, col);
        }
    }
}

/// Colour index of window `i` on beat `n`: changes every beat (step 1-3, never 0 mod 4).
pub fn beat_color(i: usize, n: u64) -> usize {
    ((i as u64 + n * (1 + i as u64 % 3)) % BLOCK_COLORS.len() as u64) as usize
}

/// fx_win_beat: on every beat each window changes colour ([`beat_color`]) and flashes,
/// decaying e^-6 per beat; alternate windows accented (1.0 / 0.4) on alternate beats.
pub fn fx_win_beat(fw: &mut GrnwaveFirmware, c: &Ctx) {
    let phase = frac(c.beat);
    let n = libm::floor(c.beat) as u64;
    for b in 0..BODY_BOARDS {
        for k in 0..3 {
            let accent = if ((k + b) % 2) as u64 == n % 2 { 1.0 } else { 0.4 };
            let level = 0.35 + 0.65 * libm::exp(-phase * 6.0) * accent;
            set_window(fw, b, k, scale(BLOCK_COLORS[beat_color(b * 3 + k, n)], level));
        }
    }
}

/// fx_blk_spin (bare windows only): inside each window one 5050 at the window's colour,
/// the rest at 0.3, the bright one circling the 2 x 2 at 8 steps a second.
pub fn fx_blk_spin(fw: &mut GrnwaveFirmware, c: &Ctx) {
    const ROUND: [usize; 4] = [0, 1, 3, 2];
    let lit = ROUND[(libm::floor(c.ms / 1000.0 * 8.0) as u64 % 4) as usize];
    for b in 0..BODY_BOARDS {
        for k in 0..3 {
            let i = GrnwaveFirmware::window(b, k);
            let col = fw.win[b * 3 + k];
            for j in (0..LEDS_PER_GROUP).filter(|&j| j != lit) {
                fw.main[i + j] = scale(col, 0.3);
            }
        }
    }
}

/// fx_hidden_off: groups not behind a window stay dark (power).
pub fn fx_hidden_off(fw: &mut GrnwaveFirmware, _c: &Ctx) {
    for (b, exposed) in EXPOSED.iter().enumerate() {
        for g in (0..GROUPS_PER_BOARD).filter(|g| !exposed.contains(g)) {
            let i = GrnwaveFirmware::group(b, g);
            fw.main[i..i + LEDS_PER_GROUP].fill(OFF);
        }
    }
}

/// fx_health: a window whose subsystem is down blinks red at 2 Hz, whatever the look.
pub fn fx_health(fw: &mut GrnwaveFirmware, c: &Ctx) {
    let on = libm::floor(c.ms / 250.0) % 2.0 != 0.0;
    for b in 0..BODY_BOARDS {
        for k in 0..3 {
            if fw.health & (1 << (b * 3 + k)) == 0 {
                set_window(fw, b, k, if on { scale(ALERT_RED, 0.9) } else { OFF });
            }
        }
    }
}

// ------------------------------------------------------------------ eyes

fn set_eyes(fw: &mut GrnwaveFirmware, l: Rgb, r: Rgb) {
    fw.main[BODY_LEDS] = l;
    fw.main[BODY_LEDS + 1] = r;
}

/// fx_eyes_flicker: gold, each eye drifting between 0.4 and 1 on its own (grnwave's look).
pub fn fx_eyes_flicker(fw: &mut GrnwaveFirmware, c: &Ctx) {
    let mut px = [OFF; EYE_LEDS];
    for (e, p) in px.iter_mut().enumerate() {
        let f = &mut fw.flicker[e];
        if c.ms >= f.next {
            f.target = 0.4 + 0.6 * fw.rng.next_f64();
            f.next = c.ms + 200.0 + fw.rng.next_f64() * 1400.0;
        }
        f.level += (f.target - f.level) * 0.08;
        *p = scale(GOLD, f.level);
    }
    set_eyes(fw, px[0], px[1]);
}

/// fx_eyes_solid: engaged blue.
pub fn fx_eyes_solid(fw: &mut GrnwaveFirmware, _c: &Ctx) {
    set_eyes(fw, ENGAGED_BLUE, ENGAGED_BLUE);
}

/// fx_eyes_pulse: listening blue, pulsing 0.5-1 at 1.5 Hz.
pub fn fx_eyes_pulse(fw: &mut GrnwaveFirmware, c: &Ctx) {
    let k = 0.75 + 0.25 * libm::sin(c.ms / 1000.0 * 2.0 * PI * 1.5);
    let px = scale(LISTEN_BLUE, k);
    set_eyes(fw, px, px);
}

/// fx_eyes_alternate: thinking - cyan hops left/right at 4 Hz.
pub fn fx_eyes_alternate(fw: &mut GrnwaveFirmware, c: &Ctx) {
    let left = libm::floor(c.ms / 250.0) % 2.0 == 0.0;
    let dim = scale(THINK_CYAN, 0.15);
    if left {
        set_eyes(fw, THINK_CYAN, dim);
    } else {
        set_eyes(fw, dim, THINK_CYAN);
    }
}

/// fx_eyes_speak: gold, 0.6 + 0.4 x the speech level.
pub fn fx_eyes_speak(fw: &mut GrnwaveFirmware, c: &Ctx) {
    let px = scale(GOLD, 0.6 + 0.4 * c.level);
    set_eyes(fw, px, px);
}

/// fx_flash: green "done" flash (SF), eyes and a sparkle on the body dots.
pub fn fx_flash(fw: &mut GrnwaveFirmware, _c: &Ctx) {
    set_eyes(fw, FLASH_GREEN, FLASH_GREEN);
    for b in 0..BODY_BOARDS {
        for k in 0..SMALL_PER_BOARD {
            if fw.rng.next_f64() < 0.55 {
                fw.main[GrnwaveFirmware::small(b, k)] = VU_GREEN;
            }
        }
    }
}

// ------------------------------------------------------------------ mouth

/// Mouth V: 0-3 the viewer-left arm top -> tip, 4-7 the right arm tip -> top. Distance from
/// the tip (0 at the tip).
pub fn mouth_rank(i: usize) -> usize {
    if i < 4 {
        3 - i
    } else {
        i - 4
    }
}

/// fx_mouth_glow: the tip glows (blue idle / listening, orange engaged).
pub fn fx_mouth_glow(fw: &mut GrnwaveFirmware, _c: &Ctx) {
    let c = if fw.mode == b'E' { scale(MOUTH_ORANGE, 0.5) } else { MOUTH_BLUE };
    for i in 0..MOUTH_LEDS {
        fw.mouth[i] = if mouth_rank(i) == 0 { c } else { OFF };
    }
}

/// fx_mouth_pulse: thinking - the whole V pulses dim blue at 1 Hz.
pub fn fx_mouth_pulse(fw: &mut GrnwaveFirmware, c: &Ctx) {
    let k = 0.15 + 0.25 * (0.5 + 0.5 * libm::sin(c.ms / 1000.0 * 2.0 * PI));
    fw.mouth = [scale(MOUTH_BLUE, k); MOUTH_LEDS];
}

/// fx_mouth_vu: speaking - lights spread from the tip up both arms with the level.
pub fn fx_mouth_vu(fw: &mut GrnwaveFirmware, c: &Ctx) {
    let lit = libm::round(c.level * 4.0) as usize;
    for i in 0..MOUTH_LEDS {
        let r = mouth_rank(i);
        fw.mouth[i] = if r < lit {
            MOUTH_SPEAK
        } else if r == 0 {
            scale(MOUTH_SPEAK, 0.2)
        } else {
            OFF
        };
    }
}

// ------------------------------------------------------------------ system states

/// fx_boot: boards fill blue one after another, then their windows light; 2.4 s a sweep.
pub fn fx_boot(fw: &mut GrnwaveFirmware, c: &Ctx) {
    let cycle = frac((c.ms - fw.boot_start) / 2400.0) * BODY_BOARDS as f64 * 1.25;
    for b in 0..BODY_BOARDS {
        let f = (cycle - b as f64).clamp(0.0, 1.25);
        for k in 0..SMALL_PER_BOARD {
            fw.main[GrnwaveFirmware::small(b, k)] =
                if f * SMALL_PER_BOARD as f64 > k as f64 { LISTEN_BLUE } else { OFF };
        }
        for k in 0..3 {
            set_window(fw, b, k, if f >= 1.0 { scale(window_base(b, k), 0.6) } else { OFF });
        }
    }
    present_windows(fw, c, None);
    fx_hidden_off(fw, c);
    let px = scale(LISTEN_BLUE, 0.3);
    set_eyes(fw, px, px);
    fw.mouth = [OFF; MOUTH_LEDS];
}

/// fx_sleep: windows barely breathe, one green dot every third second, eyes a faint gold.
pub fn fx_sleep(fw: &mut GrnwaveFirmware, c: &Ctx) {
    let t = c.ms / 1000.0;
    let level = 0.03 + 0.05 * (0.5 + 0.5 * libm::sin(t * 0.8));
    for b in 0..BODY_BOARDS {
        for k in 0..SMALL_PER_BOARD {
            fw.main[GrnwaveFirmware::small(b, k)] = OFF;
        }
        for k in 0..3 {
            set_window(fw, b, k, scale(window_base(b, k), level));
        }
    }
    if libm::floor(t) % 3.0 == 0.0 {
        fw.main[GrnwaveFirmware::small(0, 0)] = scale(VU_GREEN, 0.4);
    }
    present_windows(fw, c, None);
    fx_hidden_off(fw, c);
    let px = scale(GOLD, 0.08);
    set_eyes(fw, px, px);
    fw.mouth = [OFF; MOUTH_LEDS];
}

/// fx_fault: every window pulses red, a red dot runs up every bar, eyes red.
pub fn fx_fault(fw: &mut GrnwaveFirmware, c: &Ctx) {
    let t = c.ms / 1000.0;
    let pulse = 0.25 + 0.75 * (0.5 + 0.5 * libm::sin(t * PI * 2.0));
    let pos = (libm::floor(t * 6.0) % SMALL_PER_BOARD as f64) as usize;
    for b in 0..BODY_BOARDS {
        for k in 0..SMALL_PER_BOARD {
            fw.main[GrnwaveFirmware::small(b, k)] = if k == pos { scale(ALERT_RED, 0.6) } else { OFF };
        }
        for k in 0..3 {
            set_window(fw, b, k, scale(ALERT_RED, pulse));
        }
    }
    present_windows(fw, c, None);
    fx_hidden_off(fw, c);
    let px = scale(ALERT_RED, pulse);
    set_eyes(fw, px, px);
    fw.mouth = [OFF; MOUTH_LEDS];
}

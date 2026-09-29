//! Port of `sim/web/src/firmware.ts`: the emulator for
//! `cantina_os/arduino/rex_face_v3_clean/rex_face_v3_clean.ino`. Same serial parser, state
//! machine, timers and FastLED 8-bit arithmetic; `advance_to(ms)` runs `loop()` on a 1 ms
//! simulated clock (10 ms per iteration while a FLASH runs - the sketch's `delay(10)`).
//!
//! Reproduces the THINKING index bug: the right eye's second dot collapses onto the first and
//! step 0 writes `eyeLeds[14]`, one past the end. Here that write is bounds-checked; with
//! `oob_aliases_mouth` (default) it lands in `mouth_leds[0]`, as avr-gcc's layout would.

use std::collections::VecDeque;
use std::f64::consts::PI;

use crate::rng::Rng;

pub type Rgb = [u8; 3];

pub const NUM_EYE_LEDS: usize = 14;
pub const LEDS_PER_EYE: usize = 7;
pub const LEFT_EYE_START: usize = 0;
pub const RIGHT_EYE_START: usize = 7;
pub const NUM_MOUTH_LEDS: usize = 8;
/// FastLED.setBrightness(128) - applied at show() time.
pub const OUTPUT_BRIGHTNESS: u8 = 128;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum State {
    Idle,
    Engaged,
    Listening,
    Thinking,
    Speaking,
}

impl State {
    pub fn as_str(self) -> &'static str {
        match self {
            State::Idle => "IDLE",
            State::Engaged => "ENGAGED",
            State::Listening => "LISTENING",
            State::Thinking => "THINKING",
            State::Speaking => "SPEAKING",
        }
    }
}

const COLOR_IDLE_EYES_OUTER: Rgb = [255, 60, 0];
const COLOR_IDLE_EYES_CENTER: Rgb = [255, 180, 80];
const COLOR_IDLE_MOUTH: Rgb = [0, 50, 150];
const COLOR_ENGAGED_EYES: Rgb = [0, 35, 110];
const COLOR_ENGAGED_CENTER: Rgb = [100, 180, 255];
const COLOR_ENGAGED_MOUTH: Rgb = [255, 60, 0];
const COLOR_THINKING_DOT: Rgb = [0, 255, 255];
const COLOR_FLASH: Rgb = [0, 255, 0];
const BLACK: Rgb = [0, 0, 0];

const TWO_PI: f64 = PI * 2.0;

/// C float -> uint8_t conversion (truncate, wrap like the implicit cast).
fn u8_of(v: f64) -> u8 {
    (libm::trunc(v) as i64 & 0xff) as u8
}

/// FastLED scale8 with FASTLED_SCALE8_FIXED: (i * (1 + scale)) >> 8.
pub fn scale8(i: u8, scale: u8) -> u8 {
    ((u32::from(i) * (1 + u32::from(scale))) >> 8) as u8
}

/// CRGB::nscale8(uint8_t) on a copy.
fn nscale8(c: Rgb, scale: f64) -> Rgb {
    let s = u8_of(scale);
    [scale8(c[0], s), scale8(c[1], s), scale8(c[2], s)]
}

/// JS `parseInt(s, 10)`: optional whitespace and sign, then leading digits; `None` = NaN.
pub(crate) fn js_parse_int(s: &str) -> Option<i64> {
    let s = s.trim_start_matches(is_js_space);
    let (neg, s) = match s.as_bytes().first() {
        Some(b'-') => (true, &s[1..]),
        Some(b'+') => (false, &s[1..]),
        _ => (false, s),
    };
    let digits = s.bytes().take_while(u8::is_ascii_digit).count();
    if digits == 0 {
        return None;
    }
    let v: i64 = s[..digits].parse().ok()?;
    Some(if neg { -v } else { v })
}

/// JS WhiteSpace + LineTerminator (what `trim()` and `parseInt` skip).
pub(crate) fn is_js_space(c: char) -> bool {
    (c.is_whitespace() && c != '\u{85}') || c == '\u{feff}'
}

#[derive(Clone, Copy, Debug)]
struct Timer {
    last_update: f64,
    interval: f64,
    step: u32,
}

impl Timer {
    fn new(interval: f64) -> Self {
        Timer {
            last_update: 0.0,
            interval,
            step: 0,
        }
    }
}

#[derive(Clone, Debug)]
pub struct FirmwareOptions {
    /// Model the eyeLeds[14] overrun as a write to mouth_leds[0].
    pub oob_aliases_mouth: bool,
    pub rng: Rng,
}

impl Default for FirmwareOptions {
    fn default() -> Self {
        FirmwareOptions {
            oob_aliases_mouth: true,
            rng: Rng::default(),
        }
    }
}

#[derive(Clone, Debug)]
pub struct RexFaceFirmware {
    pub eye_leds: [Rgb; NUM_EYE_LEDS],
    pub mouth_leds: [Rgb; NUM_MOUTH_LEDS],

    pub current_state: State,
    pub previous_state: State,
    pub flash_active: bool,
    pub mouth_amplitude: u8,
    /// Simulated millis().
    pub now: f64,

    breathing_timer: Timer,
    pulse_timer: Timer,
    thinking_timer: Timer,
    mouth_glow_timer: Timer,

    breathing_phase: f64,
    mouth_glow_phase: f64,
    mouth_brightness_offset: f64,
    mouth_speed_multiplier: f64,

    is_blinking: bool,
    next_blink_time: f64,
    blink_start_time: f64,
    flash_step: u32,

    rx: VecDeque<char>,
    command_buffer: String,
    reading_command: bool,
    out: Vec<String>,
    rng: Rng,
    pub oob_aliases_mouth: bool,
}

const MOUTH_BRIGHTNESS_DRIFT: f64 = 0.002;

impl Default for RexFaceFirmware {
    fn default() -> Self {
        Self::new(FirmwareOptions::default())
    }
}

impl RexFaceFirmware {
    pub fn new(opts: FirmwareOptions) -> Self {
        let mut fw = RexFaceFirmware {
            eye_leds: [BLACK; NUM_EYE_LEDS],
            mouth_leds: [BLACK; NUM_MOUTH_LEDS],
            current_state: State::Idle,
            previous_state: State::Idle,
            flash_active: false,
            mouth_amplitude: 0,
            now: 0.0,
            breathing_timer: Timer::new(20.0),
            pulse_timer: Timer::new(20.0),
            thinking_timer: Timer::new(30.0),
            mouth_glow_timer: Timer::new(16.0),
            breathing_phase: 0.0,
            mouth_glow_phase: 0.0,
            mouth_brightness_offset: 0.0,
            mouth_speed_multiplier: 1.0,
            is_blinking: false,
            next_blink_time: 0.0,
            blink_start_time: 0.0,
            flash_step: 0,
            rx: VecDeque::new(),
            command_buffer: String::new(),
            reading_command: false,
            out: Vec::new(),
            rng: opts.rng,
            oob_aliases_mouth: opts.oob_aliases_mouth,
        };
        fw.setup();
        fw
    }

    /// Arduino random(min, max): [min, max).
    fn random(&mut self, min: f64, max: f64) -> f64 {
        min + libm::floor(self.rng.next_f64() * (max - min))
    }

    fn setup(&mut self) {
        self.eye_leds = [BLACK; NUM_EYE_LEDS];
        self.mouth_leds = [BLACK; NUM_MOUTH_LEDS];
        self.next_blink_time = self.now + self.random(12000.0, 25000.0);
        // setState(IDLE) with currentState already IDLE and no flash returns early, as in
        // the sketch, so the eyes stay black until the first breathing tick.
        self.set_state(State::Idle);
        self.println("READY");
    }

    // ------------------------------------------------------------------ serial I/O

    /// Bytes from the host, as CantinaOS writes them (e.g. "SS\n", "M128\n").
    pub fn write(&mut self, data: &str) {
        self.rx.extend(data.chars());
    }

    /// Lines the sketch printed since the last call.
    pub fn read_lines(&mut self) -> Vec<String> {
        std::mem::take(&mut self.out)
    }

    fn println(&mut self, s: &str) {
        self.out.push(s.to_string());
    }

    // ------------------------------------------------------------------ clock

    /// Run loop() until simulated time reaches `ms`.
    pub fn advance_to(&mut self, ms: f64) {
        // Guard against a huge catch-up after a background tab: never simulate > 2 s at once.
        if ms - self.now > 2000.0 {
            self.now = ms - 2000.0;
        }
        while self.now < ms {
            let flashing = self.flash_active;
            self.run_loop();
            self.now += if flashing { 10.0 } else { 1.0 };
        }
    }

    fn run_loop(&mut self) {
        self.process_serial_commands();
        self.update_base_state();
        self.apply_breathing_effect();
        self.apply_blinking_effect();
        self.update_mouth();
        self.update_flash();
    }

    // ------------------------------------------------------------------ commands

    fn process_serial_commands(&mut self) {
        while let Some(c) = self.rx.pop_front() {
            if matches!(c, 'S' | 'M' | 'T') && !self.reading_command {
                self.command_buffer = c.to_string();
                self.reading_command = true;
                continue;
            }

            if self.reading_command {
                self.command_buffer.push(c);
                // JS string length counts UTF-16 units.
                let len = self.command_buffer.encode_utf16().count();
                let first = self.command_buffer.as_bytes()[0];
                if len == 2 && first == b'S' {
                    self.handle_state_command(c);
                    self.end_command();
                } else if len == 4 && first == b'M' {
                    // String::toInt(): leading digits, 0 if none.
                    let amp = js_parse_int(&self.command_buffer[1..]).unwrap_or(0);
                    self.mouth_amplitude = amp.clamp(0, 255) as u8;
                    self.end_command();
                } else if first == b'T' && (len == 1 || len == 2) {
                    if len == 1 || c == '\n' || c == '\r' || (len == 2 && ('1'..='7').contains(&c))
                    {
                        let cmd = self.command_buffer.clone();
                        self.handle_test_command(&cmd);
                        self.end_command();
                    }
                } else if len > 4 {
                    self.end_command();
                }
                continue;
            }

            if c == 'R' {
                self.reset_system();
                self.println("+");
            } else if c == '?' {
                self.println("Commands: SI SE SL ST SS SF Mnnn R ? T T1-T7");
            }
        }
    }

    fn end_command(&mut self) {
        self.command_buffer.clear();
        self.reading_command = false;
    }

    fn handle_test_command(&mut self, cmd: &str) {
        // The self-tests are blocking delay() sequences for bench checks; not emulated.
        self.out.push(format!("(test {cmd} not emulated)"));
    }

    fn handle_state_command(&mut self, ch: char) {
        match ch {
            'I' => self.set_state(State::Idle),
            'E' => self.set_state(State::Engaged),
            'L' => self.set_state(State::Listening),
            'T' => self.set_state(State::Thinking),
            'S' => self.set_state(State::Speaking),
            'F' => self.trigger_flash(),
            _ => {
                self.println("-");
                return;
            }
        }
        self.println("+");
    }

    // ------------------------------------------------------------------ state

    fn set_state(&mut self, new_state: State) {
        if new_state == self.current_state && !self.flash_active {
            return;
        }
        self.previous_state = self.current_state;
        self.current_state = new_state;
        self.breathing_timer.step = 0;
        self.pulse_timer.step = 0;
        self.thinking_timer.step = 0;

        match self.current_state {
            State::Idle => {
                self.set_idle_eyes();
                self.next_blink_time = self.now + self.random(12000.0, 25000.0);
            }
            State::Engaged => {
                self.set_engaged_eyes();
                self.mouth_leds = [BLACK; NUM_MOUTH_LEDS];
            }
            State::Thinking => self.eye_leds = [BLACK; NUM_EYE_LEDS],
            _ => {}
        }
    }

    fn set_both_eyes(&mut self, i: usize, c: Rgb) {
        self.eye_leds[LEFT_EYE_START + i] = c;
        self.eye_leds[RIGHT_EYE_START + i] = c;
    }

    fn set_idle_eyes(&mut self) {
        for i in 0..LEDS_PER_EYE {
            self.set_both_eyes(
                i,
                if i == 0 {
                    COLOR_IDLE_EYES_CENTER
                } else {
                    COLOR_IDLE_EYES_OUTER
                },
            );
        }
    }

    fn set_engaged_eyes(&mut self) {
        for i in 0..LEDS_PER_EYE {
            self.set_both_eyes(
                i,
                if i == 0 {
                    COLOR_ENGAGED_CENTER
                } else {
                    COLOR_ENGAGED_EYES
                },
            );
        }
    }

    // ------------------------------------------------------------------ base animations

    fn update_base_state(&mut self) {
        match self.current_state {
            State::Listening => self.update_listening_pulse(),
            State::Thinking => self.update_thinking_animation(),
            State::Speaking => self.update_speaking_pulse(),
            _ => {}
        }
    }

    fn scaled_both_eyes(&mut self, brightness: f64, center: Rgb, outer: Rgb) {
        for i in 0..LEDS_PER_EYE {
            self.set_both_eyes(
                i,
                nscale8(if i == 0 { center } else { outer }, brightness * 255.0),
            );
        }
    }

    /// `timer.now - lastUpdate >= interval`: stamp it and return true.
    fn due(now: f64, t: &mut Timer) -> bool {
        if now - t.last_update < t.interval {
            return false;
        }
        t.last_update = now;
        true
    }

    fn update_listening_pulse(&mut self) {
        if !Self::due(self.now, &mut self.pulse_timer) {
            return;
        }
        let cycle = f64::from(self.pulse_timer.step % 50);
        let brightness = if cycle < 10.0 {
            0.3 + (cycle / 10.0) * 0.7
        } else {
            1.0 - ((cycle - 10.0) / 40.0) * 0.7
        };
        self.scaled_both_eyes(brightness, COLOR_ENGAGED_CENTER, COLOR_ENGAGED_EYES);
        self.pulse_timer.step += 1;
    }

    fn update_thinking_animation(&mut self) {
        if !Self::due(self.now, &mut self.thinking_timer) {
            return;
        }
        self.set_engaged_eyes();
        let half = (self.thinking_timer.step / 2) as i32;
        let left_pos1 = (half % 6) + 1;
        let left_pos2 = (left_pos1 % 6) + 1;
        let right_pos1 = 7 - (half % 6);
        let right_pos2 = ((right_pos1 - 7) % 6) + 7; // '%' truncates toward zero, as in C

        self.eye_leds[LEFT_EYE_START + left_pos1 as usize] = COLOR_THINKING_DOT;
        self.eye_leds[LEFT_EYE_START + left_pos2 as usize] = COLOR_THINKING_DOT;
        self.write_eye(RIGHT_EYE_START + right_pos1 as usize, COLOR_THINKING_DOT);
        self.write_eye(RIGHT_EYE_START + right_pos2 as usize, COLOR_THINKING_DOT);
        self.thinking_timer.step += 1;
    }

    /// eyeLeds[i] = c, including the sketch's out-of-bounds index 14.
    fn write_eye(&mut self, i: usize, c: Rgb) {
        if i < NUM_EYE_LEDS {
            self.eye_leds[i] = c;
        } else if self.oob_aliases_mouth && i - NUM_EYE_LEDS < NUM_MOUTH_LEDS {
            self.mouth_leds[i - NUM_EYE_LEDS] = c;
        }
    }

    fn update_speaking_pulse(&mut self) {
        if !Self::due(self.now, &mut self.pulse_timer) {
            return;
        }
        let pulse = (libm::sin(f64::from(self.pulse_timer.step) * 0.1) + 1.0) / 2.0;
        self.scaled_both_eyes(0.7 + pulse * 0.3, COLOR_ENGAGED_CENTER, COLOR_ENGAGED_EYES);
        self.pulse_timer.step += 1;
    }

    // ------------------------------------------------------------------ effect layers

    fn apply_breathing_effect(&mut self) {
        if self.current_state != State::Idle && self.current_state != State::Engaged {
            return;
        }
        if !Self::due(self.now, &mut self.breathing_timer) {
            return;
        }
        self.breathing_phase += 0.036;
        if self.breathing_phase > TWO_PI {
            self.breathing_phase -= TWO_PI;
        }
        let breath_value = (libm::sin(self.breathing_phase) + 1.0) / 2.0;
        let brightness = 0.85 + breath_value * 0.15;
        if self.current_state == State::Idle {
            self.scaled_both_eyes(brightness, COLOR_IDLE_EYES_CENTER, COLOR_IDLE_EYES_OUTER);
        } else {
            self.scaled_both_eyes(brightness, COLOR_ENGAGED_CENTER, COLOR_ENGAGED_EYES);
        }
    }

    fn apply_blinking_effect(&mut self) {
        if self.current_state != State::Idle {
            return;
        }
        if !self.is_blinking && self.now >= self.next_blink_time {
            self.is_blinking = true;
            self.blink_start_time = self.now;
        }
        if !self.is_blinking {
            return;
        }
        let elapsed = self.now - self.blink_start_time;
        if elapsed < 100.0 {
            self.scaled_both_eyes(
                1.0 - (elapsed / 100.0) * 0.9,
                COLOR_IDLE_EYES_CENTER,
                COLOR_IDLE_EYES_OUTER,
            );
        } else if elapsed < 200.0 {
            self.scaled_both_eyes(
                0.1 + ((elapsed - 100.0) / 100.0) * 0.9,
                COLOR_IDLE_EYES_CENTER,
                COLOR_IDLE_EYES_OUTER,
            );
        } else {
            self.is_blinking = false;
            self.next_blink_time = self.now + self.random(12000.0, 25000.0);
        }
    }

    /// Force the next idle blink now (for the UI - the sketch has no such command).
    pub fn blink_now(&mut self) {
        if self.current_state == State::Idle && !self.is_blinking {
            self.next_blink_time = self.now;
        }
    }

    // ------------------------------------------------------------------ mouth

    fn update_mouth(&mut self) {
        if self.current_state == State::Idle {
            if !Self::due(self.now, &mut self.mouth_glow_timer) {
                return;
            }
            self.mouth_leds = [BLACK; NUM_MOUTH_LEDS];
            self.mouth_brightness_offset += MOUTH_BRIGHTNESS_DRIFT;
            if self.mouth_brightness_offset > TWO_PI {
                self.mouth_brightness_offset -= TWO_PI;
            }
            self.mouth_speed_multiplier = 1.0 + libm::sin(self.mouth_brightness_offset * 0.3) * 0.3;
            self.mouth_glow_phase += 0.00523 * self.mouth_speed_multiplier;
            if self.mouth_glow_phase > TWO_PI {
                self.mouth_glow_phase -= TWO_PI;
            }

            let glow_value = (libm::sin(self.mouth_glow_phase) + 1.0) / 2.0;
            let brightness_drift = libm::sin(self.mouth_brightness_offset) * 0.05;
            let brightness = (0.2 + glow_value * 0.18 + brightness_drift).clamp(0.15, 0.42);

            let mut tube = COLOR_IDLE_MOUTH;
            if glow_value > 0.5 {
                let mut warmth = (glow_value - 0.5) / 0.5;
                warmth *= warmth;
                let warm_add = libm::trunc(warmth * 35.0);
                tube[0] = u8_of((f64::from(tube[0]) + warm_add).min(255.0));
                tube[1] = u8_of((f64::from(tube[1]) + warm_add * 0.4).min(255.0));
            }
            let glow = nscale8(tube, brightness * 255.0);
            for i in [1, 2, 5, 6] {
                self.mouth_leds[i] = glow;
            }
            return;
        }

        if self.current_state != State::Speaking && self.current_state != State::Engaged {
            return;
        }
        self.mouth_leds = [BLACK; NUM_MOUTH_LEDS];
        if self.mouth_amplitude == 0 {
            return;
        }

        let scaled_amp = libm::sqrt(libm::sqrt(f64::from(self.mouth_amplitude) / 255.0));
        let mouth = COLOR_ENGAGED_MOUTH;

        if scaled_amp > 0.0 {
            let s1 = (scaled_amp / 0.4).min(1.0);
            let middle_bright = libm::trunc(s1 * s1 * 180.0);
            let mut middle = mouth;
            if scaled_amp > 0.95 {
                let w = (scaled_amp - 0.95) / 0.05;
                let white_add = libm::trunc(w * w * 40.0) as u32;
                for ch in &mut middle {
                    *ch = (u32::from(*ch) + white_add).min(255) as u8;
                }
            }
            let c = nscale8(middle, middle_bright);
            self.mouth_leds[1] = c;
            self.mouth_leds[6] = c;
        }
        if scaled_amp > 0.3 {
            let s2 = ((scaled_amp - 0.3) / 0.3).min(1.0);
            let c = nscale8(mouth, libm::trunc(s2 * s2 * 160.0));
            self.mouth_leds[2] = c;
            self.mouth_leds[5] = c;
        }
        if scaled_amp > 0.6 {
            let s3 = ((scaled_amp - 0.6) / 0.2).min(1.0);
            let c = nscale8(mouth, libm::trunc(s3 * s3 * 140.0));
            self.mouth_leds[0] = c;
            self.mouth_leds[7] = c;
        }
        if scaled_amp > 0.8 {
            let s4 = ((scaled_amp - 0.8) / 0.2).min(1.0);
            let c = nscale8(mouth, libm::trunc(s4 * s4 * 200.0));
            self.mouth_leds[3] = c;
            self.mouth_leds[4] = c;
        }
    }

    // ------------------------------------------------------------------ flash

    fn update_flash(&mut self) {
        if !self.flash_active {
            return;
        }
        if self.flash_step < 30 {
            let pulse = |s: u32| {
                let s = f64::from(s);
                if s < 3.0 {
                    s / 3.0
                } else if s < 5.0 {
                    1.0
                } else {
                    1.0 - (s - 5.0) / 5.0
                }
            };
            let brightness = if self.flash_step < 10 {
                pulse(self.flash_step)
            } else if (15..25).contains(&self.flash_step) {
                pulse(self.flash_step - 15)
            } else {
                0.0
            };
            self.eye_leds = [nscale8(COLOR_FLASH, brightness * 255.0); NUM_EYE_LEDS];
            self.flash_step += 1;
            // delay(10) - accounted for in advance_to()
        } else {
            self.flash_active = false;
            self.flash_step = 0;
            self.set_state(State::Engaged);
        }
    }

    fn trigger_flash(&mut self) {
        self.flash_active = true;
        self.flash_step = 0;
    }

    fn reset_system(&mut self) {
        self.mouth_amplitude = 0;
        self.flash_active = false;
        self.flash_step = 0;
        self.set_state(State::Idle);
    }
}

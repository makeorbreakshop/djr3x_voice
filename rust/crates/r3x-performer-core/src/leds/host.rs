//! Port of `sim/web/src/host.ts`: the CantinaOS side of the face link, so the emulated
//! firmware receives exactly the bytes the real system would send. Ports of the
//! ElevenLabsService amplitude AGC, EyeLightControllerService (mode -> pattern, EMA(0.3)
//! amplitude at 60 Hz change-only, "FLASH then M000" at speech end) and SimpleEyeAdapter
//! (mouth commands throttled to 10 Hz by DROPPING, so the end-of-speech M000 can be lost -
//! reproduced on purpose, see `dropped_mouth_resets`).

use std::collections::VecDeque;

use super::chest::ChestHost;
use super::firmware::RexFaceFirmware;

#[derive(Clone, Copy, Debug, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "UPPERCASE")]
pub enum SystemMode {
    Idle,
    Ambient,
    Interactive,
}

impl SystemMode {
    pub fn as_str(self) -> &'static str {
        match self {
            SystemMode::Idle => "IDLE",
            SystemMode::Ambient => "AMBIENT",
            SystemMode::Interactive => "INTERACTIVE",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Pattern {
    Idle,
    Engaged,
    Listening,
    Thinking,
    Speaking,
    Flash,
}

impl Pattern {
    /// The serial word SimpleEyeAdapter sends for it.
    pub fn command(self) -> &'static str {
        match self {
            Pattern::Idle => "SI",
            Pattern::Engaged => "SE",
            Pattern::Listening => "SL",
            Pattern::Thinking => "ST",
            Pattern::Speaking => "SS",
            Pattern::Flash => "SF",
        }
    }
}

/// An EYE_COMMAND pattern as the firmware will show it. SimpleEyeAdapter maps any pattern it
/// has no serial word for (happy, sad, angry, surprised, error, ...) to idle; a name
/// EyeLightControllerService does not accept is rejected (None).
pub fn eye_pattern(name: &str) -> Option<Pattern> {
    Some(match name.to_lowercase().as_str() {
        "idle" => Pattern::Idle,
        "engaged" => Pattern::Engaged,
        "listening" => Pattern::Listening,
        "thinking" => Pattern::Thinking,
        "speaking" => Pattern::Speaking,
        "flash" => Pattern::Flash,
        "startup" | "happy" | "sad" | "angry" | "surprised" | "error" | "custom" => Pattern::Idle,
        _ => return None,
    })
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SerialDir {
    Tx,
    Rx,
}

/// One tapped serial line (the TS `SerialTap` callback's arguments).
#[derive(Clone, Debug, PartialEq)]
pub struct TapLine {
    pub dir: SerialDir,
    pub line: String,
    pub at_ms: f64,
}

const MOUTH_UPDATE_INTERVAL: f64 = 16.7; // ms (0.0167 s)
const ADAPTER_MOUTH_INTERVAL: f64 = 100.0; // ms (10 Hz)

/// Drives the face firmware it owns (`fw`); every line sent is also queued on the tap when
/// `tap` is set (drain with [`CantinaHostEmulator::take_tapped`]).
#[derive(Clone, Debug)]
pub struct CantinaHostEmulator {
    pub fw: RexFaceFirmware,
    pub mode: SystemMode,
    pub target_pattern: Pattern,
    pub current_pattern: Option<Pattern>,
    pub amplitude_modulation: f64,
    pub dropped_mouth_resets: u32,
    pub tap: bool,
    last_mouth_level: i64,
    last_mouth_command_time: f64,
    adapter_last_mouth_time: f64,
    last_control_tick: f64,
    // A show's eye command: shown until its duration ends or the interaction state changes.
    shown: Option<Pattern>,
    shown_until: f64,
    shown_over: Pattern,
    tapped: Vec<TapLine>,
}

impl CantinaHostEmulator {
    pub fn new(fw: RexFaceFirmware, tap: bool) -> Self {
        CantinaHostEmulator {
            fw,
            mode: SystemMode::Idle,
            target_pattern: Pattern::Idle,
            current_pattern: None,
            amplitude_modulation: 0.0,
            dropped_mouth_resets: 0,
            tap,
            last_mouth_level: -1,
            last_mouth_command_time: f64::NEG_INFINITY,
            adapter_last_mouth_time: f64::NEG_INFINITY,
            last_control_tick: f64::NEG_INFINITY,
            shown: None,
            shown_until: 0.0,
            shown_over: Pattern::Idle,
            tapped: Vec::new(),
        }
    }

    /// Lines sent since the last call.
    pub fn take_tapped(&mut self) -> Vec<TapLine> {
        std::mem::take(&mut self.tapped)
    }

    fn send(&mut self, line: String) {
        self.fw.write(&line);
        self.fw.write("\n");
        if self.tap {
            self.tapped.push(TapLine {
                dir: SerialDir::Tx,
                line,
                at_ms: self.fw.now,
            });
        }
    }

    // ------------------------------------------------------------------ events in

    pub fn set_mode(&mut self, mode: SystemMode) {
        self.mode = mode;
        self.target_pattern = if mode == SystemMode::Idle {
            Pattern::Idle
        } else {
            Pattern::Engaged
        };
    }

    fn interactive(&self) -> bool {
        self.mode == SystemMode::Interactive
    }

    pub fn listening_started(&mut self) {
        if self.interactive() {
            self.target_pattern = Pattern::Listening;
        }
    }

    /// VOICE_LISTENING_STOPPED / processing started / mouse recording stopped.
    pub fn listening_stopped(&mut self) {
        if self.interactive() {
            self.target_pattern = Pattern::Thinking;
        }
    }

    /// LLM_RESPONSE_CHUNK while thinking.
    pub fn llm_chunk(&mut self) {
        if self.interactive() && self.target_pattern == Pattern::Thinking {
            self.target_pattern = Pattern::Speaking;
        }
    }

    pub fn speech_started(&mut self) {
        if self.interactive() {
            self.target_pattern = Pattern::Speaking;
        }
    }

    /// SPEECH_SYNTHESIS_AMPLITUDE, amplitude 0..1.
    pub fn amplitude(&mut self, a: f64) {
        if self.target_pattern != Pattern::Speaking {
            return;
        }
        self.amplitude_modulation = 0.3 * a + 0.7 * self.amplitude_modulation;
        let now = self.fw.now;
        if now - self.last_mouth_command_time >= MOUTH_UPDATE_INTERVAL {
            let level = libm::trunc(self.amplitude_modulation * 255.0) as i64;
            if level != self.last_mouth_level {
                self.adapter_set_mouth(level);
                self.last_mouth_level = level;
                self.last_mouth_command_time = now;
            }
        }
    }

    pub fn speech_ended(&mut self) {
        if !self.interactive() {
            return;
        }
        self.target_pattern = Pattern::Flash;
        self.amplitude_modulation = 0.0;
        self.last_mouth_level = -1;
        if !self.adapter_set_mouth(0) {
            self.dropped_mouth_resets += 1;
        }
    }

    /// Returns false when the adapter's 10 Hz throttle swallowed the command.
    fn adapter_set_mouth(&mut self, level: i64) -> bool {
        let now = self.fw.now;
        if now - self.adapter_last_mouth_time < ADAPTER_MOUTH_INTERVAL {
            return false;
        }
        self.adapter_last_mouth_time = now;
        self.send(format!("M{:03}", level.clamp(0, 255)));
        true
    }

    /// EYE_COMMAND {pattern, duration?}: the pattern goes out through the control loop for
    /// `duration_s` (0: until the interaction state next changes), then the loop returns to
    /// its target. Returns false for an unknown pattern.
    pub fn eye_command(&mut self, pattern: &str, duration_s: f64) -> bool {
        let Some(p) = eye_pattern(pattern) else {
            return false;
        };
        self.shown = Some(p);
        self.shown_until = if duration_s > 0.0 {
            self.fw.now + duration_s * 1000.0
        } else {
            f64::INFINITY
        };
        self.shown_over = self.target_pattern;
        self.current_pattern = None; // re-send even if it is the same word (a second flash)
        true
    }

    /// 60 Hz control loop: send the pattern when the target changed.
    pub fn tick(&mut self) {
        let now = self.fw.now;
        if now - self.last_control_tick < 1000.0 / 60.0 {
            return;
        }
        self.last_control_tick = now;
        if self.shown.is_some()
            && (now >= self.shown_until || self.target_pattern != self.shown_over)
        {
            self.shown = None;
        }
        let want = self.shown.unwrap_or(self.target_pattern);
        if Some(want) != self.current_pattern {
            self.send(want.command().to_string());
            self.current_pattern = Some(want);
        }
    }
}

/// ElevenLabsService's per-chunk amplitude: RMS -> dBFS -> AGC-normalised 0..1.
/// Feed it int16-scale RMS values, one per ~16.7 ms chunk.
#[derive(Clone, Debug, Default)]
pub struct TtsAmplitudeAgc {
    recent: VecDeque<f64>,
}

const AGC_WINDOW: usize = 30;
const AGC_MIN_RANGE_DB: f64 = 12.0;

impl TtsAmplitudeAgc {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn next(&mut self, rms_int16: f64) -> f64 {
        if rms_int16 <= 0.0 {
            return 0.0;
        }
        let db = 20.0 * libm::log10(rms_int16 / 32768.0);
        self.recent.push_back(db);
        if self.recent.len() > AGC_WINDOW {
            self.recent.pop_front();
        }
        let a = if self.recent.len() >= 10 {
            let lo = self.recent.iter().copied().fold(f64::INFINITY, f64::min);
            let hi = self
                .recent
                .iter()
                .copied()
                .fold(f64::NEG_INFINITY, f64::max);
            let range = AGC_MIN_RANGE_DB.max(hi - lo);
            ((db - lo) / range).clamp(0.0, 1.0)
        } else {
            ((db + 50.0) / 40.0).clamp(0.0, 1.0)
        };
        (a * 2.0).min(1.0)
    }

    pub fn reset(&mut self) {
        self.recent.clear();
    }
}

/// Both boards from one event stream, as CantinaOS does (EyeLightControllerService and
/// ChestLightControllerService subscribe to the same events).
#[derive(Clone, Debug)]
pub struct DualHost {
    pub face: CantinaHostEmulator,
    pub chest: ChestHost,
}

impl DualHost {
    pub fn new(face: CantinaHostEmulator, chest: ChestHost) -> Self {
        DualHost { face, chest }
    }
    pub fn mode(&self) -> SystemMode {
        self.face.mode
    }
    pub fn dropped_mouth_resets(&self) -> u32 {
        self.face.dropped_mouth_resets
    }
    pub fn set_mode(&mut self, m: SystemMode) {
        self.face.set_mode(m);
        self.chest.set_mode(m.as_str());
    }
    pub fn listening_started(&mut self) {
        self.face.listening_started();
        self.chest.listening_started();
    }
    pub fn listening_stopped(&mut self) {
        self.face.listening_stopped();
        self.chest.listening_stopped();
    }
    pub fn llm_chunk(&mut self) {
        self.face.llm_chunk();
        self.chest.llm_chunk();
    }
    pub fn speech_started(&mut self) {
        self.face.speech_started();
        self.chest.speech_started();
    }
    pub fn amplitude(&mut self, a: f64) {
        self.face.amplitude(a);
        self.chest.amplitude_in(a);
    }
    pub fn speech_ended(&mut self) {
        self.face.speech_ended();
        self.chest.speech_ended();
    }
    pub fn tick(&mut self, now_ms: f64) {
        self.face.tick();
        self.chest.tick(now_ms);
    }
}

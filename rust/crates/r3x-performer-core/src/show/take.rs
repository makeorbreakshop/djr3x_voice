//! Take recorder (port of `take.ts`): the puppeteer command stream at 50 Hz as JSONL, plus
//! the context a policy would condition on. The training record for an operator-imitation
//! policy (see puppeteer.rs).
//!
//! Lines: a header, then `{type:"sample", t, cmd, slots, mode, ...}` every 20 ms of the
//! clock (anchored at the start, never accumulated), and `{type:"event", t, topic, data}`.
//! `t` is seconds since the take started.

use super::puppeteer::{Command, PuppetMode, CONTINUOUS, MODES, SLOT_COUNT};
use serde::Serialize;
use serde_json::{json, Map, Value};

pub const TAKE_HZ: f64 = 50.0;

#[derive(Clone, Debug, Serialize)]
pub struct TakeSample {
    pub cmd: Command,
    pub slots: Vec<usize>,
    pub mode: PuppetMode,
    pub frozen: bool,
    pub activity: String,
    pub speaking: bool,
    pub layers: Map<String, Value>,
    pub live: bool,
}

/// `+x.toFixed(n)` as a JSON number (integral values print without a fraction, as in JS).
fn fixed(x: f64, n: i32) -> Value {
    let p = 10f64.powi(n);
    let v = (x * p).round() / p;
    if v.fract() == 0.0 && v.abs() < 1e15 {
        json!(v as i64)
    } else {
        json!(v)
    }
}

#[derive(Clone, Debug, Default)]
pub struct TakeRecorder {
    lines: Vec<String>,
    t0: f64,
    n: i64,
    pub recording: bool,
}

impl TakeRecorder {
    /// `started` is the wall-clock ISO time for the header (the core has no clock).
    pub fn start(&mut self, now: f64, slots: &[String], started: &str) {
        let header = json!({
            "type": "header", "format": "r3x-take v1", "hz": TAKE_HZ as i64, "started": started,
            "command_space": {
                "continuous": CONTINUOUS, "range": [-1, 1],
                "slots": &slots[..slots.len().min(SLOT_COUNT)], "modes": MODES,
            },
        });
        self.lines = vec![header.to_string()];
        self.t0 = now;
        self.n = 0;
        self.recording = true;
    }

    pub fn stop(&mut self) {
        self.recording = false;
    }

    pub fn samples(&self) -> i64 {
        self.n
    }
    pub fn seconds(&self) -> f64 {
        self.n as f64 / TAKE_HZ
    }

    /// Call every frame; writes every 20 ms tick that has passed (holding the latest values).
    pub fn sample(&mut self, now: f64, read: impl FnOnce() -> TakeSample) {
        if !self.recording {
            return;
        }
        // Catch up at most 1 s after a stall instead of flooding.
        if (now - self.t0) * TAKE_HZ - self.n as f64 > TAKE_HZ {
            self.n = ((now - self.t0) * TAKE_HZ).floor() as i64 - TAKE_HZ as i64;
        }
        let mut read = Some(read);
        let mut s: Option<TakeSample> = None;
        while self.t0 + self.n as f64 / TAKE_HZ <= now {
            let smp = s.get_or_insert_with(|| (read.take().expect("read once"))());
            let mut line = Map::new();
            line.insert("type".into(), json!("sample"));
            line.insert("t".into(), fixed(self.n as f64 / TAKE_HZ, 3));
            if let Value::Object(m) = serde_json::to_value(&*smp).unwrap_or_default() {
                line.extend(m);
            }
            let cmd: Map<String, Value> = CONTINUOUS
                .iter()
                .zip(smp.cmd)
                .map(|(k, v)| ((*k).into(), fixed(v, 4)))
                .collect();
            line.insert("cmd".into(), Value::Object(cmd));
            self.lines.push(Value::Object(line).to_string());
            smp.slots.clear(); // a trigger belongs to one sample only
            self.n += 1;
        }
    }

    pub fn event(&mut self, now: f64, topic: &str, data: Value) {
        if self.recording {
            self.lines.push(json!({"type": "event", "t": fixed(now - self.t0, 3), "topic": topic, "data": data}).to_string());
        }
    }

    pub fn to_jsonl(&self) -> String {
        self.lines.join("\n") + "\n"
    }
}

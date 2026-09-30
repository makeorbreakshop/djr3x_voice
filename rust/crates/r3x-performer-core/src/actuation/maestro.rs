//! Pololu Maestro script interpreter (port of `maestro.ts`), the subset show scripts use,
//! running on the sim clock and driving the same channels the board would.
//!
//! Supported: decimal literals, `#` comments, begin/repeat, begin/while, if/else/endif,
//! sub/return and calls by name, goto/labels, servo, speed, acceleration, delay, quit,
//! get_position, get_moving_state, and the stack/arithmetic/logic words. Anything else is a
//! compile error with its line.

use super::pipeline::Actuation;
use std::collections::HashMap;
use std::fmt;

pub trait MaestroHost {
    fn set_target(&mut self, channel: i64, quarter_us: i64) -> bool;
    fn set_speed(&mut self, channel: i64, v: i64);
    fn set_accel(&mut self, channel: i64, v: i64);
    /// quarter-us
    fn get_position(&mut self, channel: i64) -> i64;
    fn any_moving(&mut self) -> bool;
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct MaestroScriptError(pub String);

impl fmt::Display for MaestroScriptError {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        f.write_str(&self.0)
    }
}
impl std::error::Error for MaestroScriptError {}

#[derive(Clone, Debug, PartialEq)]
pub enum Op {
    Push(i64),
    Word(String, usize),
    Jmp(usize),
    Jz(usize),
    Call(usize),
    Ret,
    Quit,
}

fn binary(w: &str, a: i64, b: i64) -> Option<i64> {
    let bool_ = |x: bool| x as i64;
    Some(match w {
        "plus" => a.wrapping_add(b),
        "minus" => a.wrapping_sub(b),
        "times" => a.wrapping_mul(b),
        "divide" => {
            if b == 0 {
                0
            } else {
                a / b
            }
        }
        "mod" => {
            if b == 0 {
                0
            } else {
                a % b
            }
        }
        "min" => a.min(b),
        "max" => a.max(b),
        "less_than" => bool_(a < b),
        "greater_than" => bool_(a > b),
        "equals" => bool_(a == b),
        "not_equals" => bool_(a != b),
        "logical_and" => bool_(a != 0 && b != 0),
        "logical_or" => bool_(a != 0 || b != 0),
        // JS bitwise operators work on int32.
        "bitwise_and" => ((a as i32) & (b as i32)) as i64,
        "bitwise_or" => ((a as i32) | (b as i32)) as i64,
        _ => return None,
    })
}

const KNOWN: [&str; 15] = [
    "servo",
    "speed",
    "acceleration",
    "delay",
    "get_position",
    "get_moving_state",
    "drop",
    "dup",
    "swap",
    "over",
    "rot",
    "depth",
    "negate",
    "logical_not",
    "bitwise_not",
];

enum Block {
    Begin {
        at: usize,
        while_at: Option<usize>,
        line: usize,
    },
    If {
        at: usize,
        line: usize,
    },
    Else {
        at: usize,
        line: usize,
    },
}

fn err(msg: String) -> MaestroScriptError {
    MaestroScriptError(msg)
}

/// Compile to ops plus the subroutine table.
pub fn compile_maestro(src: &str) -> Result<(Vec<Op>, HashMap<String, usize>), MaestroScriptError> {
    let mut tokens: Vec<(String, usize)> = Vec::new();
    for (i, raw) in src.split('\n').enumerate() {
        let text = raw.split('#').next().unwrap_or("");
        tokens.extend(text.split_whitespace().map(|t| (t.to_lowercase(), i + 1)));
    }
    let mut ops: Vec<Op> = Vec::new();
    let mut subs: HashMap<String, usize> = HashMap::new();
    let mut labels: HashMap<String, usize> = HashMap::new();
    let mut gotos: Vec<(usize, String, usize)> = Vec::new();
    let mut blocks: Vec<Block> = Vec::new();
    let mut in_sub = false;
    let patch = |ops: &mut Vec<Op>, at: usize, to: usize| match &mut ops[at] {
        Op::Jmp(t) | Op::Jz(t) => *t = to,
        _ => {}
    };

    let mut i = 0;
    while i < tokens.len() {
        let (t, line) = (tokens[i].0.as_str(), tokens[i].1);
        let is_int = {
            let d = t.strip_prefix('-').unwrap_or(t);
            !d.is_empty() && d.chars().all(|c| c.is_ascii_digit())
        };
        if is_int {
            ops.push(Op::Push(t.parse().unwrap_or(0)));
        } else if t == "begin" {
            blocks.push(Block::Begin {
                at: ops.len(),
                while_at: None,
                line,
            });
        } else if t == "while" {
            match blocks.last_mut() {
                Some(Block::Begin { while_at, .. }) => *while_at = Some(ops.len()),
                _ => return Err(err(format!("line {line}: while without begin"))),
            }
            ops.push(Op::Jz(usize::MAX));
        } else if t == "repeat" {
            let Some(Block::Begin { at, while_at, .. }) = blocks.pop() else {
                return Err(err(format!("line {line}: repeat without begin")));
            };
            ops.push(Op::Jmp(at));
            if let Some(w) = while_at {
                let end = ops.len();
                patch(&mut ops, w, end);
            }
        } else if t == "if" {
            blocks.push(Block::If {
                at: ops.len(),
                line,
            });
            ops.push(Op::Jz(usize::MAX));
        } else if t == "else" {
            let Some(Block::If { at, .. }) = blocks.pop() else {
                return Err(err(format!("line {line}: else without if")));
            };
            blocks.push(Block::Else {
                at: ops.len(),
                line,
            });
            ops.push(Op::Jmp(usize::MAX));
            let end = ops.len();
            patch(&mut ops, at, end);
        } else if t == "endif" {
            match blocks.pop() {
                Some(Block::If { at, .. } | Block::Else { at, .. }) => {
                    let end = ops.len();
                    patch(&mut ops, at, end);
                }
                _ => return Err(err(format!("line {line}: endif without if"))),
            }
        } else if t == "sub" {
            i += 1;
            let Some((name, _)) = tokens.get(i) else {
                return Err(err(format!("line {line}: sub needs a name")));
            };
            if !in_sub {
                ops.push(Op::Quit); // main program ends where subroutines begin
            }
            subs.insert(name.clone(), ops.len());
            in_sub = true;
        } else if t == "return" {
            ops.push(Op::Ret);
        } else if t == "quit" {
            ops.push(Op::Quit);
        } else if t == "goto" {
            i += 1;
            let label = tokens.get(i).map(|x| x.0.clone()).unwrap_or_default();
            gotos.push((ops.len(), label, line));
            ops.push(Op::Jmp(usize::MAX));
        } else if let Some(label) = t.strip_suffix(':') {
            labels.insert(label.to_owned(), ops.len());
        } else {
            ops.push(Op::Word(t.to_owned(), line));
        }
        i += 1;
    }
    if let Some(b) = blocks.last() {
        let (kind, line) = match b {
            Block::Begin { line, .. } => ("begin", line),
            Block::If { line, .. } => ("if", line),
            Block::Else { line, .. } => ("else", line),
        };
        return Err(err(format!("line {line}: unclosed {kind}")));
    }
    if !in_sub {
        ops.push(Op::Quit);
    }
    for (at, label, line) in gotos {
        let to = *labels
            .get(&label)
            .ok_or_else(|| err(format!("line {line}: unknown label {label}")))?;
        patch(&mut ops, at, to);
    }
    // Resolve words that name subroutines into calls.
    for op in &mut ops {
        if let Op::Word(w, line) = op {
            if let Some(&to) = subs.get(w.as_str()) {
                *op = Op::Call(to);
            } else if binary(w, 0, 1).is_none() && !KNOWN.contains(&w.as_str()) {
                return Err(err(format!("line {line}: unsupported command '{w}'")));
            }
        }
    }
    Ok((ops, subs))
}

#[derive(Clone, Debug)]
pub struct MaestroScript {
    ops: Vec<Op>,
    pc: usize,
    stack: Vec<i64>,
    calls: Vec<usize>,
    wait_until: f64,
    pub running: bool,
    /// Last few target writes, for the UI.
    pub trace: Vec<String>,
}

impl MaestroScript {
    pub fn new(src: &str) -> Result<Self, MaestroScriptError> {
        Ok(MaestroScript {
            ops: compile_maestro(src)?.0,
            pc: 0,
            stack: Vec::new(),
            calls: Vec::new(),
            wait_until: 0.0,
            running: true,
            trace: Vec::new(),
        })
    }

    fn pop(&mut self, line: usize) -> Result<i64, MaestroScriptError> {
        self.stack
            .pop()
            .ok_or_else(|| err(format!("stack underflow at line {line}")))
    }

    /// Run until the script blocks on a delay past `now_ms`, or 10k instructions.
    pub fn run(
        &mut self,
        now_ms: f64,
        host: &mut dyn MaestroHost,
    ) -> Result<(), MaestroScriptError> {
        let mut budget = 10_000;
        while self.running && now_ms >= self.wait_until && budget > 0 {
            budget -= 1;
            let Some(op) = self.ops.get(self.pc).cloned() else {
                self.running = false;
                break;
            };
            self.pc += 1;
            match op {
                Op::Push(v) => self.stack.push(v),
                Op::Jmp(to) => self.pc = to,
                Op::Jz(to) => {
                    if self
                        .stack
                        .pop()
                        .ok_or_else(|| err("stack underflow".into()))?
                        == 0
                    {
                        self.pc = to;
                    }
                }
                Op::Call(to) => {
                    self.calls.push(self.pc);
                    self.pc = to;
                }
                Op::Ret => self.pc = self.calls.pop().unwrap_or(self.ops.len()),
                Op::Quit => self.running = false,
                Op::Word(w, line) => self.word(&w, line, now_ms, host)?,
            }
        }
        Ok(())
    }

    fn word(
        &mut self,
        w: &str,
        line: usize,
        now_ms: f64,
        host: &mut dyn MaestroHost,
    ) -> Result<(), MaestroScriptError> {
        if binary(w, 0, 1).is_some() {
            let b = self.pop(line)?;
            let a = self.pop(line)?;
            self.stack.push(binary(w, a, b).unwrap_or(0));
            return Ok(());
        }
        match w {
            "servo" => {
                let ch = self.pop(line)?;
                let target = self.pop(line)?;
                host.set_target(ch, target);
                self.trace.push(format!("{ch} <- {target}"));
                if self.trace.len() > 8 {
                    self.trace.remove(0);
                }
            }
            "speed" => {
                let ch = self.pop(line)?;
                let v = self.pop(line)?;
                host.set_speed(ch, v);
            }
            "acceleration" => {
                let ch = self.pop(line)?;
                let v = self.pop(line)?;
                host.set_accel(ch, v);
            }
            "delay" => self.wait_until = now_ms + self.pop(line)?.max(0) as f64,
            "get_position" => {
                let ch = self.pop(line)?;
                self.stack.push(host.get_position(ch));
            }
            "get_moving_state" => self.stack.push(host.any_moving() as i64),
            "drop" => {
                self.pop(line)?;
            }
            "dup" => {
                let a = self.pop(line)?;
                self.stack.extend([a, a]);
            }
            "swap" => {
                let b = self.pop(line)?;
                let a = self.pop(line)?;
                self.stack.extend([b, a]);
            }
            "over" => {
                let b = self.pop(line)?;
                let a = self.pop(line)?;
                self.stack.extend([a, b, a]);
            }
            "rot" => {
                let c = self.pop(line)?;
                let b = self.pop(line)?;
                let a = self.pop(line)?;
                self.stack.extend([b, c, a]);
            }
            "depth" => self.stack.push(self.stack.len() as i64),
            "negate" => {
                let a = self.pop(line)?;
                self.stack.push(-a);
            }
            "logical_not" => {
                let a = self.pop(line)?;
                self.stack.push((a == 0) as i64);
            }
            "bitwise_not" => {
                let a = self.pop(line)?;
                self.stack.push(!(a as i32) as i64);
            }
            _ => {}
        }
        Ok(())
    }
}

/// Adapter: a Maestro script drives an [`Actuation`] exactly as the board would.
impl MaestroHost for Actuation {
    fn set_target(&mut self, channel: i64, quarter_us: i64) -> bool {
        usize::try_from(channel).is_ok_and(|c| Actuation::set_target(self, c, quarter_us as f64))
    }
    fn set_speed(&mut self, channel: i64, v: i64) {
        if let Ok(c) = usize::try_from(channel) {
            self.set_maestro_limits(c, Some(v as f64), None);
        }
    }
    fn set_accel(&mut self, channel: i64, v: i64) {
        if let Ok(c) = usize::try_from(channel) {
            self.set_maestro_limits(c, None, Some(v as f64));
        }
    }
    fn get_position(&mut self, channel: i64) -> i64 {
        usize::try_from(channel)
            .ok()
            .and_then(|c| self.channel(c))
            .map_or(0, |ch| ch.target)
    }
    fn any_moving(&mut self) -> bool {
        self.channels
            .iter()
            .any(|ch| ch.direct_us.is_some_and(|d| (d - ch.us).abs() > 0.5))
    }
}

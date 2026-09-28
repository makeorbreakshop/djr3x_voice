/**
 * Pololu Maestro script interpreter (the subset show scripts use), running on the sim
 * clock and driving the same channels the board would. Reference: Pololu Maestro User's
 * Guide, "The Maestro Scripting Language" and its command reference.
 *
 * Supported: decimal literals, # comments, begin/repeat, begin/while, if/else/endif,
 * sub/return and calls by name, goto/labels, servo, speed, acceleration, delay, quit,
 * get_position, get_moving_state, and stack/arithmetic/logic words
 * (drop dup swap over rot depth plus minus times divide mod negate min max
 *  less_than greater_than equals not_equals logical_and logical_or logical_not
 *  bitwise_and bitwise_or bitwise_not). Anything else is a compile error with its line.
 */

export interface MaestroHost {
  setTarget(channel: number, quarterUs: number): boolean;
  setSpeed(channel: number, v: number): void;
  setAccel(channel: number, v: number): void;
  getPosition(channel: number): number; // quarter-us
  anyMoving(): boolean;
}

type Op =
  | { k: 'push'; v: number }
  | { k: 'word'; w: string; line: number }
  | { k: 'jmp'; to: number }
  | { k: 'jz'; to: number }
  | { k: 'call'; to: number; name: string }
  | { k: 'ret' }
  | { k: 'quit' };

export class MaestroScriptError extends Error {}

const BINARY: Record<string, (a: number, b: number) => number> = {
  plus: (a, b) => a + b,
  minus: (a, b) => a - b,
  times: (a, b) => a * b,
  divide: (a, b) => (b === 0 ? 0 : Math.trunc(a / b)),
  mod: (a, b) => (b === 0 ? 0 : a % b),
  min: Math.min,
  max: Math.max,
  less_than: (a, b) => +(a < b),
  greater_than: (a, b) => +(a > b),
  equals: (a, b) => +(a === b),
  not_equals: (a, b) => +(a !== b),
  logical_and: (a, b) => +(a !== 0 && b !== 0),
  logical_or: (a, b) => +(a !== 0 || b !== 0),
  bitwise_and: (a, b) => a & b,
  bitwise_or: (a, b) => a | b,
};

export function compileMaestro(src: string): { ops: Op[]; subs: Record<string, number> } {
  const tokens: { t: string; line: number }[] = [];
  src.split('\n').forEach((raw, i) => {
    const text = raw.replace(/#.*$/, '');
    for (const t of text.split(/\s+/).filter(Boolean)) tokens.push({ t: t.toLowerCase(), line: i + 1 });
  });

  const ops: Op[] = [];
  const subs: Record<string, number> = {};
  const labels: Record<string, number> = {};
  const gotos: { at: number; label: string; line: number }[] = [];
  const blocks: { kind: 'begin' | 'if' | 'else'; at: number; whileAt?: number; line: number }[] = [];
  let inSub = false;

  for (let i = 0; i < tokens.length; i++) {
    const { t, line } = tokens[i];
    if (/^-?\d+$/.test(t)) {
      ops.push({ k: 'push', v: parseInt(t, 10) });
    } else if (t === 'begin') {
      blocks.push({ kind: 'begin', at: ops.length, line });
    } else if (t === 'while') {
      const b = blocks[blocks.length - 1];
      if (!b || b.kind !== 'begin') throw new MaestroScriptError(`line ${line}: while without begin`);
      b.whileAt = ops.length;
      ops.push({ k: 'jz', to: -1 });
    } else if (t === 'repeat') {
      const b = blocks.pop();
      if (!b || b.kind !== 'begin') throw new MaestroScriptError(`line ${line}: repeat without begin`);
      ops.push({ k: 'jmp', to: b.at });
      if (b.whileAt !== undefined) (ops[b.whileAt] as { to: number }).to = ops.length;
    } else if (t === 'if') {
      blocks.push({ kind: 'if', at: ops.length, line });
      ops.push({ k: 'jz', to: -1 });
    } else if (t === 'else') {
      const b = blocks.pop();
      if (!b || b.kind !== 'if') throw new MaestroScriptError(`line ${line}: else without if`);
      blocks.push({ kind: 'else', at: ops.length, line });
      ops.push({ k: 'jmp', to: -1 });
      (ops[b.at] as { to: number }).to = ops.length;
    } else if (t === 'endif') {
      const b = blocks.pop();
      if (!b || (b.kind !== 'if' && b.kind !== 'else')) throw new MaestroScriptError(`line ${line}: endif without if`);
      (ops[b.at] as { to: number }).to = ops.length;
    } else if (t === 'sub') {
      const name = tokens[++i]?.t;
      if (!name) throw new MaestroScriptError(`line ${line}: sub needs a name`);
      if (!inSub) ops.push({ k: 'quit' }); // main program ends where subroutines begin
      subs[name] = ops.length;
      inSub = true;
    } else if (t === 'return') {
      ops.push({ k: 'ret' });
    } else if (t === 'quit') {
      ops.push({ k: 'quit' });
    } else if (t === 'goto') {
      const label = tokens[++i]?.t;
      gotos.push({ at: ops.length, label, line });
      ops.push({ k: 'jmp', to: -1 });
    } else if (t.endsWith(':')) {
      labels[t.slice(0, -1)] = ops.length;
    } else {
      ops.push({ k: 'word', w: t, line });
    }
  }
  if (blocks.length) throw new MaestroScriptError(`line ${blocks[blocks.length - 1].line}: unclosed ${blocks[blocks.length - 1].kind}`);
  if (!inSub) ops.push({ k: 'quit' });
  for (const g of gotos) {
    if (!(g.label in labels)) throw new MaestroScriptError(`line ${g.line}: unknown label ${g.label}`);
    (ops[g.at] as { to: number }).to = labels[g.label];
  }
  // Resolve words that name subroutines into calls.
  ops.forEach((op, at) => {
    if (op.k === 'word' && op.w in subs) {
      ops[at] = { k: 'call', to: subs[op.w], name: op.w };
    } else if (op.k === 'word' && !(op.w in BINARY) && !KNOWN.has(op.w)) {
      throw new MaestroScriptError(`line ${op.line}: unsupported command '${op.w}'`);
    }
  });
  return { ops, subs };
}

const KNOWN = new Set([
  'servo', 'speed', 'acceleration', 'delay', 'get_position', 'get_moving_state',
  'drop', 'dup', 'swap', 'over', 'rot', 'depth', 'negate', 'logical_not', 'bitwise_not',
]);

export class MaestroScript {
  private readonly ops: Op[];
  private pc = 0;
  private stack: number[] = [];
  private calls: number[] = [];
  private waitUntil = 0;
  running = true;
  /** Last few target writes, for the UI. */
  readonly trace: string[] = [];

  constructor(src: string, private readonly host: MaestroHost) {
    this.ops = compileMaestro(src).ops;
  }

  private pop(line?: number) {
    if (!this.stack.length) throw new MaestroScriptError(`stack underflow${line ? ` at line ${line}` : ''}`);
    return this.stack.pop()!;
  }

  /** Run until the script blocks on a delay past `nowMs`, or 10k instructions. */
  run(nowMs: number) {
    let budget = 10_000;
    while (this.running && nowMs >= this.waitUntil && budget-- > 0) {
      const op = this.ops[this.pc++];
      if (!op) {
        this.running = false;
        break;
      }
      switch (op.k) {
        case 'push': this.stack.push(op.v); break;
        case 'jmp': this.pc = op.to; break;
        case 'jz': if (this.pop() === 0) this.pc = op.to; break;
        case 'call': this.calls.push(this.pc); this.pc = op.to; break;
        case 'ret': this.pc = this.calls.pop() ?? this.ops.length; break;
        case 'quit': this.running = false; break;
        case 'word': this.word(op.w, op.line, nowMs); break;
      }
    }
  }

  private word(w: string, line: number, nowMs: number) {
    const s = this.stack;
    if (w in BINARY) {
      const b = this.pop(line);
      const a = this.pop(line);
      s.push(BINARY[w](a, b));
      return;
    }
    switch (w) {
      case 'servo': {
        const ch = this.pop(line);
        const target = this.pop(line);
        this.host.setTarget(ch, target);
        this.trace.push(`${ch} <- ${target}`);
        if (this.trace.length > 8) this.trace.shift();
        break;
      }
      case 'speed': { const ch = this.pop(line); this.host.setSpeed(ch, this.pop(line)); break; }
      case 'acceleration': { const ch = this.pop(line); this.host.setAccel(ch, this.pop(line)); break; }
      case 'delay': this.waitUntil = nowMs + Math.max(0, this.pop(line)); break;
      case 'get_position': s.push(this.host.getPosition(this.pop(line))); break;
      case 'get_moving_state': s.push(+this.host.anyMoving()); break;
      case 'drop': this.pop(line); break;
      case 'dup': { const a = this.pop(line); s.push(a, a); break; }
      case 'swap': { const b = this.pop(line); const a = this.pop(line); s.push(b, a); break; }
      case 'over': { const b = this.pop(line); const a = this.pop(line); s.push(a, b, a); break; }
      case 'rot': { const c = this.pop(line); const b = this.pop(line); const a = this.pop(line); s.push(b, c, a); break; }
      case 'depth': s.push(s.length); break;
      case 'negate': s.push(-this.pop(line)); break;
      case 'logical_not': s.push(+(this.pop(line) === 0)); break;
      case 'bitwise_not': s.push(~this.pop(line)); break;
    }
  }
}

/** The sample show from Drive: R-3X Animation/08 - r3x maestro sample script.txt. */
export const SAMPLE_SCRIPT_NAME = 'r3x maestro sample script';

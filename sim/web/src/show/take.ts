/**
 * Take recorder: the puppeteer command stream at 50 Hz as JSONL, plus the context a policy
 * would condition on - active show layers, show.* events, mode/activity, and (live) any
 * vision.* person events on the bus. One take = one downloadable file; it is the training
 * record for an operator-imitation policy (see puppeteer.ts).
 *
 * Lines: a header {type:"header", ...command space...}, then {type:"sample", t, cmd,
 * slots, mode, ...} every 20 ms of the sim clock (anchored at the start, never
 * accumulated), and {type:"event", t, topic, data} as they happen. t is seconds since the
 * take started.
 */
import { CONTINUOUS, MODES, SLOT_COUNT, type Command, type PuppetMode } from './puppeteer';

export const TAKE_HZ = 50;

export interface TakeSample {
  cmd: Command;
  slots: number[];
  mode: PuppetMode;
  frozen: boolean;
  activity: string;
  speaking: boolean;
  layers: Record<string, string | null>;
  live: boolean;
}

export class TakeRecorder {
  private lines: string[] = [];
  private t0 = 0;
  private n = 0;
  recording = false;

  start(now: number, slots: string[]) {
    this.lines = [JSON.stringify({
      type: 'header', format: 'r3x-take v1', hz: TAKE_HZ, started: new Date().toISOString(),
      command_space: { continuous: CONTINUOUS, range: [-1, 1], slots: slots.slice(0, SLOT_COUNT), modes: MODES },
    })];
    this.t0 = now;
    this.n = 0;
    this.recording = true;
  }

  stop() { this.recording = false; }

  get samples() { return this.n; }
  get seconds() { return this.n / TAKE_HZ; }

  /** Call every frame; writes every 20 ms tick that has passed (holding the latest values). */
  sample(now: number, read: () => TakeSample) {
    if (!this.recording) return;
    let s: TakeSample | null = null;
    // Catch up at most 1 s after a stall (a background tab) instead of flooding.
    if ((now - this.t0) * TAKE_HZ - this.n > TAKE_HZ) this.n = Math.floor((now - this.t0) * TAKE_HZ) - TAKE_HZ;
    while (this.t0 + this.n / TAKE_HZ <= now) {
      s ??= read();
      const cmd = Object.fromEntries(CONTINUOUS.map((k) => [k, +s!.cmd[k].toFixed(4)]));
      this.lines.push(JSON.stringify({ type: 'sample', t: +(this.n / TAKE_HZ).toFixed(3), ...s, cmd }));
      s.slots = []; // a trigger belongs to one sample only
      this.n++;
    }
  }

  event(now: number, topic: string, data: unknown) {
    if (this.recording) this.lines.push(JSON.stringify({ type: 'event', t: +(now - this.t0).toFixed(3), topic, data }));
  }

  toJsonl() { return this.lines.join('\n') + '\n'; }
}

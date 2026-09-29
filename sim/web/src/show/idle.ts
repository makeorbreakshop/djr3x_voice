/**
 * Weighted idle policy (show/idle.json), after Reachy Mini's: when nothing has happened
 * for `after_s`, pick a weighted choice and perform it with source "idle" (so only
 * free-tier items pass). `while_music` replaces the list while music or DJ mode is on.
 * Any interaction (listening, speech, a UI/voice perform) cancels the idle item and
 * restarts the timer; a looping idle item is also retired after `maxLoopS`.
 */
import type { IdlePolicy, WeightedId } from './types';

export interface IdleContext {
  /** Nothing else is happening: no conversation, no gesture or show running. */
  eligible: boolean;
  music: boolean;
}

export class IdleRunner {
  enabled = true;
  private quietSince: number;
  private runId: string | null = null;
  private runMusic = false;
  private runStarted = 0;
  maxLoopS = 40;

  constructor(public policy: IdlePolicy | null, now: number, private readonly rand: () => number = Math.random) {
    this.quietSince = now;
  }

  /** Something happened: cancel what idle is doing and restart the clock. */
  poke(now: number, stop?: (runId: string) => void) {
    this.quietSince = now;
    if (this.runId && stop) stop(this.runId);
    this.runId = null;
  }

  /** The idle item finished (or was interrupted by someone else). */
  ended(runId: string, now: number) {
    if (runId !== this.runId) return;
    this.runId = null;
    this.quietSince = now;
  }

  get current() { return this.runId; }

  update(now: number, ctx: IdleContext, perform: (id: string) => string | null, stop: (runId: string) => void) {
    if (!this.enabled || !this.policy) {
      if (this.runId) this.poke(now, stop);
      return;
    }
    if (this.runId) {
      if (ctx.music !== this.runMusic || now - this.runStarted > this.maxLoopS) this.poke(now, stop);
      return;
    }
    if (!ctx.eligible) {
      this.quietSince = now;
      return;
    }
    if (now - this.quietSince < this.policy.after_s) return;
    const list = ctx.music && this.policy.while_music?.length ? this.policy.while_music : this.policy.choices;
    const id = pick(list, this.rand);
    this.quietSince = now;
    if (!id) return;
    this.runId = perform(id);
    this.runMusic = ctx.music;
    this.runStarted = now;
  }
}

export function pick(list: WeightedId[], rand: () => number = Math.random): string | null {
  const total = list.reduce((s, c) => s + c.weight, 0);
  let r = rand() * total;
  for (const c of list) {
    r -= c.weight;
    if (r < 0) return c.id;
  }
  return list.length ? list[list.length - 1].id : null;
}

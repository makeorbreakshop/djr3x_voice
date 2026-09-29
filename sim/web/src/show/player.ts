/**
 * The offline show player: the sim's stand-in for CantinaOS's TimelineExecutor (SPEC
 * "Sequence" clock and interruption rules). Live, CantinaOS conducts and the sim only
 * renders the bus topics - this class is then idle.
 *
 * Timing. Each performed item becomes a root run with a monotonic clock anchored at its
 * start: every item is scheduled at `anchor + at` (in seconds, or beats converted through
 * the current tempo segment), never by accumulating sleeps or frame dts, so a slow frame
 * delays an item by at most that frame and never shifts later ones. `wait for speech_end`
 * pauses the root's clock; every later item slides by the wait's real duration. A `beat`
 * clock chases the live tempo: a tempo change starts a new segment at the current beat
 * position, so the beat count stays continuous.
 *
 * Nesting. Nested cues and sequences become child nodes of the same root (one clock, one
 * run_id, one pause), each with its own clock unit - a `time` sequence nested in a `beat`
 * one keeps its offsets in seconds, as in expand().
 *
 * Layers. One root per layer (background < gesture < show). Clips and cues run on the
 * gesture layer, sequences on their declared layer. A new request on a layer ends the
 * current root (its clips blend out in the body, never cut), except that a gesture still
 * inside its `interruptible_after` window queues the request (latest wins).
 */
import type { Catalog } from './catalog';
import { MAX_DEPTH, normalizeAction, ownsUnion, trackEntry } from './expand';
import { clampIntensity, clampSpeed, tierAllows, type Action, type DeptAction, type Params, type Sequence, type ShowItem, type Source } from './types';

export type RunLayer = 'background' | 'gesture' | 'show';
export const RUN_LAYERS: readonly RunLayer[] = ['background', 'gesture', 'show'];

export interface RunInfo {
  run_id: string;
  id: string;
  kind: ShowItem['kind'];
  source: Source;
  layer: RunLayer;
  /** Joints the run owns (sequence `owns`, including nested sequences), or null. */
  owns: string[] | null;
  /** Clock time the run started. */
  startedAt: number;
}

export type EndReason = 'done' | 'interrupted' | 'rejected';

export interface DispatchCtx {
  run: RunInfo;
  /** Clock time the action was scheduled for (may be a fraction of a frame ago). */
  at: number;
}

export interface PlayerHost {
  dispatch(a: DeptAction, ctx: DispatchCtx): void;
  /** Is R3X speaking right now (for `wait for speech_end`)? */
  speechActive(): boolean;
  /** Live music tempo, or null (beat clocks then use the sequence's own bpm). */
  liveBpm(): number | null;
  started?(run: RunInfo): void;
  ended?(run: RunInfo, reason: EndReason): void;
}

export interface PlayerOptions {
  /** How long a wait holds for speech that never starts (s). 0 = do not wait at all. */
  waitGraceS?: number;
  /** Longest a wait may hold (s). */
  waitMaxS?: number;
}

interface Entry { at: number; e: ReturnType<typeof trackEntry>; fired: boolean }

class Node {
  readonly items: Entry[];
  readonly unit: 'time' | 'beat';
  readonly loop: boolean;
  readonly length: number;
  children: Node[] = [];
  // time clock: position = (L - startL) * scale
  startL: number;
  // beat clock: position = segB + (L - segL) * bpm / 60
  segL: number;
  segB = 0;
  bpm: number;

  constructor(readonly item: ShowItem, startL: number, readonly depth: number, readonly scale: number, readonly fallbackBpm: number) {
    this.startL = this.segL = startL;
    this.bpm = fallbackBpm;
    if (item.kind === 'clip') this.items = [{ at: 0, e: { do: 'clip', id: item.id }, fired: false }];
    else if (item.kind === 'cue') this.items = item.actions.map((a) => ({ at: a.at, e: trackEntry(a), fired: false }));
    else this.items = item.track.map((it) => ({ at: it.at, e: trackEntry(it), fired: false }));
    // Stable: ties keep file order.
    this.items.sort((a, b) => a.at - b.at);
    const seq = item.kind === 'sequence' ? (item as Sequence) : null;
    this.unit = seq?.clock === 'beat' ? 'beat' : 'time';
    this.loop = !!seq?.loop;
    this.length = seq?.length ?? 0;
  }

  pos(L: number) {
    return this.unit === 'beat' ? this.segB + ((L - this.segL) * this.bpm) / 60 : (L - this.startL) * this.scale;
  }

  /** Root-local seconds at which this node reaches position p (current tempo segment). */
  timeOf(p: number) {
    return this.unit === 'beat' ? this.segL + ((p - this.segB) * 60) / this.bpm : this.startL + p / this.scale;
  }

  chase(L: number, bpm: number) {
    if (this.unit !== 'beat' || !(bpm > 0) || Math.abs(bpm - this.bpm) < 1e-6) return;
    this.segB = this.pos(L);
    this.segL = L;
    this.bpm = bpm;
  }

  get allFired() { return this.items.every((i) => i.fired); }
  get done(): boolean { return !this.loop && this.allFired && this.children.every((c) => c.done); }
}

class Root {
  paused = 0;
  pauseAt: number | null = null;
  wait: { since: number; saw: boolean } | null = null;
  busyUntil: number;
  spoke = false;
  constructor(readonly info: RunInfo, readonly node: Node, readonly anchor: number, readonly params: Required<Params>, readonly interruptibleAt: number) {
    this.busyUntil = anchor;
  }
  local(now: number) {
    return now - this.anchor - this.paused - (this.pauseAt !== null ? now - this.pauseAt : 0);
  }
  clock(L: number) { return this.anchor + this.paused + L; }
}

interface Request { id: string; source: Source; params: Params; layer: RunLayer; runId: string }

export class ShowPlayer {
  private readonly roots = new Map<RunLayer, Root>();
  private readonly queue = new Map<RunLayer, Request>();
  private seq = 0;
  private now = 0;
  frozen = false;
  readonly waitGraceS: number;
  readonly waitMaxS: number;

  constructor(readonly cat: Catalog, private readonly host: PlayerHost, opts: PlayerOptions = {}) {
    this.waitGraceS = opts.waitGraceS ?? 1.5;
    this.waitMaxS = opts.waitMaxS ?? 30;
  }

  /**
   * Perform an item (SPEC `show.perform`). Returns the run_id, or null when it was rejected
   * (unknown id, tier violation, or motion frozen) - a rejection emits ended(rejected) only.
   */
  perform(id: string, opts: { source?: Source; params?: Params; now?: number; layer?: RunLayer } = {}): string | null {
    if (opts.now !== undefined) this.now = opts.now;
    const source = opts.source ?? 'ui';
    const item = this.cat.get(id) ?? this.cat.resolve(id);
    const runId = `${item?.id ?? id}#${++this.seq}`;
    const layer: RunLayer = opts.layer ?? (item?.kind === 'sequence' ? (item as Sequence).layer ?? 'show' : 'gesture');
    if (!item || this.frozen || !tierAllows(item.tier, source)) {
      this.host.ended?.({ run_id: runId, id, kind: item?.kind ?? 'clip', source, layer, owns: null, startedAt: this.now }, 'rejected');
      return null;
    }
    const req: Request = { id: item.id, source, params: opts.params ?? {}, layer, runId };
    const cur = this.roots.get(layer);
    if (cur && this.now < cur.interruptibleAt) {
      this.queue.set(layer, req); // latest wins
      return runId;
    }
    this.queue.delete(layer);
    this.begin(req);
    return runId;
  }

  /** SPEC `show.stop {id|layer|all}`; with no argument, stops everything. */
  stop(sel: { id?: string; layer?: RunLayer; all?: boolean } = { all: true }, now?: number) {
    if (now !== undefined) this.now = now;
    for (const [layer, r] of [...this.roots]) {
      if (sel.all || sel.layer === layer || (sel.id && (sel.id === r.info.id || sel.id === r.info.run_id))) {
        this.queue.delete(layer);
        this.finish(r, 'interrupted');
      }
    }
  }

  /** Safety beats everything: stop all runs and reject new ones until released. */
  freeze(on: boolean, now?: number) {
    if (on) this.stop({ all: true }, now);
    this.frozen = on;
  }

  running(): RunInfo[] {
    return RUN_LAYERS.flatMap((l) => (this.roots.has(l) ? [this.roots.get(l)!.info] : []));
  }

  queued(layer: RunLayer) { return this.queue.get(layer)?.id ?? null; }

  update(now: number) {
    this.now = now;
    for (const [layer, req] of [...this.queue]) {
      const cur = this.roots.get(layer);
      if (!cur || now >= cur.interruptibleAt) {
        this.queue.delete(layer);
        this.begin(req);
      }
    }
    for (const r of [...this.roots.values()]) {
      if (r.wait) {
        const sp = this.host.speechActive();
        if (sp) r.wait.saw = true;
        const held = now - r.wait.since;
        if ((r.wait.saw && !sp) || (!r.wait.saw && held >= this.waitGraceS) || held >= this.waitMaxS) {
          r.paused += now - r.pauseAt!;
          r.pauseAt = null;
          r.wait = null;
        } else {
          continue;
        }
      }
      this.step(r, r.node, r.local(now));
      if (r.node.done && !r.wait && now >= r.busyUntil && !(r.spoke && this.host.speechActive())) this.finish(r, 'done');
    }
  }

  // ------------------------------------------------------------------ internals

  private begin(req: Request) {
    const item = this.cat.get(req.id)!;
    const prev = this.roots.get(req.layer);
    if (prev) this.finish(prev, 'interrupted');
    const params = { intensity: clampIntensity(req.params.intensity ?? 1), speed: clampSpeed(req.params.speed ?? 1) };
    const info: RunInfo = {
      run_id: req.runId, id: item.id, kind: item.kind, source: req.source, layer: req.layer,
      owns: ownsUnion(item, this.cat), startedAt: this.now,
    };
    const node = this.makeNode(item, 0, 1, params.speed);
    const r = new Root(info, node, this.now, params, this.now + this.protectedFor(item, params.speed));
    this.roots.set(req.layer, r);
    this.host.started?.(info);
    this.step(r, node, 0);
  }

  private makeNode(item: ShowItem, startL: number, depth: number, speed: number) {
    const bpm = item.kind === 'sequence' ? (item.bpm ?? 120) : 120;
    const timeScale = item.kind === 'sequence' && item.clock === 'beat' ? 1 : speed;
    return new Node(item, startL, depth, timeScale, bpm);
  }

  /** Seconds a gesture root is protected by its clips' `interruptible_after`. */
  private protectedFor(item: ShowItem, speed: number): number {
    if (item.kind === 'sequence') return 0;
    const acts: { at: number; id: string; speed: number }[] = item.kind === 'clip'
      ? [{ at: 0, id: item.id, speed: 1 }]
      : item.actions.flatMap((a) => (a.do === 'clip' ? [{ at: a.at, id: a.id, speed: a.speed ?? 1 }] : []));
    let t = 0;
    for (const a of acts) {
      const c = this.cat.clip(a.id);
      if (c?.interruptible_after) t = Math.max(t, (a.at + c.interruptible_after / clampSpeed(a.speed)) / speed);
    }
    return t;
  }

  private step(r: Root, node: Node, L: number) {
    node.chase(L, this.host.liveBpm() ?? node.fallbackBpm);
    for (;;) {
      for (const it of node.items) {
        if (it.fired) continue;
        if (r.wait || it.at > node.pos(L) + 1e-9) break;
        it.fired = true;
        const Ls = node.timeOf(it.at);
        this.fire(r, node, it.e, Ls);
      }
      if (r.wait) break;
      if (node.loop && node.allFired && node.pos(L) >= node.length - 1e-9) {
        // Next lap, anchored exactly one length later.
        if (node.unit === 'beat') node.segB -= node.length;
        else node.startL += node.length / node.scale;
        node.items.forEach((i) => (i.fired = false));
        continue;
      }
      break;
    }
    for (const c of node.children) if (!r.wait) this.step(r, c, L);
    node.children = node.children.filter((c) => !c.done);
  }

  private fire(r: Root, node: Node, e: Entry['e'], Ls: number) {
    if ('ref' in e) {
      const child = this.cat.get(e.id);
      if (!child || child.kind !== e.ref || node.depth + 1 > MAX_DEPTH) {
        console.warn(`show: ${node.item.id} cannot nest ${e.ref} "${e.id}"`);
        return;
      }
      const c = this.makeNode(child, Ls, node.depth + 1, r.params.speed);
      node.children.push(c);
      this.step(r, c, Ls);
      return;
    }
    const a = e as Action;
    if (a.do === 'wait') {
      if (!this.host.speechActive() && this.waitGraceS === 0) return;
      r.pauseAt = r.clock(Ls);
      r.wait = { since: this.now, saw: this.host.speechActive() };
      return;
    }
    const n = normalizeAction(a)!;
    const at = r.clock(Ls);
    if (n.do === 'clip') {
      n.intensity = clampIntensity(n.intensity! * r.params.intensity);
      n.speed = clampSpeed(n.speed! * r.params.speed);
      const clip = this.cat.clip(n.id);
      if (clip) r.busyUntil = Math.max(r.busyUntil, at + clip.duration / n.speed);
    }
    if (n.do === 'speak') r.spoke = true;
    this.host.dispatch(n, { run: r.info, at });
  }

  private finish(r: Root, reason: EndReason) {
    if (this.roots.get(r.info.layer) !== r) return;
    this.roots.delete(r.info.layer);
    this.host.ended?.(r.info, reason);
  }
}

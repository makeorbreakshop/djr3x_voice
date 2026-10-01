// The animation library (Perform tab): every clip, cue, routine and intention, grouped the way
// R3X uses them, to play and watch. Progressive disclosure (2026-10-01: "a drop-down list, a
// play button or a loop, and you can drill into it"): pick a group from the dropdown; each row
// has ▶ and ↻; open a row to drill into it - an intention's variants (▶ on the intention plays
// a varied pick, as Claude and Jev fire it), a routine's timeline, a cue's actions. The row
// that is playing lights up. Names only; descriptions are on hover.

import './library.css';

export type LibraryDeps = {
  /** `{path: json}` as the performer loads it (show/loader.ts). */
  files: Record<string, string>;
  play: (id: string) => void;
  intend: (id: string) => void;
  stop: () => void;
  /** What is running now (polled while the tab is on screen). */
  running: () => { id: string; layer?: string }[];
  visible: () => boolean;
};

type Step = { at: number; clip?: string; cue?: string; sequence?: string; do?: string; id?: string; [k: string]: unknown };
type Item = {
  id: string;
  kind: 'clip' | 'cue' | 'sequence';
  title?: string;
  description?: string;
  tags?: string[];
  duration?: number;
  clock?: 'time' | 'beat';
  bpm?: number;
  loop?: boolean;
  length?: number;
  track?: Step[];
  actions?: Step[];
};
type Intention = { id: string; kind: string; description: string; pool: string[] };
type Pick = { id: string; weight: number };
type Idle = { after_s?: number; choices?: Pick[]; while_music?: Pick[] };
/** Something a row plays: an item, or an intention (a varied pick). */
type Target = { id: string; intend: boolean };

const GROUPS = [
  ['mood', 'Moods'],
  ['gesture', 'Gestures'],
  ['listen', 'Listening'],
  ['look', 'Looks'],
  ['dj', 'DJ & dance'],
  ['idle', 'Idle'],
  ['routines', 'Routines'],
  ['moments', 'Moments'],
  ['all', 'All'],
] as const;
type Group = (typeof GROUPS)[number][0];

const DANCE_TAGS = new Set(['dance', 'groove', 'bop', 'beat', 'dj', 'hype', 'drop', 'vibe', 'headbang']);

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls?: string, text?: string) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

const pretty = (id: string) => id.replace(/^listen_/, '').replace(/_/g, ' ');

export class Library {
  readonly root = el('div', 'lib');
  private items = new Map<string, Item>();
  private intentions: Intention[] = [];
  private idle: Idle = {};
  private group: Group = 'mood';
  private body = el('div', 'lib-body');
  private query = '';
  /** Item id -> rows showing it (lit while it runs). */
  private rows = new Map<string, HTMLElement[]>();
  private loopBtns: { t: Target; b: HTMLElement }[] = [];
  private loop: Target | null = null;
  private quietSince = 0;

  constructor(host: HTMLElement, private deps: LibraryDeps) {
    this.load();
    this.build();
    host.append(this.root);
    window.setInterval(() => this.tick(), 200);
  }

  private load() {
    for (const [path, text] of Object.entries(this.deps.files)) {
      try {
        const doc = JSON.parse(text);
        if (path.endsWith('intentions.json')) this.intentions = doc.intentions ?? [];
        else if (path.endsWith('idle.json')) this.idle = doc;
        else if (doc.id && doc.kind) this.items.set(doc.id, doc as Item);
      } catch {
        /* the performer reports bad files */
      }
    }
  }

  /** Seconds an item takes (a routine: its last step plus that step's own length). */
  private length(id: string, depth = 0): number {
    const it = this.items.get(id);
    if (!it || depth > 3) return 1;
    if (it.kind === 'clip') return it.duration ?? 1;
    const beat = it.clock === 'beat' ? 60 / (it.bpm ?? 120) : 1;
    if (it.kind === 'sequence' && it.length) return it.length * beat;
    let end = 0;
    for (const s of it.kind === 'cue' ? it.actions ?? [] : it.track ?? []) {
      const ref = this.ref(s);
      end = Math.max(end, s.at * beat + (ref ? this.length(ref, depth + 1) : 0.3));
    }
    return end || 1;
  }

  private ref(s: Step): string | undefined {
    return s.clip ?? s.cue ?? s.sequence ?? (s.do === 'clip' ? s.id : undefined);
  }

  private build() {
    const bar = el('div', 'lib-bar');
    const group = el('select', 'lib-group') as HTMLSelectElement;
    group.setAttribute('aria-label', 'Group');
    for (const [g, label] of GROUPS) group.add(new Option(label, g, false, g === this.group));
    group.onchange = () => {
      this.group = group.value as Group;
      this.render();
    };
    const stop = el('button', 'lib-btn', '■');
    stop.title = 'Stop everything';
    stop.onclick = () => {
      this.setLoop(null);
      this.deps.stop();
    };
    bar.append(group, stop);
    this.root.append(bar, this.body);
    this.render();
  }

  // ---------------------------------------------------------------- playing
  private fire(t: Target) {
    if (t.intend) this.deps.intend(t.id);
    else this.deps.play(t.id);
  }

  private setLoop(t: Target | null) {
    this.loop = t;
    this.quietSince = 0;
    for (const { t: bt, b } of this.loopBtns) b.classList.toggle('on', !!t && bt.id === t.id && bt.intend === t.intend);
    if (t) this.fire(t);
  }

  /** ▶ and ↻ for a row. */
  private controls(t: Target): HTMLElement {
    const box = el('span', 'lib-ctl');
    const play = el('button', 'lib-btn', '▶');
    play.title = t.intend ? 'Play a varied pick' : 'Play';
    play.onclick = (e) => {
      e.preventDefault();
      e.stopPropagation();
      this.fire(t);
    };
    const loop = el('button', 'lib-btn', '↻');
    loop.title = t.intend ? 'Loop: a new pick each time' : 'Loop';
    loop.onclick = (e) => {
      e.preventDefault();
      e.stopPropagation();
      const on = this.loop && this.loop.id === t.id && this.loop.intend === t.intend;
      this.setLoop(on ? null : t);
    };
    if (this.loop && this.loop.id === t.id && this.loop.intend === t.intend) loop.classList.add('on');
    this.loopBtns.push({ t, b: loop });
    box.append(play, loop);
    return box;
  }

  /** A row's head: kind mark, name, a small note, the controls. */
  private head(id: string, label: string, note: string, t: Target, title = ''): HTMLElement {
    const h = el('span', 'lib-head');
    const kind = t.intend ? 'intention' : this.items.get(id)?.kind ?? 'missing';
    h.append(el('span', `lib-mark ${kind}`), el('span', 'lib-nm', label), el('span', 'lib-note', note), this.controls(t));
    if (title) h.title = title;
    const list = this.rows.get(id) ?? [];
    list.push(h);
    this.rows.set(id, list);
    return h;
  }

  /** An item as a row; cues and routines open into what they are made of. */
  private itemRow(id: string, extra?: HTMLElement): HTMLElement {
    const it = this.items.get(id);
    const t: Target = { id, intend: false };
    const note = it ? `${this.length(id).toFixed(1)}s${it.loop ? ' ↻' : ''}` : '';
    const title = it?.description ?? it?.title ?? 'not in the show folder';
    const drill = it && (it.kind === 'sequence' || it.kind === 'cue');
    if (!drill) {
      const row = el('div', 'lib-item');
      row.append(this.head(id, pretty(id), note, t, title));
      if (extra) row.append(extra);
      return row;
    }
    const d = el('details', 'lib-item');
    const sum = el('summary');
    sum.append(this.head(id, pretty(id), note, t, title));
    d.append(sum);
    if (extra) d.append(extra);
    d.addEventListener('toggle', () => {
      if (d.open && !d.querySelector('.lib-timeline')) d.append(this.timeline(it!));
    });
    return d;
  }

  // ---------------------------------------------------------------- views
  private render() {
    this.rows.clear();
    this.loopBtns = [];
    this.body.replaceChildren();
    const g = this.group;
    const all = [...this.items.values()];
    if (g === 'mood' || g === 'gesture' || g === 'listen' || g === 'look') this.intentionRows(this.intentions.filter((i) => i.kind === g));
    else if (g === 'dj') this.djRows();
    else if (g === 'idle') this.idleRows();
    else if (g === 'routines') for (const it of all.filter((i) => i.kind === 'sequence')) this.body.append(this.itemRow(it.id));
    else if (g === 'moments') for (const it of all.filter((i) => i.kind === 'cue')) this.body.append(this.itemRow(it.id));
    else this.allRows();
  }

  private intentionRows(list: Intention[]) {
    for (const i of list) {
      const d = el('details', 'lib-item');
      const sum = el('summary');
      sum.append(this.head(i.id, pretty(i.id), String(i.pool.length), { id: i.id, intend: true }, i.description));
      const kids = el('div', 'lib-kids');
      for (const p of i.pool) kids.append(this.itemRow(p));
      d.append(sum, kids);
      this.body.append(d);
    }
  }

  private djRows() {
    const beats = this.intentions.filter((i) => i.kind === 'beat');
    this.intentionRows(beats);
    const inBeats = new Set(beats.flatMap((b) => b.pool));
    const all = [...this.items.values()];
    const loops = all.filter((i) => i.kind === 'sequence' && (i.loop || i.tags?.some((t) => DANCE_TAGS.has(t))));
    const moves = all.filter((i) => i.kind !== 'sequence' && !inBeats.has(i.id) && i.tags?.some((t) => DANCE_TAGS.has(t)));
    if (loops.length) this.body.append(el('div', 'lib-h', 'Loops & routines'));
    for (const it of loops) this.body.append(this.itemRow(it.id));
    if (moves.length) this.body.append(el('div', 'lib-h', 'Moves'));
    for (const it of moves) this.body.append(this.itemRow(it.id));
  }

  private idleRows() {
    const lane = (title: string, picks: Pick[] | undefined) => {
      if (!picks?.length) return;
      this.body.append(el('div', 'lib-h', title));
      const total = picks.reduce((a, p) => a + p.weight, 0) || 1;
      for (const p of picks) {
        const pct = Math.round((100 * p.weight) / total);
        const w = el('span', 'lib-wbar');
        const fill = el('i');
        fill.style.width = `${pct}%`;
        w.append(fill);
        w.title = `${pct}% of idle picks`;
        this.body.append(this.itemRow(p.id, w));
      }
    };
    lane(`After ${this.idle.after_s ?? '-'}s quiet`, this.idle.choices);
    lane('While music plays', this.idle.while_music);
  }

  /** What a cue or routine is made of, on a timeline: overlapping steps stack into lanes;
   *  a step that is a clip/cue/routine plays on tap. A loop is marked at the end. */
  private timeline(it: Item): HTMLElement {
    const beat = it.clock === 'beat' ? 60 / (it.bpm ?? 120) : 1;
    const total = this.length(it.id);
    const box = el('div', 'lib-timeline');
    const lanes: number[] = [];
    for (const s of it.kind === 'cue' ? it.actions ?? [] : it.track ?? []) {
      const ref = this.ref(s);
      const t = s.at * beat;
      const len = ref ? this.length(ref) : 0.25;
      let lane = lanes.findIndex((end) => end <= t + 1e-6);
      if (lane < 0) lane = lanes.push(0) - 1;
      lanes[lane] = t + len;
      const block = el(ref ? 'button' : 'span', `lib-block ${ref ? this.items.get(ref)?.kind ?? 'missing' : 'action'}`, ref ? pretty(ref) : String(s.do ?? '·'));
      block.title = ref
        ? `${this.items.get(ref)?.description ?? ref} · at ${t.toFixed(1)}s`
        : Object.entries(s).filter(([k]) => k !== 'at').map(([k, v]) => `${k}: ${v}`).join(' · ');
      if (ref) {
        block.onclick = () => this.fire({ id: ref, intend: false });
        const list = this.rows.get(ref) ?? [];
        list.push(block);
        this.rows.set(ref, list);
      }
      block.style.left = `${(100 * t) / total}%`;
      block.style.width = `${Math.max(4, (100 * len) / total)}%`;
      block.style.top = `${lane * 20}px`;
      box.append(block);
    }
    box.style.height = `${Math.max(1, lanes.length) * 20}px`;
    if (it.loop) box.classList.add('loops');
    return box;
  }

  private allRows() {
    const find = el('input', 'lib-find') as HTMLInputElement;
    find.type = 'search';
    find.placeholder = 'Find';
    find.value = this.query;
    const list = el('div', 'lib-kids flat');
    const fill = () => {
      this.rows.clear();
      this.loopBtns = [];
      list.replaceChildren();
      const q = this.query.toLowerCase();
      for (const kind of ['sequence', 'cue', 'clip'] as const) {
        for (const it of [...this.items.values()].filter((i) => i.kind === kind)) {
          if (q && !it.id.includes(q) && !(it.tags ?? []).some((t) => t.includes(q))) continue;
          list.append(this.itemRow(it.id));
        }
      }
    };
    find.oninput = () => {
      this.query = find.value.trim();
      fill();
    };
    this.body.append(find, list);
    fill();
  }

  // ---------------------------------------------------------------- live state
  private tick() {
    if (!this.deps.visible()) return;
    const running = this.deps.running();
    const ids = new Set(running.map((r) => r.id));
    for (const [id, list] of this.rows) for (const r of list) r.classList.toggle('playing', ids.has(id));
    // Loop: when nothing is running, go again (an intention re-picks each time).
    if (this.loop) {
      const t = performance.now() / 1000;
      if (running.length) this.quietSince = 0;
      else if (!this.quietSince) this.quietSince = t;
      else if (t - this.quietSince > 0.3) {
        this.quietSince = 0;
        this.fire(this.loop);
      }
    }
  }
}

/**
 * Instructions: Build's assembly guide, full screen. Modelled on the kit's printed guide - a title
 * page per sub-assembly, then steps: the model large, what the step adds in the accent with dashed
 * paths to where it seats, the step's name and the hardware by part number - but driven by the real
 * model, so each step plays (parts in one at a time, then their screws driven in: sequence.ts), and
 * the builder can orbit, scrub, and investigate any part.
 *
 * The page: the 3D view is the picture (workbench.ts draws the section alone, frames it in the area
 * this page leaves), a text column on the right (below the model on narrow screens), a slim bar
 * under the model (previous, the scrubber with a tick per section, next), Contents on the left.
 *
 * Copy: no sentences yet - each step is its title (the manifest's: authored, or named by
 * mech/workbench/steps.py after the group it adds, marked derived), its hardware and its parts.
 * Nothing here quotes the kit guide.
 */

import './guide.css';
import type { AsmNode, AStep, Look, Workbench } from './workbench';
import { assemblyLabel, libraryFrom, type LibraryItem } from './systems';
import type { MBomLine, MFastener, MPart, MSpec, MUnplaced } from './manifest';

const esc = (s: unknown) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!);
const LOOKS: [Look, string][] = [['exterior', 'Exterior'], ['mechanism', 'Mechanism'], ['inspect', 'X-ray']];
/** Narrower than this, the text goes below the model. */
const STACK_AT = 900;

export interface Section {
  node: AsmNode;
  title: string;
  /** Whose design it is ("Ours, after Jason Charlton's lift and pan"), and where it comes from. */
  by: string;
  source: string;
  look: Look | null;
  steps: AStep[];
  parts: number;
  fasteners: number;
}

/** One stop in the guide: a section's title card (step -1) or one of its steps. */
interface Stop { sec: number; step: number }

// ------------------------------------------------------------------ copy (pure; tested)

/** "V-wheel (OpenBuilds solid), front left lower" -> "V-wheel"; "Servo hub 1906, 25T (L)" -> "Servo hub 1906". */
export function baseName(name: string): string {
  return name.replace(/\s*\([^)]*\)\s*/g, ' ').split(/\s*,\s*/)[0].replace(/\s*#\d+$/, '').replace(/\s+-\s*x\d+$/i, '')
    .replace(/\s+(L|R)$/, '').replace(/\s+/g, ' ').trim() || name;
}

/** What a piece of hardware is, in a word, and the verb that puts it in. */
export function hardwareNoun(spec: MSpec | undefined): { noun: string; verb: string } {
  const t = `${spec?.type ?? ''} ${String(spec?.desc ?? '')}`.toLowerCase();
  if (/insert/.test(t)) return { noun: 'insert', verb: 'Heat-set' };
  if (/magnet/.test(t)) return { noun: 'magnet', verb: 'Glue' };
  if (/standoff/.test(t)) return { noun: 'standoff', verb: 'Screw' };
  if (/washer/.test(t)) return { noun: 'washer', verb: 'Fit' };
  if (/nut/.test(t)) return { noun: 'nut', verb: 'Fit' };
  if (/pin|dowel/.test(t)) return { noun: 'pin', verb: 'Press' };
  if (/bearing/.test(t)) return { noun: 'bearing', verb: 'Press' };
  if (/rod/.test(t)) return { noun: 'rod', verb: 'Fit' };
  if (/screw|shcs|bhcs|fhcs|bolt|set_screw|grub/.test(t)) return { noun: 'screw', verb: 'Screw' };
  return { noun: 'part', verb: 'Fit' };
}

/** One line of a step's hardware list. */
export interface HwLine { qty: number | null; label: string; partNo: string; ids: string[]; spec?: MSpec }

const TYPE_NAME: Record<string, string> = {
  shcs: 'socket head cap screw', bhcs: 'button head screw', fhcs: 'flat head screw', insert: 'heat-set insert',
  lock_nut: 'lock nut', nut: 'hex nut', washer: 'washer', threaded_rod: 'threaded rod', pin: 'pin', t_nut: 'drop-in T-nut',
  set_screw: 'set screw', grub: 'grub screw',
};

/** "M5 × 20 socket head cap screw", or the catalogue's own description. */
export function hardwareLabel(spec: MSpec | undefined): string {
  if (!spec) return 'hardware';
  if (spec.desc) return String(spec.desc).replace(/,\s*/g, ', ');
  const len = spec.length_mm && spec.type !== 'insert' && spec.type !== 't_nut' ? ` × ${+Number(spec.length_mm).toFixed(1)}` : '';
  const size = spec.thread ? `${spec.thread}${len} ` : spec.length_mm ? `${+Number(spec.length_mm).toFixed(1)} mm ` : '';
  return `${size}${TYPE_NAME[spec.type] ?? spec.type.replace(/_/g, ' ')}`.trim();
}

/** A catalogue number to fetch it by: McMaster or goBILDA, when the spec has one. */
export function partNumber(spec: MSpec | undefined): string {
  const m = spec?.mcmaster ?? (spec?.gobilda as string | undefined) ?? (spec?.part as string | undefined);
  return m ? String(m) : '';
}

/** A step's hardware: its placed fasteners grouped by kind, then what the source lists but the model does not place. */
export function hardwareLines(fasteners: MFastener[], unplaced: MUnplaced[] = []): HwLine[] {
  const by = new Map<string, HwLine>();
  for (const f of fasteners) {
    const k = f.key || hardwareLabel(f.spec);
    const line = by.get(k) ?? { qty: 0, label: hardwareLabel(f.spec), partNo: partNumber(f.spec), ids: [], spec: f.spec };
    line.qty = (line.qty ?? 0) + 1;
    line.ids.push(f.id);
    by.set(k, line);
  }
  // what goes in first reads first: inserts, then screws, then what holds them (nuts, washers)
  const rank = (h: HwLine) => ['insert', 'standoff', 'screw', 'rod', 'pin', 'bearing', 'magnet', 'nut', 'washer'].indexOf(hardwareNoun(h.spec).noun);
  const out = [...by.values()].sort((a, b) => (rank(a) + 1 || 99) - (rank(b) + 1 || 99));
  for (const u of unplaced) {
    if (u.count === 0) continue;
    out.push({ qty: u.count ?? null, label: hardwareLabel(u.spec), partNo: partNumber(u.spec), ids: [], spec: u.spec });
  }
  return out;
}

/** The new parts of a step, one entry per kind ("8 × V-wheel"). */
export function partGroups(ids: string[], nameOf: (id: string) => string): { name: string; ids: string[] }[] {
  const by = new Map<string, string[]>();
  for (const id of ids) {
    const k = baseName(nameOf(id));
    (by.get(k) ?? by.set(k, []).get(k)!).push(id);
  }
  return [...by].map(([name, ids2]) => ({ name, ids: ids2 }));
}

/** "Ours (after Jason Charlton's lift and pan)" -> "Ours, after Jason Charlton's lift and pan". */
export function byLine(author: string): string {
  return author.replace(/\s*\(([^)]*)\)\s*$/, ', $1').trim();
}

// ------------------------------------------------------------------ the page

export function injectGuideDom() {
  if (document.getElementById('guide')) return;
  const t = document.createElement('template');
  t.innerHTML = `<div id="guide" class="gd" role="region" aria-label="Instructions" hidden>
    <button id="gd-toc-open" class="gd-chip gd-toc-open" aria-expanded="false" aria-controls="gd-toc">
      <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 4h10M3 8h10M3 12h7"/></svg>Contents</button>
    <div class="gd-looks" role="radiogroup" aria-label="Look">${LOOKS.map(([l, n]) => `<button role="radio" data-gd-look="${l}" aria-checked="false">${n}</button>`).join('')}</div>
    <nav id="gd-toc" class="gd-toc" aria-label="Contents" hidden>
      <div class="gd-toc-head"><b>Contents</b><button class="gd-x" data-gd="toc-close" aria-label="Close contents">
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 4 8 8M12 4l-8 8"/></svg></button></div>
      <div id="gd-toc-list"></div>
    </nav>
    <article id="gd-col" class="gd-col" tabindex="-1">
      <div class="gd-col-head"><span id="gd-sec" class="gd-sec"></span>
        <button class="gd-done" data-gd="done" title="Back to Build (Esc)">Done</button></div>
      <div id="gd-body" class="gd-body" aria-live="polite"></div>
    </article>
    <div class="gd-bar">
      <button class="gd-nav" data-gd="prev" aria-label="Previous step" title="Previous (←)"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M10 3 5 8l5 5"/></svg></button>
      <div class="gd-scrub"><input id="gd-scrub" type="range" min="0" max="0" step="1" value="0" aria-label="Position in the instructions"><div id="gd-ticks" class="gd-ticks" aria-hidden="true"></div></div>
      <button class="gd-nav" data-gd="next" aria-label="Next step" title="Next (→)"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="m6 3 5 5-5 5"/></svg></button>
    </div>
  </div>`;
  document.body.append(t.content);
}

export interface Guide {
  open(at?: AsmNode | null): void;
  close(): void;
  readonly active: boolean;
  /** The section that covers a node (itself, else the nearest one under or above it). */
  sectionFor(node: AsmNode | null): AsmNode | null;
}

export function mountGuide(wb: Workbench, opts: { onClose?: () => void } = {}): Guide {
  injectGuideDom();
  const root = document.getElementById('guide')!;
  const body = document.getElementById('gd-body')!;
  const toc = document.getElementById('gd-toc')!;
  const tocList = document.getElementById('gd-toc-list')!;
  const tocBtn = document.getElementById('gd-toc-open')!;
  const scrub = document.getElementById('gd-scrub') as HTMLInputElement;
  const ticks = document.getElementById('gd-ticks')!;
  const col = document.getElementById('gd-col')!;

  let sections: Section[] = [];
  let stops: Stop[] = [];
  let at = 0;
  /** The section the workbench has open (its steps are `wb.steps`). */
  let openSec = -1;
  /** The look the builder picked in the guide (null: each section's own). */
  let pickedLook: Look | null = null;
  /** Build as it was, put back on Done. */
  let saved: { scope: Workbench['scope']; look: Look; explode: number; cam: ReturnType<Workbench['cameraState']> } | null = null;
  /** The part or fastener inspected (from the tray or a click in the model). */
  let inspecting: string | null = null;
  let lastSel: string | null = null;

  const nameOf = (id: string) => wb.partInfo(id)?.part.name ?? fastOf(id)?.spec.type ?? id;
  const fastOf = (id: string): MFastener | null => wb.fastenerInfo(id) ?? null;

  // ---------------------------------------------------------------- structure
  function designFor(node: AsmNode): LibraryItem | null {
    return libraryFrom(wb.manifest?.root).find((d) => d.nodes.includes(node.asm.id)) ?? null;
  }

  function buildSections() {
    sections = [];
    wb.forEachNode((n) => {
      if (!wb.nodeShown(n)) return;
      const parts = wb.sectionParts(n);
      if (!parts.size) return;
      const steps = wb.assembly({ kind: 'assembly', id: `guide:${n.key}`, parts }).list;
      if (!steps.length) return;
      const d = designFor(n);
      // a design that is this one assembly names it ("Central column", "Head gimbal"); else the assembly's own name
      const title = d && d.nodes.length === 1 ? d.name : assemblyLabel(n.asm.name);
      const fasteners = steps.reduce((k, s) => k + (s.fasteners?.length ?? 0), 0);
      sections.push({
        node: n, title, by: d ? byLine(d.by) : '', source: d?.source && !d.source.includes('/') ? d.source : '',
        look: (d?.look as Look | undefined) ?? null, steps, parts: parts.size, fasteners,
      });
    });
    stops = sections.flatMap((s, i) => [{ sec: i, step: -1 }, ...s.steps.map((_, k) => ({ sec: i, step: k }))]);
  }

  function sectionFor(node: AsmNode | null): AsmNode | null {
    if (!sections.length) buildSections();
    if (!node) return sections[0]?.node ?? null;
    const own = sections.find((s) => s.node === node);
    if (own) return own.node;
    // the first section under it, else the nearest above it
    let under: AsmNode | null = null;
    wb.forEachNode((n) => { if (!under && sections.some((s) => s.node === n)) under = n; }, node);
    if (under) return under;
    for (let p = node.parent; p; p = p.parent) if (sections.some((s) => s.node === p)) return p;
    return sections[0]?.node ?? null;
  }

  // ---------------------------------------------------------------- open / close
  function open(atNode: AsmNode | null = null) {
    if (!wb.top) return;
    buildSections();
    if (!stops.length) return;
    if (!saved) saved = { scope: wb.scope, look: wb.look, explode: wb.explode, cam: wb.cameraState() };
    wb.explode = 0;
    document.body.dataset.guide = '1';
    root.hidden = false;
    layout();
    const node = sectionFor(atNode);
    const i = Math.max(0, stops.findIndex((s) => sections[s.sec].node === node && s.step === -1));
    openSec = -1;
    go(i, false);
    col.focus({ preventScroll: true });
  }

  function close() {
    if (!saved) return;
    closeToc();
    inspecting = null;
    wb.setGuide(null);
    root.hidden = true;
    delete document.body.dataset.guide;
    const s = saved;
    saved = null;
    openSec = -1;
    wb.explode = s.explode;
    wb.setScope(s.scope);
    wb.setLook(s.look);
    wb.setCameraState(s.cam);
    // main.ts fits the view between Build's panels again
    dispatchEvent(new Event('resize'));
    opts.onClose?.();
  }

  // ---------------------------------------------------------------- moving
  function go(i: number, animate = true) {
    i = Math.max(0, Math.min(stops.length - 1, i));
    const st = stops[i];
    const sec = sections[st.sec];
    at = i;
    inspecting = null;
    if (openSec !== st.sec) {
      openSec = st.sec;
      wb.setGuide(sec.node);
      wb.setLook(pickedLook ?? sec.look ?? wb.look);
    }
    wb.setGuideStep(st.step, animate);
    wb.frameGuide(animate);
    render();
  }

  // ---------------------------------------------------------------- layout
  function layout() {
    const narrow = innerWidth < STACK_AT;
    root.classList.toggle('stacked', narrow);
    const bar = root.querySelector<HTMLElement>('.gd-bar')!;
    if (narrow) wb.guideRect = { right: 0, bottom: col.offsetHeight + bar.offsetHeight, top: 52 };
    else wb.guideRect = { right: col.offsetWidth, bottom: bar.offsetHeight, top: 52 };
  }
  addEventListener('resize', () => {
    if (!saved) return;
    layout();
  });

  // ---------------------------------------------------------------- render
  function render() {
    if (!saved) return;
    const st = stops[at];
    const sec = sections[st.sec];
    const steps = sec.steps;
    // the section's name heads the column on its steps (the title card has it large)
    document.getElementById('gd-sec')!.textContent = st.step < 0 && !inspecting ? '' : sec.title;
    for (const b of root.querySelectorAll<HTMLButtonElement>('[data-gd-look]')) {
      const on = b.dataset.gdLook === wb.look;
      b.setAttribute('aria-checked', String(on));
      b.tabIndex = on ? 0 : -1;
    }
    root.querySelector<HTMLButtonElement>('[data-gd="prev"]')!.disabled = at === 0;
    root.querySelector<HTMLButtonElement>('[data-gd="next"]')!.disabled = at >= stops.length - 1;
    scrub.max = String(stops.length - 1);
    if (document.activeElement !== scrub) scrub.value = String(at);
    scrub.setAttribute('aria-valuetext', st.step < 0 ? `${sec.title}, title` : `${sec.title}, step ${st.step + 1} of ${steps.length}`);
    renderTicks();
    if (inspecting) body.innerHTML = inspectHtml(inspecting, sec, st);
    else if (st.step < 0) body.innerHTML = titleHtml(sec);
    else body.innerHTML = stepHtml(sec, st.step);
    syncPlay();
    hydrateThumbs();
    if (!toc.hidden) renderToc();
  }

  function renderTicks() {
    const sig = `${stops.length}|${sections.length}`;
    if (ticks.dataset.sig === sig) return;
    ticks.dataset.sig = sig;
    const n = Math.max(1, stops.length - 1);
    ticks.innerHTML = stops.map((s, i) => (s.step < 0 ? `<i style="left:${((i / n) * 100).toFixed(3)}%" title="${esc(sections[s.sec].title)}"></i>` : '')).join('');
  }

  function titleHtml(sec: Section) {
    const titles = sec.steps.map((s) => s.title);
    return `<div class="gd-title">
      <h1>${esc(sec.title)}</h1>
      ${sec.by ? `<p class="gd-by">${esc(sec.by)}</p>` : ''}
      ${sec.source ? `<p class="gd-src">${esc(sec.source)}</p>` : ''}
      <p class="gd-facts">${sec.steps.length} step${sec.steps.length === 1 ? '' : 's'} · ${sec.parts} parts${sec.fasteners ? ` · ${sec.fasteners} fasteners` : ''}</p>
      <button class="gd-start" data-gd="next">Start</button>
      <ol class="gd-steps">${titles.map((t, k) => `<li><button data-gd="step" data-i="${k}"><span>${k + 1}</span>${esc(t)}</button></li>`).join('')}</ol>
    </div>`;
  }

  function hwOf(s: AStep): HwLine[] {
    return hardwareLines((s.fasteners ?? []).map(fastOf).filter((f): f is MFastener => !!f), s.unplaced ?? []);
  }

  function stepHtml(sec: Section, k: number) {
    const s = sec.steps[k];
    const hw = hwOf(s);
    const groups = partGroups(s.parts ?? [], nameOf);
    const tray = [
      ...groups.map((g) => ({ ids: g.ids, label: g.name, qty: g.ids.length })),
      ...hw.filter((h) => h.ids.length).map((h) => ({ ids: h.ids, label: h.label, qty: h.ids.length })),
    ];
    const derived = s.derived ? '<span class="gd-derived" title="No authored step for these parts: ordered from the structure (the frame first, then what hangs on it)">derived</span>' : '';
    const inferred = !s.derived && s.inferred ? `<span class="gd-derived" title="${esc(s.inferred_note || 'Not confirmed by the source')}">unconfirmed</span>` : '';
    return `<div class="gd-step">
      <p class="gd-num"><b>${k + 1}</b><span>of ${sec.steps.length}</span>${derived}${inferred}</p>
      <h1 class="gd-text">${esc(s.title)}</h1>
      <div class="gd-play" role="group" aria-label="This step's animation">
        <button class="gd-pb" data-gd="play" aria-label="Play">${PLAY}</button>
        <input id="gd-seq" type="range" min="0" max="1000" step="1" value="0" aria-label="Through this step">
        <button class="gd-pb" data-gd="replay" aria-label="Replay this step" title="Replay (R)"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8a5 5 0 1 0 1.5-3.6M3 2.5V5h2.5"/></svg></button>
        <button class="gd-sp" data-gd="speed" aria-label="Speed" title="Speed">1×</button>
      </div>
      ${hw.length ? `<h2 class="gd-h">Hardware</h2><ul class="gd-hw">${hw.map((h) => `<li${h.ids.length ? ` data-ids="${esc(h.ids.join(' '))}"` : ''}>
        <span class="q">${h.qty ? `${h.qty} ×` : ''}</span><span class="l">${esc(h.label)}</span><span class="pn">${esc(h.partNo)}</span></li>`).join('')}</ul>` : ''}
      ${tray.length ? `<h2 class="gd-h">In this step</h2><ul class="gd-tray">${tray.map((t) => `<li><button data-gd="inspect" data-ids="${esc(t.ids.join(' '))}" title="${esc(t.label)}">
        <img data-thumb="${esc(t.ids[0])}" alt="" width="64" height="64"><span class="tq">${t.qty > 1 ? `${t.qty} ×` : ''}</span><span class="tl">${esc(t.label)}</span></button></li>`).join('')}</ul>` : ''}
    </div>`;
  }

  /** The transport follows the step playing (every frame while it runs: only these few attributes change). */
  function syncPlay() {
    const q = wb.seqState;
    const bar = body.querySelector<HTMLElement>('.gd-play');
    if (!bar) return;
    bar.hidden = !q || q.items === 0;
    if (!q) return;
    const b = bar.querySelector<HTMLButtonElement>('[data-gd="play"]')!;
    const want = q.playing ? 'Pause' : 'Play';
    if (b.getAttribute('aria-label') !== want) {
      b.setAttribute('aria-label', want);
      b.innerHTML = q.playing ? PAUSE : PLAY;
    }
    const r = bar.querySelector<HTMLInputElement>('#gd-seq')!;
    if (document.activeElement !== r) r.value = String(Math.round(q.total ? (q.t / q.total) * 1000 : 1000));
    r.setAttribute('aria-valuetext', `${Math.round(q.total ? (q.t / q.total) * 100 : 100)}%`);
    const sp = bar.querySelector<HTMLButtonElement>('[data-gd="speed"]')!;
    sp.textContent = `${q.speed}×`;
    sp.setAttribute('aria-pressed', String(q.speed > 1));
  }
  wb.onSeq(syncPlay);

  // ---------------------------------------------------------------- inspect
  function inspectHtml(id: string, sec: Section, st: Stop) {
    const back = st.step < 0 ? 'Back to the title' : `Back to step ${st.step + 1}`;
    const head = `<button class="gd-back" data-gd="uninspect"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M10 3 5 8l5 5"/></svg>${back}</button>`;
    const row = (k: string, v: string) => (v ? `<dt>${k}</dt><dd>${v}</dd>` : '');
    const info = wb.partInfo(id);
    if (!info) {
      const f = fastOf(id);
      if (!f) return head;
      const same = allFasteners().filter((x) => x.key === f.key);
      return `${head}<div class="gd-insp">
        <img class="gd-insp-img" data-thumb="${esc(id)}" alt="" width="128" height="128">
        <h2>${esc(hardwareLabel(f.spec))}</h2>
        <dl>${row('Part no.', esc(partNumber(f.spec)))}
          ${row('Joins', f.joins.map((p) => esc(baseName(nameOf(p)))).join(' + '))}
          ${row('Used', `${same.length} in the build`)}</dl></div>`;
    }
    const p = info.part;
    const size = partSize(id);
    const bom = bomLine(p.id);
    const twins = bom?.parts?.length ? bom.parts : sameMesh(p);
    const where = whereUsed(twins);
    return `${head}<div class="gd-insp">
      <img class="gd-insp-img" data-thumb="${esc(id)}" alt="" width="128" height="128">
      <h2>${esc(baseName(p.name))}</h2>
      ${p.name !== baseName(p.name) ? `<p class="gd-full">${esc(p.name)}</p>` : ''}
      <dl>
        ${row('Source', esc(sourceText(p)))}
        ${row('Material', esc(materialText(p)))}
        ${row('Finish', esc(finishText(p)))}
        ${row('Size', size)}
        ${row('BOM', bom ? `${bom.qty ? `${+bom.qty} × ` : ''}${bom.source ? `<a href="${esc(bom.source)}" target="_blank" rel="noopener">${esc(bom.item)}</a>` : esc(bom.item)}` : '')}
        ${row('Used', `${twins.length > 1 ? `${twins.length} in the build` : 'once'}${where.length ? `: ${where.map((w) => `<button class="gd-link" data-gd="goto" data-i="${w.i}">${esc(w.label)}</button>`).join(', ')}` : ''}`)}
      </dl>
      ${p.inferred ? `<p class="gd-inf">Unconfirmed: ${esc(p.inferred_note || 'placement or part')}</p>` : ''}
    </div>`;
  }

  function allFasteners(): MFastener[] {
    const out: MFastener[] = [];
    wb.forEachNode((n) => { if (wb.nodeShown(n)) out.push(...(n.asm.fasteners ?? []).filter((f) => f.placed)); });
    return out;
  }

  function partSize(id: string) {
    const b = wb.partInfo(id)?.part.bbox;
    if (!b) return '';
    const d = [0, 1, 2].map((k) => Math.abs(b[1][k] - b[0][k])).sort((x, y) => y - x);
    const f = (v: number) => (v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1).replace(/\.0$/, '') : v.toFixed(1));
    return `${d.map(f).join(' × ')} mm`;
  }

  function bomLine(pid: string): MBomLine | null {
    let hit: MBomLine | null = null;
    wb.forEachNode((n) => {
      if (hit) return;
      hit = (n.asm.bom ?? []).find((b) => b.parts?.includes(pid)) ?? null;
    });
    return hit;
  }

  function sameMesh(p: MPart): string[] {
    const file = (p.mesh ?? '').split('/').pop();
    const out: string[] = [];
    wb.forEachNode((n) => {
      if (!wb.nodeShown(n)) return;
      for (const q of n.asm.parts) if (!q.replaced_by && (q.mesh ?? '').split('/').pop() === file) out.push(q.id);
    });
    return out.length ? out : [p.id];
  }

  /** The stops that place any of these parts: "Central column 9". */
  function whereUsed(ids: string[]): { i: number; label: string }[] {
    const want = new Set(ids);
    const out: { i: number; label: string }[] = [];
    stops.forEach((s, i) => {
      if (s.step < 0) return;
      const step = sections[s.sec].steps[s.step];
      if ((step.parts ?? []).some((p) => want.has(p))) out.push({ i, label: `${sections[s.sec].title} ${s.step + 1}` });
    });
    return out.slice(0, 8);
  }

  function sourceText(p: MPart) {
    const c = p.cad;
    if (c === 'parametric') return 'Parametric: our model';
    if (c === 'vendor') return "Vendor CAD: the maker's model";
    if (c === 'placeholder') return 'Placeholder: a sized box';
    const origin = (p.source as { origin?: string } | undefined)?.origin;
    return origin === 'kit' ? 'Kit print file (mesh)' : 'Mesh: the source STL';
  }
  function materialText(p: MPart) {
    const pr = p.finish?.print;
    if (p.printed && pr) return `${pr.filament}${pr.color_name ? `, ${pr.color_name}` : ''}`;
    return p.material ?? '';
  }
  function finishText(p: MPart) {
    const paint = p.finish?.paint;
    if (paint && paint !== 'none') return `Painted ${paint.replace(/^paint_/, '').replace(/_/g, ' ')}`;
    if (paint === 'none') return 'Bare';
    return p.finish?.color_name ?? '';
  }

  function inspect(ids: string[]) {
    if (!ids.length) return;
    inspecting = ids[0];
    lastSel = ids[0];
    wb.setGuideHover(null);
    wb.select(ids[0]);
    wb.isolate(ids);
    render();
    body.querySelector<HTMLElement>('.gd-back')?.focus();
  }

  function uninspect() {
    if (!inspecting) return;
    inspecting = null;
    lastSel = null;
    wb.isolate(null);
    wb.select(null);
    wb.frameGuide(true);
    render();
  }

  // ---------------------------------------------------------------- contents
  function renderToc() {
    const st = stops[at];
    tocList.innerHTML = sections.map((sec, i) => {
      const first = stops.findIndex((s) => s.sec === i && s.step === -1);
      const here = st.sec === i;
      return `<details class="gd-toc-sec"${here ? ' open' : ''}><summary><span>${esc(sec.title)}</span><small>${sec.steps.length}</small></summary>
        <ol>
          <li><button data-gd="goto" data-i="${first}" ${here && st.step < 0 ? 'aria-current="step"' : ''}><span></span>Overview</button></li>
          ${sec.steps.map((s, k) => `<li><button data-gd="goto" data-i="${first + 1 + k}" ${here && st.step === k ? 'aria-current="step"' : ''}>
            <span>${k + 1}</span>${esc(s.title)}${s.derived ? ' <i class="gd-d" title="Derived from the structure">derived</i>' : ''}</button></li>`).join('')}
        </ol></details>`;
    }).join('');
    tocList.querySelector('[aria-current="step"]')?.scrollIntoView({ block: 'center' });
  }
  function openToc() {
    toc.hidden = false;
    tocBtn.setAttribute('aria-expanded', 'true');
    renderToc();
    (tocList.querySelector<HTMLElement>('[aria-current="step"]') ?? tocList.querySelector<HTMLElement>('button'))?.focus();
  }
  function closeToc() {
    if (toc.hidden) return;
    toc.hidden = true;
    tocBtn.setAttribute('aria-expanded', 'false');
  }

  // ---------------------------------------------------------------- thumbnails
  function hydrateThumbs() {
    const imgs = [...root.querySelectorAll<HTMLImageElement>('img[data-thumb]')];
    if (!imgs.length) return;
    // a frame later: the step's view first, then the small pictures
    requestAnimationFrame(() => {
      for (const img of imgs) {
        if (!img.isConnected) continue;
        const url = wb.thumbnail(img.dataset.thumb!, img.width > 100 ? 160 : 96);
        if (url) img.src = url;
        else img.classList.add('none');
      }
    });
  }

  // ---------------------------------------------------------------- input
  root.addEventListener('click', (e) => {
    const el = (e.target as HTMLElement).closest<HTMLElement>('[data-gd], [data-gd-look]');
    if (!el) return;
    if (el.dataset.gdLook) {
      pickedLook = el.dataset.gdLook as Look;
      wb.setLook(pickedLook);
      render();
      return;
    }
    switch (el.dataset.gd) {
      case 'prev': go(at - 1); break;
      case 'next': go(at + 1); break;
      case 'done': close(); break;
      case 'step': go(at + 1 + Number(el.dataset.i)); break;
      case 'goto': closeToc(); go(Number(el.dataset.i)); break;
      case 'inspect': inspect(el.dataset.ids!.split(' ')); break;
      case 'play': wb.seqPlay(!wb.seqState?.playing); break;
      case 'replay': wb.seqReplay(); break;
      case 'speed': wb.setSeqSpeed(wb.seqSpeed > 1 ? 1 : 2); break;
      case 'uninspect': uninspect(); break;
      case 'toc-close': closeToc(); tocBtn.focus(); break;
    }
  });
  tocBtn.addEventListener('click', () => (toc.hidden ? openToc() : closeToc()));
  scrub.addEventListener('input', () => go(Number(scrub.value), false));
  body.addEventListener('input', (e) => {
    const r = e.target as HTMLInputElement;
    if (r.id === 'gd-seq') wb.seqScrub(Number(r.value) / 1000);
  });
  // tray and hardware rows: the pointer on one marks it in the model
  const hoverIds = (e: Event) => (e.target as HTMLElement).closest<HTMLElement>('[data-ids]')?.dataset.ids?.split(' ').filter(Boolean) ?? null;
  body.addEventListener('pointerover', (e) => { if (!inspecting) wb.setGuideHover(hoverIds(e)); });
  body.addEventListener('pointerleave', () => { if (!inspecting) wb.setGuideHover(null); });
  body.addEventListener('focusin', (e) => { if (!inspecting) wb.setGuideHover(hoverIds(e)); });
  body.addEventListener('focusout', () => { if (!inspecting) wb.setGuideHover(null); });

  // Keys while the guide is open: before Build's own (capture), so Esc and the arrows are the guide's.
  addEventListener('keydown', (e) => {
    if (!saved || e.metaKey || e.ctrlKey || e.altKey) return;
    const t = e.target as HTMLElement;
    const typing = (t.tagName === 'INPUT' && (t as HTMLInputElement).type !== 'range') || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT';
    if (typing) return;
    let used = true;
    if (e.key === 'Escape') {
      if (inspecting) uninspect();
      else if (!toc.hidden) { closeToc(); tocBtn.focus(); }
      else close();
    } else if (t === scrub && (e.key.startsWith('Arrow') || e.key === 'Home' || e.key === 'End')) {
      used = false; // the slider moves itself (its input event steps)
    } else if (t.id === 'gd-seq' && e.key.startsWith('Arrow')) {
      used = false; // the step's own slider
    } else if (e.key === 'ArrowRight' || e.key === 'PageDown') {
      wb.seqFinish(); // the step as it ends, then the next
      go(at + 1);
    } else if (e.key === ' ' && t.tagName !== 'BUTTON') wb.seqPlay(!wb.seqState?.playing);
    else if (e.key === 'r' || e.key === 'R') wb.seqReplay();
    else if (e.key === 'ArrowLeft' || e.key === 'PageUp') go(at - 1);
    else if (e.key === 'Home') go(stops.findIndex((s) => s.sec === stops[at].sec));
    else if (e.key === 'f' || e.key === 'F') wb.frameGuide(true);
    else if (['1', '2', '3'].includes(e.key)) {
      pickedLook = LOOKS[Number(e.key) - 1][0];
      wb.setLook(pickedLook);
      render();
    } else if (e.key === 'c' || e.key === 'C') { if (toc.hidden) openToc(); else closeToc(); }
    else used = false;
    if (used) {
      e.preventDefault();
      e.stopImmediatePropagation();
    }
  }, { capture: true });

  // A part picked in the model (workbench.pick) is inspected too.
  wb.onChange(() => {
    if (!saved) return;
    const sel = wb.selected;
    if (sel === lastSel) return;
    lastSel = sel;
    if (sel && sel !== inspecting) {
      inspecting = sel;
      render();
    } else if (!sel && inspecting) {
      inspecting = null;
      wb.isolate(null);
      render();
    }
  });

  return {
    open, close, sectionFor,
    get active() { return !!saved; },
  };
}

const PLAY = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M5 3.5v9l7.5-4.5z" fill="currentColor" stroke="none"/></svg>';
const PAUSE = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M5 3.5v9M11 3.5v9" stroke-width="2.2"/></svg>';

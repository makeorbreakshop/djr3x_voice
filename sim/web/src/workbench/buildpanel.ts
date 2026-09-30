/**
 * Build mode's panels. Scene (left) gets the view controls - explode, shell solid/x-ray/
 * hidden, section plane, fasteners - which never change the model. The R3X panel's Build
 * tabs are the builder: Parts (tree, visibility, isolate, what a picked part is), Joints
 * (sliders within limits, Home, sweep to the first contact), Steps (the assembly
 * instructions, one at a time), Checks (click to pose the problem) and BOM. Every joint names
 * the profile joint it is (`head_tilt` in Bench/Studio/Show), or says it is not in the profile.
 */

import './build.css';
import type { AsmNode, Workbench } from './workbench';
import { flatten, variantOptions, joinUrl, loadIndex, MECH_BASE, type IndexEntry, type MAssembly, type MCheck, type MJoint, type MLink, type MPart, type MStep } from './manifest';

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const esc = (s: unknown) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!);
const num = (v: number, d = 1) => (Math.abs(v) < 0.05 && d <= 1 ? '0' : v.toFixed(d).replace(/\.0+$/, ''));
const signed = (v: number, d = 1) => (v > 0.049 ? '+' : '') + num(v, d);
const KEY = 'r3x.build';

interface Stored { assembly?: string; explode?: number; shell?: string; fasteners?: boolean }

function load(): Stored {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? '{}') as Stored;
  } catch {
    return {};
  }
}
function save(s: Stored) {
  try {
    localStorage.setItem(KEY, JSON.stringify(s));
  } catch {
    /* storage blocked */
  }
}

/** Where a part's geometry comes from, as one glyph: our parametric model, the vendor's CAD, or a mesh. */
type Src = 'parametric' | 'vendor' | 'mesh' | 'placeholder';
const srcOf = (c?: string): Src => (c === 'parametric' || c === 'vendor' || c === 'placeholder' ? c : 'mesh');
const SRC_LABEL: Record<Src, string> = { parametric: 'Parametric', vendor: 'Vendor CAD', mesh: 'Mesh', placeholder: 'Placeholder' };
const SRC_TITLE: Record<Src, string> = {
  parametric: 'Parametric: our build123d model, to the source or the published spec',
  vendor: "Vendor CAD: the manufacturer's B-rep",
  mesh: 'Mesh: the source STL, not yet remodelled',
  placeholder: 'Placeholder: a sized box, no model yet',
};
const srcDot = (c?: string) => `<i class="src ${srcOf(c)}" role="img" aria-label="${SRC_LABEL[srcOf(c)]}" title="${esc(SRC_TITLE[srcOf(c)])}"></i>`;
const notReal = (c?: string) => srcOf(c) === 'mesh' || srcOf(c) === 'placeholder';

/** The name without a parenthetical the glyph already says, or a long aside (full name on hover). */
const shortName = (n: string) => n.replace(/\s*\((parametric|ours)\)\s*$/i, '');
const bare = (n: string) => n.replace(/\s*\([^)]*\)\s*/g, ' ').trim();

type Filter = 'all' | 'printed' | 'hardware' | 'servos' | 'notreal' | 'inferred';
const FILTERS: [Filter, string, (p: MPart) => boolean][] = [
  ['all', 'All', () => true],
  ['printed', 'Printed', (p) => p.printed === true || p.class === 'shell' || p.class === 'mech'],
  ['hardware', 'Hardware', (p) => p.class === 'hardware' || p.class === 'bearing'],
  ['servos', 'Servos', (p) => p.class === 'servo'],
  ['notreal', 'Not real', (p) => notReal(p.cad)],
  ['inferred', 'Inferred', (p) => !!p.inferred],
];

/**
 * Build's markup, added to the page's two panels before they are wired (main.ts calls this
 * before mountPanels / ControlPanel so their tab and rail bindings see it). Kept here rather
 * than in index.html so Build stays one module.
 */
export function injectBuildDom() {
  if (document.getElementById('sc-build')) return;
  const html = (s: string) => {
    const t = document.createElement('template');
    t.innerHTML = s.trim();
    return t.content;
  };
  // Scene panel: rail icon + the view section, first.
  const rail = document.querySelector('#scene-panel .rail')!;
  rail.prepend(html(`<button data-scene-open="sc-build" data-build aria-label="Build view" title="Build view">
    <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.5 14 5v6l-6 3.5L2 11V5z"/><path d="M2 5l6 3.5L14 5M8 8.5v6"/></svg></button>`));
  document.getElementById('scene-body')!.prepend(html(`<section id="sc-build" class="first" data-build>
    <h2>Build view</h2>
    <label class="inline">Explode <input id="bv-explode" type="range" min="0" max="1" step="0.01" value="0"><output id="bv-explode-out">0%</output></label>
    <div class="kv">Shell</div>
    <div class="row seg" role="group" aria-label="Shell">
      <button data-shell="solid" aria-pressed="true">Solid</button>
      <button data-shell="xray" aria-pressed="false">X-ray</button>
      <button data-shell="hidden" aria-pressed="false">Hidden</button>
    </div>
    <div class="kv">Section</div>
    <div class="row">
      <button id="bv-section" aria-pressed="false" title="Cut the model with a plane">Cut</button>
      <select id="bv-axis" aria-label="Section axis"><option value="x">X (side)</option><option value="y">Y (level)</option><option value="z">Z (front)</option></select>
      <button id="bv-flip" class="icon" aria-pressed="false" aria-label="Flip the kept side" title="Flip the kept side">
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 5h8l-2-2M12 11H4l2 2"/></svg></button>
    </div>
    <label class="inline">Cut at <input id="bv-cut" type="range" min="0" max="1" step="0.005" value="0.5" disabled></label>
    <div class="stack">
      <button id="bv-fasteners" aria-pressed="true" title="Screws, inserts, nuts and washers">Fasteners</button>
      <button id="bv-frame" title="Point the camera at the visible model">Frame model</button>
    </div>
    <div class="kv">Overlays</div>
    <div class="stack">
      <button id="bv-intf" aria-pressed="false" title="Parts that overlap at rest, as the test suite sees them: the shared solid in red (orange: explained)">Interference <span id="bv-intf-n" class="n"></span></button>
    </div>
    <ul id="bv-intf-list" class="intf" hidden></ul>
  </section>`));
  // Operating-mode switches: Build first.
  for (const sel of ['.stage-modes', '.rail-modes']) {
    const box = document.querySelector(sel)!;
    const rail = sel === '.rail-modes';
    box.prepend(html(`<button role="radio" aria-checked="false" data-stage-mode="build" ${rail ? 'aria-label="Build" ' : ''}title="Build: inspect and assemble the mechanics. Sim only - never drives hardware">${rail ? 'Bu' : 'Build'}</button>`));
  }
  // Tabs + bodies.
  const tabs = document.querySelector('#panel .tabs')!;
  tabs.prepend(html(['parts:Parts', 'joints:Joints', 'steps:Steps', 'checks:Checks', 'bom:BOM']
    .map((t) => { const [id, label] = t.split(':'); return `<button role="tab" data-tab="${id}" data-modes="build">${label}</button>`; }).join('')));
  const sys = tabs.querySelector<HTMLElement>('[data-tab="system"]');
  if (sys) sys.dataset.modes = `${sys.dataset.modes ?? ''} build`.trim();
  tabs.after(html(`<div id="build-head" class="build-head" data-build>
    <div class="bh-row"><select id="bp-assembly" aria-label="Assembly"></select>
      <button id="bp-reload" class="icon" aria-label="Reload the built assembly">
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M13 8a5 5 0 1 1-1.5-3.6M13 2.5V5h-2.5"/></svg></button></div>
    <select id="bp-focus" aria-label="Sub-assembly" hidden></select>
    <p id="bp-status" class="bh-status" role="status" hidden></p>
  </div>`));
  const bodies = html(`
    <div class="tab-body bp-parts-body" data-body="parts" role="tabpanel" hidden>
      <div id="bp-variants"></div>
      <div class="bp-filters" id="bp-filters" role="group" aria-label="Show parts"></div>
      <div class="bp-legend">${(['parametric', 'vendor', 'mesh'] as Src[]).map((k) => `<span title="${esc(SRC_TITLE[k])}">${srcDot(k)}${SRC_LABEL[k]}</span>`).join('')}
        <button id="bp-show-all" class="link" data-bp="show-all" hidden>Show all</button></div>
      <ul id="bp-parts" class="part-tree" aria-label="Parts by link"></ul>
      <div id="bp-detail" class="bp-drawer" role="region" aria-label="Selected part" hidden></div>
    </div>
    <div class="tab-body" data-body="joints" role="tabpanel" hidden>
      <div class="bp-bar"><span id="bp-joint-n" class="bp-count"></span>
        <button class="bp-small" data-bp="home" title="Every joint to 0, the rest pose. Moves the model only, never the robot">Home</button></div>
      <div id="bp-joints"></div>
    </div>
    <div class="tab-body" data-body="steps" role="tabpanel" hidden>
      <div id="bp-step"></div>
      <details class="bp-disc" id="bp-steps-all"><summary>All steps</summary><ol id="bp-steplist" class="step-list"></ol></details>
    </div>
    <div class="tab-body" data-body="checks" role="tabpanel" hidden>
      <div class="bp-bar"><span id="bp-check-sum" class="bp-count"></span></div>
      <ul id="bp-checks" class="checks"></ul>
    </div>
    <div class="tab-body" data-body="bom" role="tabpanel" hidden>
      <div class="bp-bar"><span id="bp-bom-sum" class="bp-count"></span></div>
      <div class="table-wrap"><table class="bom"><thead><tr><th scope="col" class="num">Qty</th><th scope="col">Item</th><th scope="col"><span class="sr">Files</span></th></tr></thead>
        <tbody id="bp-bom"></tbody></table></div>
    </div>`);
  document.getElementById('build-head')!.after(bodies);
}

export function mountBuildPanel(wb: Workbench) {
  const st = load();
  let index: IndexEntry[] = [];
  let openAsm = st.assembly ?? '';
  let loadedOnce = false;
  let jointsDirty = true;
  /** Parts tab filter (the list, and the view isolates what it lists). */
  let filter: Filter = 'all';

  // ------------------------------------------------------------------ scene panel (view only)
  const explode = $<HTMLInputElement>('bv-explode');
  explode.value = String(st.explode ?? 0);
  wb.explode = Number(explode.value);
  explode.oninput = () => {
    wb.setExplode(Number(explode.value));
    $('bv-explode-out').textContent = `${Math.round(Number(explode.value) * 100)}%`;
    st.explode = Number(explode.value);
    save(st);
  };
  $('bv-explode-out').textContent = `${Math.round(wb.explode * 100)}%`;
  document.querySelectorAll<HTMLButtonElement>('[data-shell]').forEach((b) => {
    b.onclick = () => {
      wb.setShell(b.dataset.shell as 'solid' | 'xray' | 'hidden');
      st.shell = wb.shell;
      save(st);
    };
  });
  if (st.shell === 'xray' || st.shell === 'hidden' || st.shell === 'solid') wb.shell = st.shell;
  $('bv-section').onclick = () => wb.setSection({ on: !wb.section.on });
  $<HTMLSelectElement>('bv-axis').onchange = (e) => wb.setSection({ axis: (e.target as HTMLSelectElement).value as 'x' | 'y' | 'z', on: true });
  $<HTMLInputElement>('bv-cut').oninput = (e) => wb.setSection({ at: Number((e.target as HTMLInputElement).value), on: true });
  $('bv-flip').onclick = () => wb.setSection({ flip: !wb.section.flip, on: true });
  wb.fasteners = st.fasteners ?? true;
  $('bv-fasteners').onclick = () => {
    wb.setFasteners(!wb.fasteners);
    st.fasteners = wb.fasteners;
    save(st);
  };
  $('bv-frame').onclick = () => wb.frame();
  $('bv-intf').onclick = () => wb.setInterference(!wb.interferenceOn);
  $('bv-intf-list').onclick = (e) => {
    const b = (e.target as HTMLElement).closest<HTMLButtonElement>('button[data-k]');
    if (b) wb.frameInterference(Number(b.dataset.k));
  };

  // ------------------------------------------------------------------ assembly picker + breadcrumb
  const pick = $<HTMLSelectElement>('bp-assembly');
  pick.onchange = () => {
    openAsm = pick.value;
    st.assembly = openAsm;
    save(st);
    const e = index.find((x) => x.id === openAsm);
    if (e) void wb.load(MECH_BASE + e.manifest);
  };
  $('bp-reload').onclick = () => void refreshIndex(true);
  const focusSel = $<HTMLSelectElement>('bp-focus');
  focusSel.onchange = () => {
    const n = wb.nodeOf(focusSel.value);
    if (n) wb.setFocus(n);
    focusSel.blur();
  };

  async function refreshIndex(reload = false) {
    index = await loadIndex();
    pick.innerHTML = index.length
      ? index.map((e) => `<option value="${esc(e.id)}">${esc(e.name)}</option>`).join('')
      : '<option value="">No built assemblies</option>';
    if (!index.some((e) => e.id === openAsm)) openAsm = index[0]?.id ?? '';
    pick.value = openAsm;
    const e = index.find((x) => x.id === openAsm);
    if (e && (reload || !loadedOnce)) {
      loadedOnce = true;
      await wb.load(MECH_BASE + e.manifest);
    }
    render();
  }

  /** Build opens: fetch the index and the last assembly once. */
  window.addEventListener('r3x:mode', (e) => {
    if ((e as CustomEvent).detail === 'build' && !loadedOnce) void refreshIndex();
  });

  // ------------------------------------------------------------------ delegated clicks
  document.getElementById('panel')!.addEventListener('click', (e) => {
    const el = (e.target as HTMLElement).closest<HTMLElement>('[data-bp]');
    if (!el) return;
    const d = el.dataset;
    const node = (d.asm && wb.nodeOf(d.asm)) || wb.focus;
    switch (d.bp) {
      case 'select': wb.select(wb.selected === d.id ? null : d.id!); break;
      case 'deselect': wb.select(null); break;
      case 'filter': {
        filter = d.f as Filter;
        const a = wb.focus?.asm;
        const test = FILTERS.find(([f]) => f === filter)![2];
        wb.isolate(filter === 'all' || !a ? null : a.parts.filter(test).map((p) => p.id));
        break;
      }
      case 'eye': wb.toggleHidden(d.id!); break;
      case 'isolate': wb.isolate(wb.isolated?.size === 1 && wb.isolated.has(d.id!) ? null : [d.id!]); break;
      case 'isolate-link': {
        const ids = node?.asm.parts.filter((p) => p.link === d.link).map((p) => p.id) ?? [];
        const same = wb.isolated && ids.length === wb.isolated.size && ids.every((i) => wb.isolated!.has(i));
        wb.isolate(same ? null : ids);
        break;
      }
      case 'show-all': filter = 'all'; wb.hidden.clear(); wb.isolate(null); break;
      case 'focus': if (node) wb.setFocus(node); break;
      case 'home': wb.home(); jointsDirty = true; break;
      case 'sweep': if (node) wb.startSweep(node, d.joint!); break;
      case 'step': wb.setStep(Number(d.i)); break;
      case 'prev': wb.setStep(Math.max(0, wb.step - 1)); break;
      case 'next': wb.setStep(wb.step + 1); break;
      case 'check': {
        const chk = node?.asm.checks?.find((c) => c.id === d.id) ?? null;
        wb.showCheck(wb.check?.id === d.id ? null : chk, node ?? undefined);
        jointsDirty = true;
        break;
      }
      case 'variant': wb.setVariant(d.group!, d.id!); break;
    }
  });
  document.getElementById('panel')!.addEventListener('change', (e) => {
    const el = e.target as HTMLSelectElement;
    if (el.dataset.bp !== 'variant-sel') return;
    wb.setVariant(el.dataset.group!, el.value);
    el.blur();
  });
  document.getElementById('panel')!.addEventListener('input', (e) => {
    const el = e.target as HTMLInputElement;
    if (el.dataset.bp !== 'joint') return;
    const node = wb.nodeOf(el.dataset.asm!);
    if (node) wb.setJoint(node, el.dataset.joint!, Number(el.value));
  });

  // Parts: Up/Down move between rows, Escape closes the part drawer.
  $('bp-parts').addEventListener('keydown', (e) => {
    const t = e.target as HTMLElement;
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
    const rows = [...$('bp-parts').querySelectorAll<HTMLElement>('button.nm, button.grp-btn')];
    const i = rows.indexOf(t);
    if (i < 0) return;
    rows[Math.max(0, Math.min(rows.length - 1, i + (e.key === 'ArrowDown' ? 1 : -1)))].focus();
    e.preventDefault();
  });
  document.querySelector('[data-body="parts"]')!.addEventListener('keydown', (e) => {
    if ((e as KeyboardEvent).key === 'Escape' && wb.selected) {
      const id = wb.selected;
      wb.select(null);
      $('bp-parts').querySelector<HTMLElement>(`button.nm[data-id="${CSS.escape(id)}"]`)?.focus();
    }
  });

  // Steps: arrow keys while the Steps tab is open.
  addEventListener('keydown', (e) => {
    if (!wb.active || $('body-steps')?.hidden !== false) return;
    const t = e.target as HTMLElement;
    if (t.tagName === 'INPUT' || t.tagName === 'SELECT' || t.tagName === 'TEXTAREA') return;
    if (e.key === 'ArrowRight' || e.key === 'PageDown') wb.setStep(wb.step + 1);
    else if (e.key === 'ArrowLeft' || e.key === 'PageUp') wb.setStep(Math.max(0, wb.step - 1));
    else return;
    e.preventDefault();
  });

  // Entering and leaving the Steps / Checks tabs.
  document.querySelectorAll<HTMLButtonElement>('[data-tab]').forEach((b) => b.addEventListener('click', () => onTab(b.dataset.tab!)));
  function onTab(tab: string) {
    if (tab === 'steps' && wb.step < 0 && wb.focus?.asm.steps?.length) wb.setStep(0);
    if (tab !== 'steps' && wb.step >= 0) wb.setStep(-1);
    if (tab !== 'checks' && wb.check) wb.showCheck(null);
  }

  wb.onChange(() => render());

  // ------------------------------------------------------------------ render
  function render() {
    renderView();
    if (!wb.active) return;
    renderHead();
    renderParts();
    renderJoints();
    renderSteps();
    renderChecks();
    renderBom();
  }

  function renderView() {
    const press = (id: string, on: boolean) => $(id).setAttribute('aria-pressed', String(on));
    document.querySelectorAll<HTMLButtonElement>('[data-shell]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.shell === wb.shell)));
    press('bv-section', wb.section.on);
    press('bv-flip', wb.section.flip);
    press('bv-fasteners', wb.fasteners);
    press('bv-intf', wb.interferenceOn);
    const pairs = wb.interference;
    const open = pairs.filter((p) => !p.explained).length;
    $('bv-intf-n').textContent = pairs.length ? `${open}${pairs.length > open ? ` + ${pairs.length - open}` : ''}` : '';
    $('bv-intf').title = pairs.length
      ? `${open} unexplained and ${pairs.length - open} explained overlaps at rest (the suite's no-overlap test)`
      : 'No overlaps at rest in this build (or the suite has not run: python -m workbench test)';
    const list = $('bv-intf-list');
    list.hidden = !wb.interferenceOn || !pairs.length;
    const sig = pairs.map((p) => `${p.a}|${p.b}|${p.depth_mm}`).join();
    if (list.dataset.sig !== sig) {
      list.dataset.sig = sig;
      const short = (id: string) => shortName(wb.partInfo(id.includes('/') && !wb.partInfo(id) ? id.slice(id.indexOf('/') + 1) : id)?.part.name ?? id);
      list.innerHTML = pairs.map((p, k) => `<li class="${p.explained ? 'known' : 'hot'}"><button data-k="${k}" title="${esc(`${p.a} x ${p.b}: ${p.depth_mm} mm deep${p.volume_mm3 != null ? `, ${p.volume_mm3} mm³ shared` : ''}${p.explained ? ' (explained)' : ''}. Click to frame it`)}">
        <span>${esc(short(p.a))} × ${esc(short(p.b))}</span><span class="d">${num(p.depth_mm, 1)} mm</span></button></li>`).join('');
    }
    $<HTMLSelectElement>('bv-axis').value = wb.section.axis;
    $<HTMLInputElement>('bv-cut').disabled = !wb.section.on;
  }

  function renderHead() {
    const m = wb.manifest;
    const status = $('bp-status');
    let msg = '';
    if (wb.loading) msg = 'Loading…';
    else if (wb.error) msg = esc(wb.error);
    else if (!index.length) msg = 'Nothing built yet: <code>cd mech &amp;&amp; .venv/bin/python -m workbench build hunter_head</code>';
    status.innerHTML = msg;
    status.hidden = !msg;
    status.classList.toggle('err', !!wb.error);
    $('bp-reload').title = m ? `Reload · built ${new Date(m.generated_at).toLocaleString()}` : 'Reload';
    // Sub-assemblies: one picker, indented, only when there are any.
    // Sub-assemblies of unpicked variants (the droid's other head) are out of the model.
    const shown = new Set<MAssembly>();
    wb.forEachNode((n) => { if (wb.nodeShown(n)) shown.add(n.asm); });
    const all = wb.top ? flatten(wb.top.asm).filter((x) => shown.has(x.node)) : [];
    focusSel.hidden = all.length < 2;
    if (all.length > 1) {
      const sig = all.map((x) => x.node.id).join();
      if (focusSel.dataset.sig !== sig) {
        focusSel.dataset.sig = sig;
        focusSel.innerHTML = all.map(({ node, path }, i) =>
          `<option value="${esc(node.id)}">${i ? `${' '.repeat(path.length - 2)}${esc(bare(node.name))}` : 'Whole assembly'}</option>`).join('');
      }
      focusSel.value = wb.focus?.asm.id ?? all[0].node.id;
    }
  }

  // ---------------------------------------------------------------- Parts
  const groupName = (a: MAssembly, l: MLink) => {
    const j = l.joint ? a.joints.find((x) => x.id === l.joint) : undefined;
    const name = bare(l.name);
    if (!j) return { name, how: 'fixed', j };
    const word = j.id.replace(/^head_/, '').replace(/_/g, ' ');
    return { name, how: name.toLowerCase().startsWith(word) ? (j.type === 'prismatic' ? 'slide' : 'hinge') : word, j };
  };

  function renderParts() {
    const node = wb.focus;
    const body = $('bp-parts');
    if (!node) {
      body.innerHTML = '';
      $('bp-filters').innerHTML = '';
      renderDetail();
      return;
    }
    const a = node.asm;
    // Every variant group in the shown model (part options and child options such as the
    // droid's head mech), whatever is focused: switching one changes the whole model.
    const groups = new Map<string, { id: string; name: string }[]>();
    wb.forEachNode((n) => {
      if (!wb.nodeShown(n)) return;
      for (const v of variantOptions(n.asm)) {
        const g = groups.get(v.group) ?? [];
        if (!g.some((x) => x.id === v.id)) g.push({ id: v.id, name: v.name });
        groups.set(v.group, g);
      }
    });
    $('bp-variants').innerHTML = [...groups].map(([g, opts]) => variantSelect(g, opts, wb.variants[g])).join('');
    $('bp-variants').hidden = !groups.size;

    // The focused assembly's parts and every shown sub-assembly's (the droid's root owns none).
    const subs: MAssembly[] = [];
    wb.forEachNode((n) => { if (wb.nodeShown(n)) subs.push(n.asm); }, node);
    const allParts = subs.flatMap((x) => x.parts);
    // Filter chips with counts (a filter with nothing in it stands aside).
    $('bp-filters').innerHTML = FILTERS.map(([f, label, test]) => {
      const n = allParts.filter(test).length;
      if (!n && f !== 'all' && filter !== f) return '';
      return `<button class="chip" data-bp="filter" data-f="${f}" aria-pressed="${filter === f}">${label} <span>${n}</span></button>`;
    }).join('');
    const test = FILTERS.find(([f]) => f === filter)![2];

    const byLink = subs.flatMap((sa) => sa.links.map((l) => ({ sa, l, parts: sa.parts.filter((p) => p.link === l.id && test(p)) })))
      .filter((g) => g.parts.length);
    body.innerHTML = byLink.map(({ sa, l, parts }) => {
      const g0 = groupName(sa, l);
      const g = sa === a ? g0 : { ...g0, name: `${bare(sa.name)} › ${g0.name}` };
      const ids = sa.parts.filter((p) => p.link === l.id).map((p) => p.id);
      const iso = !!wb.isolated && ids.length === wb.isolated.size && ids.every((i) => wb.isolated!.has(i));
      const tip = `${l.name}${g.j ? ` · ${g.j.name}` : ''}. Click to show only this link`;
      return `<li class="grp"><button class="grp-btn" data-bp="isolate-link" data-link="${esc(l.id)}" data-asm="${esc(sa.id)}" aria-pressed="${iso}" title="${esc(tip)}">${esc(g.name)} <span>· ${esc(g.how)}</span></button><span class="grp-n">${parts.length}</span></li>` +
        parts.map((p) => {
          const hidden = wb.hidden.has(p.id);
          const solo = wb.isolated?.size === 1 && wb.isolated.has(p.id);
          return `<li class="part${wb.selected === p.id ? ' sel' : ''}${hidden ? ' off' : ''}${solo ? ' solo' : ''}">
            ${srcDot(p.cad)}
            <button class="nm" data-bp="select" data-id="${esc(p.id)}" title="${esc(p.name)}" aria-current="${wb.selected === p.id}"><span>${esc(shortName(p.name))}</span>${p.inferred ? `<sup class="inf" title="Inferred: ${esc(p.inferred_note || 'placement or part not confirmed')}">?</sup>` : ''}</button>
            <span class="acts">
              <button class="ic" data-bp="eye" data-id="${esc(p.id)}" aria-label="${hidden ? 'Show' : 'Hide'} ${esc(p.name)}" title="${hidden ? 'Show' : 'Hide'}">${hidden ? EYE_OFF : EYE}</button>
              <button class="ic solo" data-bp="isolate" data-id="${esc(p.id)}" aria-pressed="${solo}" aria-label="${solo ? 'Show everything' : `Show only ${esc(p.name)}`}" title="${solo ? 'Show everything' : 'Solo'}">${SOLO}</button>
            </span></li>`;
        }).join('');
    }).join('') || `<li class="empty">${filter === 'all' ? (wb.top && flatten(wb.top.asm).length > 1 ? 'No parts at this level: pick a sub-assembly above.' : 'No parts.') : 'No parts match.'}</li>`;
    $('bp-show-all').hidden = !wb.isolated && !wb.hidden.size;
    renderDetail();
  }

  function renderDetail() {
    const el = $('bp-detail');
    const id = wb.selected;
    el.hidden = !id;
    if (!id) {
      el.innerHTML = '';
      return;
    }
    const head = (name: string, glyph: string) => `<div class="dr-head">${glyph}<b title="${esc(name)}">${esc(name)}</b>
      <button class="icon" data-bp="deselect" aria-label="Close" title="Close (Esc)"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 4 8 8M12 4l-8 8"/></svg></button></div>`;
    const row = (k: string, v: string, tip = '') => (v ? `<dt>${k}</dt><dd${tip ? ` title="${esc(tip)}"` : ''}>${v}</dd>` : '');
    const info = wb.partInfo(id);
    if (!info) {
      const f = wb.fastenerInfo(id);
      if (!f) {
        el.hidden = true;
        return;
      }
      el.innerHTML = head(fastLabel(f.spec), srcDot(f.cad ?? 'parametric')) + `<dl class="facts">
        ${row('Joins', f.joins.map((p) => esc(shortName(wb.partInfo(p)?.part.name ?? p))).join(' + '))}
        ${row('Step', esc(stepTitle(wb.focus?.asm, f.step)))}
        ${row('Catalog', esc(f.catalog ?? ''))}</dl>
        ${f.inferred ? `<p class="dr-inf">Inferred: ${esc(f.inferred_note || 'not confirmed')}</p>` : ''}`;
      return;
    }
    const { part: p, joint: j, node } = info;
    const src = p.source ?? {};
    const exp = p.export ?? {};
    const mates = ((node.asm as MAssembly & { mates?: { a: { part: string }; b: { part: string } }[] }).mates ?? [])
      .filter((m) => m.a?.part === p.id || m.b?.part === p.id).length;
    const params = src.params as Record<string, unknown> | undefined;
    const reg = src.regression as { p95_mm?: number; mean_mm?: number; volume_ratio?: number } | undefined;
    const file = (src.file as string | undefined) ?? (src.reference as string | undefined) ?? (src.model as string | undefined) ?? '';
    el.innerHTML = head(shortName(p.name), srcDot(p.cad)) + `<dl class="facts">
      ${row('Source', `${SRC_LABEL[srcOf(p.cad)]}${file ? ` · <span class="mono">${esc(file)}</span>` : ''}`, SRC_TITLE[srcOf(p.cad)] + (src.placement ? `\nPlaced: ${src.placement}` : ''))}
      ${row('Material', esc(p.material ?? ''))}
      ${row('Joint', j ? `${esc(bare(j.name))}${j.profile_joint ? ` <span class="mono dim">${esc(j.profile_joint)}</span>` : ''}` : 'fixed', j?.name)}
      ${row('Mates', mates ? String(mates) : '')}
      ${params ? row('Params', Object.entries(params).map(([k, v]) => `<span class="mono">${esc(k)} ${esc(v)}</span>`).join(' · ')) : ''}
      ${reg ? row('Fit', `p95 ${num(reg.p95_mm ?? 0, 2)} mm`, `vs ${String(src.reference ?? 'reference')}: mean ${num(reg.mean_mm ?? 0, 2)} mm, volume ${num((reg.volume_ratio ?? 1) * 100, 1)}%`) : ''}
      ${p.mass_g ? row('Mass', `${num(p.mass_g)} g`, p.mass_note ?? '') : ''}
      ${exp.stl || exp['3mf'] ? row('Export', `${exp.stl ? `<a href="${esc(joinUrl(node.asm.base ?? '/', exp.stl))}" download>STL</a>` : ''}${exp['3mf'] ? `<a href="${esc(joinUrl(node.asm.base ?? '/', exp['3mf']))}" download>3MF</a>` : ''}`) : ''}</dl>
      ${p.inferred ? `<p class="dr-inf">Inferred: ${esc(p.inferred_note || 'placement or part not confirmed')}</p>` : ''}
      ${p.note ? `<details class="bp-disc"><summary>Note</summary><p>${esc(p.note)}</p></details>` : ''}`;
  }

  // ---------------------------------------------------------------- Joints
  let jointSig = '';
  function renderJoints() {
    const node = wb.focus;
    const body = $('bp-joints');
    if (!node) {
      body.innerHTML = '';
      return;
    }
    const nodes: AsmNode[] = [];
    wb.forEachNode((n) => nodes.push(n), node);
    const total = nodes.reduce((k, n) => k + n.asm.joints.length, 0);
    $('bp-joint-n').textContent = total ? `${total} joint${total === 1 ? '' : 's'}` : '';
    const sig = nodes.map((n) => n.asm.id + n.asm.joints.map((j) => j.id).join()).join('|') + wb.sweeping;
    if (sig !== jointSig || jointsDirty) {
      jointSig = sig;
      jointsDirty = false;
      body.innerHTML = nodes.flatMap((n) => n.asm.joints.map((j: MJoint) => {
        const v = n.pose[j.id] ?? 0;
        const pl = j.profile_limits;
        const tip = `${j.name}\n${num(j.limits.min)}…${signed(j.limits.max)} ${j.unit}${pl ? ` (profile ${num(pl.min)}…${signed(pl.max)})` : ''} · ${driveLabel(j.drive)}`;
        const k = `${esc(n.asm.id)}:${esc(j.id)}`;
        return `<div class="joint">
          <div class="jhead"><b title="${esc(tip)}">${esc(bare(j.name))}</b>${profileTag(j.profile_joint)}${j.inferred ? `<sup class="inf" title="Inferred: ${esc(j.inferred_note ?? '')}">?</sup>` : ''}
            <output data-out="${k}">${signed(v)}</output>
            <button class="ic" data-bp="sweep" data-asm="${esc(n.asm.id)}" data-joint="${esc(j.id)}" aria-label="Sweep ${esc(j.name)}" title="Sweep through the range; the first contact lights up" aria-pressed="${wb.sweeping === j.id}">${SWEEP}</button></div>
          <input type="range" data-bp="joint" data-asm="${esc(n.asm.id)}" data-joint="${esc(j.id)}"
            min="${j.limits.min}" max="${j.limits.max}" step="0.5" value="${v}" aria-label="${esc(j.name)}">
          <div class="jlive" data-jlive="${k}"></div></div>`;
      })).join('') || '<p class="empty">No joints in this assembly.</p>';
    }
    // live values: slider positions, servo angles, contact
    for (const n of nodes) {
      for (const j of n.asm.joints) {
        const k = `${n.asm.id}:${j.id}`;
        const v = n.pose[j.id] ?? 0;
        const inp = body.querySelector<HTMLInputElement>(`input[data-asm="${CSS.escape(n.asm.id)}"][data-joint="${CSS.escape(j.id)}"]`);
        if (inp && document.activeElement !== inp) inp.value = String(v);
        const out = body.querySelector(`[data-out="${CSS.escape(k)}"]`);
        if (out) out.textContent = `${signed(v)}°`;
        const live = body.querySelector<HTMLElement>(`[data-jlive="${CSS.escape(k)}"]`);
        if (!live) continue;
        const bits: string[] = [];
        for (const lid of j.drive?.linkages ?? []) {
          const r = n.rods.get(lid);
          const lk = n.asm.linkages?.find((x) => x.id === lid);
          bits.push(r ? `${esc(lk?.servo ?? lid)} ${signed(r.servoDeg)}°` : `<span class="bad">${esc(lk?.servo ?? lid)} out of reach</span>`);
        }
        const chk = n.asm.checks?.find((c) => c.id === `interference_${j.id}`);
        if (chk?.value != null) bits.push(`contact ${signed(chk.value)}°`);
        if (wb.sweeping === j.id && wb.contact) bits.push(`<span class="${wb.contact.status === 'fail' ? 'bad' : 'meh'}">touching ${wb.contact.parts.map(esc).join(' × ')}</span>`);
        live.innerHTML = bits.join(' · ');
        live.hidden = !bits.length;
      }
    }
  }

  // ---------------------------------------------------------------- Steps
  function renderSteps() {
    const a = wb.focus?.asm;
    const steps = a?.steps ?? [];
    const body = $('bp-step');
    const list = $('bp-steplist');
    $('bp-steps-all').hidden = !steps.length;
    if (!steps.length) {
      body.innerHTML = '<p class="empty">No steps for this assembly.</p>';
      list.innerHTML = '';
      return;
    }
    const i = Math.max(0, wb.step);
    const s = steps[i];
    const fasts = a?.fasteners ?? [];
    const callouts = new Map<string, { label: string; n: number; inferred: boolean; joins: Set<string> }>();
    for (const fid of s.fasteners ?? []) {
      const f = fasts.find((x) => x.id === fid);
      if (!f) continue;
      const c = callouts.get(f.key) ?? { label: fastLabel(f.spec), n: 0, inferred: false, joins: new Set<string>() };
      c.n++;
      c.inferred ||= !!f.inferred;
      f.joins.forEach((p) => c.joins.add(p));
      callouts.set(f.key, c);
    }
    const name = (id: string) => shortName(wb.partInfo(id)?.part.name ?? id);
    body.innerHTML = `<div class="step-nav">
        <button class="ic" data-bp="prev" ${i === 0 ? 'disabled' : ''} aria-label="Previous step" title="Previous (←)"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M10 3 5 8l5 5"/></svg></button>
        <span class="step-n">${s.n ?? i + 1} / ${steps.length}</span>
        <button class="ic" data-bp="next" ${i >= steps.length - 1 ? 'disabled' : ''} aria-label="Next step" title="Next (→)"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="m6 3 5 5-5 5"/></svg></button>
        <h3 class="step-title">${esc(s.title)}</h3>${s.guide_page ? `<span class="step-page" title="Build guide page">p. ${s.guide_page}</span>` : ''}</div>
      ${s.inferred ? `<p class="dr-inf">Inferred: ${esc(s.inferred_note || 'confirm this step')}</p>` : ''}
      ${s.joint ? `<p class="zero-line">Sets the zero of <b>${esc(s.joint)}</b>${profileTag(a?.joints.find((j) => j.id === s.joint)?.profile_joint ?? null)}</p>` : ''}
      ${s.parts?.length ? `<h4>Parts</h4><ul class="plain">${s.parts.map((p) => `<li><button class="link" data-bp="select" data-id="${esc(p)}">${esc(name(p))}</button></li>`).join('')}</ul>` : ''}
      ${callouts.size ? `<h4>Fasteners</h4><ul class="callouts">${[...callouts.values()].map((c) =>
        `<li><b>${c.n}×</b><span title="${esc([...c.joins].map(name).join(' · '))}">${esc(c.label)}${c.inferred ? '<sup class="inf" title="Inferred">?</sup>' : ''}</span></li>`).join('')}</ul>` : ''}
      ${s.unplaced?.length ? `<h4>Also, not drawn</h4><ul class="callouts">${s.unplaced.map((u) =>
        `<li><b>${u.count ? `${u.count}×` : '–'}</b><span title="${esc(u.note ?? '')}">${esc(u.spec ? fastLabel(u.spec) : u.key)}</span></li>`).join('')}</ul>` : ''}
      ${s.tools?.length ? `<h4>Tools</h4><ul class="plain">${s.tools.map((t) => `<li>${esc(t)}</li>`).join('')}</ul>` : ''}
      ${s.notes?.length ? `<h4>Notes</h4><ul class="notes">${s.notes.map((t) => `<li>${esc(t)}</li>`).join('')}</ul>` : ''}`;
    list.innerHTML = steps.map((x: MStep, k) => `<li><button class="${k === i ? 'on' : ''}" data-bp="step" data-i="${k}" aria-current="${k === i ? 'step' : 'false'}">
      <span>${x.n ?? k + 1}</span>${esc(x.title)}${x.inferred ? '<sup class="inf" title="Contains inferred details">?</sup>' : ''}</button></li>`).join('');
  }

  // ---------------------------------------------------------------- Checks
  function renderChecks() {
    const a = wb.focus?.asm;
    const checks = a?.checks ?? [];
    const counts = { pass: 0, warn: 0, fail: 0, explained: 0 };
    checks.forEach((c) => counts[c.status]++);
    $('bp-check-sum').innerHTML = checks.length
      ? (['fail', 'explained', 'warn', 'pass'] as const).filter((k) => counts[k]).map((k) => `<span class="st ${k}">${counts[k]} ${k}</span>`).join('') : '';
    const row = (c: MCheck) => {
      const b = brief(c);
      const on = wb.check?.id === c.id;
      const more = b.items.length > 1 || c.assumptions?.length;
      return `<li class="chk ${c.status}${on ? ' on' : ''}">
      <button data-bp="check" data-id="${esc(c.id)}" data-asm="${esc(a!.id)}" aria-pressed="${on}" title="${on ? 'Back to the rest pose' : 'Pose the model where this happens'}">
        <span class="st ${c.status}" role="img" aria-label="${c.status}" title="${c.status}">${STATUS_GLYPH[c.status]}</span><b title="${esc(c.title)}">${esc(c.title)}</b>
        ${b.cause ? `<span class="l1">${esc(b.cause)}</span>` : ''}${b.fix ? `<span class="l2">→ ${esc(b.fix)}</span>` : ''}</button>
      ${more ? `<details class="bp-disc"><summary${c.seconds != null ? ` title="Ran in ${num(c.seconds, 2)} s"` : ''}>Details</summary>
        ${b.items.length > 1 ? `<ul>${b.items.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}
        ${c.assumptions?.length ? `<p class="dim">Assumes: ${c.assumptions.map(esc).join('; ')}</p>` : ''}</details>` : ''}</li>`;
    };
    const order = { fail: 0, explained: 1, warn: 2, pass: 3 };
    const tests = checks.filter((c) => c.kind === 'test').sort((x, y) => order[x.status] - order[y.status]);
    const eng = checks.filter((c) => c.kind !== 'test');
    $('bp-checks').innerHTML = (tests.length ? `<li class="grp">Assembly tests</li>${tests.map(row).join('')}` : '')
      + (eng.length ? `<li class="grp">Engineering checks</li>${eng.map(row).join('')}` : '')
      || '<li class="empty">No checks in this build. Build without <code>--no-checks</code>.</li>';
    const tab = document.querySelector<HTMLElement>('[data-tab="checks"]');
    if (tab) tab.dataset.status = counts.fail ? 'fail' : counts.warn ? 'warn' : 'pass';
  }

  // ---------------------------------------------------------------- BOM
  function renderBom() {
    const a = wb.focus?.asm;
    const lines = a?.bom_rollup?.length ? a.bom_rollup : a?.bom ?? [];
    const cats = [...new Set(lines.map((b) => b.category))];
    const exportsFor = (ids: string[] = []) => ids.map((id) => wb.partInfo(id)).filter(Boolean).map((i) => {
      const e = i!.part.export ?? {};
      const base = i!.node.asm.base ?? '/';
      return [e.stl ? `<a href="${esc(joinUrl(base, e.stl))}" download>STL</a>` : '', e['3mf'] ? `<a href="${esc(joinUrl(base, e['3mf']))}" download>3MF</a>` : ''].filter(Boolean).join(' ');
    }).join(' ');
    const cap = (t: string) => t.charAt(0).toUpperCase() + t.slice(1);
    $('bp-bom').innerHTML = cats.map((cat) => `<tr class="grp"><th colspan="3" scope="rowgroup">${esc(cap(cat))} <span>${lines.filter((b) => b.category === cat).length}</span></th></tr>` + lines.filter((b) => b.category === cat).map((b) => `<tr>
      <td class="num">${num(b.qty, 0)}</td>
      <td>${b.source ? `<a href="${esc(b.source)}" target="_blank" rel="noopener">${esc(b.item)}</a>` : esc(b.item)}${b.inferred ? `<sup class="inf" title="Inferred: ${esc(b.inferred_note ?? '')}">?</sup>` : ''}</td>
      <td class="exp">${cat === 'printed' ? exportsFor(b.parts) : ''}</td></tr>`).join('')).join('');
    const printed = lines.filter((b) => b.category === 'printed').length;
    $('bp-bom-sum').textContent = lines.length ? `${lines.length} lines · ${printed} printed` : '';
  }

  return { refreshIndex };
}

// ------------------------------------------------------------------ helpers

const EYE = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M1.5 8s2.5-4.5 6.5-4.5S14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8z"/><circle cx="8" cy="8" r="2"/></svg>';
const STATUS_GLYPH: Record<string, string> = { fail: '✕', warn: '!', explained: 'i', pass: '✓' };
const SOLO = '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="5.5"/><circle cx="8" cy="8" r="2" fill="currentColor"/></svg>';
const SWEEP = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 11a5.5 5.5 0 0 1 10 0"/><path d="m13 11-.3-2.6M13 11l-2.4-.9"/></svg>';

/**
 * A check's summary as two short lines: the cause and the fix ("explained: X -> fix: Y"), or
 * the first finding with a count of the rest. Long coordinates are rounded and dropped from the
 * line (the Details list keeps every finding).
 */
export function brief(c: { summary: string }): { cause: string; fix: string; items: string[] } {
  const round = (t: string) => t.replace(/-?\d+\.\d{3,}/g, (m) => String(Math.round(Number(m) * 10) / 10));
  const s = round(c.summary.replace(/^\s*explained:\s*/i, ''));
  const arrow = s.split(/\s*->\s*/);
  if (arrow.length > 1) return { cause: arrow[0], fix: arrow.slice(1).join(' -> ').replace(/^fix:\s*/i, ''), items: [s] };
  const items = s.split(/;\s+/).filter(Boolean);
  const first = (items[0] ?? '').replace(/\s+at \[[^\]]*\]/g, '');
  return { cause: items.length > 1 ? `${first} (+${items.length - 1} more)` : first, fix: '', items };
}

const EYE_OFF = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 2l12 12M6.5 4a6.5 6.5 0 0 1 8 4 9 9 0 0 1-1.6 2.2M9.8 12.3A6.4 6.4 0 0 1 1.5 8a9 9 0 0 1 2.3-2.8"/></svg>';

function profileTag(p: string | null | undefined) {
  return p
    ? ` <span class="ptag" title="This joint is ${esc(p)} in the robot profile (Bench, Studio, Show)">${esc(p)}</span>`
    : ' <span class="ptag none" title="The robot profile has no such joint">not in profile</span>';
}

/** One variant group as a labelled select: short option names (full on hover). */
function variantSelect(group: string, opts: { id: string; name: string }[], picked: string | undefined) {
  const label = group.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
  const cur = opts.find((o) => o.id === picked);
  return `<label class="bp-var" title="${esc(cur?.name ?? label)}"><span>${esc(label)}</span><select data-bp="variant-sel" data-group="${esc(group)}">${
    opts.map((o) => `<option value="${esc(o.id)}"${o.id === picked ? ' selected' : ''}>${esc(bare(o.name))}</option>`).join('')}</select></label>`;
}

function driveLabel(d?: { kind?: string; servos?: string[]; gear_ratio?: number | null }) {
  if (!d?.kind) return 'undriven';
  const k = { push_rod_pair: 'two push rods', push_rod: 'push rod', direct: 'direct', gear: 'gear', external: 'outside this assembly', none: 'undriven' }[d.kind] ?? d.kind;
  return `${k}${d.servos?.length ? ` (${d.servos.join(', ')})` : ''}${d.gear_ratio ? ` ${d.gear_ratio}:1` : ''}`;
}

function fastLabel(s: { type: string; thread?: string; length_mm?: number; mcmaster?: string }) {
  const names: Record<string, string> = {
    shcs: 'socket head cap screw', bhcs: 'button head screw', insert: 'heat-set insert', lock_nut: 'lock nut',
    nut: 'nut', washer: 'washer', threaded_rod: 'threaded rod', pin: 'alignment pin',
  };
  const size = s.thread ? (s.length_mm && s.type !== 'insert' ? `${s.thread} × ${s.length_mm}` : s.thread) : '';
  return `${size} ${names[s.type] ?? s.type}`.trim() + (s.mcmaster ? ` (McMaster ${s.mcmaster})` : '');
}

function stepTitle(a: MAssembly | undefined, id: string) {
  const s = a?.steps?.find((x) => x.id === id);
  return s ? `${s.n}. ${s.title}` : id;
}

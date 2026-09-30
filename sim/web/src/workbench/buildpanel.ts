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
import { flatten, joinUrl, loadIndex, MECH_BASE, type IndexEntry, type MAssembly, type MCheck, type MStep } from './manifest';

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

const CLASS_LABEL: Record<string, string> = {
  shell: 'shell', mech: 'printed', servo: 'servo', hardware: 'hardware', bearing: 'bearing', fastener: 'fastener',
};

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
    <div class="row"><select id="bp-assembly" aria-label="Assembly"></select>
      <button id="bp-reload" class="icon" aria-label="Reload the built assembly" title="Reload (after a workbench build)">
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M13 8a5 5 0 1 1-1.5-3.6M13 2.5V5h-2.5"/></svg></button></div>
    <nav id="bp-crumbs" class="crumbs" aria-label="Assembly path"></nav>
    <div id="bp-tree" class="asm-tree" hidden></div>
    <p id="bp-status" class="hint" role="status"></p>
  </div>`));
  const bodies = html(`
    <div class="tab-body" data-body="parts" role="tabpanel" hidden>
      <section class="first"><div id="bp-detail" class="detail"></div></section>
      <section><div id="bp-variants"></div>
        <h2>Parts <button id="bp-show-all" class="link" data-bp="show-all" hidden>show all</button></h2>
        <ul id="bp-parts" class="part-tree"></ul></section>
    </div>
    <div class="tab-body" data-body="joints" role="tabpanel" hidden>
      <section class="first home-row"><button class="primary home-btn" data-bp="home" title="Every joint to 0 (the rest pose). Moves the model only, never the robot">Home</button></section>
      <section><div id="bp-joints"></div></section>
    </div>
    <div class="tab-body" data-body="steps" role="tabpanel" hidden>
      <section class="first"><div id="bp-step"></div></section>
      <section><h2>All steps</h2><ol id="bp-steplist" class="step-list"></ol></section>
    </div>
    <div class="tab-body" data-body="checks" role="tabpanel" hidden>
      <section class="first"><h2>Checks <small id="bp-check-sum"></small></h2>
        <p class="hint">Click one to pose the model where it happens.</p>
        <ul id="bp-checks" class="checks"></ul></section>
    </div>
    <div class="tab-body" data-body="bom" role="tabpanel" hidden>
      <section class="first"><h2>Bill of materials <small id="bp-bom-sum"></small></h2>
        <div class="table-wrap"><table class="bom"><thead><tr><th scope="col" class="num">qty</th><th scope="col">item</th><th scope="col">files</th></tr></thead>
        <tbody id="bp-bom"></tbody></table></div></section>
    </div>`);
  document.getElementById('build-head')!.after(bodies);
}

export function mountBuildPanel(wb: Workbench) {
  const st = load();
  let index: IndexEntry[] = [];
  let openAsm = st.assembly ?? '';
  let loadedOnce = false;
  let jointsDirty = true;

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
      case 'eye': wb.toggleHidden(d.id!); break;
      case 'isolate': wb.isolate(wb.isolated?.size === 1 && wb.isolated.has(d.id!) ? null : [d.id!]); break;
      case 'isolate-link': {
        const ids = node?.asm.parts.filter((p) => p.link === d.link).map((p) => p.id) ?? [];
        wb.isolate(ids);
        break;
      }
      case 'show-all': wb.hidden.clear(); wb.isolate(null); break;
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
  document.getElementById('panel')!.addEventListener('input', (e) => {
    const el = e.target as HTMLInputElement;
    if (el.dataset.bp !== 'joint') return;
    const node = wb.nodeOf(el.dataset.asm!);
    if (node) wb.setJoint(node, el.dataset.joint!, Number(el.value));
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
    $<HTMLSelectElement>('bv-axis').value = wb.section.axis;
    $<HTMLInputElement>('bv-cut').disabled = !wb.section.on;
  }

  function renderHead() {
    const m = wb.manifest;
    const status = $('bp-status');
    if (wb.loading) status.textContent = 'Loading…';
    else if (wb.error) status.textContent = wb.error;
    else if (!index.length) status.innerHTML = 'Nothing built yet: <code>cd mech &amp;&amp; .venv/bin/python -m workbench build hunter_head</code>';
    else status.textContent = m ? `Built ${new Date(m.generated_at).toLocaleString()}` : '';
    status.classList.toggle('err', !!wb.error);
    const crumbs = $('bp-crumbs');
    if (!wb.top || !wb.focus) {
      crumbs.innerHTML = '';
      return;
    }
    const path: AsmNode[] = [];
    for (let n: AsmNode | null = wb.focus; n; n = n.parent) path.unshift(n);
    crumbs.innerHTML = path.map((n, i) => i === path.length - 1
      ? `<span aria-current="location">${esc(n.asm.name)}</span>`
      : `<button class="link" data-bp="focus" data-asm="${esc(n.asm.id)}">${esc(n.asm.name)}</button>`).join('<span class="sep">›</span>');
    const kids = flatten(wb.top.asm).slice(1);
    $('bp-tree').hidden = !kids.length;
    $('bp-tree').innerHTML = kids.length
      ? `<span class="hint">Sub-assemblies</span>` + kids.map(({ node, path: p }) =>
        `<button class="chip${wb.focus?.asm.id === node.id ? ' on' : ''}" data-bp="focus" data-asm="${esc(node.id)}" style="margin-left:${(p.length - 2) * 10}px">${esc(node.name)}</button>`).join('')
      : '';
  }

  // ---------------------------------------------------------------- Parts
  function renderParts() {
    const node = wb.focus;
    const body = $('bp-parts');
    if (!node) {
      body.innerHTML = '';
      $('bp-detail').innerHTML = '';
      return;
    }
    const a = node.asm;
    const groups = new Map<string, string[]>();
    for (const v of a.variants ?? []) groups.set(v.group, [...(groups.get(v.group) ?? []), v.id]);
    $('bp-variants').innerHTML = [...groups].map(([g, ids]) => `<div class="kv">${esc(g)}</div><div class="row seg wrap" role="group" aria-label="${esc(g)}">${
      ids.map((id) => `<button data-bp="variant" data-group="${esc(g)}" data-id="${esc(id)}" aria-pressed="${wb.variants[g] === id}">${esc(a.variants!.find((v) => v.id === id)!.name)}</button>`).join('')}</div>`).join('');
    $('bp-variants').hidden = !groups.size;
    const byLink = a.links.map((l) => ({ l, parts: a.parts.filter((p) => p.link === l.id) }));
    body.innerHTML = byLink.filter((g) => g.parts.length).map(({ l, parts }) => {
      const j = l.joint ? a.joints.find((x) => x.id === l.joint) : undefined;
      return `<li class="grp"><span>${esc(l.name)}</span>${j ? `<small>${esc(j.id)}</small>` : '<small>fixed</small>'}
        <button class="link" data-bp="isolate-link" data-link="${esc(l.id)}" data-asm="${esc(a.id)}">isolate</button></li>` +
        parts.map((p) => {
          const hidden = wb.hidden.has(p.id);
          const iso = wb.isolated?.size === 1 && wb.isolated.has(p.id);
          return `<li class="part${wb.selected === p.id ? ' sel' : ''}${hidden ? ' off' : ''}">
            <button class="eye" data-bp="eye" data-id="${esc(p.id)}" aria-pressed="${!hidden}" aria-label="${hidden ? 'Show' : 'Hide'} ${esc(p.name)}" title="${hidden ? 'Show' : 'Hide'}">${hidden ? EYE_OFF : EYE}</button>
            <button class="nm" data-bp="select" data-id="${esc(p.id)}" title="${esc(p.name)}">${esc(p.name)}</button>
            <i class="cls ${esc(p.class)}">${esc(CLASS_LABEL[p.class])}</i>${p.inferred ? '<i class="inf" title="Placement or part inferred">inferred</i>' : ''}
            <button class="link iso" data-bp="isolate" data-id="${esc(p.id)}" aria-pressed="${iso}">${iso ? 'all' : 'solo'}</button></li>`;
        }).join('');
    }).join('');
    $('bp-show-all').hidden = !wb.isolated && !wb.hidden.size;
    renderDetail();
  }

  function renderDetail() {
    const el = $('bp-detail');
    const id = wb.selected;
    if (!id) {
      el.innerHTML = '<p class="hint">Click a part in the view or the list.</p>';
      return;
    }
    const info = wb.partInfo(id);
    if (!info) {
      const f = wb.fastenerInfo(id);
      if (!f) {
        el.innerHTML = '';
        return;
      }
      el.innerHTML = `<h3>${esc(fastLabel(f.spec))}</h3><dl class="facts">
        <dt>Joins</dt><dd>${f.joins.map((p) => esc(wb.partInfo(p)?.part.name ?? p)).join(' + ')}</dd>
        <dt>Step</dt><dd>${esc(stepTitle(wb.focus?.asm, f.step))}</dd>
        ${f.spec.mcmaster ? `<dt>McMaster</dt><dd>${esc(f.spec.mcmaster)}</dd>` : ''}
        ${f.inferred ? `<dt>Inferred</dt><dd class="inf">${esc(f.inferred_note || 'yes')}</dd>` : ''}</dl>`;
      return;
    }
    const { part: p, joint: j, node } = info;
    const src = p.source ?? {};
    const exp = p.export ?? {};
    el.innerHTML = `<h3>${esc(p.name)}</h3><dl class="facts">
      <dt>Class</dt><dd>${esc(CLASS_LABEL[p.class])}${p.material ? ` · ${esc(p.material)}` : ''}</dd>
      <dt>Source</dt><dd>${esc(src.file ?? src.kind ?? '-')}${src.entity ? ` <small>(${esc(src.entity)})</small>` : ''}</dd>
      <dt>Placed</dt><dd>${esc(src.placement ?? '-')}${src.fit ? ` <small>${esc(src.fit)}</small>` : ''}</dd>
      <dt>Moves with</dt><dd>${j ? `${esc(j.name)} ${profileTag(j.profile_joint)}` : 'fixed (ground)'}${p.linkage ? ` · posed by ${esc(p.linkage)}` : ''}</dd>
      ${p.mass_g ? `<dt>Mass</dt><dd>${num(p.mass_g)} g <small>${esc(p.mass_note ?? '')}</small></dd>` : ''}
      ${p.triangles ? `<dt>Mesh</dt><dd>${p.triangles.display.toLocaleString()} shown / ${p.triangles.source.toLocaleString()} source triangles</dd>` : ''}
      ${exp.stl || exp['3mf'] ? `<dt>Export</dt><dd>${exp.stl ? `<a href="${esc(joinUrl(node.asm.base ?? '/', exp.stl))}" download>STL</a>` : ''} ${exp['3mf'] ? `<a href="${esc(joinUrl(node.asm.base ?? '/', exp['3mf']))}" download>3MF</a>` : ''}</dd>` : ''}
      ${p.note ? `<dt>Note</dt><dd>${esc(p.note)}</dd>` : ''}
      ${p.inferred ? `<dt>Inferred</dt><dd class="inf">${esc(p.inferred_note || 'yes')}</dd>` : ''}</dl>`;
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
    const sig = nodes.map((n) => n.asm.id + n.asm.joints.map((j) => j.id).join()).join('|') + wb.sweeping;
    if (sig !== jointSig || jointsDirty) {
      jointSig = sig;
      jointsDirty = false;
      body.innerHTML = nodes.flatMap((n) => n.asm.joints.map((j) => {
        const v = n.pose[j.id] ?? 0;
        const pl = j.profile_limits;
        return `<div class="joint" data-joint-card="${esc(n.asm.id)}:${esc(j.id)}">
          <div class="jhead"><b>${esc(j.name)}</b>${profileTag(j.profile_joint)}</div>
          <div class="jrow"><input type="range" data-bp="joint" data-asm="${esc(n.asm.id)}" data-joint="${esc(j.id)}"
            min="${j.limits.min}" max="${j.limits.max}" step="0.5" value="${v}" aria-label="${esc(j.name)}">
            <output data-out="${esc(n.asm.id)}:${esc(j.id)}">${signed(v)}</output>
            <button data-bp="sweep" data-asm="${esc(n.asm.id)}" data-joint="${esc(j.id)}" title="Animate through the range; the first contact lights up">${wb.sweeping === j.id ? 'Sweeping…' : 'Sweep'}</button></div>
          <div class="jmeta">${num(j.limits.min)}…${signed(j.limits.max)} ${esc(j.unit)}${pl ? ` · profile ${num(pl.min)}…${signed(pl.max)}` : ''}
            · ${esc(driveLabel(j.drive))}</div>
          <div class="jlive" data-jlive="${esc(n.asm.id)}:${esc(j.id)}"></div>
          ${j.inferred ? `<div class="jmeta inf">${esc(j.inferred_note)}</div>` : ''}</div>`;
      })).join('') || '<p class="hint">No joints in this assembly.</p>';
    }
    // live values: slider positions, servo angles, contact
    for (const n of nodes) {
      for (const j of n.asm.joints) {
        const k = `${n.asm.id}:${j.id}`;
        const v = n.pose[j.id] ?? 0;
        const inp = body.querySelector<HTMLInputElement>(`input[data-asm="${CSS.escape(n.asm.id)}"][data-joint="${CSS.escape(j.id)}"]`);
        if (inp && document.activeElement !== inp) inp.value = String(v);
        const out = body.querySelector(`[data-out="${CSS.escape(k)}"]`);
        if (out) out.textContent = signed(v);
        const live = body.querySelector<HTMLElement>(`[data-jlive="${CSS.escape(k)}"]`);
        if (!live) continue;
        const bits: string[] = [];
        for (const lid of j.drive?.linkages ?? []) {
          const r = n.rods.get(lid);
          const lk = n.asm.linkages?.find((x) => x.id === lid);
          bits.push(r ? `${esc(lk?.servo ?? lid)} ${signed(r.servoDeg)}°` : `<span class="bad">${esc(lk?.servo ?? lid)} out of reach</span>`);
        }
        const chk = n.asm.checks?.find((c) => c.id === `interference_${j.id}`);
        if (chk?.value != null) bits.push(`first contact ${signed(chk.value)}°`);
        if (wb.sweeping === j.id && wb.contact) bits.push(`<span class="${wb.contact.status === 'fail' ? 'bad' : 'meh'}">touching: ${wb.contact.parts.map(esc).join(' × ')}</span>`);
        live.innerHTML = bits.join(' · ');
      }
    }
  }

  // ---------------------------------------------------------------- Steps
  function renderSteps() {
    const a = wb.focus?.asm;
    const steps = a?.steps ?? [];
    const body = $('bp-step');
    const list = $('bp-steplist');
    if (!steps.length) {
      body.innerHTML = '<p class="hint">No steps for this assembly.</p>';
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
    const name = (id: string) => wb.partInfo(id)?.part.name ?? id;
    body.innerHTML = `<div class="step-nav">
        <button data-bp="prev" ${i === 0 ? 'disabled' : ''} aria-label="Previous step">‹ Prev</button>
        <span class="step-n">Step ${s.n ?? i + 1} of ${steps.length}</span>
        <button data-bp="next" ${i >= steps.length - 1 ? 'disabled' : ''} aria-label="Next step">Next ›</button></div>
      <h3 class="step-title">${esc(s.title)}${s.guide_page ? ` <small>Guide p. ${s.guide_page}</small>` : ''}</h3>
      ${s.inferred ? `<p class="inf-line">Inferred: ${esc(s.inferred_note || 'confirm this step')}</p>` : ''}
      ${s.joint ? `<p class="zero-line">Sets the zero of <b>${esc(s.joint)}</b>${profileTag(a?.joints.find((j) => j.id === s.joint)?.profile_joint ?? null)}</p>` : ''}
      ${s.parts?.length ? `<h4>Parts</h4><ul class="plain">${s.parts.map((p) => `<li><button class="link" data-bp="select" data-id="${esc(p)}">${esc(name(p))}</button></li>`).join('')}</ul>` : ''}
      ${callouts.size ? `<h4>Fasteners</h4><ul class="callouts">${[...callouts.values()].map((c) =>
        `<li><b>${c.n} ×</b><span>${esc(c.label)}${c.inferred ? ' <i class="inf">inferred</i>' : ''}<small>${[...c.joins].map((p) => esc(name(p))).join(' · ')}</small></span></li>`).join('')}</ul>` : ''}
      ${s.unplaced?.length ? `<h4>Also (not shown in 3D)</h4><ul class="callouts">${s.unplaced.map((u) =>
        `<li><b>${u.count ? `${u.count} ×` : '–'}</b><span>${esc(u.spec ? fastLabel(u.spec) : u.key)}<small>${esc(u.note ?? '')}</small></span></li>`).join('')}</ul>` : ''}
      ${s.tools?.length ? `<h4>Tools</h4><ul class="plain">${s.tools.map((t) => `<li>${esc(t)}</li>`).join('')}</ul>` : ''}
      ${s.notes?.length ? `<h4>Notes</h4><ul class="notes">${s.notes.map((t) => `<li>${esc(t)}</li>`).join('')}</ul>` : ''}`;
    list.innerHTML = steps.map((x: MStep, k) => `<li><button class="${k === i ? 'on' : ''}" data-bp="step" data-i="${k}" aria-current="${k === i ? 'step' : 'false'}">
      <span>${x.n ?? k + 1}</span>${esc(x.title)}${x.inferred ? ' <i class="inf" title="Contains inferred details">?</i>' : ''}</button></li>`).join('');
  }

  // ---------------------------------------------------------------- Checks
  function renderChecks() {
    const a = wb.focus?.asm;
    const checks = a?.checks ?? [];
    const counts = { pass: 0, warn: 0, fail: 0 };
    checks.forEach((c) => counts[c.status]++);
    $('bp-check-sum').textContent = checks.length ? `${counts.fail} fail · ${counts.warn} warn · ${counts.pass} pass` : '';
    $('bp-checks').innerHTML = checks.map((c: MCheck) => `<li class="${c.status}${wb.check?.id === c.id ? ' on' : ''}">
      <button data-bp="check" data-id="${esc(c.id)}" data-asm="${esc(a!.id)}" aria-pressed="${wb.check?.id === c.id}">
        <span class="badge ${c.status}">${c.status}</span><b>${esc(c.title)}</b><span class="sum">${esc(c.summary)}</span></button>
      ${c.assumptions?.length ? `<details><summary>Assumptions</summary><ul>${c.assumptions.map((x) => `<li>${esc(x)}</li>`).join('')}</ul></details>` : ''}</li>`).join('')
      || '<li class="hint">No checks in this manifest (build without --no-checks).</li>';
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
    $('bp-bom').innerHTML = cats.map((cat) => `<tr class="grp"><th colspan="3">${esc(cat)}</th></tr>` + lines.filter((b) => b.category === cat).map((b) => `<tr>
      <td class="num">${num(b.qty, 0)}</td>
      <td>${b.source ? `<a href="${esc(b.source)}" target="_blank" rel="noopener">${esc(b.item)}</a>` : esc(b.item)}${b.inferred ? ` <i class="inf" title="${esc(b.inferred_note ?? '')}">inferred</i>` : ''}</td>
      <td class="exp">${cat === 'printed' ? exportsFor(b.parts) : ''}</td></tr>`).join('')).join('');
    const printed = lines.filter((b) => b.category === 'printed').length;
    $('bp-bom-sum').textContent = lines.length ? `${lines.length} lines · ${printed} printed parts` : '';
  }

  return { refreshIndex };
}

// ------------------------------------------------------------------ helpers

const EYE = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M1.5 8s2.5-4.5 6.5-4.5S14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8z"/><circle cx="8" cy="8" r="2"/></svg>';
const EYE_OFF = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 2l12 12M6.5 4a6.5 6.5 0 0 1 8 4 9 9 0 0 1-1.6 2.2M9.8 12.3A6.4 6.4 0 0 1 1.5 8a9 9 0 0 1 2.3-2.8"/></svg>';

function profileTag(p: string | null | undefined) {
  return p
    ? ` <span class="ptag" title="This joint is ${esc(p)} in the robot profile (Bench, Studio, Show)">= ${esc(p)}</span>`
    : ' <span class="ptag none" title="The robot profile has no such joint">not in profile</span>';
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

/**
 * The rig animation runs on - Original (`robot.json`) or Physical (`robot.generated.json`,
 * generated from the mech model by mech/rigsync). Robot behaviour, not a view setting: it picks
 * the profile the embedded performer, Studio lint, the Bench limits and the visual model's
 * joint tree use. The look (Scene > Model) is separate.
 *
 * - Connected: the runtime owns it (`state.stage.rig`, `stage.set_rig`); the runtime stops
 *   runs, homes and reloads its performer. Every panel follows by reloading itself onto the
 *   same profile.
 * - Standalone: per viewer (localStorage); a switch stops runs, homes, then reloads the page.
 * - `?rig=physical|original` (or the old `?profile=generated`) overrides both for this tab.
 *
 * Read at module load, before anything parses the profile, so one page = one profile.
 */

import type { Rig } from './generated/Rig';

export type { Rig };

const KEY = 'r3x.rig';
export const RIG_LABEL: Record<Rig, string> = { original: 'Original', physical: 'Physical' };

function urlOverride(): Rig | null {
  if (typeof location === 'undefined') return null;
  const q = new URLSearchParams(location.search);
  const r = q.get('rig');
  if (r === 'original' || r === 'physical') return r;
  if (q.get('profile') === 'generated') return 'physical';
  return null;
}

function stored(): Rig {
  try {
    const v = localStorage.getItem(KEY);
    return v === 'physical' ? 'physical' : 'original';
  } catch {
    return 'original';
  }
}

/** The URL pinned the rig for this tab (the selector shows it and cannot change it). */
export const RIG_PINNED = urlOverride() !== null;
/** The rig this page runs (fixed for the page's life). */
export const ACTIVE_RIG: Rig = urlOverride() ?? stored();

function remember(r: Rig) {
  try {
    localStorage.setItem(KEY, r);
  } catch {
    /* storage blocked: the switch lasts one reload at most */
  }
}

export interface RigHost {
  connected(): boolean;
  /** Connected: the gateway command; resolves to the ack. */
  setRemote(rig: Rig): Promise<{ status: string; reason?: string }>;
  /** Standalone: is a show-layer run playing. */
  showRunning(): boolean;
  /** Standalone: stop runs and Home before the reload. */
  stopAndHome(): void;
  say(msg: string): void;
}

/** A panel follows the runtime's rig: a different one reloads the page onto it. */
export function followRemote(rig: Rig | undefined) {
  if (!rig || RIG_PINNED || rig === ACTIVE_RIG) return;
  remember(rig);
  location.reload();
}

/** The Original | Physical row under the operating modes in the R3X panel. */
export function mountRigSelector(host: RigHost) {
  const modes = document.querySelector('#r3x-body .stage-modes');
  if (!modes || document.getElementById('rig-choice')) return;
  let mode = 'show';
  addEventListener('r3x:mode', (e) => (mode = String((e as CustomEvent).detail)));
  const row = document.createElement('div');
  row.id = 'rig-choice';
  row.className = 'row seg rig-choice';
  row.setAttribute('role', 'radiogroup');
  row.setAttribute('aria-label', 'Rig');
  row.innerHTML = `<span class="rig-label" title="The robot profile animation runs on: limits, speeds and joint tree">Rig</span>
    <button role="radio" data-rig="original" title="robot.json: the limits and kinematics the show was authored on">Original</button>
    <button role="radio" data-rig="physical" title="robot.generated.json: generated from the mech model (mech/rigsync); head on the base's neck column">Physical</button>`;
  modes.after(row);
  const style = document.createElement('style');
  style.textContent = `.rig-choice { display: flex; align-items: center; gap: 4px; margin-top: 6px; }
    .rig-choice .rig-label { font-size: 11px; color: var(--muted); letter-spacing: .06em; text-transform: uppercase; margin-right: 4px; }
    .rig-choice button { flex: 1; }
    .rig-choice button[aria-checked="true"] { border-color: var(--accent); color: var(--accent); }`;
  document.head.appendChild(style);
  row.querySelectorAll<HTMLButtonElement>('[data-rig]').forEach((b) => {
    b.setAttribute('aria-checked', String(b.dataset.rig === ACTIVE_RIG));
    b.disabled = RIG_PINNED;
    if (RIG_PINNED) b.title += ' (pinned by the URL)';
    b.onclick = async () => {
      const rig = b.dataset.rig as Rig;
      b.blur();
      if (rig === ACTIVE_RIG) return;
      if (host.connected()) {
        const a = await host.setRemote(rig);
        if (a.status === 'rejected') host.say(`Rig: ${a.reason ?? 'refused'}`);
        return; // accepted: state.stage.rig changes and followRemote reloads
      }
      if (mode === 'show' && host.showRunning()) return host.say('Rig: a show is running; stop it before switching the rig');
      host.stopAndHome();
      remember(rig);
      host.say(`Rig: ${RIG_LABEL[rig]}; reloading onto its profile`);
      setTimeout(() => location.reload(), 400);
    };
  });
}

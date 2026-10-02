/**
 * The viewer's link: what is open and how it is shown, in the URL fragment, readable
 * (`#at=lib:hunter&look=mechanism&ctx=ghost&x=0.3&cam=...`), so a copied link opens the same view.
 *
 *   at    lib:<library id> | file:<manifest id> | build | sys:<system id> | asm:<node key>
 *         (absent: the Library list)
 *   look  exterior | mechanism | inspect
 *   ctx   ghost | hide            the rest of the droid
 *   x     explode, 0..1
 *   fast  0                       fasteners off (on when absent)
 *   v     group:id,group:id       variant picks (our build only; a library design brings its own)
 *   j     node/joint:value,...    joints off rest
 *   cam   px,py,pz,tx,ty,tz       camera position and target, metres
 *
 * Pure: parse and format only. page.ts reads the Workbench into a ViewState and back.
 */

export type At =
  | { kind: 'list' }
  | { kind: 'lib' | 'file' | 'sys' | 'asm'; id: string }
  | { kind: 'build' };

export interface ViewState {
  at: At;
  look?: string;
  ctx?: string;
  explode?: number;
  fasteners?: boolean;
  variants?: Record<string, string>;
  joints?: { node: string; joint: string; value: number }[];
  cam?: number[];
}

const LOOKS = new Set(['exterior', 'mechanism', 'inspect']);
const r3 = (v: number) => String(Math.round(v * 1000) / 1000);

export function formatView(s: ViewState): string {
  const q: string[] = [];
  const put = (k: string, v: string) => q.push(`${k}=${encodeURIComponent(v).replace(/%2C/g, ',').replace(/%3A/g, ':').replace(/%2F/g, '/')}`);
  if (s.at.kind === 'build') put('at', 'build');
  else if (s.at.kind !== 'list') put('at', `${s.at.kind}:${s.at.id}`);
  if (s.look) put('look', s.look);
  if (s.ctx) put('ctx', s.ctx);
  if (s.explode) put('x', r3(s.explode));
  if (s.fasteners === false) put('fast', '0');
  const v = Object.entries(s.variants ?? {});
  if (v.length) put('v', v.map(([g, id]) => `${g}:${id}`).join(','));
  if (s.joints?.length) put('j', s.joints.map((j) => `${j.node}/${j.joint}:${r3(j.value)}`).join(','));
  if (s.cam?.length === 6) put('cam', s.cam.map(r3).join(','));
  return q.length ? `#${q.join('&')}` : '';
}

/** A fragment back to a ViewState; anything malformed is left out (never throws). */
export function parseView(hash: string): ViewState | null {
  const h = hash.replace(/^#/, '');
  if (!h) return null;
  const p = new URLSearchParams(h);
  const s: ViewState = { at: { kind: 'list' } };
  const at = p.get('at');
  if (at === 'build') s.at = { kind: 'build' };
  else if (at) {
    const i = at.indexOf(':');
    const kind = at.slice(0, i);
    const id = at.slice(i + 1);
    if (i > 0 && id && (kind === 'lib' || kind === 'file' || kind === 'sys' || kind === 'asm')) s.at = { kind, id };
  }
  const look = p.get('look');
  if (look && LOOKS.has(look)) s.look = look;
  const ctx = p.get('ctx');
  if (ctx === 'ghost' || ctx === 'hide') s.ctx = ctx;
  const x = Number(p.get('x'));
  if (p.has('x') && Number.isFinite(x)) s.explode = Math.min(1, Math.max(0, x));
  if (p.get('fast') === '0') s.fasteners = false;
  const v = p.get('v');
  if (v) {
    s.variants = {};
    for (const kv of v.split(',')) {
      const i = kv.indexOf(':');
      if (i > 0 && i < kv.length - 1) s.variants[kv.slice(0, i)] = kv.slice(i + 1);
    }
  }
  const j = p.get('j');
  if (j) {
    s.joints = [];
    for (const t of j.split(',')) {
      const a = t.lastIndexOf(':');
      const b = t.lastIndexOf('/', a);
      const value = Number(t.slice(a + 1));
      if (b > 0 && a > b + 1 && Number.isFinite(value)) s.joints.push({ node: t.slice(0, b), joint: t.slice(b + 1, a), value });
    }
  }
  const cam = (p.get('cam') ?? '').split(',').map(Number);
  if (cam.length === 6 && cam.every(Number.isFinite)) s.cam = cam;
  return s;
}

/**
 * What a link sets, with what it does not mention at its default for the design it opens (rest pose, no
 * explode, fasteners on, the rest of the droid ghosted; `look` null: the design's own, our build's
 * Exterior), never what this browser saved: a link shows everyone the same view.
 */
export function linkSettings(v: ViewState): { look: string | null; ctx: 'ghost' | 'hide'; explode: number; fasteners: boolean; joints: NonNullable<ViewState['joints']> } {
  return {
    look: v.look ?? (v.at.kind === 'lib' ? null : 'exterior'),
    ctx: v.ctx === 'hide' ? 'hide' : 'ghost',
    explode: v.explode ?? 0,
    fasteners: v.fasteners ?? true,
    joints: v.joints ?? [],
  };
}

/**
 * Whether the address may be rewritten now: not while a pointer is down or within `settleMs` of input.
 * An address change is a round trip to the browser's own interface, and the drag's pointer events wait
 * behind it (the once-a-second write showed as a stall, then one catch-up move of 30-80 degrees).
 */
export const LINK_SETTLE_MS = 350;
export function mayWriteLink(pointersDown: number, settled: (ms: number) => boolean): boolean {
  return pointersDown <= 0 && settled(LINK_SETTLE_MS);
}

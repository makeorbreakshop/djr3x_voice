/**
 * The booth's stage-lighting controller: a small DMX-style console that runs beside the
 * show, the way the park's light desk runs beside R3X's audio.
 *
 * Nothing here knows about three.js. The controller owns fixture GROUPS, named CUES, and a
 * playback engine (crossfades, a beat-clocked chase, per-mode programs). Every frame it
 * produces one linear-light RGB "flux" per group (colour x dimmer, 0..~1.5). booth.ts maps
 * each group onto the runtime lights it already had, the shader-side rack washes, the
 * practicals and the baked bounce.
 *
 * Two rig presets, because the references show two different eras of the venue:
 *
 * - `disneyland_2019` (default). The 2019 Oga's Cantina shows (MouseSteps 10-minute set,
 *   Cosmic Eclipse opening): the rock is washed a near-constant desaturated salmon (hue
 *   0-10 deg, sat ~0.55 over the whole 10 minutes); the droid has his own fixtures - a
 *   warm amber KEY on the torso from the front, and blue-violet RIMS on both sides of the
 *   head - plus a blue-white spot on the ceiling centre above him. During songs the key is
 *   on (and its level drifts, 4:30 vs 5:30); in the interludes (6:40-9:00) the key and rims
 *   drop out, the room wash stays, and he shows his true light-grey paint.
 * - `venue_cycle`. Brandon's own phone video: full stage cues that change every few
 *   seconds with snappy fades - yellow-green, blue-white wall with amber racks, amber,
 *   orange-red, cool white - often split (the wall one colour, the racks another).
 *
 * Default cue. The booth starts in `disneyland_2019 / song`: amber key on, blue rims on,
 * salmon room. That is the look of the park photo the render harness is matched to
 * (`photo-oga-front-low`). The key tints the paint, so for judging paint and weathering
 * use `interlude` (key off: the paint lit only by the neutral-ish room bounce) or
 * `venue_cycle / cool_white` (a near-neutral key: he reads silver-white, as in the video).
 * `idle` mode goes to `interlude` by itself. URL: `?rig=venue_cycle&cue=cool_white`.
 */

export const GROUPS = [
  'droid_key', // amber/cue key on the droid's torso, from the front (shadow caster)
  'head_rim_l', // blue-violet rim on the head, droid's right (-X)
  'head_rim_r', // ... and left (+X)
  'wall_wash_l', // uplight on the side rock walls (with back_uplight: the room uplight)
  'wall_wash_r',
  'back_uplight', // behind the machinery panel, up the back wall
  'ceiling_down', // the blue-white spot on the ceiling centre above him
  'fill', // room fill through the arch, from the guests' left
  'rack_wash_l', // cassette racks (shader-side wash: costs only the booth's pixels)
  'rack_wash_r',
  'practicals', // readouts, pedestal ring, deck glow (a dimmer on emissive colour)
] as const;
export type Group = (typeof GROUPS)[number];

export type Mode = 'idle' | 'music' | 'dj' | 'speaking' | 'off';

/** Linear-light RGB. */
export type RGB = [number, number, number];

/** A group's setting in a cue: sRGB hex colour and a dimmer (1 = the fixture's nominal level). */
export interface Level {
  color: number;
  level: number;
}

/** Shorthands accepted in cue definitions: one entry sets several groups. */
const ALIASES: Record<string, readonly Group[]> = {
  room: ['wall_wash_l', 'wall_wash_r', 'back_uplight'],
  walls: ['wall_wash_l', 'wall_wash_r'],
  rims: ['head_rim_l', 'head_rim_r'],
  racks: ['rack_wash_l', 'rack_wash_r'],
};
type CueKey = Group | keyof typeof ALIASES;
export type CueDef = Partial<Record<CueKey, Level>>;

/** How a mode runs the cue list. */
export interface ModeProgram {
  /** Cues to run, in order (a single cue just holds). Omitted: hold whatever is up. */
  list?: string[];
  /** Step every `bars` bars of the beat clock (music), or every `holdS` seconds (idle). */
  bars?: number;
  holdS?: number;
  /** Crossfade time into each cue (s). */
  fadeS: number;
  /** Multiplier on the droid key (speaking: lift him out of the room a little). */
  keyLift?: number;
}

export interface RigPreset {
  /** Levels every cue starts from; a cue overrides only what it names. */
  base: CueDef;
  cues: Record<string, CueDef>;
  modes: Record<Mode, ModeProgram>;
  /** Cue the booth is built with, before anything drives the controller. */
  initial: string;
}

const L = (color: number, level = 1): Level => ({ color, level });

// ------------------------------------------------------------------ rig presets

/**
 * 2019. Colours from the MouseSteps set (per-second samples: dome ~#7a3834, hue 0-10,
 * sat 0.55; droid during songs hue 10-35, in the interlude darker and redder - only the
 * room). The camera auto-exposed and white-balanced, so these are hue hints tuned against
 * the photo-matched harness shot, not measured radiometry.
 */
const DISNEYLAND_2019: RigPreset = {
  base: {
    room: L(0xff90a0, 1), // desaturated salmon-pink, not crimson
    ceiling_down: L(0x9ea8ff, 2.2), // blue-white / lavender on the dome centre
    fill: L(0x8a86ff, 1),
    racks: L(0xff9a70, 0.35),
    practicals: L(0xffffff, 1),
    droid_key: L(0xffc060, 0),
    rims: L(0x6b63ff, 0),
  },
  cues: {
    song: { droid_key: L(0xffc060, 1), rims: L(0x6b63ff, 1) },
    song_dim: { droid_key: L(0xffa452, 0.6), rims: L(0x6b63ff, 0.9) },
    song_gold: { droid_key: L(0xffc25a, 1.1), rims: L(0x7a6cff, 1) },
    interlude: { room: L(0xff90a0, 1.25), ceiling_down: L(0xa9b4ff, 1.2) },
    red_flash: { room: L(0xff2a3c, 1.4), droid_key: L(0xff5a3a, 0.5), rims: L(0x6b63ff, 0.6), ceiling_down: L(0xa9b4ff, 0.4) },
    blackout: { room: L(0xff90a0, 0.15), ceiling_down: L(0xa9b4ff, 0.1), fill: L(0x8a86ff, 0.3), racks: L(0xff9a70, 0.1) },
  },
  modes: {
    idle: { list: ['interlude'], fadeS: 2.5 },
    music: { list: ['song', 'song', 'song_gold', 'song', 'song_dim'], bars: 8, fadeS: 1.5 },
    dj: { list: ['song', 'song_gold', 'song', 'song_dim'], bars: 4, fadeS: 1.0 },
    speaking: { list: ['song'], fadeS: 0.6, keyLift: 1.0 },
    off: { list: ['blackout'], fadeS: 1.5 },
  },
  initial: 'song',
};

/**
 * Brandon's venue video. Wall-average samples (auto-exposed, so hue hints): yellow-green
 * #947659 / racks #9f9244; blue-white #8283ac / racks #794a37; amber #9a6f46; orange-red
 * racks #9b5b1d; neutral #846f52. The rock reads cream-white under most cues - the colour
 * lands on the droid, the panel and the racks - so the wall washes are paler than the
 * rack washes, and several cues are split.
 */
// Key levels are balanced by luminance against the 2019 amber key (lum ~0.6 at level 1),
// so a white or yellow-green key does not blow the droid out.
const VENUE_CYCLE: RigPreset = {
  base: {
    walls: L(0xfff0dc, 0.55),
    back_uplight: L(0xfff0dc, 0.5),
    ceiling_down: L(0xffffff, 0.25),
    fill: L(0xc8ccff, 0.5),
    racks: L(0xffffff, 0.6),
    practicals: L(0xffffff, 1),
    droid_key: L(0xfff4e6, 0.6),
    rims: L(0xd8dcff, 0),
  },
  cues: {
    yellow_green: { walls: L(0xf2f0a0, 0.6), back_uplight: L(0xd8f040, 0.8), racks: L(0xd4f028, 1.3), droid_key: L(0xf6f050, 0.7) },
    blue_white: {
      walls: L(0x9aa6ff, 0.9), back_uplight: L(0xb8c0ff, 0.7), ceiling_down: L(0xb8c0ff, 0.6),
      rack_wash_l: L(0xff6a22, 1.2), rack_wash_r: L(0xe0e040, 1), droid_key: L(0xffd860, 0.8),
    },
    amber: { walls: L(0xe8e0ff, 0.55), back_uplight: L(0xffb040, 0.8), racks: L(0xffb030, 1.2), droid_key: L(0xffc048, 1) },
    orange_red: { walls: L(0xfff0e8, 0.6), back_uplight: L(0xff7a28, 0.8), racks: L(0xff5a18, 1.2), droid_key: L(0xffa040, 1.1) },
    cool_white: {
      walls: L(0xfff0dc, 0.6), back_uplight: L(0xf0f0e0, 0.5), racks: L(0xd8f040, 1.0),
      droid_key: L(0xf2f4ff, 0.6), rims: L(0xd8dcff, 0.3),
    },
    blackout: { walls: L(0xfff0dc, 0.08), back_uplight: L(0xfff0dc, 0.05), ceiling_down: L(0xffffff, 0), fill: L(0xc8ccff, 0.2), racks: L(0xffffff, 0.05), droid_key: L(0xffffff, 0) },
  },
  modes: {
    // Gentle: long holds, slow fades, mostly the neutral cue.
    idle: { list: ['cool_white', 'amber', 'cool_white', 'yellow_green'], holdS: 7, fadeS: 2.5 },
    // The video: a new cue every ~2 bars at ~120 BPM, near-instant changes.
    music: { list: ['yellow_green', 'blue_white', 'amber', 'orange_red', 'yellow_green', 'cool_white'], bars: 2, fadeS: 0.3 },
    dj: { list: ['yellow_green', 'blue_white', 'amber', 'orange_red', 'cool_white'], bars: 2, fadeS: 0.25 },
    // Hold whatever is up; lift the key so he stands out while he talks.
    speaking: { fadeS: 0.4, keyLift: 1.2 },
    off: { list: ['blackout'], fadeS: 1.0 },
  },
  initial: 'cool_white',
};

export const RIGS: Record<string, RigPreset> = { disneyland_2019: DISNEYLAND_2019, venue_cycle: VENUE_CYCLE };
export const DEFAULT_RIG = 'disneyland_2019';

// ------------------------------------------------------------------ colour

const srgbToLinear = (c: number) => (c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));

/** A Level as linear flux (colour x dimmer). */
export function flux(l: Level): RGB {
  const r = ((l.color >> 16) & 255) / 255, g = ((l.color >> 8) & 255) / 255, b = (l.color & 255) / 255;
  return [srgbToLinear(r) * l.level, srgbToLinear(g) * l.level, srgbToLinear(b) * l.level];
}

/** Resolve a cue against its rig's base into flux for every group. */
export function resolveCue(rig: RigPreset, name: string): Record<Group, RGB> {
  const def = rig.cues[name];
  if (!def) throw new Error(`stagelights: no cue "${name}"`);
  const out = {} as Record<Group, RGB>;
  for (const g of GROUPS) out[g] = [0, 0, 0];
  for (const src of [rig.base, def]) {
    for (const [k, lvl] of Object.entries(src) as [CueKey, Level][]) {
      for (const g of (ALIASES[k] ?? [k]) as Group[]) out[g] = flux(lvl);
    }
  }
  return out;
}

// ------------------------------------------------------------------ controller

export interface StageLightsOptions {
  rig?: string;
  cue?: string;
  bpm?: number;
}

export class StageLights {
  /** Current output: linear flux per group. Read after update(). */
  readonly out = {} as Record<Group, RGB>;

  private rigName: string;
  private rig: RigPreset;
  private _mode: Mode | null = null;
  private _cue: string;
  private _bpm = 120;

  /** Output before the key lift (what fades start from). */
  private raw = {} as Record<Group, RGB>;
  // Crossfade: from a snapshot of the output to a resolved cue.
  private from = {} as Record<Group, RGB>;
  private to = {} as Record<Group, RGB>;
  private fadeT = 0;
  private fadeS = 0;

  // Program position.
  private listIdx = 0;
  /** Beats since the last step (music) or seconds since the last step (idle). */
  private phase = 0;
  /** Seconds into the current beat, for the internal beat clock. */
  private beatClock = 0;
  /** Set when beat() was called since the last update: the internal clock defers to it. */
  private externalBeats = 0;
  private lastExternalAt = -Infinity;
  private time = 0;

  private keyLift = 1;
  private keyLiftTarget = 1;
  /** A show's cue hold (s left): the mode program is paused until it runs out. */
  private holdLeft = 0;
  private preHoldCue: string | null = null;
  /** Bumped whenever the output changed (booth skips GPU work when it has not). */
  version = 0;

  constructor(opts: StageLightsOptions = {}) {
    this.rigName = opts.rig && RIGS[opts.rig] ? opts.rig : DEFAULT_RIG;
    this.rig = RIGS[this.rigName];
    this._cue = opts.cue && this.rig.cues[opts.cue] ? opts.cue : this.rig.initial;
    if (opts.bpm) this.setBpm(opts.bpm);
    const c = resolveCue(this.rig, this._cue);
    for (const g of GROUPS) {
      this.out[g] = [...c[g]];
      this.raw[g] = [...c[g]];
      this.from[g] = [...c[g]];
      this.to[g] = [...c[g]];
    }
  }

  get rigPreset() { return this.rigName; }
  get mode() { return this._mode; }
  get cue() { return this._cue; }
  get bpm() { return this._bpm; }
  get cues() { return Object.keys(this.rig.cues); }
  /** Fade progress 0..1 (1 = arrived). */
  get fade() { return this.fadeS > 0 ? Math.min(1, this.fadeT / this.fadeS) : 1; }

  /** Switch era. Keeps the mode; starts the new rig at its initial cue (or the mode's first). */
  setRig(name: string, fadeS = 1) {
    if (!RIGS[name] || name === this.rigName) return;
    this.rigName = name;
    this.rig = RIGS[name];
    const m = this._mode;
    this._mode = null;
    if (m) this.setMode(m, fadeS);
    else this.goCue(this.rig.initial, fadeS);
  }

  setBpm(n: number) {
    if (Number.isFinite(n) && n > 0) this._bpm = Math.min(300, Math.max(30, n));
  }

  /**
   * An external beat (from the music analysis or the DJ clock). Re-phases the internal
   * clock to it, so a live beat source and the BPM estimate never double-count.
   */
  beat() {
    this.externalBeats++;
    this.lastExternalAt = this.time;
    this.beatClock = 0;
  }

  /** Change mode: starts that mode's program (speaking holds the current cue by default). */
  setMode(mode: Mode, fadeS?: number) {
    if (mode === this._mode) return;
    this._mode = mode;
    const p = this.rig.modes[mode];
    this.keyLiftTarget = p.keyLift ?? 1;
    this.phase = 0;
    if (p.list?.length) {
      // Re-enter a list at the cue already up if it is in it (no jump when music resumes).
      const at = p.list.indexOf(this._cue);
      this.listIdx = at >= 0 ? at : 0;
      // During a show's hold the new program takes over when the hold ends.
      if (!this.holdLeft) this.goCue(p.list[this.listIdx], fadeS ?? p.fadeS);
    }
  }

  /**
   * A show's `lights` action (SPEC stage.lights): fade to a cue. With holdS > 0 the mode
   * program is paused for that long and then resumes (back to whatever it was running);
   * hold 0 leaves the cue up until the program or a mode change moves on. Returns false
   * when this rig has no such cue.
   */
  showCue(name: string, fadeS = 0, holdS = 0): boolean {
    if (!this.rig.cues[name]) return false;
    if (holdS > 0) {
      if (!this.holdLeft) this.preHoldCue = this._cue;
      this.holdLeft = holdS;
    }
    this.goCue(name, fadeS);
    return true;
  }

  /** Seconds left on a show's cue hold (0 = the mode program is running). */
  get holding() { return this.holdLeft; }

  private resumeProgram() {
    const p = this._mode ? this.rig.modes[this._mode] : null;
    this.phase = 0;
    if (p?.list?.length) this.goCue(p.list[this.listIdx % p.list.length], p.fadeS);
    else if (this.preHoldCue && this.rig.cues[this.preHoldCue]) this.goCue(this.preHoldCue, p?.fadeS ?? 0.5);
    this.preHoldCue = null;
  }

  /** Fade to a named cue over fadeS seconds (0 = snap). */
  goCue(name: string, fadeS = 1) {
    const target = resolveCue(this.rig, name);
    for (const g of GROUPS) {
      this.from[g] = [...this.raw[g]];
      this.to[g] = target[g];
    }
    this._cue = name;
    this.fadeS = Math.max(0, fadeS);
    this.fadeT = 0;
    if (this.fadeS === 0) this.apply(1);
  }

  /** Advance the console by dt seconds and recompute the output. */
  update(dt: number) {
    if (!(dt > 0)) return;
    this.time += dt;
    const p = this._mode ? this.rig.modes[this._mode] : null;

    // ---- a show's cue hold pauses the program
    if (this.holdLeft > 0) {
      this.holdLeft = Math.max(0, this.holdLeft - dt);
      if (!this.holdLeft) this.resumeProgram();
    }

    // ---- program stepping
    let stepped = false;
    if (this.holdLeft > 0) {
      this.externalBeats = 0;
    } else if (p?.list && p.list.length > 1) {
      let steps = 0;
      if (p.bars) {
        const beatS = 60 / this._bpm;
        // An external beat source counts beats itself; while it is live (a beat within the
        // last two beat periods) the internal clock only fills gaps between them.
        let beats = this.externalBeats;
        this.externalBeats = 0;
        if (this.time - this.lastExternalAt > 2 * beatS) {
          this.beatClock += dt;
          beats += Math.floor(this.beatClock / beatS);
          this.beatClock %= beatS;
        }
        this.phase += beats;
        const per = p.bars * 4;
        steps = Math.floor(this.phase / per);
        this.phase -= steps * per;
      } else if (p.holdS) {
        this.phase += dt;
        const per = p.holdS + p.fadeS;
        steps = Math.floor(this.phase / per);
        this.phase -= steps * per;
      }
      if (steps > 0) {
        this.listIdx = (this.listIdx + steps) % p.list.length;
        this.goCue(p.list[this.listIdx], p.fadeS);
        // Time already spent past the step boundary counts toward the new fade.
        const into = p.bars ? this.phase * (60 / this._bpm) : this.phase;
        this.fadeT = Math.min(this.fadeS, into);
        stepped = true;
      }
    } else {
      this.externalBeats = 0;
    }

    // ---- fade (clamped: never past the target however large dt is)
    if (!stepped) this.fadeT = Math.min(this.fadeS, this.fadeT + dt);

    // ---- key lift eases in/out (~0.3 s)
    this.keyLift += (this.keyLiftTarget - this.keyLift) * (1 - Math.exp(-dt / 0.3));

    this.apply(this.fade);
  }

  private apply(t: number) {
    for (const g of GROUPS) {
      const a = this.from[g], b = this.to[g], r = this.raw[g], o = this.out[g];
      const k = g === 'droid_key' ? this.keyLift : 1;
      for (let i = 0; i < 3; i++) {
        r[i] = a[i] + (b[i] - a[i]) * t;
        o[i] = r[i] * k;
      }
    }
    this.version++;
  }
}

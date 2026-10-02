/**
 * The camera pane: a floating, draggable, resizable window over the 3D view showing this
 * browser's camera with R3X's face tracking drawn on top (`vision.faces` from r3x-vision).
 *
 * The picture is the browser's own capture (smooth, full frame rate, what a recording
 * needs); the boxes come from the runtime's own capture at its recognition cadence (2-5 Hz)
 * and glide between updates. Both see the same camera, so normalised boxes line up as long
 * as the two captures share an aspect ratio (r3x-vision captures 1280x720; this asks for 16:9).
 *
 * Tracking is the runtime's vision switch (console `vision on|off`): off closes R3X's camera
 * and stops recognition, the picture here keeps running. Placement, size and toggles are
 * this viewer's own (localStorage, try/catch). `V` shows or hides the pane.
 */

import type { GatewayClient, RetainedState } from './gateway';
import type { FaceBox } from './generated/FaceBox';

const KEY = 'r3x.cameraPane';
/** A face list older than this is dropped (the runtime sends an empty list when faces go;
 *  this covers a runtime that went away mid-track). */
const STALE_MS = 2500;
/** Box easing toward each new detection, per 60 Hz frame. */
const EASE = 0.35;
const MIN_W = 300;
/** Title bar height (CSS `.cam-head`). */
const HEAD_H = 34;

/** The pane. Header controls are compact chips: the panels' `button.toggle` is a full-width
 *  switch row, which would fill the title bar and leave nothing to grab. */
export const CAM_PANE_HTML = `
  <div class="cam-head">
    <span class="cam-title">R3X vision</span>
    <select class="cam-dev" title="Camera" hidden></select>
    <button class="cam-track cam-chip" aria-pressed="false" title="R3X's face tracking and recognition (the runtime's vision switch)">Tracking</button>
    <button class="cam-ov cam-chip" aria-pressed="true" title="Draw what R3X sees: face boxes, landmarks, who">Overlay</button>
    <button class="cam-mirror cam-chip" aria-pressed="false" title="Mirror the picture (selfie view); off is R3X's own view">Mirror</button>
    <button class="cam-close icon" aria-label="Close camera ( V )" title="Close ( V )">
      <svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 4 8 8M12 4l-8 8" /></svg>
    </button>
  </div>
  <div class="cam-body">
    <video muted playsinline autoplay></video>
    <canvas></canvas>
    <p class="cam-note"></p>
  </div>
  <div class="cam-grip" title="Resize"></div>`;

/** A press drags the pane from anywhere on it (title bar or picture) except a control or the grip. */
export function canStartDrag(target: Element): boolean {
  return !target.closest('button, select, .cam-grip');
}

/** Width between the minimum and the window, and the whole pane (title bar + picture) on screen. */
export function clampPane(p: { x: number; y: number; w: number }, aspect: number, vp: { w: number; h: number }) {
  const w = Math.max(MIN_W, Math.min(p.w, vp.w - 16));
  const h = w / aspect + HEAD_H;
  return {
    x: Math.min(Math.max(0, p.x), Math.max(0, vp.w - w)),
    y: Math.min(Math.max(0, p.y), Math.max(0, vp.h - h)),
    w,
  };
}

interface Stored {
  open: boolean;
  x: number;
  y: number;
  w: number;
  mirror: boolean;
  overlay: boolean;
  device: string;
}

function load(): Stored {
  const d: Stored = { open: false, x: -1, y: -1, w: 420, mirror: false, overlay: true, device: '' };
  try {
    return { ...d, ...(JSON.parse(localStorage.getItem(KEY) ?? '{}') as Partial<Stored>) };
  } catch {
    return d;
  }
}

function save(s: Stored) {
  try {
    localStorage.setItem(KEY, JSON.stringify(s));
  } catch {
    /* storage blocked: lasts this page load */
  }
}

interface Shown {
  box: [number, number, number, number];
  marks: [number, number][];
  face: FaceBox;
}

const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

export class CameraPane {
  private readonly st = load();
  private readonly el: HTMLDivElement;
  private readonly video: HTMLVideoElement;
  private readonly canvas: HTMLCanvasElement;
  private readonly note: HTMLElement;
  private readonly devices: HTMLSelectElement;
  private readonly trackBtn: HTMLButtonElement;
  private stream: MediaStream | null = null;
  private faces: FaceBox[] = [];
  private facesAt = 0;
  private shown: Shown[] = [];
  private raf = 0;
  private tracking: 'on' | 'off' | 'unavailable' = 'unavailable';
  private onToggle: ((open: boolean) => void)[] = [];

  constructor(private readonly gw: GatewayClient) {
    this.el = document.createElement('div');
    this.el.id = 'cam-pane';
    this.el.className = 'cam-pane';
    this.el.hidden = true;
    this.el.setAttribute('role', 'dialog');
    this.el.setAttribute('aria-label', 'Camera');
    this.el.innerHTML = CAM_PANE_HTML;
    document.body.appendChild(this.el);
    const q = <T extends Element>(s: string) => this.el.querySelector<T>(s)!;
    this.video = q('video');
    this.canvas = q('canvas');
    this.note = q('.cam-note');
    this.devices = q('.cam-dev');
    this.trackBtn = q('.cam-track');

    q<HTMLButtonElement>('.cam-close').onclick = () => this.setOpen(false);
    const ov = q<HTMLButtonElement>('.cam-ov');
    const mirror = q<HTMLButtonElement>('.cam-mirror');
    const syncToggles = () => {
      ov.setAttribute('aria-pressed', String(this.st.overlay));
      mirror.setAttribute('aria-pressed', String(this.st.mirror));
      this.video.classList.toggle('mirror', this.st.mirror);
    };
    ov.onclick = () => {
      this.st.overlay = !this.st.overlay;
      syncToggles();
      save(this.st);
    };
    mirror.onclick = () => {
      this.st.mirror = !this.st.mirror;
      syncToggles();
      save(this.st);
    };
    syncToggles();
    this.trackBtn.onclick = async () => {
      if (this.tracking === 'unavailable') return;
      this.trackBtn.disabled = true;
      const a = await this.gw.send({ class: 'intent', type: 'console', line: this.tracking === 'on' ? 'vision off' : 'vision on' });
      this.trackBtn.disabled = false;
      if (a.status === 'rejected') this.setNote(a.reason);
    };
    this.devices.onchange = () => {
      this.st.device = this.devices.value;
      save(this.st);
      if (!this.el.hidden) void this.startCamera();
    };

    this.dragging();
    this.resizing(q('.cam-grip'));
    addEventListener('resize', () => this.place());

    gw.subscribe({
      onHello: (h) => this.onState(h.state),
      onState: (s) => this.onState(s),
      onStatus: (on) => {
        if (!on) this.onState(null);
      },
      onEvent: (e) => {
        if (e.domain === 'vision' && e.type === 'faces') {
          this.faces = e.faces;
          this.facesAt = performance.now();
        }
      },
    });
    this.onState(gw.state);

    addEventListener('keydown', (e) => {
      const t = e.target as HTMLElement | null;
      if (e.defaultPrevented || e.repeat || e.metaKey || e.ctrlKey || e.altKey) return;
      if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
      if (e.key !== 'v' && e.key !== 'V') return;
      this.setOpen(!this.open);
      e.preventDefault();
    });
    if (this.st.open) this.setOpen(true);
  }

  get open() {
    return !this.el.hidden;
  }

  /** Called with the new open state whenever the pane opens or closes. */
  subscribe(fn: (open: boolean) => void) {
    this.onToggle.push(fn);
  }

  setOpen(open: boolean) {
    if (open === this.open) return;
    this.el.hidden = !open;
    this.st.open = open;
    save(this.st);
    if (open) {
      this.place();
      void this.startCamera();
      this.raf = requestAnimationFrame(this.draw);
    } else {
      this.stopCamera();
      cancelAnimationFrame(this.raf);
    }
    for (const fn of this.onToggle) fn(open);
  }

  private onState(s: RetainedState | null) {
    const h = s?.services.services['vision'];
    this.tracking = h?.status === 'running' ? 'on' : h?.status === 'stopped' ? 'off' : 'unavailable';
    this.trackBtn.setAttribute('aria-pressed', String(this.tracking === 'on'));
    this.trackBtn.disabled = this.tracking === 'unavailable';
    this.trackBtn.title =
      this.tracking === 'unavailable'
        ? s
          ? 'Vision is not running in the runtime (start it with vision on)'
          : 'r3x runtime offline'
        : "R3X's face tracking and recognition (the runtime's vision switch)";
    if (this.tracking !== 'on') this.faces = [];
  }

  private setNote(text: string) {
    this.note.textContent = text;
    this.note.hidden = !text;
  }

  private async startCamera() {
    this.stopCamera();
    if (!navigator.mediaDevices?.getUserMedia) {
      this.setNote('This browser cannot open a camera here (it needs localhost or HTTPS).');
      return;
    }
    this.setNote('Opening camera…');
    try {
      const video: MediaTrackConstraints = { width: { ideal: 1920 }, height: { ideal: 1080 }, aspectRatio: { ideal: 16 / 9 }, frameRate: { ideal: 30 } };
      if (this.st.device) video.deviceId = { exact: this.st.device };
      let stream: MediaStream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video, audio: false });
      } catch (e) {
        // The remembered camera is gone: fall back to the default.
        if (!this.st.device || (e as DOMException).name !== 'OverconstrainedError') throw e;
        delete video.deviceId;
        stream = await navigator.mediaDevices.getUserMedia({ video, audio: false });
      }
      if (this.el.hidden) {
        for (const t of stream.getTracks()) t.stop();
        return;
      }
      this.stream = stream;
      this.video.srcObject = stream;
      await this.video.play().catch(() => {});
      this.setNote('');
      await this.listDevices(stream.getVideoTracks()[0]?.getSettings().deviceId);
      this.place();
    } catch (e) {
      const err = e as DOMException;
      this.setNote(
        err.name === 'NotAllowedError'
          ? 'Camera blocked: allow it in the address bar'
          : err.name === 'NotReadableError'
            ? 'The camera is busy in another app.'
            : `Camera failed: ${err.message || err.name}`,
      );
    }
  }

  private stopCamera() {
    for (const t of this.stream?.getTracks() ?? []) t.stop();
    this.stream = null;
    this.video.srcObject = null;
  }

  private async listDevices(current?: string) {
    const all = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === 'videoinput');
    this.devices.innerHTML = '';
    for (const d of all) {
      const o = document.createElement('option');
      o.value = d.deviceId;
      o.textContent = d.label || `Camera ${this.devices.length + 1}`;
      this.devices.appendChild(o);
    }
    if (current) this.devices.value = current;
    this.devices.hidden = all.length < 2;
  }

  // ------------------------------------------------------------------ placement

  /** The picture's aspect (16:9 until the camera reports its own). */
  private aspect() {
    const { videoWidth: w, videoHeight: h } = this.video;
    return w && h ? w / h : 16 / 9;
  }

  /** Size and clamp the pane into the window; first open lands bottom left of the 3D view. */
  private place() {
    // A hidden tab reports a 0x0 window: placing then would pin the pane to the corner.
    if (this.el.hidden || !innerWidth || !innerHeight) return;
    if (this.st.x < 0 || this.st.y < 0) {
      const left = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--scene-space')) || 12;
      this.st.x = left + 12;
      this.st.y = innerHeight - (this.st.w / this.aspect() + HEAD_H) - 80;
    }
    Object.assign(this.st, clampPane(this.st, this.aspect(), { w: innerWidth, h: innerHeight }));
    Object.assign(this.el.style, { left: `${this.st.x}px`, top: `${this.st.y}px`, width: `${this.st.w}px` });
  }

  /** Drag from the title bar or the picture; the pane captures the pointer so a fast move
   *  that leaves it still drags. */
  private dragging() {
    const pane = this.el;
    pane.addEventListener('pointerdown', (e) => {
      if (e.button !== 0 || !canStartDrag(e.target as Element)) return;
      const sx = e.clientX - this.st.x;
      const sy = e.clientY - this.st.y;
      pane.setPointerCapture(e.pointerId);
      pane.classList.add('moving');
      e.preventDefault();
      const move = (m: PointerEvent) => {
        this.st.x = m.clientX - sx;
        this.st.y = m.clientY - sy;
        this.place();
      };
      const up = () => {
        pane.removeEventListener('pointermove', move);
        pane.classList.remove('moving');
        save(this.st);
      };
      pane.addEventListener('pointermove', move);
      pane.addEventListener('pointerup', up, { once: true });
      pane.addEventListener('pointercancel', up, { once: true });
    });
  }

  private resizing(grip: HTMLElement) {
    grip.addEventListener('pointerdown', (e) => {
      e.stopPropagation();
      const sx = e.clientX;
      const sw = this.st.w;
      grip.setPointerCapture(e.pointerId);
      this.el.classList.add('moving');
      const move = (m: PointerEvent) => {
        this.st.w = sw + (m.clientX - sx);
        this.place();
      };
      const up = () => {
        grip.removeEventListener('pointermove', move);
        this.el.classList.remove('moving');
        save(this.st);
      };
      grip.addEventListener('pointermove', move);
      grip.addEventListener('pointerup', up, { once: true });
      grip.addEventListener('pointercancel', up, { once: true });
      e.preventDefault();
    });
  }

  // ------------------------------------------------------------------ overlay

  private draw = () => {
    this.raf = requestAnimationFrame(this.draw);
    const c = this.canvas;
    const dpr = devicePixelRatio || 1;
    const w = Math.round(c.clientWidth * dpr);
    const h = Math.round(c.clientHeight * dpr);
    if (c.width !== w || c.height !== h) {
      c.width = w;
      c.height = h;
    }
    const g = c.getContext('2d')!;
    g.clearRect(0, 0, w, h);
    if (!this.st.overlay || !w || !h) return;

    const fresh = performance.now() - this.facesAt < STALE_MS ? this.faces : [];
    this.ease(fresh);
    // Mirroring flips positions only; labels stay readable.
    const mx = (x: number) => (this.st.mirror ? 1 - x : x);
    const s = dpr;
    for (const f of this.shown) {
      const [bx, by, bw, bh] = f.box;
      const x0 = mx(this.st.mirror ? bx + bw : bx) * w;
      const y0 = by * h;
      const pw = bw * w;
      const ph = bh * h;
      const known = !!f.face.name;
      const col = known ? '#57d38c' : f.face.primary ? '#e8762a' : 'rgba(217,220,227,.7)';
      g.strokeStyle = col;
      g.lineWidth = (f.face.primary ? 2 : 1.25) * s;
      // Corner brackets.
      const k = Math.min(pw, ph) * 0.22;
      g.beginPath();
      for (const [cx, cy, dx, dy] of [
        [x0, y0, 1, 1],
        [x0 + pw, y0, -1, 1],
        [x0, y0 + ph, 1, -1],
        [x0 + pw, y0 + ph, -1, -1],
      ]) {
        g.moveTo(cx + dx * k, cy);
        g.lineTo(cx, cy);
        g.lineTo(cx, cy + dy * k);
      }
      g.stroke();
      g.fillStyle = col;
      for (const [lx, ly] of f.marks) {
        g.beginPath();
        g.arc(mx(lx) * w, ly * h, 1.8 * s, 0, Math.PI * 2);
        g.fill();
      }
      if (f.face.primary) {
        // The gaze target: where R3X is told to look.
        const cx = x0 + pw / 2;
        const cy = y0 + ph / 2;
        g.lineWidth = 1 * s;
        g.beginPath();
        g.moveTo(cx - 6 * s, cy);
        g.lineTo(cx + 6 * s, cy);
        g.moveTo(cx, cy - 6 * s);
        g.lineTo(cx, cy + 6 * s);
        g.stroke();
      }
      const label = known
        ? `${f.face.name!.toUpperCase()}  ${f.face.similarity?.toFixed(2) ?? ''}`
        : f.face.primary
          ? `UNKNOWN${f.face.similarity !== undefined ? `  ${f.face.similarity.toFixed(2)}` : ''}`
          : '';
      if (label) {
        g.font = `600 ${11 * s}px ui-monospace, SFMono-Regular, Menlo, monospace`;
        const tw = g.measureText(label).width + 10 * s;
        const ty = Math.max(0, y0 - 18 * s);
        g.fillStyle = 'rgba(8,9,12,.78)';
        g.fillRect(x0, ty, tw, 16 * s);
        g.fillStyle = col;
        g.fillText(label, x0 + 5 * s, ty + 12 * s);
      }
    }
  };

  /** Glide shown boxes toward the latest detection (matched by order: largest first). */
  private ease(target: FaceBox[]) {
    const next: Shown[] = target.map((f, i) => {
      const prev = this.shown[i];
      if (!prev) return { box: [...f.bbox] as Shown['box'], marks: f.landmarks.map((p) => [...p] as [number, number]), face: f };
      return {
        box: prev.box.map((v, j) => lerp(v, f.bbox[j], EASE)) as Shown['box'],
        marks: prev.marks.map((p, j) => [lerp(p[0], f.landmarks[j][0], EASE), lerp(p[1], f.landmarks[j][1], EASE)] as [number, number]),
        face: f,
      };
    });
    this.shown = next;
  }
}

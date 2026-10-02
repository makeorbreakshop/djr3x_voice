/**
 * A part's display geometry made ready to draw, as plain typed arrays so it can run in a Web Worker
 * (geom.worker.ts) or, where no worker can start (Node's vitest, a locked-down page), right here.
 * Both paths run this one function, so the geometry is the same either way.
 *
 *   in    positions (in the part's frame, the GLB's quantization already undone), the index, and the
 *         Original's per-corner UVs (`<mesh>.uv.bin`, mech/workbench/uvtransfer.py) when it has them
 *   out   welded, creased normals (round faces round, hard edges sharp), and the feature edges
 *
 * This was the main thread's load-time work: ~1 s of EdgesGeometry (sliced, but every slice still
 * held a frame), ~0.5 s of mergeVertices / setIndex / toCreasedNormals in 70-170 ms long tasks.
 */
import * as THREE from 'three';
import { mergeVertices, toCreasedNormals } from 'three/addons/utils/BufferGeometryUtils.js';

/** Normals within this of each other are smoothed together; sharper creases stay hard. */
export const CREASE = (30 * Math.PI) / 180;
/** Feature edges: faces meeting at more than this (degrees) draw a line. */
export const EDGE_ANGLE = 40;

export interface GeomIn {
  position: Float32Array;
  index: Uint32Array | Uint16Array | null;
  uv: Float32Array | null;
}

export interface GeomOut {
  attributes: Record<string, { array: Float32Array; itemSize: number; normalized: boolean }>;
  index: Uint32Array | Uint16Array | null;
  /** The feature edges' line-segment positions (EdgesGeometry's), or null when not asked for. */
  edges: Float32Array | null;
}

/** A zero-length normal (a degenerate triangle in a decimated mesh) shades as NaN, and one
 *  NaN pixel spreads through the bloom into a white screen: give those an arbitrary unit normal. */
export function saneNormals(geo: THREE.BufferGeometry) {
  const n = geo.attributes.normal as THREE.BufferAttribute;
  for (let i = 0; i < n.count; i++) {
    const x = n.getX(i), y = n.getY(i), z = n.getZ(i);
    const l = Math.hypot(x, y, z);
    if (!Number.isFinite(l) || l < 1e-6) n.setXYZ(i, 0, 1, 0);
  }
}

export function processGeometry(input: GeomIn, edges = true): GeomOut {
  let geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(input.position, 3));
  if (input.index) geo.setIndex(new THREE.BufferAttribute(input.index, 1));
  // the Original's UVs, per face corner in the GLB's index order: unindex first, and the weld below
  // keeps the seams between UV islands split
  if (input.uv) {
    const n = geo.index ? geo.index.count : geo.attributes.position.count;
    if (input.uv.length === n * 2) {
      if (geo.index) geo = geo.toNonIndexed();
      geo.setAttribute('uv', new THREE.BufferAttribute(input.uv, 2));
    }
  }
  geo = toCreasedNormals(mergeVertices(geo, 1e-4), CREASE);
  saneNormals(geo);
  const attributes: GeomOut['attributes'] = {};
  for (const [k, a] of Object.entries(geo.attributes)) {
    const b = a as THREE.BufferAttribute;
    attributes[k] = { array: b.array as Float32Array, itemSize: b.itemSize, normalized: b.normalized };
  }
  const e = edges ? new THREE.EdgesGeometry(geo, EDGE_ANGLE) : null;
  return {
    attributes,
    index: (geo.index?.array as Uint32Array | Uint16Array | undefined) ?? null,
    edges: e ? (e.attributes.position.array as Float32Array) : null,
  };
}

/** Every buffer in a result, for a zero-copy postMessage. */
export function transferables(o: GeomOut): ArrayBuffer[] {
  const out = new Set<ArrayBuffer>();
  for (const a of Object.values(o.attributes)) out.add(a.array.buffer as ArrayBuffer);
  if (o.index) out.add(o.index.buffer as ArrayBuffer);
  if (o.edges) out.add(o.edges.buffer as ArrayBuffer);
  return [...out];
}

// ------------------------------------------------------------------ the pool

type Job = { id: number; resolve: (o: GeomOut) => void; reject: (e: unknown) => void; input: GeomIn; edges: boolean };

/**
 * A few workers (hardwareConcurrency - 1, at most 4: the GLB fetches and the GPU uploads still want
 * the main thread and a core), each fed the next job as it finishes one. Falls back to the main
 * thread for good when a worker cannot be made or fails (no Worker in Node, a CSP, a load error).
 */
class GeomPool {
  private workers: Worker[] = [];
  private idle: Worker[] = [];
  private queue: Job[] = [];
  private running = new Map<number, Job>();
  private next = 1;
  private broken = typeof Worker === 'undefined' || new URLSearchParams(globalThis.location?.search ?? '').get('geomworker') === '0';

  private start() {
    if (this.broken || this.workers.length) return;
    const n = Math.max(1, Math.min(4, (globalThis.navigator?.hardwareConcurrency ?? 2) - 1));
    try {
      for (let i = 0; i < n; i++) {
        const w = new Worker(new URL('./geom.worker.ts', import.meta.url), { type: 'module' });
        // a worker joins the pool once it says it is up: one that cannot load its module (a bad
        // path in a sub-path build, a CSP) errors before then, with the jobs still here to redo
        w.onmessage = (ev: MessageEvent<{ ready?: true; id?: number; out?: GeomOut; error?: string }>) => {
          if (ev.data.ready) {
            this.idle.push(w);
            this.pump();
            return;
          }
          const job = this.running.get(ev.data.id!);
          this.running.delete(ev.data.id!);
          this.idle.push(w);
          if (job) {
            if (ev.data.out) job.resolve(ev.data.out);
            else job.reject(new Error(ev.data.error ?? 'geometry worker'));
          }
          this.pump();
        };
        w.onerror = (e) => {
          e.preventDefault?.();
          this.fail();
        };
        this.workers.push(w);
      }
    } catch {
      this.fail();
    }
  }

  /** No workers from here on: what is queued or running is redone here. */
  private fail() {
    if (this.broken) return;
    this.broken = true;
    for (const w of this.workers) w.terminate();
    this.workers = [];
    this.idle = [];
    // a job already sent gave its buffers away: it fails (as a bad mesh would); the queued ones run here
    for (const j of this.running.values()) j.reject(new Error('geometry worker failed'));
    this.running.clear();
    const jobs = this.queue;
    this.queue = [];
    for (const j of jobs) this.local(j);
  }

  private local(j: Job) {
    try {
      j.resolve(processGeometry(j.input, j.edges));
    } catch (e) {
      j.reject(e);
    }
  }

  private pump() {
    while (this.idle.length && this.queue.length) {
      const w = this.idle.pop()!;
      const j = this.queue.shift()!;
      this.running.set(j.id, j);
      const send = j.input;
      const t = [send.position.buffer, send.index?.buffer, send.uv?.buffer].filter((b): b is ArrayBuffer => !!b);
      w.postMessage({ id: j.id, input: send, edges: j.edges }, t);
    }
  }

  run(input: GeomIn, edges: boolean): Promise<GeomOut> {
    this.start();
    if (this.broken) {
      try {
        return Promise.resolve(processGeometry(input, edges));
      } catch (e) {
        return Promise.reject(e);
      }
    }
    return new Promise((resolve, reject) => {
      this.queue.push({ id: this.next++, resolve, reject, input, edges });
      this.pump();
    });
  }
}

let pool: GeomPool | null = null;

/** Weld, crease and edge a part's geometry off the main thread when it can (see GeomPool). */
export function prepareGeometry(input: GeomIn, edges = true): Promise<GeomOut> {
  pool ??= new GeomPool();
  return pool.run(input, edges);
}

/** A result as three geometry; its feature edges (when computed) in `userData.edges`. */
export function toGeometry(o: GeomOut): THREE.BufferGeometry {
  const g = new THREE.BufferGeometry();
  for (const [k, a] of Object.entries(o.attributes)) g.setAttribute(k, new THREE.BufferAttribute(a.array, a.itemSize, a.normalized));
  if (o.index) g.setIndex(new THREE.BufferAttribute(o.index, 1));
  if (o.edges) g.userData.edges = o.edges;
  return g;
}

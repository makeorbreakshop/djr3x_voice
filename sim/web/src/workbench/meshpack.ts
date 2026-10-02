/**
 * Mesh packs (scripts/meshpack.mjs, written by publish-viewer.mjs): a published assembly folder's
 * overview GLBs and transferred UVs in one file, named in its manifest's `pack` (SCHEMA.md). A file
 * the packs hold is sliced out of the pack, fetched once; anything else (no pack: the dev server's
 * mech/out, a full-detail mesh, a pack that failed to load) comes from its own URL as before.
 */

export interface PackSpec {
  file: string;
  format?: string;
  /** 'gzip': the body is gzip (inflated here; the static hosts do not compress binary types). */
  encoding?: string;
  /** The inflated pack's length (preallocated for the streamed read). */
  size?: number;
  /** Path from the manifest's folder -> [byte offset, length] in the inflated pack. */
  entries: Record<string, [number, number]>;
}

const here = () => (globalThis as { location?: { href: string } }).location?.href ?? 'http://localhost/';

/** A file's identity: absolute, without the query (a content hash or reload counter). */
function key(url: string) {
  return new URL(url.split('?')[0], here()).href;
}

type Source = ArrayBuffer | ReadableStream<Uint8Array>;

/** A ReadableStream over `src` (an ArrayBuffer is one chunk). */
function streamOf(src: Source): ReadableStream<Uint8Array> {
  if (!(src instanceof ArrayBuffer)) return src;
  return new ReadableStream({ start(c) { c.enqueue(new Uint8Array(src)); c.close(); } });
}

/**
 * A pack read as it arrives: the body streamed (inflated on the way when it starts with the gzip
 * magic; a host that sent it with Content-Encoding hands over the inflated body), so a file near the
 * front (the publisher puts the shells first) is ready before the whole pack is in.
 */
class PackStream {
  private buf: Uint8Array;
  private filled = 0;
  private failed = false;
  private done = false;
  private waiters: { end: number; resolve: (ok: boolean) => void }[] = [];

  constructor(src: Promise<Source>, size?: number) {
    this.buf = new Uint8Array(size && size > 0 ? size : 1 << 20);
    void this.run(src);
  }

  private async run(src: Promise<Source>) {
    try {
      const reader = streamOf(await src).getReader();
      const first = await reader.read();
      const head = first.value ?? new Uint8Array(0);
      const rest = new ReadableStream<Uint8Array>({
        start(c) { if (head.length) c.enqueue(head); if (first.done) c.close(); },
        async pull(c) { const r = await reader.read(); if (r.done) c.close(); else c.enqueue(r.value); },
      });
      const gz = head[0] === 0x1f && head[1] === 0x8b;
      const out = (gz ? rest.pipeThrough(new DecompressionStream('gzip') as unknown as ReadableWritablePair<Uint8Array, Uint8Array>) : rest).getReader();
      for (;;) {
        const r = await out.read();
        if (r.done) break;
        this.append(r.value);
      }
      this.done = true;
    } catch (e) {
      console.warn('mesh pack', e);
      this.failed = true;
    }
    this.settle();
  }

  private append(c: Uint8Array) {
    if (this.filled + c.length > this.buf.length) {
      const next = new Uint8Array(Math.max(this.buf.length * 2, this.filled + c.length));
      next.set(this.buf.subarray(0, this.filled));
      this.buf = next;
    }
    this.buf.set(c, this.filled);
    this.filled += c.length;
    this.settle();
  }

  private settle() {
    this.waiters = this.waiters.filter((w) => {
      if (this.filled >= w.end) w.resolve(true);
      else if (this.failed || this.done) w.resolve(false);
      else return true;
      return false;
    });
  }

  /** Bytes [at, at + n) once they have arrived (a copy), or null (the pack failed or is shorter). */
  async slice(at: number, n: number): Promise<ArrayBuffer | null> {
    if (this.filled < at + n) {
      if (this.failed || this.done) return null;
      if (!(await new Promise<boolean>((resolve) => this.waiters.push({ end: at + n, resolve })))) return null;
    }
    return this.buf.slice(at, at + n).buffer;
  }
}

export class MeshPacks {
  private files = new Map<string, { load: () => PackStream; at: number; n: number }>();

  constructor(private readonly fetcher: (url: string) => Promise<Source> = async (u) => {
    const r = await fetch(u);
    if (!r.ok) throw new Error(`${u}: ${r.status}`);
    return r.body ?? r.arrayBuffer();
  }) {}

  /** Register the pack named by the manifest at `manifestUrl` (fetched on the first file asked of it). */
  add(manifestUrl: string, spec: PackSpec | undefined) {
    if (!spec?.file || !spec.entries) return;
    const packUrl = new URL(spec.file, new URL(manifestUrl, here())).href;
    let body: PackStream | null = null;
    const load = () => (body ??= new PackStream(this.fetcher(packUrl), spec.size));
    for (const [k, [at, n]] of Object.entries(spec.entries)) this.files.set(key(new URL(k, packUrl).href), { load, at, n });
  }

  has(url: string) {
    return this.files.has(key(url));
  }

  /** The file's bytes from its pack (as soon as they have streamed in), or null (not packed, or the pack did not load). */
  async get(url: string): Promise<ArrayBuffer | null> {
    const f = this.files.get(key(url));
    if (!f) return null;
    return f.load().slice(f.at, f.n);
  }
}

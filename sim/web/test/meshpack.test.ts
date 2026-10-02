import { describe, expect, it } from 'vitest';
import { MeshPacks } from '../src/workbench/meshpack';

const body = new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8, 9]);
const spec = { file: 'meshes.pack', encoding: 'gzip', entries: { 'parts/a.glb': [0, 4] as [number, number], 'parts/a.uv.bin': [4, 5] as [number, number] } };
const gzip = (b: Uint8Array) => new Response(new Blob([b as BlobPart]).stream().pipeThrough(new CompressionStream('gzip'))).arrayBuffer();
const M = 'http://host/data/asm/manifest.json';

describe('MeshPacks', () => {
  it('slices packed files (gzip inflated, query ignored) and fetches the pack once', async () => {
    let n = 0;
    const p = new MeshPacks(async () => { n++; return gzip(body); });
    p.add(M, spec);
    expect(p.has('http://host/data/asm/parts/a.glb?v=abc')).toBe(true);
    expect([...new Uint8Array((await p.get('http://host/data/asm/parts/a.glb'))!)]).toEqual([1, 2, 3, 4]);
    expect([...new Uint8Array((await p.get('http://host/data/asm/parts/a.uv.bin'))!)]).toEqual([5, 6, 7, 8, 9]);
    expect(n).toBe(1);
  });
  it('falls back: an unpacked file, no pack, or a pack that fails to load gives null (fetch it by URL)', async () => {
    const none = new MeshPacks(async () => body.buffer);
    none.add(M, undefined);
    expect(none.has('http://host/data/asm/parts/a.glb')).toBe(false);
    expect(await none.get('http://host/data/asm/parts/a.glb')).toBeNull();
    const broken = new MeshPacks(async () => { throw new Error('404'); });
    broken.add(M, spec);
    expect(await broken.get('http://host/data/asm/parts/b.glb')).toBeNull();
    expect(await broken.get('http://host/data/asm/parts/a.glb')).toBeNull();
  });
  it('streams: a file near the front is ready before the rest of the pack has arrived', async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    const p = new MeshPacks(async () => new ReadableStream<Uint8Array>({
      async start(c) { c.enqueue(body.slice(0, 4)); await gate; c.enqueue(body.slice(4)); c.close(); },
    }));
    p.add(M, { ...spec, encoding: undefined, size: body.length });
    const late = p.get('http://host/data/asm/parts/a.uv.bin');
    let lateDone = false;
    void late.then(() => (lateDone = true));
    expect([...new Uint8Array((await p.get('http://host/data/asm/parts/a.glb'))!)]).toEqual([1, 2, 3, 4]);
    expect(lateDone).toBe(false);
    release();
    expect([...new Uint8Array((await late)!)]).toEqual([5, 6, 7, 8, 9]);
  });
});


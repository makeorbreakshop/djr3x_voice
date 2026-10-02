/** The geometry pool's worker (geomwork.ts): weld, crease normals and feature edges off the main thread. */
import { processGeometry, transferables, type GeomIn } from './geomwork';

const scope = self as unknown as { postMessage(m: unknown, t?: Transferable[]): void; onmessage: ((e: MessageEvent) => void) | null };
scope.onmessage = (e: MessageEvent<{ id: number; input: GeomIn; edges: boolean }>) => {
  const { id, input, edges } = e.data;
  try {
    const out = processGeometry(input, edges);
    scope.postMessage({ id, out }, transferables(out));
  } catch (err) {
    scope.postMessage({ id, error: String(err) });
  }
};
scope.postMessage({ ready: true });

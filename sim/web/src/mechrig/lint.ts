/**
 * Studio lint, torque: a clip whose required servo torque (mechrig/torque.ts, played from
 * the rest pose) exceeds the 70 % rule gets a mark at the peak, on the servo's joint row.
 */

import { evalKeys, fromDoc, type ClipDoc } from '../studio/model';
import type { LintMark } from '../studio/model';
import { clipLoads, RULE, type ClipTrack } from './torque';

export function clipTracks(doc: ClipDoc): ClipTrack[] {
  return fromDoc(doc).tracks
    .filter((t) => t.keys.length)
    .map((t) => ({ joint: t.joint, value: (u: number) => evalKeys(t.keys, u) }));
}

export function torqueMarks(doc: ClipDoc): LintMark[] {
  if (!doc.tracks || !Object.keys(doc.tracks).length || !(doc.duration > 0)) return [];
  const tracks = clipTracks(doc);
  const authored = new Set(tracks.map((t) => t.joint));
  return clipLoads(tracks, doc.duration, 50)
    .filter((l) => l.peak > RULE)
    .map((l) => ({
      joint: l.joints.find((j) => authored.has(j)) ?? l.joints[0],
      t: l.at,
      message: `${doc.id}.${l.joints.join('+')}: ${l.servo} needs ${l.peakNm.toFixed(2)} N m = ${(l.peak * 100).toFixed(0)} % of stall at t=${l.at.toFixed(2)} (70 % rule; mech model torque)`,
    }));
}

/**
 * Live tempo from CantinaOS.
 *
 * `music.playback.started` carries `track.bpm` once the track has been through the offline
 * beat analysis (cantina_os/.../music_controller_service/beat_analysis.py); a crossfade
 * re-announces the new track. Absent or implausible means "no tempo known", and the sim
 * keeps whatever the BPM slider says.
 */
export const LIVE_BPM_MIN = 40;
export const LIVE_BPM_MAX = 250;

export function trackBpm(d: unknown): number | null {
  if (!d || typeof d !== 'object') return null;
  const track = (d as { track?: unknown }).track;
  if (!track || typeof track !== 'object') return null;
  const t = track as Record<string, unknown>;
  const raw = t.bpm ?? t.tempo;
  const n = typeof raw === 'string' ? Number(raw) : raw;
  return typeof n === 'number' && Number.isFinite(n) && n >= LIVE_BPM_MIN && n <= LIVE_BPM_MAX ? n : null;
}

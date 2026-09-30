//! Live tempo from CantinaOS (port of `tempo.ts`): `music.playback.started` carries
//! `track.bpm` once the track has been analysed. Absent or implausible means unknown.

use serde_json::Value;

pub const LIVE_BPM_MIN: f64 = 40.0;
pub const LIVE_BPM_MAX: f64 = 250.0;

pub fn track_bpm(d: &Value) -> Option<f64> {
    let t = d.get("track")?.as_object()?;
    let raw = t
        .get("bpm")
        .filter(|v| !v.is_null())
        .or_else(|| t.get("tempo"))?;
    let n = match raw {
        Value::String(s) => s.trim().parse::<f64>().ok()?,
        v => v.as_f64()?,
    };
    (n.is_finite() && (LIVE_BPM_MIN..=LIVE_BPM_MAX).contains(&n)).then_some(n)
}

//! Tempo and beat grid per local track, cached on disk, analysed off the playback path.
//!
//! Port of CantinaOS `music_controller_service/beat_analysis.py` (plan §7a, Phase 4):
//! - method: librosa 0.11 `beat.beat_track` ported in [`dsp`] (same onset envelope, tempogram
//!   prior and DP tracker), then the same `refine_bpm`, octave fold into 70-180 and grid phase;
//! - cache key = absolute path + mtime + size + [`ANALYZER_VERSION`] (bumped from
//!   `librosa-beat_track-3`, so the old cache is never mistaken for ours); one JSON sidecar per
//!   file, named by a hash of the path, in its own folder (`R3X_BEAT_CACHE_DIR`, default
//!   `~/.cache/dj-r3x/beats-r3x`) so CantinaOS's cache keeps working during the migration;
//! - [`Background`] analyses on a low-priority thread; fail-open (no bpm, never an error).

pub mod dsp;

use std::path::{Path, PathBuf};
use std::sync::mpsc;
use std::time::UNIX_EPOCH;

use anyhow::{anyhow, Result};
use serde::{Deserialize, Serialize};

/// Bump when the method changes; every older cached result is ignored.
pub const ANALYZER_VERSION: &str = "r3x-beats-1";
/// CantinaOS's librosa analyser, for the comparison report.
pub const LIBROSA_VERSION: &str = "librosa-beat_track-3";
pub const BPM_MIN: f64 = 70.0;
pub const BPM_MAX: f64 = 180.0;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BeatInfo {
    pub bpm: f64,
    /// Beat-grid phase: beat `n` falls at `first_beat_s + n * 60 / bpm`.
    pub first_beat_s: f64,
    #[serde(default)]
    pub beats: Vec<f64>,
}

/// Fold an octave error into `[70, 180)`: 60 -> 120, 240 -> 120.
pub fn fold_bpm(bpm: f64) -> f64 {
    if bpm.is_nan() || bpm <= 0.0 {
        return 0.0;
    }
    let mut b = bpm;
    while b < BPM_MIN {
        b *= 2.0;
    }
    while b >= BPM_MAX {
        b /= 2.0;
    }
    b
}

/// Mean of the inter-beat intervals within 15% of the median (averages the 23 ms hop grid away).
pub fn refine_bpm(beats: &[f64], fallback: f64) -> f64 {
    if beats.len() < 8 {
        return fallback;
    }
    let mut ibis: Vec<f64> = beats.windows(2).map(|w| w[1] - w[0]).filter(|d| *d > 0.0).collect();
    if ibis.is_empty() {
        return fallback;
    }
    let mut sorted = ibis.clone();
    sorted.sort_by(|a, b| a.total_cmp(b));
    let med = sorted[sorted.len() / 2];
    ibis.retain(|x| (x - med).abs() <= 0.15 * med);
    if ibis.len() < 4 {
        return fallback;
    }
    60.0 / (ibis.iter().sum::<f64>() / ibis.len() as f64)
}

/// Grid phase: the first tracked beat stepped back whole periods towards 0.
pub fn grid_phase(beats: &[f64], bpm: f64) -> f64 {
    match beats.first() {
        Some(&b) if bpm > 0.0 => round3(b.rem_euclid(60.0 / bpm)),
        _ => 0.0,
    }
}

fn round3(v: f64) -> f64 {
    (v * 1000.0).round() / 1000.0
}

/// Tempo and beats of mono 22.05 kHz samples.
pub fn analyze_samples(y: &[f32]) -> Result<BeatInfo> {
    let (raw, beats) = dsp::beat_track(y);
    let beats: Vec<f64> = beats.into_iter().map(round3).collect();
    let raw = refine_bpm(&beats, raw);
    let bpm = (fold_bpm(raw) * 10.0).round() / 10.0;
    if bpm <= 0.0 {
        return Err(anyhow!("no tempo found (raw {raw})"));
    }
    Ok(BeatInfo { bpm, first_beat_s: grid_phase(&beats, raw), beats })
}

/// Decode and analyse one file. Heavy (seconds): never on the playback path.
pub fn analyze_file(path: &Path) -> Result<BeatInfo> {
    let y = r3x_audio::decode::decode_file(path, dsp::SR, 1)?.swap_remove(0);
    if y.is_empty() {
        return Err(anyhow!("empty audio"));
    }
    analyze_samples(&y)
}

// ------------------------------------------------------------------------------------ cache

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CacheKey {
    pub path: String,
    pub mtime: f64,
    pub size: u64,
    pub version: String,
}

/// `os.path.abspath`: absolute and lexically normalised (`..` removed), symlinks kept.
pub fn abspath(path: &Path) -> PathBuf {
    use std::path::Component;
    let abs = std::path::absolute(path).unwrap_or_else(|_| path.to_owned());
    let mut out = PathBuf::new();
    for c in abs.components() {
        match c {
            Component::ParentDir => {
                out.pop();
            }
            Component::CurDir => {}
            c => out.push(c),
        }
    }
    out
}

impl CacheKey {
    pub fn of(path: &Path, version: &str) -> Option<Self> {
        let abs = abspath(path);
        let md = std::fs::metadata(&abs).ok()?;
        let mtime = md.modified().ok()?.duration_since(UNIX_EPOCH).ok()?.as_secs_f64();
        Some(Self { path: abs.to_string_lossy().into_owned(), mtime, size: md.len(), version: version.into() })
    }

    /// Same file version, allowing for float formatting of mtime across writers.
    fn matches(&self, other: &CacheKey) -> bool {
        self.path == other.path && self.size == other.size && self.version == other.version && (self.mtime - other.mtime).abs() < 1e-5
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct Record {
    #[serde(flatten)]
    key: CacheKey,
    #[serde(flatten, default)]
    info: Option<BeatInfo>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    error: Option<String>,
}

#[derive(Debug, Clone)]
pub struct BeatCache {
    pub dir: PathBuf,
    pub version: String,
}

pub fn default_cache_dir() -> PathBuf {
    if let Some(d) = std::env::var_os("R3X_BEAT_CACHE_DIR").filter(|d| !d.is_empty()) {
        return PathBuf::from(d);
    }
    home().join(".cache/dj-r3x/beats-r3x")
}

/// CantinaOS's librosa cache (`BEAT_CACHE_DIR`, default `~/.cache/dj-r3x/beats`).
pub fn librosa_cache_dir() -> PathBuf {
    std::env::var_os("BEAT_CACHE_DIR").filter(|d| !d.is_empty()).map(PathBuf::from).unwrap_or_else(|| home().join(".cache/dj-r3x/beats"))
}

fn home() -> PathBuf {
    std::env::var_os("HOME").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("."))
}

impl BeatCache {
    pub fn new(dir: PathBuf) -> Self {
        Self { dir, version: ANALYZER_VERSION.into() }
    }

    /// Read-only view of CantinaOS's librosa cache (same file naming and keys).
    pub fn librosa(dir: PathBuf) -> Self {
        Self { dir, version: LIBROSA_VERSION.into() }
    }

    fn file_for(&self, path: &Path) -> PathBuf {
        let abs = abspath(path);
        let digest = sha1_smol::Sha1::from(abs.to_string_lossy().as_bytes()).digest().to_string();
        self.dir.join(format!("{}.json", &digest[..20]))
    }

    fn record(&self, path: &Path) -> Option<(CacheKey, Record)> {
        let key = CacheKey::of(path, &self.version)?;
        let rec: Record = serde_json::from_slice(&std::fs::read(self.file_for(path)).ok()?).ok()?;
        rec.key.matches(&key).then_some((key, rec))
    }

    /// A still-valid result, or `None`. Never errors.
    pub fn get(&self, path: &Path) -> Option<BeatInfo> {
        let (_, rec) = self.record(path)?;
        rec.info.filter(|i| rec.error.is_none() && i.bpm > 0.0)
    }

    /// This exact file version was attempted (success or failure): do not re-analyse.
    pub fn has_entry(&self, path: &Path) -> bool {
        self.record(path).is_some()
    }

    pub fn put(&self, path: &Path, result: &Result<BeatInfo>) -> Result<()> {
        let key = CacheKey::of(path, &self.version).ok_or_else(|| anyhow!("cannot stat {}", path.display()))?;
        let rec = match result {
            Ok(i) => Record { key, info: Some(i.clone()), error: None },
            Err(e) => Record { key, info: None, error: Some(e.to_string().chars().take(300).collect()) },
        };
        std::fs::create_dir_all(&self.dir)?;
        let target = self.file_for(path);
        let tmp = target.with_extension("tmp");
        std::fs::write(&tmp, serde_json::to_vec(&rec)?)?;
        std::fs::rename(tmp, target)?; // atomic: readers never see half a file
        Ok(())
    }

    /// Cached result, else analyse now and cache (blocking).
    pub fn get_or_analyze(&self, path: &Path, force: bool) -> Result<BeatInfo> {
        if !force {
            if let Some(i) = self.get(path) {
                return Ok(i);
            }
        }
        let r = analyze_file(path);
        if let Err(e) = self.put(path, &r) {
            tracing::warn!("beat cache write failed: {e}");
        }
        r
    }
}

// ------------------------------------------------------------------------------- background

/// Lower this thread's priority (macOS QoS background; Linux nice 10).
fn lower_priority() {
    #[cfg(target_os = "macos")]
    unsafe {
        libc::pthread_set_qos_class_self_np(libc::qos_class_t::QOS_CLASS_UTILITY, 0);
    }
    #[cfg(target_os = "linux")]
    unsafe {
        let tid = libc::syscall(libc::SYS_gettid) as libc::id_t;
        libc::setpriority(libc::PRIO_PROCESS, tid, 10);
    }
}

/// Analyses files the cache does not know on one low-priority thread; results arrive on
/// the returned channel as `(path, BeatInfo)` (failures are cached and not sent).
pub struct Background;

impl Background {
    pub fn spawn(cache: BeatCache, paths: Vec<PathBuf>) -> mpsc::Receiver<(PathBuf, BeatInfo)> {
        let (tx, rx) = mpsc::channel();
        let todo: Vec<PathBuf> = paths.into_iter().filter(|p| !cache.has_entry(p)).collect();
        if todo.is_empty() {
            return rx;
        }
        tracing::info!("beat analysis: {} track(s) queued on a background thread", todo.len());
        let spawned = std::thread::Builder::new().name("r3x-beats".into()).spawn(move || {
            lower_priority();
            let mut ok = 0;
            for p in &todo {
                match cache.get_or_analyze(p, false) {
                    Ok(info) => {
                        ok += 1;
                        if tx.send((p.clone(), info)).is_err() {
                            return;
                        }
                    }
                    Err(e) => tracing::info!("beat analysis: no tempo for {}: {e}", p.display()),
                }
            }
            tracing::info!("beat analysis finished: {ok}/{} track(s) have a tempo", todo.len());
        });
        if let Err(e) = spawned {
            tracing::warn!("beat analysis unavailable: {e}");
        }
        rx
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fold_refine_phase_match_python() {
        assert_eq!(fold_bpm(60.0), 120.0);
        assert_eq!(fold_bpm(240.0), 120.0);
        assert_eq!(fold_bpm(180.0), 90.0);
        assert_eq!(fold_bpm(0.0), 0.0);
        let beats: Vec<f64> = (0..20).map(|i| 0.07 + i as f64 * 0.5).collect();
        assert!((refine_bpm(&beats, 99.0) - 120.0).abs() < 1e-9);
        assert_eq!(refine_bpm(&beats[..5], 99.0), 99.0);
        assert_eq!(grid_phase(&[1.07, 1.57], 120.0), 0.07);
    }

    #[test]
    fn cache_is_keyed_on_file_version() {
        let dir = std::env::temp_dir().join(format!("r3x-beats-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let f = dir.join("a.wav");
        std::fs::write(&f, b"x").unwrap();
        let c = BeatCache::new(dir.join("cache"));
        assert!(!c.has_entry(&f));
        let info = BeatInfo { bpm: 120.0, first_beat_s: 0.1, beats: vec![0.1] };
        c.put(&f, &Ok(info.clone())).unwrap();
        assert_eq!(c.get(&f), Some(info));
        c.put(&f, &Err(anyhow!("undecodable"))).unwrap();
        assert!(c.has_entry(&f) && c.get(&f).is_none(), "failures are remembered");
        std::fs::write(&f, b"xy").unwrap(); // size changes -> stale
        assert!(!c.has_entry(&f));
        let other = BeatCache { dir: c.dir.clone(), version: "other".into() };
        assert!(!other.has_entry(&f));
        std::fs::remove_dir_all(dir).ok();
    }
}

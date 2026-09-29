//! Enrolled people: per-person embeddings from a folder of photos, nearest-neighbour matching,
//! and the leave-one-out threshold tuning the `r3x-vision eval` command reports.
//!
//! Layout on disk (`R3X_VISION_GALLERY`, else `~/.local/share/dj-r3x/vision/gallery.json`):
//! `{"embedder": "sface_2021dec", "threshold": 0.5, "people": {"Brandon": [[..128..], ...]}}`.
//! Photos come from `<dir>/<Name>/*.jpg` (the CantinaOS `vision_data/training/` layout).

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::face::{cosine, FaceEngine, EMBEDDER_ID};
use crate::frame::Frame;
use crate::VisionError;

/// Cosine similarity needed for a match. `r3x-vision eval` (2026-09-29): Brandon's 20 training
/// photos leave-one-out score 0.747-0.889; 5,749 LFW impostors (one photo per person) max
/// 0.472, 0.03% false accepts at 0.45, none at 0.50. SFace's published point (0.363) would
/// false-accept 1%. A mistaken identity writes to someone's memory, so err strict.
pub const DEFAULT_THRESHOLD: f32 = 0.50;

pub fn default_path() -> PathBuf {
    if let Some(p) = std::env::var_os("R3X_VISION_GALLERY") {
        return p.into();
    }
    let home = std::env::var_os("HOME").unwrap_or_default();
    Path::new(&home).join(".local/share/dj-r3x/vision/gallery.json")
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Gallery {
    pub embedder: String,
    pub threshold: f32,
    pub people: BTreeMap<String, Vec<Vec<f32>>>,
}

impl Default for Gallery {
    fn default() -> Self {
        Self { embedder: EMBEDDER_ID.into(), threshold: DEFAULT_THRESHOLD, people: BTreeMap::new() }
    }
}

/// Best match: name and cosine similarity.
#[derive(Debug, Clone, PartialEq)]
pub struct Match {
    pub name: String,
    pub similarity: f32,
}

impl Gallery {
    pub fn load(path: &Path) -> Result<Self, VisionError> {
        let g: Gallery = serde_json::from_str(&std::fs::read_to_string(path)?).map_err(|e| VisionError::Gallery(format!("{}: {e}", path.display())))?;
        if g.embedder != EMBEDDER_ID {
            return Err(VisionError::Gallery(format!("{} was built with {}, not {EMBEDDER_ID}; re-enrol", path.display(), g.embedder)));
        }
        Ok(g)
    }

    pub fn save(&self, path: &Path) -> Result<(), VisionError> {
        if let Some(d) = path.parent() {
            std::fs::create_dir_all(d)?;
        }
        std::fs::write(path, serde_json::to_string(self).map_err(|e| VisionError::Gallery(e.to_string()))?)?;
        Ok(())
    }

    /// Nearest enrolled embedding over everyone, whatever the threshold.
    pub fn nearest(&self, e: &[f32]) -> Option<Match> {
        self.people
            .iter()
            .flat_map(|(n, es)| es.iter().map(move |x| (n, cosine(e, x))))
            .max_by(|a, b| a.1.total_cmp(&b.1))
            .map(|(n, s)| Match { name: n.clone(), similarity: s })
    }

    /// The nearest person if at or above the threshold.
    pub fn identify(&self, e: &[f32]) -> Option<Match> {
        self.nearest(e).filter(|m| m.similarity >= self.threshold)
    }
}

/// Image files directly in `dir`, sorted.
pub fn images_in(dir: &Path) -> Result<Vec<PathBuf>, VisionError> {
    let mut v: Vec<PathBuf> = std::fs::read_dir(dir)?
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| p.extension().and_then(|x| x.to_str()).is_some_and(|x| matches!(x.to_ascii_lowercase().as_str(), "jpg" | "jpeg" | "png")))
        .collect();
    v.sort();
    Ok(v)
}

/// Embeddings of the largest face in each photo; photos with no face are reported and skipped.
pub fn embed_photos(engine: &mut FaceEngine, photos: &[PathBuf]) -> Result<Vec<(PathBuf, Vec<f32>)>, VisionError> {
    let mut out = Vec::new();
    for p in photos {
        match engine.largest(&Frame::open(p)?)? {
            Some((_, e)) => out.push((p.clone(), e)),
            None => tracing::warn!("no face in {}", p.display()),
        }
    }
    Ok(out)
}

/// Enrol every `<root>/<Name>/` folder (or just `name` from `root` itself when given).
/// Replaces those people in `gallery`; returns (name, photos used, photos without a face).
pub fn enroll(engine: &mut FaceEngine, gallery: &mut Gallery, root: &Path, name: Option<&str>) -> Result<Vec<(String, usize, usize)>, VisionError> {
    let folders: Vec<(String, PathBuf)> = match name {
        Some(n) => vec![(n.to_string(), root.to_path_buf())],
        None => {
            let mut v: Vec<(String, PathBuf)> = std::fs::read_dir(root)?
                .filter_map(|e| e.ok().map(|e| e.path()))
                .filter(|p| p.is_dir())
                .filter_map(|p| Some((p.file_name()?.to_str()?.to_string(), p)))
                .collect();
            v.sort();
            v
        }
    };
    let mut report = Vec::new();
    for (n, dir) in folders {
        let photos = images_in(&dir)?;
        let embs = embed_photos(engine, &photos)?;
        if embs.is_empty() {
            return Err(VisionError::Gallery(format!("no usable face photos for {n} in {}", dir.display())));
        }
        report.push((n.clone(), embs.len(), photos.len() - embs.len()));
        gallery.people.insert(n, embs.into_iter().map(|(_, e)| e).collect());
    }
    Ok(report)
}

/// Leave-one-out scores for tuning: for each genuine photo, its best similarity to the *other*
/// photos of the same person (genuine), and each impostor's best similarity to anyone.
#[derive(Debug, Clone, Default)]
pub struct Evaluation {
    pub genuine: Vec<f32>,
    pub impostor: Vec<f32>,
}

impl Evaluation {
    pub fn leave_one_out(people: &BTreeMap<String, Vec<Vec<f32>>>, impostors: &[Vec<f32>]) -> Self {
        let mut genuine = Vec::new();
        for es in people.values() {
            for (i, e) in es.iter().enumerate() {
                let best = es.iter().enumerate().filter(|(j, _)| *j != i).map(|(_, x)| cosine(e, x)).fold(f32::MIN, f32::max);
                if best > f32::MIN {
                    genuine.push(best);
                }
            }
        }
        let impostor = impostors
            .iter()
            .map(|e| people.values().flatten().map(|x| cosine(e, x)).fold(f32::MIN, f32::max))
            .collect();
        Self { genuine, impostor }
    }

    /// (true-accept rate, false-accept rate) at `t`.
    pub fn rates(&self, t: f32) -> (f32, f32) {
        let frac = |v: &[f32]| if v.is_empty() { 0.0 } else { v.iter().filter(|s| **s >= t).count() as f32 / v.len() as f32 };
        (frac(&self.genuine), frac(&self.impostor))
    }

    /// Midpoint of the gap between the worst genuine and best impostor when they separate;
    /// otherwise the lowest threshold with no false accepts.
    pub fn suggest(&self) -> f32 {
        let min_g = self.genuine.iter().copied().fold(f32::MAX, f32::min);
        let max_i = self.impostor.iter().copied().fold(f32::MIN, f32::max);
        if min_g > max_i {
            (min_g + max_i) / 2.0
        } else {
            max_i + 1e-3
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn identify_respects_threshold_and_loo_rates() {
        let people: BTreeMap<_, _> = [("A".to_string(), vec![vec![1.0, 0.0], vec![0.8, 0.6]])].into();
        let g = Gallery { people: people.clone(), threshold: 0.9, ..Default::default() };
        assert_eq!(g.identify(&[0.8, 0.6]).unwrap().name, "A");
        assert!(g.identify(&[0.0, 1.0]).is_none());
        let ev = Evaluation::leave_one_out(&people, &[vec![0.0, 1.0]]);
        assert_eq!(ev.genuine, [0.8, 0.8]);
        assert_eq!(ev.impostor, [0.6]);
        assert!((ev.suggest() - 0.7).abs() < 1e-6);
        assert_eq!(ev.rates(0.7), (1.0, 0.0));
    }
}

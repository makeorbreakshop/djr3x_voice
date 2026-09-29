//! Face detection (YuNet 2023mar) and embeddings (SFace 2021dec) on `ort`, matching OpenCV's
//! `FaceDetectorYN` / `FaceRecognizerSF` (both OpenCV-zoo models, MIT / Apache-2.0; fetched by
//! `crates/r3x-vision/scripts/download_models.sh`, never committed).
//!
//! Detection: letterbox into the fixed 640x640 input (BGR, 0-255), decode the 3 strides, NMS.
//! Embedding: similarity-align the 5 landmarks onto the ArcFace 112x112 template, RGB 0-255 in,
//! 128-d out, L2-normalised (cosine = dot).

use std::path::{Path, PathBuf};

use ort::session::Session;
use ort::value::Tensor;

use crate::frame::Frame;
use crate::VisionError;

pub const DET_SIZE: usize = 640;
pub const ALIGN_SIZE: usize = 112;
const STRIDES: [usize; 3] = [8, 16, 32];
/// ArcFace's 112x112 landmark template (eyes, nose, mouth corners), as OpenCV `alignCrop`.
const TEMPLATE: [[f32; 2]; 5] = [[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366], [41.5493, 92.3655], [70.7299, 92.2041]];

pub const DETECTOR_FILE: &str = "yunet.onnx";
pub const EMBEDDER_FILE: &str = "sface.onnx";
/// Identifies the embedding space a gallery was built in.
pub const EMBEDDER_ID: &str = "sface_2021dec";

/// `R3X_VISION_MODELS`, else `~/.cache/dj-r3x/vision`.
pub fn model_dir() -> PathBuf {
    if let Some(d) = std::env::var_os("R3X_VISION_MODELS") {
        return d.into();
    }
    let home = std::env::var_os("HOME").unwrap_or_default();
    Path::new(&home).join(".cache/dj-r3x/vision")
}

/// Both model files present.
pub fn models_available(dir: &Path) -> bool {
    dir.join(DETECTOR_FILE).is_file() && dir.join(EMBEDDER_FILE).is_file()
}

#[derive(Debug, Clone, PartialEq)]
pub struct Face {
    /// x, y, w, h in source pixels.
    pub bbox: [f32; 4],
    /// Right eye, left eye, nose, right mouth corner, left mouth corner (image left to right).
    pub landmarks: [[f32; 2]; 5],
    pub score: f32,
}

impl Face {
    pub fn area(&self) -> f32 {
        self.bbox[2] * self.bbox[3]
    }
}

/// CoreML first on macOS (feature `coreml`, `R3X_VISION_EP` != `cpu`), CPU otherwise. ort
/// falls back to CPU by itself when CoreML cannot take a node.
fn session(path: &Path) -> Result<Session, VisionError> {
    let err = |e: ort::Error| VisionError::Model(format!("{}: {e}", path.display()));
    #[allow(unused_mut)]
    let mut b = Session::builder().map_err(err)?.with_intra_threads(2).map_err(err)?;
    #[cfg(all(feature = "coreml", target_os = "macos"))]
    if std::env::var("R3X_VISION_EP").map_or(true, |v| v != "cpu") {
        use ort::execution_providers::CoreMLExecutionProvider;
        b = b.with_execution_providers([CoreMLExecutionProvider::default().build()]).map_err(err)?;
    }
    if !path.is_file() {
        return Err(VisionError::Model(format!("{} missing; run crates/r3x-vision/scripts/download_models.sh", path.display())));
    }
    b.commit_from_file(path).map_err(err)
}

pub struct Detector {
    session: Session,
    /// Minimum `sqrt(cls * obj)`. OpenCV's default is 0.9; the training photos score 0.67-0.92.
    pub score_threshold: f32,
    pub nms_iou: f32,
}

impl Detector {
    pub fn load(dir: &Path) -> Result<Self, VisionError> {
        Ok(Self { session: session(&dir.join(DETECTOR_FILE))?, score_threshold: 0.6, nms_iou: 0.3 })
    }

    /// Faces, best score first.
    pub fn detect(&mut self, frame: &Frame) -> Result<Vec<Face>, VisionError> {
        let (input, scale) = letterbox_bgr(frame);
        let t = Tensor::from_array(([1usize, 3, DET_SIZE, DET_SIZE], input)).map_err(|e| VisionError::Model(e.to_string()))?;
        let out = self.session.run(ort::inputs!["input" => t]).map_err(|e| VisionError::Model(e.to_string()))?;
        let mut heads: Vec<Vec<f32>> = Vec::with_capacity(12);
        for kind in ["cls", "obj", "bbox", "kps"] {
            for s in STRIDES {
                let (_, v) = out[format!("{kind}_{s}").as_str()].try_extract_tensor::<f32>().map_err(|e| VisionError::Model(e.to_string()))?;
                heads.push(v.to_vec());
            }
        }
        let faces = decode(&heads, self.score_threshold);
        Ok(nms(faces, self.nms_iou).into_iter().map(|f| f.scaled(1.0 / scale)).collect())
    }
}

impl Face {
    fn scaled(mut self, k: f32) -> Self {
        self.bbox.iter_mut().for_each(|v| *v *= k);
        self.landmarks.iter_mut().flatten().for_each(|v| *v *= k);
        self
    }
}

/// Fit into 640x640 at the top-left (aspect kept, zero pad, as OpenCV pads), NCHW BGR 0-255.
/// Returns the input and the scale from source to input pixels.
fn letterbox_bgr(frame: &Frame) -> (Vec<f32>, f32) {
    let scale = (DET_SIZE as f32 / frame.width as f32).min(DET_SIZE as f32 / frame.height as f32);
    let (w, h) = ((frame.width as f32 * scale).round() as usize, (frame.height as f32 * scale).round() as usize);
    let plane = DET_SIZE * DET_SIZE;
    let mut out = vec![0.0f32; 3 * plane];
    for y in 0..h.min(DET_SIZE) {
        let sy = (y as f32 + 0.5) / scale - 0.5;
        for x in 0..w.min(DET_SIZE) {
            let p = frame.sample((x as f32 + 0.5) / scale - 0.5, sy);
            let i = y * DET_SIZE + x;
            out[i] = p[2];
            out[plane + i] = p[1];
            out[2 * plane + i] = p[0];
        }
    }
    (out, scale)
}

/// YuNet head decode (`face_detect.cpp` `postProcess`). `heads`: cls x3, obj x3, bbox x3, kps x3.
fn decode(heads: &[Vec<f32>], threshold: f32) -> Vec<Face> {
    let mut faces = Vec::new();
    for (i, &stride) in STRIDES.iter().enumerate() {
        let cols = DET_SIZE / stride;
        let (cls, obj, bbox, kps) = (&heads[i], &heads[3 + i], &heads[6 + i], &heads[9 + i]);
        for idx in 0..cls.len() {
            let score = (cls[idx].clamp(0.0, 1.0) * obj[idx].clamp(0.0, 1.0)).sqrt();
            if score < threshold {
                continue;
            }
            let (r, c) = ((idx / cols) as f32, (idx % cols) as f32);
            let s = stride as f32;
            let b = &bbox[idx * 4..idx * 4 + 4];
            let (cx, cy) = ((c + b[0]) * s, (r + b[1]) * s);
            let (w, h) = (b[2].exp() * s, b[3].exp() * s);
            let k = &kps[idx * 10..idx * 10 + 10];
            let mut landmarks = [[0.0; 2]; 5];
            for (n, lm) in landmarks.iter_mut().enumerate() {
                *lm = [(k[2 * n] + c) * s, (k[2 * n + 1] + r) * s];
            }
            faces.push(Face { bbox: [cx - w / 2.0, cy - h / 2.0, w, h], landmarks, score });
        }
    }
    faces
}

fn iou(a: &[f32; 4], b: &[f32; 4]) -> f32 {
    let (x1, y1) = (a[0].max(b[0]), a[1].max(b[1]));
    let (x2, y2) = ((a[0] + a[2]).min(b[0] + b[2]), (a[1] + a[3]).min(b[1] + b[3]));
    let inter = (x2 - x1).max(0.0) * (y2 - y1).max(0.0);
    inter / (a[2] * a[3] + b[2] * b[3] - inter).max(f32::EPSILON)
}

fn nms(mut faces: Vec<Face>, thr: f32) -> Vec<Face> {
    faces.sort_by(|a, b| b.score.total_cmp(&a.score));
    let mut keep: Vec<Face> = Vec::new();
    for f in faces {
        if keep.iter().all(|k| iou(&k.bbox, &f.bbox) <= thr) {
            keep.push(f);
        }
    }
    keep
}

/// Least-squares similarity (Umeyama, no reflection) taking `src` onto `dst`:
/// `dst = [[a, -b], [b, a]] * src + t`. Returns `(a, b, tx, ty)`.
pub fn similarity(src: &[[f32; 2]; 5], dst: &[[f32; 2]; 5]) -> (f32, f32, f32, f32) {
    let n = src.len() as f32;
    let mean = |p: &[[f32; 2]; 5]| p.iter().fold([0.0f32; 2], |m, q| [m[0] + q[0] / n, m[1] + q[1] / n]);
    let (ms, md) = (mean(src), mean(dst));
    let (mut var, mut sa, mut sb) = (0.0f32, 0.0f32, 0.0f32);
    for (s, d) in src.iter().zip(dst) {
        let (sx, sy, dx, dy) = (s[0] - ms[0], s[1] - ms[1], d[0] - md[0], d[1] - md[1]);
        var += sx * sx + sy * sy;
        sa += sx * dx + sy * dy;
        sb += sx * dy - sy * dx;
    }
    let (a, b) = (sa / var, sb / var);
    (a, b, md[0] - (a * ms[0] - b * ms[1]), md[1] - (b * ms[0] + a * ms[1]))
}

/// The 112x112 aligned crop (RGB), as OpenCV `FaceRecognizerSF::alignCrop`.
pub fn align(frame: &Frame, face: &Face) -> Frame {
    let (a, b, tx, ty) = similarity(&face.landmarks, &TEMPLATE);
    let det = a * a + b * b;
    let mut rgb = Vec::with_capacity(ALIGN_SIZE * ALIGN_SIZE * 3);
    for y in 0..ALIGN_SIZE {
        for x in 0..ALIGN_SIZE {
            let (u, v) = (x as f32 - tx, y as f32 - ty);
            // Inverse of [[a,-b],[b,a]].
            let p = frame.sample((a * u + b * v) / det, (-b * u + a * v) / det);
            rgb.extend(p.iter().map(|c| c.round().clamp(0.0, 255.0) as u8));
        }
    }
    Frame { width: ALIGN_SIZE as u32, height: ALIGN_SIZE as u32, rgb }
}

pub struct Embedder {
    session: Session,
}

impl Embedder {
    pub fn load(dir: &Path) -> Result<Self, VisionError> {
        Ok(Self { session: session(&dir.join(EMBEDDER_FILE))? })
    }

    /// Unit-length embedding of an aligned 112x112 RGB crop.
    pub fn embed(&mut self, aligned: &Frame) -> Result<Vec<f32>, VisionError> {
        let plane = ALIGN_SIZE * ALIGN_SIZE;
        let mut input = vec![0.0f32; 3 * plane];
        for (i, px) in aligned.rgb.chunks_exact(3).enumerate() {
            for c in 0..3 {
                input[c * plane + i] = px[c] as f32;
            }
        }
        let t = Tensor::from_array(([1usize, 3, ALIGN_SIZE, ALIGN_SIZE], input)).map_err(|e| VisionError::Model(e.to_string()))?;
        let out = self.session.run(ort::inputs!["data" => t]).map_err(|e| VisionError::Model(e.to_string()))?;
        let (_, v) = out["fc1"].try_extract_tensor::<f32>().map_err(|e| VisionError::Model(e.to_string()))?;
        Ok(normalize(v.to_vec()))
    }
}

pub fn normalize(mut v: Vec<f32>) -> Vec<f32> {
    let n = v.iter().map(|x| x * x).sum::<f32>().sqrt().max(f32::EPSILON);
    v.iter_mut().for_each(|x| *x /= n);
    v
}

pub fn cosine(a: &[f32], b: &[f32]) -> f32 {
    a.iter().zip(b).map(|(x, y)| x * y).sum()
}

/// Detector + embedder.
pub struct FaceEngine {
    pub detector: Detector,
    pub embedder: Embedder,
}

impl FaceEngine {
    pub fn load(dir: &Path) -> Result<Self, VisionError> {
        Ok(Self { detector: Detector::load(dir)?, embedder: Embedder::load(dir)? })
    }

    /// The largest face and its embedding (enrolment and the single-person live path).
    pub fn largest(&mut self, frame: &Frame) -> Result<Option<(Face, Vec<f32>)>, VisionError> {
        let Some(face) = self.detector.detect(frame)?.into_iter().max_by(|a, b| a.area().total_cmp(&b.area())) else {
            return Ok(None);
        };
        let e = self.embedder.embed(&align(frame, &face))?;
        Ok(Some((face, e)))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn similarity_recovers_a_known_transform() {
        // src = template rotated 10 deg, scaled 3x, shifted.
        let (c, s) = (10f32.to_radians().cos() * 3.0, 10f32.to_radians().sin() * 3.0);
        let src = TEMPLATE.map(|p| [c * p[0] - s * p[1] + 400.0, s * p[0] + c * p[1] + 200.0]);
        let (a, b, tx, ty) = similarity(&src, &TEMPLATE);
        for (p, q) in src.iter().zip(TEMPLATE) {
            let (x, y) = (a * p[0] - b * p[1] + tx, b * p[0] + a * p[1] + ty);
            assert!((x - q[0]).abs() < 1e-2 && (y - q[1]).abs() < 1e-2, "{x},{y} vs {q:?}");
        }
    }

    #[test]
    fn nms_keeps_the_best_of_overlaps() {
        let f = |x: f32, score| Face { bbox: [x, 0.0, 10.0, 10.0], landmarks: [[0.0; 2]; 5], score };
        let kept = nms(vec![f(0.0, 0.7), f(1.0, 0.9), f(50.0, 0.8)], 0.3);
        assert_eq!(kept.iter().map(|k| k.score).collect::<Vec<_>>(), [0.9, 0.8]);
    }
}

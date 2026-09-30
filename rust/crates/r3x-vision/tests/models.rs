//! Detection + embeddings on the real models and Brandon's training photos. Skips (passes with
//! a note) when the models are not downloaded: `crates/r3x-vision/scripts/download_models.sh`.

use std::path::{Path, PathBuf};

use r3x_vision::face::{self, cosine, FaceEngine};
use r3x_vision::gallery::{embed_photos, images_in, Evaluation, DEFAULT_THRESHOLD};
use r3x_vision::Frame;
use serde_json::Value;

fn engine() -> Option<FaceEngine> {
    let dir = face::model_dir();
    if !face::models_available(&dir) {
        eprintln!("SKIP: vision models not in {} (run crates/r3x-vision/scripts/download_models.sh)", dir.display());
        return None;
    }
    Some(FaceEngine::load(&dir).expect("load models"))
}

fn training() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../cantina_os/vision_data/training/Brandon")
}

#[test]
fn matches_opencv_detector_and_embedder() {
    let Some(mut e) = engine() else { return };
    let refs: Vec<Value> = serde_json::from_str(include_str!("data/opencv_reference.json")).unwrap();
    for r in refs {
        let photo = r["photo"].as_str().unwrap();
        let frame = Frame::open(training().join(photo)).unwrap();
        let faces = e.detector.detect(&frame).unwrap();
        assert_eq!(faces.len(), 1, "{photo}");
        let f = &faces[0];
        let near = |a: &[f32], b: &Value| a.iter().zip(b.as_array().unwrap()).all(|(x, y)| (x - y.as_f64().unwrap() as f32).abs() < 3.0);
        assert!(near(&f.bbox, &r["bbox"]), "{photo} bbox {:?} vs {}", f.bbox, r["bbox"]);
        assert!(near(f.landmarks.as_flattened(), &r["landmarks"]), "{photo} landmarks");
        let emb = e.embedder.embed(&face::align(&frame, f)).unwrap();
        let reference: Vec<f32> = r["embedding"].as_array().unwrap().iter().map(|v| v.as_f64().unwrap() as f32).collect();
        let sim = cosine(&emb, &reference);
        assert!(sim > 0.98, "{photo}: cosine to OpenCV's embedding {sim}");
    }
}

#[test]
fn leave_one_out_on_the_training_photos() {
    let Some(mut e) = engine() else { return };
    let photos = images_in(&training()).unwrap();
    assert_eq!(photos.len(), 20);
    let embs: Vec<Vec<f32>> = embed_photos(&mut e, &photos).unwrap().into_iter().map(|x| x.1).collect();
    assert_eq!(embs.len(), 20, "a face in every training photo");
    // No other people are on disk (impostor rates come from `r3x-vision eval --negatives`);
    // a blank frame must at least yield no face.
    assert!(e.largest(&Frame::new(320, 240, vec![128; 320 * 240 * 3]).unwrap()).unwrap().is_none());
    let people = [("Brandon".to_string(), embs)].into();
    let ev = Evaluation::leave_one_out(&people, &[]);
    let (accept, _) = ev.rates(DEFAULT_THRESHOLD);
    let worst = ev.genuine.iter().copied().fold(f32::MAX, f32::min);
    eprintln!("leave-one-out: accept {:.0}% at {DEFAULT_THRESHOLD}, worst genuine {worst:.3}", accept * 100.0);
    assert_eq!(accept, 1.0);
    assert!(worst > DEFAULT_THRESHOLD + 0.2, "margin: {worst}");
}

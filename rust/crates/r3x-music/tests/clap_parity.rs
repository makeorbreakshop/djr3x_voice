//! CLAP through `ort` against the Python/torch reference written by
//! `scripts/export_clap_onnx.py` (plan §9 Phase 4: ranking checked on a fixed query set).
//! Local and free, but needs the ~600 MB export in `~/.cache/dj-r3x/clap`, so it is ignored
//! by default:  cargo test -p r3x-music --release --test clap_parity -- --ignored --nocapture
#![cfg(feature = "clap")]

use r3x_music::library::Library;
use r3x_music::semantic::{default_model_dir, rank, Clap, Index, NEGATIVE_WEIGHT, SAMPLE_RATE, SEGMENT_SAMPLES};
use serde_json::Value;

fn cos(a: &[f32], b: &[f32]) -> f32 {
    let d: f32 = a.iter().zip(b).map(|(x, y)| x * y).sum();
    d / (a.iter().map(|x| x * x).sum::<f32>().sqrt() * b.iter().map(|x| x * x).sum::<f32>().sqrt())
}

fn vecf(v: &Value) -> Vec<f32> {
    v.as_array().unwrap().iter().map(|x| x.as_f64().unwrap() as f32).collect()
}

fn probe() -> Vec<f32> {
    (0..SEGMENT_SAMPLES)
        .map(|i| {
            let t = i as f64 / SAMPLE_RATE as f64;
            let mut x = 0.3 * (std::f64::consts::TAU * 220.0 * t).sin() + 0.2 * (std::f64::consts::TAU * 1330.0 * t).sin();
            if i % 24_000 < 480 {
                x += 0.4;
            }
            x as f32
        })
        .collect()
}

#[test]
#[ignore = "needs the local CLAP export (scripts/export_clap_onnx.py)"]
fn clap_matches_python() {
    let dir = default_model_dir();
    let reference: Value = serde_json::from_slice(&std::fs::read(dir.join("reference.json")).unwrap()).unwrap();
    let clap = Clap::load(&dir).unwrap();

    // Audio front end + encoder on a synthetic probe.
    let ours = &clap.embed_audio(&[probe()]).unwrap()[0];
    let c = cos(ours, &vecf(&reference["probe_audio_embed"]));
    println!("probe audio embedding cosine vs torch: {c:.6}");
    assert!(c > 0.999, "{c}");

    // Text: token ids, vectors, and the ranking over the Python index.
    let py = Index {
        names: reference["track_names"].as_array().unwrap().iter().map(|v| v.as_str().unwrap().to_owned()).collect(),
        vectors: reference["track_embeddings"].as_array().unwrap().iter().map(vecf).collect(),
    };
    // Rust-built index over the same library (decode + resample differ from librosa slightly).
    let lib = Library::scan(&r3x_music::library::default_music_dir(), false);
    let keyed: Vec<_> = lib.tracks.iter().map(|t| (t.key.clone(), t.path.clone())).collect();
    let tmp = std::env::temp_dir().join("r3x-clap-parity-index.json");
    let ours_idx = clap.build_index(&keyed, &tmp).unwrap();
    let mut track_cos = Vec::new();
    for (n, v) in ours_idx.names.iter().zip(&ours_idx.vectors) {
        if let Some(i) = py.names.iter().position(|p| p == n) {
            track_cos.push(cos(v, &py.vectors[i]));
        }
    }
    let min_track = track_cos.iter().cloned().fold(1.0f32, f32::min);
    println!("track vectors vs Python index: {} matched, min cosine {min_track:.4}", track_cos.len());
    assert_eq!(track_cos.len(), py.names.len(), "same library keys as CantinaOS");

    let (mut top1_py, mut top1_ours, mut top3_ours) = (0, 0, 0);
    let queries = reference["queries"].as_array().unwrap();
    for q in queries {
        let text = q["query"].as_str().unwrap();
        let neg = q["negative"].as_str();
        let want_ids: Vec<i64> = q["input_ids"].as_array().unwrap().iter().map(|v| v.as_i64().unwrap()).collect();
        assert_eq!(clap.token_ids(text).unwrap(), want_ids, "{text}");
        let pos = clap.embed_text(text).unwrap();
        let tc = cos(&pos, &vecf(&q["text_embed"]));
        assert!(tc > 0.9999, "{text}: {tc}");
        let negv = neg.map(|n| clap.embed_text(n).unwrap());
        let want: Vec<&str> = q["ranking"].as_array().unwrap().iter().map(|v| v.as_str().unwrap()).collect();
        let got_py: Vec<String> = rank(&py, &pos, negv.as_deref(), NEGATIVE_WEIGHT, 22).into_iter().map(|m| m.key).collect();
        let got: Vec<String> = rank(&ours_idx, &pos, negv.as_deref(), NEGATIVE_WEIGHT, 22).into_iter().map(|m| m.key).collect();
        top1_py += (got_py[0] == want[0]) as usize;
        top1_ours += (got[0] == want[0]) as usize;
        top3_ours += want[..3].iter().filter(|w| got[..3].iter().any(|g| g == *w)).count();
        println!("{text:32} py-index top3 {:?} | r3x-index top3 {:?} | python {:?}", &got_py[..3], &got[..3], &want[..3]);
        assert_eq!(&got_py[..5], &want[..5], "{text}: same text vectors over the Python index rank identically");
    }
    println!("top-1 agreement: python index {top1_py}/{n}, r3x index {top1_ours}/{n}; top-3 overlap (r3x index) {top3_ours}/{}", 3 * queries.len(), n = queries.len());
    assert!(top1_ours * 4 >= queries.len() * 3, "r3x-built index should agree on top-1 for >= 75% of queries");
}

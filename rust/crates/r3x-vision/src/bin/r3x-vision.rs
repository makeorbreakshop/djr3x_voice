//! `r3x-vision` tool. No camera is opened by any command except `cameras` (which only lists).
//!
//! ```text
//! r3x-vision enroll <root> [--name NAME] [--gallery PATH] [--threshold T]
//!     <root>/<Name>/*.jpg for everyone, or --name for one folder of photos.
//! r3x-vision eval <root> [--negatives DIR] [--max-negatives N]
//!     leave-one-out genuine scores vs impostor scores; suggests a threshold.
//! r3x-vision detect <image>...          faces, landmarks, best gallery match
//! r3x-vision cameras                    AVFoundation video devices (Continuity marked)
//! ```

use std::path::{Path, PathBuf};

use anyhow::{bail, Context};
use r3x_vision::camera::Camera;
use r3x_vision::gallery::{self, embed_photos, images_in, Evaluation, Gallery};
use r3x_vision::{face, FaceEngine, FfmpegCamera, Frame};

fn main() -> anyhow::Result<()> {
    tracing_subscriber_init();
    let args: Vec<String> = std::env::args().skip(1).collect();
    let flag = |f: &str| args.iter().position(|a| a == f).and_then(|i| args.get(i + 1)).cloned();
    let positional: Vec<&String> = {
        let mut v = Vec::new();
        let mut skip = false;
        for a in &args[1.min(args.len())..] {
            if skip {
                skip = false;
            } else if a.starts_with("--") {
                skip = true;
            } else {
                v.push(a);
            }
        }
        v
    };
    let engine = || FaceEngine::load(&face::model_dir()).context("load models (crates/r3x-vision/scripts/download_models.sh)");
    let gallery_path = flag("--gallery").map_or_else(gallery::default_path, PathBuf::from);
    match args.first().map(String::as_str) {
        Some("enroll") => {
            let root = positional.first().context("enroll <root>")?;
            let mut g = Gallery::load(&gallery_path).unwrap_or_default();
            if let Some(t) = flag("--threshold") {
                g.threshold = t.parse()?;
            }
            let report = gallery::enroll(&mut engine()?, &mut g, Path::new(root), flag("--name").as_deref())?;
            for (n, used, skipped) in report {
                println!("{n}: {used} photos enrolled, {skipped} without a face");
            }
            g.save(&gallery_path)?;
            println!("gallery: {} (threshold {:.3})", gallery_path.display(), g.threshold);
        }
        Some("eval") => {
            let root = PathBuf::from(positional.first().context("eval <root>")?);
            let mut e = engine()?;
            let mut people = std::collections::BTreeMap::new();
            for d in std::fs::read_dir(&root)?.filter_map(|d| d.ok().map(|d| d.path())).filter(|p| p.is_dir()) {
                let embs = embed_photos(&mut e, &images_in(&d)?)?;
                people.insert(d.file_name().unwrap_or_default().to_string_lossy().to_string(), embs.into_iter().map(|x| x.1).collect::<Vec<_>>());
            }
            let mut negs = Vec::new();
            if let Some(n) = flag("--negatives") {
                let max: usize = flag("--max-negatives").map_or(Ok(500), |m| m.parse())?;
                negs = walk_images(Path::new(&n), max)?;
            }
            let neg_embs: Vec<Vec<f32>> = embed_photos(&mut e, &negs)?.into_iter().map(|x| x.1).collect();
            let ev = Evaluation::leave_one_out(&people, &neg_embs);
            let stat = |v: &[f32]| (v.iter().copied().fold(f32::MAX, f32::min), v.iter().sum::<f32>() / v.len().max(1) as f32, v.iter().copied().fold(f32::MIN, f32::max));
            println!("genuine (leave-one-out, n={}): min/mean/max {:.3?}", ev.genuine.len(), stat(&ev.genuine));
            println!("impostor (n={}): min/mean/max {:.3?}", ev.impostor.len(), stat(&ev.impostor));
            for t in [0.30, 0.363, 0.40, 0.45, 0.50] {
                let (tar, far) = ev.rates(t);
                println!("  t={t:.3}: accept {:.1}%  false-accept {:.2}%", tar * 100.0, far * 100.0);
            }
            println!("suggested threshold: {:.3}", ev.suggest());
        }
        Some("detect") => {
            let g = Gallery::load(&gallery_path).ok();
            let mut e = engine()?;
            for p in &positional {
                let frame = Frame::open(p)?;
                let t0 = std::time::Instant::now();
                let faces = e.detector.detect(&frame)?;
                let det_ms = t0.elapsed().as_secs_f64() * 1e3;
                println!("{p}: {} face(s), detect {det_ms:.1} ms", faces.len());
                for f in faces {
                    let emb = e.embedder.embed(&face::align(&frame, &f))?;
                    let m = g.as_ref().and_then(|g| g.nearest(&emb));
                    println!("  score {:.3} bbox {:.0?} match {:?}", f.score, f.bbox, m.map(|m| (m.name, m.similarity)));
                }
            }
        }
        Some("cameras") => {
            for c in FfmpegCamera::default().list() {
                println!("[{}] {}{}", c.index, c.name, if c.is_continuity() { " (Continuity, skipped)" } else { "" });
            }
        }
        _ => bail!("usage: r3x-vision enroll <root> [--name N] | eval <root> [--negatives DIR] | detect <img>... | cameras"),
    }
    Ok(())
}

/// Up to `max` images under `dir`, recursively, one per sub-folder first (distinct people).
fn walk_images(dir: &Path, max: usize) -> anyhow::Result<Vec<PathBuf>> {
    let mut dirs: Vec<PathBuf> = std::fs::read_dir(dir)?.filter_map(|d| d.ok().map(|d| d.path())).filter(|p| p.is_dir()).collect();
    dirs.sort();
    let mut out = images_in(dir)?;
    for d in dirs {
        if out.len() >= max {
            break;
        }
        if let Some(first) = images_in(&d)?.into_iter().next() {
            out.push(first);
        }
    }
    out.truncate(max);
    Ok(out)
}

fn tracing_subscriber_init() {
    let filter = tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "warn".into());
    tracing_subscriber::fmt().with_env_filter(filter).with_writer(std::io::stderr).init();
}

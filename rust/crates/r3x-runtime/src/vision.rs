//! `--vision` (`R3X_VISION=1`): camera + face recognition + scene descriptions (Phase 6).
//! Fail-open: missing models, no gallery or no physical camera log a warning, report
//! `vision` degraded, and the runtime carries on without it.

use std::sync::Arc;

use r3x_bus::Bus;
use r3x_contracts::ServiceStatus;
use r3x_vision::{face, gallery, FaceEngine, FfmpegCamera, Gallery, GalleryRecognizer, Vision, VisionConfig};

pub fn enabled_from_env() -> bool {
    std::env::var("R3X_VISION").is_ok_and(|v| !matches!(v.as_str(), "" | "0" | "false" | "no" | "off"))
}

/// Start vision. `memory`: the brain's `Memory`, so presence lands in the same instance the
/// turn context reads (person detected/exited -> `Memory::person_detected|exited`).
pub fn start(bus: &Bus, memory: Option<Arc<r3x_memory::Memory>>) -> Option<Vision> {
    let degraded = |why: String| {
        tracing::warn!("vision off: {why}");
        r3x_ops::report(bus, "vision", ServiceStatus::Degraded, Some(why));
        None
    };
    let engine = match FaceEngine::load(&face::model_dir()) {
        Ok(e) => e,
        Err(e) => return degraded(e.to_string()),
    };
    let gallery = match Gallery::load(&gallery::default_path()) {
        Ok(g) => g,
        Err(e) => return degraded(format!("{e} (enrol: cargo run -p r3x-vision --release -- enroll <dir>)")),
    };
    let cfg = VisionConfig::from_env();
    // Scene descriptions are paid calls: only with a key, and never when R3X_VISION_SCENES=0
    // (on-demand analyze_scene still needs the client).
    let llm = r3x_llm::LlmClient::from_env().ok().flatten();
    let (vision, _task) = Vision::spawn(bus, cfg, Box::new(FfmpegCamera::default()), Box::new(GalleryRecognizer::new(engine, gallery)), llm);
    if let Some(m) = memory {
        r3x_vision::link_memory(bus, m);
    }
    Some(vision)
}

/// Vision as the brain's [`r3x_brain::SceneSource`]: scene context for turns, `analyze_scene`.
pub struct BrainEyes(pub Vision);

impl r3x_brain::SceneSource for BrainEyes {
    fn scene(&self) -> Option<(String, f64)> {
        self.0.scene()
    }

    fn analyze<'a>(
        &'a self,
        question: &'a str,
        turn: Option<String>,
    ) -> std::pin::Pin<Box<dyn std::future::Future<Output = Result<String, String>> + Send + 'a>> {
        Box::pin(async move { self.0.analyze_scene(question, turn).await.map_err(|e| e.to_string()) })
    }

    fn console(&self, line: &str) -> Option<String> {
        self.0.console(line)
    }
}

//! `--vision` (`R3X_VISION=1`): camera + face recognition + scene descriptions (Phase 6).
//! Fail-open: missing models, no gallery or no physical camera log a warning, report
//! `vision` degraded, and the runtime carries on without it.

use std::sync::{Arc, OnceLock};

use r3x_bus::Bus;
use r3x_contracts::ServiceStatus;
use r3x_vision::{face, gallery, Camera, FaceEngine, FfmpegCamera, Gallery, GalleryRecognizer, Recognizer, Vision, VisionConfig};

pub fn enabled_from_env() -> bool {
    std::env::var("R3X_VISION").is_ok_and(|v| !matches!(v.as_str(), "" | "0" | "false" | "no" | "off"))
}

/// Vision once it is up. The face models take ~0.9 s to load, so they load in the
/// background and the gateway serves without waiting; until then the slot is empty and the
/// brain simply has no eyes (exactly as with vision off).
pub type VisionSlot = Arc<OnceLock<Vision>>;

type Loaded = (Box<dyn Camera>, Box<dyn Recognizer>);

/// Start vision. `memory`: the brain's `Memory`, so presence lands in the same instance the
/// turn context reads (person detected/exited -> `Memory::person_detected|exited`). `brain`
/// gets the scene source once vision is up.
pub fn start(bus: &Bus, memory: Option<Arc<r3x_memory::Memory>>, brain: Option<r3x_brain::Brain>) -> VisionSlot {
    start_with(bus, memory, brain, || {
        let engine = FaceEngine::load(&face::model_dir()).map_err(|e| e.to_string())?;
        let gallery = Gallery::load(&gallery::default_path())
            .map_err(|e| format!("{e} (enrol: cargo run -p r3x-vision --release -- enroll <dir>)"))?;
        Ok((Box::new(FfmpegCamera::default()) as Box<dyn Camera>, Box::new(GalleryRecognizer::new(engine, gallery)) as Box<dyn Recognizer>))
    })
}

/// [`start`] with the blocking load (models, gallery) supplied: it runs on the blocking pool,
/// this returns at once.
pub(crate) fn start_with<L>(bus: &Bus, memory: Option<Arc<r3x_memory::Memory>>, brain: Option<r3x_brain::Brain>, load: L) -> VisionSlot
where
    L: FnOnce() -> Result<Loaded, String> + Send + 'static,
{
    let slot = VisionSlot::default();
    let (bus, filled) = (bus.clone(), slot.clone());
    tokio::spawn(async move {
        let loaded = tokio::task::spawn_blocking(load).await.unwrap_or_else(|e| Err(format!("model load panicked: {e}")));
        let (camera, recognizer) = match loaded {
            Ok(l) => l,
            Err(why) => {
                tracing::warn!("vision off: {why}");
                r3x_ops::report(&bus, "vision", ServiceStatus::Degraded, Some(why));
                return;
            }
        };
        let cfg = VisionConfig::from_env();
        // Scene descriptions are paid calls: only with a key, and never when R3X_VISION_SCENES=0
        // (on-demand analyze_scene still needs the client).
        let llm = r3x_llm::LlmClient::from_env().ok().flatten();
        let (vision, _task) = Vision::spawn(&bus, cfg, camera, recognizer, llm);
        if let Some(m) = memory {
            r3x_vision::link_memory(&bus, m);
        }
        if let Some(b) = brain {
            b.attach_vision(Arc::new(BrainEyes(vision.clone())));
        }
        let _ = filled.set(vision);
    });
    slot
}

/// Vision as the brain's [`r3x_brain::SceneSource`]: scene context for turns, and the camera
/// frame for looks (Jev-flagged turns and `analyze_scene`).
pub struct BrainEyes(pub Vision);

impl r3x_brain::SceneSource for BrainEyes {
    fn scene(&self) -> Option<(String, f64)> {
        self.0.scene()
    }

    fn active(&self) -> bool {
        self.0.enabled()
    }

    fn snapshot(&self) -> std::pin::Pin<Box<dyn std::future::Future<Output = Result<String, String>> + Send + '_>> {
        Box::pin(async move { self.0.snapshot().await.map_err(|e| e.to_string()) })
    }

    fn console(&self, line: &str) -> Option<String> {
        self.0.console(line)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{Duration, Instant};

    use r3x_vision::{CameraInfo, Frame, FrameSource, VisionError};

    struct NoCamera;
    impl r3x_vision::Camera for NoCamera {
        fn list(&self) -> Vec<CameraInfo> {
            Vec::new()
        }
        fn open(&self, _: u32, _: f64) -> Result<Box<dyn FrameSource>, VisionError> {
            Err(VisionError::Camera("none in tests".into()))
        }
    }
    struct Nobody;
    impl r3x_vision::Recognizer for Nobody {
        fn recognize(&mut self, _: &Frame) -> Option<(String, f32)> {
            None
        }
    }

    /// The face models take ~0.9 s to load; the gateway must not wait for them. `start_with`
    /// returns at once and vision lands in the slot when the load finishes.
    #[tokio::test(flavor = "multi_thread")]
    async fn model_load_is_off_the_ready_path() {
        let bus = Bus::default();
        let t0 = Instant::now();
        let slot = start_with(&bus, None, None, || {
            std::thread::sleep(Duration::from_millis(400));
            Ok((Box::new(NoCamera) as Box<dyn r3x_vision::Camera>, Box::new(Nobody) as Box<dyn r3x_vision::Recognizer>))
        });
        assert!(t0.elapsed() < Duration::from_millis(100), "start blocked for {:?}", t0.elapsed());
        assert!(slot.get().is_none());
        let deadline = Instant::now() + Duration::from_secs(5);
        while slot.get().is_none() && Instant::now() < deadline {
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
        assert!(slot.get().is_some(), "vision never came up");
    }

    /// Fail-open: a load error leaves vision off and the runtime running.
    #[tokio::test(flavor = "multi_thread")]
    async fn failed_load_leaves_vision_off() {
        let bus = Bus::default();
        let slot = start_with(&bus, None, None, || Err("no models".to_string()));
        tokio::time::sleep(Duration::from_millis(200)).await;
        assert!(slot.get().is_none());
    }
}

//! `r3x-vision` (plan Phase 6): camera, face recognition on `ort`, presence, scene description.
//!
//! ```ignore
//! let engine = FaceEngine::load(&face::model_dir())?;
//! let gallery = Gallery::load(&gallery::default_path())?;
//! let (vision, _task) = Vision::spawn(&bus, VisionConfig::from_env(), Box::new(FfmpegCamera::default()),
//!                                     Box::new(GalleryRecognizer::new(engine, gallery)), llm);
//! link_memory(&bus, memory.clone());                    // person detected/exited -> r3x-memory
//! let ctx = memory.turn_context(vision.scene().as_ref().map(|(s, t)| (s.as_str(), *t)), 5)?;
//! let answer = vision.analyze_scene("what am I holding?", Some(turn)).await?;   // the tool
//! ```
//!
//! Models: `crates/r3x-vision/scripts/download_models.sh` (YuNet + SFace into `~/.cache/dj-r3x/vision`).
//! Enrolment: `cargo run -p r3x-vision --release -- enroll ../cantina_os/vision_data/training` (from `rust/`).

pub mod cadence;
pub mod camera;
pub mod face;
pub mod frame;
pub mod gallery;
pub mod presence;
pub mod service;

pub use camera::{kill_captures, Camera, CameraInfo, FfmpegCamera, FrameSource};
pub use face::{FaceEngine, Face};
pub use frame::Frame;
pub use gallery::{Gallery, Match};
pub use presence::{Presence, PresenceConfig};
pub use service::{face_to_gaze, link_memory, CameraStatus, GalleryRecognizer, Recognizer, Vision, VisionConfig};

#[derive(Debug, thiserror::Error)]
pub enum VisionError {
    #[error("model: {0}")]
    Model(String),
    #[error("image: {0}")]
    Image(String),
    #[error("camera: {0}")]
    Camera(String),
    #[error("gallery: {0}")]
    Gallery(String),
    #[error("io: {0}")]
    Io(#[from] std::io::Error),
    #[error("llm: {0}")]
    Llm(#[from] r3x_llm::LlmError),
}

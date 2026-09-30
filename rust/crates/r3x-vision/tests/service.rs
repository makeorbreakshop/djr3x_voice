//! The service loop on a fake camera + recognizer, a replayed Claude, and real memory.
//! No camera, no network, no paid call.

use std::sync::Arc;
use std::time::Duration;

use r3x_bus::{Bus, BusConfig, Received};
use r3x_contracts::{Body, Domain, Event, VisionEvent};
use r3x_llm::{ClaudeFixtures, LlmClient, Message, MessagesRequest};
use r3x_vision::camera::{Camera, CameraInfo, FrameSource};
use r3x_vision::{link_memory, Frame, Recognizer, Vision, VisionConfig, VisionError};

/// Frames whose first byte says who is in them: 1 = Brandon, 0 = nobody.
struct Script(Vec<u8>);
impl FrameSource for Script {
    fn next_frame(&mut self) -> Result<Option<Frame>, VisionError> {
        if self.0.is_empty() {
            return Ok(None);
        }
        let who = self.0.remove(0);
        let mut rgb = vec![0u8; 4 * 4 * 3];
        rgb[0] = who;
        Frame::new(4, 4, rgb).map(Some)
    }
}

struct FakeCamera(Vec<u8>);
impl Camera for FakeCamera {
    fn list(&self) -> Vec<CameraInfo> {
        vec![CameraInfo { index: 0, name: "Brandon's iPhone Camera".into() }, CameraInfo { index: 1, name: "FaceTime HD Camera".into() }]
    }
    fn open(&self, index: u32, _fps: f64) -> Result<Box<dyn FrameSource>, VisionError> {
        assert_eq!(index, 1, "Continuity camera must be skipped");
        Ok(Box::new(Script(self.0.clone())))
    }
}

struct ByteRecognizer;
impl Recognizer for ByteRecognizer {
    fn recognize(&mut self, f: &Frame) -> Option<(String, f32)> {
        (f.rgb[0] == 1).then(|| ("Brandon".to_string(), 0.8))
    }
}

fn fixture_line(prompt: &str, reply: &str) -> String {
    let req = MessagesRequest::new(200).messages(vec![Message::user_image("image/jpeg", "x", prompt)]);
    serde_json::json!({"key": ClaudeFixtures::key_for("create", &req), "method": "create",
                       "final": {"content": [{"type": "text", "text": reply}]}})
    .to_string()
}

#[tokio::test]
async fn presence_scene_memory_and_on_demand_analysis() {
    let dir = std::env::temp_dir().join(format!("r3x-vision-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let startup = "Describe the scene you see. Face recognition detected Brandon (confidence: 0.80). Focus on the environment, objects, and activities. Keep it concise (under 100 words).";
    let jsonl = [fixture_line(startup, "A workshop with a droid."), fixture_line("What am I holding?", "A soldering iron.")].join("\n");
    std::fs::write(dir.join("claude.jsonl"), jsonl).unwrap();
    let llm = LlmClient::replay(Arc::new(ClaudeFixtures::load(&dir, 0.0).unwrap()), "claude-haiku-4-5-20251001");

    let bus = Bus::new(BusConfig::default());
    let memory = Arc::new(r3x_memory::Memory::open_in_memory().unwrap());
    link_memory(&bus, memory.clone());
    let mut rx = bus.subscribe(Domain::Vision);
    let mut frames = vec![1u8; 3];
    frames.extend([0u8; 10]);
    let cfg = VisionConfig { fps: 0.0, frame_max_age: Duration::from_secs(30), ..Default::default() };
    let (vision, _task) = Vision::spawn(&bus, cfg, Box::new(FakeCamera(frames)), Box::new(ByteRecognizer), Some(llm));

    let mut got = Vec::new();
    while got.len() < 3 {
        let Received::Message(env) = tokio::time::timeout(Duration::from_secs(5), rx.recv()).await.expect("vision events").unwrap() else { continue };
        if let Body::Event(Event::Vision(e)) = &env.body {
            got.push(e.clone());
        }
    }
    let detected: Vec<_> = got.iter().filter(|e| matches!(e, VisionEvent::PersonDetected { name, .. } if name == "Brandon")).collect();
    let exited: Vec<_> = got.iter().filter(|e| matches!(e, VisionEvent::PersonExited { name, .. } if name == "Brandon")).collect();
    assert_eq!((detected.len(), exited.len()), (1, 1), "{got:?}");
    assert!(got.iter().any(|e| matches!(e, VisionEvent::SceneCaptured { reason, person: Some(p), description }
        if reason == "person_changed_from_none_to_Brandon" && p == "Brandon" && description == "A workshop with a droid.")));
    assert_eq!(vision.scene().unwrap().0, "A workshop with a droid.");

    // Memory saw the visit (the link task runs concurrently: give it a moment).
    tokio::time::sleep(Duration::from_millis(50)).await;
    let p = memory.person_profile("Brandon").unwrap().expect("profile");
    assert_eq!(p.visit_count, 1);

    // The camera list and the Continuity skip.
    let st = vision.status();
    assert_eq!((st.selected, st.frames), (Some(1), 13));
    assert!(vision.console("camera list").unwrap().contains("[0] Brandon's iPhone Camera (skipped: Continuity)"));
    assert!(vision.console("camera select 7").unwrap().contains("no camera 7"));
    assert!(vision.console("play music").is_none());

    // The analyze_scene tool: last frame, the user's question, tagged with the turn.
    let answer = vision.analyze_scene("What am I holding?", Some("turn-1".into())).await.unwrap();
    assert_eq!(answer, "A soldering iron.");
    loop {
        let Some(Received::Message(env)) = tokio::time::timeout(Duration::from_secs(2), rx.recv()).await.unwrap() else { continue };
        if let Body::Event(Event::Vision(VisionEvent::SceneCaptured { reason, .. })) = &env.body {
            assert_eq!((reason.as_str(), env.conversation_id.as_deref()), ("on_demand", Some("turn-1")));
            break;
        }
    }
    vision.shutdown();
    let _ = std::fs::remove_dir_all(&dir);
}

async fn next_vision(rx: &mut r3x_bus::EventReceiver) -> VisionEvent {
    loop {
        let Received::Message(env) = tokio::time::timeout(Duration::from_secs(5), rx.recv()).await.expect("vision event").unwrap() else { continue };
        if let Body::Event(Event::Vision(e)) = &env.body {
            return e.clone();
        }
    }
}

/// The panel's Vision switch (stage `set_vision`): off closes the camera, ends the current
/// visit and refuses looks (no paid call); on reopens it and recognition resumes.
#[tokio::test]
async fn switched_off_the_camera_closes_and_looks_are_refused() {
    let bus = Bus::new(BusConfig::default());
    let mut rx = bus.subscribe(Domain::Vision);
    let cfg = VisionConfig { fps: 50.0, frame_max_age: Duration::from_secs(30), ..Default::default() };
    let (vision, _task) = Vision::spawn(&bus, cfg, Box::new(FakeCamera(vec![1u8; 20_000])), Box::new(ByteRecognizer), None);
    assert!(matches!(next_vision(&mut rx).await, VisionEvent::PersonDetected { .. }));
    assert!(vision.enabled());

    vision.set_enabled(false);
    assert!(matches!(next_vision(&mut rx).await, VisionEvent::PersonExited { .. }), "switching off ends the visit");
    tokio::time::sleep(Duration::from_millis(200)).await;
    assert!(!vision.status().streaming, "camera closed");
    assert!(!vision.enabled());
    let e = vision.analyze_scene("What is this?", None).await.unwrap_err().to_string();
    assert!(e.contains("switched off"), "{e}");

    vision.set_enabled(true);
    assert!(matches!(next_vision(&mut rx).await, VisionEvent::PersonDetected { .. }), "back on: recognition resumes");
    assert!(vision.status().streaming);

    // The same switch as console lines (the panel's System tab sends these).
    assert_eq!(vision.console("vision off").as_deref(), Some("Vision off: camera closed, no scene photos"));
    assert!(!vision.enabled());
    assert!(vision.console("vision status").unwrap().starts_with("vision: off"));
    assert_eq!(vision.console("vision on").as_deref(), Some("Vision on"));
    assert!(vision.enabled());
    assert!(vision.console("vision sideways").unwrap().starts_with("usage: vision on"));
    vision.shutdown();
}

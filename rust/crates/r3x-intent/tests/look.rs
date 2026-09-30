//! The "should R3X look?" question: its own small Jev request, sent in parallel with the main
//! one, so the main request (and every recorded CantinaOS fixture keyed on it) is unchanged.

use std::sync::Arc;

use r3x_intent::catalogue::{build_look_questions, build_questions, build_state};
use r3x_intent::{IntentRouter, JevClient, JevFixtures, RouterConfig, JEV_MODEL};
use serde_json::json;

fn router_with(lines: &[(String, f64)]) -> Arc<IntentRouter> {
    let dir = std::env::temp_dir().join(format!("r3x-intent-look-{}-{}", std::process::id(), lines.len()));
    std::fs::create_dir_all(&dir).unwrap();
    let body: String = lines
        .iter()
        .map(|(t, p)| {
            json!({"key": JevFixtures::key_for(JEV_MODEL, &build_state(t), &build_look_questions()), "status": 200,
                   "body": {"answers": {"look": {"type": "noul", "noul": p}}}, "latency_ms": 0.0})
            .to_string()
                + "\n"
        })
        .collect();
    std::fs::write(dir.join("jev.jsonl"), body).unwrap();
    let jev = Arc::new(JevFixtures::load(&dir, 0.0).unwrap());
    IntentRouter::new(JevClient::replay(jev), RouterConfig { api_key: "replay".into(), ..Default::default() })
}

#[test]
fn the_look_question_is_separate_from_the_main_request() {
    let main = build_questions();
    assert!(main.get("look").is_none(), "the main request stays byte-identical to CantinaOS's");
    let look = build_look_questions();
    assert_eq!(look.as_object().unwrap().keys().collect::<Vec<_>>(), ["look"]);
    assert_eq!(look["look"]["type"], "noul");
}

#[tokio::test]
async fn look_is_decided_by_threshold_and_fails_closed() {
    let r = router_with(&[("what am I holding".into(), 0.93), ("look, I'm so tired".into(), 0.08)]);
    assert!(r.should_look("what am I holding").await);
    assert!(!r.should_look("look, I'm so tired").await);
    assert!(!r.should_look("an utterance with no answer").await, "no Jev answer = no look");
    assert!((RouterConfig::default().look_threshold - 0.6).abs() < 1e-9);
}

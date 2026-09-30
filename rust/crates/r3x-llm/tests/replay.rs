//! Replays a real recorded Claude stream (Phase 0 corpus) with its chunk boundaries, plus one
//! opt-in live call (`cargo test -p r3x-llm -- --ignored`).

use r3x_llm::*;
use std::path::PathBuf;
use std::sync::Arc;

fn repo() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..")
}

#[tokio::test]
async fn replays_recorded_stream_chunk_for_chunk() {
    let fx = Arc::new(ClaudeFixtures::load(repo().join("fixtures/smoke-voice"), 0.0).unwrap());
    let before = fx.remaining();
    let llm = LlmClient::replay(fx.clone(), "claude-sonnet-5-5");
    // The chat turn: no router action, so the user message is the bare transcript.
    let req = prompt::turn_request("persona", vec![Message::user("what is your favourite cantina band")], request::default_tools(), false);
    let mut s = llm.stream(&req).await.unwrap();
    let (mut deltas, mut done) = (Vec::new(), None);
    while let Some(ev) = s.next().await {
        match ev.unwrap() {
            StreamEvent::TextDelta(t) => deltas.push(t),
            StreamEvent::ToolUse(_) => panic!("chat turn has no tools"),
            StreamEvent::Done(m) => done = Some(m),
        }
    }
    let m = done.expect("Done is last");
    assert!(deltas.len() > 5, "chunk boundaries preserved: {deltas:?}");
    assert_eq!(deltas.concat(), m.text());
    assert!(m.text().contains("Cantina Band"));
    assert_eq!(fx.remaining(), before - 1);

    // same request again: last record repeats; an unknown one is a clean error
    assert!(llm.stream(&req).await.is_ok());
    let miss = prompt::turn_request("p", vec![Message::user("never recorded")], vec![], false);
    assert!(matches!(llm.stream(&miss).await, Err(LlmError::Fixture(_))));
}

fn dotenv() -> impl Fn(&str) -> Option<String> {
    let text = std::fs::read_to_string(repo().join(".env")).unwrap_or_default();
    let map: std::collections::HashMap<String, String> = text
        .lines()
        .filter_map(|l| l.split_once('='))
        .map(|(k, v)| (k.trim().to_string(), v.trim().trim_matches('"').to_string()))
        .collect();
    move |k| map.get(k).cloned()
}

/// One real request: streaming, tools present, `tool_choice: none`, cache_control, the model's
/// sampling rule. Reads `.env` only (a Claude Code shell exports ANTHROPIC_BASE_URL).
#[tokio::test]
#[ignore = "spends API credit"]
async fn live_stream_with_tool_choice_none() {
    let Some(cfg) = LlmConfig::from_lookup(dotenv()) else {
        eprintln!("no key in .env; skipped");
        return;
    };
    let llm = LlmClient::new(cfg).unwrap();
    let req = MessagesRequest::new(40)
        .system("You are DJ R3X. Reply in five words or fewer.", true)
        .user("Say hello.")
        .tools(request::default_tools())
        .tool_choice(ToolChoice::None)
        .temperature(0.4);
    let t0 = std::time::Instant::now();
    let m = llm.stream(&req).await.unwrap().finish().await.unwrap();
    eprintln!("{} via {:?} in {:?}: {:?}", llm.model(), llm.provider(), t0.elapsed(), m.text());
    assert!(!m.text().is_empty());
    assert!(m.tool_uses().is_empty());
}

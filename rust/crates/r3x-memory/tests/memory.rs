use r3x_llm::{ClaudeFixtures, LlmClient, MessagesRequest};
use r3x_memory::*;
use serde_json::json;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

fn tmp(name: &str) -> PathBuf {
    let d = std::env::temp_dir().join(format!("r3x-memory-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    d
}

/// A clock the test moves by hand (seconds).
fn clock() -> (Arc<AtomicU64>, impl Fn() -> f64 + Send + Sync + 'static) {
    let t = Arc::new(AtomicU64::new(1_790_000_000));
    let c = t.clone();
    (t, move || c.load(Ordering::SeqCst) as f64)
}

/// Claude fixture dir answering `create` calls whose prompt is known.
fn llm_with(dir: &PathBuf, answers: &[(String, &str)]) -> LlmClient {
    let mut lines = String::new();
    for (prompt, text) in answers {
        let key = ClaudeFixtures::key_for("create", &MessagesRequest::new(1).user(prompt.clone()));
        lines += &json!({"key": key, "method": "create", "wait": 0.0, "final": {"content": [{"type": "text", "text": text}]}}).to_string();
        lines.push('\n');
    }
    std::fs::write(dir.join("claude.jsonl"), lines).unwrap();
    LlmClient::replay(Arc::new(ClaudeFixtures::load(dir, 0.0).unwrap()), "claude-sonnet-5")
}

#[tokio::test]
async fn conversation_both_sides_visits_and_summaries() {
    let dir = tmp("flow");
    let (t, clk) = clock();
    let db = dir.join("mem.db");
    let mem = Memory::open(&db).unwrap().with_clock(clk);

    assert!(mem.person_detected("Unknown").unwrap().is_none());
    assert_eq!(mem.person_detected("Brandon").unwrap().unwrap().visit_count, 1);
    for (u, r) in [("play some music", "Doshka is spinning!"), ("who made you", "A very clever maker.")] {
        t.fetch_add(5, Ordering::SeqCst);
        mem.record_user(u, Some("c1")).unwrap();
        t.fetch_add(1, Ordering::SeqCst);
        mem.record_reply(r, Some("c1")).unwrap();
    }
    mem.record_track("Doshka", true).unwrap();
    let hist = mem.conversation_history(Some("Brandon"), 20).unwrap();
    assert_eq!(hist.len(), 2);
    assert_eq!((hist[1].user.as_deref(), hist[1].assistant.as_deref()), (Some("who made you"), Some("A very clever maker.")));
    assert_eq!(mem.dj_history(10).unwrap(), ["Doshka"]);

    let ctx = mem.turn_context(Some(("a workshop", t.load(Ordering::SeqCst) as f64 - 10.0)), 5).unwrap();
    assert_eq!(ctx.observations, ["What you can see - a workshop", "Speaking with: [Brandon | 1 visits | just now]"]);
    assert!(ctx.conversation_history.unwrap().contains("  You: Doshka is spinning!"));

    // same visit within 5 min, new one after
    t.fetch_add(60, Ordering::SeqCst);
    assert_eq!(mem.person_detected("Brandon").unwrap().unwrap().visit_count, 1);

    // leaving INTERACTIVE with someone who talked -> rolling summary through the LLM
    let rolling = summary::rolling_prompt("Brandon", "", &hist);
    let visit_json = r#"```json
{"visit_summary":"Asked for music.","memorable_moments":["asked who made R3X"],"notable_facts":["makes things"],
 "personality_observations":["curious"],"music_preferences":{"requests":["Doshka"]},"topics_discussed":["music"],
 "mood":"cheerful","follow_up_opportunities":["ask what he is building"]}
```"#;
    let p = mem.person_profile("Brandon").unwrap().unwrap();
    let visit = summary::visit_prompt(&p, &hist, 60.0, &["Doshka".to_string()]);
    let mem = mem.with_llm(llm_with(&dir, &[(rolling, "Brandon likes Doshka."), (visit, visit_json)]));
    mem.mode_changed("INTERACTIVE", "IDLE").await.unwrap();
    let p = mem.person_profile("Brandon").unwrap().unwrap();
    assert_eq!(p.metadata["conversation_summary"], "Brandon likes Doshka.");

    // structured visit summary (what shutdown runs for a person still present)
    assert!(mem.summarize_visit("Brandon", 60.0).await.unwrap());
    let p = mem.person_profile("Brandon").unwrap().unwrap();
    assert_eq!(p.recent_visits.len(), 1);
    assert_eq!(p.recent_visits[0].summary, "Asked for music.");
    assert_eq!(p.recent_visits[0].music_played, ["Doshka"]);
    assert_eq!(p.metadata["follow_up_opportunities"], json!(["ask what he is building"]));
    assert!(p.person_memory().unwrap().contains("<moment>asked who made R3X</moment>"));
    mem.save_profile(&p).unwrap(); // re-saving does not duplicate the visit
    assert_eq!(mem.person_profile("Brandon").unwrap().unwrap().recent_visits.len(), 1);

    t.fetch_add(400, Ordering::SeqCst);
    assert_eq!(mem.person_detected("Brandon").unwrap().unwrap().visit_count, 2);

    mem.set_state("dj_mode_active", &json!(true)).unwrap();
    drop(mem);
    // reopen: everything persisted; catch-up re-summarises (seen since last summary)
    let (_, clk2) = clock();
    let mem = Memory::open(&db).unwrap().with_clock(clk2);
    assert_eq!(mem.get_state("dj_mode_active").unwrap(), Some(json!(true)));
    let hist = mem.conversation_history(Some("Brandon"), 20).unwrap();
    let again = summary::rolling_prompt("Brandon", "Brandon likes Doshka.", &hist);
    let mem = mem.with_llm(llm_with(&dir, &[(again, "Still a Doshka fan.")]));
    assert_eq!(mem.catch_up_summaries().await.unwrap(), 1);
    assert_eq!(mem.person_profile("Brandon").unwrap().unwrap().metadata["conversation_summary"], "Still a Doshka fan.");
}

#[test]
fn imports_cantina_memory_once() {
    let dir = tmp("import");
    let root = dir.join("cantina_os");
    std::fs::create_dir_all(root.join("memory_data/profiles")).unwrap();
    std::fs::create_dir_all(root.join("cantina_os/services/nervous_system_service/data")).unwrap();
    let ev = [
        json!({"timestamp": 1.0, "event_type": "transcription.final", "event_data": {"text": "Hey Rex", "conversation_id": "c"}, "person": null, "conversation_id": "c"}),
        json!({"timestamp": 1.5, "event_type": "transcription.final", "event_data": {"text": ""}, "person": null}),
        json!({"timestamp": 2.0, "event_type": "llm.response.text", "event_data": {"text": "Oh", "is_complete": false}, "person": null}),
        json!({"timestamp": 2.1, "event_type": "llm.response.text", "event_data": {"text": "Oh YEAH!", "is_complete": true}, "person": null, "conversation_id": "c"}),
        json!({"timestamp": 3.0, "event_type": "track.playing", "event_data": {}, "person": null}),
    ];
    std::fs::write(root.join("memory_data/events.jsonl"), ev.iter().map(|e| e.to_string() + "\n").collect::<String>()).unwrap();
    std::fs::write(root.join("memory_data/profiles/Brandon.json"), json!({"name": "Brandon", "visit_count": 4, "last_seen": 5.0}).to_string()).unwrap();
    std::fs::write(
        root.join("cantina_os/services/nervous_system_service/data/nervous_system_state.json"),
        json!({"mode": "IDLE", "dj_mode_active": false, "dj_track_history": ["Goola Bukee", "Doshka"], "dj_user_preferences": {}}).to_string(),
    )
    .unwrap();

    let mem = Memory::open_in_memory().unwrap();
    let s = import::import_cantina(&mem, &root).unwrap();
    assert_eq!((s.events, s.skipped, s.profiles, s.state_keys, s.dj_tracks), (3, 2, 1, 3, 2));
    let h = mem.conversation_history(None, 10).unwrap();
    assert_eq!((h[0].user.as_deref(), h[0].assistant.as_deref()), (Some("Hey Rex"), Some("Oh YEAH!")));
    assert_eq!(mem.person_profile("Brandon").unwrap().unwrap().visit_count, 4);
    assert_eq!(mem.dj_history(5).unwrap(), ["Goola Bukee", "Doshka"]);
    assert!(mem.get_state("mode").unwrap().is_none());
    // idempotent
    let s = import::import_cantina(&mem, &root).unwrap();
    assert_eq!((s.events, s.dj_tracks), (0, 0));
}

/// The real local corpus, when present (gitignored).
#[test]
fn imports_real_events_when_present() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../cantina_os");
    if !root.join("memory_data/events.jsonl").exists() {
        return;
    }
    let mem = Memory::open_in_memory().unwrap();
    let s = import::import_cantina(&mem, &root).unwrap();
    assert!(s.events > 0 && s.skipped > 0, "{s:?}");
    let h = mem.conversation_history(None, 1000).unwrap();
    assert!(h.iter().any(|t| t.user.is_some()) && h.iter().any(|t| t.assistant.is_some()));
    eprintln!("{s:?}, {} turns", h.len());
}

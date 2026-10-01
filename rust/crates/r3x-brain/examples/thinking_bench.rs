//! `cargo run -p r3x-brain --example thinking_bench [reps]` (env: ANTHROPIC_API_KEY or
//! OPENROUTER_API_KEY; unset ANTHROPIC_BASE_URL): real turns with R3X's system prompt and tools,
//! `between_tools` vs `adaptive` thinking (effort low), interleaved so network noise hits both.
//! Prints time to the first spoken text, total time, any text before a tool call (the reasoning
//! leak: "Brandon says \"Stop.\" It's a direct command, ..."), tools and output tokens.
//! Paid: 2 modes x 6 prompts x reps calls (the system prompt is cached after the first).
use std::time::Instant;

use r3x_llm::request::{default_tools, Thinking};
use r3x_llm::{prompt, LlmClient, Message, StreamEvent};

const PROMPTS: &[&str] = &["Stop.", "play some music", "turn your eyes blue", "tell me a joke", "what's your favourite song?", "Stop playing music. Stop"];

#[tokio::main]
async fn main() {
    let reps: usize = std::env::args().nth(1).and_then(|r| r.parse().ok()).unwrap_or(2);
    let persona = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/../../../cantina_os/dj_r3x-persona.txt")).expect("persona");
    let base = LlmClient::from_env().expect("client").expect("an API key");
    println!("model {}  reps {reps}", base.model());
    if std::env::args().nth(1).as_deref() == Some("conv") {
        // Two turns, adaptive: the session keeps only text (no thinking blocks), as the brain does.
        let llm = base.clone().with_thinking(Thinking::Adaptive);
        let first = llm.create(&prompt::turn_request(persona.trim(), vec![Message::user("hi, I'm Brandon")], default_tools(), false)).await;
        let said = first.as_ref().map(|m| m.text()).unwrap_or_default();
        println!("turn 1: {:?} -> {said:?}", first.as_ref().map(|m| m.stop_reason.clone()));
        let history = vec![Message::user("hi, I'm Brandon"), Message::assistant(said), Message::user("what's my name? then play some music")];
        let second = llm.create(&prompt::turn_request(persona.trim(), history, default_tools(), false)).await;
        match second {
            Ok(m) => println!("turn 2: ok {:?} -> {:?} tools {:?}", m.stop_reason, m.text(), m.tool_uses().iter().map(|t| t.name.clone()).collect::<Vec<_>>()),
            Err(e) => println!("turn 2: ERROR {e}"),
        }
        return;
    }
    let mut rows: Vec<(Thinking, &str, f64, f64, String, Vec<String>, u64)> = vec![];
    for rep in 0..reps {
        for p in PROMPTS {
            for mode in [Thinking::BetweenTools, Thinking::Adaptive] {
                let llm = base.clone().with_thinking(mode);
                let req = prompt::turn_request(persona.trim(), vec![Message::user(*p)], default_tools(), false);
                let t0 = Instant::now();
                let mut first = None;
                let mut text = String::new();
                let mut tools = vec![];
                let mut out_tokens = 0;
                let mut stream = match llm.stream(&req).await {
                    Ok(s) => s,
                    Err(e) => {
                        println!("{mode:?} {p:?}: {e}");
                        continue;
                    }
                };
                while let Some(ev) = stream.next().await {
                    match ev {
                        Ok(StreamEvent::TextDelta(t)) => {
                            first.get_or_insert(t0.elapsed().as_secs_f64());
                            text.push_str(&t);
                        }
                        Ok(StreamEvent::ToolUse(u)) => {
                            first.get_or_insert(t0.elapsed().as_secs_f64());
                            tools.push(u.name);
                        }
                        Ok(StreamEvent::Done(m)) => {
                            out_tokens = m.usage.output_tokens;
                            break;
                        }
                        Err(e) => {
                            println!("{mode:?} {p:?}: {e}");
                            break;
                        }
                    }
                }
                let total = t0.elapsed().as_secs_f64();
                let first = first.unwrap_or(total);
                println!("rep{rep} {:<13} {:<30} first {first:5.2}s total {total:5.2}s out {out_tokens:4} tools {tools:?}\n    {:?}", format!("{mode:?}"), format!("{p:?}"), text.chars().take(140).collect::<String>());
                rows.push((mode, p, first, total, text, tools, out_tokens));
            }
        }
    }
    for mode in [Thinking::BetweenTools, Thinking::Adaptive] {
        let r: Vec<_> = rows.iter().filter(|r| r.0 == mode).collect();
        let med = |mut v: Vec<f64>| {
            v.sort_by(|a, b| a.partial_cmp(b).unwrap());
            v.get(v.len() / 2).copied().unwrap_or(0.0)
        };
        let firsts = med(r.iter().map(|x| x.2).collect());
        let totals = med(r.iter().map(|x| x.3).collect());
        let outs: u64 = r.iter().map(|x| x.6).sum();
        println!("{mode:?}: n={} median first {firsts:.2}s, median total {totals:.2}s, output tokens {outs}", r.len());
    }
}

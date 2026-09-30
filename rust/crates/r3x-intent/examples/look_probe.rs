//! `cargo run -p r3x-intent --example look_probe`: ask live Jev the look question for a set of
//! phrasings that should and should not attach the camera frame (TYPESAFE_API_KEY from .env).
//! One small Jev request per line; no Claude calls.
use std::time::Duration;

use r3x_intent::catalogue::{build_look_questions, build_state};
use r3x_intent::JevClient;

const SHOULD: &[&str] = &[
    "look at this", "what am I holding", "check out my dragon", "do you like my costume", "what colour is this",
    "can you see me", "how many fingers am I holding up",
];
const SHOULD_NOT: &[&str] = &[
    "look, I'm so tired", "have you seen the new movie", "play some music", "what's your favourite song",
    "tell me a joke", "look up the weather", "I saw a dragon at the zoo yesterday",
];

#[tokio::main]
async fn main() {
    let key = std::env::var("TYPESAFE_API_KEY").expect("TYPESAFE_API_KEY");
    let jev = JevClient::new(&key, Duration::from_secs(3));
    let q = build_look_questions();
    let threshold = 0.6;
    let mut wrong = 0;
    for (want, lines) in [(true, SHOULD), (false, SHOULD_NOT)] {
        for line in lines {
            let p = jev.classify(&build_state(line), &q).await.map(|r| r.noul("look", 0.0));
            let got = p.is_some_and(|p| p >= threshold);
            if got != want {
                wrong += 1;
            }
            println!("{} {:>5} want={want:<5} {line}", if got == want { "ok " } else { "BAD" }, p.map_or("none".into(), |p| format!("{p:.2}")));
        }
    }
    println!("{wrong} wrong at threshold {threshold}");
}

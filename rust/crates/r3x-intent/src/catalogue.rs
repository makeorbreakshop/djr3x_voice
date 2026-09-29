//! The Jev question catalogue ("design D") and parameter extraction, ported from
//! `llm/jev_intents.py`, `core/music_search.py` and `core/track_request.py`.
//!
//! Every string here is load-bearing: the thresholds were measured against this exact wording,
//! and the fixture tests check the request is byte-identical to CantinaOS's (same sha1 key).

use regex::Regex;
use serde_json::{json, Map, Value};
use std::sync::OnceLock;

pub const TOOL_INTENTS: &[&str] =
    &["play_music", "stop_music", "next_track", "dj_mode_on", "dj_mode_off", "set_eye_animation"];
pub const NO_TOOL_INTENTS: &[&str] = &["general_chat", "unclear"];

const TOOL_DESCRIPTIONS: &[(&str, &str)] = &[
    ("play_music", "Start playing music, or play a particular song, artist, or playlist."),
    ("stop_music", "Stop or pause the music that is currently playing."),
    ("next_track", "Skip the current track and play the next one."),
    ("dj_mode_on", "Turn on DJ mode, where the droid talks over the music like a club DJ."),
    ("dj_mode_off", "Turn off DJ mode and go back to normal behaviour."),
    ("set_eye_animation", "Change the droid's LED eye animation, colour, or pattern."),
];

const NOT_A_REQUEST: &str = "The speaker is chatting, commenting, reminiscing, asking about something that already \
happened, or describing something they might want later. Anything that is not an instruction to do this right now.";

fn noul(instructions: &str, when_true: &str, when_false: &str) -> Value {
    json!({"type": "noul", "instructions": instructions, "criteria": {"true": when_true, "false": when_false}})
}

fn choice(instructions: &str, criteria: &[(&str, &str)]) -> Value {
    let c: Map<String, Value> = criteria.iter().map(|(k, v)| ((*k).to_string(), json!(v))).collect();
    json!({"type": "choice", "instructions": instructions, "criteria": c})
}

fn tool_nouls() -> Vec<(&'static str, Value)> {
    vec![
        ("play_music", noul(
            "Is the speaker telling the assistant to start playing music right now?",
            "They ask for music, songs, tunes, a mix, a playlist, or one named song or artist, to be started now. Slang for it \
counts: \"spin something up\", \"start the jams\", \"put something on\".",
            &format!("{NOT_A_REQUEST} Asking to skip to a different song is not this."),
        )),
        ("stop_music", noul(
            "Is the speaker telling the assistant to stop or pause the music right now?",
            "They ask for the music, song, or sound to stop, pause, end, cut out, or go quiet now, including \"knock it off\", \
\"enough\", \"silence\", \"quiet please\".",
            NOT_A_REQUEST,
        )),
        ("next_track", noul(
            "Is the speaker telling the assistant to skip the current track and play the next one?",
            "They ask to skip, advance, or change to the next song, or say they dislike this one and want a different one.",
            &format!("{NOT_A_REQUEST} Asking to start music when none is playing is not this."),
        )),
        ("dj_mode_on", noul(
            "Is the speaker telling the assistant to enter DJ mode?",
            "They name DJ mode, or ask the droid to act as a DJ, host, or MC over the music, or to \"do your DJ thing\".",
            &format!("{NOT_A_REQUEST} Asking merely to play music is not this."),
        )),
        ("dj_mode_off", noul(
            "Is the speaker telling the assistant to leave DJ mode?",
            "They ask to exit, end, or turn off DJ mode, or to stop acting as a DJ.",
            &format!("{NOT_A_REQUEST} Asking to stop the music is not this."),
        )),
        ("set_eye_animation", noul(
            "Is the speaker telling the assistant to change its LED eye colour, pattern, or animation?",
            "They name the eyes, or a colour, flash, blink, scan, pulse, or rainbow effect the droid should display.",
            &format!("{NOT_A_REQUEST} Asking about room lights or a screen is not this."),
        )),
    ]
}

pub const MUSIC_VIBE_QUERIES: &[(&str, &str)] = &[
    ("fun_upbeat_playful", "fun upbeat playful party music"),
    ("calm_relaxing", "calm relaxing gentle background music"),
    ("dark_aggressive", "dark aggressive heavy intense music"),
    ("bright_uplifting", "bright optimistic cheerful uplifting music"),
    ("quirky_robotic", "quirky robotic electronic strange music"),
    ("cinematic_adventure", "cinematic dramatic epic space adventure music"),
];
pub const MUSIC_NEGATIVE_HEAVY_QUERY: &str = "heavy aggressive dark intense music";
pub const MUSIC_KIND_MIN_CONFIDENCE: f64 = 0.70;
pub const MUSIC_VIBE_MIN_CONFIDENCE: f64 = 0.70;
pub const MUSIC_AVOID_MIN_NOUL: f64 = 0.70;

/// The full question set, one parallel request per utterance.
pub fn build_questions() -> Value {
    let mut q = Map::new();
    let mut intent_criteria: Vec<(&str, &str)> = TOOL_DESCRIPTIONS.to_vec();
    intent_criteria.push(("general_chat", "Nothing to do: the speaker is chatting, greeting, complimenting, joking, asking an \
opinion or a general-knowledge question, commenting on something already happening, or describing something they may want \
at some later time."));
    intent_criteria.push(("unclear", "An action is wanted but the utterance does not say which: it is cut off, garbled, only \
a wake word, or too vague to match any of the tools above."));
    q.insert("intent".into(), choice("Which one thing is the speaker asking the assistant to do right now?", &intent_criteria));
    for (k, v) in tool_nouls() {
        q.insert(k.into(), v);
    }
    q.insert("is_a_command".into(), noul(
        "Is the speaker telling the assistant to do something, as opposed to talking with it?",
        "The utterance is an instruction or a request for an action.",
        "The utterance is conversation: a greeting, a compliment, a joke request, an opinion question, small talk, a question \
about something the assistant already did, or a fragment that asks for nothing.",
    ));
    q.insert("music_request_kind".into(), choice("What kind of music request is contained in this full utterance?", &[
        ("named_catalog_item", "A particular song, artist, album, or title is named, including a close phonetic or \
speech-to-text rendering."),
        ("semantic_vibe", "Music is requested using mood, energy, style, atmosphere, or exclusions, without naming one \
particular catalog item."),
        ("generic_music", "Music is requested, but there is no name, mood, style, atmosphere, energy, or exclusion."),
        ("not_music", "There is no instruction to play music now."),
    ]));
    q.insert("music_positive_vibe".into(), choice("Which musical quality is the strongest positive request in the utterance?", &[
        ("fun_upbeat_playful", "Fun, upbeat, playful, lively, party-like, or celebratory."),
        ("calm_relaxing", "Calm, relaxing, quiet, gentle, mellow, or background music."),
        ("dark_aggressive", "Dark, aggressive, heavy, angry, hard, or intense."),
        ("bright_uplifting", "Bright, optimistic, hopeful, cheerful, or uplifting."),
        ("quirky_robotic", "Quirky, robotic, electronic, strange, or droid-like."),
        ("cinematic_adventure", "Cinematic, dramatic, epic, space, or adventure music."),
        ("unspecified_or_other", "No positive musical quality is requested, or it is outside these choices."),
    ]));
    q.insert("music_avoid_heavy_aggressive".into(), noul(
        "Does the speaker explicitly ask to avoid heavy, aggressive, dark, angry, hard, or intense music?",
        "They directly exclude one or more of those qualities.",
        "They request one of those qualities, are neutral about them, or say nothing about them.",
    ));
    Value::Object(q)
}

/// The identity line - load-bearing ("put on some cantina tunes" is general_chat without it).
/// No conversation history, deliberately: it made the router eager.
pub const ASSISTANT_IDENTITY: &str = "You are DJ R3X, a Star Wars droid DJ in a cantina. You play music, run a DJ mode where \
you talk over the tracks, and you have LED eyes you can change.";

/// `json.dumps(build_state(utterance))`, byte-identical (default separators, ensure_ascii).
pub fn build_state(utterance: &str) -> String {
    let mut s = String::from("{\"assistant\": ");
    r3x_llm::pyjson::write_str(&mut s, ASSISTANT_IDENTITY);
    s.push_str(", \"utterance\": ");
    r3x_llm::pyjson::write_str(&mut s, utterance);
    s.push('}');
    s
}

// ------------------------------------------------------------------------------------------
// Parameter extraction
// ------------------------------------------------------------------------------------------

pub const SEMANTIC_PREFIX: &str = "@semantic";
pub const AVOID_SEPARATOR: &str = "@avoid";

pub const SEMANTIC_MUSIC_WORDS: &[&str] = &[
    "aggressive", "ambient", "angry", "atmospheric", "bright", "calm", "celebratory", "cheerful", "chill", "cinematic",
    "crazy", "craziest", "country", "dance", "dark", "dramatic", "dreamy", "electronic", "energetic", "energy", "epic",
    "funk", "funky", "fun", "gentle", "happy", "hard", "heavy", "hopeful", "intense", "jazz", "lively", "melancholy",
    "mellow", "metal", "optimistic", "party", "peaceful", "playful", "quirky", "relaxing", "robotic", "rock", "sad",
    "scary", "soft", "space", "spooky", "strange", "synth", "uplifting", "upbeat", "banger",
];
const HEAVY_AGGRESSIVE_WORDS: &[&str] = &["aggressive", "angry", "dark", "hard", "heavy", "intense"];

/// Words that say nothing about *which* track (`track_request.GENERIC_REQUEST_WORDS`).
pub const GENERIC_REQUEST_WORDS: &[&str] = &[
    "a", "ahead", "along", "an", "and", "any", "anything", "beat", "beats", "can", "could", "do", "for", "go", "going",
    "have", "hear", "hey", "i", "in", "it", "jam", "jams", "just", "let", "lets", "let's", "like", "listen", "me", "music",
    "my", "now", "of", "ok", "okay", "on", "one", "play", "playing", "please", "put", "r3x", "rex", "s", "shuffle", "some",
    "something", "song", "songs", "sound", "sounds", "spin", "start", "the", "thing", "to", "track", "tracks", "tune",
    "tunes", "up", "us", "want", "we", "would", "yeah", "yes", "you",
];

/// Longest-first so "magenta" is not read as something shorter.
pub const EYE_COLORS: &[&str] = &[
    "rainbow", "magenta", "purple", "orange", "yellow", "silver", "violet", "maroon", "indigo", "golden", "green", "white",
    "black", "amber", "cyan", "teal", "blue", "pink", "gold", "red",
];
pub const EYE_PATTERNS: &[(&str, &str)] = &[
    ("solid", "solid"), ("flash", "flash"), ("flashing", "flash"), ("blink", "flash"), ("blinking", "flash"),
    ("pulse", "flash"), ("pulsing", "flash"), ("happy", "happy"), ("sad", "sad"), ("angry", "angry"),
    ("surprised", "surprised"), ("idle", "idle"), ("thinking", "thinking"),
];

fn words(text: &str) -> Vec<String> {
    static RE: OnceLock<Regex> = OnceLock::new();
    let re = RE.get_or_init(|| Regex::new(r"[a-z0-9']+").expect("static regex"));
    re.find_iter(&text.to_lowercase()).map(|m| m.as_str().to_string()).collect()
}

/// What in a spoken request names a track: a bare number, the non-filler words, or `None`.
pub fn naming_phrase(request: &str) -> Option<String> {
    let text = request.trim();
    if text.is_empty() {
        return None;
    }
    if text.chars().all(|c| c.is_ascii_digit()) {
        return Some(text.to_string());
    }
    let meaningful: Vec<String> = words(text).into_iter().filter(|w| !GENERIC_REQUEST_WORDS.contains(&w.as_str())).collect();
    (!meaningful.is_empty()).then(|| meaningful.join(" "))
}

fn squash(s: &str) -> String {
    s.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// `@semantic <query> [@avoid <negative>]`, the transport-safe music request.
pub fn encode_semantic_request(query: &str, negative: Option<&str>) -> Option<String> {
    let positive = squash(query);
    if positive.is_empty() {
        return None;
    }
    let mut s = format!("{SEMANTIC_PREFIX} {positive}");
    if let Some(n) = negative.map(squash).filter(|n| !n.is_empty()) {
        s.push_str(&format!(" {AVOID_SEPARATOR} {n}"));
    }
    Some(s)
}

/// Keep the speaker's own musical descriptors, in order, once each.
pub fn semantic_query_from_utterance(utterance: &str, avoid_heavy: bool) -> String {
    let mut out: Vec<String> = Vec::new();
    for w in words(utterance) {
        if SEMANTIC_MUSIC_WORDS.contains(&w.as_str())
            && !(avoid_heavy && HEAVY_AGGRESSIVE_WORDS.contains(&w.as_str()))
            && !out.contains(&w)
        {
            out.push(w);
        }
    }
    out.join(" ")
}

/// First whole-word match from `list`, in list order.
pub fn word_search<'a>(text: &str, list: impl IntoIterator<Item = &'a str>) -> Option<&'a str> {
    let lowered = text.to_lowercase();
    list.into_iter().find(|w| {
        Regex::new(&format!(r"\b{}\b", regex::escape(w))).is_ok_and(|re| re.is_match(&lowered))
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extraction_helpers() {
        assert_eq!(naming_phrase("Yeah. Go ahead and play some music for me."), None);
        assert_eq!(naming_phrase("put on Huttuk Cheeka").as_deref(), Some("huttuk cheeka"));
        assert_eq!(naming_phrase("4").as_deref(), Some("4"));
        assert_eq!(naming_phrase("play track 4").as_deref(), Some("4"));
        assert_eq!(semantic_query_from_utterance("Chill, CHILL space jazz, not heavy", true), "chill space jazz");
        assert_eq!(encode_semantic_request(" calm  music ", Some("heavy music")).unwrap(), "@semantic calm music @avoid heavy music");
        assert_eq!(word_search("Eyes RED please", EYE_COLORS.iter().copied()), Some("red"));
        assert_eq!(word_search("reddish", EYE_COLORS.iter().copied()), None);
        assert!(build_state("é").ends_with("\"utterance\": \"\\u00e9\"}"));
    }
}

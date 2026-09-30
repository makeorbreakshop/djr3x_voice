//! Inline performance tags in Claude's replies: `{cue:<id>}` / `{clip:<id>}` (port of
//! `cantina_os/show/tags.py`).
//!
//! - [`TagParser`] strips tags from the stream chunk by chunk (an unclosed `{` is held across
//!   chunks), so nothing downstream ever sees tag text; at most two tags per reply.
//! - [`TagScheduler`] holds a reply's tags until its speech starts, arms each at
//!   `offset / chars_per_sec` (the fallback pace), then moves it onto its character's real
//!   start when the voice's character timing arrives, so the move fires on the word.

use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Duration;

use regex::Regex;

/// A held-back `{` longer than this without a `}` is plain text, not a tag.
pub const MAX_TAG_LEN: usize = 64;
pub const MAX_TAGS_PER_REPLY: usize = 2;
/// `SHOW_TAG_CHARS_PER_SEC` default in `claude_service.py` (the docs' 19 is stale).
pub const DEFAULT_CHARS_PER_SEC: f64 = 13.0;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Tag {
    /// `cue` | `clip`
    pub kind: String,
    pub id: String,
    /// Character offset into the clean text.
    pub offset: usize,
}

fn tag_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| Regex::new(r"^\{\s*(cue|clip)\s*:\s*([a-z][a-z0-9_]*)\s*\}$").unwrap())
}

fn partial_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| Regex::new(r"^\{\s*(c|cu|cue|cl|cli|clip)?\s*(:\s*[a-z0-9_]*)?\s*$").unwrap())
}

/// Incremental, chunking-invariant tag stripper. `valid` = the `(kind, id)` pairs that may be
/// performed (`None` = any); anything else in braces is dropped from the text and reported.
#[derive(Debug, Default)]
pub struct TagParser {
    valid: Option<Arc<HashSet<(String, String)>>>,
    pub tags: Vec<Tag>,
    pub dropped: Vec<String>,
    held: String,
    held_len: usize,
    clean_len: usize,
    last_char: Option<char>,
    skip_space: bool,
}

impl TagParser {
    pub fn new(valid: Option<Arc<HashSet<(String, String)>>>) -> Self {
        Self { valid, ..Default::default() }
    }

    pub fn feed(&mut self, text: &str) -> String {
        let mut out = String::new();
        for ch in text.chars() {
            if !self.held.is_empty() {
                self.held.push(ch);
                self.held_len += 1;
                if ch == '}' {
                    self.close_tag();
                } else if ch == '{' || ch == '\n' || self.held_len > MAX_TAG_LEN {
                    // Not a tag after all: release what was held (a fresh "{" re-opens).
                    let held = std::mem::take(&mut self.held);
                    self.held_len = 0;
                    if ch == '{' {
                        let body: String = held.chars().take(held.chars().count() - 1).collect();
                        self.emit(&body, &mut out);
                        self.held.push('{');
                        self.held_len = 1;
                    } else {
                        self.emit(&held, &mut out);
                    }
                }
                continue;
            }
            if ch == '{' {
                self.held.push('{');
                self.held_len = 1;
                continue;
            }
            self.emit_char(ch, &mut out);
        }
        out
    }

    /// End of reply. An unclosed `{...` that could still have been a tag is dropped.
    pub fn flush(&mut self) -> String {
        let held = std::mem::take(&mut self.held);
        self.held_len = 0;
        if held.is_empty() {
            return String::new();
        }
        if partial_re().is_match(&held) {
            self.dropped.push(held);
            return String::new();
        }
        let mut out = String::new();
        self.emit(&held, &mut out);
        out
    }

    fn emit(&mut self, text: &str, out: &mut String) {
        for ch in text.chars() {
            self.emit_char(ch, out);
        }
    }

    fn emit_char(&mut self, ch: char, out: &mut String) {
        if self.skip_space && (ch == ' ' || ch == '\t') {
            return;
        }
        self.skip_space = false;
        out.push(ch);
        self.clean_len += 1;
        self.last_char = Some(ch);
    }

    fn close_tag(&mut self) {
        let held = std::mem::take(&mut self.held);
        self.held_len = 0;
        match tag_re().captures(&held) {
            None => self.dropped.push(held),
            Some(c) => {
                let (kind, id) = (c[1].to_string(), c[2].to_string());
                let known = self.valid.as_ref().is_none_or(|v| v.contains(&(kind.clone(), id.clone())));
                if !known || self.tags.len() >= MAX_TAGS_PER_REPLY {
                    self.dropped.push(held);
                } else {
                    self.tags.push(Tag { kind, id, offset: self.clean_len });
                }
            }
        }
        // Removing a tag from "word {tag} word" must not leave a double space.
        if self.clean_len == 0 || matches!(self.last_char, Some(' ' | '\t' | '\n')) {
            self.skip_space = true;
        }
    }
}

/// Whole-text form: `(clean, tags, dropped)`.
pub fn extract_tags(text: &str, valid: Option<Arc<HashSet<(String, String)>>>) -> (String, Vec<Tag>, Vec<String>) {
    let mut p = TagParser::new(valid);
    let mut clean = p.feed(text);
    clean.push_str(&p.flush());
    (clean, p.tags, p.dropped)
}

/// Fires `perform(id, conversation_id)` for one tag.
pub type Perform = Arc<dyn Fn(String, Option<String>) + Send + Sync>;
/// The bus's monotonic seconds (the clock `SpeechTiming.audio_t0` is on).
pub type Now = Arc<dyn Fn() -> f64 + Send + Sync>;

struct Item {
    tag: Tag,
    /// Index into the spoken (stripped) text.
    index: usize,
    fired: AtomicBool,
    refined: AtomicBool,
    /// Bumped on every re-arm; a timer only fires if its generation is still current.
    gen: AtomicU64,
}

struct Active {
    items: Vec<Arc<Item>>,
    seen: usize,
}

/// Holds each turn's tags until its speech starts, then fires them on the word.
pub struct TagScheduler {
    perform: Perform,
    now: Now,
    pub chars_per_sec: f64,
    pending: Mutex<HashMap<String, (String, Vec<Tag>)>>,
    active: Mutex<HashMap<String, Active>>,
}

impl TagScheduler {
    pub fn new(perform: Perform, now: Now, chars_per_sec: f64) -> Arc<Self> {
        Arc::new(Self { perform, now, chars_per_sec: chars_per_sec.max(1e-3), pending: Mutex::default(), active: Mutex::default() })
    }

    pub fn register(&self, conversation_id: &str, clean_text: &str, tags: Vec<Tag>) {
        if !tags.is_empty() {
            self.pending.lock().unwrap().insert(conversation_id.into(), (clean_text.into(), tags));
        }
    }

    pub fn has_pending(&self, conversation_id: &str) -> bool {
        self.pending.lock().unwrap().contains_key(conversation_id)
    }

    /// The reply's first audible sample. Returns `[(id, delay_s)]` as armed.
    pub fn on_speech_started(self: &Arc<Self>, conversation_id: &str) -> Vec<(String, f64)> {
        let Some((clean, tags)) = self.pending.lock().unwrap().remove(conversation_id) else { return vec![] };
        // The voice speaks the reply trimmed; shift offsets by what was stripped.
        let lead = clean.chars().take_while(|c| c.is_whitespace()).count();
        let mut report = Vec::new();
        let mut items = Vec::new();
        for tag in tags {
            let item = Arc::new(Item {
                index: tag.offset.saturating_sub(lead),
                tag,
                fired: AtomicBool::new(false),
                refined: AtomicBool::new(false),
                gen: AtomicU64::new(0),
            });
            let delay = item.index as f64 / self.chars_per_sec;
            self.arm(&item, conversation_id, delay);
            report.push((item.tag.id.clone(), delay));
            items.push(item);
        }
        tracing::info!(conversation_id, ?report, "show tags scheduled");
        self.active.lock().unwrap().insert(conversation_id.into(), Active { items, seen: 0 });
        report
    }

    /// One chunk of character timing (`start_ms` from the line's first sample, heard at
    /// `audio_t0` on the bus clock). Returns `[(id, new_delay_s)]` for the tags it moved.
    pub fn on_timing(self: &Arc<Self>, conversation_id: &str, chars: usize, start_ms: &[f64], audio_t0: f64) -> Vec<(String, f64)> {
        let mut moved = Vec::new();
        let mut active = self.active.lock().unwrap();
        let Some(a) = active.get_mut(conversation_id) else { return moved };
        let n = chars.min(start_ms.len());
        for item in &a.items {
            let Some(i) = item.index.checked_sub(a.seen).filter(|i| *i < n) else { continue };
            if item.fired.load(Ordering::SeqCst) || item.refined.swap(true, Ordering::SeqCst) {
                continue;
            }
            let delay = (audio_t0 + start_ms[i] / 1000.0 - (self.now)()).max(0.0);
            self.arm(item, conversation_id, delay);
            moved.push((item.tag.id.clone(), delay));
        }
        a.seen += chars;
        moved
    }

    pub fn cancel_all(&self) {
        for a in self.active.lock().unwrap().drain().map(|(_, a)| a) {
            for i in a.items {
                i.fired.store(true, Ordering::SeqCst);
            }
        }
        self.pending.lock().unwrap().clear();
    }

    fn arm(self: &Arc<Self>, item: &Arc<Item>, conversation_id: &str, delay: f64) {
        let gen = item.gen.fetch_add(1, Ordering::SeqCst) + 1;
        let (me, item, cid) = (self.clone(), item.clone(), conversation_id.to_string());
        tokio::spawn(async move {
            tokio::time::sleep(Duration::from_secs_f64(delay)).await;
            if item.gen.load(Ordering::SeqCst) != gen || item.fired.swap(true, Ordering::SeqCst) {
                return;
            }
            {
                let mut active = me.active.lock().unwrap();
                if active.get(&cid).is_some_and(|a| a.items.iter().all(|i| i.fired.load(Ordering::SeqCst))) {
                    active.remove(&cid);
                }
            }
            (me.perform)(item.tag.id.clone(), Some(cid));
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn valid(pairs: &[(&str, &str)]) -> Option<Arc<HashSet<(String, String)>>> {
        Some(Arc::new(pairs.iter().map(|(k, i)| (k.to_string(), i.to_string())).collect()))
    }

    #[test]
    fn chunking_invariant_with_offsets_and_limits() {
        let text = "{clip:beat_bop} Oh YEAH, {cue:excited} spinning {cue:nope} now {clip:nod} {clip:nod}!";
        let v = valid(&[("clip", "beat_bop"), ("cue", "excited"), ("clip", "nod")]);
        let (clean, tags, dropped) = extract_tags(text, v.clone());
        assert_eq!(clean, "Oh YEAH, spinning now !");
        assert_eq!(tags.iter().map(|t| (t.id.as_str(), t.offset)).collect::<Vec<_>>(), [("beat_bop", 0), ("excited", 9)]);
        assert_eq!(dropped.len(), 3, "unknown + two over the limit: {dropped:?}");
        // any chunking gives the same text and offsets
        for size in 1..8 {
            let mut p = TagParser::new(v.clone());
            let chars: Vec<char> = text.chars().collect();
            let mut out: String = chars.chunks(size).map(|c| p.feed(&c.iter().collect::<String>())).collect();
            out.push_str(&p.flush());
            assert_eq!((out, p.tags.clone()), (clean.clone(), tags.clone()), "chunk {size}");
        }
    }

    #[test]
    fn held_brace_never_leaks_and_non_tags_release() {
        let mut p = TagParser::new(None);
        assert_eq!(p.feed("Hi {cl"), "Hi ");
        assert_eq!(p.feed("ip:no"), "");
        assert_eq!(p.feed("d} there"), "there");
        assert_eq!(p.tags[0].offset, 3);
        let (clean, _, dropped) = extract_tags("a {x\nb} c {cue:", None);
        assert_eq!(clean, "a {x\nb} c ");
        assert_eq!(dropped, ["{cue:"]);
    }

    #[tokio::test(start_paused = true)]
    async fn fires_on_fallback_pace_then_on_the_word() {
        let fired = Arc::new(Mutex::new(Vec::new()));
        let f = fired.clone();
        let t0 = tokio::time::Instant::now();
        let now: Now = Arc::new(move || t0.elapsed().as_secs_f64());
        let s = TagScheduler::new(Arc::new(move |id, _| f.lock().unwrap().push((id, t0.elapsed().as_millis()))), now, 10.0);
        let (clean, tags, _) = extract_tags(" Hello {clip:a}world {cue:b}yes", None);
        s.register("c1", &clean, tags);
        assert!(s.on_speech_started("nope").is_empty());
        let armed = s.on_speech_started("c1");
        assert_eq!(armed, [("a".to_string(), 0.6), ("b".to_string(), 1.2)]);
        // timing for the first 8 chars: 'w' (index 6) starts at 300 ms from audio_t0 = 0.05
        let moved = s.on_timing("c1", 8, &[0., 50., 100., 150., 200., 250., 300., 350.], 0.05);
        assert_eq!(moved.len(), 1);
        tokio::time::sleep(Duration::from_secs(2)).await;
        assert_eq!(*fired.lock().unwrap(), [("a".to_string(), 350), ("b".to_string(), 1200)]);
    }
}

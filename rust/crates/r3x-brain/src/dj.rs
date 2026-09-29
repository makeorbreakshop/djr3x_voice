//! DJ mode (`brain_service.py` + the commentary half of `claude_service.py`).
//!
//! Start: pick a track (avoiding recent ones), play it, run the `dj_intro` sequence on the
//! `show` layer, and ask Claude for an intro line that is cached and played with ducking.
//! Lookahead: pick the next track and cache its transition commentary - immediately, again
//! after every transition, and on a 15 s loop as a retry. Nothing in it blocks: Claude and the
//! speech cache are awaited on their own tasks (CantinaOS stalled its event loop ~3 s here, so a
//! turn's outcome depended on the poll phase). At `track_ending_soon`: a transition plan with
//! the cached line (duck, speak, crossfade under it, unduck), else urgent caching and a 3 s
//! grace, else an emergency pick with a crossfade-only plan.

use std::collections::{HashMap, VecDeque};
use std::time::Duration;

use r3x_contracts::{Command, ConversationEvent, DjEvent, DjState as DjRetained, Event, MusicEvent, MusicState, PerfCommand, PerfLayer, Source, StopTarget, Track};
use r3x_llm::MessagesRequest;

use crate::plan::{Plan, Step};
use crate::Brain;

pub const INTRO_LOCAL: &str = include_str!("prompts/intro_local.txt");
pub const INTRO_SPOTIFY: &str = include_str!("prompts/intro_spotify.txt");
pub const TRANSITION_LOCAL: &str = include_str!("prompts/transition_local.txt");
pub const TRANSITION_SPOTIFY: &str = include_str!("prompts/transition_spotify.txt");
const FALLBACK_PROMPT: &str = "Generate a brief DJ commentary for the music. Keep it energetic and in character as DJ R3X, 2-3 sentences max.";

#[derive(Debug, Clone)]
pub struct DjConfig {
    pub cache_interval: Duration,
    pub max_recent: usize,
    pub crossfade_s: f64,
    /// How long `track_ending_soon` waits for urgent caching before planning anyway.
    pub urgent_grace: Duration,
}

impl Default for DjConfig {
    fn default() -> Self {
        Self { cache_interval: Duration::from_secs(15), max_recent: 10, crossfade_s: 8.0, urgent_grace: Duration::from_secs(3) }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Context {
    Intro,
    Transition,
}

#[derive(Debug, Clone)]
struct Request {
    context: Context,
    /// The track the commentary leads into (transition) or introduces (intro).
    track: String,
    cache_key: Option<String>,
    ready: bool,
}

#[derive(Debug, Default)]
pub struct DjState {
    pub active: bool,
    pub current: Option<String>,
    pub next: Option<String>,
    recent: VecDeque<String>,
    /// request id -> commentary request.
    requests: HashMap<String, Request>,
    /// Plans the DJ submitted (failure recovery applies only to these).
    plans: Vec<String>,
}

impl DjState {
    fn next_ready_key(&self) -> Option<(String, String)> {
        let next = self.next.as_ref()?;
        self.requests
            .iter()
            .find(|(_, r)| r.context == Context::Transition && r.ready && &r.track == next)
            .and_then(|(id, r)| Some((id.clone(), r.cache_key.clone()?)))
    }

    fn next_requested(&self) -> bool {
        self.next.as_ref().is_some_and(|n| self.requests.values().any(|r| r.context == Context::Transition && &r.track == n))
    }
}

/// Claude's commentary prompt for local (cantina) or Spotify tracks, byte-identical to
/// CantinaOS's so recorded fixtures replay.
pub fn commentary_prompt(context: Context, current: &Track, next: Option<&Track>) -> String {
    let spotify = |t: &Track| t.path.as_deref().is_some_and(|p| p.starts_with("spotify:"));
    let artist = |t: &Track| t.artist.clone().unwrap_or_else(|| "None".into());
    match (context, next) {
        (Context::Transition, Some(n)) => {
            let tpl = if spotify(current) { TRANSITION_SPOTIFY } else { TRANSITION_LOCAL };
            tpl.replace("{current_track.title}", &current.title)
                .replace("{current_track.artist}", &artist(current))
                .replace("{next_track.title}", &n.title)
                .replace("{next_track.artist}", &artist(n))
        }
        (Context::Intro, _) => {
            let tpl = if spotify(current) { INTRO_SPOTIFY } else { INTRO_LOCAL };
            tpl.replace("{current_track.title}", &current.title).replace("{current_track.artist}", &artist(current))
        }
        _ => FALLBACK_PROMPT.into(),
    }
}

fn track(title: &str) -> Track {
    Track { title: title.into(), ..Default::default() }
}

impl Brain {
    fn library(&self) -> Vec<String> {
        self.inner.bus.get::<MusicState>().library
    }

    fn sync_retained(&self) {
        let (active, current, next) = {
            let d = self.inner.dj.lock().unwrap();
            (d.active, d.current.clone(), d.next.clone())
        };
        self.inner.bus.set(Source::Timeline, DjRetained { active, current: current.map(|t| track(&t)), next: next.map(|t| track(&t)) });
    }

    /// Random pick avoiding recent tracks; history clears once everything was recent.
    fn pick_track(&self) -> Option<String> {
        let lib = self.library();
        if lib.is_empty() {
            return None;
        }
        let mut options: Vec<String> = {
            let d = self.inner.dj.lock().unwrap();
            lib.iter().filter(|t| !d.recent.contains(t)).cloned().collect()
        };
        if options.is_empty() {
            self.inner.dj.lock().unwrap().recent.clear();
            options = lib;
        }
        (self.inner.chooser)("brain.next_track", &options)
    }

    fn remember(&self, title: &str) {
        let max = self.inner.cfg.dj.max_recent;
        let mut d = self.inner.dj.lock().unwrap();
        d.recent.push_back(title.into());
        while d.recent.len() > max {
            d.recent.pop_front();
        }
    }

    fn submit_dj(&self, plan: Plan) {
        self.inner.dj.lock().unwrap().plans.push(plan.plan_id.clone());
        if let Err(e) = self.inner.exec.submit(plan) {
            tracing::error!("DJ plan: {e}");
        }
    }

    pub async fn dj_start(&self) -> (String, bool) {
        if self.inner.dj.lock().unwrap().active {
            return ("DJ mode is already active".into(), false);
        }
        if self.library().is_empty() {
            return ("Error: No music tracks available. Please ensure music files are loaded.".into(), true);
        }
        let Some(first) = self.pick_track() else {
            return ("Error: No music tracks available. Please ensure music files are loaded.".into(), true);
        };
        {
            let mut d = self.inner.dj.lock().unwrap();
            d.active = true;
            d.current = Some(first.clone());
            d.next = None;
        }
        self.remember(&first);
        self.persist("dj_mode_active", serde_json::json!(true));
        self.inner.bus.publish(Source::Timeline, None, Event::Dj(DjEvent::Started));
        self.sync_retained();
        self.inner.bus.publish(Source::Timeline, None, Event::Music(MusicEvent::Play { query: Some(first.clone()) }));
        // The dj_intro show sequence, if it exists: `show` layer, so replies neither pause nor
        // cancel it; optional, so a missing file is skipped quietly.
        let intro = Step::Sequence { id: "dj_intro".into(), params: None, wait_for_completion: false, optional: true };
        let mut plan = Plan::new("show", vec![intro]);
        plan.plan_id = format!("dj-intro-{}", &plan.plan_id[..8]);
        self.submit_dj(plan);
        self.request_commentary(Context::Intro, &first, None);
        self.dj_cache_tick();
        ("DJ mode activated...".into(), false)
    }

    pub async fn dj_stop(&self) -> (String, bool) {
        if !self.inner.dj.lock().unwrap().active {
            return ("DJ mode is not active".into(), false);
        }
        self.persist("dj_mode_active", serde_json::json!(false));
        tokio::time::sleep(Duration::from_millis(50)).await;
        *self.inner.dj.lock().unwrap() = DjState { recent: VecDeque::new(), ..Default::default() };
        self.inner.bus.publish(Source::Timeline, None, Event::Dj(DjEvent::Stopped));
        self.sync_retained();
        self.inner.exec.cancel_all();
        let stop = Command::Perf(PerfCommand::Stop(StopTarget::Layer { layer: PerfLayer::Show }));
        let _ = self.inner.bus.command(Source::Timeline, None, stop).await;
        tokio::time::sleep(Duration::from_millis(100)).await;
        self.inner.bus.publish(Source::Timeline, None, Event::Music(MusicEvent::Stop));
        ("DJ mode deactivated".into(), false)
    }

    /// Skip now: with the cached line if it is ready, else a straight crossfade.
    pub async fn dj_next(&self) -> (String, bool) {
        if !self.inner.dj.lock().unwrap().active {
            return ("Error: Cannot skip track, DJ mode is not active".into(), true);
        }
        let ready = self.inner.dj.lock().unwrap().next_ready_key().is_some();
        if ready {
            self.transition_plan();
            return ("Skipping to next track...".into(), false);
        }
        if self.inner.dj.lock().unwrap().next.is_none() {
            let pick = self.pick_track();
            self.inner.dj.lock().unwrap().next = pick;
        }
        if self.inner.dj.lock().unwrap().next.is_none() {
            return ("Error: Cannot skip - no next track available".into(), true);
        }
        self.crossfade_only_plan();
        ("Skipping to next track...".into(), false)
    }

    fn persist(&self, key: &str, v: serde_json::Value) {
        if let Some(m) = &self.inner.memory {
            if let Err(e) = m.set_state(key, &v) {
                tracing::warn!("memory: {e}");
            }
        }
    }

    /// The 15 s lookahead loop (a retry: the real triggers are start and each transition).
    pub(crate) async fn dj_loop(self) {
        loop {
            tokio::time::sleep(self.inner.cfg.dj.cache_interval).await;
            self.dj_cache_tick();
        }
    }

    /// Select the next track if none, and request its transition commentary if not yet asked.
    pub(crate) fn dj_cache_tick(&self) {
        let (active, current, has_next, requested) = {
            let d = self.inner.dj.lock().unwrap();
            (d.active, d.current.clone(), d.next.is_some(), d.next_requested())
        };
        let Some(current) = current.filter(|_| active) else { return };
        if !has_next {
            let Some(next) = self.pick_track() else {
                tracing::warn!("DJ lookahead: no tracks to pick from");
                return;
            };
            self.inner.dj.lock().unwrap().next = Some(next.clone());
            self.inner.bus.publish(Source::Timeline, None, Event::Dj(DjEvent::NextSelected { track: track(&next) }));
            self.sync_retained();
        } else if requested {
            return;
        }
        let next = self.inner.dj.lock().unwrap().next.clone();
        if let Some(next) = next {
            self.request_commentary(Context::Transition, &current, Some(&next));
        }
    }

    /// Ask Claude for a line (own task), then ask the speech cache to synthesise it.
    fn request_commentary(&self, context: Context, current: &str, next: Option<&str>) {
        let id = uuid::Uuid::new_v4().to_string();
        let lead = next.unwrap_or(current).to_string();
        self.inner.dj.lock().unwrap().requests.insert(id.clone(), Request { context, track: lead, cache_key: None, ready: false });
        let prompt = commentary_prompt(context, &track(current), next.map(track).as_ref());
        let me = self.clone();
        tokio::spawn(async move {
            let Some(llm) = me.inner.llm.clone() else { return };
            let req = MessagesRequest::new(150).system(me.inner.dj_persona.clone(), false).user(prompt).temperature(0.8);
            let text = match llm.create(&req).await {
                Ok(m) => m.text(),
                Err(e) => {
                    tracing::error!("DJ commentary failed: {e}");
                    String::new()
                }
            };
            if text.trim().is_empty() {
                me.inner.dj.lock().unwrap().requests.remove(&id);
                return;
            }
            let key = match context {
                Context::Intro => format!("commentary_intro_{}", &id[..8]),
                Context::Transition => format!("commentary_{id}"),
            };
            {
                let mut d = me.inner.dj.lock().unwrap();
                let active = d.active;
                match d.requests.get_mut(&id) {
                    Some(r) if active => r.cache_key = Some(key.clone()),
                    _ => return, // DJ stopped meanwhile
                }
            }
            me.inner.bus.publish(Source::Timeline, None, Event::Conversation(ConversationEvent::CacheSpeech { key, text }));
        });
    }

    pub(crate) fn dj_speech_cached(&self, key: &str, _duration_s: f64) {
        let intro = {
            let mut d = self.inner.dj.lock().unwrap();
            let Some(r) = d.requests.values_mut().find(|r| r.cache_key.as_deref() == Some(key)) else { return };
            r.ready = true;
            r.context == Context::Intro
        };
        if intro {
            let steps = vec![
                Step::MusicDuck { duck_level: 0.5, fade_duration_ms: 500.0 },
                Step::Delay { duration: 0.5 },
                Step::PlayCachedSpeech { cache_key: key.into(), wait_for_completion: true },
                Step::MusicUnduck { fade_duration_ms: 500.0 },
            ];
            self.submit_dj(Plan::new("foreground", steps));
        }
    }

    pub(crate) fn dj_speech_cache_failed(&self, key: &str, error: &str) {
        tracing::error!(key, error, "DJ commentary could not be cached");
        let mut d = self.inner.dj.lock().unwrap();
        d.requests.retain(|_, r| r.cache_key.as_deref() != Some(key));
    }

    pub(crate) fn dj_track_started(&self, title: &str) {
        *self.inner.now_playing.lock().unwrap() = Some(title.to_string());
        let dj = self.inner.dj.lock().unwrap().active;
        if let (true, Some(m)) = (dj, &self.inner.memory) {
            let _ = m.record_track(title, true);
        }
    }

    pub(crate) async fn dj_track_ending_soon(&self) {
        let (active, next, ready) = {
            let d = self.inner.dj.lock().unwrap();
            (d.active, d.next.clone(), d.next_ready_key().is_some())
        };
        if !active {
            return;
        }
        // The engine's retained state is the truth about what is playing.
        if let Some(t) = self.inner.bus.get::<MusicState>().track {
            self.inner.dj.lock().unwrap().current = Some(t.title);
        }
        match (next, ready) {
            (Some(_), true) => self.transition_plan(),
            (Some(n), false) => {
                tracing::warn!(next = n, "commentary not cached at track end; urgent caching");
                let current = self.inner.dj.lock().unwrap().current.clone().unwrap_or_default();
                if !self.inner.dj.lock().unwrap().next_requested() {
                    self.request_commentary(Context::Transition, &current, Some(&n));
                }
                tokio::time::sleep(self.inner.cfg.dj.urgent_grace).await;
                if self.inner.dj.lock().unwrap().active {
                    self.transition_plan();
                }
            }
            (None, _) => {
                tracing::warn!("no next track at track end; emergency pick");
                let pick = self.pick_track();
                self.inner.dj.lock().unwrap().next = pick;
                if self.inner.dj.lock().unwrap().next.is_some() {
                    self.crossfade_only_plan();
                } else {
                    self.transition_failure();
                }
            }
        }
    }

    /// Duck, start the cached line, crossfade under it after 3 s, wait for it, unduck - all on
    /// `ambient`, so the music never pauses. Falls back to a crossfade when no line is ready.
    fn transition_plan(&self) {
        let (next, key) = {
            let d = self.inner.dj.lock().unwrap();
            (d.next.clone(), d.next_ready_key())
        };
        let Some(next) = next else { return self.transition_failure() };
        let xf = Step::MusicCrossfade { next_track_id: next.clone(), crossfade_duration: self.inner.cfg.dj.crossfade_s };
        let steps = match &key {
            Some((_, k)) => vec![
                Step::MusicDuck { duck_level: 0.5, fade_duration_ms: 500.0 },
                Step::Delay { duration: 0.5 },
                Step::PlayCachedSpeech { cache_key: k.clone(), wait_for_completion: false },
                Step::Delay { duration: 3.0 },
                xf,
                Step::WaitForSpeechEnd { cache_key: k.clone() },
                Step::MusicUnduck { fade_duration_ms: 500.0 },
            ],
            None => vec![xf],
        };
        self.submit_dj(Plan::new("ambient", steps));
        self.advance(&next, key.map(|(id, _)| id));
    }

    /// Crossfade-only fallback (no time or no line for commentary).
    fn crossfade_only_plan(&self) {
        let Some(next) = self.inner.dj.lock().unwrap().next.clone() else { return };
        let xf = Step::MusicCrossfade { next_track_id: next.clone(), crossfade_duration: self.inner.cfg.dj.crossfade_s };
        self.submit_dj(Plan::new("ambient", vec![xf]));
        self.advance(&next, None);
    }

    /// The next track becomes current; start caching the one after it right away.
    fn advance(&self, next: &str, used_request: Option<String>) {
        {
            let mut d = self.inner.dj.lock().unwrap();
            d.current = Some(next.into());
            d.next = None;
            if let Some(id) = used_request {
                d.requests.remove(&id);
            }
            d.requests.retain(|_, r| r.context == Context::Intro && !r.ready);
        }
        self.remember(next);
        self.sync_retained();
        self.dj_cache_tick();
    }

    /// Nothing to go to: repeat the current track, else stop.
    fn transition_failure(&self) {
        let current = self.inner.dj.lock().unwrap().current.clone();
        match current {
            Some(c) => self.submit_dj(Plan::new("ambient", vec![Step::MusicCrossfade { next_track_id: c, crossfade_duration: 2.0 }])),
            None => {
                self.inner.bus.publish(Source::Timeline, None, Event::Music(MusicEvent::Stop));
                let mut d = self.inner.dj.lock().unwrap();
                d.current = None;
                d.next = None;
            }
        }
    }

    /// A DJ plan failed: emergency pick if needed, then a quick crossfade.
    pub(crate) async fn dj_plan_failed(&self, plan_id: &str) {
        let ours = {
            let mut d = self.inner.dj.lock().unwrap();
            let ours = d.active && d.plans.iter().any(|p| p == plan_id);
            d.plans.retain(|p| p != plan_id);
            ours
        };
        if !ours {
            return;
        }
        tracing::warn!(plan_id, "DJ plan failed; recovering with a quick crossfade");
        if self.inner.dj.lock().unwrap().next.is_none() {
            let pick = self.pick_track();
            self.inner.dj.lock().unwrap().next = pick;
        }
        let Some(next) = self.inner.dj.lock().unwrap().next.clone() else { return self.transition_failure() };
        let id = uuid::Uuid::new_v4().to_string();
        self.inner.bus.publish(Source::Timeline, None, Event::Music(MusicEvent::Crossfade { track: next.clone(), duration_s: 3.0, id }));
        self.advance(&next, None);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn prompts_match_cantina_fixture_keys() {
        use r3x_llm::{ClaudeFixtures, Message};
        let key = |p: String| {
            let req = MessagesRequest::new(150).system("persona", false).messages(vec![Message::user(p)]);
            ClaudeFixtures::key_for("create", &req)
        };
        let (bai, alo, dosh) = (track("Bai Tee Tee"), track("Aloogahoo"), track("Doshka"));
        assert_eq!(key(commentary_prompt(Context::Intro, &bai, None)), "9a1d7b88c491551a");
        assert_eq!(key(commentary_prompt(Context::Transition, &bai, Some(&alo))), "9c24d85b133dd924");
        assert_eq!(key(commentary_prompt(Context::Intro, &dosh, None)), "908cf8b31d87f450");
        let spot = Track { title: "Never Grow Up".into(), artist: Some("Taylor Swift".into()), path: Some("spotify:x".into()), ..Default::default() };
        assert!(commentary_prompt(Context::Intro, &spot, None).contains("by Taylor Swift"));
    }
}

//! The plan executor (the plan half of `timeline_executor_service.py`).
//!
//! Layers `ambient` 0, `foreground` 1, `show` 1, `override` 2. A new plan on a layer replaces
//! the one running there; `override` cancels lower layers; `foreground` pauses lower layers
//! for as long as it runs (the pause is derived from running plans, so it always lifts).
//! Intended differences from CantinaOS: a paused layer holds *between steps*, not only before
//! its first; `wait_for_event` and `wait_for_speech_end` are implemented (CantinaOS failed
//! the plan on both, which sent every DJ transition down its failure-recovery path); a step's
//! `duration` is never slept a second time after the step.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use r3x_bus::{Bus, Received};
use r3x_contracts::{
    Ack, Body, Command, ConversationEvent, Domain, EndReason, Envelope, Event, MusicEvent, OpsEvent, PerfCommand, PerfEvent, Source,
};
use r3x_performer_core::show::types::Kind;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tokio::sync::watch;
use tokio::task::JoinHandle;
use tokio::time::Instant;

pub const LAYERS: &[(&str, u8)] = &[("ambient", 0), ("foreground", 1), ("show", 1), ("override", 2)];
/// Layers whose plans pause every lower layer while they run.
const PAUSING: &[&str] = &["foreground"];
pub const DUCK_LEVEL: f64 = 0.5;
pub const DUCK_FADE_MS: f64 = 500.0;
/// Speech budget: synthesis + playback is ~linear in characters (measured ~61 ms/char).
pub const SPEECH_WAIT_BASE_S: f64 = 8.0;
pub const SPEECH_WAIT_PER_CHAR_S: f64 = 0.060;
pub const SPEECH_WAIT_MAX_S: f64 = 180.0;
pub const CACHED_SPEECH_WAIT_S: f64 = 25.0;
pub const SHOW_WAIT_S: f64 = 60.0;

fn priority(layer: &str) -> Option<u8> {
    LAYERS.iter().find(|(l, _)| *l == layer).map(|(_, p)| *p)
}

fn yes() -> bool {
    true
}
fn duck_default() -> f64 {
    0.3
}
fn fade_default() -> f64 {
    1500.0
}
fn xfade_default() -> f64 {
    3.0
}
fn ten() -> f64 {
    10.0
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "step_type", rename_all = "snake_case")]
pub enum Step {
    /// A spoken line. `reply` lines use the turn id as their speech id.
    Speak {
        text: String,
        #[serde(default)]
        id: Option<String>,
        #[serde(default)]
        reply: bool,
    },
    PlayCachedSpeech {
        cache_key: String,
        #[serde(default = "yes")]
        wait_for_completion: bool,
    },
    MusicCrossfade {
        next_track_id: String,
        #[serde(default = "xfade_default")]
        crossfade_duration: f64,
    },
    MusicDuck {
        #[serde(default = "duck_default")]
        duck_level: f64,
        #[serde(default = "fade_default")]
        fade_duration_ms: f64,
    },
    MusicUnduck {
        #[serde(default = "fade_default")]
        fade_duration_ms: f64,
    },
    /// `genre: "stop"` stops; otherwise plays (the genre is the query).
    PlayMusic {
        #[serde(default)]
        genre: Option<String>,
    },
    EyePattern {
        #[serde(default)]
        pattern: Option<String>,
    },
    /// Placeholder in CantinaOS too; motion lives in shows.
    Move {
        #[serde(default)]
        motion: Option<String>,
    },
    ParallelSteps { steps: Vec<Step> },
    /// Show elements. Never fail a plan.
    Perform {
        id: String,
        #[serde(default)]
        params: Option<Value>,
        #[serde(default)]
        wait_for_completion: bool,
        #[serde(default)]
        optional: bool,
    },
    Sequence {
        id: String,
        #[serde(default)]
        params: Option<Value>,
        #[serde(default)]
        wait_for_completion: bool,
        #[serde(default)]
        optional: bool,
    },
    /// Wait for the next bus event with this topic (e.g. `music.track_started`).
    WaitForEvent {
        topic: String,
        #[serde(default = "ten")]
        timeout_s: f64,
    },
    Delay { duration: f64 },
    /// Wait until the cached line started by an earlier `play_cached_speech` has finished.
    WaitForSpeechEnd { cache_key: String },
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Plan {
    pub plan_id: String,
    pub layer: String,
    pub steps: Vec<Step>,
}

impl Plan {
    pub fn new(layer: &str, steps: Vec<Step>) -> Self {
        Self { plan_id: uuid::Uuid::new_v4().to_string(), layer: layer.into(), steps }
    }
}

struct Running {
    plan_id: String,
    task: JoinHandle<()>,
}

struct Inner {
    bus: Bus,
    kinds: HashMap<String, Kind>,
    running: Mutex<HashMap<String, Running>>,
    paused: HashMap<&'static str, watch::Sender<bool>>,
    /// cache_key -> finished flag of its latest playback.
    cached: Mutex<HashMap<String, watch::Receiver<bool>>>,
    music_playing: watch::Receiver<bool>,
}

/// Cheap to clone.
#[derive(Clone)]
pub struct Executor(Arc<Inner>);

impl Executor {
    /// `kinds`: show item id -> kind, for validating `perform`/`sequence` steps.
    pub fn new(bus: Bus, kinds: HashMap<String, Kind>) -> Self {
        let music_playing = watch_music(&bus);
        let paused = LAYERS.iter().map(|(l, _)| (*l, watch::Sender::new(false))).collect();
        Self(Arc::new(Inner { bus, kinds, running: Mutex::default(), paused, cached: Mutex::default(), music_playing }))
    }

    pub fn music_playing(&self) -> bool {
        *self.0.music_playing.borrow()
    }

    /// Start `plan` on its layer. Err for an unknown layer.
    pub fn submit(&self, plan: Plan) -> Result<(), String> {
        let prio = priority(&plan.layer).ok_or_else(|| format!("unknown layer {}", plan.layer))?;
        let layer: &'static str = LAYERS.iter().find(|(l, _)| *l == plan.layer).map(|(l, _)| *l).unwrap();
        let mut running = self.0.running.lock().unwrap();
        if let Some(old) = running.remove(layer) {
            if !old.task.is_finished() {
                old.task.abort();
                self.ended(&old.plan_id, layer, "cancelled");
            }
        }
        if layer == "override" {
            for (l, p) in LAYERS {
                if *p < prio {
                    if let Some(old) = running.remove(*l).filter(|r| !r.task.is_finished()) {
                        old.task.abort();
                        self.ended(&old.plan_id, l, "cancelled");
                    }
                }
            }
        } else if PAUSING.contains(&layer) {
            for (l, p) in LAYERS {
                if *p < prio && !self.0.paused[l].send_replace(true) {
                    if let Some(r) = running.get(*l).filter(|r| !r.task.is_finished()) {
                        self.ended(&r.plan_id, l, "paused");
                    }
                }
            }
        }
        let me = self.clone();
        let plan_id = plan.plan_id.clone();
        self.0.bus.publish(Source::Timeline, None, Event::Ops(OpsEvent::PlanStarted { plan_id: plan_id.clone(), layer: layer.into() }));
        let task = tokio::spawn(async move {
            let status = me.run(&plan, layer).await;
            me.ended(&plan.plan_id, layer, status);
            // Lift the pause this plan put on lower layers, unless another pausing plan runs.
            let mut running = me.0.running.lock().unwrap();
            if running.get(layer).is_some_and(|r| r.plan_id == plan.plan_id) {
                running.remove(layer);
            }
            me.refresh_pauses(&running);
        });
        running.insert(layer.into(), Running { plan_id, task });
        Ok(())
    }

    /// Cancel every plan (DJ mode off).
    pub fn cancel_all(&self) {
        let mut running = self.0.running.lock().unwrap();
        for (layer, r) in running.drain() {
            if !r.task.is_finished() {
                r.task.abort();
                self.ended(&r.plan_id, &layer, "cancelled");
            }
        }
        self.refresh_pauses(&running);
    }

    /// Plan ids currently running, by layer.
    pub fn running(&self) -> HashMap<String, String> {
        self.0.running.lock().unwrap().iter().filter(|(_, r)| !r.task.is_finished()).map(|(l, r)| (l.clone(), r.plan_id.clone())).collect()
    }

    fn refresh_pauses(&self, running: &HashMap<String, Running>) {
        let holding = PAUSING
            .iter()
            .filter(|l| running.get(**l).is_some_and(|r| !r.task.is_finished()))
            .filter_map(|l| priority(l))
            .max();
        for (l, p) in LAYERS {
            if holding.is_none_or(|h| *p >= h) {
                self.0.paused[l].send_replace(false);
            }
        }
    }

    fn ended(&self, plan_id: &str, layer: &str, status: &str) {
        self.0.bus.publish(
            Source::Timeline,
            None,
            Event::Ops(OpsEvent::PlanEnded { plan_id: plan_id.into(), layer: layer.into(), status: status.into() }),
        );
    }

    async fn run(&self, plan: &Plan, layer: &'static str) -> &'static str {
        for step in &plan.steps {
            let mut paused = self.0.paused[layer].subscribe();
            let _ = paused.wait_for(|p| !*p).await;
            let (ok, detail) = self.step(step, &plan.plan_id).await;
            if !ok {
                tracing::warn!(plan = plan.plan_id, ?step, %detail, "plan step failed");
                return "failed";
            }
        }
        "completed"
    }

    fn publish(&self, conversation_id: Option<String>, e: Event) {
        self.0.bus.publish(Source::Timeline, conversation_id, e);
    }

    /// `(success, details)`.
    pub fn step<'a>(&'a self, step: &'a Step, plan_id: &'a str) -> StepFuture<'a> {
        Box::pin(async move {
            let bus = &self.0.bus;
            match step {
                Step::Speak { text, id, reply } => self.speak(text, id.clone(), *reply, plan_id).await,
                Step::PlayCachedSpeech { cache_key, wait_for_completion } => {
                    let playback_id = uuid::Uuid::new_v4().to_string();
                    let mut rx = bus.subscribe(Domain::Conversation);
                    let (done_tx, done_rx) = watch::channel(false);
                    self.0.cached.lock().unwrap().insert(cache_key.clone(), done_rx);
                    self.publish(None, Event::Conversation(ConversationEvent::PlayCached { key: cache_key.clone(), playback_id: playback_id.clone() }));
                    let pid = playback_id.clone();
                    let waiter = tokio::spawn(async move {
                        let ok = wait_for(&mut rx, Duration::from_secs_f64(CACHED_SPEECH_WAIT_S), |_, e| {
                            matches!(e, Event::Conversation(ConversationEvent::CachedPlaybackEnded { playback_id, .. }) if *playback_id == pid)
                        })
                        .await
                        .is_some();
                        let _ = done_tx.send(true);
                        ok
                    });
                    if !wait_for_completion {
                        return (true, serde_json::json!({"cache_key": cache_key, "playback_id": playback_id, "status": "started"}));
                    }
                    let ok = waiter.await.unwrap_or(false);
                    (ok, serde_json::json!({"cache_key": cache_key, "playback_id": playback_id, "status": if ok { "completed" } else { "timeout" }}))
                }
                Step::WaitForSpeechEnd { cache_key } => {
                    let rx = self.0.cached.lock().unwrap().get(cache_key).cloned();
                    if let Some(mut rx) = rx {
                        let _ = tokio::time::timeout(Duration::from_secs_f64(CACHED_SPEECH_WAIT_S), rx.wait_for(|d| *d)).await;
                    }
                    (true, serde_json::json!({"cache_key": cache_key}))
                }
                Step::MusicCrossfade { next_track_id, crossfade_duration } => {
                    let id = uuid::Uuid::new_v4().to_string();
                    let mut rx = bus.subscribe(Domain::Music);
                    self.publish(None, Event::Music(MusicEvent::Crossfade { track: next_track_id.clone(), duration_s: *crossfade_duration, id: id.clone() }));
                    let limit = (crossfade_duration + 10.0).max(20.0);
                    let done = wait_for(&mut rx, Duration::from_secs_f64(limit), |_, e| {
                        matches!(e, Event::Music(MusicEvent::CrossfadeComplete { id: i }) if *i == id)
                    })
                    .await
                    .is_some();
                    // A lost completion never wedges the plan.
                    (true, serde_json::json!({"next_track_id": next_track_id, "status": if done { "completed" } else { "timeout" }}))
                }
                Step::MusicDuck { duck_level, fade_duration_ms } => {
                    self.publish(None, Event::Music(MusicEvent::Duck { level: *duck_level, fade_ms: *fade_duration_ms }));
                    tokio::time::sleep(Duration::from_secs_f64(fade_duration_ms / 1000.0)).await;
                    (true, serde_json::json!({"duck_level": duck_level}))
                }
                Step::MusicUnduck { fade_duration_ms } => {
                    self.publish(None, Event::Music(MusicEvent::Unduck { fade_ms: *fade_duration_ms }));
                    tokio::time::sleep(Duration::from_secs_f64(fade_duration_ms / 1000.0)).await;
                    (true, Value::Null)
                }
                Step::PlayMusic { genre } => {
                    let e = match genre.as_deref() {
                        Some("stop") => MusicEvent::Stop,
                        g => MusicEvent::Play { query: g.map(str::to_owned) },
                    };
                    self.publish(None, Event::Music(e));
                    (true, serde_json::json!({"genre": genre}))
                }
                Step::EyePattern { pattern } => {
                    let pattern = pattern.clone().unwrap_or_else(|| "idle".into());
                    let ack = bus.command(Source::Timeline, None, Command::Perf(PerfCommand::Eyes { pattern: pattern.clone(), duration: None })).await;
                    (true, serde_json::json!({"pattern": pattern, "accepted": ack.is_accepted()}))
                }
                Step::Move { motion } => (true, serde_json::json!({"motion": motion})),
                Step::ParallelSteps { steps } => {
                    let results = futures_join(steps.iter().map(|s| self.step(s, plan_id)).collect()).await;
                    let ok = results.iter().all(|(ok, _)| *ok);
                    (ok, serde_json::json!({"successful_steps": results.iter().filter(|r| r.0).count(), "total_steps": steps.len()}))
                }
                Step::Perform { id, params, wait_for_completion, .. } => self.show(id, params, *wait_for_completion, false).await,
                Step::Sequence { id, params, wait_for_completion, .. } => self.show(id, params, *wait_for_completion, true).await,
                Step::WaitForEvent { topic, timeout_s } => {
                    let mut rx = bus.subscribe_all();
                    let got = wait_for(&mut rx, Duration::from_secs_f64(*timeout_s), |_, e| e.topic() == *topic).await;
                    (got.is_some(), serde_json::json!({"topic": topic}))
                }
                Step::Delay { duration } => {
                    tokio::time::sleep(Duration::from_secs_f64(duration.max(0.0))).await;
                    (true, Value::Null)
                }
            }
        })
    }

    /// Duck (if music plays) -> say -> wait for that line's end (bounded) -> unduck.
    async fn speak(&self, text: &str, id: Option<String>, reply: bool, plan_id: &str) -> (bool, Value) {
        let text = text.trim();
        if text.is_empty() {
            return (false, serde_json::json!({"error": "Missing text field"}));
        }
        let speech_id = id.unwrap_or_else(|| format!("clip-{}", uuid::Uuid::new_v4()));
        let ducked = self.music_playing();
        if ducked {
            self.publish(None, Event::Music(MusicEvent::Duck { level: DUCK_LEVEL, fade_ms: DUCK_FADE_MS }));
            tokio::time::sleep(Duration::from_millis(150)).await;
        }
        let mut rx = self.0.bus.subscribe(Domain::Conversation);
        let clip_id = (!reply).then(|| speech_id.clone());
        let e = ConversationEvent::Speak { text: text.into(), clip_id, plan_id: Some(plan_id.into()), reply };
        self.0.bus.publish(if reply { Source::Claude } else { Source::Timeline }, Some(speech_id.clone()), Event::Conversation(e));
        let budget = (SPEECH_WAIT_BASE_S + SPEECH_WAIT_PER_CHAR_S * text.chars().count() as f64).min(SPEECH_WAIT_MAX_S);
        let done = wait_for(&mut rx, Duration::from_secs_f64(budget), |env, e| {
            matches!(e, Event::Conversation(ConversationEvent::SpeechEnded)) && env.conversation_id.as_deref() == Some(speech_id.as_str())
        })
        .await
        .is_some();
        if !done {
            tracing::warn!(speech_id, budget, "speech did not finish within its budget; continuing");
        }
        if ducked {
            self.publish(None, Event::Music(MusicEvent::Unduck { fade_ms: DUCK_FADE_MS }));
        }
        // CantinaOS forces progress on a timeout too.
        (true, serde_json::json!({"text": text, "speech_id": speech_id, "timeout_s": budget, "finished": done}))
    }

    async fn show(&self, id: &str, params: &Option<Value>, wait: bool, sequence: bool) -> (bool, Value) {
        match self.0.kinds.get(id) {
            None => return (true, serde_json::json!({"id": id, "skipped": true, "reason": "unknown show item"})),
            Some(k) if sequence && *k != Kind::Sequence => {
                return (true, serde_json::json!({"id": id, "skipped": true, "reason": format!("is a {}, not a sequence", k.as_str())}))
            }
            _ => {}
        }
        let num = |k: &str| params.as_ref().and_then(|p| p.get(k)).and_then(Value::as_f64).unwrap_or(1.0);
        let mut rx = self.0.bus.subscribe(Domain::Perf);
        let cmd = PerfCommand::Play { id: id.into(), intensity: num("intensity"), speed: num("speed"), layer: None };
        let ack = self.0.bus.command(Source::Timeline, None, Command::Perf(cmd)).await;
        if let Ack::Rejected { reason } = ack {
            return (true, serde_json::json!({"id": id, "skipped": true, "reason": reason}));
        }
        if !wait {
            return (true, serde_json::json!({"id": id, "status": "requested"}));
        }
        let end = wait_for(&mut rx, Duration::from_secs_f64(SHOW_WAIT_S), |_, e| {
            matches!(e, Event::Perf(PerfEvent::Ended { id: i, source: Source::Timeline, .. }) if i == id)
        })
        .await;
        let reason = match end.as_ref().map(|e| &e.body) {
            Some(Body::Event(Event::Perf(PerfEvent::Ended { reason, .. }))) => match reason {
                EndReason::Done => "done",
                EndReason::Interrupted => "interrupted",
                EndReason::Rejected => "rejected",
            },
            _ => "timeout",
        };
        (true, serde_json::json!({"id": id, "status": reason}))
    }
}

type StepFuture<'a> = std::pin::Pin<Box<dyn std::future::Future<Output = (bool, Value)> + Send + 'a>>;

/// Drive step futures concurrently (no `futures` dependency).
async fn futures_join(futs: Vec<StepFuture<'_>>) -> Vec<(bool, Value)> {
    let mut pending: Vec<_> = futs.into_iter().enumerate().collect();
    let mut out: Vec<(bool, Value)> = vec![(false, Value::Null); pending.len()];
    std::future::poll_fn(|cx| {
        pending.retain_mut(|(i, f)| match f.as_mut().poll(cx) {
            std::task::Poll::Ready(v) => {
                out[*i] = v;
                false
            }
            std::task::Poll::Pending => true,
        });
        if pending.is_empty() { std::task::Poll::Ready(()) } else { std::task::Poll::Pending }
    })
    .await;
    out
}

/// Wait (bounded) for the first event matching `pred`; `None` on timeout or bus gone.
pub async fn wait_for(
    rx: &mut r3x_bus::EventReceiver,
    timeout: Duration,
    mut pred: impl FnMut(&Envelope, &Event) -> bool,
) -> Option<Arc<Envelope>> {
    let deadline = Instant::now() + timeout;
    loop {
        match tokio::time::timeout_at(deadline, rx.recv()).await {
            Ok(Some(Received::Message(env))) => {
                if let Body::Event(e) = &env.body {
                    if pred(&env, e) {
                        return Some(env);
                    }
                }
            }
            Ok(Some(Received::Lagged { .. })) => continue,
            _ => return None,
        }
    }
}

/// Whether music plays, from the engine's own events (and `state.music` as a seed).
fn watch_music(bus: &Bus) -> watch::Receiver<bool> {
    let (tx, rx) = watch::channel(bus.get::<r3x_contracts::MusicState>().playing);
    let mut sub = bus.subscribe(Domain::Music);
    tokio::spawn(async move {
        while let Some(m) = sub.recv().await {
            let v = match m {
                Received::Message(env) => match &env.body {
                    Body::Event(Event::Music(MusicEvent::TrackStarted { .. })) => true,
                    Body::Event(Event::Music(MusicEvent::TrackStopped)) => false,
                    _ => continue,
                },
                Received::Lagged { state, .. } => state.music.playing,
            };
            if tx.send(v).is_err() {
                break;
            }
        }
    });
    rx
}

#[cfg(test)]
mod tests {
    use super::*;

    type Log = Arc<Mutex<Vec<(String, Option<String>, Event)>>>;

    fn collect(bus: &Bus) -> Log {
        let out = Arc::new(Mutex::new(Vec::new()));
        let o = out.clone();
        let mut rx = bus.subscribe_all();
        tokio::spawn(async move {
            while let Some(Received::Message(env)) = rx.recv().await {
                if let Body::Event(e) = &env.body {
                    o.lock().unwrap().push((e.topic(), env.conversation_id.clone(), e.clone()));
                }
            }
        });
        out
    }

    /// Answers `speak` after 1 s and cached playback after 2 s, like a voice would.
    fn fake_voice(bus: &Bus) {
        let mut rx = bus.subscribe(Domain::Conversation);
        let bus = bus.clone();
        tokio::spawn(async move {
            while let Some(Received::Message(env)) = rx.recv().await {
                let (bus, cid) = (bus.clone(), env.conversation_id.clone());
                match &env.body {
                    Body::Event(Event::Conversation(ConversationEvent::Speak { .. })) => {
                        tokio::spawn(async move {
                            bus.publish(Source::System, cid.clone(), Event::Conversation(ConversationEvent::SpeechStarted));
                            tokio::time::sleep(Duration::from_secs(1)).await;
                            bus.publish(Source::System, cid, Event::Conversation(ConversationEvent::SpeechEnded));
                        });
                    }
                    Body::Event(Event::Conversation(ConversationEvent::PlayCached { playback_id, .. })) => {
                        let playback_id = playback_id.clone();
                        tokio::spawn(async move {
                            tokio::time::sleep(Duration::from_secs(2)).await;
                            bus.publish(Source::System, None, Event::Conversation(ConversationEvent::CachedPlaybackEnded { playback_id, ok: true }));
                        });
                    }
                    _ => {}
                }
            }
        });
    }

    fn ends(log: &[(String, Option<String>, Event)]) -> Vec<(String, String)> {
        log.iter()
            .filter_map(|(_, _, e)| match e {
                Event::Ops(OpsEvent::PlanEnded { layer, status, .. }) => Some((layer.clone(), status.clone())),
                _ => None,
            })
            .collect()
    }

    #[tokio::test(start_paused = true)]
    async fn foreground_pauses_ambient_between_steps_and_resumes() {
        let bus = Bus::default();
        fake_voice(&bus);
        let log = collect(&bus);
        let ex = Executor::new(bus.clone(), HashMap::new());
        bus.publish(Source::System, None, Event::Music(MusicEvent::TrackStarted { track: Default::default() }));
        tokio::task::yield_now().await;
        ex.submit(Plan::new("ambient", vec![Step::Delay { duration: 0.5 }, Step::PlayMusic { genre: Some("stop".into()) }])).unwrap();
        tokio::time::sleep(Duration::from_millis(100)).await;
        ex.submit(Plan::new("foreground", vec![Step::Speak { text: "hello".into(), id: Some("turn-1".into()), reply: true }])).unwrap();
        tokio::time::sleep(Duration::from_millis(900)).await;
        assert!(!log.lock().unwrap().iter().any(|(t, ..)| t == "music.stop"), "ambient held while R3X speaks");
        tokio::time::sleep(Duration::from_secs(2)).await;
        let log = log.lock().unwrap();
        let topics: Vec<&str> = log.iter().map(|(t, ..)| t.as_str()).collect();
        let pos = |t: &str| topics.iter().position(|x| *x == t).unwrap();
        assert!(pos("music.duck") < pos("conversation.speak") && pos("conversation.speech_ended") < pos("music.unduck"));
        assert!(pos("music.unduck") < pos("music.stop"), "ambient resumed after the reply: {topics:?}");
        let speak = log.iter().find(|(t, ..)| t == "conversation.speak").unwrap();
        assert_eq!(speak.1.as_deref(), Some("turn-1"));
        assert_eq!(ends(&log), [("ambient".into(), "paused".into()), ("foreground".into(), "completed".into()), ("ambient".into(), "completed".into())]);
    }

    #[tokio::test(start_paused = true)]
    async fn replace_override_cancel_and_non_failing_show_steps() {
        let bus = Bus::default();
        fake_voice(&bus);
        let log = collect(&bus);
        let kinds = HashMap::from([("nod".to_string(), Kind::Clip)]);
        let ex = Executor::new(bus.clone(), kinds);
        // no performer on the bus: show steps are skipped, never fail
        let (ok, d) = ex.step(&Step::Sequence { id: "nod".into(), params: None, wait_for_completion: true, optional: true }, "p").await;
        assert!(ok && d["skipped"] == true);
        let (ok, _) = ex.step(&Step::Perform { id: "ghost".into(), params: None, wait_for_completion: false, optional: false }, "p").await;
        assert!(ok);
        let (ok, _) = ex.step(&Step::WaitForEvent { topic: "music.track_started".into(), timeout_s: 0.2 }, "p").await;
        assert!(!ok, "wait_for_event times out");

        ex.submit(Plan::new("ambient", vec![Step::Delay { duration: 5.0 }])).unwrap();
        ex.submit(Plan::new("ambient", vec![Step::Delay { duration: 5.0 }])).unwrap();
        ex.submit(Plan::new("show", vec![Step::Delay { duration: 5.0 }])).unwrap();
        ex.submit(Plan::new("override", vec![Step::PlayCachedSpeech { cache_key: "k".into(), wait_for_completion: false }, Step::WaitForSpeechEnd { cache_key: "k".into() }])).unwrap();
        assert!(ex.submit(Plan::new("bogus", vec![])).is_err());
        tokio::time::sleep(Duration::from_millis(1500)).await;
        assert_eq!(ex.running().keys().collect::<Vec<_>>(), ["override"], "wait_for_speech_end holds until playback ends");
        tokio::time::sleep(Duration::from_secs(1)).await;
        assert!(ex.running().is_empty());
        let e = ends(&log.lock().unwrap());
        assert_eq!(e.iter().filter(|(_, s)| s == "cancelled").count(), 3);
        assert_eq!(e.last().unwrap(), &("override".to_string(), "completed".to_string()));
        // a parsed CantinaOS step dict
        let s: Step = serde_json::from_value(serde_json::json!({"step_type": "music_duck", "duck_level": 0.5, "fade_duration_ms": 500})).unwrap();
        assert_eq!(s, Step::MusicDuck { duck_level: 0.5, fade_duration_ms: 500.0 });
    }
}

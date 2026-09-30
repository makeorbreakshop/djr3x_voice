//! Dedup rendezvous between the fast router and the Claude turn (`core/fast_router_gate.py`).
//!
//! The Claude side awaits the router's verdict (bounded, 1.2 s), then - if something fired -
//! the execution side's *outcome* (bounded, 1.2 s: "which track actually started"), then
//! consumes the record and builds `<action_already_taken>` with `tool_choice: none`.
//! Fail-open throughout: no router registered -> no wait; timeout -> `None`.
//!
//! Keyed by any per-turn string. The turn's `conversation_id` is the natural key in the Rust
//! brain (CantinaOS keyed on transcript text).
//!
//! Difference from Python (intended): a *decline* verdict is stored too, so a Claude side that
//! arrives after the router answered "nothing" returns at once instead of waiting 1.2 s.

use serde_json::{Map, Value};
use std::collections::HashMap;
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tokio::sync::Notify;

use crate::decide::RouterDecision;

/// How long a verdict stays valid: covers Claude's round trip and verbal follow-up, never
/// leaks into a later turn.
pub const RECORD_TTL: Duration = Duration::from_secs(30);

#[derive(Debug, Clone, PartialEq)]
pub struct ActionTaken {
    pub intent_name: String,
    /// What was asked for, amended by [`FastRouterGate::resolve_outcome`] with what happened.
    pub parameters: Map<String, Value>,
    pub confidence: f64,
    pub transcript: String,
    pub at: Instant,
    pub outcome_known: bool,
}

impl ActionTaken {
    /// `None` when the decision did not fire.
    pub fn from_decision(d: &RouterDecision, transcript: &str) -> Option<Self> {
        Some(Self {
            intent_name: d.intent.clone()?,
            parameters: d.parameters.clone(),
            confidence: d.confidence,
            transcript: transcript.into(),
            at: Instant::now(),
            outcome_known: false,
        })
    }

    /// The `<action_already_taken>` block to prepend to the user message.
    pub fn context_block(&self) -> String {
        r3x_llm::prompt::action_already_taken(&self.intent_name, &self.parameters)
    }
}

#[derive(Default)]
struct Inner {
    registered: bool,
    /// key -> (verdict, when). `None` verdict = router declined.
    verdicts: HashMap<String, (Option<ActionTaken>, Instant)>,
}

#[derive(Default)]
pub struct FastRouterGate {
    inner: Mutex<Inner>,
    changed: Notify,
}

impl FastRouterGate {
    pub fn new() -> Self {
        Self::default()
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, Inner> {
        self.inner.lock().unwrap_or_else(|p| p.into_inner())
    }

    /// Router side, once it can actually produce verdicts. Until then nobody waits.
    pub fn register_router(&self) {
        self.lock().registered = true;
        tracing::info!("Fast router registered with gate; Claude path will await verdicts");
    }

    /// Releases every waiter (verdict waits return `None`, outcome waits the unamended record).
    pub fn unregister_router(&self) {
        self.lock().registered = false;
        self.changed.notify_waiters();
    }

    pub fn enabled(&self) -> bool {
        self.lock().registered
    }

    /// Publish the verdict for `key`; safe before anyone waits. Record *before* announcing the
    /// action on the bus so a waiter can never wake to a missing record.
    pub fn resolve(&self, key: &str, action: Option<ActionTaken>) {
        {
            let mut g = self.lock();
            let now = Instant::now();
            g.verdicts.retain(|_, (_, at)| now.duration_since(*at) < RECORD_TTL);
            g.verdicts.insert(key.into(), (action, now));
        }
        self.changed.notify_waiters();
    }

    /// What the dispatched action actually did (merged into its parameters). No-op without a
    /// record, so always safe to call.
    pub fn resolve_outcome(&self, key: &str, parameters: Map<String, Value>) {
        {
            let mut g = self.lock();
            let Some((Some(rec), _)) = g.verdicts.get_mut(key) else { return };
            rec.parameters.extend(parameters);
            rec.outcome_known = true;
        }
        self.changed.notify_waiters();
    }

    async fn wait_until<T>(&self, timeout: Duration, mut check: impl FnMut(&Inner) -> Option<T>) -> Result<T, ()> {
        let deadline = tokio::time::Instant::now() + timeout;
        loop {
            let notified = self.changed.notified();
            tokio::pin!(notified);
            notified.as_mut().enable();
            if let Some(v) = check(&self.lock()) {
                return Ok(v);
            }
            if tokio::time::timeout_at(deadline, notified).await.is_err() {
                return Err(());
            }
        }
    }

    /// Up to `timeout` for the verdict. `None`: nothing fired, no router, or timed out.
    pub async fn wait_for_verdict(&self, key: &str, timeout: Duration) -> Option<ActionTaken> {
        let r = self
            .wait_until(timeout, |g| {
                if !g.registered {
                    return Some(None);
                }
                g.verdicts.get(key).map(|(a, _)| a.clone())
            })
            .await;
        r.unwrap_or_else(|_| {
            tracing::warn!("Fast router verdict timed out after {:.2}s; proceeding on the Claude path", timeout.as_secs_f64());
            None
        })
    }

    /// Up to `timeout` for the outcome of a dispatched action. Returns the record (amended, or
    /// as dispatched on timeout); `None` only when there is no record.
    pub async fn wait_for_outcome(&self, key: &str, timeout: Duration) -> Option<ActionTaken> {
        let rec = |g: &Inner| g.verdicts.get(key).and_then(|(a, _)| a.clone());
        let r = self
            .wait_until(timeout, |g| match rec(g) {
                None => Some(None),
                Some(a) if a.outcome_known || !g.registered => Some(Some(a)),
                Some(_) => None,
            })
            .await;
        r.unwrap_or_else(|_| {
            tracing::info!("Action outcome not reported within {:.2}s; confirming the request rather than the result", timeout.as_secs_f64());
            rec(&self.lock())
        })
    }

    /// Take and remove the record so a retry of the same turn cannot double-suppress.
    pub fn consume(&self, key: &str) -> Option<ActionTaken> {
        self.lock().verdicts.remove(key).and_then(|(a, _)| a)
    }

    pub fn peek(&self, key: &str) -> Option<ActionTaken> {
        self.lock().verdicts.get(key).and_then(|(a, _)| a.clone())
    }

    /// The whole Claude-side sequence: verdict -> outcome -> consume. `Some` means the router
    /// already acted: prepend `context_block()` and send `tool_choice: none`.
    pub async fn await_router(&self, key: &str, verdict_wait: Duration, outcome_wait: Duration) -> Option<ActionTaken> {
        if !self.enabled() {
            return None;
        }
        let action = self.wait_for_verdict(key, verdict_wait).await;
        if action.is_none() {
            // drop the decline record; nothing else will
            self.consume(key);
            return None;
        }
        let action = self.wait_for_outcome(key, outcome_wait).await.or(action);
        self.consume(key);
        if let Some(a) = &action {
            tracing::info!("Fast router already executed '{}' (confidence {:.2}); suppressing tools for this turn", a.intent_name, a.confidence);
        }
        action
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::sync::Arc;

    fn act(name: &str) -> ActionTaken {
        ActionTaken {
            intent_name: name.into(),
            parameters: json!({"track": null}).as_object().unwrap().clone(),
            confidence: 0.97,
            transcript: "t".into(),
            at: Instant::now(),
            outcome_known: false,
        }
    }
    const W: Duration = Duration::from_millis(1200);

    #[tokio::test(start_paused = true)]
    async fn no_router_means_no_wait() {
        let g = FastRouterGate::new();
        let t0 = tokio::time::Instant::now();
        assert!(g.await_router("k", W, W).await.is_none());
        assert_eq!(t0.elapsed(), Duration::ZERO);
    }

    #[tokio::test(start_paused = true)]
    async fn verdict_then_outcome_then_consume() {
        let g = Arc::new(FastRouterGate::new());
        g.register_router();
        let g2 = g.clone();
        tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(190)).await;
            g2.resolve("k", Some(act("play_music")));
            tokio::time::sleep(Duration::from_millis(300)).await;
            g2.resolve_outcome("k", json!({"track": "Doshka"}).as_object().unwrap().clone());
        });
        let t0 = tokio::time::Instant::now();
        let a = g.await_router("k", W, W).await.unwrap();
        assert_eq!(t0.elapsed(), Duration::from_millis(490));
        assert!(a.outcome_known);
        assert_eq!(a.parameters["track"], "Doshka");
        assert!(g.peek("k").is_none(), "consumed");
    }

    #[tokio::test(start_paused = true)]
    async fn bounded_waits_fail_open() {
        let g = FastRouterGate::new();
        g.register_router();
        let t0 = tokio::time::Instant::now();
        assert!(g.wait_for_verdict("k", W).await.is_none());
        assert_eq!(t0.elapsed(), W);
        // action but no outcome: the unamended record after the outcome wait
        g.resolve("k", Some(act("stop_music")));
        let t0 = tokio::time::Instant::now();
        let a = g.await_router("k", W, W).await.unwrap();
        assert!(!a.outcome_known);
        assert_eq!(t0.elapsed(), W);
        // an early decline answers immediately
        g.resolve("d", None);
        let t0 = tokio::time::Instant::now();
        assert!(g.await_router("d", W, W).await.is_none());
        assert_eq!(t0.elapsed(), Duration::ZERO);
        assert!(g.resolve_outcome_is_noop_without_record());
    }

    impl FastRouterGate {
        fn resolve_outcome_is_noop_without_record(&self) -> bool {
            self.resolve_outcome("nope", Map::new());
            self.peek("nope").is_none()
        }
    }

    #[tokio::test(start_paused = true)]
    async fn unregister_releases_waiters() {
        let g = Arc::new(FastRouterGate::new());
        g.register_router();
        let g2 = g.clone();
        let h = tokio::spawn(async move { g2.wait_for_verdict("k", W).await });
        tokio::time::sleep(Duration::from_millis(10)).await;
        g.unregister_router();
        assert!(h.await.unwrap().is_none());
    }
}

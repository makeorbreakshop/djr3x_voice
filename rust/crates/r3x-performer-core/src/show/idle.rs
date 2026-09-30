//! Weighted idle policy (`show/idle.json`, port of `idle.ts`), after Reachy Mini's: when
//! nothing has happened for `after_s`, pick a weighted choice and perform it with source
//! "idle" (so only free-tier items pass). `while_music` replaces the list while music or DJ
//! mode is on. Any interaction cancels the idle item and restarts the timer; a looping idle
//! item is also retired after `max_loop_s`.

use super::types::{IdlePolicy, WeightedId};
use crate::rng::Rng;

#[derive(Clone, Copy, Debug)]
pub struct IdleContext {
    /// Nothing else is happening: no conversation, no gesture or show running.
    pub eligible: bool,
    pub music: bool,
}

/// What the runner wants done this update.
#[derive(Clone, Debug, PartialEq)]
pub enum IdleAction {
    /// Stop this run (it was ours).
    Stop(String),
    /// Perform this id with source "idle"; report the run id back with [`IdleRunner::started`].
    Perform(String),
}

#[derive(Clone, Debug)]
pub struct IdleRunner {
    pub enabled: bool,
    pub policy: Option<IdlePolicy>,
    pub max_loop_s: f64,
    quiet_since: f64,
    run_id: Option<String>,
    run_music: bool,
    run_started: f64,
}

impl IdleRunner {
    pub fn new(policy: Option<IdlePolicy>, now: f64) -> Self {
        IdleRunner {
            enabled: true,
            policy,
            max_loop_s: 40.0,
            quiet_since: now,
            run_id: None,
            run_music: false,
            run_started: 0.0,
        }
    }

    /// Something happened: cancel what idle is doing and restart the clock. Returns the run to stop.
    pub fn poke(&mut self, now: f64) -> Option<String> {
        self.quiet_since = now;
        self.run_id.take()
    }

    /// The idle item finished (or was interrupted by someone else).
    pub fn ended(&mut self, run_id: &str, now: f64) {
        if self.run_id.as_deref() == Some(run_id) {
            self.run_id = None;
            self.quiet_since = now;
        }
    }

    pub fn current(&self) -> Option<&str> {
        self.run_id.as_deref()
    }

    /// Call after performing what [`IdleAction::Perform`] asked for (None = rejected).
    pub fn started(&mut self, run_id: Option<String>) {
        self.run_id = run_id;
    }

    pub fn update(&mut self, now: f64, ctx: IdleContext, rng: &mut Rng) -> Option<IdleAction> {
        let Some(policy) = self.policy.as_ref().filter(|_| self.enabled) else {
            return self
                .run_id
                .is_some()
                .then(|| self.poke(now))
                .flatten()
                .map(IdleAction::Stop);
        };
        if self.run_id.is_some() {
            if ctx.music != self.run_music || now - self.run_started > self.max_loop_s {
                return self.poke(now).map(IdleAction::Stop);
            }
            return None;
        }
        if !ctx.eligible {
            self.quiet_since = now;
            return None;
        }
        if now - self.quiet_since < policy.after_s {
            return None;
        }
        let list = match &policy.while_music {
            Some(m) if ctx.music && !m.is_empty() => m,
            _ => &policy.choices,
        };
        let id = pick(list, rng);
        self.quiet_since = now;
        let id = id?;
        self.run_music = ctx.music;
        self.run_started = now;
        Some(IdleAction::Perform(id))
    }
}

pub fn pick(list: &[WeightedId], rng: &mut Rng) -> Option<String> {
    let total: f64 = list.iter().map(|c| c.weight).sum();
    let mut r = rng.next_f64() * total;
    for c in list {
        r -= c.weight;
        if r < 0.0 {
            return Some(c.id.clone());
        }
    }
    list.last().map(|c| c.id.clone())
}

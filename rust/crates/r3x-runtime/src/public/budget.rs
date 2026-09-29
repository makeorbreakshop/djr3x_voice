//! Spend caps and rate limits for the public server. All in memory: a restart forgets them
//! (the daily cap is a safety net, not accounting - the provider dashboards are that).

use std::collections::HashMap;
use std::time::{Duration, Instant};

/// Caps and rates, from env (see [`Limits::from_env`]). Conservative by default: who pays
/// for public traffic is still an open question (plan §12 Q3).
#[derive(Debug, Clone)]
pub struct Limits {
    /// Cost-weighted Claude tokens per visitor token (input + cache writes + output + 1/10 of
    /// cache reads).
    pub visitor_llm_tokens: u64,
    /// ElevenLabs characters per visitor token.
    pub visitor_tts_chars: u64,
    /// The same, for every visitor together, per UTC day.
    pub daily_llm_tokens: u64,
    pub daily_tts_chars: u64,
    /// Turns (typed or push-to-talk) per visitor per minute.
    pub turns_per_min: u32,
    /// Any command per visitor per minute.
    pub commands_per_min: u32,
    /// New connections (and token mints) per IP per minute.
    pub connects_per_min: u32,
    pub sessions_per_ip: usize,
    pub max_sessions: usize,
    /// Longest hold-to-talk before the server lets go (Deepgram bills streamed audio).
    pub max_ptt: Duration,
    /// A session with no command this long is closed.
    pub idle: Duration,
}

impl Default for Limits {
    fn default() -> Self {
        Self {
            visitor_llm_tokens: 30_000,
            visitor_tts_chars: 1_500,
            daily_llm_tokens: 600_000,
            daily_tts_chars: 20_000,
            turns_per_min: 6,
            commands_per_min: 120,
            connects_per_min: 10,
            sessions_per_ip: 2,
            max_sessions: 8,
            max_ptt: Duration::from_secs(15),
            idle: Duration::from_secs(300),
        }
    }
}

impl Limits {
    /// `R3X_PUBLIC_{VISITOR_LLM_TOKENS, VISITOR_TTS_CHARS, DAILY_LLM_TOKENS, DAILY_TTS_CHARS,
    /// TURNS_PER_MIN, COMMANDS_PER_MIN, CONNECTS_PER_MIN, SESSIONS_PER_IP, MAX_SESSIONS,
    /// MAX_PTT_S, IDLE_S}`.
    pub fn from_env() -> Self {
        let d = Self::default();
        fn get<T: std::str::FromStr>(k: &str, d: T) -> T {
            std::env::var(format!("R3X_PUBLIC_{k}")).ok().and_then(|v| v.trim().parse().ok()).unwrap_or(d)
        }
        let secs = |k: &str, d: Duration| Duration::from_secs_f64(get(k, d.as_secs_f64()).max(0.0));
        Self {
            visitor_llm_tokens: get("VISITOR_LLM_TOKENS", d.visitor_llm_tokens),
            visitor_tts_chars: get("VISITOR_TTS_CHARS", d.visitor_tts_chars),
            daily_llm_tokens: get("DAILY_LLM_TOKENS", d.daily_llm_tokens),
            daily_tts_chars: get("DAILY_TTS_CHARS", d.daily_tts_chars),
            turns_per_min: get("TURNS_PER_MIN", d.turns_per_min),
            commands_per_min: get("COMMANDS_PER_MIN", d.commands_per_min),
            connects_per_min: get("CONNECTS_PER_MIN", d.connects_per_min),
            sessions_per_ip: get("SESSIONS_PER_IP", d.sessions_per_ip),
            max_sessions: get("MAX_SESSIONS", d.max_sessions),
            max_ptt: secs("MAX_PTT_S", d.max_ptt),
            idle: secs("IDLE_S", d.idle),
        }
    }
}

/// Said instead of a turn once a cap is reached (shown as R3X's reply; never synthesised).
pub const VISITOR_SPENT: &str =
    "Whoa, my vocabulator needs a cooldown - you've used up this visit's chatter. Come back later and we'll talk again!";
pub const DAILY_SPENT: &str =
    "I've talked to so many visitors today my circuits are fried. Catch me tomorrow!";

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Spend {
    pub llm_tokens: u64,
    pub tts_chars: u64,
}

pub fn weighted_tokens(u: &r3x_llm::Usage) -> u64 {
    u.input_tokens + u.output_tokens + u.cache_creation_input_tokens.unwrap_or(0) + u.cache_read_input_tokens.unwrap_or(0) / 10
}

#[derive(Debug)]
pub struct Budgets {
    limits: Limits,
    day: u64,
    daily: Spend,
    /// visitor id -> (spend, token expiry)
    visitors: HashMap<String, (Spend, u64)>,
}

impl Budgets {
    pub fn new(limits: Limits) -> Self {
        Self { limits, day: 0, daily: Spend::default(), visitors: HashMap::new() }
    }

    fn roll(&mut self, now: u64) {
        if now / 86_400 != self.day {
            self.day = now / 86_400;
            self.daily = Spend::default();
        }
        // A visitor's spend outlives its token by a day, so a re-used id can't reset it.
        self.visitors.retain(|_, (_, exp)| *exp + 86_400 > now);
    }

    /// Why `visitor` may not start a turn now, if it may not (the refusal R3X "says").
    pub fn refusal(&mut self, visitor: &str, now: u64) -> Option<&'static str> {
        self.roll(now);
        let l = &self.limits;
        if self.daily.llm_tokens >= l.daily_llm_tokens || self.daily.tts_chars >= l.daily_tts_chars {
            return Some(DAILY_SPENT);
        }
        let s = self.visitors.get(visitor).map(|v| v.0).unwrap_or_default();
        (s.llm_tokens >= l.visitor_llm_tokens || s.tts_chars >= l.visitor_tts_chars).then_some(VISITOR_SPENT)
    }

    pub fn charge_llm(&mut self, visitor: &str, exp: u64, tokens: u64, now: u64) {
        self.roll(now);
        self.daily.llm_tokens += tokens;
        self.visitors.entry(visitor.to_owned()).or_insert((Spend::default(), exp)).0.llm_tokens += tokens;
    }

    /// Charge `chars` of speech if both caps have room for it; `false` = do not synthesise.
    pub fn try_tts(&mut self, visitor: &str, exp: u64, chars: u64, now: u64) -> bool {
        self.roll(now);
        let l = self.limits.clone();
        let v = &mut self.visitors.entry(visitor.to_owned()).or_insert((Spend::default(), exp)).0;
        if v.tts_chars + chars > l.visitor_tts_chars || self.daily.tts_chars + chars > l.daily_tts_chars {
            return false;
        }
        v.tts_chars += chars;
        self.daily.tts_chars += chars;
        true
    }

    pub fn visitor(&self, visitor: &str) -> Spend {
        self.visitors.get(visitor).map(|v| v.0).unwrap_or_default()
    }

    pub fn daily(&self) -> Spend {
        self.daily
    }
}

/// Token bucket: `per_min` events per minute, bursts up to `per_min`.
#[derive(Debug)]
pub struct Bucket {
    tokens: f64,
    cap: f64,
    last: Instant,
}

impl Bucket {
    pub fn per_min(n: u32) -> Self {
        Self { tokens: f64::from(n), cap: f64::from(n), last: Instant::now() }
    }

    pub fn take(&mut self) -> bool {
        let now = Instant::now();
        self.tokens = (self.tokens + now.duration_since(self.last).as_secs_f64() * self.cap / 60.0).min(self.cap);
        self.last = now;
        if self.tokens >= 1.0 {
            self.tokens -= 1.0;
            true
        } else {
            false
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn visitor_and_daily_caps() {
        let l = Limits { visitor_llm_tokens: 100, visitor_tts_chars: 50, daily_llm_tokens: 150, daily_tts_chars: 80, ..Limits::default() };
        let mut b = Budgets::new(l);
        let now = 10 * 86_400 + 5;
        assert_eq!(b.refusal("a", now), None);
        b.charge_llm("a", now + 60, 100, now);
        assert_eq!(b.refusal("a", now), Some(VISITOR_SPENT));
        assert_eq!(b.refusal("b", now), None, "per visitor");
        assert!(b.try_tts("b", now + 60, 50, now));
        assert!(!b.try_tts("b", now + 60, 1, now), "visitor tts cap");
        assert!(!b.try_tts("c", now + 60, 31, now), "daily tts cap (50 + 31 > 80)");
        b.charge_llm("c", now + 60, 60, now);
        assert_eq!(b.refusal("d", now), Some(DAILY_SPENT));
        assert_eq!(b.refusal("d", now + 86_400), None, "the daily cap resets at UTC midnight");
        assert_eq!(b.visitor("a").llm_tokens, 100, "visitor spend outlives the day");
    }

    #[test]
    fn bucket_refills() {
        let mut b = Bucket::per_min(2);
        assert!(b.take() && b.take() && !b.take());
        b.last -= Duration::from_secs(30);
        assert!(b.take());
    }
}

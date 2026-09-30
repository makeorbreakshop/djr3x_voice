//! `r3x-memory`: R3X's long-term memory on SQLite.
//!
//! Tables: `people` (profile JSON), `visits` (episodic summaries), `timeline` (both sides of
//! every conversation, intents, tracks, modes), `dj_history`, `ops_state` (persisted
//! nervous-system keys). Summaries use the one `CLAUDE_MODEL` through `r3x-llm`.
//!
//! Brain wiring (events -> writes): person detected/exited -> [`Memory::person_detected`] /
//! [`Memory::person_exited`]; final transcript -> [`Memory::record_user`]; *complete* reply ->
//! [`Memory::record_reply`]; intent result / track / mode -> [`Memory::record_event`],
//! [`Memory::record_track`], [`Memory::mode_changed`]; startup -> [`Memory::catch_up_summaries`];
//! shutdown -> [`Memory::shutdown`]. Reads: [`Memory::person_profile`],
//! [`Memory::conversation_history`], [`Memory::turn_context`].

pub mod import;
pub mod model;
pub mod summary;

pub use model::{format_history, EventKind, PersonProfile, TimelineEvent, Turn, VisitSummary};

use r3x_llm::{LlmClient, MessagesRequest, TurnContext};
use rusqlite::{params, Connection, OptionalExtension};
use serde_json::{json, Value};
use std::path::Path;
use std::sync::{Arc, Mutex, MutexGuard};

/// A sighting more than this after `last_seen` is a new visit.
pub const NEW_VISIT_AFTER_S: f64 = 300.0;
/// Scene text older than this is not injected.
pub const SCENE_MAX_AGE_S: f64 = 60.0;

#[derive(Debug, thiserror::Error)]
pub enum MemoryError {
    #[error("sqlite: {0}")]
    Sql(#[from] rusqlite::Error),
    #[error("io: {0}")]
    Io(#[from] std::io::Error),
    #[error("json: {0}")]
    Json(#[from] serde_json::Error),
}
pub type Result<T> = std::result::Result<T, MemoryError>;

type Clock = Arc<dyn Fn() -> f64 + Send + Sync>;

fn system_now() -> f64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0.0, |d| d.as_secs_f64())
}

#[derive(Default)]
struct Presence {
    person: Option<String>,
    arrived: Option<f64>,
    needs_summary: bool,
}

pub struct Memory {
    db: Mutex<Connection>,
    llm: Option<LlmClient>,
    presence: Mutex<Presence>,
    clock: Clock,
}

const SCHEMA: &str = "
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS people (name TEXT PRIMARY KEY, profile TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS visits (id INTEGER PRIMARY KEY, person TEXT NOT NULL, visit_number INTEGER NOT NULL,
    date TEXT NOT NULL, duration_s REAL NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS visits_person ON visits(person, id);
CREATE TABLE IF NOT EXISTS timeline (id INTEGER PRIMARY KEY, ts REAL NOT NULL, kind TEXT NOT NULL, person TEXT,
    conversation_id TEXT, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS timeline_kind ON timeline(kind, ts);
CREATE INDEX IF NOT EXISTS timeline_person ON timeline(person, ts);
CREATE TABLE IF NOT EXISTS dj_history (id INTEGER PRIMARY KEY, ts REAL NOT NULL, track TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ops_state (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated REAL NOT NULL);
";

fn date_of(ts: f64) -> String {
    // civil date (UTC) from unix seconds, no chrono dependency
    let days = (ts / 86400.0).floor() as i64;
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + i64::from(m <= 2);
    format!("{y:04}-{m:02}-{d:02}")
}

impl Memory {
    pub fn open(path: impl AsRef<Path>) -> Result<Self> {
        if let Some(dir) = path.as_ref().parent().filter(|d| !d.as_os_str().is_empty()) {
            std::fs::create_dir_all(dir)?;
        }
        Self::from_conn(Connection::open(path)?)
    }

    pub fn open_in_memory() -> Result<Self> {
        Self::from_conn(Connection::open_in_memory()?)
    }

    fn from_conn(conn: Connection) -> Result<Self> {
        conn.execute_batch(SCHEMA)?;
        Ok(Self { db: Mutex::new(conn), llm: None, presence: Mutex::default(), clock: Arc::new(system_now) })
    }

    /// Enables summaries. Without it, memory still records; it just never summarises.
    pub fn with_llm(mut self, llm: LlmClient) -> Self {
        self.llm = Some(llm);
        self
    }

    /// For tests: a controllable clock (unix seconds).
    pub fn with_clock(mut self, clock: impl Fn() -> f64 + Send + Sync + 'static) -> Self {
        self.clock = Arc::new(clock);
        self
    }

    pub(crate) fn now(&self) -> f64 {
        (self.clock)()
    }
    pub(crate) fn db(&self) -> MutexGuard<'_, Connection> {
        self.db.lock().unwrap_or_else(|p| p.into_inner())
    }
    fn presence(&self) -> MutexGuard<'_, Presence> {
        self.presence.lock().unwrap_or_else(|p| p.into_inner())
    }

    // ------------------------------------------------------------------ people

    /// Read-only lookup for the brain (`None` = never seen).
    pub fn person_profile(&self, name: &str) -> Result<Option<PersonProfile>> {
        let db = self.db();
        let Some(json): Option<String> =
            db.query_row("SELECT profile FROM people WHERE name=?1", [name], |r| r.get(0)).optional()?
        else {
            return Ok(None);
        };
        let mut p: PersonProfile = serde_json::from_str(&json)?;
        let mut st = db.prepare("SELECT data FROM (SELECT id, data FROM visits WHERE person=?1 ORDER BY id DESC LIMIT 5) ORDER BY id")?;
        p.recent_visits = st
            .query_map([name], |r| r.get::<_, String>(0))?
            .filter_map(|s| s.ok().and_then(|s| serde_json::from_str(&s).ok()))
            .collect();
        Ok(Some(p))
    }

    pub fn people(&self) -> Result<Vec<String>> {
        let db = self.db();
        let mut st = db.prepare("SELECT name FROM people ORDER BY name")?;
        let names = st.query_map([], |r| r.get(0))?.collect::<std::result::Result<_, _>>()?;
        Ok(names)
    }

    /// Persist a profile. `recent_visits` beyond those already stored are appended to `visits`.
    pub fn save_profile(&self, p: &PersonProfile) -> Result<()> {
        let db = self.db();
        let known: std::collections::HashSet<String> = {
            let mut st = db.prepare("SELECT data FROM (SELECT id, data FROM visits WHERE person=?1 ORDER BY id DESC LIMIT 5)")?;
            let rows = st.query_map([&p.name], |r| r.get::<_, String>(0))?;
            rows.filter_map(std::result::Result::ok).collect()
        };
        for v in &p.recent_visits {
            let data = serde_json::to_string(v)?;
            if !known.contains(&data) {
                db.execute(
                    "INSERT INTO visits(person, visit_number, date, duration_s, data) VALUES (?1,?2,?3,?4,?5)",
                    params![p.name, v.visit_number, v.date, v.duration_seconds, data],
                )?;
            }
        }
        let mut bare = p.clone();
        bare.recent_visits.clear();
        db.execute(
            "INSERT INTO people(name, profile) VALUES (?1, ?2) ON CONFLICT(name) DO UPDATE SET profile=excluded.profile",
            params![p.name, serde_json::to_string(&bare)?],
        )?;
        Ok(())
    }

    /// Vision saw `name`: new visit if last seen > 5 min ago. Returns the updated profile.
    pub fn person_detected(&self, name: &str) -> Result<Option<PersonProfile>> {
        if name.is_empty() || name == "Unknown" {
            return Ok(None);
        }
        let now = self.now();
        let mut p = self.person_profile(name)?.unwrap_or_else(|| PersonProfile::new(name, now));
        if p.visit_count == 0 || now - p.last_seen > NEW_VISIT_AFTER_S {
            p.visit_count += 1;
            tracing::info!("New visit recorded for {name} (total: {})", p.visit_count);
        }
        p.last_seen = now;
        self.save_profile(&p)?;
        let mut pr = self.presence();
        pr.person = Some(name.into());
        pr.arrived = Some(now);
        Ok(Some(p))
    }

    /// Vision lost `name`: add interaction time. Summaries are deferred until R3X leaves
    /// INTERACTIVE (never mid-conversation).
    pub fn person_exited(&self, name: &str) -> Result<()> {
        let arrived = {
            let pr = self.presence();
            if pr.person.as_deref() != Some(name) {
                return Ok(());
            }
            pr.arrived
        };
        if let (Some(t0), Some(mut p)) = (arrived, self.person_profile(name)?) {
            p.total_interaction_time_seconds += self.now() - t0;
            self.save_profile(&p)?;
        }
        let mut pr = self.presence();
        pr.person = None;
        pr.arrived = None;
        Ok(())
    }

    pub fn current_person(&self) -> Option<String> {
        self.presence().person.clone()
    }

    // ------------------------------------------------------------------ timeline

    pub(crate) fn insert_event(&self, ts: f64, kind: &str, person: Option<&str>, conv: Option<&str>, data: &Value) -> Result<()> {
        self.db().execute(
            "INSERT INTO timeline(ts, kind, person, conversation_id, data) VALUES (?1,?2,?3,?4,?5)",
            params![ts, kind, person, conv, serde_json::to_string(data)?],
        )?;
        Ok(())
    }

    /// Append an event attributed to whoever is present now.
    pub fn record_event(&self, kind: EventKind, data: Value, conversation_id: Option<&str>) -> Result<()> {
        let person = self.current_person();
        self.insert_event(self.now(), kind.as_str(), person.as_deref(), conversation_id, &data)
    }

    /// What the person said (final transcript).
    pub fn record_user(&self, text: &str, conversation_id: Option<&str>) -> Result<()> {
        if text.trim().is_empty() {
            return Ok(());
        }
        self.record_event(EventKind::UserSaid, json!({"text": text}), conversation_id)?;
        let mut pr = self.presence();
        if pr.person.is_some() {
            pr.needs_summary = true;
        }
        Ok(())
    }

    /// What R3X said - the complete reply, once per turn (not stream chunks).
    pub fn record_reply(&self, text: &str, conversation_id: Option<&str>) -> Result<()> {
        if text.trim().is_empty() {
            return Ok(());
        }
        self.record_event(EventKind::RexSaid, json!({"text": text}), conversation_id)
    }

    /// A track started; `dj` also appends to the DJ history.
    pub fn record_track(&self, track: &str, dj: bool) -> Result<()> {
        self.record_event(EventKind::TrackPlaying, json!({"track_name": track}), None)?;
        if dj {
            self.db().execute("INSERT INTO dj_history(ts, track) VALUES (?1, ?2)", params![self.now(), track])?;
        }
        Ok(())
    }

    /// Most recent last.
    pub fn dj_history(&self, limit: usize) -> Result<Vec<String>> {
        let db = self.db();
        let mut st = db.prepare("SELECT track FROM (SELECT id, track FROM dj_history ORDER BY id DESC LIMIT ?1) ORDER BY id")?;
        let v = st.query_map([limit as i64], |r| r.get(0))?.collect::<std::result::Result<_, _>>()?;
        Ok(v)
    }

    /// Newest first.
    pub fn recent_events(&self, kind: Option<EventKind>, person: Option<&str>, limit: usize) -> Result<Vec<TimelineEvent>> {
        let db = self.db();
        let mut st = db.prepare(
            "SELECT ts, kind, person, conversation_id, data FROM timeline
             WHERE (?1 IS NULL OR kind=?1) AND (?2 IS NULL OR person=?2) ORDER BY ts DESC, id DESC LIMIT ?3",
        )?;
        let rows = st.query_map(params![kind.map(EventKind::as_str), person, limit as i64], |r| {
            Ok(TimelineEvent {
                ts: r.get(0)?,
                kind: r.get(1)?,
                person: r.get(2)?,
                conversation_id: r.get(3)?,
                data: serde_json::from_str(&r.get::<_, String>(4)?).unwrap_or(Value::Null),
            })
        })?;
        Ok(rows.collect::<std::result::Result<_, _>>()?)
    }

    /// Paired user/R3X turns, oldest first, the last `limit`.
    pub fn conversation_history(&self, person: Option<&str>, limit: usize) -> Result<Vec<Turn>> {
        let mut ev = self.recent_events(Some(EventKind::UserSaid), person, limit * 2)?;
        ev.extend(self.recent_events(Some(EventKind::RexSaid), person, limit * 2)?);
        ev.sort_by(|a, b| a.ts.total_cmp(&b.ts));
        let mut turns: Vec<Turn> = Vec::new();
        let mut cur: Option<Turn> = None;
        for e in ev {
            let text = e.data["text"].as_str().unwrap_or_default().to_string();
            if e.kind == EventKind::UserSaid.as_str() {
                turns.extend(cur.take());
                cur = Some(Turn { user: Some(text), ts: e.ts, conversation_id: e.conversation_id, assistant: None });
            } else if let Some(mut t) = cur.take() {
                t.assistant = Some(text);
                turns.push(t);
            }
        }
        turns.extend(cur);
        let n = turns.len();
        Ok(turns.split_off(n.saturating_sub(limit)))
    }

    /// Context for the next Claude turn: `<person_memory>`, `<system_observation>`,
    /// `<conversation_history>`. `scene` is `(text, captured_at_unix)`.
    pub fn turn_context(&self, scene: Option<(&str, f64)>, history_turns: usize) -> Result<TurnContext> {
        let now = self.now();
        let mut ctx = TurnContext::default();
        if let Some((s, _)) = scene.filter(|(s, at)| !s.is_empty() && now - at < SCENE_MAX_AGE_S) {
            ctx.observations.push(format!("What you can see - {s}"));
        }
        if let Some(name) = self.current_person() {
            match self.person_profile(&name)? {
                Some(p) => {
                    ctx.observations.push(format!("Speaking with: {}", p.minimal_context(now)));
                    ctx.person_memory = p.person_memory();
                }
                None => ctx.observations.push(format!("Speaking with: {name}")),
            }
            ctx.conversation_history = format_history(&name, &self.conversation_history(Some(&name), history_turns)?);
        }
        Ok(ctx)
    }

    // ------------------------------------------------------------------ ops state

    pub fn set_state(&self, key: &str, value: &Value) -> Result<()> {
        self.db().execute(
            "INSERT INTO ops_state(key, value, updated) VALUES (?1,?2,?3)
             ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
            params![key, serde_json::to_string(value)?, self.now()],
        )?;
        Ok(())
    }

    pub fn get_state(&self, key: &str) -> Result<Option<Value>> {
        let v: Option<String> = self.db().query_row("SELECT value FROM ops_state WHERE key=?1", [key], |r| r.get(0)).optional()?;
        Ok(v.map(|s| serde_json::from_str(&s)).transpose()?)
    }

    // ------------------------------------------------------------------ summaries

    async fn complete(&self, prompt: String, max_tokens: u32) -> Option<String> {
        let llm = self.llm.as_ref()?;
        match llm.create(&MessagesRequest::new(max_tokens).user(prompt)).await {
            Ok(m) => Some(m.text().trim().to_string()),
            Err(e) => {
                tracing::error!("memory summary call failed: {e}");
                None
            }
        }
    }

    /// Rolling 2-3 sentence summary in `metadata.conversation_summary`. Needs >= 2 turns.
    pub async fn update_rolling_summary(&self, name: &str) -> Result<bool> {
        if self.llm.is_none() {
            return Ok(false);
        }
        let turns = self.conversation_history(Some(name), 20)?;
        let Some(p) = self.person_profile(name)? else { return Ok(false) };
        if turns.len() < 2 {
            return Ok(false);
        }
        let existing = p.metadata.get("conversation_summary").and_then(Value::as_str).unwrap_or_default().to_string();
        let Some(text) = self.complete(summary::rolling_prompt(name, &existing, &turns), summary::ROLLING_MAX_TOKENS).await else {
            return Ok(false);
        };
        // re-read: the profile may have moved on during the call
        let mut p = self.person_profile(name)?.unwrap_or(p);
        let count = p.metadata.get("summary_visit_count").and_then(Value::as_u64).unwrap_or(0) + 1;
        p.metadata.insert("conversation_summary".into(), json!(text));
        p.metadata.insert("summary_updated_at".into(), json!(self.now()));
        p.metadata.insert("summary_visit_count".into(), json!(count));
        self.save_profile(&p)?;
        tracing::info!("Updated conversation summary for {name} (visit #{})", p.visit_count);
        Ok(true)
    }

    /// Structured episodic summary of the visit that just ended (facts, traits, music, follow-ups).
    pub async fn summarize_visit(&self, name: &str, duration_s: f64) -> Result<bool> {
        if self.llm.is_none() {
            return Ok(false);
        }
        let turns = self.conversation_history(Some(name), 20)?;
        let Some(p) = self.person_profile(name)? else { return Ok(false) };
        if turns.is_empty() {
            return Ok(false);
        }
        let music: Vec<String> = self
            .recent_events(Some(EventKind::TrackPlaying), Some(name), 10)?
            .iter()
            .map(|e| e.data["track_name"].as_str().unwrap_or("Unknown").to_string())
            .collect();
        let Some(raw) = self.complete(summary::visit_prompt(&p, &turns, duration_s, &music), summary::VISIT_MAX_TOKENS).await else {
            return Ok(false);
        };
        let mut p = self.person_profile(name)?.unwrap_or(p);
        match summary::extract_json(&raw).and_then(|j| serde_json::from_str::<Value>(j).ok()) {
            Some(data) => summary::apply_visit(&mut p, &data, &date_of(self.now()), duration_s, &music, self.now()),
            None => {
                tracing::error!("visit summary for {name} was not JSON: {}", raw.chars().take(200).collect::<String>());
                p.metadata.insert(
                    "conversation_summary".into(),
                    json!(format!("Visit #{} - {}min interaction", p.visit_count, (duration_s / 60.0) as i64)),
                );
            }
        }
        self.save_profile(&p)?;
        Ok(true)
    }

    /// Mode change: logged; leaving INTERACTIVE with someone who talked -> rolling summary.
    pub async fn mode_changed(&self, old_mode: &str, new_mode: &str) -> Result<()> {
        self.record_event(EventKind::ModeChanged, json!({"old_mode": old_mode, "new_mode": new_mode}), None)?;
        let who = {
            let pr = self.presence();
            (old_mode == "INTERACTIVE" && pr.needs_summary).then(|| pr.person.clone()).flatten()
        };
        if let Some(name) = who {
            self.update_rolling_summary(&name).await?;
            self.presence().needs_summary = false;
        }
        Ok(())
    }

    /// Startup: summarise anyone seen since their last summary who has >= 2 turns.
    pub async fn catch_up_summaries(&self) -> Result<usize> {
        if self.llm.is_none() {
            return Ok(0);
        }
        let mut n = 0;
        for name in self.people()? {
            let Some(p) = self.person_profile(&name)? else { continue };
            let summarised = p.metadata.get("summary_updated_at").and_then(Value::as_f64).unwrap_or(0.0);
            if p.last_seen > summarised && self.conversation_history(Some(&name), 20)?.len() >= 2 && self.update_rolling_summary(&name).await? {
                n += 1;
            }
        }
        if n > 0 {
            tracing::info!("Catch-up complete: generated {n} conversation summaries");
        }
        Ok(n)
    }

    /// Shutdown with someone still present and unsummarised: visit summary now.
    pub async fn shutdown(&self) -> Result<()> {
        let pending = {
            let pr = self.presence();
            match (&pr.person, pr.arrived, pr.needs_summary) {
                (Some(p), Some(t0), true) => Some((p.clone(), t0)),
                _ => None,
            }
        };
        if let Some((name, t0)) = pending {
            let dur = self.now() - t0;
            if let Some(mut p) = self.person_profile(&name)? {
                p.total_interaction_time_seconds += dur;
                self.save_profile(&p)?;
            }
            self.summarize_visit(&name, dur).await?;
            self.presence().needs_summary = false;
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn civil_date() {
        assert_eq!(date_of(0.0), "1970-01-01");
        assert_eq!(date_of(1_790_699_489.0), "2026-09-29");
    }
}

//! JSONL session log: one line per stamped message (plan §8.2).

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use r3x_contracts::{Envelope, Source};
use serde::{Deserialize, Serialize};
use tokio::io::AsyncWriteExt;
use tokio::sync::mpsc;

/// One line of `session-<utc>.jsonl`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LogRecord {
    pub seq: u64,
    pub t_mono: f64,
    pub t_wall: f64,
    pub topic: String,
    pub source: Source,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub conversation_id: Option<String>,
    pub payload: serde_json::Value,
}

pub(crate) struct LogSender {
    tx: mpsc::Sender<(Arc<Envelope>, String)>,
    dropped: AtomicU64,
}

impl LogSender {
    /// Never blocks the bus: a full queue drops the record and counts it.
    pub(crate) fn record(&self, env: &Arc<Envelope>, topic: String) {
        if self.tx.try_send((env.clone(), topic)).is_err() {
            let n = self.dropped.fetch_add(1, Ordering::Relaxed) + 1;
            if n.is_power_of_two() {
                tracing::warn!(dropped = n, "session log queue full; dropping records");
            }
        }
    }
}

pub(crate) async fn start(
    dir: &Path,
    capacity: usize,
) -> std::io::Result<(LogSender, PathBuf, tokio::task::JoinHandle<std::io::Result<()>>)> {
    tokio::fs::create_dir_all(dir).await?;
    let path = dir.join(format!("session-{}.jsonl", utc_stamp(std::time::SystemTime::now())));
    let file = tokio::fs::OpenOptions::new().create(true).append(true).open(&path).await?;
    let (tx, mut rx) = mpsc::channel::<(Arc<Envelope>, String)>(capacity);

    let task = tokio::spawn(async move {
        let mut out = tokio::io::BufWriter::new(file);
        while let Some((env, topic)) = rx.recv().await {
            let rec = to_record(&env, topic);
            let mut line = serde_json::to_vec(&rec).map_err(std::io::Error::other)?;
            line.push(b'\n');
            out.write_all(&line).await?;
            if rx.is_empty() {
                out.flush().await?;
            }
        }
        out.flush().await
    });
    Ok((LogSender { tx, dropped: AtomicU64::new(0) }, path, task))
}

fn to_record(env: &Envelope, topic: String) -> LogRecord {
    // The body serialises as {kind, body}; the payload is the inner body.
    let payload = serde_json::to_value(&env.body)
        .ok()
        .and_then(|mut v| v.get_mut("body").map(serde_json::Value::take))
        .unwrap_or(serde_json::Value::Null);
    LogRecord {
        seq: env.seq,
        t_mono: env.t_mono,
        t_wall: env.t_wall,
        topic,
        source: env.source,
        conversation_id: env.conversation_id.clone(),
        payload,
    }
}

/// `YYYYMMDDTHHMMSSZ` without a date-time dependency.
fn utc_stamp(t: std::time::SystemTime) -> String {
    let secs = t.duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0) as i64;
    let (days, rem) = (secs.div_euclid(86_400), secs.rem_euclid(86_400));
    // Howard Hinnant's civil_from_days.
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + i64::from(m <= 2);
    format!("{y:04}{m:02}{d:02}T{:02}{:02}{:02}Z", rem / 3600, rem % 3600 / 60, rem % 60)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{Duration, UNIX_EPOCH};

    #[test]
    fn utc_stamp_formats() {
        assert_eq!(utc_stamp(UNIX_EPOCH), "19700101T000000Z");
        // 2026-09-29 12:34:56 UTC
        assert_eq!(utc_stamp(UNIX_EPOCH + Duration::from_secs(1_790_685_296)), "20260929T123456Z");
    }
}

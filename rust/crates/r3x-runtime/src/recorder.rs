//! The recorder (`telemetry.record`, the panel's Record button): one folder per take under
//! `R3X_RECORD_DIR` (default `~/Movies/R3X`), no browser in the loop:
//!
//! - `screen.mp4`: the main screen at 30 fps (ffmpeg avfoundation, VideoToolbox H.264).
//! - `mic.wav`: the system default microphone (`R3X_RECORD_MIC` = a name to match instead),
//!   from the same ffmpeg, so it shares the screen's clock.
//! - `r3x.wav`: R3X's output mix (speech, music, sfx) from the mixer tap, cut or padded to
//!   start when ffmpeg's capture did: `q` ends the capture, so start = stop - its media time.
//! - `session.json`: devices, rates, markers.
//!
//! macOS asks once for Screen Recording (and Microphone) permission for the app that started
//! `./r3x` (Terminal). State is the `recorder` service status: running = recording.

use std::collections::VecDeque;
use std::io::{Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use r3x_bus::Bus;
use r3x_contracts::{Ack, RecordAction, ServiceStatus};
use r3x_gateway::AudioOut;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::sync::{broadcast, oneshot};

/// An avfoundation device: ffmpeg's index and its name.
pub type Device = (u32, String);

/// ffmpeg's `AVFoundation video devices:` / `audio devices:` lists.
pub fn parse_devices(stderr: &str) -> (Vec<Device>, Vec<Device>) {
    let (mut video, mut audio) = (Vec::new(), Vec::new());
    let mut into_audio = false;
    for line in stderr.lines() {
        if line.contains("AVFoundation video devices") {
            into_audio = false;
            continue;
        }
        if line.contains("AVFoundation audio devices") {
            into_audio = true;
            continue;
        }
        // "[AVFoundation indev @ 0x...] [3] Capture screen 0"
        let Some(rest) = line.split("] [").nth(1) else { continue };
        let Some((idx, name)) = rest.split_once("] ") else { continue };
        let Ok(i) = idx.parse() else { continue };
        if into_audio { &mut audio } else { &mut video }.push((i, name.trim().to_string()));
    }
    (video, audio)
}

/// The first screen, and the mic whose name matches `mic` (exactly, else containing it,
/// ignoring case). No match = no mic track rather than the wrong mic.
pub fn pick_inputs(video: &[Device], audio: &[Device], mic: Option<&str>) -> Result<(u32, Option<u32>), String> {
    let screen = video
        .iter()
        .find(|(_, n)| n.starts_with("Capture screen"))
        .map(|(i, _)| *i)
        .ok_or("ffmpeg lists no screen to capture")?;
    let mic = mic.map(str::to_lowercase).and_then(|want| {
        audio
            .iter()
            .find(|(_, n)| n.to_lowercase() == want)
            .or_else(|| audio.iter().find(|(_, n)| n.to_lowercase().contains(&want)))
            .map(|(i, _)| *i)
    });
    Ok((screen, mic))
}

/// One ffmpeg: the screen (and mic) in, `screen.mp4` (and `mic.wav`) out, progress on stdout.
pub fn ffmpeg_args(screen: u32, mic: Option<u32>, dir: &Path) -> Vec<String> {
    let input = format!("{screen}:{}", mic.map_or("none".to_string(), |m| m.to_string()));
    let mut a: Vec<String> = [
        "-hide_banner", "-loglevel", "warning", "-nostats", "-progress", "pipe:1", "-stats_period", "0.1",
        "-f", "avfoundation", "-capture_cursor", "1", "-framerate", "30", "-thread_queue_size", "4096", "-i",
    ]
    .iter()
    .map(|s| s.to_string())
    .collect();
    a.push(input);
    for s in [
        "-map", "0:v", "-vf", "scale='min(2560,iw)':-2,format=yuv420p", "-r", "30",
        "-c:v", "h264_videotoolbox", "-b:v", "16M", "-movflags", "+faststart",
    ] {
        a.push(s.into());
    }
    a.push(dir.join("screen.mp4").display().to_string());
    if mic.is_some() {
        for s in ["-map", "0:a", "-c:a", "pcm_s16le"] {
            a.push(s.into());
        }
        a.push(dir.join("mic.wav").display().to_string());
    }
    a
}

/// 16-bit PCM WAV, header patched on close.
pub struct WavFile {
    file: std::fs::File,
    rate: u32,
    channels: u16,
    bytes: u32,
}

impl WavFile {
    pub fn create(path: &Path, rate: u32, channels: u16) -> std::io::Result<Self> {
        let mut file = std::fs::File::create(path)?;
        file.write_all(&[0u8; 44])?;
        Ok(Self { file, rate, channels, bytes: 0 })
    }

    pub fn write(&mut self, pcm16le: &[u8]) -> std::io::Result<()> {
        self.bytes += pcm16le.len() as u32;
        self.file.write_all(pcm16le)
    }

    pub fn finish(mut self) -> std::io::Result<()> {
        let (rate, ch) = (self.rate, u32::from(self.channels));
        let mut h = Vec::with_capacity(44);
        h.extend_from_slice(b"RIFF");
        h.extend_from_slice(&(36 + self.bytes).to_le_bytes());
        h.extend_from_slice(b"WAVEfmt ");
        h.extend_from_slice(&16u32.to_le_bytes());
        h.extend_from_slice(&1u16.to_le_bytes());
        h.extend_from_slice(&self.channels.to_le_bytes());
        h.extend_from_slice(&rate.to_le_bytes());
        h.extend_from_slice(&(rate * ch * 2).to_le_bytes());
        h.extend_from_slice(&((ch * 2) as u16).to_le_bytes());
        h.extend_from_slice(&16u16.to_le_bytes());
        h.extend_from_slice(b"data");
        h.extend_from_slice(&self.bytes.to_le_bytes());
        self.file.seek(SeekFrom::Start(0))?;
        self.file.write_all(&h)?;
        self.file.sync_all()
    }
}

fn record_dir() -> PathBuf {
    match std::env::var_os("R3X_RECORD_DIR").filter(|v| !v.is_empty()) {
        Some(d) => PathBuf::from(d),
        None => PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join("Movies/R3X"),
    }
}

/// Local time for folder names (`date`, so no time-zone crate).
fn stamp() -> String {
    std::process::Command::new("date")
        .arg("+%Y-%m-%d_%H-%M-%S")
        .output()
        .ok()
        .and_then(|o| String::from_utf8(o.stdout).ok())
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| format!("{}", std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0, |d| d.as_secs())))
}

fn ffmpeg() -> String {
    std::env::var("R3X_FFMPEG").unwrap_or_else(|_| "ffmpeg".into())
}

enum State {
    Idle,
    Busy,
    Recording { stop: oneshot::Sender<()>, markers: Arc<Mutex<Vec<f64>>>, started: Arc<Mutex<Option<Instant>>> },
}

#[derive(Clone)]
pub struct Recorder {
    bus: Bus,
    mix: Option<broadcast::Sender<Arc<AudioOut>>>,
    state: Arc<Mutex<State>>,
}

impl Recorder {
    pub fn new(bus: &Bus, mix: Option<broadcast::Sender<Arc<AudioOut>>>) -> Self {
        r3x_ops::report(bus, "recorder", ServiceStatus::Stopped, Some(format!("Saves to {}", record_dir().display())));
        Self { bus: bus.clone(), mix, state: Arc::new(Mutex::new(State::Idle)) }
    }

    pub fn control(&self) -> r3x_ops::RecordControl {
        let me = self.clone();
        Arc::new(move |a| me.act(a))
    }

    fn act(&self, action: RecordAction) -> Ack {
        let mut st = self.state.lock().unwrap();
        match (action, &*st) {
            (RecordAction::Start, State::Idle) => {
                let (tx, rx) = oneshot::channel();
                let markers = Arc::new(Mutex::new(Vec::new()));
                let started = Arc::new(Mutex::new(None));
                *st = State::Recording { stop: tx, markers: markers.clone(), started: started.clone() };
                let me = self.clone();
                tokio::spawn(async move {
                    let result = me.take(rx, markers, started).await;
                    *me.state.lock().unwrap() = State::Idle;
                    match result {
                        Ok(dir) => r3x_ops::report(&me.bus, "recorder", ServiceStatus::Stopped, Some(format!("Saved {}", dir.display()))),
                        Err(e) => {
                            tracing::warn!("recording failed: {e}");
                            r3x_ops::report(&me.bus, "recorder", ServiceStatus::Error, Some(e));
                        }
                    }
                });
                Ack::Accepted
            }
            (RecordAction::Start, _) => Ack::rejected("already recording"),
            (RecordAction::Stop, State::Recording { .. }) => {
                if let State::Recording { stop, .. } = std::mem::replace(&mut *st, State::Busy) {
                    let _ = stop.send(());
                }
                Ack::Accepted
            }
            (RecordAction::Stop, _) => Ack::rejected("not recording"),
            (RecordAction::Marker, State::Recording { markers, started, .. }) => match *started.lock().unwrap() {
                Some(t0) => {
                    markers.lock().unwrap().push((t0.elapsed().as_secs_f64() * 100.0).round() / 100.0);
                    Ack::Accepted
                }
                None => Ack::rejected("the recording has not started yet"),
            },
            (RecordAction::Marker, _) => Ack::rejected("not recording"),
        }
    }

    async fn take(&self, mut stop: oneshot::Receiver<()>, markers: Arc<Mutex<Vec<f64>>>, started: Arc<Mutex<Option<Instant>>>) -> Result<PathBuf, String> {
        let name = format!("r3x-{}", stamp());
        let dir = record_dir().join(&name);
        std::fs::create_dir_all(&dir).map_err(|e| format!("could not create {}: {e}", dir.display()))?;

        let list = tokio::process::Command::new(ffmpeg())
            .args(["-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""])
            .stdin(Stdio::null())
            .output()
            .await
            .map_err(|e| format!("ffmpeg not found ({e}); install it with `brew install ffmpeg`"))?;
        let (video, audio) = parse_devices(&String::from_utf8_lossy(&list.stderr));
        let want_mic = std::env::var("R3X_RECORD_MIC").ok().filter(|m| !m.is_empty()).or_else(r3x_audio::device::default_input_name);
        let (screen, mic) = pick_inputs(&video, &audio, want_mic.as_deref())?;
        let mic_name = mic.and_then(|m| audio.iter().find(|(i, _)| *i == m)).map(|(_, n)| n.clone());
        if mic.is_none() {
            tracing::warn!(wanted = ?want_mic, "recorder: no matching mic; recording without mic.wav");
        }

        // Subscribe before ffmpeg starts, so the tap is already running when capture begins.
        let mut mix = self.mix.as_ref().map(|m| m.subscribe());
        let mut child = tokio::process::Command::new(ffmpeg())
            .args(ffmpeg_args(screen, mic, &dir))
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true)
            .spawn()
            .map_err(|e| format!("could not start ffmpeg: {e}"))?;
        let mut stdin = child.stdin.take();
        let mut progress = BufReader::new(child.stdout.take().expect("piped")).lines();
        let errors = Arc::new(Mutex::new(VecDeque::<String>::new()));
        {
            let errors = errors.clone();
            let mut lines = BufReader::new(child.stderr.take().expect("piped")).lines();
            tokio::spawn(async move {
                while let Ok(Some(l)) = lines.next_line().await {
                    tracing::debug!("ffmpeg: {l}");
                    let mut e = errors.lock().unwrap();
                    e.push_back(l);
                    if e.len() > 8 {
                        e.pop_front();
                    }
                }
            });
        }
        let last_error = || errors.lock().unwrap().iter().rev().find(|l| !l.trim().is_empty()).cloned().unwrap_or_default();

        // R3X's mix goes to a raw file from the first chunk; the WAV is cut to the capture start
        // once it is known (below).
        let raw_path = dir.join("r3x.pcm");
        let mut raw: Option<std::fs::File> = None;
        let mut mix_fmt: Option<(u32, u8)> = None;
        let mut mix_first: Option<Instant> = None;
        let mut capturing = false;
        let mut media_s = 0.0f64;
        let mut stopped_at: Option<Instant> = None;
        let mut died: Option<String> = None;
        let spawned = Instant::now();

        loop {
            tokio::select! {
                line = progress.next_line() => match line {
                    Ok(Some(l)) => {
                        // out_time_us: media time written so far; the last one is the take's length.
                        if let Some(us) = l.strip_prefix("out_time_us=").and_then(|v| v.trim().parse::<i64>().ok()).filter(|us| *us > 0) {
                            media_s = us as f64 / 1e6;
                            if !capturing {
                                capturing = true;
                                // Markers count from here until the end-aligned start replaces it.
                                *started.lock().unwrap() = Some(Instant::now() - Duration::from_secs_f64(media_s));
                                tracing::info!(take = %name, "recording");
                                r3x_ops::report(&self.bus, "recorder", ServiceStatus::Running, Some(format!("Recording to {}", dir.display())));
                            }
                        }
                    }
                    Ok(None) | Err(_) => {
                        if stopped_at.is_none() {
                            died = Some(last_error());
                        }
                        break;
                    }
                },
                c = recv_mix(&mut mix) => match c {
                    Ok(c) if stopped_at.is_none() => {
                        if raw.is_none() {
                            match std::fs::File::create(&raw_path) {
                                Ok(f) => raw = Some(f),
                                Err(e) => tracing::warn!("r3x.wav: {e}"),
                            }
                            let per_s = f64::from(c.meta.sample_rate) * f64::from(c.meta.channels) * 2.0;
                            mix_first = Some(Instant::now() - Duration::from_secs_f64(c.pcm.len() as f64 / per_s.max(1.0)));
                            mix_fmt = Some((c.meta.sample_rate, c.meta.channels));
                        }
                        if let Some(f) = raw.as_mut() {
                            if let Err(e) = f.write_all(&c.pcm) {
                                tracing::warn!("r3x.wav: {e}");
                            }
                        }
                    }
                    Ok(_) => {} // after stop: the capture has ended
                    Err(broadcast::error::RecvError::Lagged(n)) => tracing::warn!(chunks = n, "recorder: R3X audio lagged; r3x.wav has a gap"),
                    Err(broadcast::error::RecvError::Closed) => mix = None,
                },
                _ = &mut stop, if stopped_at.is_none() => {
                    stopped_at = Some(Instant::now());
                    // `q` ends the capture now and finalises the files; ffmpeg then closes stdout.
                    if let Some(mut s) = stdin.take() {
                        let _ = s.write_all(b"q").await;
                        let _ = s.flush().await;
                    }
                },
                _ = tokio::time::sleep(Duration::from_secs(10)), if !capturing && stopped_at.is_none() => {
                    died = Some(format!("ffmpeg did not start capturing: {}", last_error()));
                    break;
                },
            }
        }
        drop(mix);
        let end = stopped_at.unwrap_or_else(Instant::now);
        match tokio::time::timeout(Duration::from_secs(15), child.wait()).await {
            Ok(_) => {}
            Err(_) => {
                tracing::warn!("ffmpeg did not finish; killing it");
                let _ = child.kill().await;
            }
        }
        // Capture ran from `end - media_s` to `end` (ffmpeg stops capturing on `q`); its encoder
        // finishing later does not move that. Everything is measured from that start.
        let start = end.checked_sub(Duration::from_secs_f64(media_s)).unwrap_or(spawned);
        drop(raw);
        if let (Some((rate, ch)), Some(first)) = (mix_fmt, mix_first) {
            let lead = signed_secs(start, first);
            finish_mix(&raw_path, &dir.join("r3x.wav"), rate, ch, head_adjust(lead, rate, ch)).map_err(|e| format!("r3x.wav: {e}"))?;
        }
        let duration = media_s;
        let marks: Vec<f64> = {
            // Markers were stamped against the provisional start; re-base them on the real one.
            let provisional = started.lock().unwrap().unwrap_or(start);
            let shift = signed_secs(provisional, start);
            markers.lock().unwrap().iter().map(|m| ((m + shift) * 100.0).round() / 100.0).collect()
        };
        let tracks: Vec<serde_json::Value> = [
            mic_name.as_ref().map(|m| serde_json::json!({ "file": "mic.wav", "source": "microphone", "device": m })),
            mix_fmt.map(|(rate, ch)| serde_json::json!({ "file": "r3x.wav", "source": "R3X output mix (speech, music, sfx)", "rate": rate, "channels": ch })),
        ]
        .into_iter()
        .flatten()
        .collect();
        let session = serde_json::json!({
            "take": name,
            "duration_s": (duration * 100.0).round() / 100.0,
            "screen": { "file": "screen.mp4", "fps": 30 },
            "tracks": tracks,
            "sync": "screen.mp4 and mic.wav come from one ffmpeg; r3x.wav starts when ffmpeg's capture did (within ~0.1 s). Line all three up at 0.",
            "markers": marks,
        });
        std::fs::write(dir.join("session.json"), serde_json::to_vec_pretty(&session).unwrap_or_default()).map_err(|e| format!("session.json: {e}"))?;
        match died {
            Some(e) if !capturing => Err(format!(
                "recording did not start: {e}. If macOS asked for Screen Recording or Microphone permission, allow it for the app running ./r3x, then restart ./r3x."
            )),
            Some(e) => Err(format!("ffmpeg stopped early ({e}); partial take in {}", dir.display())),
            None => Ok(dir),
        }
    }
}

/// `a - b` in seconds, either sign.
fn signed_secs(a: Instant, b: Instant) -> f64 {
    match a.checked_duration_since(b) {
        Some(d) => d.as_secs_f64(),
        None => -b.duration_since(a).as_secs_f64(),
    }
}

/// How to line the mix up with the capture, in bytes of 16-bit PCM: `lead_s` = how long the
/// mix had been running when the capture started. Negative = drop that much from the head,
/// positive = that much silence in front. Whole frames.
pub fn head_adjust(lead_s: f64, rate: u32, channels: u8) -> i64 {
    let frames = (lead_s * f64::from(rate)).round() as i64;
    -frames * i64::from(channels) * 2
}

/// `raw` (16-bit PCM) -> `wav`, its head cut or padded by `adjust` bytes; `raw` is removed.
fn finish_mix(raw: &Path, wav: &Path, rate: u32, channels: u8, adjust: i64) -> std::io::Result<()> {
    use std::io::Read;
    let mut src = std::io::BufReader::new(std::fs::File::open(raw)?);
    let mut out = WavFile::create(wav, rate, u16::from(channels))?;
    if adjust > 0 {
        out.write(&vec![0u8; adjust as usize])?;
    } else if adjust < 0 {
        std::io::copy(&mut (&mut src).take(adjust.unsigned_abs()), &mut std::io::sink())?;
    }
    let mut buf = vec![0u8; 1 << 16];
    loop {
        let n = src.read(&mut buf)?;
        if n == 0 {
            break;
        }
        out.write(&buf[..n])?;
    }
    out.finish()?;
    std::fs::remove_file(raw)
}

async fn recv_mix(rx: &mut Option<broadcast::Receiver<Arc<AudioOut>>>) -> Result<Arc<AudioOut>, broadcast::error::RecvError> {
    match rx {
        Some(rx) => rx.recv().await,
        None => std::future::pending().await,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const LIST: &str = "\
[AVFoundation indev @ 0x155704810] AVFoundation video devices:
[AVFoundation indev @ 0x155704810] [0] FaceTime HD Camera
[AVFoundation indev @ 0x155704810] [1] Brandon’s iPhone (2) Camera
[AVFoundation indev @ 0x155704810] [3] Capture screen 0
[AVFoundation indev @ 0x155704810] AVFoundation audio devices:
[AVFoundation indev @ 0x155704810] [0] Brandon’s iPhone (2) Microphone
[AVFoundation indev @ 0x155704810] [1] MacBook Pro Microphone
Error opening input file .";

    #[test]
    fn lists_parse_into_video_and_audio() {
        let (v, a) = parse_devices(LIST);
        assert_eq!(v.len(), 3);
        assert_eq!(v[2], (3, "Capture screen 0".to_string()));
        assert_eq!(a, vec![(0, "Brandon’s iPhone (2) Microphone".to_string()), (1, "MacBook Pro Microphone".to_string())]);
    }

    #[test]
    fn picks_the_screen_and_the_default_mic_not_the_first() {
        let (v, a) = parse_devices(LIST);
        assert_eq!(pick_inputs(&v, &a, Some("MacBook Pro Microphone")), Ok((3, Some(1))));
        assert_eq!(pick_inputs(&v, &a, Some("macbook")), Ok((3, Some(1))), "a partial name works");
        assert_eq!(pick_inputs(&v, &a, Some("USB interface")), Ok((3, None)), "no match = no mic, never the wrong one");
        assert!(pick_inputs(&v[..2], &a, None).is_err(), "no screen is an error");
    }

    #[test]
    fn ffmpeg_writes_screen_and_mic_from_one_input() {
        let a = ffmpeg_args(3, Some(1), Path::new("/t"));
        let s = a.join(" ");
        assert!(s.contains("-f avfoundation -capture_cursor 1 -framerate 30 -thread_queue_size 4096 -i 3:1"), "{s}");
        assert!(s.contains("-map 0:v") && s.ends_with("-map 0:a -c:a pcm_s16le /t/mic.wav"), "{s}");
        assert!(s.contains("-progress pipe:1"), "start detection reads progress: {s}");
        let a = ffmpeg_args(3, None, Path::new("/t")).join(" ");
        assert!(a.contains("-i 3:none") && a.ends_with("/t/screen.mp4") && !a.contains("mic.wav"), "{a}");
    }

    #[test]
    fn mix_is_cut_to_the_capture_start() {
        // 48 kHz stereo: 192 bytes per ms. Mix began 250 ms before the capture: drop 250 ms.
        assert_eq!(head_adjust(0.25, 48_000, 2), -(250 * 192));
        // Mix began 100 ms after it: 100 ms of silence in front.
        assert_eq!(head_adjust(-0.1, 48_000, 2), 100 * 192);
        // Whole frames only, never half a sample.
        assert_eq!(head_adjust(0.000_01, 48_000, 2) % 4, 0);
    }

    #[test]
    fn wav_header_matches_the_data() {
        let dir = std::env::temp_dir().join(format!("r3x-rec-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let p = dir.join("t.wav");
        let mut w = WavFile::create(&p, 48_000, 2).unwrap();
        w.write(&[1, 0, 2, 0, 3, 0, 4, 0]).unwrap();
        w.write(&[5, 0, 6, 0]).unwrap();
        w.finish().unwrap();
        let b = std::fs::read(&p).unwrap();
        let u32_at = |o: usize| u32::from_le_bytes(b[o..o + 4].try_into().unwrap());
        let u16_at = |o: usize| u16::from_le_bytes(b[o..o + 2].try_into().unwrap());
        assert_eq!(b.len(), 44 + 12);
        assert_eq!((&b[0..4], &b[8..16], &b[36..40]), (&b"RIFF"[..], &b"WAVEfmt "[..], &b"data"[..]));
        assert_eq!(u32_at(4), 36 + 12);
        assert_eq!((u16_at(20), u16_at(22), u32_at(24), u32_at(28), u16_at(32), u16_at(34)), (1, 2, 48_000, 192_000, 4, 16));
        assert_eq!(u32_at(40), 12);
        let _ = std::fs::remove_dir_all(&dir);
    }
}

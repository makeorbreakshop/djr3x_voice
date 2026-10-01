//! Bridge mode (Phase 2): r3x owns the mic, STT and TTS; CantinaOS is still the brain.
//!
//! CantinaOS runs with `R3X_EXTERNAL_VOICE=1`, so its mouse, Deepgram and ElevenLabs services
//! are off. This adapter speaks CantinaOS's bus language over the Phase 0 tap:
//!
//! out (r3x -> CantinaOS), exactly what the retired services emitted:
//!   `voice.listening.started`, `transcription.interim|final`, `mouse.recording.stopped`,
//!   `voice.listening.stopped`, `speech.generation.started` (at the first audible sample, with
//!   `audio_t0`), `speech.synthesis.amplitude`, `speech.alignment`, `speech.generation.complete`,
//!   and `plan.ready` (a reply becomes a one-step speak plan so the timeline owns ducking).
//!
//! in (CantinaOS -> r3x):
//!   `llm.response` (buffered to the complete reply, duplicates per conversation dropped),
//!   `tts.generate.request` / `speech.generation.request` (the timeline's speak steps and show
//!   lines), `mic.recording.start|stop` (CLI `record`, legacy callers).

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use r3x_contracts::Source;
use serde_json::{json, Value};
use tokio::sync::broadcast;

use crate::speaker::{SpeechEvent, SpeechRequest};
use crate::voice::{Voice, VoiceEvent};

/// Fire-and-forget emit onto the CantinaOS bus (the runtime's tap link).
pub trait TapEmit: Send + Sync + 'static {
    fn emit(&self, topic: &str, payload: Value);
}

impl<F: Fn(&str, Value) + Send + Sync + 'static> TapEmit for F {
    fn emit(&self, topic: &str, payload: Value) {
        self(topic, payload)
    }
}

/// Owner name for push-to-talk that arrives as `mic.recording.start` from CantinaOS.
pub const CANTINA_OWNER: &str = "cantina";

#[derive(Default)]
struct Replies {
    conv: Option<String>,
    buffer: String,
    spoken: HashMap<String, Vec<String>>,
}

pub struct CantinaVoice {
    voice: Voice,
    tap: Arc<dyn TapEmit>,
    replies: Mutex<Replies>,
}

impl CantinaVoice {
    /// Start forwarding `voice`'s events to CantinaOS. Feed tap events to [`Self::on_tap`].
    pub fn attach(voice: &Voice, tap: Arc<dyn TapEmit>) -> Arc<Self> {
        let me = Arc::new(Self { voice: voice.clone(), tap, replies: Mutex::default() });
        let mut rx = voice.subscribe();
        let out = me.clone();
        tokio::spawn(async move {
            loop {
                match rx.recv().await {
                    Ok(e) => out.forward(e),
                    Err(broadcast::error::RecvError::Lagged(n)) => tracing::warn!(n, "cantina voice adapter lagged"),
                    Err(_) => return,
                }
            }
        });
        me
    }

    fn emit(&self, topic: &str, payload: Value) {
        self.tap.emit(topic, payload);
    }

    fn forward(&self, e: VoiceEvent) {
        let now = wall();
        match e {
            VoiceEvent::ListeningStarted { turn, .. } => {
                self.emit("voice.listening.started", json!({"conversation_id": turn, "timestamp": now}));
            }
            VoiceEvent::Transcript(t) => {
                let topic = if t.is_final { "transcription.final" } else { "transcription.interim" };
                self.emit(topic, json!({"text": t.text, "source": "deepgram", "is_final": t.is_final,
                    "confidence": t.confidence, "conversation_id": t.turn, "timestamp": now}));
            }
            // Same pair MouseInputService emitted: this one flips the eyes to thinking at once.
            VoiceEvent::Released { .. } => self.emit("mouse.recording.stopped", json!({})),
            // CantinaOS has no listening reactor: the ears stay on the typed bus.
            VoiceEvent::Listen(_) => {}
            VoiceEvent::ListeningStopped { turn, transcript } => {
                let has = !transcript.is_empty();
                self.emit("voice.listening.stopped", json!({"transcript": transcript, "conversation_id": turn, "has_transcript": has}));
            }
            VoiceEvent::Speech(s) => self.forward_speech(s),
        }
    }

    fn forward_speech(&self, e: SpeechEvent) {
        match e {
            SpeechEvent::Started { req, t0 } => self.emit(
                "speech.generation.started",
                json!({"conversation_id": req.conversation_id, "text": req.text, "clip_id": req.clip_id, "audio_t0": t0.wall}),
            ),
            SpeechEvent::Mouth { req, level } => self.emit(
                "speech.synthesis.amplitude",
                json!({"conversation_id": req.conversation_id, "amplitude": level, "timestamp": wall()}),
            ),
            SpeechEvent::Timing { req, timings, t0 } => self.emit(
                "speech.alignment",
                json!({"conversation_id": req.conversation_id, "text": req.text, "chars": timings.chars,
                    "char_start_ms": timings.start_ms, "char_duration_ms": timings.duration_ms,
                    "audio_t0": t0.wall, "clip_id": req.clip_id}),
            ),
            SpeechEvent::Ended { req, audio_s, error } => self.complete(&req, audio_s, error),
            // A waiting speak step must still be released.
            SpeechEvent::Dropped { req, .. } => self.complete(&req, 0.0, None),
        }
    }

    fn complete(&self, req: &SpeechRequest, audio_s: f64, error: Option<String>) {
        self.emit(
            "speech.generation.complete",
            json!({"conversation_id": req.conversation_id.clone().unwrap_or_else(|| "unknown".into()),
                "text": req.text, "audio_length_seconds": audio_s, "success": error.is_none(), "error": error,
                "clip_id": req.clip_id, "step_id": req.step_id, "plan_id": req.plan_id}),
        );
    }

    /// One event from the tap (`kind: event`).
    pub fn on_tap(&self, topic: &str, p: &Value) {
        let s = |k: &str| p.get(k).and_then(Value::as_str).filter(|v| !v.is_empty()).map(str::to_owned);
        match topic {
            "llm.response" => {
                let Some(conv) = s("conversation_id") else { return };
                let complete = p.get("is_complete").and_then(Value::as_bool).unwrap_or(false);
                let text = s("text").unwrap_or_default();
                if let Some(reply) = self.buffer_reply(&conv, &text, complete) {
                    self.plan_speech(&conv, &reply);
                }
            }
            "tts.generate.request" | "speech.generation.request" => {
                let Some(text) = s("text") else {
                    let req = SpeechRequest { clip_id: s("clip_id"), step_id: s("step_id"), plan_id: s("plan_id"), ..SpeechRequest::reply("", s("conversation_id"), Source::Timeline) };
                    self.complete(&req, 0.0, Some("Missing text field".into()));
                    return;
                };
                // ElevenLabsService used `conversation_id or clip_id or step_id`; the reply's
                // plan step id *is* the turn id, so the turn's events keep its id.
                let conversation_id = s("conversation_id").or_else(|| s("clip_id")).or_else(|| s("step_id"));
                self.voice.say(SpeechRequest {
                    text,
                    conversation_id,
                    clip_id: s("clip_id"),
                    step_id: s("step_id"),
                    plan_id: s("plan_id"),
                    source: Source::Timeline,
                    reply: true,
                });
            }
            "mic.recording.start" => {
                let v = self.voice.clone();
                tokio::spawn(async move {
                    let ack = v.ptt_start(CANTINA_OWNER).await;
                    if !ack.is_accepted() {
                        tracing::warn!(?ack, "mic.recording.start refused");
                    }
                });
            }
            "mic.recording.stop" => {
                let v = self.voice.clone();
                tokio::spawn(async move {
                    v.ptt_stop(None).await;
                });
            }
            _ => {}
        }
    }

    /// ElevenLabsService's buffering: deltas accumulate, `is_complete` carries the whole
    /// reply; a complete reply already spoken in this conversation is dropped.
    fn buffer_reply(&self, conv: &str, text: &str, complete: bool) -> Option<String> {
        let mut r = self.replies.lock().unwrap();
        if r.conv.as_deref() != Some(conv) {
            r.conv = Some(conv.to_owned());
            r.buffer.clear();
        }
        if !text.is_empty() {
            if complete {
                r.buffer = text.to_owned();
            } else {
                r.buffer.push_str(text);
            }
        }
        if !complete {
            return None;
        }
        let reply = std::mem::take(&mut r.buffer).trim().to_owned();
        if reply.is_empty() {
            return None;
        }
        if r.spoken.len() > 64 {
            r.spoken.clear();
        }
        let seen = r.spoken.entry(conv.to_owned()).or_default();
        if seen.contains(&reply) {
            tracing::info!(conv, "duplicate reply for this conversation; not speaking it again");
            return None;
        }
        seen.push(reply.clone());
        Some(reply)
    }

    /// A reply becomes a one-step `speak` plan so TimelineExecutorService owns ducking.
    fn plan_speech(&self, conv: &str, text: &str) {
        let plan_id = uuid::Uuid::new_v4().to_string();
        self.emit(
            "plan.ready",
            json!({"timestamp": wall(), "plan_id": plan_id,
                "plan": {"plan_id": plan_id, "steps": [{"step_type": "speak", "text": text, "id": conv, "duration": null}]}}),
        );
    }
}

fn wall() -> f64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::deepgram::{DeepgramConfig, Stt};
    use crate::eleven::{ChunkRx, TtsBackend, TtsChunk};
    use crate::speaker::Speaker;
    use crate::voice::VoiceOptions;
    use r3x_audio::sink::NullSink;
    use r3x_bus::Bus;
    use tokio::sync::{mpsc, watch};

    struct Beep;
    impl TtsBackend for Beep {
        fn dialogue(&self, _: &str) -> Option<ChunkRx> {
            let (tx, rx) = mpsc::channel(4);
            let a = crate::eleven::Alignment { chars: vec!["h".into()], start_ms: vec![5.0], duration_ms: vec![40.0] };
            tx.try_send(Ok(TtsChunk { pcm: vec![3000; 2400], alignment: Some(a) })).unwrap();
            Some(rx)
        }
        fn http(&self, _: &str) -> ChunkRx {
            mpsc::channel(1).1
        }
    }

    #[tokio::test]
    async fn replies_become_speak_plans_and_speech_reports_back() {
        let (_e, engaged) = watch::channel(false);
        let stt = Stt::spawn(DeepgramConfig::new("k"), engaged);
        let speaker = Speaker::spawn(Arc::new(Beep), Arc::new(NullSink::new(1.0)), 30.0);
        let voice = Voice::new(Bus::default(), stt, speaker, None, VoiceOptions::default());
        let (tx, mut out) = mpsc::unbounded_channel::<(String, Value)>();
        let cv = CantinaVoice::attach(&voice, Arc::new(move |t: &str, p: Value| {
            let _ = tx.send((t.to_owned(), p));
        }));

        cv.on_tap("llm.response", &json!({"conversation_id": "c1", "text": "Hey ", "is_complete": false}));
        cv.on_tap("llm.response", &json!({"conversation_id": "c1", "text": "Hey there", "is_complete": true}));
        cv.on_tap("llm.response", &json!({"conversation_id": "c1", "text": "Hey there", "is_complete": true}));
        let (topic, plan) = out.recv().await.unwrap();
        assert_eq!(topic, "plan.ready");
        assert_eq!(plan["plan"]["steps"][0], json!({"step_type": "speak", "text": "Hey there", "id": "c1", "duration": null}));

        // The timeline's speak step comes back as a TTS request keyed by clip_id = turn id.
        cv.on_tap("tts.generate.request", &json!({"text": "Hey there", "clip_id": "c1", "step_id": "c1", "plan_id": "p1", "conversation_id": null}));
        let mut topics = Vec::new();
        loop {
            let (t, p) = out.recv().await.unwrap();
            if t == "speech.generation.started" {
                assert_eq!((p["conversation_id"].as_str(), p["clip_id"].as_str()), (Some("c1"), Some("c1")));
                assert!(p["audio_t0"].as_f64().unwrap() > 1.7e9);
            }
            if t == "speech.alignment" {
                assert_eq!(p["char_start_ms"], json!([5.0]));
            }
            topics.push(t.clone());
            if t == "speech.generation.complete" {
                assert_eq!((p["success"].as_bool(), p["plan_id"].as_str()), (Some(true), Some("p1")));
                break;
            }
        }
        assert_eq!(topics.first().map(String::as_str), Some("speech.generation.started"));
        assert!(topics.contains(&"speech.alignment".to_string()) && topics.contains(&"speech.synthesis.amplitude".to_string()));
        assert!(out.try_recv().is_err(), "the duplicate reply made no second plan");
    }
}

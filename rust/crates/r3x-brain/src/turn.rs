//! One turn: the Jev router and Claude race behind the dedup gate (`claude_service.py`,
//! `jev_intent_service.py`).
//!
//! - The turn id is the capture's (`conversation_id` on `listening_stopped`), adopted as is.
//! - Router side: classify; a dispatch resolves the gate with the verdict, runs the tool, then
//!   resolves the *outcome* (what actually started) so Claude narrates the result.
//! - Claude side: bounded wait on the verdict and outcome; an action taken becomes
//!   `<action_already_taken>` + `tool_choice: none` (tools stay in the request for the cache).
//! - Streaming: show tags stripped chunk by chunk; `tool_use` blocks dispatch the moment they
//!   close; the memory/verbal-feedback follow-up waits until the reply is in the session.
//! - One spoken reply: tags registered with the scheduler, then a one-step foreground plan
//!   (the executor owns ducking); a duplicate text for the same turn is dropped.

use r3x_contracts::{ConversationEvent, Event, ServiceStatus, Source};
use r3x_intent::ActionTaken;
use r3x_llm::prompt::{self, needs_verbal_feedback, VISION_TOOLS, VISUAL_ONLY_TOOLS};
use r3x_llm::{pyjson, Role, StreamEvent};
use serde_json::{Map, Value};
use tokio::task::JoinHandle;

use crate::plan::{Plan, Step};
use crate::tags::{extract_tags, TagParser};
use crate::Brain;

/// Recent turns folded into `<conversation_history>` when a person is present.
const HISTORY_TURNS: usize = 5;

impl Brain {
    pub(crate) fn start_turn(&self, turn: String, transcript: String) {
        if let Some(m) = &self.inner.memory {
            if let Err(e) = m.record_user(&transcript, Some(&turn)) {
                tracing::warn!("memory: {e}");
            }
        }
        if self.inner.router.active() {
            let (me, turn, t) = (self.clone(), turn.clone(), transcript.clone());
            tokio::spawn(async move { me.router_side(turn, t).await });
        }
        let me = self.clone();
        tokio::spawn(async move { me.claude_side(turn, transcript).await });
    }

    async fn router_side(&self, turn: String, transcript: String) {
        let out = self.inner.router.classify_turn(&transcript).await;
        let Some(action) = ActionTaken::from_decision(&out.decision, &transcript) else {
            self.inner.gate.resolve(&turn, None);
            return;
        };
        self.inner.gate.resolve(&turn, Some(action.clone()));
        let tool = action.intent_name.clone();
        self.publish_intent(&turn, &tool, Some(action.confidence), Source::Jev);
        let result = self.execute_tool(&tool, &action.parameters, Some(&turn), Source::Jev).await;
        // What actually happened, for the Claude side's <action_already_taken>.
        let outcome: Map<String, Value> = ["success", "track", "action", "message"]
            .iter()
            .filter_map(|k| result.get(*k).filter(|v| !v.is_null()).map(|v| (k.to_string(), v.clone())))
            .collect();
        self.inner.gate.resolve_outcome(&turn, outcome);
        self.publish_result(&turn, &tool, &action.parameters, &result, Source::Jev);
    }

    pub(crate) fn publish_intent(&self, turn: &str, tool: &str, confidence: Option<f64>, source: Source) {
        let e = ConversationEvent::IntentDetected { tool: tool.into(), confidence };
        self.inner.bus.publish(source, Some(turn.into()), Event::Conversation(e));
    }

    pub(crate) fn publish_result(&self, turn: &str, tool: &str, params: &Map<String, Value>, result: &Value, source: Source) {
        let success = result.get("success").and_then(Value::as_bool).unwrap_or(true);
        let e = ConversationEvent::ToolResult { tool: tool.into(), parameters: Value::Object(params.clone()), success, result: result.clone() };
        self.inner.bus.publish(source, Some(turn.into()), Event::Conversation(e));
    }

    async fn claude_side(&self, turn: String, transcript: String) {
        let Some(llm) = self.inner.llm.clone() else {
            tracing::warn!("no LLM configured; turn {turn} gets no reply");
            return;
        };
        let rc = self.inner.router.config().clone();
        // The router's verdict and "should R3X look?" come back together: two Jev requests in
        // parallel (the look one only while the camera is on), one bounded wait.
        let eyes = self.inner.vision.get().filter(|v| v.active()).cloned();
        let verdict = async {
            if self.inner.gate.enabled() {
                self.inner.gate.await_router(&turn, rc.verdict_wait, rc.outcome_wait).await
            } else {
                None
            }
        };
        let look = async {
            match &eyes {
                Some(_) if self.inner.router.active() => {
                    tokio::time::timeout(rc.verdict_wait, self.inner.router.should_look(&transcript)).await.unwrap_or(false)
                }
                _ => false,
            }
        };
        let (action, look) = tokio::join!(verdict, look);
        if let Some(a) = &action {
            tracing::info!(tool = a.intent_name, "fast router already acted; tools suppressed for this turn");
        }
        let image = match (look, &eyes) {
            (true, Some(e)) => match e.snapshot().await {
                Ok(jpeg) => Some(jpeg),
                Err(err) => {
                    tracing::warn!("look wanted but no frame: {err}");
                    None
                }
            },
            _ => None,
        };
        let scene = self.inner.vision.get().and_then(|v| v.scene());
        let ctx = match &self.inner.memory {
            Some(m) => m.turn_context(scene.as_ref().map(|(s, at)| (s.as_str(), *at)), HISTORY_TURNS).unwrap_or_default(),
            None => Default::default(),
        };
        let block = action.as_ref().map(ActionTaken::context_block);
        let msg = prompt::user_message(&transcript, block.as_deref(), &ctx);
        let history = {
            let mut s = self.inner.session.lock().unwrap();
            s.add(Role::User, msg.clone());
            s.messages()
        };
        let router_acted = action.is_some();
        let request = |text: String, image: Option<&String>| {
            prompt::turn_request(&self.inner.system, with_last_user(history.clone(), text, image), self.inner.tools.clone(), router_acted)
        };
        let req = match &image {
            Some(jpeg) => request(crate::look_turn_text(&msg), Some(jpeg)),
            None => request(msg.clone(), None),
        };
        let Some((mut m, mut dispatched)) = self.stream_reply(&llm, &turn, &req).await else { return };
        if image.is_some() && m.text().trim().is_empty() && dispatched.is_empty() && refused(&m).is_some() {
            tracing::warn!("Claude declined the camera frame ({}); answering without it", refused(&m).unwrap_or_default());
            let Some(again) = self.stream_reply(&llm, &turn, &request(crate::look_refused_text(&msg), None)).await else { return };
            (m, dispatched) = again;
        }
        let full = m.text();
        if !full.is_empty() || dispatched.is_empty() {
            self.inner.session.lock().unwrap().add(Role::Assistant, full.clone());
        }
        self.emit_reply(&turn, &full);
        for (tool, params, h) in dispatched {
            let result = h.await.unwrap_or_else(|e| serde_json::json!({"success": false, "error": e.to_string()}));
            self.publish_result(&turn, &tool, &params, &result, Source::Claude);
            let me = self.clone();
            let turn = turn.clone();
            tokio::spawn(async move { me.after_claude_tool(&turn, &tool, &params, &result).await });
        }
    }

    /// Stream one reply: text deltas out as they come (show tags stripped), each `tool_use`
    /// dispatched the moment it closes. `None` when the call failed.
    async fn stream_reply(
        &self,
        llm: &r3x_llm::LlmClient,
        turn: &str,
        req: &r3x_llm::MessagesRequest,
    ) -> Option<(r3x_llm::FinalMessage, Vec<(String, Map<String, Value>, JoinHandle<Value>)>)> {
        let mut stream = match llm.stream(req).await {
            Ok(s) => s,
            Err(e) => {
                tracing::error!("Claude turn failed: {e}");
                r3x_ops::report(&self.inner.bus, "brain", ServiceStatus::Degraded, Some(format!("Claude: {e}")));
                return None;
            }
        };
        let mut parser = TagParser::new(Some(self.inner.catalog.taggable.clone()));
        let mut dispatched: Vec<(String, Map<String, Value>, JoinHandle<Value>)> = Vec::new();
        let final_msg = loop {
            match stream.next().await {
                Some(Ok(StreamEvent::TextDelta(t))) => {
                    let clean = parser.feed(&t);
                    self.delta(turn, clean);
                }
                Some(Ok(StreamEvent::ToolUse(t))) => {
                    let params = t.input.as_object().cloned().unwrap_or_default();
                    self.publish_intent(turn, &t.name, None, Source::Claude);
                    let (me, tn, p, tool) = (self.clone(), turn.to_string(), params.clone(), t.name.clone());
                    let h = tokio::spawn(async move { me.execute_tool(&tool, &p, Some(&tn), Source::Claude).await });
                    dispatched.push((t.name, params, h));
                }
                Some(Ok(StreamEvent::Done(m))) => break Some(m),
                Some(Err(e)) => {
                    tracing::error!("Claude stream failed: {e}");
                    break None;
                }
                None => break None,
            }
        };
        let tail = parser.flush();
        self.delta(turn, tail);
        final_msg.map(|m| (m, dispatched))
    }

    fn delta(&self, turn: &str, text: String) {
        if !text.is_empty() {
            self.inner.bus.publish(Source::Claude, Some(turn.into()), Event::Conversation(ConversationEvent::ReplyDelta { text }));
        }
    }

    /// A complete reply (tags still in): strip + schedule tags, publish, remember, speak.
    pub(crate) fn emit_reply(&self, turn: &str, tagged: &str) {
        let (clean, tags, dropped) = extract_tags(tagged, Some(self.inner.catalog.taggable.clone()));
        for d in dropped {
            tracing::warn!("dropped show tag {d:?} (unknown id, not taggable, or over the limit)");
        }
        self.inner.tags.register(turn, &clean, tags);
        self.inner.bus.publish(Source::Claude, Some(turn.into()), Event::Conversation(ConversationEvent::Reply { text: clean.clone() }));
        if let Some(m) = &self.inner.memory {
            if let Err(e) = m.record_reply(&clean, Some(turn)) {
                tracing::warn!("memory: {e}");
            }
        }
        self.speak_reply(turn, &clean);
    }

    /// Route a reply through a one-step foreground plan; drop a text already spoken this turn.
    pub(crate) fn speak_reply(&self, turn: &str, text: &str) {
        let text = text.trim();
        if text.is_empty() {
            return;
        }
        {
            let mut spoken = self.inner.spoken.lock().unwrap();
            if spoken.len() > 64 {
                spoken.clear();
            }
            let seen = spoken.entry(turn.into()).or_default();
            if seen.iter().any(|t| t == text) {
                tracing::info!(turn, "duplicate reply text dropped");
                return;
            }
            seen.push(text.into());
        }
        let plan = Plan::new("foreground", vec![Step::Speak { text: text.into(), id: Some(turn.into()), reply: true }]);
        if let Err(e) = self.inner.exec.submit(plan) {
            tracing::error!("reply plan: {e}");
        }
    }

    /// Claude called a tool: record its result for the next turn and, unless the eyes or a
    /// routine speak for themselves, generate the verbal-feedback line.
    async fn after_claude_tool(&self, turn: &str, tool: &str, params: &Map<String, Value>, result: &Value) {
        if VISION_TOOLS.contains(&tool) {
            return self.after_vision(turn, result).await;
        }
        if VISUAL_ONLY_TOOLS.contains(&tool) {
            return;
        }
        let success = result.get("success").and_then(Value::as_bool).unwrap_or(true);
        let mut content = match result {
            Value::Null => "Action completed successfully.".to_string(),
            r => pyjson::dumps(r, false, false),
        };
        if !success {
            if let Some(msg) = result.get("error").or_else(|| result.get("message")).and_then(Value::as_str) {
                content = format!("Error: {msg}");
            }
        }
        self.inner.session.lock().unwrap().add(Role::User, format!("Tool execution result for {tool}: {content}"));
        if !needs_verbal_feedback(tool, false) {
            return;
        }
        let Some(llm) = self.inner.llm.clone() else { return };
        let req = prompt::verbal_feedback_request(self.inner.feedback_persona.as_deref(), tool, &Value::Object(params.clone()), result, success);
        let text = match llm.create(&req).await {
            Ok(m) if !m.text().is_empty() => m.text(),
            Ok(_) => "Action completed successfully.".into(),
            Err(e) => {
                tracing::error!("verbal feedback failed: {e}");
                "Action completed successfully.".into()
            }
        };
        self.emit_reply(turn, &text);
    }

    /// `analyze_scene` answered: Claude answers again with the camera frame attached, in the
    /// main persona (tools kept for the cache, not callable, so it cannot loop). This is the
    /// fallback for looks Jev did not flag; there is no separate describe call.
    ///
    /// R3X has already said "let me look", so this never ends in silence: a failed look (or a
    /// frame Claude declines) goes into the conversation as [`crate::vision_failure_note`] and
    /// Claude says so in character (the next turn knows the look failed instead of promising
    /// again); an empty or failed answer falls back to [`crate::VISION_FALLBACK_LINE`].
    async fn after_vision(&self, turn: &str, result: &Value) {
        let question = result.get("question").and_then(Value::as_str).unwrap_or("What do you see?").to_string();
        let jpeg = self.inner.looks.lock().unwrap().remove(turn).filter(|_| result.get("success").and_then(Value::as_bool) == Some(true));
        let failure = |reason: &str| {
            tracing::warn!(question, reason, "analyze_scene: look failed");
            crate::vision_failure_note(&question, reason)
        };
        let (session_text, request_text) = match &jpeg {
            Some(_) => (format!("[You looked through your camera to answer '{question}'.]"), crate::look_tool_text(&question)),
            None => {
                let reason = result
                    .get("message")
                    .or_else(|| result.get("error"))
                    .and_then(Value::as_str)
                    .filter(|m| !m.is_empty())
                    .unwrap_or("no camera frame");
                let note = failure(reason);
                (note.clone(), note)
            }
        };
        let history = {
            let mut s = self.inner.session.lock().unwrap();
            s.add(Role::User, session_text);
            s.messages()
        };
        let ask = |text: String, image: Option<&String>| {
            prompt::turn_request(&self.inner.system, with_last_user(history.clone(), text, image), self.inner.tools.clone(), true)
        };
        let text = match self.inner.llm.clone() {
            None => None,
            Some(llm) => {
                let mut reply = llm.create(&ask(request_text, jpeg.as_ref())).await;
                if let (Ok(m), Some(_)) = (&reply, &jpeg) {
                    if let Some(category) = refused(m).filter(|_| m.text().trim().is_empty()) {
                        let note = failure(&format!("Claude declined the camera image (refusal: {category})"));
                        reply = llm.create(&ask(note, None)).await;
                    }
                }
                match reply {
                    Ok(m) => Some(m.text()).filter(|t| !t.trim().is_empty()).or_else(|| {
                        tracing::warn!(stop_reason = ?m.stop_reason, "vision reply came back empty");
                        None
                    }),
                    Err(e) => {
                        tracing::error!("vision reply failed: {e}");
                        None
                    }
                }
            }
        };
        let text = text.unwrap_or_else(|| crate::VISION_FALLBACK_LINE.to_string());
        self.inner.session.lock().unwrap().add(Role::Assistant, text.clone());
        self.emit_reply(turn, &text);
    }
}

/// The request's history with its last user message replaced by `text`, carrying `image`
/// (base64 JPEG) when given. The session keeps its own text; images never enter it.
fn with_last_user(mut history: Vec<r3x_llm::Message>, text: String, image: Option<&String>) -> Vec<r3x_llm::Message> {
    if let Some(m) = history.iter_mut().rev().find(|m| m.role == Role::User) {
        m.content = text;
        m.images = image.map(|b64| vec![r3x_llm::Image { media_type: "image/jpeg".into(), data_b64: b64.clone() }]).unwrap_or_default();
    }
    history
}

/// A safety refusal (HTTP 200, `stop_reason: refusal`): its category, else "unspecified".
fn refused(m: &r3x_llm::FinalMessage) -> Option<String> {
    (m.stop_reason.as_deref() == Some("refusal"))
        .then(|| m.stop_details.as_ref().and_then(|d| d["category"].as_str()).unwrap_or("unspecified").to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_frame_rides_on_the_last_user_message_only() {
        let h = vec![r3x_llm::Message::user("earlier"), r3x_llm::Message::assistant("hi"), r3x_llm::Message::user("now")];
        let out = with_last_user(h, "look text".into(), Some(&"/9j/x".to_string()));
        assert_eq!((out[0].content.as_str(), out[0].images.len()), ("earlier", 0));
        assert_eq!((out[2].content.as_str(), out[2].images.len()), ("look text", 1));
        assert_eq!(out[2].images[0].media_type, "image/jpeg");
        let req = prompt::turn_request("sys", out, vec![], false);
        let body = serde_json::to_string(&req.to_body("claude-sonnet-5-5", "claude-sonnet-5-5", "low", true)).unwrap();
        assert!(body.contains(r#"{"type":"image","source":{"type":"base64","media_type":"image/jpeg","data":"/9j/x"}}"#), "{body}");
    }
}

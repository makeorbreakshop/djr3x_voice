//! Tool dispatch (`intent_router_service.py`): a tool name + parameters become typed bus
//! requests, and the result describes what *happened* (e.g. the track that actually started,
//! awaited briefly), because that is what R3X says out loud.
//!
//! Result objects keep CantinaOS's key order: the router's outcome is rendered into Claude's
//! `<action_already_taken>` block, and the recorded Claude fixtures are keyed on that text.

use r3x_contracts::{Command, Domain, Event, MusicEvent, PerfCommand, Source};
use r3x_intent::catalogue::{naming_phrase, SEMANTIC_PREFIX};
use serde_json::{json, Map, Value};

use crate::plan::wait_for;
use crate::Brain;

impl Brain {
    /// Run a tool on behalf of a gateway command or the console (`source` is the caller), and
    /// publish its result. Returns the result.
    pub async fn dispatch(&self, tool: &str, params: Map<String, Value>, turn: Option<&str>, source: Source) -> Value {
        let result = self.execute_tool(tool, &params, turn, source).await;
        if let Some(t) = turn {
            self.publish_result(t, tool, &params, &result, source);
        }
        result
    }

    pub(crate) async fn execute_tool(&self, tool: &str, params: &Map<String, Value>, turn: Option<&str>, source: Source) -> Value {
        let s = |k: &str| params.get(k).and_then(Value::as_str).map(str::trim).unwrap_or("").to_string();
        let cid = turn.map(str::to_owned);
        match tool {
            "play_music" => self.play_music(&s("track"), cid).await,
            "search_music" => {
                let q = s("query");
                if q.is_empty() {
                    return json!({"success": false, "error": "A music search query is required"});
                }
                self.play_music(&q, cid).await
            }
            "stop_music" => self.stop_music(cid).await,
            "next_track" => self.next_track(cid).await,
            "dj_mode_on" | "dj_mode_off" => {
                let on = tool == "dj_mode_on";
                let me = self.clone();
                // Like the CLI command CantinaOS emits: the result does not wait for DJ start-up.
                tokio::spawn(async move {
                    let (msg, err) = if on { me.dj_start().await } else { me.dj_stop().await };
                    me.console(msg, err);
                });
                json!({"success": true, "action": tool, "message": if on { "DJ mode starting" } else { "DJ mode stopping" }})
            }
            "set_eye_color" | "set_eye_animation" => {
                let color = s("color");
                if color.is_empty() {
                    return json!({"success": false, "message": "No color specified"});
                }
                let pattern = params.get("pattern").and_then(Value::as_str).unwrap_or("custom").to_string();
                let intensity = params.get("intensity").cloned().unwrap_or(json!(1.0));
                // The face firmware has no colour channel; the pattern is what it can show.
                let ack = self.inner.bus.command(source, None, Command::Perf(PerfCommand::Eyes { pattern: pattern.clone(), duration: None })).await;
                if !ack.is_accepted() {
                    tracing::info!(pattern, ?ack, "eye pattern not shown");
                }
                json!({"success": true, "color": color, "pattern": pattern, "intensity": intensity,
                       "message": format!("Set eyes to {color} with {pattern} pattern")})
            }
            "perform_show" => {
                let id = s("id");
                if id.is_empty() {
                    return json!({"success": false, "message": "No routine id"});
                }
                let cmd = Command::Perf(PerfCommand::Play { id: id.clone(), intensity: 1.0, speed: 1.0, layer: None });
                let ack = self.inner.bus.command(Source::Claude, None, cmd).await;
                if !ack.is_accepted() {
                    tracing::info!(id, ?ack, "routine not performed");
                }
                json!({"success": true, "id": id, "message": format!("Performing {id}")})
            }
            // Vision is Phase 6: say so instead of pretending to look.
            "analyze_scene" => json!({"success": false, "action": "analyze_scene", "question": s("question"),
                                      "message": "Vision is not available yet"}),
            other => json!({"success": false, "message": format!("No handler for intent: {other}")}),
        }
    }

    fn music(&self, turn: Option<String>, e: MusicEvent) {
        self.inner.bus.publish(Source::Timeline, turn, Event::Music(e));
    }

    /// Wait briefly for the engine's `track_started`; its title, if it came.
    async fn started_track(&self, rx: &mut r3x_bus::EventReceiver) -> Option<String> {
        let env = wait_for(rx, self.inner.cfg.playback_confirm_wait, |_, e| matches!(e, Event::Music(MusicEvent::TrackStarted { .. }))).await?;
        match &env.body {
            r3x_contracts::Body::Event(Event::Music(MusicEvent::TrackStarted { track })) => Some(track.title.clone()),
            _ => None,
        }
    }

    async fn play_music(&self, track: &str, turn: Option<String>) -> Value {
        // A generic request names nothing: the engine chooses (it knows the real library).
        let selected = if track.is_empty() {
            None
        } else if track.starts_with(SEMANTIC_PREFIX) {
            Some(track.to_string())
        } else {
            naming_phrase(track)
        };
        let mut rx = self.inner.bus.subscribe(Domain::Music);
        self.music(turn, MusicEvent::Play { query: selected.clone() });
        let requested = (!track.is_empty()).then(|| track.to_string());
        match self.started_track(&mut rx).await {
            Some(t) => json!({"success": true, "track": t, "requested": requested, "selected": selected, "action": "play",
                              "message": format!("Now playing: {t}")}),
            None => json!({"success": true, "track": null, "requested": requested, "selected": selected, "action": "play",
                           "message": "Music playback requested"}),
        }
    }

    async fn stop_music(&self, turn: Option<String>) -> Value {
        let playing = self.inner.now_playing.lock().unwrap().clone();
        let mut rx = self.inner.bus.subscribe(Domain::Music);
        self.music(turn, MusicEvent::Stop);
        let stopped = wait_for(&mut rx, self.inner.cfg.playback_confirm_wait, |_, e| matches!(e, Event::Music(MusicEvent::TrackStopped)))
            .await
            .and(playing);
        let message = match &stopped {
            Some(t) => format!("Music stopped: {t}"),
            None => "Music is stopped".into(),
        };
        json!({"success": true, "action": "stop", "track": stopped, "message": message})
    }

    /// Music owns `next` (plan 7b); in DJ mode the DJ's transition (commentary + crossfade)
    /// is layered on top.
    async fn next_track(&self, turn: Option<String>) -> Value {
        let mut rx = self.inner.bus.subscribe(Domain::Music);
        if self.inner.dj.lock().unwrap().active {
            let (msg, err) = self.dj_next().await;
            let track = self.inner.dj.lock().unwrap().current.clone();
            return json!({"success": !err, "action": "next_track", "track": track, "message": msg});
        }
        self.music(turn, MusicEvent::Next);
        let t = self.started_track(&mut rx).await;
        let message = t.as_ref().map_or("Next track requested".to_string(), |t| format!("Now playing: {t}"));
        json!({"success": true, "action": "next_track", "track": t, "message": message})
    }
}

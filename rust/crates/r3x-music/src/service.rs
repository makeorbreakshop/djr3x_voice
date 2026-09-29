//! The engine on the r3x bus: `state.music` (with the beat-clock anchor), `music.*` events,
//! the brain's music requests, `IntentCommand::Music`, and sfx (show `perf.sfx` cues and the
//! mode-change ding) on the sfx bus.

use std::path::PathBuf;
use std::sync::Arc;
use std::time::Instant;

use r3x_audio::sfx::SfxBank;
use r3x_bus::{Bus, Received};
use r3x_contracts::{Ack, Body, Domain, EngagementState, Event, MusicCommand, MusicEvent, MusicState, PerfEvent, Source};

use crate::engine::{Engine, EngineEvent, Reply, Status};

/// Engine outputs are stamped `System`.
const SRC: Source = Source::System;

fn music_state(st: &Status, library: Vec<String>, bus: &Bus) -> MusicState {
    let t_now = bus.clock().t_mono();
    let age = Instant::now().saturating_duration_since(st.at).as_secs_f64();
    MusicState {
        playing: st.playing,
        track: st.track.as_ref().map(|t| t.to_contract()),
        volume: if st.ducked { st.duck_level as f64 } else { 1.0 },
        ducked: st.ducked,
        library,
        position_s: st.position_s,
        position_t: t_now - age,
        paused: st.paused,
    }
}

fn to_ack(r: Reply) -> Ack {
    r.map_or_else(Ack::rejected, |_| Ack::Accepted)
}

/// A typed music command (the bridge / intent handler forwards `IntentCommand::Music` here).
pub async fn command(engine: &Engine, source: Source, cmd: MusicCommand) -> Ack {
    let src = format!("{source:?}").to_ascii_lowercase();
    to_ack(match cmd {
        MusicCommand::Play { query } => engine.play(query, &src).await,
        MusicCommand::Stop => engine.stop().await,
        MusicCommand::Next => engine.next(&src).await,
    })
}

/// Publish engine state/events on `bus` and serve music requests published there.
pub fn spawn(bus: &Bus, engine: &Engine) {
    // state.music
    {
        let (bus, engine) = (bus.clone(), engine.clone());
        tokio::spawn(async move {
            let mut w = engine.status();
            let mut ev = engine.subscribe();
            let sorted = |e: &Engine| {
                let mut v = e.library().keys();
                v.sort();
                v
            };
            let mut library = sorted(&engine);
            loop {
                let st = w.borrow_and_update().clone();
                bus.set(SRC, music_state(&st, library.clone(), &bus));
                tokio::select! {
                    r = w.changed() => if r.is_err() { break },
                    e = ev.recv() => match e {
                        Ok(EngineEvent::Library { .. }) => library = sorted(&engine),
                        Ok(e) => publish(&bus, e),
                        Err(tokio::sync::broadcast::error::RecvError::Lagged(_)) => {}
                        Err(_) => break,
                    },
                }
            }
        });
    }
    // requests from the brain (r3x-brain plans and tools publish these)
    {
        let (bus, engine) = (bus.clone(), engine.clone());
        let mut rx = bus.subscribe(Domain::Music);
        tokio::spawn(async move {
            while let Some(m) = rx.recv().await {
                let Received::Message(env) = m else { continue };
                let Body::Event(Event::Music(e)) = &env.body else { continue };
                let src = format!("{:?}", env.source).to_ascii_lowercase();
                let engine = engine.clone();
                let e = e.clone();
                tokio::spawn(async move {
                    let r = match e {
                        MusicEvent::Play { query } => engine.play(query, &src).await,
                        MusicEvent::Stop => engine.stop().await,
                        MusicEvent::Next => engine.next(&src).await,
                        MusicEvent::Crossfade { track, duration_s, id } => engine.crossfade(&track, duration_s, &id, &src).await,
                        MusicEvent::Duck { level, .. } => engine.duck(Some(level as f32)).await,
                        MusicEvent::Unduck { .. } => engine.unduck().await,
                        _ => return, // our own outputs
                    };
                    if let Err(err) = r {
                        tracing::info!("music request: {err}");
                    }
                });
            }
        });
    }
}

fn publish(bus: &Bus, e: EngineEvent) {
    let ev = match e {
        EngineEvent::Started { track, .. } => MusicEvent::TrackStarted { track: track.to_contract() },
        EngineEvent::Stopped { track: Some(_) } | EngineEvent::Finished { .. } => MusicEvent::TrackStopped,
        EngineEvent::EndingSoon { remaining_s, .. } => MusicEvent::TrackEndingSoon { remaining_s },
        EngineEvent::Ducked { level } => MusicEvent::Ducked { level: level as f64 },
        EngineEvent::Unducked => MusicEvent::Unducked,
        EngineEvent::CrossfadeComplete { id, .. } => MusicEvent::CrossfadeComplete { id },
        _ => return,
    };
    bus.publish(SRC, None, Event::Music(ev));
}

/// The mode-change ding (CantinaOS `mode_change_sound`): `R3X_MODE_SOUND`, else
/// `audio/startours_audio/startours_ding.mp3` in the repo.
pub fn default_mode_sound() -> PathBuf {
    std::env::var_os("R3X_MODE_SOUND").filter(|v| !v.is_empty()).map(PathBuf::from).unwrap_or_else(|| {
        r3x_beats::abspath(std::path::Path::new(concat!(env!("CARGO_MANIFEST_DIR"), "/../../../audio/startours_audio/startours_ding.mp3")))
    })
}

/// Show sfx kit folders: `R3X_SFX_DIR`, else the sim's `sim/web/public/sfx`.
pub fn default_sfx_dirs() -> Vec<PathBuf> {
    match std::env::var_os("R3X_SFX_DIR").filter(|v| !v.is_empty()) {
        Some(d) => std::env::split_paths(&d).collect(),
        None => vec![r3x_beats::abspath(std::path::Path::new(concat!(env!("CARGO_MANIFEST_DIR"), "/../../../sim/web/public/sfx")))],
    }
}

/// Play `perf.sfx` cues and the mode-change ding on the sfx bus (plan §7b: audible with no
/// sim open). Decoding happens off the runtime; a missing file is logged, never fatal.
pub fn spawn_sfx(bus: &Bus, bank: Arc<SfxBank>, mode_sound: Option<PathBuf>) {
    {
        let bank = bank.clone();
        let mut rx = bus.subscribe(Domain::Perf);
        tokio::spawn(async move {
            while let Some(m) = rx.recv().await {
                let Received::Message(env) = m else { continue };
                if let Body::Event(Event::Perf(PerfEvent::Sfx { id })) = &env.body {
                    let (bank, id) = (bank.clone(), id.clone());
                    tokio::task::spawn_blocking(move || match bank.play(&id, 1.0) {
                        Ok(p) => tracing::debug!(sfx = %id, file = %p.display(), "sfx"),
                        Err(e) => tracing::info!("sfx {id}: {e}"),
                    });
                }
            }
        });
    }
    if let Some(ding) = mode_sound.filter(|p| p.is_file()) {
        let mut w = bus.watch::<EngagementState>();
        tokio::spawn(async move {
            let mut prev = w.borrow_and_update().engagement;
            while w.changed().await.is_ok() {
                let cur = w.borrow_and_update().engagement;
                if cur == prev {
                    continue;
                }
                prev = cur;
                let (bank, ding) = (bank.clone(), ding.clone());
                tokio::task::spawn_blocking(move || {
                    if let Err(e) = bank.play(&ding.to_string_lossy(), 1.0) {
                        tracing::info!("mode sound: {e}");
                    }
                });
            }
        });
    }
}

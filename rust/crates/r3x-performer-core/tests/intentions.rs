//! The intention pools (`show/intentions.json`): rich enough that R3X never looks canned,
//! and every pick performable. Listen reactions fire over the listening idle while the guest
//! talks, so they are held to a tighter contract: small, short, head and visor only, inside
//! the channel limits even at the picker's fastest, strongest pick.

mod common;

use common::{repo, show_catalog};
use r3x_contracts::RobotProfile;
use r3x_performer_core::performer::{Command, Out, PerformerConfig};
use r3x_performer_core::show::body::{BodyCompositor, PlayRequest, Pose};
use r3x_performer_core::show::catalog::Catalog;
use r3x_performer_core::show::curve::track_peaks;
use r3x_performer_core::show::expand::children;
use r3x_performer_core::show::intentions::IntentKind;
use r3x_performer_core::show::lint::joint_limits;
use r3x_performer_core::show::player::RunLayer;
use r3x_performer_core::show::types::{Body, Clip, Source, TrackMode};
use r3x_performer_core::Performer;
use std::collections::BTreeSet;
use std::sync::Arc;

/// The picker's extremes (`show::intentions`): intensity x0.8-1.05, speed x0.9-1.12.
const PICK_INTENSITY_MAX: f64 = 1.05;
const PICK_SPEEDS: [f64; 2] = [0.9, 1.12];
/// Clips authored to touch a soft limit at intensity 1, frozen by the parity traces
/// (`tests/parity/perf_*.json`); above intensity 1 the servo pulse clamp holds them.
const AT_THE_LIMIT_BY_DESIGN: [&str; 1] = ["arm_throw"];

/// The clips an item plays: itself, or every clip under a cue / sequence.
fn clips_of(cat: &Catalog, id: &str) -> Vec<Arc<Clip>> {
    let Some(it) = cat.get(id) else { return vec![] };
    match &it.body {
        Body::Clip(c) => vec![c.clone()],
        _ => children(it)
            .into_iter()
            .flat_map(|(_, c)| clips_of(cat, c))
            .collect(),
    }
}

#[test]
fn every_intention_pool_is_rich_enough_to_vary() {
    let cat = show_catalog();
    assert!(cat.errors.is_empty(), "{:?}", cat.errors);
    for i in cat.intentions.values() {
        let min = match i.kind {
            IntentKind::Mood | IntentKind::Listen => 4,
            IntentKind::Gesture | IntentKind::Beat | IntentKind::Look => 3,
        };
        let distinct: BTreeSet<&String> = i.pool.iter().collect();
        assert_eq!(distinct.len(), i.pool.len(), "{}: a pool entry twice", i.id);
        assert!(i.pool.len() >= min, "{} ({}): {} in the pool, want >= {min}", i.id, i.kind.as_str(), i.pool.len());
    }
}

#[test]
fn listen_reactions_are_small_short_head_only_and_hold_at_the_fastest_pick() {
    let cat = show_catalog();
    let limits = joint_limits();
    // Per joint, how far a backchannel may move (deg; mm for head_lift), as (lo, hi).
    // Listening already holds the visor open at -8 (limit -12) and a 4-8 deg roll cant
    // toward the guest (limit 10), so those two get the least room.
    let cap = |j: &str| match j {
        "head_tilt" | "head_pan" => Some((-5.0, 5.0)),
        "head_roll" => Some((-1.5, 1.5)),
        "head_lift" => Some((-4.0, 4.0)),
        "visor" => Some((-3.0, 8.0)),
        _ => None,
    };
    let s = *PICK_SPEEDS.last().unwrap();
    for i in cat.intentions.values().filter(|i| i.kind == IntentKind::Listen) {
        for item in &i.pool {
            let clips = clips_of(&cat, item);
            assert!(!clips.is_empty(), "{}: {item} plays no clip", i.id);
            for c in clips {
                let w = format!("{} > {}", i.id, c.id);
                assert!((0.4..=1.2).contains(&c.duration), "{w}: {} s, want 0.4-1.2 s", c.duration);
                for (j, tr) in &c.tracks {
                    let (lo, hi) = cap(j).unwrap_or_else(|| panic!("{w}: {j} is not a head or visor joint"));
                    assert_eq!(tr.mode, TrackMode::Additive, "{w}.{j}: listen reactions add to the listening pose");
                    for [t, v] in &tr.keys {
                        assert!((lo..=hi).contains(v), "{w}.{j}: {v} at t={t} outside {lo}..{hi}");
                    }
                    let pk = track_peaks(tr);
                    let lim = &limits[j];
                    let (v, a) = (pk.v * s * PICK_INTENSITY_MAX, pk.a * s * s * PICK_INTENSITY_MAX);
                    assert!(v <= lim.v_max && a <= lim.a_max, "{w}.{j}: v {v:.0}/{} a {a:.0}/{} at speed {s}", lim.v_max, lim.a_max);
                }
            }
        }
    }
}

/// Every clip any intention can pick, played on the body compositor from rest at the picker's
/// extremes: it starts at rest, stays inside the soft joint ranges, and ends back at rest.
#[test]
fn every_pool_clip_starts_ends_at_rest_and_stays_inside_the_joint_ranges() {
    let cat = show_catalog();
    let limits = joint_limits();
    let ids: BTreeSet<&String> = cat.intentions.values().flat_map(|i| i.pool.iter()).collect();
    let mut played = 0;
    let mut bad = BTreeSet::new();
    for id in ids {
        for clip in clips_of(&cat, id) {
            for speed in PICK_SPEEDS {
                let mut b = BodyCompositor::new();
                b.play(PlayRequest {
                    run_id: "r".into(),
                    clip: clip.clone(),
                    intensity: Some(if AT_THE_LIMIT_BY_DESIGN.contains(&clip.id.as_str()) { 1.0 } else { PICK_INTENSITY_MAX }),
                    speed: Some(speed),
                    layer: RunLayer::Gesture,
                    owns: None,
                    t0: 0.0,
                });
                let end = clip.duration / speed + 0.5; // + the longest blend-out, with room
                let mut t = 0.0;
                while t <= end + 1e-9 {
                    let mut p = Pose::new();
                    b.apply(&mut p, t, None);
                    for (j, v) in &p {
                        let lim = &limits[j];
                        if *v < lim.lo - 1e-6 || *v > lim.hi + 1e-6 {
                            bad.insert(format!("{id} > {}.{j} leaves {:.1}..{:.1}", clip.id, lim.lo, lim.hi));
                        }
                        if (t == 0.0 || t >= end - 1e-9) && v.abs() >= 1e-3 {
                            bad.insert(format!("{id} > {}.{j} not at rest at t={t:.3}", clip.id));
                        }
                    }
                    t += 1.0 / 240.0;
                }
                played += 1;
            }
        }
    }
    assert!(bad.is_empty(), "at the picker's extremes (intensity {PICK_INTENSITY_MAX}):\n  {}", bad.iter().cloned().collect::<Vec<_>>().join("\n  "));
    assert!(played > 100, "{played}");
}

/// The real performer, listening: every listen intention fires, plays, and keeps the composed
/// targets inside the joint ranges on top of the listening pose.
#[test]
fn listen_intentions_play_over_the_listening_pose_inside_the_joint_ranges() {
    let profile = RobotProfile::load(repo("profiles/r3x/robot.json")).unwrap();
    let mut p = Performer::new(Arc::new(show_catalog()), &profile, PerformerConfig::default()).unwrap();
    let limits = joint_limits();
    let cat = show_catalog();
    let listen: Vec<String> = cat.intentions.values().filter(|i| i.kind == IntentKind::Listen).map(|i| i.id.clone()).collect();
    p.command(Command::ListeningStarted);
    let mut t = 0.0;
    let mut started = 0;
    for round in 0..4 {
        for id in &listen {
            p.command(Command::Intend { id: id.clone(), source: Source::Jev, intensity: 1.0 });
            if p.take_events().iter().any(|o| matches!(o, Out::Started { .. })) {
                started += 1;
            }
            let until = t + 6.0; // past every listen cooldown (<= 5 s)
            while t < until {
                let f = p.tick(t);
                for (j, v) in &f.targets {
                    let Some(lim) = limits.get(j) else { continue };
                    assert!(*v >= lim.lo - 1e-6 && *v <= lim.hi + 1e-6, "{id} (round {round}): {j} = {v:.2} at t={t:.2} outside {:.1}..{:.1}", lim.lo, lim.hi);
                }
                t += 1.0 / 60.0;
            }
            p.take_events();
        }
    }
    assert_eq!(started, 4 * listen.len(), "every listen intention plays every time");
}

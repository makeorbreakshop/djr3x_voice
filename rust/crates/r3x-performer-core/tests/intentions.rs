//! The intention pools (`show/intentions.json`): rich enough that R3X never looks canned,
//! and every pick performable. Listen reactions fire over the listening idle while the guest
//! talks, so they are held to a tighter contract: small, short, head and visor only, inside
//! the channel limits even at the picker's fastest, strongest pick.
//!
//! Limits are the library's (`lint::library_limits`): positions inside the range both rigs
//! share, velocity and acceleration inside the Physical build's, checked at the picker's
//! extremes ([`PICK_INTENSITY`]`.1`, [`PICK_SPEED`]`.1`): velocity scales with intensity x
//! speed, acceleration with intensity x speed^2.

mod common;

use common::{library_limits, repo, show_catalog};
use r3x_contracts::RobotProfile;
use r3x_performer_core::performer::{Command, Out, PerformerConfig};
use r3x_performer_core::show::body::{BodyCompositor, PlayRequest, Pose};
use r3x_performer_core::show::catalog::Catalog;
use r3x_performer_core::show::curve::track_peaks;
use r3x_performer_core::show::expand::children;
use r3x_performer_core::show::intentions::{IntentKind, PICK_INTENSITY, PICK_SPEED};
use r3x_performer_core::show::player::RunLayer;
use r3x_performer_core::show::types::{clamp_intensity, clamp_speed, Action, Body, Clip, Entry, Source, TrackMode};
use r3x_performer_core::Performer;
use std::collections::BTreeSet;
use std::sync::Arc;

/// The picker's extremes (`show::intentions`): the strongest pick and both speed ends (the
/// slowest pick holds a clip longest, the fastest one is the hardest on the servos).
const PICK_INTENSITY_MAX: f64 = PICK_INTENSITY.1;
const PICK_SPEEDS: [f64; 2] = [PICK_SPEED.0, PICK_SPEED.1];

/// Every clip an item plays, with the intensity and speed it plays at when the item runs at
/// `(i, s)`: a cue's or sequence's own clip params multiply the run's (`player.rs`).
fn played(cat: &Catalog, id: &str, i: f64, s: f64, out: &mut Vec<(Arc<Clip>, f64, f64)>) {
    let Some(it) = cat.get(id) else { return };
    let entries = match &it.body {
        Body::Clip(c) => return out.push((c.clone(), clamp_intensity(i), clamp_speed(s))),
        Body::Cue(c) => &c.actions,
        Body::Sequence(q) => &q.track,
    };
    for e in entries {
        match &e.entry {
            Entry::Act(Action::Clip { id, intensity, speed }) => {
                played(cat, id, i * intensity.unwrap_or(1.0), s * speed.unwrap_or(1.0), out)
            }
            Entry::Ref(_, id) => played(cat, id, i, s, out),
            _ => {}
        }
    }
}

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
    let limits = library_limits();
    // Per joint, how far a backchannel may move (deg; mm for head_lift), as (lo, hi).
    // Listening already holds the visor open at -8 (limit -12) and a 4-8 deg roll cant
    // toward the guest (limit 10), so those two get the least room. The visor may drop
    // further: down is the brow's whole story (a squint, a frown, a blink).
    let cap = |j: &str| match j {
        "head_tilt" | "head_pan" => Some((-7.0, 7.0)),
        "head_roll" => Some((-1.5, 1.5)),
        "head_lift" => Some((-5.0, 5.0)),
        "visor" => Some((-3.0, 12.0)),
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
    let limits = library_limits();
    let ids: BTreeSet<&String> = cat.intentions.values().flat_map(|i| i.pool.iter()).collect();
    let mut played = 0;
    let mut bad = BTreeSet::new();
    let mut clamped = BTreeSet::new();
    for id in ids {
        for clip in clips_of(&cat, id) {
            for speed in PICK_SPEEDS {
                let mut b = BodyCompositor::new();
                b.play(PlayRequest {
                    run_id: "r".into(),
                    clip: clip.clone(),
                    intensity: Some(PICK_INTENSITY_MAX),
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
                        } else if *v < lim.warn_lo - 1e-6 || *v > lim.warn_hi + 1e-6 {
                            // inside the Original rig: the Physical rig's performer clamps it (a warning)
                            clamped.insert(format!("{id} > {}.{j} past the Physical {:.1}..{:.1}", clip.id, lim.warn_lo, lim.warn_hi));
                        } else if let Some(c) = &lim.coupling {
                            let a = p.get(&c.depends_on).copied().unwrap_or(0.0);
                            if let Some((lo, hi)) = c.range_at(a) {
                                if *v < lo - 1e-6 || *v > hi + 1e-6 {
                                    clamped.insert(format!("{id} > {}.{j} past the coupled {lo:.1}..{hi:.1} at {} {a:+.1}", clip.id, c.depends_on));
                                }
                            }
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
    if !clamped.is_empty() {
        eprintln!("the Physical rig clamps (warnings):\n  {}", clamped.iter().cloned().collect::<Vec<_>>().join("\n  "));
    }
    assert!(bad.is_empty(), "at the picker's extremes (intensity {PICK_INTENSITY_MAX}):\n  {}", bad.iter().cloned().collect::<Vec<_>>().join("\n  "));
    assert!(played > 100, "{played}");
}

/// Every clip a pick can play, at the strongest, fastest pick (with a cue's or sequence's own
/// clip params on top), stays inside the Physical build's velocity and acceleration; so does
/// every clip the idle policy and the shows play, at their own params.
#[test]
fn every_clip_at_its_extreme_play_stays_inside_the_physical_speed_limits() {
    let cat = show_catalog();
    let limits = library_limits();
    let mut plays = vec![];
    for i in cat.intentions.values() {
        for item in &i.pool {
            played(&cat, item, PICK_INTENSITY_MAX, PICK_SPEED.1, &mut plays);
        }
    }
    for it in cat.items.values().filter(|it| !matches!(it.body, Body::Clip(_))) {
        played(&cat, &it.id, 1.0, 1.0, &mut plays);
    }
    let mut bad = BTreeSet::new();
    for (clip, i, s) in &plays {
        for (j, tr) in &clip.tracks {
            let pk = track_peaks(tr);
            let lim = &limits[j];
            let (v, a) = (pk.v * i * s, pk.a * i * s * s);
            if v > lim.v_max + 1e-6 || a > lim.a_max + 1e-6 {
                bad.insert(format!("{}.{j} at x{i:.2} intensity, x{s:.2} speed: v {v:.0}/{} a {a:.0}/{}", clip.id, lim.v_max, lim.a_max));
            }
        }
    }
    assert!(bad.is_empty(), "{}", bad.iter().cloned().collect::<Vec<_>>().join("\n  "));
    assert!(plays.len() > 150, "{}", plays.len());
}

/// The real performer, listening: every listen intention fires, plays, and keeps the composed
/// targets inside the joint ranges on top of the listening pose.
#[test]
fn listen_intentions_play_over_the_listening_pose_inside_the_joint_ranges() {
    let profile = RobotProfile::load(repo("profiles/r3x/robot.json")).unwrap();
    let mut p = Performer::new(Arc::new(show_catalog()), &profile, PerformerConfig::default()).unwrap();
    let limits = library_limits();
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

//! Port of `sim/web/test/stagelights.test.ts` and `tempo.test.ts`.

use r3x_performer_core::stagelights::{
    flux, resolve_cue, rig, rigs, Level, LightMode, Output, Rgb, StageLights, GROUPS,
};
use r3x_performer_core::tempo::track_bpm;
use serde_json::json;

fn close(a: Rgb, b: Rgb, eps: f64) -> bool {
    a.iter().zip(b).all(|(x, y)| (x - y).abs() < eps)
}
fn lerp(a: Rgb, b: Rgb, t: f64) -> Rgb {
    [0, 1, 2].map(|i| a[i] + (b[i] - a[i]) * t)
}
fn g(name: &str) -> usize {
    GROUPS.iter().position(|x| *x == name).unwrap()
}
fn vc(name: &str) -> Output {
    resolve_cue(rig("venue_cycle").unwrap(), name).unwrap()
}
fn venue(cue: Option<&str>) -> StageLights {
    StageLights::new(Some("venue_cycle"), cue, None)
}
fn list(mode: LightMode) -> Vec<&'static str> {
    rig("venue_cycle").unwrap().mode(mode).list.clone().unwrap()
}

#[test]
fn cues_resolve_against_the_rig_base_aliases_fan_out() {
    let c = vc("blue_white");
    assert!(!close(c[g("rack_wash_l")], c[g("rack_wash_r")], 1e-9)); // split cue
    assert!(close(c[g("wall_wash_l")], c[g("wall_wash_r")], 1e-9)); // `walls` sets both
    assert!(close(
        c[g("practicals")],
        flux(Level {
            color: 0xffffff,
            level: 1.0
        }),
        1e-9
    ));
}

#[test]
fn every_cue_in_every_rig_resolves_with_finite_non_negative_flux() {
    for r in rigs() {
        for (name, _) in &r.cues {
            for v in resolve_cue(r, name).unwrap().iter().flatten() {
                assert!(*v >= 0.0 && v.is_finite());
            }
        }
        for m in &r.modes {
            for n in m.list.iter().flatten() {
                assert!(r.cue(n).is_some());
            }
        }
        assert!(r.cue(r.initial).is_some());
    }
}

#[test]
fn flux_decodes_srgb_hex_to_linear_light_times_the_dimmer() {
    assert!(close(
        flux(Level {
            color: 0xffffff,
            level: 0.5
        }),
        [0.5; 3],
        1e-9
    ));
    let [r, gr, _] = flux(Level {
        color: 0x808000,
        level: 1.0,
    });
    assert!((r - 0.2158605).abs() < 1e-6 && (gr - 0.2158605).abs() < 1e-6); // sRGB 128 -> linear
}

#[test]
fn crossfade_interpolates_linearly_in_light() {
    let mut l = venue(Some("cool_white"));
    let a = l.out;
    let b = vc("orange_red");
    l.go_cue("orange_red", 2.0);
    l.update(0.5);
    (0..11).for_each(|i| assert!(close(l.out[i], lerp(a[i], b[i], 0.25), 1e-9)));
    l.update(0.5);
    (0..11).for_each(|i| assert!(close(l.out[i], lerp(a[i], b[i], 0.5), 1e-9)));
    assert!((l.fade() - 0.5).abs() < 1e-9);
}

#[test]
fn an_interrupted_fade_restarts_from_where_the_light_is() {
    let mut l = venue(Some("cool_white"));
    l.go_cue("orange_red", 2.0);
    l.update(1.0);
    let mid = l.out;
    l.go_cue("blue_white", 1.0);
    (0..11).for_each(|i| assert!(close(l.out[i], mid[i], 1e-9)));
    l.update(0.5);
    let b = vc("blue_white");
    (0..11).for_each(|i| assert!(close(l.out[i], lerp(mid[i], b[i], 0.5), 1e-9)));
}

#[test]
fn fade_0_snaps() {
    let mut l = venue(Some("cool_white"));
    l.go_cue("amber", 0.0);
    let b = vc("amber");
    (0..11).for_each(|i| assert!(close(l.out[i], b[i], 1e-9)));
}

#[test]
fn a_fade_never_overshoots_however_large_the_step() {
    let mut l = venue(Some("cool_white"));
    let (a, b) = (l.out, vc("blue_white"));
    l.go_cue("blue_white", 1.0);
    l.update(1000.0);
    assert_eq!(l.fade(), 1.0);
    for i in 0..11 {
        assert!(close(l.out[i], b[i], 1e-9));
        for k in 0..3 {
            assert!(
                l.out[i][k] <= a[i][k].max(b[i][k]) + 1e-12
                    && l.out[i][k] >= a[i][k].min(b[i][k]) - 1e-12
            );
        }
    }
}

#[test]
fn a_chase_lands_on_the_cue_the_elapsed_time_implies() {
    let mut l = venue(None);
    l.set_bpm(120.0); // music steps every 2 bars = 8 beats = 4 s
    l.set_mode(LightMode::Music, None);
    let list = list(LightMode::Music);
    let start = list.iter().position(|c| *c == l.cue()).unwrap();
    l.update(4.0 * 7.0 + 1.0);
    assert_eq!(l.cue(), list[(start + 7) % list.len()]);
    assert_eq!(l.fade(), 1.0);
    let b = vc(l.cue());
    (0..11).for_each(|i| assert!(close(l.out[i], b[i], 1e-9)));
}

#[test]
fn non_positive_or_non_finite_dt_is_ignored() {
    let mut l = StageLights::default();
    let v = l.version;
    l.update(0.0);
    l.update(-1.0);
    l.update(f64::NAN);
    assert_eq!(l.version, v);
}

#[test]
fn steps_the_chase_every_bars_bars_from_the_internal_clock() {
    let mut l = venue(None);
    l.set_bpm(96.0); // 2 bars = 8 beats = 5 s
    l.set_mode(LightMode::Music, None);
    let list = list(LightMode::Music);
    let start = list.iter().position(|c| *c == l.cue()).unwrap();
    let dt = 1.0 / 60.0;
    let (mut t, mut changes, mut cur) = (0.0, vec![], l.cue());
    while t < 16.0 {
        l.update(dt);
        t += dt;
        if l.cue() != cur {
            changes.push(t);
            cur = l.cue();
        }
    }
    assert_eq!(changes.len(), 3);
    for (i, c) in changes.iter().enumerate() {
        assert!((c - 5.0 * (i + 1) as f64).abs() < dt * 1.01);
    }
    assert_eq!(l.cue(), list[(start + 3) % list.len()]);
}

#[test]
fn external_beats_drive_the_steps_and_rephase_the_internal_clock() {
    let mut l = venue(Some("yellow_green"));
    l.set_bpm(120.0);
    l.set_mode(LightMode::Dj, None);
    let list = list(LightMode::Dj);
    assert_eq!(l.cue(), list[0]);
    for _ in 0..7 {
        l.beat();
        for _ in 0..30 {
            l.update(1.0 / 60.0);
        }
    }
    assert_eq!(l.cue(), list[0]);
    l.beat(); // the 8th beat
    l.update(1.0 / 60.0);
    assert_eq!(l.cue(), list[1]);
}

#[test]
fn a_slow_idle_cycle_steps_on_seconds_not_beats() {
    let mut l = venue(None);
    l.set_mode(LightMode::Idle, None);
    let p = rig("venue_cycle").unwrap().mode(LightMode::Idle).clone();
    l.set_bpm(300.0);
    l.update(p.hold_s.unwrap() + p.fade_s - 0.01);
    assert_eq!(l.cue(), p.list.as_ref().unwrap()[0]);
    l.update(0.02);
    assert_eq!(l.cue(), p.list.as_ref().unwrap()[1]);
}

#[test]
fn disneyland_2019_music_puts_the_key_on_idle_takes_it_off_off_is_dark() {
    let mut l = StageLights::new(Some("disneyland_2019"), None, None);
    l.set_mode(LightMode::Idle, None);
    l.update(10.0);
    assert_eq!(l.cue(), "interlude");
    assert_eq!(l.out[g("droid_key")], [0.0; 3]);
    assert_eq!(l.out[g("head_rim_l")], [0.0; 3]);
    l.set_mode(LightMode::Music, None);
    l.update(10.0);
    assert!(l.out[g("droid_key")][0] > 0.5);
    assert!(l.out[g("head_rim_r")][2] > 0.5);
    l.set_mode(LightMode::Off, None);
    l.update(10.0);
    let lum = |c: Rgb| 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
    assert!(lum(l.out[g("wall_wash_l")]) < 0.1);
    assert_eq!(lum(l.out[g("droid_key")]), 0.0);
}

#[test]
fn speaking_holds_the_cue_and_lifts_the_key_leaving_eases_it_back() {
    let mut l = venue(None);
    l.set_mode(LightMode::Music, None);
    l.update(1.0);
    let cue = l.cue();
    let base = l.out[g("droid_key")];
    l.set_mode(LightMode::Speaking, None);
    l.update(30.0);
    assert_eq!(l.cue(), cue);
    let lift = rig("venue_cycle")
        .unwrap()
        .mode(LightMode::Speaking)
        .key_lift
        .unwrap();
    assert!((l.out[g("droid_key")][0] - base[0] * lift).abs() < 1e-6);
    l.set_mode(LightMode::Music, None);
    l.update(5.0);
    assert!((l.out[g("droid_key")][0] - vc(l.cue())[g("droid_key")][0]).abs() < 1e-6);
}

#[test]
fn the_key_lift_never_compounds_into_later_fades() {
    let mut l = venue(Some("amber"));
    l.set_mode(LightMode::Speaking, None);
    l.update(5.0);
    l.go_cue("amber", 1.0);
    l.set_mode(LightMode::Music, None);
    l.set_mode(LightMode::Speaking, None);
    l.update(5.0);
    let lift = rig("venue_cycle")
        .unwrap()
        .mode(LightMode::Speaking)
        .key_lift
        .unwrap();
    assert!((l.out[g("droid_key")][0] - vc(l.cue())[g("droid_key")][0] * lift).abs() < 1e-6);
}

#[test]
fn re_entering_music_resumes_at_the_cue_already_up() {
    let mut l = venue(None);
    l.set_mode(LightMode::Music, None);
    l.update(4.2);
    let cue = l.cue();
    l.set_mode(LightMode::Speaking, None);
    l.set_mode(LightMode::Music, None);
    assert_eq!(l.cue(), cue);
}

#[test]
fn set_rig_switches_era_and_keeps_the_mode_running() {
    let mut l = StageLights::new(Some("disneyland_2019"), None, None);
    l.set_mode(LightMode::Idle, None);
    l.set_rig("venue_cycle", 0.0);
    assert_eq!(l.rig_preset(), "venue_cycle");
    assert!(list(LightMode::Idle).contains(&l.cue()));
}

#[test]
fn defaults_disneyland_2019_at_its_photo_matched_song_cue() {
    let l = StageLights::default();
    assert_eq!(l.rig_preset(), "disneyland_2019");
    assert_eq!(l.cue(), rig("disneyland_2019").unwrap().initial);
    assert!(l.mode().is_none());
}

#[test]
fn tempo_reads_the_analysed_track_bpm() {
    assert_eq!(
        track_bpm(&json!({"track": {"title": "Batuu Boogie", "bpm": 119.9}})),
        Some(119.9)
    );
    assert_eq!(track_bpm(&json!({"track": {"bpm": "128"}})), Some(128.0));
}

#[test]
fn tempo_is_none_when_the_track_has_no_tempo_yet() {
    for v in [
        json!({"track": {"title": "x", "bpm": null}}),
        json!({"track": {"title": "x"}}),
        json!({}),
        json!(null),
        json!({"track": "Cantina Band"}),
    ] {
        assert_eq!(track_bpm(&v), None, "{v}");
    }
}

#[test]
fn tempo_rejects_implausible_values() {
    for v in [json!(0), json!(-5), json!(999), json!("fast")] {
        assert_eq!(track_bpm(&json!({"track": {"bpm": v}})), None);
    }
}

//! The operator layer: turns raw pad snapshots into (a) the continuous input the puppeteer
//! reads and (b) discrete [`Action`]s the runtime turns into bus commands. Pure: no bus, no
//! clock of its own (`now` is passed in), so every rule here is unit-tested.
//!
//! | Input | Base | Hold L1 / R1 (banks, `pad.json`) | Menu open (Select) |
//! |---|---|---|---|
//! | Sticks | gaze, body | same | same |
//! | D-pad | lift / visor | bank binding | ↑↓ move, → enter |
//! | ✕ ○ □ △ tap / hold | emote 1-4 / 5-8 | bank tap / hold | ✕ pick, ○ back |
//! | L2 | push-to-talk (hold) | same | same |
//! | R2 | arm raise | same | same |
//! | L3 / R3 | alive layers / cancel | same | same |
//! | Start | freeze | same | same |
//! | PS hold | arm / disarm | same | same |
//!
//! Face buttons fire their tap binding on release, or their hold binding once they have been
//! held `tap_hold_s` (Disney BD-X's short/long press, which doubles the slots). A face press
//! belongs to the bank held when it went down, whatever happens to L1/R1 after.

use r3x_contracts::{PadBinding, PadControls, PadMapping, PadMenuView};
use r3x_performer_core::show::puppeteer::PadState;

use crate::ds3::std_btn as b;

const FACES: [usize; 4] = [b::CROSS, b::CIRCLE, b::SQUARE, b::TRIANGLE];
const DPAD: [usize; 4] = [b::UP, b::RIGHT, b::DOWN, b::LEFT];

/// Something the operator asked for.
#[derive(Clone, Debug, PartialEq)]
pub enum Action {
    /// L2 went down (`true`) or up.
    Talk(bool),
    Emote(u8),
    Play(String),
    Sfx(String),
    /// R3: end the gesture and show layers.
    Cancel,
    /// L3: all alive layers (and autonomy) off, or back as they were.
    ToggleAlive,
    ToggleFreeze,
    ToggleArm,
    /// A menu leaf, by its id.
    Menu(String),
}

/// One menu entry; the runtime rebuilds the tree from live state every step, so labels can
/// say "DJ mode: on".
#[derive(Clone, Debug, PartialEq)]
pub struct MenuItem {
    pub label: String,
    /// Leaf: the id [`Action::Menu`] carries. Ignored when `children` is non-empty.
    pub id: String,
    pub children: Vec<MenuItem>,
}

impl MenuItem {
    pub fn leaf(label: impl Into<String>, id: impl Into<String>) -> Self {
        MenuItem { label: label.into(), id: id.into(), children: vec![] }
    }
    pub fn sub(label: impl Into<String>, children: Vec<MenuItem>) -> Self {
        MenuItem { label: label.into(), id: String::new(), children }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Bank {
    L1,
    R1,
}

/// One step's result.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Step {
    /// What the puppeteer reads: sticks, R2 and (only when free) the d-pad. Every other
    /// button is withheld, so its own discrete handling never fires under this layer.
    pub puppet: PadState,
    pub actions: Vec<Action>,
    pub view: PadControls,
}

pub struct Controls {
    map: PadMapping,
    prev: Vec<bool>,
    /// Per face button: when it went down and the bank it belongs to; `None` once fired.
    face: [Option<(f64, Option<Bank>)>; 4],
    ps_down: Option<f64>,
    menu: Option<(Vec<usize>, usize)>,
    pub armed: bool,
}

impl Controls {
    pub fn new(map: PadMapping) -> Self {
        Controls { map, prev: vec![], face: [None; 4], ps_down: None, menu: None, armed: true }
    }

    pub fn mapping(&self) -> &PadMapping {
        &self.map
    }

    /// The pad went away: forget what was held (the runtime stops talk itself).
    pub fn reset(&mut self) {
        self.prev.clear();
        self.face = [None; 4];
        self.ps_down = None;
        self.menu = None;
    }

    fn bank(&self, pad: &PadState) -> Option<Bank> {
        let down = |i: usize| pad.buttons.get(i).is_some_and(|x| x.0);
        match (down(b::L1), down(b::R1)) {
            (true, false) => Some(Bank::L1),
            (false, true) => Some(Bank::R1),
            _ => None, // neither, or both (no chord defined): base layer
        }
    }

    fn binding(&self, bank: Bank) -> &r3x_contracts::PadBank {
        match bank {
            Bank::L1 => &self.map.banks.l1,
            Bank::R1 => &self.map.banks.r1,
        }
    }

    fn bound(b: &PadBinding) -> Option<Action> {
        if let Some(id) = &b.play {
            Some(Action::Play(id.clone()))
        } else if let Some(id) = &b.sfx {
            Some(Action::Sfx(id.clone()))
        } else {
            b.emote.map(Action::Emote)
        }
    }

    fn face_action(&self, k: usize, bank: Option<Bank>, hold: bool) -> Option<Action> {
        match bank {
            None => Some(Action::Emote(k as u8 + if hold { 4 } else { 0 })),
            Some(bank) => {
                let bk = self.binding(bank);
                Self::bound(if hold { &bk.face_hold[k] } else { &bk.face_tap[k] })
            }
        }
    }

    pub fn step(&mut self, now: f64, pad: &PadState, menu: &[MenuItem]) -> Step {
        let down = |i: usize| pad.buttons.get(i).is_some_and(|x| x.0);
        let prev = std::mem::take(&mut self.prev);
        let was = |i: usize| prev.get(i).copied().unwrap_or(false);
        let pressed = |i: usize| down(i) && !was(i);
        let released = |i: usize| !down(i) && was(i);
        let mut actions = vec![];
        let bank = self.bank(pad);

        if pressed(b::L2) {
            actions.push(Action::Talk(true));
        }
        if released(b::L2) {
            actions.push(Action::Talk(false));
        }
        if pressed(b::L3) {
            actions.push(Action::ToggleAlive);
        }
        if pressed(b::R3) {
            actions.push(Action::Cancel);
        }
        if pressed(b::START) {
            actions.push(Action::ToggleFreeze);
        }
        // PS: a hold arms/disarms, once per hold.
        match (down(b::PS), self.ps_down) {
            (true, None) => self.ps_down = Some(now),
            (true, Some(t)) if t.is_finite() && now - t >= self.map.arm_hold_s => {
                self.armed = !self.armed;
                actions.push(Action::ToggleArm);
                self.ps_down = Some(f64::INFINITY);
            }
            (false, _) => self.ps_down = None,
            _ => {}
        }
        if pressed(b::SELECT) {
            self.menu = if self.menu.is_some() { None } else { Some((vec![], 0)) };
        }

        let menu_open = self.menu.is_some();
        if menu_open {
            self.navigate(menu, &pressed, &mut actions);
            // The menu owns the face buttons: drop any half-made press.
            self.face = [None; 4];
        } else {
            for (k, &i) in FACES.iter().enumerate() {
                if pressed(i) {
                    self.face[k] = Some((now, bank));
                }
                match self.face[k] {
                    Some((t, bk)) if down(i) && now - t >= self.map.tap_hold_s => {
                        actions.extend(self.face_action(k, bk, true));
                        self.face[k] = None;
                    }
                    Some((_, bk)) if released(i) => {
                        actions.extend(self.face_action(k, bk, false));
                        self.face[k] = None;
                    }
                    _ => {}
                }
            }
            if let Some(bk) = bank {
                for (k, &i) in DPAD.iter().enumerate() {
                    if pressed(i) {
                        actions.extend(Self::bound(&self.binding(bk).dpad[k]));
                    }
                }
            }
        }

        // The puppeteer's share: sticks and R2 always, the d-pad only when nothing else owns it.
        let mut buttons = vec![(false, 0.0); b::COUNT];
        let keep = |buttons: &mut Vec<(bool, f64)>, i: usize| {
            if let Some(x) = pad.buttons.get(i) {
                buttons[i] = *x;
            }
        };
        keep(&mut buttons, b::R2);
        if bank.is_none() && !menu_open {
            for i in DPAD {
                keep(&mut buttons, i);
            }
        }

        self.prev = (0..b::COUNT).map(down).collect();
        let view = PadControls {
            bank: bank.map(|x| match x {
                Bank::L1 => "l1".into(),
                Bank::R1 => "r1".into(),
            }),
            menu: self.menu_view(menu),
            talking: down(b::L2),
            armed: self.armed,
            last: None,
        };
        Step { puppet: PadState { axes: pad.axes.clone(), buttons }, actions, view }
    }

    fn level<'a>(root: &'a [MenuItem], path: &[usize]) -> Option<(&'a [MenuItem], Vec<String>)> {
        let mut items = root;
        let mut titles = vec!["Menu".to_string()];
        for &i in path {
            let it = items.get(i)?;
            titles.push(it.label.clone());
            items = &it.children;
        }
        Some((items, titles))
    }

    fn navigate(&mut self, root: &[MenuItem], pressed: &dyn Fn(usize) -> bool, actions: &mut Vec<Action>) {
        let Some((path, cursor)) = self.menu.as_mut() else { return };
        // The tree can change under us (a label, a list): fall back to the root if the path broke.
        let (items, _) = match Self::level(root, path) {
            Some(l) => l,
            None => {
                path.clear();
                *cursor = 0;
                (root, vec![])
            }
        };
        let n = items.len().max(1);
        *cursor = (*cursor).min(n - 1);
        if pressed(b::UP) {
            *cursor = (*cursor + n - 1) % n;
        }
        if pressed(b::DOWN) {
            *cursor = (*cursor + 1) % n;
        }
        if pressed(b::CROSS) || pressed(b::RIGHT) {
            if let Some(it) = items.get(*cursor) {
                if it.children.is_empty() {
                    actions.push(Action::Menu(it.id.clone()));
                } else {
                    path.push(*cursor);
                    *cursor = 0;
                }
            }
        } else if pressed(b::CIRCLE) || pressed(b::LEFT) {
            match path.pop() {
                Some(i) => *cursor = i,
                None => self.menu = None,
            }
        }
    }

    fn menu_view(&self, root: &[MenuItem]) -> Option<PadMenuView> {
        let (path, cursor) = self.menu.as_ref()?;
        let (items, titles) = Self::level(root, path).unwrap_or((root, vec!["Menu".into()]));
        Some(PadMenuView {
            path: titles,
            items: items.iter().map(|i| if i.children.is_empty() { i.label.clone() } else { format!("{} ›", i.label) }).collect(),
            cursor: (*cursor).min(items.len().saturating_sub(1)),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn mapping() -> PadMapping {
        let text = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/../../../profiles/r3x/pad.json")).unwrap();
        serde_json::from_str(&text).unwrap()
    }

    fn pad(held: &[usize]) -> PadState {
        let mut buttons = vec![(false, 0.0); b::COUNT];
        for &i in held {
            buttons[i] = (true, 1.0);
        }
        PadState { axes: vec![0.1, 0.0, -0.5, 0.2], buttons }
    }

    struct Run {
        c: Controls,
        t: f64,
        menu: Vec<MenuItem>,
    }
    impl Run {
        fn new() -> Self {
            let menu = vec![
                MenuItem::leaf("DJ mode", "dj"),
                MenuItem::sub("Music", vec![MenuItem::leaf("Next", "music.next"), MenuItem::leaf("Stop", "music.stop")]),
            ];
            Run { c: Controls::new(mapping()), t: 0.0, menu }
        }
        fn at(&mut self, dt: f64, held: &[usize]) -> Step {
            self.t += dt;
            self.c.step(self.t, &pad(held), &self.menu)
        }
    }

    #[test]
    fn the_shipped_mapping_parses() {
        let m = mapping();
        assert_eq!(m.banks.l1.face_tap[0].play.as_deref(), Some("nod"));
        assert_eq!(m.banks.r1.dpad[0].sfx.as_deref(), Some("air_horn"));
    }

    #[test]
    fn tap_fires_on_release_hold_fires_once_at_threshold() {
        let mut r = Run::new();
        assert!(r.at(0.01, &[b::CROSS]).actions.is_empty(), "nothing until release or hold");
        assert_eq!(r.at(0.1, &[]).actions, vec![Action::Emote(0)], "tap = slot 1");
        r.at(0.01, &[b::TRIANGLE]);
        assert_eq!(r.at(0.4, &[b::TRIANGLE]).actions, vec![Action::Emote(7)], "hold = slot 8");
        assert!(r.at(0.5, &[b::TRIANGLE]).actions.is_empty(), "once per hold");
        assert!(r.at(0.01, &[]).actions.is_empty(), "no tap after a hold");
    }

    #[test]
    fn banks_bind_face_and_dpad_and_keep_the_press_bank() {
        let mut r = Run::new();
        r.at(0.01, &[b::L1]);
        r.at(0.01, &[b::L1, b::CROSS]);
        // L1 let go before the release: still the L1 binding.
        assert_eq!(r.at(0.05, &[]).actions, vec![Action::Play("nod".into())]);
        let s = r.at(0.01, &[b::R1, b::UP]);
        assert_eq!(s.actions, vec![Action::Sfx("air_horn".into())]);
        assert_eq!(s.view.bank.as_deref(), Some("r1"));
        assert!(!s.puppet.buttons[b::UP].0, "a bank owns the d-pad");
        let s = r.at(0.01, &[b::UP]);
        assert!(s.puppet.buttons[b::UP].0, "free d-pad goes to the puppeteer");
    }

    #[test]
    fn talk_follows_l2_and_the_puppeteer_never_sees_discrete_buttons() {
        let mut r = Run::new();
        let s = r.at(0.01, &[b::L2, b::R2, b::START, b::CROSS, b::L1, b::R3]);
        assert!(s.actions.contains(&Action::Talk(true)));
        assert!(s.actions.contains(&Action::ToggleFreeze) && s.actions.contains(&Action::Cancel));
        assert!(s.view.talking);
        for i in [b::L2, b::START, b::CROSS, b::L1, b::R3, b::SELECT] {
            assert!(!s.puppet.buttons[i].0, "button {i} withheld");
        }
        assert!(s.puppet.buttons[b::R2].0, "R2 = arm");
        assert_eq!(s.puppet.axes, vec![0.1, 0.0, -0.5, 0.2]);
        assert!(r.at(0.01, &[]).actions.contains(&Action::Talk(false)));
    }

    #[test]
    fn ps_hold_toggles_arming_once() {
        let mut r = Run::new();
        assert!(r.c.armed);
        r.at(0.01, &[b::PS]);
        assert!(r.at(0.5, &[b::PS]).actions.is_empty());
        assert_eq!(r.at(0.6, &[b::PS]).actions, vec![Action::ToggleArm]);
        assert!(r.at(1.0, &[b::PS]).actions.is_empty());
        assert!(!r.at(0.01, &[]).view.armed);
    }

    #[test]
    fn menu_navigates_picks_and_backs_out() {
        let mut r = Run::new();
        let s = r.at(0.01, &[b::SELECT]);
        assert_eq!(s.view.menu.as_ref().unwrap().items, vec!["DJ mode", "Music ›"]);
        r.at(0.01, &[]);
        r.at(0.01, &[b::DOWN]);
        r.at(0.01, &[]);
        let s = r.at(0.01, &[b::CROSS]);
        let m = s.view.menu.unwrap();
        assert_eq!((m.path, m.cursor), (vec!["Menu".to_string(), "Music".to_string()], 0));
        assert!(s.actions.is_empty(), "entering a submenu fires nothing, and ✕ is not an emote here");
        r.at(0.01, &[]);
        r.at(0.01, &[b::DOWN]);
        r.at(0.01, &[]);
        assert_eq!(r.at(0.01, &[b::CROSS]).actions, vec![Action::Menu("music.stop".into())]);
        r.at(0.01, &[]);
        assert_eq!(r.at(0.01, &[b::CIRCLE]).view.menu.unwrap().path, vec!["Menu"]);
        r.at(0.01, &[]);
        assert!(r.at(0.01, &[b::CIRCLE]).view.menu.is_none(), "○ at the root closes");
        r.at(0.01, &[]);
        r.at(0.01, &[b::SELECT]);
        r.at(0.01, &[]);
        assert!(r.at(0.01, &[b::SELECT]).view.menu.is_none(), "Select toggles");
    }
}

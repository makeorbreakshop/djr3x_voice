//! The show player (port of `player.ts`), the conductor for SPEC "Sequence" clock and
//! interruption rules.
//!
//! Timing. Each performed item becomes a root run with a monotonic clock anchored at its
//! start: every item is scheduled at `anchor + at` (seconds, or beats through the current
//! tempo segment), never by accumulating frame dts. `wait for speech_end` pauses the root's
//! clock; every later item slides by the wait's real duration. A `beat` clock chases the
//! live tempo: a tempo change starts a new segment at the current beat position.
//!
//! Nesting. Nested cues and sequences become child nodes of the same root (one clock, one
//! run_id, one pause), each with its own clock unit.
//!
//! Layers. One root per layer (background < gesture < show). Clips and cues run on the
//! gesture layer, sequences on their declared layer. A new request on a layer ends the
//! current root, except that a gesture inside its `interruptible_after` window queues the
//! request (latest wins).

use super::catalog::Catalog;
use super::expand::{normalize_action, owns_union, MAX_DEPTH};
use super::types::{
    clamp_intensity, clamp_speed, tier_allows, Action, Body, Clock, Entry, Kind, Params, SeqLayer,
    ShowItem, Source,
};
use indexmap::IndexMap;
use serde::{Deserialize, Serialize};
use std::sync::Arc;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum RunLayer {
    Background,
    Gesture,
    Show,
}

pub const RUN_LAYERS: [RunLayer; 3] = [RunLayer::Background, RunLayer::Gesture, RunLayer::Show];

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct RunInfo {
    pub run_id: String,
    pub id: String,
    pub kind: Kind,
    pub source: Source,
    pub layer: RunLayer,
    /// Joints the run owns (sequence `owns`, including nested sequences), or None.
    pub owns: Option<Vec<String>>,
    /// Clock time the run started.
    pub started_at: f64,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum EndReason {
    Done,
    Interrupted,
    Rejected,
}

#[derive(Clone, Copy, Debug)]
pub struct DispatchCtx<'a> {
    pub run: &'a RunInfo,
    /// Clock time the action was scheduled for (may be a fraction of a frame ago).
    pub at: f64,
}

/// Where the player's output goes. Passed to every call, so the player holds no references.
pub trait PlayerHost {
    fn dispatch(&mut self, a: &Action, ctx: DispatchCtx);
    /// Is R3X speaking right now (for `wait for speech_end`)?
    fn speech_active(&self) -> bool;
    /// Live music tempo, or None (beat clocks then use the sequence's own bpm).
    fn live_bpm(&self) -> Option<f64>;
    fn started(&mut self, _run: &RunInfo) {}
    fn ended(&mut self, _run: &RunInfo, _reason: EndReason) {}
}

#[derive(Clone, Copy, Debug)]
pub struct PlayerOptions {
    /// How long a wait holds for speech that never starts (s). 0 = do not wait at all.
    pub wait_grace_s: f64,
    /// Longest a wait may hold (s).
    pub wait_max_s: f64,
}

impl Default for PlayerOptions {
    fn default() -> Self {
        PlayerOptions {
            wait_grace_s: 1.5,
            wait_max_s: 30.0,
        }
    }
}

#[derive(Clone, Debug)]
struct Item {
    at: f64,
    e: Entry,
    fired: bool,
}

#[derive(Clone, Debug)]
struct Node {
    id: String,
    items: Vec<Item>,
    beat: bool,
    looped: bool,
    length: f64,
    depth: usize,
    scale: f64,
    fallback_bpm: f64,
    children: Vec<Node>,
    // time clock: position = (L - start_l) * scale
    start_l: f64,
    // beat clock: position = seg_b + (L - seg_l) * bpm / 60
    seg_l: f64,
    seg_b: f64,
    bpm: f64,
}

impl Node {
    fn new(item: &ShowItem, start_l: f64, depth: usize, speed: f64) -> Node {
        let seq = item.sequence();
        let beat = seq.is_some_and(|s| s.clock == Clock::Beat);
        let fallback_bpm = seq.and_then(|s| s.bpm).unwrap_or(120.0);
        let mut items: Vec<Item> = match &item.body {
            Body::Clip(_) => vec![Item {
                at: 0.0,
                e: Entry::Act(Action::Clip {
                    id: item.id.clone(),
                    intensity: None,
                    speed: None,
                }),
                fired: false,
            }],
            Body::Cue(c) => c
                .actions
                .iter()
                .map(|a| Item {
                    at: a.at,
                    e: a.entry.clone(),
                    fired: false,
                })
                .collect(),
            Body::Sequence(s) => s
                .track
                .iter()
                .map(|a| Item {
                    at: a.at,
                    e: a.entry.clone(),
                    fired: false,
                })
                .collect(),
        };
        // Stable: ties keep file order.
        items.sort_by(|a, b| a.at.total_cmp(&b.at));
        Node {
            id: item.id.clone(),
            items,
            beat,
            looped: seq.is_some_and(|s| s.looped),
            length: seq.and_then(|s| s.length).unwrap_or(0.0),
            depth,
            scale: if beat { 1.0 } else { speed },
            fallback_bpm,
            children: Vec::new(),
            start_l,
            seg_l: start_l,
            seg_b: 0.0,
            bpm: fallback_bpm,
        }
    }

    fn pos(&self, l: f64) -> f64 {
        if self.beat {
            self.seg_b + (l - self.seg_l) * self.bpm / 60.0
        } else {
            (l - self.start_l) * self.scale
        }
    }

    /// Root-local seconds at which this node reaches position p (current tempo segment).
    fn time_of(&self, p: f64) -> f64 {
        if self.beat {
            self.seg_l + (p - self.seg_b) * 60.0 / self.bpm
        } else {
            self.start_l + p / self.scale
        }
    }

    fn chase(&mut self, l: f64, bpm: f64) {
        if !self.beat || bpm.is_nan() || bpm <= 0.0 || (bpm - self.bpm).abs() < 1e-6 {
            return;
        }
        self.seg_b = self.pos(l);
        self.seg_l = l;
        self.bpm = bpm;
    }

    fn all_fired(&self) -> bool {
        self.items.iter().all(|i| i.fired)
    }
    fn done(&self) -> bool {
        !self.looped && self.all_fired() && self.children.iter().all(Node::done)
    }
}

#[derive(Clone, Debug)]
struct Wait {
    since: f64,
    saw: bool,
}

#[derive(Clone, Debug)]
struct RootState {
    info: RunInfo,
    anchor: f64,
    params: (f64, f64),
    interruptible_at: f64,
    paused: f64,
    pause_at: Option<f64>,
    wait: Option<Wait>,
    busy_until: f64,
    spoke: bool,
}

impl RootState {
    fn local(&self, now: f64) -> f64 {
        now - self.anchor - self.paused - self.pause_at.map_or(0.0, |p| now - p)
    }
    fn clock(&self, l: f64) -> f64 {
        self.anchor + self.paused + l
    }
}

#[derive(Clone, Debug)]
struct Root {
    st: RootState,
    node: Node,
}

#[derive(Clone, Debug)]
struct Request {
    id: String,
    source: Source,
    params: Params,
    layer: RunLayer,
    run_id: String,
}

/// Selects runs for [`ShowPlayer::stop`].
#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
pub struct StopSel {
    #[serde(default)]
    pub id: Option<String>,
    #[serde(default)]
    pub layer: Option<RunLayer>,
    #[serde(default)]
    pub all: bool,
}

impl StopSel {
    pub fn all() -> Self {
        StopSel {
            all: true,
            ..Default::default()
        }
    }
    pub fn layer(l: RunLayer) -> Self {
        StopSel {
            layer: Some(l),
            ..Default::default()
        }
    }
    pub fn id(id: impl Into<String>) -> Self {
        StopSel {
            id: Some(id.into()),
            ..Default::default()
        }
    }
}

/// Everything a step needs besides the root it walks.
struct Env<'a> {
    cat: &'a Catalog,
    host: &'a mut dyn PlayerHost,
    now: f64,
    wait_grace_s: f64,
}

pub struct ShowPlayer {
    pub cat: Arc<Catalog>,
    pub frozen: bool,
    pub opts: PlayerOptions,
    // IndexMap with shift_remove: iteration order is insertion order, as a JS Map's.
    roots: IndexMap<RunLayer, Root>,
    queue: IndexMap<RunLayer, Request>,
    seq: u64,
    now: f64,
}

impl ShowPlayer {
    pub fn new(cat: Arc<Catalog>, opts: PlayerOptions) -> Self {
        ShowPlayer {
            cat,
            frozen: false,
            opts,
            roots: IndexMap::new(),
            queue: IndexMap::new(),
            seq: 0,
            now: 0.0,
        }
    }

    /// Perform an item (SPEC `show.perform`). Returns the run_id, or None when it was
    /// rejected (unknown id, tier violation, or motion frozen) - a rejection emits
    /// `ended(rejected)` only.
    pub fn perform(
        &mut self,
        host: &mut dyn PlayerHost,
        id: &str,
        source: Source,
        params: Params,
        now: f64,
        layer: Option<RunLayer>,
    ) -> Option<String> {
        self.now = now;
        let cat = self.cat.clone();
        let item = cat.get(id).or_else(|| cat.resolve(id));
        self.seq += 1;
        let run_id = format!("{}#{}", item.map_or(id, |i| i.id.as_str()), self.seq);
        let layer = layer.unwrap_or(match item.and_then(ShowItem::sequence) {
            Some(s) => match s.layer {
                Some(SeqLayer::Gesture) => RunLayer::Gesture,
                _ => RunLayer::Show,
            },
            None => RunLayer::Gesture,
        });
        let item = match item {
            Some(it) if !self.frozen && tier_allows(it.tier, source) => it,
            _ => {
                let info = RunInfo {
                    run_id,
                    id: id.to_owned(),
                    kind: item.map_or(Kind::Clip, ShowItem::kind),
                    source,
                    layer,
                    owns: None,
                    started_at: self.now,
                };
                host.ended(&info, EndReason::Rejected);
                return None;
            }
        };
        let req = Request {
            id: item.id.clone(),
            source,
            params,
            layer,
            run_id: run_id.clone(),
        };
        if self
            .roots
            .get(&layer)
            .is_some_and(|cur| self.now < cur.st.interruptible_at)
        {
            self.queue.shift_remove(&layer);
            self.queue.insert(layer, req); // latest wins
            return Some(run_id);
        }
        self.queue.shift_remove(&layer);
        self.begin(host, req);
        Some(run_id)
    }

    /// SPEC `show.stop {id|layer|all}`.
    pub fn stop(&mut self, host: &mut dyn PlayerHost, sel: &StopSel, now: f64) {
        self.now = now;
        let layers: Vec<RunLayer> = self.roots.keys().copied().collect();
        for layer in layers {
            let r = &self.roots[&layer];
            let hit = sel.all
                || sel.layer == Some(layer)
                || sel
                    .id
                    .as_deref()
                    .is_some_and(|id| id == r.st.info.id || id == r.st.info.run_id);
            if hit {
                self.queue.shift_remove(&layer);
                self.finish(host, layer, EndReason::Interrupted);
            }
        }
    }

    /// Safety beats everything: stop all runs and reject new ones until released.
    pub fn freeze(&mut self, host: &mut dyn PlayerHost, on: bool, now: f64) {
        if on {
            self.stop(host, &StopSel::all(), now);
        }
        self.frozen = on;
    }

    pub fn running(&self) -> Vec<&RunInfo> {
        RUN_LAYERS
            .iter()
            .filter_map(|l| self.roots.get(l).map(|r| &r.st.info))
            .collect()
    }

    pub fn queued(&self, layer: RunLayer) -> Option<&str> {
        self.queue.get(&layer).map(|r| r.id.as_str())
    }

    pub fn update(&mut self, host: &mut dyn PlayerHost, now: f64) {
        self.now = now;
        let queued: Vec<RunLayer> = self.queue.keys().copied().collect();
        for layer in queued {
            if self
                .roots
                .get(&layer)
                .is_none_or(|cur| now >= cur.st.interruptible_at)
            {
                if let Some(req) = self.queue.shift_remove(&layer) {
                    self.begin(host, req);
                }
            }
        }
        let cat = self.cat.clone();
        let layers: Vec<RunLayer> = self.roots.keys().copied().collect();
        for layer in layers {
            let Some(r) = self.roots.get_mut(&layer) else {
                continue;
            };
            if let Some(w) = &mut r.st.wait {
                let sp = host.speech_active();
                if sp {
                    w.saw = true;
                }
                let held = now - w.since;
                if (w.saw && !sp)
                    || (!w.saw && held >= self.opts.wait_grace_s)
                    || held >= self.opts.wait_max_s
                {
                    r.st.paused += now - r.st.pause_at.unwrap_or(now);
                    r.st.pause_at = None;
                    r.st.wait = None;
                } else {
                    continue;
                }
            }
            let l = r.st.local(now);
            let mut env = Env {
                cat: &cat,
                host,
                now,
                wait_grace_s: self.opts.wait_grace_s,
            };
            step(&mut env, &mut r.st, &mut r.node, l);
            let finished = r.node.done()
                && r.st.wait.is_none()
                && now >= r.st.busy_until
                && !(r.st.spoke && host.speech_active());
            if finished {
                self.finish(host, layer, EndReason::Done);
            }
        }
    }

    // ------------------------------------------------------------------ internals

    fn begin(&mut self, host: &mut dyn PlayerHost, req: Request) {
        let cat = self.cat.clone();
        let Some(item) = cat.get(&req.id) else { return };
        if self.roots.contains_key(&req.layer) {
            self.finish(host, req.layer, EndReason::Interrupted);
        }
        let params = (
            clamp_intensity(req.params.intensity.unwrap_or(1.0)),
            clamp_speed(req.params.speed.unwrap_or(1.0)),
        );
        let info = RunInfo {
            run_id: req.run_id,
            id: item.id.clone(),
            kind: item.kind(),
            source: req.source,
            layer: req.layer,
            owns: owns_union(item, &cat),
            started_at: self.now,
        };
        let node = Node::new(item, 0.0, 1, params.1);
        let st = RootState {
            info,
            anchor: self.now,
            params,
            interruptible_at: self.now + protected_for(&cat, item, params.1),
            paused: 0.0,
            pause_at: None,
            wait: None,
            busy_until: self.now,
            spoke: false,
        };
        let mut root = Root { st, node };
        host.started(&root.st.info);
        let mut env = Env {
            cat: &cat,
            host,
            now: self.now,
            wait_grace_s: self.opts.wait_grace_s,
        };
        step(&mut env, &mut root.st, &mut root.node, 0.0);
        self.roots.insert(req.layer, root);
    }

    fn finish(&mut self, host: &mut dyn PlayerHost, layer: RunLayer, reason: EndReason) {
        if let Some(r) = self.roots.shift_remove(&layer) {
            host.ended(&r.st.info, reason);
        }
    }
}

/// Seconds a gesture root is protected by its clips' `interruptible_after`.
fn protected_for(cat: &Catalog, item: &ShowItem, speed: f64) -> f64 {
    let acts: Vec<(f64, &str, f64)> = match &item.body {
        Body::Sequence(_) => return 0.0,
        Body::Clip(_) => vec![(0.0, item.id.as_str(), 1.0)],
        Body::Cue(c) => c
            .actions
            .iter()
            .filter_map(|a| match &a.entry {
                Entry::Act(Action::Clip { id, speed, .. }) => {
                    Some((a.at, id.as_str(), speed.unwrap_or(1.0)))
                }
                _ => None,
            })
            .collect(),
    };
    let mut t: f64 = 0.0;
    for (at, id, s) in acts {
        if let Some(ia) = cat
            .clip(id)
            .and_then(|c| c.interruptible_after)
            .filter(|&ia| ia != 0.0)
        {
            t = t.max((at + ia / clamp_speed(s)) / speed);
        }
    }
    t
}

fn step(env: &mut Env, r: &mut RootState, node: &mut Node, l: f64) {
    let bpm = env.host.live_bpm().unwrap_or(node.fallback_bpm);
    node.chase(l, bpm);
    loop {
        for i in 0..node.items.len() {
            if node.items[i].fired {
                continue;
            }
            if r.wait.is_some() || node.items[i].at > node.pos(l) + 1e-9 {
                break;
            }
            node.items[i].fired = true;
            let ls = node.time_of(node.items[i].at);
            let e = node.items[i].e.clone();
            fire(env, r, node, &e, ls);
        }
        if r.wait.is_some() {
            break;
        }
        if node.looped && node.all_fired() && node.pos(l) >= node.length - 1e-9 {
            // Next lap, anchored exactly one length later.
            if node.beat {
                node.seg_b -= node.length;
            } else {
                node.start_l += node.length / node.scale;
            }
            node.items.iter_mut().for_each(|i| i.fired = false);
            continue;
        }
        break;
    }
    for c in &mut node.children {
        if r.wait.is_none() {
            step(env, r, c, l);
        }
    }
    node.children.retain(|c| !c.done());
}

fn fire(env: &mut Env, r: &mut RootState, node: &mut Node, e: &Entry, ls: f64) {
    let a = match e {
        Entry::Ref(kind, id) => {
            match env
                .cat
                .get(id)
                .filter(|c| c.kind() == *kind && node.depth < MAX_DEPTH)
            {
                Some(child) => {
                    let mut c = Node::new(child, ls, node.depth + 1, r.params.1);
                    step(env, r, &mut c, ls);
                    node.children.push(c);
                }
                None => {
                    // Mirrors the TS console.warn; the linter reports these statically.
                    let _ = (&node.id, kind, id);
                }
            }
            return;
        }
        Entry::Act(a) => a,
    };
    if let Action::Wait { .. } = a {
        let sp = env.host.speech_active();
        if !sp && env.wait_grace_s == 0.0 {
            return;
        }
        r.pause_at = Some(r.clock(ls));
        r.wait = Some(Wait {
            since: env.now,
            saw: sp,
        });
        return;
    }
    let Some(mut n) = normalize_action(a) else {
        return;
    };
    let at = r.clock(ls);
    if let Action::Clip {
        id,
        intensity,
        speed,
    } = &mut n
    {
        let i = clamp_intensity(intensity.unwrap_or(1.0) * r.params.0);
        let s = clamp_speed(speed.unwrap_or(1.0) * r.params.1);
        *intensity = Some(i);
        *speed = Some(s);
        if let Some(clip) = env.cat.clip(id) {
            r.busy_until = r.busy_until.max(at + clip.duration / s);
        }
    }
    if let Action::Speak { .. } = n {
        r.spoke = true;
    }
    env.host.dispatch(&n, DispatchCtx { run: &r.info, at });
}

//! The one command console (`intent.console`): the panel's command line and `r3x-cli` both
//! send raw lines here, so there is a single parser - CantinaOS's command set, its shortcuts
//! and aliases, and the r3x additions. A line becomes a typed command (sent on the bus as the
//! client that typed it, so tiers and stage gates still apply), a reply computed from
//! retained state, or a line the owner of the console answers itself ([`Parsed::Pass`]:
//! `debug latency`, `dj next`, `reset`, `camera ...`).

use r3x_bus::Bus;
use r3x_contracts::{
    Ack, Command, Engagement, IntentCommand, MusicCommand, OperatingMode, PerfCommand, PerfLayer, RetainedState, Source, StageCommand,
    StopTarget, TelemetryCommand,
};

/// CantinaOS `CLIService.SHORTCUTS`.
pub const SHORTCUTS: &[(&str, &str)] = &[
    ("e", "engage"),
    ("a", "ambient"),
    ("d", "disengage"),
    ("h", "help"),
    ("st", "status"),
    ("r", "reset"),
    ("q", "quit"),
    ("l", "list music"),
    ("p", "play music"),
    ("s", "stop music"),
    ("rec", "record"),
];

pub const HELP: &str = "\
r3x commands:
  engage | ambient | disengage | idle      engagement (e, a, d)
  record | done                            push-to-talk on/off (rec; engages if needed)
  say <text>                               a typed turn
  list music | play music [n|query] | stop music | next music     (l, p, s)
  dj start | dj stop | dj next
  show list | show <id> [intensity] [speed] | show stop [id|show|gesture|all]
  freeze | unfreeze
  eye pattern <pattern> | eye status       idle engaged listening thinking speaking flash ...
  emote <slot|cue>                         a profile emote
  mode show|bench|studio                   operating mode
  brain on|off | autonomy on|off | output <name> on|off | layer <name> on|off
  log <filter> | debug level <level>       runtime log filter (debug, info, r3x_gateway=trace)
  status | state                           summary (st) | full retained state
  debug latency | reset | camera list|status|select <n>
  help | quit                              (h, q; quit leaves the terminal client)";

/// What a line means.
#[derive(Debug, Clone, PartialEq)]
pub enum Parsed {
    Empty,
    /// A typed command, sent as the client that typed the line.
    Send(Command),
    /// `play music <n>`: the n-th title of `list music` (1-based).
    PlayNumber(usize),
    Help,
    Status,
    State,
    ListMusic,
    ShowList,
    EyeStatus,
    Quit,
    /// A fixed answer (an alias that has nothing behind it any more says so).
    Reply(String),
    /// Answered by the console's owner (the brain, vision, or CantinaOS in bridge mode).
    Pass(String),
    Error(String),
}

/// Shortcuts applied to the first word.
pub fn expand(line: &str) -> String {
    let line = line.trim();
    let (first, rest) = line.split_once(char::is_whitespace).unwrap_or((line, ""));
    let first = first.to_ascii_lowercase();
    let head = SHORTCUTS.iter().find(|(k, _)| *k == first).map(|(_, v)| (*v).to_owned()).unwrap_or(first);
    if rest.trim().is_empty() {
        head
    } else {
        format!("{head} {}", rest.trim())
    }
}

fn on_off(w: Option<&str>) -> Option<bool> {
    match w? {
        "on" | "true" | "1" | "enable" => Some(true),
        "off" | "false" | "0" | "disable" => Some(false),
        _ => None,
    }
}

/// Parse one line. `emotes` (the profile's) resolves `emote <cue>` to its slot.
pub fn parse(line: &str, emotes: &[String]) -> Parsed {
    let line = expand(line);
    let words: Vec<&str> = line.split_whitespace().collect();
    let rest = |n: usize| words.get(n..).map(|w| w.join(" ")).unwrap_or_default();
    let send = Parsed::Send;
    let intent = |i| send(Command::Intent(i));
    let stage = |s| send(Command::Stage(s));
    let perf = |p| send(Command::Perf(p));
    let engage = |e| stage(StageCommand::SetEngagement { engagement: e });
    let lc: Vec<String> = words.iter().map(|w| w.to_ascii_lowercase()).collect();
    let w = |i: usize| lc.get(i).map(String::as_str);
    let pass = || Parsed::Pass(lc.join(" "));

    match (w(0), w(1)) {
        (None, _) => Parsed::Empty,
        (Some("quit" | "exit"), _) => Parsed::Quit,
        (Some("?" | "help"), _) => Parsed::Help,
        (Some("status"), None) => Parsed::Status,
        (Some("state"), None) => Parsed::State,
        (Some("list"), Some("music")) => Parsed::ListMusic,
        (Some("engage"), None) => engage(Engagement::Interactive),
        (Some("ambient"), None) => engage(Engagement::Ambient),
        (Some("disengage" | "idle"), None) => engage(Engagement::Idle),
        (Some("record"), None) => intent(IntentCommand::PttStart),
        (Some("done"), None) => intent(IntentCommand::PttStop),
        (Some("say"), Some(_)) => intent(IntentCommand::Say { text: rest(1) }),
        (Some("play"), Some("music")) => match rest(2) {
            q if q.is_empty() => intent(IntentCommand::Music(MusicCommand::Play { query: None })),
            q => match q.parse::<usize>() {
                Ok(n) => Parsed::PlayNumber(n),
                Err(_) => intent(IntentCommand::Music(MusicCommand::Play { query: Some(q) })),
            },
        },
        (Some("stop"), Some("music")) => intent(IntentCommand::Music(MusicCommand::Stop)),
        (Some("next"), Some("music" | "track")) => intent(IntentCommand::Music(MusicCommand::Next)),
        (Some("install"), Some("music")) => Parsed::Reply("the library is MUSIC_DIR; add files there and restart".into()),
        (Some("dj"), Some("start" | "on")) if words.len() == 2 => intent(IntentCommand::Dj { active: true }),
        (Some("dj"), Some("stop" | "off")) if words.len() == 2 => intent(IntentCommand::Dj { active: false }),
        (Some("dj"), Some("next")) => pass(),
        (Some("dj"), Some("queue")) => Parsed::Reply("dj queue is not available; DJ mode picks the next track itself".into()),
        (Some("dj"), _) => Parsed::Error("usage: dj start|stop|next".into()),
        (Some("freeze"), None) => stage(StageCommand::Freeze { on: true }),
        (Some("unfreeze"), None) => stage(StageCommand::Freeze { on: false }),
        (Some("show"), None | Some("list")) => Parsed::ShowList,
        (Some("show"), Some("reload")) => Parsed::Reply("the show folder reloads by itself when a file changes".into()),
        (Some("show"), Some("stop")) => {
            let t = match w(2) {
                None | Some("all") => StopTarget::All,
                Some("show") => StopTarget::Layer { layer: PerfLayer::Show },
                Some("gesture") => StopTarget::Layer { layer: PerfLayer::Gesture },
                Some(_) => StopTarget::Id { id: words[2].to_owned() },
            };
            perf(PerfCommand::Stop(t))
        }
        (Some("show"), Some(_)) => {
            let num = |i: usize| words.get(i).map(|s| s.parse::<f64>()).unwrap_or(Ok(1.0));
            match (num(2), num(3)) {
                (Ok(intensity), Ok(speed)) => perf(PerfCommand::Play { id: words[1].to_owned(), intensity, speed, layer: None }),
                _ => Parsed::Error("usage: show <id> [intensity] [speed]".into()),
            }
        }
        // CantinaOS `eye pattern <pattern> [color]`: the colour went with the old firmware.
        (Some("eye" | "eyes"), Some("pattern")) => match w(2) {
            Some(p) => perf(PerfCommand::Eyes { pattern: p.to_owned(), duration: None }),
            None => Parsed::Error("usage: eye pattern <pattern>".into()),
        },
        (Some("eye" | "eyes"), Some("status")) => Parsed::EyeStatus,
        (Some("eye" | "eyes"), Some("test")) => Parsed::Reply("eye test is not available; try eye pattern <pattern>".into()),
        (Some("eye" | "eyes"), _) => Parsed::Error("usage: eye pattern <pattern> | eye status".into()),
        (Some("emote"), Some(which)) => {
            let slot = which.parse::<u8>().ok().or_else(|| emotes.iter().position(|e| e == which).map(|i| i as u8));
            match slot {
                Some(slot) => perf(PerfCommand::Emote { slot }),
                None => Parsed::Error(format!("no emote {which:?}; slots: {}", emotes.join(", "))),
            }
        }
        (Some("mode"), m) => match m {
            Some("show") => stage(StageCommand::SetMode { mode: OperatingMode::Show }),
            Some("bench") => stage(StageCommand::SetMode { mode: OperatingMode::Bench }),
            Some("studio") => stage(StageCommand::SetMode { mode: OperatingMode::Studio }),
            _ => Parsed::Error("usage: mode show|bench|studio".into()),
        },
        (Some("brain"), _) => match on_off(w(1)) {
            Some(enabled) => stage(StageCommand::SetBrain { enabled }),
            None => Parsed::Error("usage: brain on|off".into()),
        },
        (Some("autonomy"), _) => match on_off(w(1)) {
            Some(enabled) => stage(StageCommand::SetAutonomy { enabled }),
            None => Parsed::Error("usage: autonomy on|off".into()),
        },
        (Some(k @ ("output" | "layer")), Some(_)) => match on_off(w(2)) {
            Some(enabled) if k == "output" => stage(StageCommand::SetOutput { output: words[1].to_owned(), enabled }),
            Some(enabled) => stage(StageCommand::SetLayer { layer: words[1].to_owned(), enabled }),
            None => Parsed::Error(format!("usage: {k} <name> on|off")),
        },
        (Some("log"), Some(_)) => send(Command::Telemetry(TelemetryCommand::SetLogLevel { level: rest(1) })),
        (Some("debug"), Some("level")) if words.len() > 2 => {
            let level = match w(2) {
                Some("warning") => "warn".to_owned(),
                _ => rest(2).to_ascii_lowercase(),
            };
            send(Command::Telemetry(TelemetryCommand::SetLogLevel { level }))
        }
        (Some("debug"), Some("latency")) | (Some("reset"), None) | (Some("conversation"), Some("reset")) | (Some("camera"), _) => pass(),
        (Some("debug"), _) => Parsed::Error("usage: debug latency | debug level <level>".into()),
        _ => Parsed::Error(format!("unknown command '{line}' - `help` lists them")),
    }
}

/// What the console owner does with a line.
pub enum Outcome {
    /// Publish this as the console reply.
    Reply(String, bool),
    /// Answer this (expanded, lower-case) line yourself.
    Pass(String),
}

/// What the console needs besides the bus.
#[derive(Debug, Clone, Default)]
pub struct ConsoleCtx {
    pub emotes: Vec<String>,
    /// `show/`, read fresh for `show list`.
    pub show_dir: Option<std::path::PathBuf>,
}

/// Run one line as `source`.
pub async fn run(bus: &Bus, source: Source, line: &str, ctx: &ConsoleCtx) -> Outcome {
    let reply = |s: String| Outcome::Reply(s, false);
    let cmd = match parse(line, &ctx.emotes) {
        Parsed::Empty => return reply(String::new()),
        Parsed::Help => return reply(HELP.into()),
        Parsed::Status => return reply(status(&bus.snapshot())),
        Parsed::State => return reply(serde_json::to_string_pretty(&bus.snapshot()).unwrap_or_default()),
        Parsed::ListMusic => {
            let lib = bus.snapshot().music.library;
            if lib.is_empty() {
                return reply("the music library is empty".into());
            }
            return reply(lib.iter().enumerate().map(|(i, t)| format!("{:>3}. {t}", i + 1)).collect::<Vec<_>>().join("\n"));
        }
        Parsed::ShowList => return reply(show_list(ctx)),
        Parsed::EyeStatus => {
            let l = bus.snapshot().lights;
            return reply(format!(
                "eyes: {}{}",
                l.eye_pattern.as_deref().unwrap_or("following the interaction state"),
                l.eye_color.map(|c| format!(" ({c})")).unwrap_or_default()
            ));
        }
        Parsed::Quit => return reply("quit leaves the terminal client (r3x-cli); stop the runtime with Ctrl-C in ./r3x".into()),
        Parsed::Reply(s) => return reply(s),
        Parsed::Error(e) => return Outcome::Reply(e, true),
        Parsed::Pass(l) => return Outcome::Pass(l),
        Parsed::PlayNumber(n) => {
            let lib = bus.snapshot().music.library;
            match n.checked_sub(1).and_then(|i| lib.get(i)) {
                Some(t) => Command::Intent(IntentCommand::Music(MusicCommand::Play { query: Some(t.clone()) })),
                // Not an index: a title that is a number, or a search.
                None => Command::Intent(IntentCommand::Music(MusicCommand::Play { query: Some(n.to_string()) })),
            }
        }
        Parsed::Send(c) => c,
    };
    match bus.command(source, None, cmd).await {
        Ack::Accepted => reply("ok".into()),
        Ack::Rejected { reason } => Outcome::Reply(reason, true),
    }
}

fn show_list(ctx: &ConsoleCtx) -> String {
    let Some(dir) = &ctx.show_dir else { return "no show folder".into() };
    let cat = crate::ShowCatalog::load(dir);
    let mut out = String::new();
    for kind in ["sequence", "cue", "clip"] {
        let mut ids: Vec<&str> = cat.kinds.iter().filter(|(_, k)| k.as_str() == kind).map(|(id, _)| id.as_str()).collect();
        ids.sort_unstable();
        if !ids.is_empty() {
            out += &format!("{kind}s ({}): {}\n", ids.len(), ids.join(", "));
        }
    }
    if out.is_empty() {
        format!("no shows in {}", dir.display())
    } else {
        out.trim_end().to_owned()
    }
}

pub fn status(s: &RetainedState) -> String {
    let mut out = format!(
        "engagement {:?} | mode {:?} | brain {} | autonomy {}{}\nmusic: {}{}",
        s.engagement.engagement,
        s.stage.mode,
        if s.stage.brain { "on" } else { "off" },
        if s.stage.autonomy { "on" } else { "off" },
        if s.stage.frozen { " | FROZEN" } else { "" },
        match (&s.music.track, s.music.playing) {
            (Some(t), true) => t.title.clone(),
            _ => "nothing playing".into(),
        },
        if s.dj.active { " (DJ mode)" } else { "" },
    );
    for (name, h) in &s.services.services {
        out += &format!("\n  {name:<14} {:?}{}", h.status, h.detail.as_deref().map(|d| format!(" - {d}")).unwrap_or_default());
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn send(line: &str) -> Command {
        match parse(line, &["yes".into(), "no".into()]) {
            Parsed::Send(c) => c,
            other => panic!("{line}: {other:?}"),
        }
    }

    #[test]
    fn cantina_command_set_maps_to_typed_commands() {
        assert_eq!(send("e"), Command::Stage(StageCommand::SetEngagement { engagement: Engagement::Interactive }));
        assert_eq!(send("d"), Command::Stage(StageCommand::SetEngagement { engagement: Engagement::Idle }));
        assert_eq!(send("p cantina band"), Command::Intent(IntentCommand::Music(MusicCommand::Play { query: Some("cantina band".into()) })));
        assert_eq!(send("p"), Command::Intent(IntentCommand::Music(MusicCommand::Play { query: None })));
        assert_eq!(parse("p 3", &[]), Parsed::PlayNumber(3));
        assert_eq!(send("s"), Command::Intent(IntentCommand::Music(MusicCommand::Stop)));
        assert_eq!(send("rec"), Command::Intent(IntentCommand::PttStart));
        assert_eq!(send("dj start"), Command::Intent(IntentCommand::Dj { active: true }));
        assert_eq!(send("show wave 0.5 2"), Command::Perf(PerfCommand::Play { id: "wave".into(), intensity: 0.5, speed: 2.0, layer: None }));
        assert_eq!(send("show stop gesture"), Command::Perf(PerfCommand::Stop(StopTarget::Layer { layer: PerfLayer::Gesture })));
        assert_eq!(send("freeze"), Command::Stage(StageCommand::Freeze { on: true }));
        assert_eq!(send("emote no"), Command::Perf(PerfCommand::Emote { slot: 1 }));
        assert_eq!(send("mode bench"), Command::Stage(StageCommand::SetMode { mode: OperatingMode::Bench }));
        assert_eq!(send("output neck on"), Command::Stage(StageCommand::SetOutput { output: "neck".into(), enabled: true }));
        assert_eq!(send("eye pattern Thinking"), Command::Perf(PerfCommand::Eyes { pattern: "thinking".into(), duration: None }));
        assert_eq!(send("eye pattern happy red"), Command::Perf(PerfCommand::Eyes { pattern: "happy".into(), duration: None }));
        assert_eq!(send("debug level WARNING"), Command::Telemetry(TelemetryCommand::SetLogLevel { level: "warn".into() }));
    }

    #[test]
    fn local_answers_passes_and_errors() {
        for (line, p) in [
            ("h", Parsed::Help),
            ("help", Parsed::Help),
            ("st", Parsed::Status),
            ("status", Parsed::Status),
            ("l", Parsed::ListMusic),
            ("show list", Parsed::ShowList),
            ("show", Parsed::ShowList),
            ("eye status", Parsed::EyeStatus),
            ("q", Parsed::Quit),
        ] {
            assert_eq!(parse(line, &[]), p, "{line}");
        }
        for (line, to) in [("debug latency", "debug latency"), ("dj next", "dj next"), ("r", "reset"), ("camera list", "camera list")] {
            assert_eq!(parse(line, &[]), Parsed::Pass(to.into()), "{line}");
        }
        for line in ["mode loud", "show wave loud", "eye", "frobnicate", "dj dance"] {
            assert!(matches!(parse(line, &[]), Parsed::Error(_)), "{line}");
        }
    }

    /// Every command `help` lists parses to something other than "unknown".
    #[test]
    fn everything_help_lists_is_understood() {
        for line in [
            "engage", "ambient", "disengage", "idle", "record", "done", "say hi", "list music", "play music", "play music 2",
            "stop music", "next music", "dj start", "dj stop", "dj next", "show list", "show wave", "show stop", "freeze",
            "unfreeze", "eye pattern thinking", "eye status", "emote yes", "mode show", "brain on", "autonomy off",
            "output face on", "layer breath off", "log debug", "debug level info", "status", "state", "debug latency",
            "reset", "camera status", "help", "quit",
        ] {
            let p = parse(line, &["yes".into()]);
            assert!(!matches!(p, Parsed::Error(_) | Parsed::Empty), "{line}: {p:?}");
        }
    }
}

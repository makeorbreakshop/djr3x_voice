//! Line parsing for `r3x-cli`: the CantinaOS command set and shortcuts, as typed commands.
//! `help`, `status` and `list music` are answered locally from retained state. Other lines
//! with no typed equivalent (`debug ...`, `dj next`, `camera ...`, `reset`) go to the brain's
//! console (`intent.console`) unchanged.

use r3x_contracts::{
    Command, Engagement, IntentCommand, MusicCommand, OperatingMode, PerfCommand, PerfLayer,
    StageCommand, StopTarget, TelemetryCommand,
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
  show <id> [intensity] [speed] | show stop [id|show|gesture|all]
  freeze | unfreeze
  emote <slot|cue>                         a profile emote
  mode show|bench|studio                   operating mode
  brain on|off | autonomy on|off | output <name> on|off | layer <name> on|off
  log <filter>                             runtime log filter (debug, info, r3x_gateway=trace)
  status | state                           summary (st) | full retained state
  debug latency | reset                    brain console
  help | quit                              (h, q)";

#[derive(Debug, Clone, PartialEq)]
pub enum Parsed {
    Send(Command),
    Quit,
    Help,
    Status,
    ListMusic,
    State,
    Error(String),
    Empty,
}

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

/// Parse one line. `emotes` resolves `emote <cue>` to its slot.
pub fn parse(line: &str, emotes: &[String]) -> Parsed {
    let line = expand(line);
    let words: Vec<&str> = line.split_whitespace().collect();
    let rest = |n: usize| words.get(n..).map(|w| w.join(" ")).unwrap_or_default();
    let intent = |i| Parsed::Send(Command::Intent(i));
    let stage = |s| Parsed::Send(Command::Stage(s));
    let engage = |e| stage(StageCommand::SetEngagement { engagement: e });
    let console = || intent(IntentCommand::Console { line: line.clone() });
    let lc: Vec<String> = words.iter().map(|w| w.to_ascii_lowercase()).collect();
    let w = |i: usize| lc.get(i).map(String::as_str);

    match (w(0), w(1)) {
        (None, _) => Parsed::Empty,
        (Some("quit" | "exit"), _) => Parsed::Quit,
        (Some("?" | "help"), _) => Parsed::Help,
        (Some("status"), None) => Parsed::Status,
        (Some("list"), Some("music")) => Parsed::ListMusic,
        (Some("state"), None) => Parsed::State,
        (Some("engage"), None) => engage(Engagement::Interactive),
        (Some("ambient"), None) => engage(Engagement::Ambient),
        (Some("disengage" | "idle"), None) => engage(Engagement::Idle),
        (Some("record"), None) => intent(IntentCommand::PttStart),
        (Some("done"), None) => intent(IntentCommand::PttStop),
        (Some("say"), Some(_)) => intent(IntentCommand::Say { text: rest(1) }),
        (Some("play"), Some("music")) => {
            let q = rest(2);
            intent(IntentCommand::Music(MusicCommand::Play { query: (!q.is_empty()).then_some(q) }))
        }
        (Some("stop"), Some("music")) => intent(IntentCommand::Music(MusicCommand::Stop)),
        (Some("next"), Some("music")) => intent(IntentCommand::Music(MusicCommand::Next)),
        (Some("dj"), Some("start")) if words.len() == 2 => intent(IntentCommand::Dj { active: true }),
        (Some("dj"), Some("stop")) if words.len() == 2 => intent(IntentCommand::Dj { active: false }),
        (Some("freeze"), None) => stage(StageCommand::Freeze { on: true }),
        (Some("unfreeze"), None) => stage(StageCommand::Freeze { on: false }),
        (Some("show"), Some("stop")) => {
            let t = match w(2) {
                None | Some("all") => StopTarget::All,
                Some("show") => StopTarget::Layer { layer: PerfLayer::Show },
                Some("gesture") => StopTarget::Layer { layer: PerfLayer::Gesture },
                Some(_) => StopTarget::Id { id: words[2].to_owned() },
            };
            Parsed::Send(Command::Perf(PerfCommand::Stop(t)))
        }
        // `show`, `show list`, `show reload` stay with CantinaOS's show player.
        (Some("show"), None | Some("list" | "reload")) => console(),
        (Some("show"), Some(_)) => {
            let num = |i: usize, def: f64| words.get(i).map(|s| s.parse::<f64>()).unwrap_or(Ok(def));
            match (num(2, 1.0), num(3, 1.0)) {
                (Ok(intensity), Ok(speed)) => Parsed::Send(Command::Perf(PerfCommand::Play {
                    id: words[1].to_owned(),
                    intensity,
                    speed,
                    layer: None,
                })),
                _ => Parsed::Error("usage: show <id> [intensity] [speed]".into()),
            }
        }
        (Some("emote"), Some(which)) => {
            let slot = which.parse::<u8>().ok().or_else(|| emotes.iter().position(|e| e == which).map(|i| i as u8));
            match slot {
                Some(slot) => Parsed::Send(Command::Perf(PerfCommand::Emote { slot })),
                None => Parsed::Error(format!("no emote {which:?}; slots: {}", emotes.join(", "))),
            }
        }
        (Some("mode"), Some(m)) => {
            let mode = match m {
                "show" => OperatingMode::Show,
                "bench" => OperatingMode::Bench,
                "studio" => OperatingMode::Studio,
                _ => return Parsed::Error("usage: mode show|bench|studio".into()),
            };
            stage(StageCommand::SetMode { mode })
        }
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
        (Some("log"), Some(_)) => Parsed::Send(Command::Telemetry(TelemetryCommand::SetLogLevel { level: rest(1) })),
        _ => console(),
    }
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
        assert_eq!(send("s"), Command::Intent(IntentCommand::Music(MusicCommand::Stop)));
        assert_eq!(send("rec"), Command::Intent(IntentCommand::PttStart));
        assert_eq!(send("dj start"), Command::Intent(IntentCommand::Dj { active: true }));
        assert_eq!(
            send("show wave 0.5 2"),
            Command::Perf(PerfCommand::Play { id: "wave".into(), intensity: 0.5, speed: 2.0, layer: None })
        );
        assert_eq!(send("show stop gesture"), Command::Perf(PerfCommand::Stop(StopTarget::Layer { layer: PerfLayer::Gesture })));
        assert_eq!(send("freeze"), Command::Stage(StageCommand::Freeze { on: true }));
        assert_eq!(send("emote no"), Command::Perf(PerfCommand::Emote { slot: 1 }));
        assert_eq!(send("mode bench"), Command::Stage(StageCommand::SetMode { mode: OperatingMode::Bench }));
        assert_eq!(send("output neck on"), Command::Stage(StageCommand::SetOutput { output: "neck".into(), enabled: true }));
    }

    #[test]
    fn local_lines_and_everything_else_to_the_console() {
        for (line, p) in [("h", Parsed::Help), ("help", Parsed::Help), ("st", Parsed::Status), ("l", Parsed::ListMusic)] {
            assert_eq!(parse(line, &[]), p, "{line}");
        }
        for line in ["eye pattern happy red", "debug latency", "dj next", "show list", "reset"] {
            let Command::Intent(IntentCommand::Console { line: l }) = send(line) else { panic!("{line}") };
            assert_eq!(l, expand(line));
        }
        assert_eq!(parse("q", &[]), Parsed::Quit);
        assert!(matches!(parse("mode loud", &[]), Parsed::Error(_)));
        assert!(matches!(parse("show wave loud", &[]), Parsed::Error(_)));
    }
}

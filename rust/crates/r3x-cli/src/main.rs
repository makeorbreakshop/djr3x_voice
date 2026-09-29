//! `r3x-cli [--url ws://127.0.0.1:8780/] [-c LINE]...`
//!
//! Interactive with history (`~/.config/dj-r3x/cli_history`) unless `-c` lines are given, in
//! which case each is sent in order, its ack printed, and the client exits. Token:
//! `R3X_CLI_TOKEN` or `~/.config/dj-r3x/cli_token` (the runtime reads the same file).

use std::io::IsTerminal;
use std::time::Duration;

use anyhow::Context;
use futures_util::{SinkExt, StreamExt};
use r3x_cli::{parse, Parsed, HELP};
use r3x_contracts::{
    Ack, Body, Command, ConversationEvent, IntentCommand, MusicCommand, DjEvent, Envelope, Event, MusicEvent, OpsEvent, PerfEvent,
    RetainedState, ServiceStatus, StageEvent,
};
use r3x_gateway::tokens;
use rustyline::ExternalPrinter;
use serde_json::json;
use tokio::sync::mpsc;
use tokio_tungstenite::tungstenite::{client::IntoClientRequest, http::HeaderValue, Message};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let mut url = std::env::var("R3X_GATEWAY_URL").unwrap_or_else(|_| "ws://127.0.0.1:8780/".into());
    let mut script = Vec::new();
    let mut args = std::env::args().skip(1);
    while let Some(a) = args.next() {
        match a.as_str() {
            "--url" => url = args.next().context("--url needs a value")?,
            "-c" => script.push(args.next().context("-c needs a line")?),
            "-h" | "--help" => {
                println!("usage: r3x-cli [--url URL] [-c LINE]...\n\n{HELP}");
                return Ok(());
            }
            other => anyhow::bail!("unknown argument {other}"),
        }
    }
    let token = tokens::load_or_create("R3X_CLI_TOKEN", tokens::config_dir().join("cli_token"))?;
    let mut req = url.as_str().into_client_request()?;
    req.headers_mut().insert("Authorization", HeaderValue::from_str(&format!("Bearer {token}"))?);
    let (ws, _) = tokio_tungstenite::connect_async(req)
        .await
        .with_context(|| format!("cannot reach the r3x gateway at {url} (is r3x-runtime running?)"))?;
    let (mut sink, mut stream) = ws.split();

    // Lines in: from rustyline on its own thread, or the -c script.
    let (line_tx, mut lines) = mpsc::unbounded_channel::<String>();
    let interactive = script.is_empty();
    let mut printer: Box<dyn FnMut(String) + Send> = if interactive && !std::io::stdin().is_terminal() {
        // Piped input (no line editor): read lines as they come.
        std::thread::spawn(move || {
            for line in std::io::stdin().lines().map_while(Result::ok) {
                if line_tx.send(line).is_err() {
                    return;
                }
            }
            let _ = line_tx.send("quit".into());
        });
        Box::new(|s: String| println!("{s}"))
    } else if interactive {
        let mut rl = rustyline::DefaultEditor::new()?;
        let history = tokens::config_dir().join("cli_history");
        let _ = rl.load_history(&history);
        let mut ext = rl.create_external_printer()?;
        std::thread::spawn(move || {
            while let Ok(line) = rl.readline("r3x> ") {
                let _ = rl.add_history_entry(line.as_str());
                let _ = rl.save_history(&history);
                if line_tx.send(line).is_err() {
                    break;
                }
            }
            let _ = line_tx.send("quit".into());
        });
        Box::new(move |s: String| {
            let _ = ext.print(s + "\n");
        })
    } else {
        for l in script {
            let _ = line_tx.send(l);
        }
        drop(line_tx);
        Box::new(|s: String| println!("{s}"))
    };
    if interactive {
        printer(format!("connected to {url}. `help` lists commands, `quit` or Ctrl-C stops."));
    }

    let mut state = RetainedState::default();
    let mut emotes: Vec<String> = Vec::new();
    let mut waiting: Option<String> = None;
    let mut next_id = 0u64;
    let mut input_done = false;
    let mut hello_seen = false;

    loop {
        tokio::select! {
            line = lines.recv(), if hello_seen && waiting.is_none() && !input_done => {
                let Some(line) = line else { input_done = true; continue };
                match parse(&line, &emotes) {
                    Parsed::Empty => {}
                    Parsed::Quit => break,
                    Parsed::Help => printer(HELP.into()),
                    Parsed::Status => printer(status(&state)),
                    Parsed::ListMusic => printer(
                        state.music.library.iter().enumerate().map(|(i, t)| format!("{:>3}. {t}", i + 1)).collect::<Vec<_>>().join("\n"),
                    ),
                    Parsed::State => printer(serde_json::to_string_pretty(&state)?),
                    Parsed::Error(e) => printer(e),
                    Parsed::Send(mut cmd) => {
                        // `play music 3` = the 3rd title of `list music`, as in CantinaOS.
                        if let Command::Intent(IntentCommand::Music(MusicCommand::Play { query: Some(q) })) = &mut cmd {
                            if let Some(t) = q.parse::<usize>().ok().and_then(|n| state.music.library.get(n.wrapping_sub(1))) {
                                *q = t.clone();
                            }
                        }
                        next_id += 1;
                        let id = format!("cli{next_id}");
                        let msg = json!({ "kind": "command", "id": id, "body": cmd });
                        sink.send(Message::Text(msg.to_string().into())).await?;
                        if !interactive {
                            waiting = Some(id);
                        }
                    }
                }
            }
            msg = stream.next() => {
                let Some(msg) = msg else { printer("gateway closed the connection".into()); break };
                let Message::Text(t) = msg? else { continue };
                let Ok(env) = serde_json::from_str::<Envelope>(t.as_str()) else { continue };
                if let (Body::Ack(ack), Some(re)) = (&env.body, &env.re) {
                    if waiting.as_deref() == Some(re) {
                        waiting = None;
                    }
                    match ack {
                        Ack::Accepted if !interactive => printer(format!("ok ({re})")),
                        Ack::Accepted => {}
                        Ack::Rejected { reason } => printer(format!("rejected: {reason}")),
                    }
                }
                match env.body {
                    Body::Hello(h) => {
                        hello_seen = true;
                        emotes = h.profile.map(|p| p.emotes).unwrap_or_default();
                        state = h.state;
                    }
                    Body::State(u) => u.apply(&mut state),
                    Body::Event(e) => {
                        if let Some(s) = describe(&e) {
                            printer(s);
                        }
                    }
                    _ => {}
                }
            }
            // Script mode: give trailing events (console replies) a moment, then exit.
            _ = tokio::time::sleep(Duration::from_millis(1500)), if input_done && waiting.is_none() => break,
        }
    }
    Ok(())
}

fn status(s: &RetainedState) -> String {
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

fn describe(e: &Event) -> Option<String> {
    Some(match e {
        Event::Conversation(c) => match c {
            ConversationEvent::ListeningStarted => "[listening]".into(),
            ConversationEvent::ListeningStopped { transcript } if !transcript.is_empty() => format!("you: {transcript}"),
            ConversationEvent::IntentDetected { tool, .. } => format!("[intent {tool}]"),
            ConversationEvent::Reply { text } if !text.is_empty() => format!("R3X: {text}"),
            _ => return None,
        },
        Event::Ops(OpsEvent::Console { message, is_error }) => {
            if *is_error {
                format!("error: {message}")
            } else {
                message.clone()
            }
        }
        Event::Ops(OpsEvent::ServiceStatus { service, status: s @ (ServiceStatus::Error | ServiceStatus::Degraded), detail }) => {
            format!("[{service} {s:?}] {}", detail.as_deref().unwrap_or(""))
        }
        Event::Stage(StageEvent::ModeChanged { to, .. }) => format!("[mode {to:?}]"),
        Event::Stage(StageEvent::EngagementChanged { to, .. }) => format!("[engagement {to:?}]"),
        Event::Music(MusicEvent::TrackStarted { track }) => format!("[playing {}]", track.title),
        Event::Music(MusicEvent::TrackStopped) => "[music stopped]".into(),
        Event::Dj(DjEvent::Started) => "[DJ mode on]".into(),
        Event::Dj(DjEvent::Stopped) => "[DJ mode off]".into(),
        Event::Perf(PerfEvent::Ended { id, reason: r3x_contracts::EndReason::Rejected, .. }) => {
            format!("[show {id} rejected]")
        }
        _ => return None,
    })
}

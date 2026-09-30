//! `r3x-cli [--url ws://127.0.0.1:8780/] [-c LINE]...`
//!
//! Lines go to the runtime's command console as typed (shortcuts, `help`, `status`, ... are
//! answered there, the same as the panel's command line); replies are printed as they arrive.
//! Interactive with history (`~/.config/dj-r3x/cli_history`) unless `-c` lines are given, in
//! which case each is sent in order, its ack printed, and the client exits. Token:
//! `R3X_CLI_TOKEN` or `~/.config/dj-r3x/cli_token` (the runtime reads the same file).

use std::io::IsTerminal;
use std::time::Duration;

use anyhow::Context;
use futures_util::{SinkExt, StreamExt};
use r3x_cli::{classify, Local};
use r3x_contracts::{
    Ack, Body, Command, ConversationEvent, DjEvent, Envelope, Event, IntentCommand, MusicEvent, OpsEvent, PerfEvent, ServiceStatus,
    StageEvent,
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
                println!("usage: r3x-cli [--url URL] [-c LINE]...\n\nOnce connected, `help` lists the commands (the runtime answers it).");
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

    let mut waiting: Option<String> = None;
    let mut next_id = 0u64;
    let mut input_done = false;
    let mut hello_seen = false;

    loop {
        tokio::select! {
            line = lines.recv(), if hello_seen && waiting.is_none() && !input_done => {
                let Some(line) = line else { input_done = true; continue };
                match classify(&line) {
                    Local::Empty => {}
                    Local::Quit => break,
                    Local::Send(line) => {
                        next_id += 1;
                        let id = format!("cli{next_id}");
                        let cmd = Command::Intent(IntentCommand::Console { line });
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
                    // The console's reply is an `ops.console` event; only a refusal comes as the ack.
                    if let Ack::Rejected { reason } = ack {
                        printer(format!("rejected: {reason}"));
                    }
                }
                match env.body {
                    Body::Hello(_) => hello_seen = true,
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

fn describe(e: &Event) -> Option<String> {
    Some(match e {
        Event::Conversation(c) => match c {
            ConversationEvent::ListeningStarted => "[listening]".into(),
            ConversationEvent::ListeningStopped { transcript } if transcript.trim().is_empty() => "[nothing heard]".into(),
            ConversationEvent::ListeningStopped { transcript } => format!("you: {transcript}"),
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

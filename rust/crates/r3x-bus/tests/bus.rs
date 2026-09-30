use r3x_bus::{Bus, BusConfig, LogRecord, Received};
use r3x_contracts::{
    Ack, Body, Command, Domain, Event, IntentCommand, MessageClass, OperatingMode, PerfCommand,
    PerfEvent, Source, StageCommand, StageState,
};

fn sfx(id: &str) -> Event {
    Event::Perf(PerfEvent::Sfx { id: id.into() })
}

fn expect_msg(r: Option<Received>) -> std::sync::Arc<r3x_contracts::Envelope> {
    match r {
        Some(Received::Message(env)) => env,
        other => panic!("expected a message, got {other:?}"),
    }
}

#[tokio::test]
async fn stamps_monotonic_seq_and_source() {
    let bus = Bus::default();
    let mut rx = bus.subscribe(Domain::Perf);
    for i in 0..5 {
        bus.publish(Source::Idle, Some("turn-1".into()), sfx(&format!("s{i}")));
    }
    // State updates share the same sequence.
    bus.update(Source::System, |s: &mut StageState| s.mode = OperatingMode::Bench);
    bus.publish(Source::Claude, None, sfx("last"));

    let mut last = (0, 0.0);
    for _ in 0..6 {
        let env = expect_msg(rx.recv().await);
        assert!(env.seq > last.0 && env.t_mono >= last.1, "seq/t_mono must increase");
        assert!(env.t_wall > 1.6e9);
        last = (env.seq, env.t_mono);
    }
    assert_eq!(last.0, 7, "the state update took seq 6");
}

#[tokio::test]
async fn command_ack_round_trip() {
    let bus = Bus::default();
    let mut perf = bus.take_commands(MessageClass::Perf).unwrap();
    assert!(bus.take_commands(MessageClass::Perf).is_none(), "one owner per class");

    tokio::spawn(async move {
        while let Some(req) = perf.recv().await {
            let ack = match &req.command {
                Command::Perf(PerfCommand::Emote { slot }) if *slot < 8 => Ack::Accepted,
                _ => Ack::rejected("bad slot"),
            };
            assert_eq!(req.source(), Source::Ui);
            req.ack(ack);
        }
    });

    let emote = |slot| Command::Perf(PerfCommand::Emote { slot });
    assert_eq!(bus.command(Source::Ui, Some("c1".into()), emote(2)).await, Ack::Accepted);
    assert_eq!(bus.command(Source::Ui, None, emote(9)).await, Ack::rejected("bad slot"));
}

#[tokio::test]
async fn commands_never_hang() {
    let bus = Bus::default();
    let stage = Command::Stage(StageCommand::SetMode { mode: OperatingMode::Bench });

    // Handler dropped without acking.
    let mut rx = bus.take_commands(MessageClass::Stage).unwrap();
    tokio::spawn(async move { drop(rx.recv().await) });
    assert!(!bus.command(Source::Ui, None, stage.clone()).await.is_accepted());

    // Handler gone entirely (receiver dropped).
    assert!(!bus.command(Source::Ui, None, stage).await.is_accepted());

    // Public may only say.
    let freeze = Command::Perf(PerfCommand::Freeze { on: true });
    assert!(!bus.command(Source::Public, None, freeze).await.is_accepted());
}

#[tokio::test]
async fn lagging_receiver_resyncs_from_state() {
    let bus = Bus::new(BusConfig { event_capacity: 4, ..Default::default() });
    let mut rx = bus.subscribe(Domain::Perf);
    bus.set(Source::System, StageState { mode: OperatingMode::Studio, ..Default::default() });
    for i in 0..10 {
        bus.publish(Source::Idle, None, sfx(&i.to_string()));
    }
    match rx.recv().await {
        Some(Received::Lagged { missed, state }) => {
            assert_eq!(missed, 6);
            assert_eq!(state.stage.mode, OperatingMode::Studio);
        }
        other => panic!("expected lag, got {other:?}"),
    }
    // Then it continues with the newest retained events.
    let env = expect_msg(rx.recv().await);
    assert!(matches!(&env.body, Body::Event(Event::Perf(PerfEvent::Sfx { id })) if id == "6"));
}

#[tokio::test]
async fn late_joiner_sees_current_state() {
    let bus = Bus::default();
    assert!(bus.update(Source::System, |s: &mut StageState| s.mode = OperatingMode::Bench));
    assert!(!bus.update(Source::System, |s: &mut StageState| s.mode = OperatingMode::Bench), "no-op");

    let mut late = bus.watch::<StageState>();
    assert_eq!(late.borrow_and_update().mode, OperatingMode::Bench);
    assert_eq!(bus.snapshot().stage.mode, OperatingMode::Bench);

    bus.update(Source::System, |s: &mut StageState| {
        s.outputs.insert("neck".into(), true);
    });
    late.changed().await.unwrap();
    assert_eq!(late.borrow().outputs.get("neck"), Some(&true));
}

#[tokio::test]
async fn session_log_writes_jsonl() {
    let dir = std::env::temp_dir().join(format!("r3x-bus-log-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);

    let bus = Bus::default();
    let (path, writer) = bus.attach_session_log(&dir).await.unwrap();
    assert!(path.file_name().unwrap().to_str().unwrap().starts_with("session-"));

    bus.publish(Source::Claude, Some("turn-9".into()), sfx("zap"));
    bus.set(Source::System, StageState { mode: OperatingMode::Bench, ..Default::default() });
    let say = Command::Intent(IntentCommand::Say { text: "hi".into() });
    bus.command(Source::Cli, Some("c7".into()), say).await; // no handler: rejected, still logged
    drop(bus);
    writer.await.unwrap().unwrap();

    let text = std::fs::read_to_string(&path).unwrap();
    let recs: Vec<LogRecord> = text.lines().map(|l| serde_json::from_str(l).unwrap()).collect();
    let topics: Vec<_> = recs.iter().map(|r| r.topic.as_str()).collect();
    assert_eq!(topics, ["perf.sfx", "state.stage", "command.intent.say", "ack"]);
    assert!(recs.windows(2).all(|w| w[0].seq < w[1].seq));
    assert_eq!(recs[0].source, Source::Claude);
    assert_eq!(recs[0].conversation_id.as_deref(), Some("turn-9"));
    assert_eq!(recs[0].payload["id"], "zap");
    assert_eq!(recs[1].payload["state"]["mode"], "bench");
    assert_eq!(recs[3].payload["status"], "rejected");
    std::fs::remove_dir_all(&dir).unwrap();
}

#[tokio::test]
async fn frames_fan_out_off_the_tap() {
    let bus = Bus::default();
    let mut all = bus.subscribe_all();
    let mut frames = bus.subscribe_frames();
    bus.publish_frames(r3x_contracts::Frames { t_mono: 0.5, ..Default::default() });
    assert_eq!(frames.recv().await.unwrap().t_mono, 0.5);
    bus.publish(Source::Idle, None, sfx("after"));
    assert!(matches!(&expect_msg(all.recv().await).body, Body::Event(_)), "frames never hit the tap");
}

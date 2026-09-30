//! Live checks against the real services. Paid: run deliberately with
//! `cargo test -p r3x-voice --test live -- --ignored`.

use std::time::Duration;

use r3x_bus::Bus;
use r3x_contracts::{Engagement, EngagementState, Source};
use r3x_voice::eleven::{open_speech, Eleven};
use r3x_voice::VoiceSettings;

/// One ElevenLabs line (dialogue socket, with timings) fed as 20 ms chunks to one Deepgram turn.
#[tokio::test]
#[ignore = "paid: 1 ElevenLabs + 1 Deepgram request"]
async fn elevenlabs_to_deepgram_roundtrip() {
    let s = VoiceSettings::from_env().expect("keys in env or .env");
    let eleven = Eleven::start(s.eleven.clone());
    let (first, mut rest) = open_speech(eleven.as_ref(), "Play some cantina music.").await.unwrap();
    let first = first.expect("audio");
    assert!(first.alignment.is_some(), "dialogue socket returns character timing");
    let mut pcm = first.pcm;
    while let Some(c) = rest.recv().await {
        pcm.extend(c.unwrap().pcm);
    }

    let bus = Bus::default();
    bus.set(Source::System, EngagementState { engagement: Engagement::Interactive });
    let stt = r3x_voice::deepgram::Stt::spawn(s.deepgram.clone(), r3x_voice::voice::interactive(&bus));
    tokio::time::timeout(Duration::from_secs(10), stt.connected().wait_for(|u| *u)).await.unwrap().unwrap();
    stt.begin("live").await.unwrap();
    let mut rs = r3x_audio::resample::Resampler::new(24_000, 16_000);
    let mut f16 = Vec::new();
    rs.process(&pcm.iter().map(|&x| r3x_audio::i16_to_f32(x)).collect::<Vec<_>>(), &mut f16);
    for c in f16.chunks(r3x_audio::MIC_CHUNK) {
        stt.audio(c.iter().map(|&x| r3x_audio::f32_to_i16(x)).collect());
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
    let text = stt.finish().await.to_lowercase();
    assert!(text.contains("cantina") && text.contains("music"), "{text}");
}

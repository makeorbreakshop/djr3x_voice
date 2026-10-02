//! The output mix to gateway clients that record it (`telemetry.mix_audio`): every 20 ms,
//! while anyone is subscribed, the mixer's tap is drained into one 16-bit PCM chunk.

use std::sync::Arc;
use std::time::Duration;

use r3x_audio::mixer::MixerHandle;
use r3x_contracts::{AudioDirection, AudioMeta};
use r3x_gateway::AudioOut;
use tokio::sync::broadcast;

/// About 5 s of 20 ms chunks before a slow client starts losing audio.
const QUEUE: usize = 256;

pub fn spawn(mixer: &MixerHandle) -> broadcast::Sender<Arc<AudioOut>> {
    let (tx, _) = broadcast::channel::<Arc<AudioOut>>(QUEUE);
    let tap = mixer.tap();
    let feed = tx.clone();
    tokio::spawn(async move {
        let meta = AudioMeta { direction: AudioDirection::Mix, sample_rate: tap.sample_rate(), channels: tap.channels() as u8 };
        let mut tick = tokio::time::interval(Duration::from_millis(20));
        tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
        let mut buf = Vec::new();
        loop {
            tick.tick().await;
            let listening = feed.receiver_count() > 0;
            if listening != tap.is_on() {
                tap.set(listening);
                tracing::info!("output mix tap {}", if listening { "on" } else { "off" });
            }
            if !listening {
                continue;
            }
            buf.clear();
            tap.drain(&mut buf);
            if buf.is_empty() {
                continue;
            }
            let pcm: Vec<u8> = buf.iter().flat_map(|&s| r3x_audio::f32_to_i16(s).to_le_bytes()).collect();
            let _ = feed.send(Arc::new(AudioOut { meta: meta.clone(), pcm: pcm.into() }));
        }
    });
    tx
}

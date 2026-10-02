//! Remote voice over the gateway (plan §3b `audio`).
//!
//! - client -> runtime: after `ptt_start`, an `audio` meta `{direction:"in", sample_rate:16000,
//!   channels:1}` then binary 16-bit LE PCM, 20 ms per frame, until `ptt_stop`.
//! - runtime -> client: `audio` meta `{direction:"out", sample_rate:24000, channels:1}` + one
//!   binary frame per 20 ms of TTS, paced in real time. Character timings and mouth levels
//!   arrive as ordinary `conversation` events (`speech_timing`, `mouth`).
//! - A zero-length binary frame after `out` meta marks a line's end; `Abort` is sent as a
//!   zero-length frame too, preceded by the out meta with `channels: 0`.

use r3x_audio::sink::{RemoteAudio, RemoteSink};
use r3x_audio::{MIC_RATE, TTS_RATE};
use r3x_contracts::envelope::{AudioDirection, AudioMeta};
use r3x_gateway::{AudioHooks, AudioIn, AudioOut};
use std::sync::Arc;
use tokio::sync::{broadcast, mpsc};

use crate::voice::Voice;

pub fn pcm16le(bytes: &[u8]) -> Vec<i16> {
    bytes.chunks_exact(2).map(|b| i16::from_le_bytes([b[0], b[1]])).collect()
}

pub fn to_pcm16le(samples: &[i16]) -> Vec<u8> {
    samples.iter().flat_map(|s| s.to_le_bytes()).collect()
}

/// Gateway audio hooks wired to `voice` (inbound) and `sink` (outbound).
pub fn hooks(voice: &Voice, sink: &RemoteSink) -> AudioHooks {
    let (in_tx, mut in_rx) = mpsc::channel::<AudioIn>(512);
    let v = voice.clone();
    tokio::spawn(async move {
        while let Some(a) = in_rx.recv().await {
            let rate = a.meta.as_ref().map_or(MIC_RATE, |m| m.sample_rate);
            if rate != MIC_RATE || a.meta.as_ref().is_some_and(|m| m.channels != 1) {
                tracing::warn!(client = %a.client, ?a.meta, "remote audio must be 16 kHz mono; dropped");
                continue;
            }
            v.remote_audio(&a.client, &pcm16le(&a.pcm)).await;
        }
    });

    let out = broadcast::channel::<Arc<AudioOut>>(1024).0;
    let tx = out.clone();
    let mut frames = sink.subscribe();
    tokio::spawn(async move {
        let meta = |channels| AudioMeta { direction: AudioDirection::Out, sample_rate: TTS_RATE, channels };
        loop {
            let chunk = match frames.recv().await {
                Ok(RemoteAudio::Pcm { samples, .. }) => AudioOut { meta: meta(1), pcm: to_pcm16le(&samples).into() },
                Ok(RemoteAudio::LineEnd { .. }) => AudioOut { meta: meta(1), pcm: Vec::new().into() },
                Ok(RemoteAudio::Abort) => AudioOut { meta: meta(0), pcm: Vec::new().into() },
                Err(broadcast::error::RecvError::Lagged(n)) => {
                    tracing::warn!(n, "remote audio lagged");
                    continue;
                }
                Err(_) => return,
            };
            let _ = tx.send(Arc::new(chunk));
        }
    });
    AudioHooks { inbound: Some(in_tx), outbound: Some(out), mix: None }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pcm_round_trip() {
        let s = vec![0, 1, -1, i16::MAX, i16::MIN];
        assert_eq!(pcm16le(&to_pcm16le(&s)), s);
    }
}

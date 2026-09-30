//! Real devices through cpal: the output engine (mixer on the device callback) and mic
//! capture as 16 kHz mono, 20 ms chunks.
//!
//! cpal streams are not `Send` on every platform, so each lives on its own thread and is
//! dropped (stopped) when its owner handle is dropped.

use std::sync::mpsc as std_mpsc;
use std::thread;
use std::time::Instant;

use anyhow::{anyhow, Context, Result};
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use tokio::sync::mpsc;

use crate::mixer::{mixer, MixerHandle, RenderTime};
use crate::resample::Resampler;
use crate::{f32_to_i16, MIC_CHUNK, MIC_RATE};

#[derive(Debug, Clone)]
pub struct DeviceInfo {
    pub name: String,
    pub input: bool,
    pub output: bool,
    pub default: bool,
}

pub fn list_devices() -> Result<Vec<DeviceInfo>> {
    let host = cpal::default_host();
    let def_in = host.default_input_device().map(|d| d.to_string());
    let def_out = host.default_output_device().map(|d| d.to_string());
    let mut out: Vec<DeviceInfo> = Vec::new();
    for d in host.devices()? {
        let name = d.to_string();
        out.push(DeviceInfo {
            default: Some(&name) == def_in.as_ref() || Some(&name) == def_out.as_ref(),
            input: d.supports_input(),
            output: d.supports_output(),
            name,
        });
    }
    Ok(out)
}

/// `None` or empty = system default; otherwise the first device whose name contains `name`
/// (case-insensitive).
fn pick(input: bool, name: Option<&str>) -> Result<cpal::Device> {
    let host = cpal::default_host();
    let want = name.map(str::trim).filter(|n| !n.is_empty()).map(str::to_lowercase);
    let Some(want) = want else {
        let d = if input { host.default_input_device() } else { host.default_output_device() };
        return d.ok_or_else(|| anyhow!("no default {} device", if input { "input" } else { "output" }));
    };
    let devices = if input { host.input_devices()?.collect::<Vec<_>>() } else { host.output_devices()?.collect() };
    devices
        .into_iter()
        .find(|d| d.to_string().to_lowercase().contains(&want))
        .ok_or_else(|| anyhow!("no {} device matching {want:?}", if input { "input" } else { "output" }))
}

/// A running output device with the mixer on its callback.
pub struct OutputEngine {
    pub mixer: MixerHandle,
    pub device: String,
    _stop: std_mpsc::Sender<()>,
}

impl OutputEngine {
    pub fn start(device: Option<&str>) -> Result<Self> {
        let (ready_tx, ready_rx) = std_mpsc::channel();
        let (stop_tx, stop_rx) = std_mpsc::channel::<()>();
        let device = device.map(str::to_owned);
        thread::Builder::new().name("r3x-audio-out".into()).spawn(move || {
            let run = || -> Result<(cpal::Stream, MixerHandle, String)> {
                let dev = pick(false, device.as_deref())?;
                let name = dev.to_string();
                let cfg = dev.default_output_config().context("output config")?.config();
                let (mut m, handle) = mixer(cfg.sample_rate, cfg.channels as usize);
                let stream = dev.build_output_stream::<f32, _, _>(
                    cfg,
                    move |out, info| {
                        let ts = info.timestamp();
                        let when = RenderTime { at: Instant::now(), latency: ts.playback.duration_since(ts.callback) };
                        m.render(out, &when);
                    },
                    |e| tracing::warn!(error = %e, "audio output stream error"),
                    None,
                )?;
                stream.play()?;
                Ok((stream, handle, name))
            };
            match run() {
                Ok((stream, handle, name)) => {
                    let _ = ready_tx.send(Ok((handle, name)));
                    let _ = stop_rx.recv(); // until the engine is dropped
                    drop(stream);
                }
                Err(e) => {
                    let _ = ready_tx.send(Err(e));
                }
            }
        })?;
        let (mixer, device) = ready_rx.recv().map_err(|_| anyhow!("audio thread died"))??;
        tracing::info!(%device, rate = mixer.sample_rate(), channels = mixer.channels(), "audio output started");
        Ok(Self { mixer, device, _stop: stop_tx })
    }

    /// No device (`--audio null`): a thread renders the mixer into nothing at 48 kHz stereo,
    /// in 10 ms blocks, `speed` times faster than real time (1.0 = real time; tests go faster
    /// so speech and music finish sooner). Everything downstream (speech timing, mouth,
    /// music position, crossfades, ducking) runs exactly as on a device.
    pub fn null(speed: f64) -> Self {
        const RATE: u32 = 48_000;
        let (mut m, mixer) = mixer(RATE, 2);
        let (stop_tx, stop_rx) = std_mpsc::channel::<()>();
        let block = std::time::Duration::from_millis(10);
        let pause = block.div_f64(speed.max(0.01));
        thread::Builder::new()
            .name("r3x-audio-null".into())
            .spawn(move || {
                let mut out = vec![0.0f32; (RATE / 100) as usize * 2];
                let mut next = Instant::now();
                while matches!(stop_rx.try_recv(), Err(std_mpsc::TryRecvError::Empty)) {
                    out.fill(0.0);
                    m.render(&mut out, &RenderTime::now());
                    next += pause;
                    if let Some(d) = next.checked_duration_since(Instant::now()) {
                        thread::sleep(d);
                    } else {
                        next = Instant::now();
                    }
                }
            })
            .expect("spawn null audio thread");
        tracing::info!(speed, "audio output: null device");
        Self { mixer, device: "null".into(), _stop: stop_tx }
    }
}

/// An open microphone. Chunks are 16 kHz mono i16, 20 ms each. Dropping it closes the device.
pub struct MicCapture {
    pub chunks: mpsc::Receiver<Vec<i16>>,
    pub device: String,
    _stop: std_mpsc::Sender<()>,
}

/// Keeps a mic open; drop to close it.
pub struct MicGuard(#[allow(dead_code)] std_mpsc::Sender<()>);

impl MicCapture {
    pub fn split(self) -> (mpsc::Receiver<Vec<i16>>, MicGuard) {
        (self.chunks, MicGuard(self._stop))
    }

    pub fn open(device: Option<&str>) -> Result<Self> {
        let (ready_tx, ready_rx) = std_mpsc::channel();
        let (stop_tx, stop_rx) = std_mpsc::channel::<()>();
        let (tx, chunks) = mpsc::channel(256);
        let device = device.map(str::to_owned);
        thread::Builder::new().name("r3x-audio-mic".into()).spawn(move || {
            let run = || -> Result<(cpal::Stream, String)> {
                let dev = pick(true, device.as_deref())?;
                let name = dev.to_string();
                let cfg = dev.default_input_config().context("input config")?.config();
                let ch = cfg.channels as usize;
                let mut rs = Resampler::new(cfg.sample_rate, MIC_RATE);
                let mut mono = Vec::with_capacity(4096);
                let mut out16 = Vec::with_capacity(4096);
                let mut pending: Vec<i16> = Vec::with_capacity(MIC_CHUNK * 4);
                let stream = dev.build_input_stream::<f32, _, _>(
                    cfg,
                    move |data: &[f32], _| {
                        mono.clear();
                        mono.extend(data.chunks(ch).map(|f| f.iter().sum::<f32>() / ch as f32));
                        out16.clear();
                        rs.process(&mono, &mut out16);
                        pending.extend(out16.iter().map(|&s| f32_to_i16(s)));
                        while pending.len() >= MIC_CHUNK {
                            let chunk: Vec<i16> = pending.drain(..MIC_CHUNK).collect();
                            if tx.try_send(chunk).is_err() {
                                tracing::warn!("mic chunk dropped (consumer behind)");
                            }
                        }
                    },
                    |e| tracing::warn!(error = %e, "mic stream error"),
                    None,
                )?;
                stream.play()?;
                Ok((stream, name))
            };
            match run() {
                Ok((stream, name)) => {
                    let _ = ready_tx.send(Ok(name));
                    let _ = stop_rx.recv();
                    drop(stream);
                }
                Err(e) => {
                    let _ = ready_tx.send(Err(e));
                }
            }
        })?;
        let device = ready_rx.recv().map_err(|_| anyhow!("mic thread died"))??;
        Ok(Self { chunks, device, _stop: stop_tx })
    }
}

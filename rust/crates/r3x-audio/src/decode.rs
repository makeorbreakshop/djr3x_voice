//! File decoding (symphonia: `.mp3 .wav .m4a`, plus flac/ogg) folded to mono or stereo and
//! resampled (rubato, FFT) to a target rate, as planar `f32` blocks.
//!
//! Used by music streaming ([`crate::music`]), SFX ([`crate::sfx`]), beat analysis and CLAP
//! (`r3x-beats`, `r3x-music`), so every consumer hears the same decode.

use std::fs::File;
use std::path::Path;

use anyhow::{anyhow, Context, Result};
use rubato::{FftFixedIn, Resampler as _};
use symphonia::core::audio::SampleBuffer;
use symphonia::core::codecs::{Decoder, DecoderOptions, CODEC_TYPE_NULL};
use symphonia::core::errors::Error as SymError;
use symphonia::core::formats::{FormatOptions, FormatReader};
use symphonia::core::io::MediaSourceStream;
use symphonia::core::meta::MetadataOptions;
use symphonia::core::probe::Hint;

/// Extensions the music library accepts (CantinaOS: `.mp3 .wav .m4a`).
pub const MUSIC_EXTENSIONS: [&str; 3] = ["mp3", "wav", "m4a"];

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct FileInfo {
    pub sample_rate: u32,
    pub channels: usize,
    pub duration_s: Option<f64>,
}

struct Opened {
    format: Box<dyn FormatReader>,
    decoder: Box<dyn Decoder>,
    track_id: u32,
    info: FileInfo,
}

fn open(path: &Path) -> Result<Opened> {
    let file = File::open(path).with_context(|| format!("open {}", path.display()))?;
    let mss = MediaSourceStream::new(Box::new(file), Default::default());
    let mut hint = Hint::new();
    if let Some(ext) = path.extension().and_then(|e| e.to_str()) {
        hint.with_extension(ext);
    }
    let fmt_opts = FormatOptions { enable_gapless: true, ..Default::default() };
    let probed = symphonia::default::get_probe()
        .format(&hint, mss, &fmt_opts, &MetadataOptions::default())
        .with_context(|| format!("probe {}", path.display()))?;
    let format = probed.format;
    let track = format
        .tracks()
        .iter()
        .find(|t| t.codec_params.codec != CODEC_TYPE_NULL)
        .ok_or_else(|| anyhow!("no audio track in {}", path.display()))?;
    let p = &track.codec_params;
    let sample_rate = p.sample_rate.ok_or_else(|| anyhow!("unknown sample rate"))?;
    let channels = p.channels.map(|c| c.count()).unwrap_or(2).max(1);
    let duration_s = p.n_frames.map(|n| n as f64 / sample_rate as f64);
    let decoder = symphonia::default::get_codecs().make(p, &DecoderOptions::default())?;
    let track_id = track.id;
    Ok(Opened { format, decoder, track_id, info: FileInfo { sample_rate, channels, duration_s } })
}

/// Rate, channels and duration. When the container does not state a length (CBR mp3 without
/// a Xing/Info header), packets are counted without decoding them.
pub fn probe(path: &Path) -> Result<FileInfo> {
    let mut o = open(path)?;
    if o.info.duration_s.is_none() {
        let tb = o.format.tracks().iter().find(|t| t.id == o.track_id).and_then(|t| t.codec_params.time_base);
        let mut ts: u64 = 0;
        loop {
            match o.format.next_packet() {
                Ok(p) if p.track_id() == o.track_id => ts = ts.max(p.ts() + p.dur()),
                Ok(_) => {}
                Err(_) => break,
            }
        }
        o.info.duration_s = Some(match tb {
            Some(tb) => {
                let t = tb.calc_time(ts);
                t.seconds as f64 + t.frac
            }
            None => ts as f64 / o.info.sample_rate as f64,
        });
    }
    Ok(o.info)
}

/// Streaming decoder: planar blocks, `channels` (1 or 2) wide, at `rate`.
pub struct PlanarStream {
    o: Opened,
    out_channels: usize,
    rs: Option<Rs>,
    sb: Option<SampleBuffer<f32>>,
    done: bool,
}

struct Rs {
    r: FftFixedIn<f32>,
    pending: Vec<Vec<f32>>,
    /// Output frames still to drop (the resampler's delay), so t=0 stays t=0.
    skip: usize,
    ratio: f64,
    fed: u64,
    emitted: u64,
}

impl Rs {
    fn run(&mut self, chunk: &[Vec<f32>], out: &mut [Vec<f32>]) -> Result<()> {
        let res = self.r.process(chunk, None).map_err(|e| anyhow!("resample: {e}"))?;
        let n = res[0].len();
        let drop = self.skip.min(n);
        self.skip -= drop;
        self.emitted += (n - drop) as u64;
        for (o, r) in out.iter_mut().zip(res) {
            o.extend_from_slice(&r[drop..]);
        }
        Ok(())
    }
}

impl PlanarStream {
    pub fn open(path: &Path, rate: u32, channels: usize) -> Result<Self> {
        assert!(channels == 1 || channels == 2, "fold to mono or stereo");
        let o = open(path)?;
        let rs = if o.info.sample_rate != rate {
            let r = FftFixedIn::<f32>::new(o.info.sample_rate as usize, rate as usize, 1024, 2, channels)
                .map_err(|e| anyhow!("resampler: {e}"))?;
            let skip = r.output_delay();
            let ratio = rate as f64 / o.info.sample_rate as f64;
            Some(Rs { r, pending: vec![Vec::new(); channels], skip, ratio, fed: 0, emitted: 0 })
        } else {
            None
        };
        Ok(Self { o, out_channels: channels, rs, sb: None, done: false })
    }

    pub fn info(&self) -> FileInfo {
        self.o.info
    }

    /// Next block, or `None` at the end of the file.
    pub fn next_block(&mut self) -> Result<Option<Vec<Vec<f32>>>> {
        loop {
            if self.done {
                return Ok(None);
            }
            let Some(raw) = self.decode_packet()? else {
                self.done = true;
                return self.flush();
            };
            let Some(rs) = &mut self.rs else { return Ok(Some(raw)) };
            rs.fed += raw[0].len() as u64;
            for (p, r) in rs.pending.iter_mut().zip(raw) {
                p.extend(r);
            }
            let mut out: Vec<Vec<f32>> = vec![Vec::new(); self.out_channels];
            while rs.pending[0].len() >= rs.r.input_frames_next() {
                let n = rs.r.input_frames_next();
                let chunk: Vec<Vec<f32>> = rs.pending.iter_mut().map(|p| p.drain(..n).collect()).collect();
                rs.run(&chunk, &mut out)?;
            }
            if !out[0].is_empty() {
                return Ok(Some(out));
            }
        }
    }

    fn flush(&mut self) -> Result<Option<Vec<Vec<f32>>>> {
        let Some(rs) = &mut self.rs else { return Ok(None) };
        let mut out: Vec<Vec<f32>> = vec![Vec::new(); self.out_channels];
        // Feed the tail plus silence until the output covers the whole input, then trim.
        let target = (rs.fed as f64 * rs.ratio).round() as u64;
        let mut tail = std::mem::take(&mut rs.pending);
        while rs.emitted < target {
            let n = rs.r.input_frames_next();
            for c in &mut tail {
                c.resize(c.len().max(n), 0.0);
            }
            let chunk: Vec<Vec<f32>> = tail.iter_mut().map(|c| c.drain(..n).collect()).collect();
            rs.run(&chunk, &mut out)?;
        }
        let extra = (rs.emitted - target) as usize;
        for c in &mut out {
            c.truncate(c.len().saturating_sub(extra));
        }
        Ok((!out[0].is_empty()).then_some(out))
    }

    /// One decoded packet folded to the output channel count, at the file's rate.
    fn decode_packet(&mut self) -> Result<Option<Vec<Vec<f32>>>> {
        loop {
            let packet = match self.o.format.next_packet() {
                Ok(p) => p,
                Err(SymError::IoError(e)) if e.kind() == std::io::ErrorKind::UnexpectedEof => return Ok(None),
                Err(SymError::ResetRequired) => return Ok(None),
                Err(e) => return Err(e.into()),
            };
            if packet.track_id() != self.o.track_id {
                continue;
            }
            let buf = match self.o.decoder.decode(&packet) {
                Ok(b) => b,
                Err(SymError::DecodeError(e)) => {
                    tracing::debug!("skipping undecodable packet: {e}");
                    continue;
                }
                Err(e) => return Err(e.into()),
            };
            let spec = *buf.spec();
            let frames = buf.frames();
            if frames == 0 {
                continue;
            }
            let sb = match &mut self.sb {
                Some(sb) if sb.capacity() >= buf.capacity() * spec.channels.count() => sb,
                _ => self.sb.insert(SampleBuffer::<f32>::new(buf.capacity() as u64, spec)),
            };
            sb.copy_interleaved_ref(buf);
            let ch = spec.channels.count().max(1);
            let s = &sb.samples()[..frames * ch];
            return Ok(Some(fold(s, ch, self.out_channels)));
        }
    }
}

/// Interleaved `ch`-wide -> planar mono (mean) or stereo (mono duplicated; surround keeps L/R).
fn fold(s: &[f32], ch: usize, out: usize) -> Vec<Vec<f32>> {
    let frames = s.len() / ch;
    if out == 1 {
        return vec![s.chunks(ch).map(|f| f.iter().sum::<f32>() / ch as f32).collect()];
    }
    let mut l = Vec::with_capacity(frames);
    let mut r = Vec::with_capacity(frames);
    for f in s.chunks(ch) {
        l.push(f[0]);
        r.push(if ch > 1 { f[1] } else { f[0] });
    }
    vec![l, r]
}

/// Decode a whole file to planar `channels` (1 or 2) at `rate`.
pub fn decode_file(path: &Path, rate: u32, channels: usize) -> Result<Vec<Vec<f32>>> {
    let mut st = PlanarStream::open(path, rate, channels)?;
    let mut out: Vec<Vec<f32>> = vec![Vec::new(); channels];
    while let Some(b) = st.next_block()? {
        for (o, c) in out.iter_mut().zip(b) {
            o.extend(c);
        }
    }
    Ok(out)
}

/// Planar stereo -> interleaved in the device's channel layout (1 = mono mix; >2 = L, R, 0...).
pub fn interleave_for_device(planar: &[Vec<f32>], dev_channels: usize, out: &mut Vec<f32>) {
    let (l, r) = (&planar[0], planar.get(1).unwrap_or(&planar[0]));
    out.reserve(l.len() * dev_channels);
    for i in 0..l.len() {
        match dev_channels {
            1 => out.push(0.5 * (l[i] + r[i])),
            n => {
                out.push(l[i]);
                out.push(r[i]);
                out.extend(std::iter::repeat_n(0.0, n - 2));
            }
        }
    }
}

/// A 16-bit PCM WAV, for tests and fixtures.
pub fn wav_bytes(rate: u32, channels: u16, samples: &[f32]) -> Vec<u8> {
    let data_len = (samples.len() * 2) as u32;
    let mut v = Vec::with_capacity(44 + data_len as usize);
    v.extend_from_slice(b"RIFF");
    v.extend_from_slice(&(36 + data_len).to_le_bytes());
    v.extend_from_slice(b"WAVEfmt ");
    v.extend_from_slice(&16u32.to_le_bytes());
    v.extend_from_slice(&1u16.to_le_bytes());
    v.extend_from_slice(&channels.to_le_bytes());
    v.extend_from_slice(&rate.to_le_bytes());
    v.extend_from_slice(&(rate * channels as u32 * 2).to_le_bytes());
    v.extend_from_slice(&(channels * 2).to_le_bytes());
    v.extend_from_slice(&16u16.to_le_bytes());
    v.extend_from_slice(b"data");
    v.extend_from_slice(&data_len.to_le_bytes());
    for s in samples {
        v.extend_from_slice(&crate::f32_to_i16(*s).to_le_bytes());
    }
    v
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tone(rate: u32, secs: f64, hz: f64) -> Vec<f32> {
        (0..(rate as f64 * secs) as usize).map(|i| (0.5 * (i as f64 * hz * std::f64::consts::TAU / rate as f64).sin()) as f32).collect()
    }

    #[test]
    fn wav_decodes_resamples_and_keeps_length() {
        let dir = std::env::temp_dir().join(format!("r3x-decode-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let p = dir.join("t.wav");
        std::fs::write(&p, wav_bytes(44_100, 1, &tone(44_100, 2.0, 440.0))).unwrap();
        let info = probe(&p).unwrap();
        assert_eq!((info.sample_rate, info.channels), (44_100, 1));
        assert!((info.duration_s.unwrap() - 2.0).abs() < 1e-3);

        let st = decode_file(&p, 48_000, 2).unwrap();
        assert_eq!(st.len(), 2);
        assert!((st[0].len() as i64 - 96_000).abs() < 64, "{}", st[0].len());
        assert_eq!(st[0][10_000], st[1][10_000], "mono duplicated");
        let rms = (st[0][4800..90_000].iter().map(|x| x * x).sum::<f32>() / 85_200.0).sqrt();
        assert!((rms - 0.5 / 2f32.sqrt()).abs() < 0.01, "{rms}");
        // No shift: the first zero-crossing stays at t=0 (resampler delay trimmed).
        assert!(st[0][0].abs() < 0.05 && st[0][27] > 0.3, "{} {}", st[0][0], st[0][27]);
        std::fs::remove_dir_all(dir).ok();
    }
}

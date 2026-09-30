//! Cameras behind two traits, so the loop runs on a fake in tests.
//!
//! The real one is AVFoundation through an `ffmpeg` child (the same device list and indexes
//! CantinaOS reads, `vision_service.py` `_get_camera_names`): ffmpeg drops to the loop rate and
//! scales to a fixed size, so a frame is exactly `w * h * 3` bytes on its stdout. The device is
//! asked for the lowest capture rate it supports at or above the loop rate (5, 10, 15, 24, then
//! 30 fps), so ffmpeg does not convert 30 frames a second to keep 5. Continuity
//! (iPhone), Desk View and screen-capture devices are never auto-selected.

use std::io::Read;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use serde::Serialize;

use crate::frame::Frame;
use crate::VisionError;

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct CameraInfo {
    pub index: u32,
    pub name: String,
}

impl CameraInfo {
    /// iPhone / Continuity / Desk View / screen capture: listed, never auto-selected.
    pub fn is_continuity(&self) -> bool {
        let n = self.name.to_lowercase();
        ["iphone", "continuity", "desk view", "capture screen"].iter().any(|m| n.contains(m))
    }
}

/// First physical, non-Continuity camera, unless `preferred` is given.
pub fn pick(cameras: &[CameraInfo], preferred: Option<u32>) -> Option<u32> {
    preferred.or_else(|| cameras.iter().find(|c| !c.is_continuity()).map(|c| c.index))
}

/// A stream of frames. Blocking; `Ok(None)` = the stream ended.
pub trait FrameSource: Send {
    fn next_frame(&mut self) -> Result<Option<Frame>, VisionError>;
}

/// Lists and opens cameras.
pub trait Camera: Send {
    fn list(&self) -> Vec<CameraInfo>;
    fn open(&self, index: u32, fps: f64) -> Result<Box<dyn FrameSource>, VisionError>;
}

/// Parse `ffmpeg -f avfoundation -list_devices true -i ""` stderr: video devices only, screen
/// captures dropped (as CantinaOS).
pub fn parse_avfoundation_list(stderr: &str) -> Vec<CameraInfo> {
    let mut out = Vec::new();
    let mut video = false;
    for line in stderr.lines() {
        if line.contains("AVFoundation video devices:") {
            video = true;
            continue;
        }
        if line.contains("AVFoundation audio devices:") {
            break;
        }
        if !video {
            continue;
        }
        // "[AVFoundation indev @ 0x...] [0] FaceTime HD Camera"
        let Some(rest) = line.split("] [").nth(1) else { continue };
        let Some((idx, name)) = rest.split_once("] ") else { continue };
        let (Ok(index), name) = (idx.trim().parse::<u32>(), name.trim()) else { continue };
        if !name.to_lowercase().starts_with("capture screen") {
            out.push(CameraInfo { index, name: name.to_string() });
        }
    }
    out
}

/// AVFoundation through `ffmpeg` (`R3X_FFMPEG`, else `ffmpeg` on PATH).
pub struct FfmpegCamera {
    pub ffmpeg: String,
    pub width: u32,
    pub height: u32,
}

impl Default for FfmpegCamera {
    fn default() -> Self {
        Self { ffmpeg: std::env::var("R3X_FFMPEG").unwrap_or_else(|_| "ffmpeg".into()), width: 1280, height: 720 }
    }
}

impl Camera for FfmpegCamera {
    fn list(&self) -> Vec<CameraInfo> {
        let out = Command::new(&self.ffmpeg)
            .args(["-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""])
            .stdin(Stdio::null())
            .output();
        match out {
            Ok(o) => parse_avfoundation_list(&String::from_utf8_lossy(&o.stderr)),
            Err(e) => {
                tracing::warn!("cannot list cameras with {}: {e}", self.ffmpeg);
                Vec::new()
            }
        }
    }

    fn open(&self, index: u32, fps: f64) -> Result<Box<dyn FrameSource>, VisionError> {
        let (w, h) = (self.width, self.height);
        let vf = format!("fps={fps},scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2");
        let rates = capture_rates(fps);
        for (i, rate) in rates.iter().enumerate() {
            let last = i + 1 == rates.len();
            let child = Command::new(&self.ffmpeg)
                // nv12: a native format of Mac cameras; the default (yuv420p) makes ffmpeg complain.
                .args(["-hide_banner", "-loglevel", "error", "-f", "avfoundation", "-framerate", &rate.to_string(), "-pixel_format", "nv12", "-i"])
                .arg(format!("{index}:none"))
                .args(["-vf", &vf, "-pix_fmt", "rgb24", "-f", "rawvideo", "-"])
                .stdin(Stdio::null())
                .stdout(Stdio::piped())
                // An unsupported rate is expected on the way down the list: keep that quiet.
                .stderr(if last { Stdio::inherit() } else { Stdio::null() })
                .spawn()
                .map_err(|e| VisionError::Camera(format!("{}: {e}", self.ffmpeg)))?;
            let mut src = PipeSource::new(child, w, h);
            match src.first_frame(FIRST_FRAME_TIMEOUT) {
                Ok(Some(first)) => {
                    tracing::info!("camera {index} capturing at {rate} fps");
                    src.pending = Some(first);
                    return Ok(Box::new(src));
                }
                // Silent past the deadline: the camera is busy, not refusing a rate. Stop here.
                Err(VisionError::Camera(e)) => return Err(VisionError::Camera(format!("camera {index} gave {e}"))),
                // The device refused this rate (ffmpeg exits before the first frame): try the next.
                _ if !last => continue,
                Ok(None) => return Err(VisionError::Camera(format!("camera {index} gave no frames"))),
                Err(e) => return Err(e),
            }
        }
        Err(VisionError::Camera(format!("camera {index}: no capture rate")))
    }
}

/// Capture rates to ask the device for, lowest first: at or above the loop rate, then 30.
pub fn capture_rates(fps: f64) -> Vec<u32> {
    let mut v: Vec<u32> = [5, 10, 15, 24].into_iter().filter(|r| fps > 0.0 && f64::from(*r) >= fps).collect();
    v.push(30);
    v
}

/// Capture children alive in this process. Their `PipeSource`s live on the capture thread,
/// which process exit does not unwind, so the runtime's shutdown (and a panic on the main
/// thread) calls [`kill_captures`]; otherwise the camera would stay on after the runtime.
static CAPTURES: Mutex<Vec<u32>> = Mutex::new(Vec::new());

fn captures() -> std::sync::MutexGuard<'static, Vec<u32>> {
    CAPTURES.lock().unwrap_or_else(|e| e.into_inner())
}

/// SIGKILL every live capture child (the camera turns off). Safe to call more than once.
pub fn kill_captures() {
    for pid in captures().drain(..) {
        // SAFETY: plain kill(2) on a pid this process spawned and has not yet reaped.
        unsafe {
            libc::kill(pid as libc::pid_t, libc::SIGKILL);
        }
    }
}

/// Raw RGB24 frames from a child's stdout. Kills the child when dropped.
pub struct PipeSource {
    child: Child,
    width: u32,
    height: u32,
    /// A frame already read (the capture-rate probe), returned first.
    pending: Option<Frame>,
}

impl PipeSource {
    /// Take over `child` (registered for [`kill_captures`] until dropped).
    pub fn new(child: Child, width: u32, height: u32) -> Self {
        captures().push(child.id());
        Self { child, width, height, pending: None }
    }
}

/// How long a camera may take to send its first frame before it counts as unavailable.
pub const FIRST_FRAME_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(5);

impl PipeSource {
    /// The first frame, or an error after `timeout`: a camera another app holds lets ffmpeg
    /// start but never sends one, and a blocking read would hang the capture thread (and every
    /// control it owes a reply, `vision off` included). On timeout the child is killed.
    pub fn first_frame(&mut self, timeout: std::time::Duration) -> Result<Option<Frame>, VisionError> {
        let Some(mut out) = self.child.stdout.take() else { return Ok(None) };
        let n = self.width as usize * self.height as usize * 3;
        let (tx, rx) = std::sync::mpsc::channel();
        std::thread::Builder::new()
            .name("r3x-vision-first-frame".into())
            .spawn(move || {
                let mut buf = vec![0u8; n];
                let r = out.read_exact(&mut buf).map(|()| buf);
                let _ = tx.send((out, r));
            })
            .map_err(VisionError::Io)?;
        match rx.recv_timeout(timeout) {
            Ok((out, r)) => {
                self.child.stdout = Some(out);
                match r {
                    Ok(buf) => Frame::new(self.width, self.height, buf).map(Some),
                    Err(e) if e.kind() == std::io::ErrorKind::UnexpectedEof => Ok(None),
                    Err(e) => Err(e.into()),
                }
            }
            Err(_) => {
                let _ = self.child.kill(); // the reader thread then sees EOF and ends
                let _ = self.child.wait();
                Err(VisionError::Camera(format!("no frame within {:.0} s (in use by another app?)", timeout.as_secs_f64())))
            }
        }
    }
}

impl FrameSource for PipeSource {
    fn next_frame(&mut self) -> Result<Option<Frame>, VisionError> {
        if let Some(f) = self.pending.take() {
            return Ok(Some(f));
        }
        let Some(out) = self.child.stdout.as_mut() else { return Ok(None) };
        let mut buf = vec![0u8; self.width as usize * self.height as usize * 3];
        match out.read_exact(&mut buf) {
            Ok(()) => Frame::new(self.width, self.height, buf).map(Some),
            Err(e) if e.kind() == std::io::ErrorKind::UnexpectedEof => Ok(None),
            Err(e) => Err(e.into()),
        }
    }
}

impl Drop for PipeSource {
    fn drop(&mut self) {
        captures().retain(|p| *p != self.child.id());
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Tests that spawn capture children share the process-wide capture list (and
    /// `kill_captures` kills every child on it): one at a time.
    static SERIAL: Mutex<()> = Mutex::new(());
    fn serial() -> std::sync::MutexGuard<'static, ()> {
        SERIAL.lock().unwrap_or_else(|e| e.into_inner())
    }

    const LIST: &str = "[AVFoundation indev @ 0x1] AVFoundation video devices:
[AVFoundation indev @ 0x1] [0] Brandon's iPhone Camera
[AVFoundation indev @ 0x1] [1] Desk View Camera
[AVFoundation indev @ 0x1] [2] Logitech BRIO
[AVFoundation indev @ 0x1] [3] Capture screen 0
[AVFoundation indev @ 0x1] AVFoundation audio devices:
[AVFoundation indev @ 0x1] [0] MacBook Pro Microphone
: Input/output error";

    #[test]
    fn list_parse_and_continuity_skip() {
        let cams = parse_avfoundation_list(LIST);
        assert_eq!(cams.iter().map(|c| (c.index, c.name.as_str())).collect::<Vec<_>>(), [(0, "Brandon's iPhone Camera"), (1, "Desk View Camera"), (2, "Logitech BRIO")]);
        assert_eq!(pick(&cams, None), Some(2));
        assert_eq!(pick(&cams, Some(0)), Some(0));
        assert_eq!(pick(&cams[..2], None), None);
    }

    #[test]
    fn capture_rates_start_at_the_loop_rate() {
        assert_eq!(capture_rates(5.0), [5, 10, 15, 24, 30]);
        assert_eq!(capture_rates(12.0), [15, 24, 30]);
        assert_eq!(capture_rates(0.0), [30]);
    }

    /// A camera another app holds: ffmpeg starts but never sends a frame (2026-09-30: the
    /// capture thread then hung in open(), so `vision off` could not close it). The first
    /// frame has a deadline; past it the child is killed and the error says why.
    #[test]
    fn a_first_frame_that_never_comes_times_out_and_kills_the_child() {
        let _one = serial();
        let child = Command::new("sleep").arg("30").stdout(Stdio::piped()).spawn().unwrap();
        let mut src = PipeSource::new(child, 2, 2);
        let t0 = std::time::Instant::now();
        let e = src.first_frame(std::time::Duration::from_millis(300)).unwrap_err().to_string();
        assert!(e.contains("no frame within") && e.contains("another app"), "{e}");
        assert!(t0.elapsed() < std::time::Duration::from_secs(3), "did not wait for sleep 30");
        assert!(src.child.try_wait().unwrap().is_some(), "the child was killed");

        let child = Command::new("head").args(["-c", "24", "/dev/zero"]).stdout(Stdio::piped()).spawn().unwrap();
        let mut src = PipeSource::new(child, 2, 2);
        let f = src.first_frame(std::time::Duration::from_secs(5)).unwrap().expect("a frame");
        assert_eq!((f.width, f.height), (2, 2));
        assert!(src.next_frame().unwrap().is_some(), "the stream carries on after the first frame");
    }

    #[test]
    fn kill_captures_ends_a_capture_child_nobody_dropped() {
        let _one = serial();
        let child = Command::new("sleep").arg("30").stdout(Stdio::piped()).spawn().unwrap();
        let pid = child.id();
        let src = PipeSource::new(child, 2, 2);
        kill_captures();
        let mut src = std::mem::ManuallyDrop::new(src); // as on a thread that never unwinds
        let status = src.child.wait().unwrap();
        assert!(!status.success(), "sleep {pid} was killed");
        assert!(captures().is_empty());
    }
}

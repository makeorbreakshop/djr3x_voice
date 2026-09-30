//! `cargo run -p r3x-pad --example pad_watch [seconds]`: print what the pad reports, one
//! line per change (sticks rounded, pressed buttons with pressure, tilt), to check a pad and
//! its mapping without the runtime.
use std::time::{Duration, Instant};

use r3x_pad::PadEvent;

const NAMES: [&str; 17] = [
    "cross", "circle", "square", "triangle", "L1", "R1", "L2", "R2", "select", "start", "L3", "R3", "up", "down",
    "left", "right", "PS",
];

fn main() {
    let secs: u64 = std::env::args().nth(1).and_then(|s| s.parse().ok()).unwrap_or(30);
    let mut last = String::new();
    let _reader = r3x_pad::spawn(move |ev| match ev {
        PadEvent::State(r) => {
            let r1 = |v: f64| (v * 10.0).round() / 10.0;
            let pressed: Vec<String> = r
                .buttons
                .iter()
                .enumerate()
                .filter(|(_, b)| b.0)
                .map(|(i, b)| format!("{}:{:.2}", NAMES[i], b.1))
                .collect();
            let line = format!(
                "sticks L({:+.1},{:+.1}) R({:+.1},{:+.1})  tilt({:+.1},{:+.1},{:+.1})  {:?}  [{}]",
                r1(r.axes[0]), r1(r.axes[1]), r1(r.axes[2]), r1(r.axes[3]),
                r1(r.accel[0]), r1(r.accel[1]), r1(r.accel[2]), r.battery, pressed.join(" ")
            );
            if line != last {
                println!("{line}");
                last = line;
            }
        }
        other => println!("{other:?}"),
    })
    .expect("pad thread");
    let end = Instant::now() + Duration::from_secs(secs);
    while Instant::now() < end {
        std::thread::sleep(Duration::from_millis(100));
    }
}

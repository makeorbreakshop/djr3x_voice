//! r3x_servo motion controller firmware, RP2040 (plan D6, `rust/crates/r3x-drivers/PROTOCOL.md`).
//!
//! All behaviour lives in `r3x-servo-ctl` (host-tested); this file is only the wiring:
//!
//! | Task | Rate | Does |
//! |---|---|---|
//! | `control` | 200 Hz | `Controller::tick`, pulses -> PWM / PIO, rail enable, telemetry (25 Hz) |
//! | `serial_rx` | on data | USB CDC bytes -> `Controller::receive` (replies -> TX pipe) |
//! | `serial_tx` | on data | TX pipe -> USB CDC packets |
//! | `rail` | 100 Hz | INA219 shunt voltage -> `Controller::set_rail_ma` (stall detect) |
//! | `usb` | - | the USB device stack |
//!
//! Pin map (see README): channel n = GPIO n for n in 0..16 (hardware PWM slice n/2, A/B),
//! channels 16/17 = GPIO16/17 (PIO0 SM0/SM1), INA219 on I2C0 (SDA GPIO20, SCL GPIO21),
//! servo rail enable GPIO22 (active high), status LED GPIO25.

#![no_std]
#![no_main]

use core::cell::RefCell;
use core::time::Duration as CoreDuration;

use defmt::{info, warn};
use embassy_executor::Spawner;
use embassy_rp::bind_interrupts;
use embassy_rp::gpio::{Level, Output};
use embassy_rp::i2c::{self, I2c};
use embassy_rp::peripherals::{I2C0, PIO0, USB};
use embassy_rp::pio::{self, Pio};
use embassy_rp::pio_programs::pwm::{PioPwm, PioPwmProgram};
use embassy_rp::pwm::{Config as PwmConfig, Pwm, PwmBatch};
use embassy_rp::usb::{self, Driver};
use embassy_sync::blocking_mutex::raw::CriticalSectionRawMutex;
use embassy_sync::blocking_mutex::Mutex;
use embassy_sync::pipe::Pipe;
use embassy_time::{Duration, Instant, Ticker, Timer};
use embassy_usb::class::cdc_acm::{CdcAcmClass, Receiver, Sender, State};
use embassy_usb::{Builder, UsbDevice};
use r3x_servo_ctl::{ina219_shunt_to_ma, Controller, CHANNELS, DT};
use static_cell::StaticCell;
use {defmt_rtt as _, panic_probe as _};

bind_interrupts!(struct Irqs {
    USBCTRL_IRQ => usb::InterruptHandler<USB>;
    I2C0_IRQ => i2c::InterruptHandler<I2C0>;
    PIO0_IRQ_0 => pio::InterruptHandler<PIO0>;
});

type Ctl = Mutex<CriticalSectionRawMutex, RefCell<Controller>>;

/// Controller -> host bytes (whole frames only; see `send`).
static TX: Pipe<CriticalSectionRawMutex, 2048> = Pipe::new();

/// Servo frame: 50 Hz. PWM counter at 1 MHz (125 MHz / 125): compare = pulse in us.
const PWM_DIV: u8 = 125;
const PWM_TOP: u16 = 19_999;
/// INA219 shunt on the servo rail (R100 on the common breakout is too small a range for a
/// 17-servo rail; fit 10 mOhm = 32 A full scale). Measure and adjust.
const SHUNT_MILLIOHM: u32 = 10;
const INA219_ADDR: u8 = 0x40;
const USB_PACKET: usize = 64;

/// Queue a whole frame or none of it (a full pipe means no host is reading).
fn send(frame: &[u8]) {
    if TX.free_capacity() >= frame.len() {
        let _ = TX.try_write(frame);
    }
}

fn now_us() -> u64 {
    Instant::now().as_micros()
}

#[embassy_executor::main]
async fn main(spawner: Spawner) {
    let p = embassy_rp::init(Default::default());
    info!("r3x-servo {}", env!("CARGO_PKG_VERSION"));

    // Boot: rail off and every output low before anything else (motion-control.md §2).
    let rail = Output::new(p.PIN_22, Level::Low);
    let led = Output::new(p.PIN_25, Level::Low);

    // Hardware PWM, channels 0..16, all slices started together (phase-aligned), compare 0
    // (no pulse) until the controller powers a channel.
    let cfg = || {
        let mut c = PwmConfig::default();
        c.divider = PWM_DIV.into();
        c.top = PWM_TOP;
        c.enable = false;
        c
    };
    let slices: [Pwm<'static>; 8] = [
        Pwm::new_output_ab(p.PWM_SLICE0, p.PIN_0, p.PIN_1, cfg()),
        Pwm::new_output_ab(p.PWM_SLICE1, p.PIN_2, p.PIN_3, cfg()),
        Pwm::new_output_ab(p.PWM_SLICE2, p.PIN_4, p.PIN_5, cfg()),
        Pwm::new_output_ab(p.PWM_SLICE3, p.PIN_6, p.PIN_7, cfg()),
        Pwm::new_output_ab(p.PWM_SLICE4, p.PIN_8, p.PIN_9, cfg()),
        Pwm::new_output_ab(p.PWM_SLICE5, p.PIN_10, p.PIN_11, cfg()),
        Pwm::new_output_ab(p.PWM_SLICE6, p.PIN_12, p.PIN_13, cfg()),
        Pwm::new_output_ab(p.PWM_SLICE7, p.PIN_14, p.PIN_15, cfg()),
    ];
    PwmBatch::set_enabled(true, |b| slices.iter().for_each(|s| b.enable(s)));

    // PIO PWM, channels 16..18.
    let Pio { mut common, sm0, sm1, .. } = Pio::new(p.PIO0, Irqs);
    static PRG: StaticCell<PioPwmProgram<'static, PIO0>> = StaticCell::new();
    let prg = PRG.init(PioPwmProgram::new(&mut common));
    let mut pio16 = PioPwm::new(&mut common, sm0, p.PIN_16, prg);
    let mut pio17 = PioPwm::new(&mut common, sm1, p.PIN_17, prg);
    pio16.set_period(CoreDuration::from_millis(20));
    pio17.set_period(CoreDuration::from_millis(20));

    static CTL: StaticCell<Ctl> = StaticCell::new();
    let ctl: &'static Ctl = CTL.init(Mutex::new(RefCell::new(Controller::new())));

    // USB CDC ACM.
    let driver = Driver::new(p.USB, Irqs);
    let mut config = embassy_usb::Config::new(0x2e8a, 0x000a); // Raspberry Pi RP2040 CDC
    config.manufacturer = Some("R3X");
    config.product = Some("r3x-servo");
    config.serial_number = Some("r3x-servo-1");
    config.max_power = 100;
    config.max_packet_size_0 = 64;
    static CONFIG_DESC: StaticCell<[u8; 256]> = StaticCell::new();
    static BOS_DESC: StaticCell<[u8; 256]> = StaticCell::new();
    static CONTROL_BUF: StaticCell<[u8; 64]> = StaticCell::new();
    static CDC_STATE: StaticCell<State> = StaticCell::new();
    let mut builder = Builder::new(
        driver,
        config,
        CONFIG_DESC.init([0; 256]),
        BOS_DESC.init([0; 256]),
        &mut [],
        CONTROL_BUF.init([0; 64]),
    );
    let class = CdcAcmClass::new(&mut builder, CDC_STATE.init(State::new()), USB_PACKET as u16);
    let (tx, rx) = class.split();
    let usb = builder.build();

    let i2c = I2c::new_async(p.I2C0, p.PIN_21, p.PIN_20, Irqs, i2c::Config::default());

    spawner.spawn(usb_task(usb).unwrap());
    spawner.spawn(serial_rx(rx, ctl).unwrap());
    spawner.spawn(serial_tx(tx).unwrap());
    spawner.spawn(rail_task(i2c, ctl).unwrap());
    spawner.spawn(control(ctl, slices, pio16, pio17, rail, led).unwrap());
}

#[embassy_executor::task]
async fn usb_task(mut usb: UsbDevice<'static, Driver<'static, USB>>) -> ! {
    usb.run().await
}

#[embassy_executor::task]
async fn serial_rx(mut rx: Receiver<'static, Driver<'static, USB>>, ctl: &'static Ctl) -> ! {
    let mut buf = [0u8; USB_PACKET];
    loop {
        rx.wait_connection().await;
        info!("host connected");
        while let Ok(n) = rx.read_packet(&mut buf).await {
            let now = now_us();
            ctl.lock(|c| c.borrow_mut().receive(&buf[..n], now, &mut send));
        }
        info!("host disconnected");
    }
}

#[embassy_executor::task]
async fn serial_tx(mut tx: Sender<'static, Driver<'static, USB>>) -> ! {
    let mut buf = [0u8; USB_PACKET];
    loop {
        let n = TX.read(&mut buf).await;
        if tx.write_packet(&buf[..n]).await.is_err() {
            continue; // not connected: drop (telemetry is periodic, replies are retried)
        }
        // A full-size packet ends a USB transfer only with a zero-length packet after it.
        if n == USB_PACKET && TX.is_empty() {
            let _ = tx.write_packet(&[]).await;
        }
    }
}

#[embassy_executor::task]
async fn rail_task(mut i2c: I2c<'static, I2C0, i2c::Async>, ctl: &'static Ctl) -> ! {
    // Config: 32 V bus, PGA /8 (+-320 mV shunt), 12-bit, continuous shunt + bus.
    let mut configured = false;
    let mut ticker = Ticker::every(Duration::from_millis(10));
    loop {
        if !configured {
            match i2c.write_async(INA219_ADDR, [0x00, 0x39, 0x9F]).await {
                Ok(()) => {
                    configured = true;
                    info!("INA219 found");
                }
                Err(_) => {
                    warn!("INA219 not answering; no stall detection");
                    Timer::after_secs(1).await;
                    continue;
                }
            }
        }
        let mut raw = [0u8; 2];
        match i2c.write_read_async(INA219_ADDR, [0x01], &mut raw).await {
            Ok(()) => {
                let ma = ina219_shunt_to_ma(i16::from_be_bytes(raw), SHUNT_MILLIOHM);
                let now = now_us();
                ctl.lock(|c| c.borrow_mut().set_rail_ma(ma, now));
            }
            Err(_) => configured = false,
        }
        ticker.next().await;
    }
}

#[embassy_executor::task]
async fn control(
    ctl: &'static Ctl,
    mut slices: [Pwm<'static>; 8],
    mut pio16: PioPwm<'static, PIO0, 0>,
    mut pio17: PioPwm<'static, PIO0, 1>,
    mut rail: Output<'static>,
    mut led: Output<'static>,
) -> ! {
    let mut ticker = Ticker::every(Duration::from_hz(r3x_servo_ctl::CONTROL_HZ as u64));
    let mut pio_on = [false; 2];
    let mut n: u32 = 0;
    loop {
        ticker.next().await;
        let now = now_us();
        let (pulses, rail_on, holding) = ctl.lock(|c| {
            let mut c = c.borrow_mut();
            c.tick(now, DT);
            c.poll_telemetry(now, &mut send);
            (c.pulses(), c.rail_enabled(), c.holding(now))
        });
        rail.set_level(if rail_on { Level::High } else { Level::Low });
        led.set_level(if holding { Level::Low } else { Level::High });
        // Compare registers are double-buffered: a new pulse starts at the next frame.
        for (i, s) in slices.iter_mut().enumerate() {
            let mut c = PwmConfig::default();
            c.divider = PWM_DIV.into();
            c.top = PWM_TOP;
            c.compare_a = pulses[2 * i];
            c.compare_b = pulses[2 * i + 1];
            s.set_config(&c);
        }
        // PIO takes one level per 20 ms frame from its FIFO: feed it at the frame rate.
        n = n.wrapping_add(1);
        if n.is_multiple_of(4) {
            pio_write(&mut pio16, &mut pio_on[0], pulses[16]);
            pio_write(&mut pio17, &mut pio_on[1], pulses[CHANNELS - 1]);
        }
    }
}

fn pio_write<const SM: usize>(pwm: &mut PioPwm<'static, PIO0, SM>, on: &mut bool, us: u16) {
    if us == 0 {
        if *on {
            pwm.stop();
            *on = false;
        }
        return;
    }
    // The PIO loop is 3 cycles per count; compute from the real clock (the HAL's `write`
    // rounds clk/1e6/3 to an integer and so runs ~1.6% short at 125 MHz).
    let cycles = (embassy_rp::clocks::clk_sys_freq() as u64 * us as u64 / 3_000_000) as u32;
    pwm.set_level(cycles);
    if !*on {
        pwm.start();
        *on = true;
    }
}

//! r3x_servo motion controller firmware, Teensy 4.1 (plan D6, `rust/crates/r3x-drivers/PROTOCOL.md`).
//!
//! All behaviour lives in `r3x-servo-ctl` (host-tested); this file is only the wiring, the
//! same tasks at the same rates as the RP2040 build (`../rp2040/src/main.rs`):
//!
//! | Task | Priority | Rate | Does |
//! |---|---|---|---|
//! | `usb` | 3 (USB_OTG1 IRQ) | on USB events | CDC bytes -> `Controller::receive`; TX queue -> CDC |
//! | `control` | 2 | 200 Hz | `Controller::tick`, pulses -> PWM, rail enable, LED, telemetry (25 Hz) |
//! | `rail` | 1 | 100 Hz | INA219 shunt voltage -> `Controller::set_rail_ma` (stall detect) |
//!
//! `rail` is lowest so a wedged I2C bus can never delay the control loop.
//!
//! Pin map (see README): servo channels on FlexPWM / QuadTimer pins (`pwm.rs`), INA219 on
//! LPI2C1 (SDA 18, SCL 19), servo rail enable pin 30 (active high), status LED pin 13.

#![no_std]
#![no_main]

mod pwm;

use teensy4_panic as _;

#[rtic::app(device = teensy4_bsp, peripherals = true, dispatchers = [KPP, CSU])]
mod app {
    use heapless::Deque;
    use r3x_servo_ctl::{ina219_shunt_to_ma, Controller, CHANNELS, CONTROL_HZ, DT};
    use rtic_monotonics::imxrt::prelude::*;
    use teensy4_bsp::{
        board,
        hal::{gpio, gpt},
        ral,
        usbd::{
            gpt::{Instance::Gpt0, Mode},
            BusAdapter, EndpointMemory, EndpointState, Speed,
        },
    };
    use usb_device::{
        bus::UsbBusAllocator,
        device::{StringDescriptors, UsbDevice, UsbDeviceBuilder, UsbDeviceState, UsbVidPid},
        LangID, UsbError,
    };
    use usbd_serial::SerialPort;

    use crate::pwm::{self, Servos};

    // GPT1 at PERCLK = 1 MHz: `Mono::now()` is microseconds since boot, 64-bit.
    imxrt_gpt1_monotonic!(Mono, 1_000_000);

    /// PJRC's Teensy USB-serial ID, so Teensy tooling (udev rule, the 134-baud reboot in
    /// Teensy Loader / `teensy_reboot`) treats the board as a Teensy. The host driver opens
    /// `R3X_SERVO_PORT` and never matches on this.
    const VID_PID: UsbVidPid = UsbVidPid(0x16C0, 0x0483);
    const USB_PACKET: usize = 64;
    /// Host asks for 134 baud = "reboot into the bootloader" (Teensyduino convention).
    const REBOOT_BAUD: u32 = 134;
    /// INA219 shunt on the servo rail (see the RP2040 build: 10 mOhm = 32 A full scale).
    const SHUNT_MILLIOHM: u32 = 10;
    const INA219_ADDR: u8 = 0x40;
    /// Controller -> host bytes (whole frames only; see `send`). Same size as the RP2040 pipe.
    type TxQueue = Deque<u8, 2048>;

    #[shared]
    struct Shared {
        ctl: Controller,
        tx: TxQueue,
        reboot: bool,
    }

    #[local]
    struct Local {
        usb_device: UsbDevice<'static, BusAdapter>,
        serial: SerialPort<'static, BusAdapter>,
        servos: Servos,
        rail: gpio::Output,
        led: board::Led,
        i2c: board::Lpi2c,
    }

    /// Queue a whole frame or none of it (a full queue means no host is reading).
    fn send(tx: &mut TxQueue, frame: &[u8]) {
        if tx.capacity() - tx.len() >= frame.len() {
            for &b in frame {
                let _ = tx.push_back(b);
            }
        }
    }

    fn now_us() -> u64 {
        Mono::now().ticks()
    }

    #[init(local = [
        ep_memory: EndpointMemory<2048> = EndpointMemory::new(),
        ep_state: EndpointState = EndpointState::max_endpoints(),
        usb_bus: Option<UsbBusAllocator<BusAdapter>> = None,
    ])]
    fn init(cx: init::Context) -> (Shared, Local) {
        let board::T41Resources {
            pins,
            mut gpio2,
            mut gpio3,
            mut gpt1,
            usb,
            lpi2c1,
            mut ccm,
            ..
        } = board::t41(cx.device);

        // Boot: rail off and every output low before anything else (motion-control.md §2).
        let rail = gpio3
            .output(pins.p30)
            .expect("pin 30 (GPIO_EMC_37) is GPIO3_IO23");
        rail.clear();
        let led = board::led(&mut gpio2, pins.p13);
        led.clear();

        let servos = Servos::new(
            pwm::Pins {
                p2: pins.p2,
                p3: pins.p3,
                p4: pins.p4,
                p5: pins.p5,
                p6: pins.p6,
                p9: pins.p9,
                p10: pins.p10,
                p11: pins.p11,
                p12: pins.p12,
                p22: pins.p22,
                p23: pins.p23,
                p24: pins.p24,
                p25: pins.p25,
                p28: pins.p28,
                p29: pins.p29,
                p33: pins.p33,
                p36: pins.p36,
                p37: pins.p37,
            },
            &mut ccm,
        );

        // Monotonic: GPT1 on PERCLK (1 MHz, divider 1).
        gpt1.disable();
        gpt1.set_clock_source(gpt::ClockSource::PeripheralClock);
        gpt1.set_divider(1);
        let _ = gpt1; // the HAL wrapper is done; the monotonic takes the registers
        const _: () = assert!(board::PERCLK_FREQUENCY == 1_000_000);
        // Safety: the BSP's `gpt1` wrapper is not used after this; the monotonic owns GPT1.
        Mono::start(unsafe { ral::gpt::GPT1::instance() });

        let i2c: board::Lpi2c =
            board::lpi2c(lpi2c1, pins.p19, pins.p18, board::Lpi2cClockSpeed::KHz100);

        // USB CDC ACM, full speed (64-byte packets, as on the RP2040).
        let bus =
            BusAdapter::with_speed(usb, cx.local.ep_memory, cx.local.ep_state, Speed::LowFull);
        bus.set_interrupts(true);
        // USB's own GPT wakes the USB task every 5 ms, so queued TX drains even if no
        // other USB event comes along.
        bus.gpt_mut(Gpt0, |gpt| {
            gpt.stop();
            gpt.clear_elapsed();
            gpt.set_interrupt_enabled(true);
            gpt.set_mode(Mode::Repeat);
            gpt.set_load(5_000); // us
            gpt.reset();
            gpt.run();
        });
        let bus = cx.local.usb_bus.insert(UsbBusAllocator::new(bus));
        let serial = SerialPort::new(bus);
        let usb_device = UsbDeviceBuilder::new(bus, VID_PID)
            .strings(&[StringDescriptors::new(LangID::EN_US)
                .manufacturer("R3X")
                .product("r3x-servo")
                .serial_number("r3x-servo-1")])
            .unwrap()
            .max_power(100)
            .unwrap()
            .max_packet_size_0(64)
            .unwrap()
            .device_class(usbd_serial::USB_CLASS_CDC)
            .build();

        control::spawn().unwrap();
        rail_task::spawn().unwrap();

        (
            Shared {
                ctl: Controller::new(),
                tx: TxQueue::new(),
                reboot: false,
            },
            Local {
                usb_device,
                serial,
                servos,
                rail,
                led,
                i2c,
            },
        )
    }

    #[task(binds = USB_OTG1, priority = 3, shared = [ctl, tx, reboot],
           local = [usb_device, serial, configured: bool = false])]
    fn usb(mut cx: usb::Context) {
        let usb::LocalResources {
            usb_device,
            serial,
            configured,
            ..
        } = cx.local;
        usb_device.bus().gpt_mut(Gpt0, |gpt| {
            while gpt.is_elapsed() {
                gpt.clear_elapsed();
            }
        });
        if usb_device.poll(&mut [serial]) {
            if usb_device.state() == UsbDeviceState::Configured {
                if !*configured {
                    usb_device.bus().configure();
                }
                *configured = true;
            } else {
                *configured = false;
            }
        }
        if !*configured {
            // Not connected: drop (telemetry is periodic, replies are retried).
            cx.shared.tx.lock(|tx| tx.clear());
            return;
        }
        if serial.line_coding().data_rate() == REBOOT_BAUD {
            cx.shared.reboot.lock(|r| *r = true);
        }

        // RX: host bytes -> controller (replies queue on `tx`).
        let mut buf = [0u8; USB_PACKET];
        loop {
            match serial.read(&mut buf) {
                Ok(n) if n > 0 => {
                    let now = now_us();
                    (&mut cx.shared.ctl, &mut cx.shared.tx)
                        .lock(|c, tx| c.receive(&buf[..n], now, &mut |f| send(tx, f)));
                }
                _ => break,
            }
        }

        // TX: as much of the queue as the endpoint takes. usbd-serial sends the zero-length
        // packet after a full-size one itself.
        cx.shared.tx.lock(|tx| loop {
            let (chunk, _) = tx.as_slices();
            if chunk.is_empty() {
                let _ = serial.flush();
                break;
            }
            match serial.write(chunk) {
                Ok(n) => {
                    for _ in 0..n {
                        tx.pop_front();
                    }
                }
                Err(UsbError::WouldBlock) => break,
                Err(_) => {
                    tx.clear();
                    break;
                }
            }
        });
    }

    #[task(priority = 2, shared = [ctl, tx, reboot], local = [servos, rail, led])]
    async fn control(mut cx: control::Context) {
        let control::LocalResources {
            servos, rail, led, ..
        } = cx.local;
        let period = (1_000_000 / CONTROL_HZ as u64).micros();
        let mut next = Mono::now();
        loop {
            next += period;
            Mono::delay_until(next).await;
            let now = now_us();
            let (pulses, rail_on, holding, sent) =
                (&mut cx.shared.ctl, &mut cx.shared.tx).lock(|c, tx| {
                    let before = tx.len();
                    c.tick(now, DT);
                    c.poll_telemetry(now, &mut |f| send(tx, f));
                    (
                        c.pulses(),
                        c.rail_enabled(),
                        c.holding(now),
                        tx.len() != before,
                    )
                });
            if cx.shared.reboot.lock(|r| *r) {
                reboot(servos, rail);
            }
            if rail_on {
                rail.set();
            } else {
                rail.clear();
            }
            if holding {
                led.clear();
            } else {
                led.set();
            }
            servos.write(&pulses);
            if sent {
                rtic::pend(ral::Interrupt::USB_OTG1);
            }
        }
    }

    /// Rail off, pulses off, then hand the core to the Teensy's bootloader chip (it watches
    /// for this breakpoint, as Teensyduino's `_reboot_Teensyduino_` does).
    fn reboot(servos: &mut Servos, rail: &mut gpio::Output) -> ! {
        rail.clear();
        servos.write(&[0; CHANNELS]);
        // Let the frame in flight finish so no pulse is cut.
        cortex_m::asm::delay(board::ARM_FREQUENCY / 50);
        loop {
            // Safety: a breakpoint; the bootloader chip takes over from here.
            unsafe { core::arch::asm!("bkpt #251") };
        }
    }

    #[task(priority = 1, shared = [ctl], local = [i2c])]
    async fn rail_task(mut cx: rail_task::Context) {
        use embedded_hal::i2c::I2c;
        let i2c = cx.local.i2c;
        // Config: 32 V bus, PGA /8 (+-320 mV shunt), 12-bit, continuous shunt + bus.
        let mut configured = false;
        let period = 10.millis();
        let mut next = Mono::now();
        loop {
            if !configured {
                if i2c.write(INA219_ADDR, &[0x00, 0x39, 0x9F]).is_ok() {
                    configured = true;
                } else {
                    // INA219 not answering: no stall detection. Retry in 1 s.
                    Mono::delay(1.secs()).await;
                    next = Mono::now();
                    continue;
                }
            }
            let mut raw = [0u8; 2];
            match i2c.write_read(INA219_ADDR, &[0x01], &mut raw) {
                Ok(()) => {
                    let ma = ina219_shunt_to_ma(i16::from_be_bytes(raw), SHUNT_MILLIOHM);
                    let now = now_us();
                    cx.shared.ctl.lock(|c| c.set_rail_ma(ma, now));
                }
                Err(_) => configured = false,
            }
            next += period;
            Mono::delay_until(next).await;
        }
    }
}

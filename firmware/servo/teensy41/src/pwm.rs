//! 19 servo outputs at 50 Hz on the Teensy 4.1: FlexPWM A/B/X outputs plus three QuadTimer 1
//! channels and one QuadTimer 3 channel (pin 14, the second visor servo). One timebase for both: IPG 150 MHz / 64 = 2.34375 MHz, so a 20 ms frame is
//! exactly 46 875 counts and a pulse resolves to 0.43 us (`us * 75 / 32` counts).
//!
//! A/B pins are muxed through `imxrt-iomuxc`'s `flexpwm::Pin`, whose associated types carry
//! the module, submodule and output, so the channel table below cannot disagree with the
//! chip: a pin on the wrong module does not compile. The HAL has no PWM_X or QuadTimer
//! driver, so those two use the RAL directly (alt modes as in Teensyduino's `pwm.c`).

use imxrt_iomuxc::{consts::Unsigned, flexpwm, Iomuxc};
use teensy4_bsp::ral;

use r3x_servo_ctl::CHANNELS;

/// Counter counts per 20 ms frame (`150 MHz / 64 / 50 Hz`).
pub const PERIOD_COUNTS: u32 = 46_875;
/// `ral::pwm` prescaler field value: divide by 2^6 = 64. QuadTimer PCS = 8 + 6 (IPG / 64).
const PRESCALE_LOG2: u16 = 6;
/// A compare value the counter never reaches (it runs 0..=PERIOD_COUNTS-1): an edge parked
/// here never happens, which is how a channel is held low with no pulse.
const NEVER: u16 = u16::MAX;

/// Pulse width in us -> counter counts, rounded.
pub const fn counts(us: u16) -> u32 {
    (us as u32 * 75 + 16) / 32
}

#[derive(Clone, Copy)]
enum Out {
    /// FlexPWM `module` (1..=4) submodule `sm`, output A / B / X.
    Flex { module: u8, sm: u8, ch: Ch },
    /// QuadTimer `timer` (1 or 3) channel.
    Qt(u8, u8),
}

#[derive(Clone, Copy, PartialEq)]
enum Ch {
    A,
    B,
    X,
}

trait AorB {
    const CH: Ch;
}
impl AorB for flexpwm::A {
    const CH: Ch = Ch::A;
}
impl AorB for flexpwm::B {
    const CH: Ch = Ch::B;
}

/// The README's pin table, checked against the chip data: a wrong module / submodule /
/// output here fails the build.
trait Same<T> {}
impl<T> Same<T> for T {}
const fn same<A: Same<B>, B>() {}
macro_rules! pin_is {
    ($pin:ident, PWM $m:literal SM $sm:literal $out:ident) => {
        const _: () = {
            type P = teensy4_bsp::pins::t41::$pin;
            assert!(<<P as flexpwm::Pin>::Module as Unsigned>::USIZE == $m);
            assert!(<<P as flexpwm::Pin>::Submodule as Unsigned>::USIZE == $sm);
            let _: fn() = same::<<P as flexpwm::Pin>::Output, flexpwm::$out>;
        };
    };
}
pin_is!(P2, PWM 4 SM 2 A);
pin_is!(P3, PWM 4 SM 2 B);
pin_is!(P4, PWM 2 SM 0 A);
pin_is!(P5, PWM 2 SM 1 A);
pin_is!(P6, PWM 2 SM 2 A);
pin_is!(P9, PWM 2 SM 2 B);
pin_is!(P22, PWM 4 SM 0 A);
pin_is!(P23, PWM 4 SM 1 A);
pin_is!(P28, PWM 3 SM 1 B);
pin_is!(P29, PWM 3 SM 1 A);
pin_is!(P33, PWM 2 SM 0 B);
pin_is!(P36, PWM 2 SM 3 A);
pin_is!(P37, PWM 2 SM 3 B);

/// Mux an A/B pin to its FlexPWM output and say which output it is.
fn ab<P>(mut pin: P) -> Out
where
    P: flexpwm::Pin,
    P::Output: AorB,
{
    flexpwm::prepare(&mut pin);
    Out::Flex {
        module: <P::Module as Unsigned>::USIZE as u8,
        sm: <P::Submodule as Unsigned>::USIZE as u8,
        ch: <P::Output as AorB>::CH,
    }
}

/// Mux a pad to an output the HAL has no pin trait for (ALT value from the RT1060 RM pad
/// mux table, as used by Teensyduino).
fn alt<P: Iomuxc>(mut pin: P, alt: u32, out: Out) -> Out {
    imxrt_iomuxc::alternate(&mut pin, alt);
    out
}

pub struct Servos {
    map: [Out; CHANNELS],
}

/// The Teensy 4.1 pins, channel order. Pins 0/1, 7/8 (Serial1/2, future bus servos),
/// 13 (LED), 18/19 (Wire, INA219) and 30 (rail enable) are not used here.
pub struct Pins {
    pub p2: teensy4_bsp::pins::t41::P2,
    pub p3: teensy4_bsp::pins::t41::P3,
    pub p4: teensy4_bsp::pins::t41::P4,
    pub p5: teensy4_bsp::pins::t41::P5,
    pub p6: teensy4_bsp::pins::t41::P6,
    pub p9: teensy4_bsp::pins::t41::P9,
    pub p10: teensy4_bsp::pins::t41::P10,
    pub p11: teensy4_bsp::pins::t41::P11,
    pub p12: teensy4_bsp::pins::t41::P12,
    pub p22: teensy4_bsp::pins::t41::P22,
    pub p23: teensy4_bsp::pins::t41::P23,
    pub p24: teensy4_bsp::pins::t41::P24,
    pub p25: teensy4_bsp::pins::t41::P25,
    pub p28: teensy4_bsp::pins::t41::P28,
    pub p29: teensy4_bsp::pins::t41::P29,
    pub p33: teensy4_bsp::pins::t41::P33,
    pub p36: teensy4_bsp::pins::t41::P36,
    pub p37: teensy4_bsp::pins::t41::P37,
    /// Channel 18: pin 14 (Serial3 TX, unused), QuadTimer 3 channel 2.
    pub p14: teensy4_bsp::pins::t41::P14,
}

fn pwm(module: u8) -> &'static ral::pwm::RegisterBlock {
    // Safety: the four FlexPWM register blocks are only touched through `Servos` (the BSP's
    // `hal::flexpwm::Pwm` wrappers are dropped unused in `init`). Single core.
    unsafe {
        &*match module {
            1 => ral::pwm::PWM1,
            2 => ral::pwm::PWM2,
            3 => ral::pwm::PWM3,
            _ => ral::pwm::PWM4,
        }
    }
}

fn tmr(timer: u8) -> &'static ral::tmr::RegisterBlock {
    // Safety: QuadTimers 1 and 3 are used only here; the BSP does not hand them out.
    unsafe {
        &*match timer {
            1 => ral::tmr::TMR1,
            _ => ral::tmr::TMR3,
        }
    }
}

/// One QuadTimer channel's registers (the RAL names them per channel: COMP10, COMP11, ...).
struct QtRegs<'a> {
    comp1: &'a ral::RWRegister<u16>,
    comp2: &'a ral::RWRegister<u16>,
    load: &'a ral::RWRegister<u16>,
    cntr: &'a ral::RWRegister<u16>,
    ctrl: &'a ral::RWRegister<u16>,
    sctrl: &'a ral::RWRegister<u16>,
    cmpld1: &'a ral::RWRegister<u16>,
    cmpld2: &'a ral::RWRegister<u16>,
    csctrl: &'a ral::RWRegister<u16>,
}

fn qt(timer: u8, ch: u8) -> QtRegs<'static> {
    let t = tmr(timer);
    macro_rules! regs {
        ($c1:ident, $c2:ident, $ld:ident, $cn:ident, $ct:ident, $sc:ident, $l1:ident, $l2:ident, $cs:ident) => {
            QtRegs {
                comp1: &t.$c1,
                comp2: &t.$c2,
                load: &t.$ld,
                cntr: &t.$cn,
                ctrl: &t.$ct,
                sctrl: &t.$sc,
                cmpld1: &t.$l1,
                cmpld2: &t.$l2,
                csctrl: &t.$cs,
            }
        };
    }
    match ch {
        0 => regs!(COMP10, COMP20, LOAD0, CNTR0, CTRL0, SCTRL0, CMPLD10, CMPLD20, CSCTRL0),
        1 => regs!(COMP11, COMP21, LOAD1, CNTR1, CTRL1, SCTRL1, CMPLD11, CMPLD21, CSCTRL1),
        2 => regs!(COMP12, COMP22, LOAD2, CNTR2, CTRL2, SCTRL2, CMPLD12, CMPLD22, CSCTRL2),
        _ => regs!(COMP13, COMP23, LOAD3, CNTR3, CTRL3, SCTRL3, CMPLD13, CMPLD23, CSCTRL3),
    }
}

// QuadTimer bit fields (offsets checked against imxrt-ral's tmr block).
const QT_CTRL_RUN: u16 = (1 << 13) // CM = 1: count rising edges of the primary source
    | ((8 + PRESCALE_LOG2) << 9)    // PCS: IP bus clock / 64
    | (1 << 5)                      // LENGTH: re-initialise at each compare
    | 4; // OUTMODE 4: toggle OFLAG on alternating COMP1 / COMP2
const QT_SCTRL_OEN: u16 = 1 << 0;
const QT_SCTRL_FORCE: u16 = 1 << 2; // VAL (bit 3) = 0: force OFLAG low
const QT_CSCTRL_LOADS: u16 = 0b10   // CL1: COMP1 <- CMPLD1 on a COMP2 compare (frame start)
    | (0b01 << 2); // CL2: COMP2 <- CMPLD2 on a COMP1 compare (rising edge)

impl Servos {
    /// Mux the pins and start every output low (no pulse). `ccm` turns on QuadTimer 1's
    /// clock gate, which the BSP's clock policy does not cover.
    pub fn new(p: Pins, ccm: &mut ral::ccm::CCM) -> Self {
        // QuadTimer 1 (CG13) and 3 (CG15) clock gates
        ral::modify_reg!(ral::ccm, ccm, CCGR6, CG13: 0b11, CG15: 0b11);
        let map = [
            ab(p.p2),                   // 0  PWM4 SM2 A
            ab(p.p3),                   // 1  PWM4 SM2 B
            ab(p.p4),                   // 2  PWM2 SM0 A
            ab(p.p5),                   // 3  PWM2 SM1 A
            ab(p.p6),                   // 4  PWM2 SM2 A
            ab(p.p9),                   // 5  PWM2 SM2 B
            alt(p.p10, 1, Out::Qt(1, 0)), // 6  GPIO_B0_00 ALT1 = QTIMER1_TIMER0
            alt(p.p11, 1, Out::Qt(1, 2)), // 7  GPIO_B0_02 ALT1 = QTIMER1_TIMER2
            alt(p.p12, 1, Out::Qt(1, 1)), // 8  GPIO_B0_01 ALT1 = QTIMER1_TIMER1
            ab(p.p22),                  // 9  PWM4 SM0 A
            ab(p.p23),                  // 10 PWM4 SM1 A
            alt(
                p.p24,
                4,
                Out::Flex {
                    module: 1,
                    sm: 2,
                    ch: Ch::X,
                },
            ), // 11 GPIO_AD_B0_12 ALT4
            alt(
                p.p25,
                4,
                Out::Flex {
                    module: 1,
                    sm: 3,
                    ch: Ch::X,
                },
            ), // 12 GPIO_AD_B0_13 ALT4
            ab(p.p28),                  // 13 PWM3 SM1 B
            ab(p.p29),                  // 14 PWM3 SM1 A
            ab(p.p33),                  // 15 PWM2 SM0 B
            ab(p.p36),                  // 16 PWM2 SM3 A
            ab(p.p37),                  // 17 PWM2 SM3 B
            alt(p.p14, 1, Out::Qt(3, 2)), // 18 GPIO_AD_B1_02 ALT1 = QTIMER3_TIMER2 (visor R)
        ];
        let s = Servos { map };
        s.init_flexpwm();
        s.init_qtimer();
        s
    }

    fn submodules(&self, module: u8) -> u16 {
        self.map.iter().fold(0, |m, o| match *o {
            Out::Flex { module: md, sm, .. } if md == module => m | (1 << sm),
            _ => m,
        })
    }

    fn init_flexpwm(&self) {
        for module in 1..=4u8 {
            let used = self.submodules(module);
            if used == 0 {
                continue;
            }
            let p = pwm(module);
            ral::modify_reg!(ral::pwm, p, MCTRL, CLDOK: used);
            // Fault inputs must not disable outputs: the XBAR-routed fault lines idle low,
            // which with the reset FLVL would read as a fault.
            ral::modify_reg!(ral::pwm, p, FCTRL0, FLVL: 0xF);
            ral::write_reg!(ral::pwm, p, FSTS0, 0x000F);
            for sm in 0..4usize {
                if used & (1 << sm) == 0 {
                    continue;
                }
                let s = &p.SM[sm];
                ral::write_reg!(ral::pwm::sm, s, SMCTRL2, INDEP: 1, WAITEN: 1, DBGEN: 1);
                ral::write_reg!(ral::pwm::sm, s, SMCTRL, PRSC: PRESCALE_LOG2, FULL: 1);
                ral::write_reg!(ral::pwm::sm, s, SMOCTRL, 0);
                ral::write_reg!(ral::pwm::sm, s, SMDISMAP0, 0);
                ral::write_reg!(ral::pwm::sm, s, SMDISMAP1, 0);
                ral::write_reg!(ral::pwm::sm, s, SMINIT, 0);
                // Counter runs 0..=46874; compares are equality, so the u16 values are fine
                // past i16::MAX (Teensyduino does the same).
                ral::write_reg!(ral::pwm::sm, s, SMVAL1, (PERIOD_COUNTS - 1) as u16);
                // A/B: on at VAL2/VAL4, off at VAL3/VAL5. X: on at VAL0, off at the period
                // end. Every "on" edge parked at NEVER: output low.
                ral::write_reg!(ral::pwm::sm, s, SMVAL0, NEVER);
                ral::write_reg!(ral::pwm::sm, s, SMVAL2, NEVER);
                ral::write_reg!(ral::pwm::sm, s, SMVAL3, 0);
                ral::write_reg!(ral::pwm::sm, s, SMVAL4, NEVER);
                ral::write_reg!(ral::pwm::sm, s, SMVAL5, 0);
            }
            let (mut a, mut b, mut x) = (0u16, 0u16, 0u16);
            for o in &self.map {
                if let Out::Flex { module: md, sm, ch } = *o {
                    if md == module {
                        match ch {
                            Ch::A => a |= 1 << sm,
                            Ch::B => b |= 1 << sm,
                            Ch::X => x |= 1 << sm,
                        }
                    }
                }
            }
            ral::modify_reg!(ral::pwm, p, OUTEN, PWMA_EN: a, PWMB_EN: b, PWMX_EN: x);
            ral::modify_reg!(ral::pwm, p, MCTRL, LDOK: used);
            // All used submodules of a module start in one write (phase-aligned).
            ral::modify_reg!(ral::pwm, p, MCTRL, RUN: used);
        }
    }

    fn init_qtimer(&self) {
        for o in &self.map {
            if let Out::Qt(timer, ch) = *o {
                qt_stop(&qt(timer, ch));
            }
        }
    }

    /// Apply one control tick's pulses (us; 0 = no pulse, output held low). FlexPWM values
    /// are double-buffered and load at the next frame boundary; QuadTimer values load at the
    /// next edge, so no pulse is ever cut short or doubled.
    pub fn write(&mut self, pulses: &[u16; CHANNELS]) {
        for module in 1..=4u8 {
            let used = self.submodules(module);
            if used != 0 {
                // Buffered VALx writes are ignored while LDOK is set: clear, write, set.
                ral::modify_reg!(ral::pwm, pwm(module), MCTRL, CLDOK: used);
            }
        }
        for (o, &us) in self.map.iter().zip(pulses) {
            match *o {
                Out::Flex { module, sm, ch } => {
                    let s = &pwm(module).SM[sm as usize];
                    let n = counts(us) as u16;
                    match (ch, us) {
                        (Ch::A, 0) => ral::write_reg!(ral::pwm::sm, s, SMVAL2, NEVER),
                        (Ch::A, _) => {
                            ral::write_reg!(ral::pwm::sm, s, SMVAL2, 0);
                            ral::write_reg!(ral::pwm::sm, s, SMVAL3, n);
                        }
                        (Ch::B, 0) => ral::write_reg!(ral::pwm::sm, s, SMVAL4, NEVER),
                        (Ch::B, _) => {
                            ral::write_reg!(ral::pwm::sm, s, SMVAL4, 0);
                            ral::write_reg!(ral::pwm::sm, s, SMVAL5, n);
                        }
                        (Ch::X, 0) => ral::write_reg!(ral::pwm::sm, s, SMVAL0, NEVER),
                        // High from VAL0 to the end of the frame.
                        (Ch::X, _) => ral::write_reg!(
                            ral::pwm::sm,
                            s,
                            SMVAL0,
                            (PERIOD_COUNTS - n as u32) as u16
                        ),
                    }
                }
                Out::Qt(timer, ch) => qt_write(&qt(timer, ch), us),
            }
        }
        for module in 1..=4u8 {
            let used = self.submodules(module);
            if used != 0 {
                ral::modify_reg!(ral::pwm, pwm(module), MCTRL, LDOK: used);
            }
        }
    }
}

fn qt_running(r: &QtRegs) -> bool {
    r.ctrl.read() >> 13 != 0
}

/// Stop the counter and force the output low. FORCE is only valid while stopped (RM).
fn qt_stop(r: &QtRegs) {
    r.ctrl.write(0);
    r.sctrl.write(QT_SCTRL_OEN | QT_SCTRL_FORCE);
}

/// Toggle-on-alternating-compare PWM (the NXP SDK's `QTMR_SetupPwm` scheme): from OFLAG
/// low, count `low` (COMP1) -> toggle high, count `high` (COMP2) -> toggle low, repeat.
fn qt_write(r: &QtRegs, us: u16) {
    if us == 0 {
        if qt_running(r) {
            qt_stop(r);
        }
        return;
    }
    let high = counts(us);
    let low = PERIOD_COUNTS - high;
    // The counter re-initialises to LOAD (0) after reaching COMPx: a phase is COMPx + 1 counts.
    r.cmpld1.write((low - 1) as u16);
    r.cmpld2.write((high - 1) as u16);
    if !qt_running(r) {
        // Start from a defined state: OFLAG forced low, counter 0, compares loaded directly.
        r.comp1.write((low - 1) as u16);
        r.comp2.write((high - 1) as u16);
        r.load.write(0);
        r.cntr.write(0);
        r.csctrl.write(QT_CSCTRL_LOADS);
        r.sctrl.write(QT_SCTRL_OEN | QT_SCTRL_FORCE);
        r.ctrl.write(QT_CTRL_RUN);
    }
}

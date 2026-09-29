//! The r3x_servo motion controller (plan D6, `rust/crates/r3x-drivers/PROTOCOL.md`), as a
//! hardware-free state machine. `firmware/servo` runs it on an RP2040; `tests/sim.rs`
//! runs it on the host with a fake clock against the real host driver.
#![no_std]

pub mod controller;
pub mod wire;

pub use controller::{Controller, StallConfig, CHANNELS, CONTROL_HZ, DT};

/// INA219 shunt-voltage register (0x01, signed, 10 uV/LSB) -> rail current in mA for a
/// shunt of `shunt_milliohm`. Negative (reverse) current reads as 0.
pub fn ina219_shunt_to_ma(raw: i16, shunt_milliohm: u32) -> u16 {
    if raw <= 0 || shunt_milliohm == 0 {
        return 0;
    }
    // I = raw * 10 uV / R  ->  mA = raw * 10 / R[mOhm]
    (raw as u32 * 10 / shunt_milliohm).min(u16::MAX as u32) as u16
}

#[cfg(test)]
mod tests {
    #[test]
    fn ina219_conversion() {
        // 10 mOhm shunt: 1 mA per LSB; full scale (320 mV) = 32 A.
        assert_eq!(super::ina219_shunt_to_ma(2500, 10), 2500);
        assert_eq!(super::ina219_shunt_to_ma(-40, 10), 0);
        assert_eq!(super::ina219_shunt_to_ma(1000, 100), 100);
    }
}

//! LED board emulators: line-for-line ports of the sim's `firmware.ts` (face), `chest.ts`
//! (chest panels), `grnwave.rs` (our sketch for the grnwave LED set) and `host.ts` (the CantinaOS side that produces the named commands).
//! Fed the same named command stream as the real boards (D5), they produce pixel frames.

pub mod chest;
pub mod firmware;
pub mod grnwave;
pub mod host;

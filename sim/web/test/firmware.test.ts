import { describe, expect, it } from 'vitest';
import { RexFaceFirmware, State, scale8 } from '../src/firmware';
import { CantinaHostEmulator } from '../src/host';

const fixedRandom = () => 0.5;

function boot(opts = {}) {
  const fw = new RexFaceFirmware({ random: fixedRandom, ...opts });
  expect(fw.readLines()).toEqual(['READY']);
  return fw;
}

describe('serial protocol', () => {
  it('acks valid state commands and rejects unknown ones', () => {
    const fw = boot();
    fw.write('SE\nSX\nR\n');
    fw.advanceTo(5);
    expect(fw.readLines()).toEqual(['+', '-', '+']);
    expect(fw.currentState).toBe(State.IDLE); // R resets to IDLE
  });

  it('parses Mnnn as fire-and-forget and clamps to 255', () => {
    const fw = boot();
    fw.write('M300\nM042\n');
    fw.advanceTo(2);
    expect(fw.readLines()).toEqual([]);
    expect(fw.mouthAmplitude).toBe(42);
    fw.write('M999\n');
    fw.advanceTo(3);
    expect(fw.mouthAmplitude).toBe(255);
  });
});

describe('animations', () => {
  it('scale8 matches FastLED fixed scaling', () => {
    expect(scale8(255, 255)).toBe(255);
    expect(scale8(255, 128)).toBe(128);
    expect(scale8(100, 0)).toBe(0);
  });

  it('boots dark, then IDLE breathing paints orange eyes on the first 20 ms tick', () => {
    const fw = boot();
    expect(fw.eyeLeds[0]).toEqual([0, 0, 0]);
    fw.advanceTo(25);
    const [r, g, b] = fw.eyeLeds[1];
    expect(r).toBeGreaterThan(200);
    expect(g).toBeGreaterThan(40);
    expect(b).toBe(0);
  });

  it('IDLE mouth lights only the V middle (1, 2, 5, 6) in blue', () => {
    const fw = boot();
    fw.advanceTo(50);
    const lit = fw.mouthLeds.map((c) => c.some((v) => v > 0));
    expect(lit).toEqual([false, true, true, false, false, true, true, false]);
    expect(fw.mouthLeds[1][2]).toBeGreaterThan(fw.mouthLeds[1][0]);
  });

  it('SPEAKING mouth stages outward with amplitude', () => {
    const fw = boot();
    fw.write('SS\n');
    const litAt = (amp: number) => {
      fw.write(`M${String(amp).padStart(3, '0')}\n`);
      fw.advanceTo(fw.now + 5);
      return fw.mouthLeds.map((c) => c.some((v) => v > 0));
    };
    // scaledAmp = (amp/255)^0.25; stage 2 above 0.30, stage 3 above 0.60, stage 4 above 0.80.
    expect(litAt(1)).toEqual([false, true, false, false, false, false, true, false]);
    expect(litAt(10)).toEqual([false, true, true, false, false, true, true, false]);
    expect(litAt(60)).toEqual([true, true, true, false, false, true, true, true]);
    expect(litAt(255)).toEqual([true, true, true, true, true, true, true, true]);
    expect(litAt(0)).toEqual(Array(8).fill(false));
  });

  it('FLASH pulses green then drops into ENGAGED', () => {
    const fw = boot();
    fw.write('SS\n');
    fw.advanceTo(10);
    fw.write('SF\n');
    fw.advanceTo(40);
    expect(fw.flashActive).toBe(true);
    expect(fw.eyeLeds[0][1]).toBeGreaterThan(0);
    expect(fw.eyeLeds[0][0]).toBe(0);
    fw.advanceTo(400);
    expect(fw.flashActive).toBe(false);
    expect(fw.currentState).toBe(State.ENGAGED);
  });

  it('THINKING: left eye has two dots, right eye one (sketch index bug)', () => {
    const fw = boot({ oobAliasesMouth: false });
    fw.write('ST\n');
    fw.advanceTo(100); // a few 30 ms steps in
    const cyan = (c: number[]) => c[0] === 0 && c[1] === 255 && c[2] === 255;
    expect(fw.eyeLeds.slice(0, 7).filter(cyan).length).toBe(2);
    expect(fw.eyeLeds.slice(7, 14).filter(cyan).length).toBe(1);
  });

  it('THINKING step 0 overruns eyeLeds[14]; aliased, it paints mouth LED 0 cyan', () => {
    const fw = boot({ oobAliasesMouth: true });
    fw.write('ST\n');
    fw.advanceTo(40);
    expect(fw.mouthLeds[0]).toEqual([0, 255, 255]);
  });
});

describe('host emulator', () => {
  it('drops the end-of-speech M000 inside the 10 Hz window, leaving the mouth lit', () => {
    const fw = boot();
    const sent: string[] = [];
    const host = new CantinaHostEmulator(fw, (dir, line) => dir === 'tx' && sent.push(line));
    host.setMode('INTERACTIVE');
    host.speechStarted();
    host.tick();
    fw.advanceTo(20);
    for (let i = 0; i < 10; i++) {
      host.amplitude(0.9);
      fw.advanceTo(fw.now + 17);
    }
    host.speechEnded(); // < 100 ms after the last mouth command
    for (let t = 0; t < 30; t++) {
      host.tick();
      fw.advanceTo(fw.now + 17);
    }
    expect(host.droppedMouthResets).toBe(1);
    expect(sent).not.toContain('M000');
    expect(fw.currentState).toBe(State.ENGAGED);
    expect(fw.mouthAmplitude).toBeGreaterThan(0);
    expect(fw.mouthLeds.some((c) => c.some((v) => v > 0))).toBe(true);
  });
});

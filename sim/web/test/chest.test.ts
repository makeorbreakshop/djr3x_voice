import { describe, expect, it } from 'vitest';
import { ChestFirmware, ChestHost, ChestLightSpec, healthMask } from '../src/chest';

/** Three panels ~31 deg apart, each 8 dots (vertical) + 3 windows - the detected layout. */
function layout(): ChestLightSpec[] {
  const out: ChestLightSpec[] = [];
  for (const deg of [-88.7, -57.7, -26.7]) {
    const a = (deg * Math.PI) / 180;
    const at = (y: number): [number, number, number] => [Math.sin(a) * 0.14, y, Math.cos(a) * 0.14];
    for (let k = 0; k < 8; k++) out.push({ kind: 'dot', pos: at(0.4336 + k * 0.0055), normal: [0, 0, 1], w: 0.0024, h: 0.0024, panel: 'MS_P_1_Full' });
    for (let k = 0; k < 3; k++) out.push({ kind: 'window', pos: at(0.44 + k * 0.015), normal: [0, 0, 1], w: 0.014, h: 0.012, panel: 'MS_P_1_Full' });
  }
  return out;
}

const lit = (fw: ChestFirmware, kind: 'dot' | 'window') =>
  fw.specs.map((s, i) => [s, fw.pixels[i]] as const).filter(([s, p]) => s.kind === kind && p.some((v) => v > 0)).length;

describe('chest light controller', () => {
  it('speaking: the dots are a VU meter driven by Mnnn', () => {
    const fw = new ChestFirmware(layout(), () => 0.5);
    fw.write('X0\nSS\nM255\n');
    fw.update(100);
    expect(lit(fw, 'dot')).toBe(24);
    fw.write('M000\n');
    fw.update(120);
    expect(lit(fw, 'dot')).toBe(0);
    expect(lit(fw, 'window')).toBe(9); // windows keep a base glow
  });

  it('a VU level lights the bottom rows first', () => {
    const fw = new ChestFirmware(layout(), () => 0.5);
    fw.write('X0\nSS\nM064\n'); // sqrt(64/255) ~ 0.5 -> 4 of 8 rows
    fw.update(100);
    const panel1 = fw.specs.slice(0, 8).map((_, i) => fw.pixels[i].some((v) => v > 0));
    expect(panel1).toEqual([true, true, true, true, false, false, false, false]);
  });

  it('Bnnn starts a beat chase and B000 stops it; SF does not change the mode', () => {
    const fw = new ChestFirmware(layout(), () => 0.5);
    fw.write('B120\nSF\n');
    fw.update(0);
    expect(fw.bpm).toBe(120);
    expect(fw.mode).toBe('I');
    fw.write('B000\n');
    fw.update(10);
    expect(fw.bpm).toBe(0);
  });

  it('boots into the boot sweep and leaves it on X0', () => {
    const fw = new ChestFirmware(layout(), () => 0.5);
    expect(fw.sysState).toBe(1);
    fw.write('X0\n');
    fw.update(10);
    expect(fw.sysState).toBe(0);
  });

  it('a down subsystem blinks its window red; fault turns every window red', () => {
    const fw = new ChestFirmware(layout(), () => 0.5);
    fw.write('X0\nSE\nH1FE\n'); // window 0 (mic / STT) down
    fw.update(250); // blink phase "on"
    const w0 = fw.specs.findIndex((s) => s.kind === 'window');
    expect(fw.pixels[w0][0]).toBeGreaterThan(200);
    expect(fw.pixels[w0][1]).toBeLessThan(40);
    fw.write('X3\n');
    fw.update(500);
    const windows = fw.specs.map((s, i) => [s, fw.pixels[i]] as const).filter(([s]) => s.kind === 'window');
    expect(windows.every(([, p]) => p[0] > 0 && p[2] === 0)).toBe(true);
  });
});

describe('ChestHost (port of ChestLightControllerService)', () => {
  it('produces the same conversation sequence as the Python service test', () => {
    const sent: string[] = [];
    const h = new ChestHost((c) => sent.push(c));
    h.setMode('INTERACTIVE');
    h.tick(0);
    sent.length = 0;
    h.listeningStarted(); h.tick(1);
    h.listeningStopped(); h.tick(2);
    h.speechStarted(); h.tick(3);
    for (const a of [0.8, 0.9, 0.7]) { h.amplitudeIn(a); h.tick(4); }
    h.speechEnded(); h.tick(5);
    expect(sent.filter((c) => c[0] === 'S')).toEqual(['SL', 'ST', 'SS', 'SF', 'SE']);
    expect(sent.filter((c) => c[0] === 'M').at(-1)).toBe('M000');
    expect(sent.indexOf('M000')).toBeLessThan(sent.indexOf('SF'));
  });

  it('LLM down is a fault; music down only blinks its window', () => {
    const sent: string[] = [];
    const h = new ChestHost((c) => sent.push(c));
    h.setMode('IDLE');
    h.serviceStatus('MusicControllerService', 'degraded');
    h.tick(0);
    expect(sent).toContain(`H${(0x1ff & ~(1 << 4)).toString(16).toUpperCase().padStart(3, '0')}`);
    expect(sent).not.toContain('X3');
    h.serviceStatus('ClaudeService', 'error');
    h.tick(1);
    expect(sent).toContain('X3');
    expect(healthMask({})).toBe(0x1ff);
  });
});

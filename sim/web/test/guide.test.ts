// Instructions (src/workbench/guide.ts): the copy rules - one or two plain sentences, hardware by part number.
import { describe, expect, it } from 'vitest';
import {
  baseName, byLine, hardwareLabel, hardwareLines, listPhrase, partGroups, partNumber, partPhrase, stepLabel, stepSentence,
} from '../src/workbench/guide';
import type { MFastener } from '../src/workbench/manifest';

const names: Record<string, string> = {
  w1: 'V-wheel (OpenBuilds solid), front left lower', w2: 'V-wheel (OpenBuilds solid), front right lower',
  top: 'Column top plate, 6 mm 6061', rr: 'TR_RR_Full', sr: 'TR_SR_Full',
  hub_l: 'Servo hub 1906, 25T (L)', hub_r: 'Servo hub 1906, 25T (R)',
};
const nameOf = (id: string) => names[id] ?? id;
const fast = (id: string, spec: MFastener['spec'], key = `${spec.type}-${spec.thread}x${spec.length_mm}`): MFastener =>
  ({ id, spec, key, joins: [], link: 'l', placed: true, step: 's' });

describe('guide copy', () => {
  it('names parts for a sentence: kit codes as they are, ours short with "the"', () => {
    expect(partPhrase('TR_SR_Full')).toBe('TR_SR_Full');
    expect(partPhrase('Column top plate, 6 mm 6061')).toBe('the column top plate');
    expect(partPhrase('V-wheel', 8)).toBe('eight V-wheels');
    expect(baseName('Servo hub 1906, 25T (L)')).toBe('Servo hub 1906');
    expect(listPhrase(['a', 'b', 'c'])).toBe('a, b and c');
  });

  it('groups a step\'s parts by kind ("8 x V-wheel")', () => {
    expect(partGroups(['w1', 'w2', 'top'], nameOf)).toEqual([{ name: 'V-wheel', ids: ['w1', 'w2'] }, { name: 'Column top plate', ids: ['top'] }]);
    expect(partGroups(['hub_l', 'hub_r'], nameOf)).toEqual([{ name: 'Servo hub 1906', ids: ['hub_l', 'hub_r'] }]);
  });

  it('lists hardware as quantity x spec with its part number, inserts and screws before nuts', () => {
    const lines = hardwareLines([
      fast('n1', { type: 'lock_nut', thread: 'M5' }), fast('s1', { type: 'shcs', thread: 'M5', length_mm: 20 }),
      fast('n2', { type: 'lock_nut', thread: 'M5' }), fast('s2', { type: 'shcs', thread: 'M5', length_mm: 20 }),
    ], [{ key: 'mcmaster-93365A240', spec: { type: '', mcmaster: '93365A240', desc: '10-32 tapered heat-set insert, 0.15in' }, count: 12 }]);
    expect(lines.map((l) => [l.qty, l.label, l.partNo])).toEqual([
      [2, 'M5 × 20 socket head cap screw', ''], [2, 'M5 lock nut', ''], [12, '10-32 tapered heat-set insert, 0.15in', '93365A240'],
    ]);
    expect(hardwareLabel({ type: 'insert', thread: 'M4', length_mm: 6 })).toBe('M4 heat-set insert');
    expect(partNumber({ type: 'x', mcmaster: '92125A130' })).toBe('92125A130');
  });

  it('uses the authored sentence when there is one', () => {
    expect(stepSentence({ title: 'Top plate', text: 'Close the column with the top plate.', parts: ['top'] }, nameOf, [])).toBe('Close the column with the top plate.');
  });

  it('generates one plain sentence from the parts and hardware otherwise', () => {
    const hw = hardwareLines([fast('a', { type: 'shcs', thread: 'M5', length_mm: 12 }), fast('b', { type: 'shcs', thread: 'M5', length_mm: 12 })]);
    expect(stepSentence({ title: 'Top plate', parts: ['top'], derived: true }, nameOf, hw)).toBe('Fit the column top plate with 2 screws.');
    expect(stepSentence({ title: 'Step 3', parts: ['sr'], targets: ['rr'] }, nameOf, [])).toBe('Fit TR_SR_Full to TR_RR_Full.');
    const ins = hardwareLines([], [{ key: 'k', spec: { type: '', mcmaster: '93365A240', desc: '10-32 tapered heat-set insert' }, count: 12 }]);
    expect(stepSentence({ title: 'Step 1', parts: [], targets: ['rr'] }, nameOf, ins)).toBe('Heat-set 12 inserts into TR_RR_Full.');
  });

  it('labels a step in the contents by its title, or its sentence cut at a word', () => {
    expect(stepLabel({ title: 'Top plate' }, 'Close the column.')).toBe('Top plate');
    expect(stepLabel({ title: 'Step 1' }, 'Heat-set 12 inserts into TR_RR_Full.')).toBe('Heat-set 12 inserts into TR_RR_Full');
    const long = 'Press the two bearings into the top plate, then slide the hub in from above and clamp the collar under it.';
    const label = stepLabel({ title: long.slice(0, 80), text: long }, long);
    expect(label.endsWith('…')).toBe(true);
    expect(label.length).toBeLessThanOrEqual(73);
  });

  it('reads a design author as a by-line', () => {
    expect(byLine("Ours (after Jason Charlton's lift and pan)")).toBe("Ours, after Jason Charlton's lift and pan");
    expect(byLine('Hunter Smoke')).toBe('Hunter Smoke');
  });
});

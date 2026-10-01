import { describe, expect, it } from 'vitest';
import { BodyRegions } from '../src/regions';
import { PROFILE_JSON } from '../src/performer';
import { PACKAGES } from '../src/electronics';

// As the runtime sends it: the selected electronics package's light groups first.
const profile = JSON.parse(PROFILE_JSON);
profile.lights = [...PACKAGES[profile.electronics].lights, ...profile.lights];
const regions = new BodyRegions(profile);

describe('body regions (derived from the profile hierarchy)', () => {
  it('groups the r3x outputs as the Rig tab shows them', () => {
    const outputs = [...profile.actuators.filter((a: { extended: boolean }) => !a.extended).map((a: { name: string }) => a.name),
      ...profile.lights.map((l: { name: string }) => l.name), 'middle_ring'];
    const g = Object.fromEntries(regions.group(outputs, (n) => regions.ofOutput(n)));
    expect(g.Head).toEqual(['neck', 'headlift', 'headtilt', 'headroll', 'visor', 'eyes', 'mouth']);
    expect(g.Arms).toEqual(['elbow', 'hand', 'heroarm']);
    expect(g.Torso).toEqual(['lowarm', 'middle_ring']);
    expect(g['Lights & stage']).toEqual(['chest', 'stage']);
    expect(g.Other).toBeUndefined();
  });

  it('puts arm parts in Arms and unknown names in Other', () => {
    for (const n of ['throttle_elbow', 'poker_claw', 'hero_claw']) expect(regions.ofOutput(n)).toBe('Arms');
    expect(regions.ofJoint('visor')).toBe('Head');
    expect(regions.ofJoint('torso_middle')).toBe('Torso');
    expect(regions.ofDrivenJoint('torso_top')).toBe('Arms'); // driven by heroarm
    expect(regions.ofOutput('nope')).toBe('Other');
  });
});

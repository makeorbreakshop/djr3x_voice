import * as THREE from 'three';
import { describe, expect, it } from 'vitest';
import { FramePacer } from '../src/pacer';
import { DEFAULT_VALUES, PRESETS, RenderSettings } from '../src/rendersettings';
import type { PostPipeline } from '../src/post';

/** The parts of PostPipeline RenderSettings drives, recording the last call. */
function fakePost() {
  const calls: Record<string, unknown[]> = {};
  const post = {
    envScale: 1,
    pacer: new FramePacer({ active: 30, quiet: 15 }),
    setTone: (...a: unknown[]) => (calls.tone = a),
    setBloom: (...a: unknown[]) => (calls.bloom = a),
    setAO: (...a: unknown[]) => (calls.ao = a),
  };
  return { post: post as unknown as PostPipeline, calls };
}

function set() {
  const scene = new THREE.Scene();
  const key = new THREE.SpotLight(0xffffff, 110);
  key.name = 'droid_key';
  key.castShadow = true;
  key.shadow.radius = 4;
  const wash = new THREE.SpotLight(0xffffff, 5.5);
  wash.name = 'wall_wash_l';
  const workKey = new THREE.SpotLight(0xfff1e2, 30);
  workKey.name = 'work_key';
  const other = new THREE.PointLight(0xffffff, 2);
  scene.add(key, wash, workKey, other);
  return { scene, key, wash, workKey, other };
}

describe('render settings', () => {
  it('scale each light from the level it was built with, and only the named groups', () => {
    const { post, calls } = fakePost();
    const s = set();
    const rs = new RenderSettings(post, s.scene);
    expect(rs.preset).toBe('default');
    rs.set({ key: 0.5, desk: 2, work: 2, env: 1.5 });
    expect(s.key.intensity).toBeCloseTo(55);
    expect(s.wash.intensity).toBeCloseTo(11);
    expect(s.workKey.intensity).toBeCloseTo(30); // key 0.5 x work 2
    expect(s.other.intensity).toBe(2);
    expect(post.envScale).toBe(1.5);
    // Not cumulative: setting again starts from the built level.
    rs.set({ key: 0.5 });
    expect(s.key.intensity).toBeCloseTo(55);
    expect(rs.preset).toBeNull();
    rs.usePreset('default');
    expect(s.key.intensity).toBe(110);
    expect(calls.bloom).toEqual([true, DEFAULT_VALUES.bloomStrength, DEFAULT_VALUES.bloomThreshold, DEFAULT_VALUES.bloomRadius]);
  });

  it('shadows toggle the casters and softness scales their radius', () => {
    const { post } = fakePost();
    const s = set();
    const rs = new RenderSettings(post, s.scene);
    rs.set({ shadowSoftness: 2 });
    expect(s.key.shadow.radius).toBe(8);
    rs.set({ shadows: false });
    expect(s.key.castShadow).toBe(false);
    rs.usePreset('default');
    expect(s.key.castShadow).toBe(true);
    expect(s.key.shadow.radius).toBe(4);
  });

  it('colour temperature keeps the work light at 5000 K as built, warmer below, cooler above', () => {
    const { post } = fakePost();
    const s = set();
    const built = s.workKey.color.clone();
    const rs = new RenderSettings(post, s.scene);
    expect(s.workKey.color.r).toBeCloseTo(built.r, 5);
    expect(s.workKey.color.b).toBeCloseTo(built.b, 5);
    rs.set({ workTemp: 3000 });
    expect(s.workKey.color.r / s.workKey.color.b).toBeGreaterThan(built.r / built.b * 1.5);
    rs.set({ workTemp: 8000 });
    expect(s.workKey.color.b / s.workKey.color.r).toBeGreaterThan(built.b / built.r);
  });

  it('presets are recognised, and flat turns off every costly or glowing effect', () => {
    const { post, calls } = fakePost();
    const rs = new RenderSettings(post, set().scene);
    rs.usePreset('flat');
    expect(rs.preset).toBe('flat');
    expect(calls.bloom?.[0]).toBe(false);
    expect(calls.ao?.[0]).toBe(false);
    expect(PRESETS.flat.values.eyesGlow).toBe(0);
    rs.usePreset('performance');
    expect(rs.preset).toBe('performance');
  });
});

describe('frame pacer states', () => {
  it('reports why frames come as they do, and a profiling run draws every frame', () => {
    const p = new FramePacer({ active: 30, quiet: 15 });
    expect(p.state(0)).toBe('quiet');
    expect(p.fps(0)).toBe(15);
    p.continuous(1000, 0);
    expect(p.state(500)).toBe('continuous');
    expect(p.fps(500)).toBe(Infinity);
    expect(p.state(1500)).toBe('quiet');
    p.continuous(1000, 2000);
    p.continuous(0, 2100);
    expect(p.state(2200)).toBe('quiet');
  });
});

// Minimal types for n8ao (the package ships none) - only what post.ts uses.
declare module 'n8ao' {
  import type { Camera, Color, Scene, ShaderMaterial, Mesh } from 'three';
  import { Pass } from 'three/addons/postprocessing/Pass.js';

  export interface N8AOConfiguration {
    aoRadius: number;
    distanceFalloff: number;
    intensity: number;
    color: Color;
    aoSamples: number;
    denoiseSamples: number;
    denoiseRadius: number;
    halfRes: boolean;
    depthAwareUpsampling: boolean;
    gammaCorrection: boolean;
    screenSpaceRadius: boolean;
    transparencyAware: boolean;
    accumulate: boolean;
  }

  export class N8AOPass extends Pass {
    constructor(scene: Scene, camera: Camera, width?: number, height?: number);
    configuration: N8AOConfiguration;
    autoDetectTransparency: boolean;
    effectCompositerQuad: Mesh & { material: ShaderMaterial };
    configureEffectCompositer(...args: unknown[]): void;
    setQualityMode(mode: 'Performance' | 'Low' | 'Medium' | 'High' | 'Ultra'): void;
    setDisplayMode(mode: 'Combined' | 'AO' | 'No AO' | 'Split' | 'Split AO'): void;
  }
}

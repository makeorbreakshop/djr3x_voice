import * as THREE from 'three';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';

/**
 * What R3X stands in front of, and a work light that does not depend on the stage-light desk.
 *
 * The booth (booth.ts) is built once at start-up (`?booth=0` builds the old turntable stage
 * instead and this control stands aside). The plain backdrops hide the booth's objects and
 * show a seamless floor that fades into the background colour (fog), with the droid's contact
 * shadow on it. The work light is a soft key (casting that shadow), a fill and a rim; bloom
 * only starts at 1.0 (post.ts) and look.ts keeps lit surfaces under it, so the eye and chest
 * LEDs stay the only glow.
 *
 * The choice is remembered (`r3x.scene`) and is the same in every operating mode: Show,
 * Bench and Studio light R3X identically, so a mode switch never changes how he reads. The
 * booth's own key/rims/fill have a floor (booth.ts `characterLit`), so the stage desk adds
 * to his light but is never all of it.
 */

export type Backdrop = 'booth' | 'grey' | 'dark' | 'light';
export interface SceneChoice {
  backdrop: Backdrop;
  workLight: boolean;
}

const PLAIN: Record<Exclude<Backdrop, 'booth'>, { bg: number; floor: number; env: number }> = {
  grey: { bg: 0x3c3f44, floor: 0x55585d, env: 0.45 },
  dark: { bg: 0x08090b, floor: 0x16171a, env: 0.25 },
  light: { bg: 0xa9abae, floor: 0xb9bbbe, env: 0.6 },
};
const KEY = 'r3x.scene';

export const DEFAULT_CHOICE: SceneChoice = { backdrop: 'booth', workLight: false };

export function storedChoice(): SceneChoice | null {
  try {
    const s = JSON.parse(localStorage.getItem(KEY) ?? 'null') as SceneChoice | null;
    return s && s.backdrop in { booth: 1, ...PLAIN } ? { backdrop: s.backdrop, workLight: !!s.workLight } : null;
  } catch {
    return null;
  }
}

function store(c: SceneChoice) {
  try {
    localStorage.setItem(KEY, JSON.stringify(c));
  } catch {
    /* storage blocked: the choice lasts this page load */
  }
}

export class SceneLook {
  choice: SceneChoice;
  private plain = new THREE.Group();
  private floorMat = new THREE.MeshStandardMaterial({ roughness: 0.92, metalness: 0 });
  private work = new THREE.Group();
  private env: THREE.Texture;

  constructor(
    private scene: THREE.Scene,
    renderer: THREE.WebGLRenderer,
    /** The booth's own show/hide (booth.ts `StageSet.show`). */
    private showBooth: (on: boolean) => void,
    private onChange: (showingBooth: boolean) => void,
  ) {
    const pmrem = new THREE.PMREMGenerator(renderer);
    this.env = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    pmrem.dispose();

    const floor = new THREE.Mesh(new THREE.CircleGeometry(12, 96), this.floorMat);
    floor.rotation.x = -Math.PI / 2;
    floor.receiveShadow = true;
    this.plain.add(floor);
    this.plain.visible = false;

    // Soft key (front right, high; casts the floor shadow), fill (front left), rim (behind).
    const key = new THREE.SpotLight(0xfff1e2, 30, 9, 0.5, 0.9, 1.6);
    key.position.set(1.4, 2.9, 2.3);
    key.target.position.set(0, 0.55, 0);
    key.castShadow = true;
    key.shadow.mapSize.set(2048, 2048);
    key.shadow.camera.near = 1.5;
    key.shadow.camera.far = 7;
    key.shadow.bias = -0.0002;
    key.shadow.normalBias = 0.004;
    key.shadow.radius = 6;
    const fill = new THREE.DirectionalLight(0xdfe8f5, 0.9);
    fill.position.set(-2.2, 1.3, 2.4);
    const rim = new THREE.SpotLight(0xdce8ff, 16, 8, 0.45, 0.9, 1.6);
    rim.position.set(-0.9, 2.4, -2.4);
    rim.target.position.set(0, 0.7, 0);
    // Named for the viewer's lighting levels (rendersettings.ts).
    key.name = 'work_key';
    fill.name = 'work_fill';
    rim.name = 'work_rim';
    this.work.add(key, key.target, fill, rim, rim.target);
    this.work.visible = false;
    scene.add(this.plain, this.work);

    this.choice = storedChoice() ?? { ...DEFAULT_CHOICE };
    this.apply();
  }

  private listeners: (() => void)[] = [];
  /** Called after every change, for the control's display. */
  onUpdate(fn: () => void) {
    this.listeners.push(fn);
  }

  get showingBooth() {
    return (this.stand ?? this.choice.backdrop) === 'booth';
  }

  /** A backdrop that stands in for the viewer's pick without replacing it (Build shows its
   *  parts on Studio grey rather than inside the booth); null returns to the pick. */
  private stand: Backdrop | null = null;
  standIn(b: Backdrop | null) {
    if (b === this.stand) return;
    this.stand = b;
    this.apply();
  }

  /** The operator's pick: remembered, in every mode. */
  pick(c: Partial<SceneChoice>) {
    const next = { ...this.choice, ...c };
    // A plain backdrop without the work light is only the ambient environment; picking one
    // switches the light on (it can be turned off again).
    if (c.backdrop && c.backdrop !== 'booth' && this.choice.backdrop === 'booth' && c.workLight === undefined) next.workLight = true;
    store(next);
    this.set(next);
  }

  private set(c: SceneChoice) {
    if (c.backdrop === this.choice.backdrop && c.workLight === this.choice.workLight) return;
    this.choice = c;
    this.apply();
  }

  private apply() {
    const { workLight } = this.choice;
    const backdrop = this.stand ?? this.choice.backdrop;
    const booth = backdrop === 'booth';
    const s = this.scene;
    this.showBooth(booth); // restores the booth's background, fog and environment
    if (!booth) {
      const p = PLAIN[backdrop];
      const bg = new THREE.Color(p.bg);
      s.background = bg;
      s.fog = new THREE.Fog(bg, 3.5, 10);
      s.environment = this.env;
      s.environmentIntensity = p.env;
      this.floorMat.color.setHex(p.floor);
    }
    this.plain.visible = !booth;
    this.work.visible = workLight;
    this.onChange(booth);
    for (const fn of this.listeners) fn();
  }
}

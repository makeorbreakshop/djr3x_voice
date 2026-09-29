// Second half of the model build (sim/model/build.sh runs it after Blender).
//
// Blender writes an uncompressed GLB whose materials are only *named* "<class>" (body
// atlas) or "<class>__head" (head atlas), plus PNG texture sets per atlas. This script:
//   1. puts the textures in the standard glTF PBR slots (baseColor; ORM as
//      metallicRoughness + occlusion; normal) and the per-class clearcoat from
//      src/palette.json (KHR_materials_clearcoat; its strength/roughness come from the
//      ORM texture, so dust, grime and chips are not glossy);
//   2. compresses the textures to KTX2 with basisu (brew install basis_universal):
//      ETC1S for baseColor and the body's ORM, UASTC (Zstandard) for normal maps and the
//      head's ORM - textures first;
//   3. compresses the meshes with Draco and writes public/model/r3x.glb.
//
// A model built with --no-bake has no textures: materials get flat palette colours.
//
//   node scripts/pack-model.mjs [--work ../model/.work] [--out public/model] [--no-ktx2] [--uastc-orm]
import { NodeIO, TextureInfo } from '@gltf-transform/core';
import { ALL_EXTENSIONS, KHRMaterialsClearcoat, KHRTextureBasisu } from '@gltf-transform/extensions';
import { dedup, draco, prune } from '@gltf-transform/functions';
import draco3d from 'draco3dgltf';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const argv = process.argv.slice(2);
const arg = (name, dflt) => (argv.includes(name) ? argv[argv.indexOf(name) + 1] : dflt);
const WORK = resolve(arg('--work', join(here, '../../model/.work')));
const OUT = resolve(arg('--out', join(here, '../public/model')));
const PALETTE = JSON.parse(readFileSync(join(here, '../src/palette.json'), 'utf8'));

// basisu settings per texture kind. Normal maps need UASTC (ETC1S blocks them badly).
// ORM: the body's is ETC1S. UASTC would cost ~3 MB more (its per-texel AO and roughness
// variation defeats Zstandard) for an error (mean |err| ~0.02-0.03 per channel, measured)
// only visible up close. The head's is UASTC at 1K, because close-ups look at the head and
// ETC1S's 4x4 blocks show in the specular there. --uastc-orm makes both UASTC.
const UASTC_ORM = argv.includes('--uastc-orm');
const ETC1S = (space) => ['-etc1s', '-quality', '255', '-effort', '5', space, '-mipmap'];
const UASTC_LINEAR = ['-uastc', '-uastc_level', '2', '-uastc_rdo_l', '2.0', '-linear', '-mipmap', '-ktx2_zstandard_level', '18'];
const ENCODE = {
  basecolor: () => ETC1S('-srgb'),
  orm: (set) => (UASTC_ORM || set === 'head' ? UASTC_LINEAR : ETC1S('-linear')),
  normal: () => ['-uastc', '-uastc_level', '2', '-uastc_rdo_l', '1.0', '-normal_map', '-mipmap', '-mip_renorm',
    '-ktx2_zstandard_level', '18'],
};

function hasBasisu() {
  try {
    execFileSync('basisu', ['-version'], { stdio: 'ignore' });
    return true;
  } catch {
    return false;
  }
}

/** PNG -> KTX2 bytes, cached by content + settings (re-packing without a re-bake is fast). */
function toKtx2(png, kind, set) {
  const flags = ENCODE[kind](set);
  const key = createHash('sha1').update(readFileSync(png)).update(flags.join(' ')).digest('hex').slice(0, 16);
  const cacheDir = join(WORK, 'ktx2-cache');
  mkdirSync(cacheDir, { recursive: true });
  const out = join(cacheDir, `${key}.ktx2`);
  if (!existsSync(out)) {
    const t = Date.now();
    execFileSync('basisu', [...flags, '-ktx2', '-no_stats', '-quiet', '-output_file', out, png], { stdio: 'inherit' });
    console.log(`[pack] ${png.split('/').pop()} -> KTX2 (${kind}) in ${((Date.now() - t) / 1000).toFixed(1)}s`);
  }
  return readFileSync(out);
}

const srgbToLinear = (c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
const hexLinear = (h) => [1, 3, 5].map((i) => srgbToLinear(parseInt(h.slice(i, i + 2), 16) / 255));

const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({
  'draco3d.encoder': await draco3d.createEncoderModule(),
  'draco3d.decoder': await draco3d.createDecoderModule(),
});
const doc = await io.read(join(WORK, 'r3x_raw.glb'));
const root = doc.getRoot();

const ktx2 = !argv.includes('--no-ktx2') && hasBasisu();
if (!ktx2) console.warn(argv.includes('--no-ktx2') ? '[pack] --no-ktx2: textures stay PNG'
  : '[pack] basisu not found (brew install basis_universal): textures stay PNG - large download');
const clearcoatExt = doc.createExtension(KHRMaterialsClearcoat);

/** Texture sets by atlas name ('head', 'body'), or undefined if not baked. */
const sets = {};
for (const set of ['head', 'body']) {
  const png = (kind) => join(WORK, `r3x_${set}_${kind}.png`);
  if (!existsSync(png('basecolor'))) continue;
  sets[set] = {};
  for (const kind of ['basecolor', 'orm', 'normal']) {
    const tex = doc.createTexture(`r3x_${set}_${kind}`);
    if (ktx2) tex.setImage(toKtx2(png(kind), kind, set)).setMimeType('image/ktx2').setURI(`r3x_${set}_${kind}.ktx2`);
    else tex.setImage(readFileSync(png(kind))).setMimeType('image/png').setURI(`r3x_${set}_${kind}.png`);
    sets[set][kind] = tex;
  }
}

if (ktx2 && Object.keys(sets).length) doc.createExtension(KHRTextureBasisu).setRequired(true);

const sampler = (info) => info
  ?.setMinFilter(TextureInfo.MinFilter.LINEAR_MIPMAP_LINEAR)
  .setMagFilter(TextureInfo.MagFilter.LINEAR)
  .setWrapS(TextureInfo.WrapMode.CLAMP_TO_EDGE)
  .setWrapT(TextureInfo.WrapMode.CLAMP_TO_EDGE);

for (const mat of root.listMaterials()) {
  const [cls, set = 'body'] = mat.getName().split('__');
  const p = PALETTE.classes[cls];
  if (!p) {
    console.warn(`[pack] material ${mat.getName()} has no palette class; left as exported`);
    continue;
  }
  const t = sets[set];
  mat.setName(mat.getName()).setDoubleSided(false).setEmissiveFactor([0, 0, 0]);
  if (t) {
    mat.setBaseColorFactor([1, 1, 1, 1]).setBaseColorTexture(t.basecolor)
      .setMetallicFactor(1).setRoughnessFactor(1).setMetallicRoughnessTexture(t.orm)
      .setOcclusionTexture(t.orm).setOcclusionStrength(1)
      .setNormalTexture(t.normal).setNormalScale(1);
    [mat.getBaseColorTextureInfo(), mat.getMetallicRoughnessTextureInfo(), mat.getOcclusionTextureInfo(),
      mat.getNormalTextureInfo()].forEach(sampler);
  } else {
    mat.setBaseColorFactor([...hexLinear(p.color), 1]).setMetallicFactor(p.metalness).setRoughnessFactor(p.roughness);
  }
  if (p.clearcoat > 0) {
    const cc = clearcoatExt.createClearcoat().setClearcoatFactor(p.clearcoat);
    if (t) {
      // Coat strength x occlusion (none packed in crevice grime), coat roughness = the
      // weathered base roughness (dust, grime and primer kill the gloss).
      cc.setClearcoatTexture(t.orm).setClearcoatRoughnessFactor(1).setClearcoatRoughnessTexture(t.orm);
      sampler(cc.getClearcoatTextureInfo());
      sampler(cc.getClearcoatRoughnessTextureInfo());
    } else {
      cc.setClearcoatRoughnessFactor(p.clearcoatRoughness ?? 0);
    }
    mat.setExtension('KHR_materials_clearcoat', cc);
  }
}

// Joint and anchor empties are leaves the sim needs: keep them.
await doc.transform(
  // keepUniqueNames: per-class materials look identical once the paint is in shared
  // textures, but the names are how the sim (and a debugger) tells the classes apart.
  dedup({ textures: false, keepUniqueNames: true }),
  prune({ keepLeaves: true, keepAttributes: true, keepExtras: true }),
  // 13-bit UVs = half a texel on a 4K atlas; 10-bit tangents. ~0.6 MB less than 14/12.
  draco({ method: 'edgebreaker', quantizePosition: 14, quantizeNormal: 10, quantizeTexcoord: 13, quantizeGeneric: 10 }),
);
mkdirSync(OUT, { recursive: true });
const glb = join(OUT, 'r3x.glb');
await io.write(glb, doc);
const mb = (n) => (n / 1e6).toFixed(1);
const texBytes = root.listTextures().reduce((s, t) => s + (t.getImage()?.byteLength ?? 0), 0);
console.log(`[pack] wrote ${glb}: ${mb(statSync(glb).size)} MB (textures ${mb(texBytes)} MB, ` +
  `${root.listTextures().length} textures${root.listTextures().length ? (ktx2 ? ', KTX2' : ', PNG') : ''})`);

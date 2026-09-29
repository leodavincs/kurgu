// Runs the shared vectors against web/js/anim.js (copied to anim.mjs next to this file by test_anim.py).
// usage: node anim_node_check.mjs <anim.mjs> <anim_vectors.json>   -> prints {"n":..,"max_err":..,"bad":[..]}
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
const A = await import(pathToFileURL(process.argv[2]).href);
const V = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const JSFN = { ease: 'ease', hash32: 'hash32', fnv1a: 'fnv1a', fbm: 'fbm', mod_value: 'modValue', value: 'value', effect_params: 'effectParams', raster_scale: 'rasterScale' };
let maxErr = 0; const bad = [];
function cmp(a, b) {
  if (typeof b === 'number') return typeof a === 'number' ? Math.abs(a - b) : Infinity;
  if (b && typeof b === 'object') { let m = 0; for (const k of Object.keys(b)) m = Math.max(m, cmp(a?.[k], b[k])); for (const k of Object.keys(a || {})) if (!(k in b)) m = Infinity; return m; }
  return a === b ? 0 : Infinity;
}
for (const c of V.cases) {
  const f = A[JSFN[c.fn]];
  const got = f(...c.args);
  const e = cmp(got, c.out);
  if (!(e <= V.tolerance)) bad.push({ fn: c.fn, args: c.args, got, want: c.out });
  if (e !== Infinity && e > maxErr) maxErr = e;
}
console.log(JSON.stringify({ n: V.cases.length, max_err: maxErr, bad: bad.slice(0, 5) }));
process.exit(bad.length ? 1 : 0);

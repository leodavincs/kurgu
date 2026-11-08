// Runs the shared vectors against web/js/anchor.js (copied to anchor.mjs by tests/test_note_anchor.py).
// usage: node anchor_node_check.mjs <anchor.mjs> <anchor_vectors.json>   -> prints {"n":..,"bad":[..]}
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
const A = await import(pathToFileURL(process.argv[2]).href);
const V = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const JSFN = { anchor_at: 'anchorAt', resolve: 'resolve', locate: 'locate', valid: 'valid', sig: 'sig', layer_anchor: 'layerAnchor', mark_layers: 'markLayers' };
const TOL = 1e-9;
function same(a, b) {
  if (typeof b === 'number') return typeof a === 'number' && Math.abs(a - b) <= TOL;
  if (b === null || typeof b !== 'object') return a === b;
  if (a === null || typeof a !== 'object' || Array.isArray(a) !== Array.isArray(b)) return false;
  const ka = Object.keys(a), kb = Object.keys(b);
  return ka.length === kb.length && kb.every(k => k in a && same(a[k], b[k]));
}
const bad = [];
for (const c of V.cases) {
  const args = JSON.parse(JSON.stringify(c.args));
  let got;
  if (c.fn === 'follow') { A.follow(args[0]); got = args[0].notes; } else got = A[JSFN[c.fn]](...args);
  if (got === undefined) got = null;
  if (!same(got, c.out)) bad.push({ fn: c.fn, args: c.args, got, want: c.out });
}
console.log(JSON.stringify({ n: V.cases.length, bad: bad.slice(0, 5) }));
process.exit(bad.length ? 1 : 0);

// Unit tests of web/js/trackpack.js (track view packing). usage: node trackpack_node_check.mjs <trackpack.mjs>
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
const { pack, sections, layout, place, collides } = await import(pathToFileURL(process.argv[2]).href);
const L = (id, s, e, g = 'G') => ({ id, start: s, end: e, group: g });
let n = 0; const test = (name, fn) => { fn(); n++; };
const tr = (list) => Object.fromEntries([...pack(list).track]);

test('back to back clips share one track (touching is not overlap)', () => {
  assert.deepEqual(tr([L('a', 0, 2), L('b', 2, 4), L('c', 4, 6)]), { a: 0, b: 0, c: 0 });
});
test('overlap goes to the next track, first fit reuses gaps', () => {
  assert.deepEqual(tr([L('a', 0, 5), L('b', 4, 9), L('c', 9, 12), L('d', 8, 10)]), { a: 0, b: 1, c: 0, d: 2 });
});
test('crossfade chain of 18 needs only 2 tracks', () => {
  const xs = Array.from({ length: 18 }, (_, i) => L('i' + i, i * 4.5, i * 4.5 + 5));
  assert.equal(pack(xs).count, 2);
});
test('sections are contiguous group runs', () => {
  const s = sections([L('a', 0, 1, 'X'), L('b', 0, 1, 'X'), L('c', 0, 1, 'Y'), L('d', 0, 1, '')]);
  assert.deepEqual(s.map(x => [x.group, x.layers.length]), [['X', 2], ['Y', 1], ['', 1]]);
});
test('deterministic', () => {
  const xs = [L('a', 0, 3), L('b', 1, 4), L('c', 5, 6)];
  assert.deepEqual(layout(xs), layout(xs));
});
test('place: a clip dragged to the lower track lands there', () => {
  const rest = [L('a', 0, 5), L('b', 4, 9)];              // a row 0, b row 1
  const x = L('x', 3, 3.9);                               // overlaps a only
  assert.equal(place(rest, x, { track: 1 }).track, 1);
  assert.equal(place(rest, x, { track: 0 }).track, 0);
});
test('place: a clip that overlaps nothing always packs to the top row (rows are derived, first fit)', () => {
  const rest = [L('a', 0, 5), L('b', 4, 9)];
  assert.equal(place(rest, L('x', 10, 12), { track: 1 }).track, 0);
});
test('place: dropped on a busy row it goes in front, pushes them down, and says when that needs a new row', () => {
  const rest = [L('a', 0, 5), L('b', 6, 9)];              // both on row 0
  const x = L('x', 2, 7);
  assert.equal(collides(rest, x, 0), true);
  const r = place(rest, x, { gap: 0 });
  assert.equal(r.track, 0); assert.equal(r.grew, true); assert.equal(r.index, 0);
});
console.log('ok', n);

// Unit tests of web/js/merge.js (three-way merge after a save conflict). usage: node merge_node_check.mjs <merge.mjs>
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
const { mergeProjects } = await import(pathToFileURL(process.argv[2]).href);
const clone = o => JSON.parse(JSON.stringify(o));
const L = (id, extra = {}) => ({ id, type: 'text', name: id.toUpperCase(), start: 0, end: 3, x: 960, y: 540, opacity: 1, text: 'hi', crop: { top: 0, left: 0 }, ...extra });
const P = (layers, extra = {}) => ({ name: 'p', width: 1920, height: 1080, fps: 30, duration: 10, global: { grain: 0, vignette: 0 }, layers, notes: [], ...extra });
const ids = r => r.merged.layers.map(l => l.id);
const get = (r, id) => r.merged.layers.find(l => l.id === id);
const fields = xs => xs.map(x => x.id + '.' + x.field).sort();
let n = 0; const test = (name, fn) => { fn(); n++; };

test('different fields of the same layer both survive', () => {
  const base = P([L('a')]), local = clone(base), server = clone(base);
  local.layers[0].x = 100; server.layers[0].opacity = 0.5;
  const r = mergeProjects(base, local, server);
  assert.equal(get(r, 'a').x, 100); assert.equal(get(r, 'a').opacity, 0.5);
  assert.deepEqual(fields(r.applied), ['a.x']); assert.deepEqual(r.conflicts, []);
});
test('same field on both sides: the server value is kept and reported', () => {
  const base = P([L('a')]), local = clone(base), server = clone(base);
  local.layers[0].x = 100; server.layers[0].x = 200; local.layers[0].text = 'mine';
  const r = mergeProjects(base, local, server);
  assert.equal(get(r, 'a').x, 200); assert.equal(get(r, 'a').text, 'mine');
  assert.deepEqual(fields(r.conflicts), ['a.x']); assert.deepEqual(fields(r.applied), ['a.text']);
  assert.equal(r.conflicts[0].name, 'A');
});
test('both sides made the same edit: no conflict, nothing to report', () => {
  const base = P([L('a')]), local = clone(base), server = clone(base);
  local.layers[0].x = 7; server.layers[0].x = 7;
  const r = mergeProjects(base, local, server);
  assert.equal(get(r, 'a').x, 7); assert.equal(r.conflicts.length, 0); assert.equal(r.applied.length, 0);
});
test('nested objects merge per key (crop.top vs crop.left)', () => {
  const base = P([L('a')]), local = clone(base), server = clone(base);
  local.layers[0].crop.top = 10; server.layers[0].crop.left = 20;
  const r = mergeProjects(base, local, server);
  assert.deepEqual(get(r, 'a').crop, { top: 10, left: 20 });
  assert.deepEqual(fields(r.applied), ['a.crop.top']);
});
test('a layer you added survives; a layer the agent added survives', () => {
  const base = P([L('a')]), local = clone(base), server = clone(base);
  local.layers.unshift(L('mine')); server.layers.push(L('theirs'));
  const r = mergeProjects(base, local, server);
  assert.deepEqual(ids(r).sort(), ['a', 'mine', 'theirs']);
  assert.equal(r.conflicts.length, 0);
});
test('you deleted a layer the agent did not touch: it stays deleted; the agent edited it: the edit wins', () => {
  const base = P([L('a'), L('b')]), local = clone(base), server = clone(base);
  local.layers = local.layers.filter(l => l.id !== 'a' && l.id !== 'b'); server.layers[1].x = 5;
  const r = mergeProjects(base, local, server);
  assert.deepEqual(ids(r), ['b']); assert.equal(get(r, 'b').x, 5);
  assert.deepEqual(fields(r.conflicts), ['b.−']);
});
test('the agent deleted a layer you edited: the deletion wins and is reported; untouched by you: silently gone', () => {
  const base = P([L('a'), L('b')]), local = clone(base), server = clone(base);
  local.layers[0].x = 1; server.layers = [];
  const r = mergeProjects(base, local, server);
  assert.deepEqual(ids(r), []); assert.deepEqual(fields(r.conflicts), ['a.−']);
});
test('top-level fields merge too (name vs global.grain vs the same width)', () => {
  const base = P([]), local = clone(base), server = clone(base);
  local.name = 'Mine'; server.global.grain = 0.3; local.width = 1080; server.width = 720;
  const r = mergeProjects(base, local, server);
  assert.equal(r.merged.name, 'Mine'); assert.equal(r.merged.global.grain, 0.3); assert.equal(r.merged.width, 720);
  assert.deepEqual(r.conflicts.map(c => c.field), ['width']);
});
test('your reordering wins when the agent did not reorder; new server layers keep their place', () => {
  const base = P([L('a'), L('b'), L('c')]), local = clone(base), server = clone(base);
  local.layers = [local.layers[2], local.layers[0], local.layers[1]]; // c a b
  server.layers.splice(1, 0, L('n')); // a n b c
  const r = mergeProjects(base, local, server);
  assert.deepEqual(ids(r), ['c', 'a', 'n', 'b']);
});
test('notes merge by id', () => {
  const N = (id, extra = {}) => ({ id, t: 1, text: id, done: false, ...extra });
  const base = P([], { notes: [N('n1'), N('n2')] }), local = clone(base), server = clone(base);
  local.notes[0].done = true; server.notes[1].done = true; local.notes.push(N('n3'));
  const r = mergeProjects(base, local, server);
  assert.deepEqual(r.merged.notes.map(x => [x.id, x.done]), [['n1', true], ['n2', true], ['n3', false]]);
});
test('no local change: result is exactly the server project', () => {
  const base = P([L('a')]), server = clone(base); server.layers[0].x = 9; server.layers.push(L('b'));
  const r = mergeProjects(base, clone(base), server);
  assert.deepEqual(r.merged, server); assert.equal(r.applied.length + r.conflicts.length, 0);
});
test('inputs are not mutated', () => {
  const base = P([L('a')]), local = clone(base), server = clone(base); local.layers[0].x = 1;
  const b0 = JSON.stringify(base), l0 = JSON.stringify(local), s0 = JSON.stringify(server);
  const r = mergeProjects(base, local, server); r.merged.layers[0].x = 99;
  assert.equal(JSON.stringify(base), b0); assert.equal(JSON.stringify(local), l0); assert.equal(JSON.stringify(server), s0);
});
console.log(JSON.stringify({ n }));

// merge.js — three-way merge of a project after a save conflict (HTTP 409): pure, no DOM, no imports (unit-tested with node).
//   base   = the last version we saved / loaded      local  = what the user has now      server = what is on disk now (the agent edited it)
// Rule: a field changed on ONE side takes that side's value; changed on BOTH sides to different values → the server's value is kept and the clash is reported.
// Layers and notes are matched by id; inside a layer every field is compared on its own (objects such as keys / crop / shadow one level deeper).
// Returns { merged, applied: [{scope,id,name,field}], conflicts: [{scope,id,name,field}] }  (applied = your edits that made it in, conflicts = the server's value was kept).

const isObj = v => v !== null && typeof v === 'object' && !Array.isArray(v);
const canon = v => (Array.isArray(v) ? '[' + v.map(canon).join(',') + ']' : isObj(v) ? '{' + Object.keys(v).sort().map(k => JSON.stringify(k) + ':' + canon(v[k])).join(',') + '}' : JSON.stringify(v) ?? 'undefined');
export const same = (a, b) => canon(a) === canon(b);
const clone = v => (v === undefined ? undefined : JSON.parse(JSON.stringify(v)));

function mergeValue(b, l, s, field, ctx, depth) {
  if (same(l, s)) return clone(l);
  if (same(b, l)) return clone(s);
  if (same(b, s)) { ctx.applied.push({ ...ctx.at, field }); return clone(l); }
  if (depth < 2 && isObj(l) && isObj(s) && (isObj(b) || b === undefined)) {
    const out = {}; const bb = b || {};
    for (const k of new Set([...Object.keys(bb), ...Object.keys(l), ...Object.keys(s)])) {
      const v = mergeValue(bb[k], l[k], s[k], field ? field + '.' + k : k, ctx, depth + 1);
      if (v !== undefined) out[k] = v;
    }
    return out;
  }
  ctx.conflicts.push({ ...ctx.at, field });
  return clone(s);
}
const mergeFields = (b, l, s, ctx, skip = []) => mergeValue(strip(b, skip), strip(l, skip), strip(s, skip), '', ctx, 0);
const strip = (o, skip) => { const c = { ...o }; for (const k of skip) delete c[k]; return c; };

function insertAfter(seq, id, ref) {
  const at = ref.indexOf(id);
  for (let i = at - 1; i >= 0; i--) { const j = seq.indexOf(ref[i]); if (j >= 0) { seq.splice(j + 1, 0, id); return; } }
  seq.unshift(id);
}
const order = (a, of) => a.filter(id => of.has(id));

/** Merge two id-keyed lists (layers or notes). */
function mergeList(bl, ll, sl, scope, ctx) {
  const B = new Map(bl.map(x => [x.id, x])), L = new Map(ll.map(x => [x.id, x])), S = new Map(sl.map(x => [x.id, x]));
  const kept = new Map(); // id -> merged item
  const nameOf = (...xs) => { for (const x of xs) if (x && x.name) return x.name; return undefined; };
  const ids = new Set([...B.keys(), ...L.keys(), ...S.keys()]);
  for (const id of ids) {
    const b = B.get(id), l = L.get(id), s = S.get(id);
    ctx.at = { scope, id, name: nameOf(s, l, b) || (scope === 'note' ? nameOfNote(s || l || b) : id) };
    if (b === undefined) {                       // added
      if (l && s) kept.set(id, mergeFields({}, l, s, ctx));
      else if (l) { ctx.applied.push({ ...ctx.at, field: '+' }); kept.set(id, clone(l)); }
      else kept.set(id, clone(s));
    } else if (l === undefined && s === undefined) { /* removed on both sides */ }
    else if (l === undefined) {                  // you removed it
      if (same(b, s)) ctx.applied.push({ ...ctx.at, field: '−' });
      else { ctx.conflicts.push({ ...ctx.at, field: '−' }); kept.set(id, clone(s)); }
    } else if (s === undefined) {                // the agent removed it
      if (!same(b, l)) ctx.conflicts.push({ ...ctx.at, field: '−', by: 'server' });
    } else kept.set(id, mergeFields(b, l, s, ctx));
  }
  // order: your reordering wins when the agent did not reorder; the other side's new items slot in after their neighbour
  const bIds = bl.map(x => x.id), lIds = ll.map(x => x.id), sIds = sl.map(x => x.id);
  const lReordered = !same(order(lIds, new Set(bIds)), order(bIds, new Set(lIds)));
  const sReordered = !same(order(sIds, new Set(bIds)), order(bIds, new Set(sIds)));
  const useLocal = lReordered && !sReordered;
  const main = useLocal ? lIds : sIds, other = useLocal ? sIds : lIds;
  const seq = main.filter(id => kept.has(id));
  for (const id of other) if (kept.has(id) && !seq.includes(id)) insertAfter(seq, id, other);
  return seq.map(id => kept.get(id));
}
const nameOfNote = n => (n && n.text ? String(n.text).slice(0, 24) : '');

export function mergeProjects(base, local, server) {
  const ctx = { applied: [], conflicts: [], at: { scope: 'project', id: '', name: '' } };
  const skip = ['layers', 'notes'];
  const top = mergeFields(base || {}, local || {}, server || {}, ctx, skip);
  const layers = mergeList((base || {}).layers || [], (local || {}).layers || [], (server || {}).layers || [], 'layer', ctx);
  const notes = mergeList((base || {}).notes || [], (local || {}).notes || [], (server || {}).notes || [], 'note', ctx);
  return { merged: { ...top, layers, notes }, applied: ctx.applied, conflicts: ctx.conflicts };
}

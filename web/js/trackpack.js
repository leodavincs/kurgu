// trackpack.js — pure logic of the Premiere-style TRACK view (no DOM, no state): which layers share a track row, and where a vertical drag puts a clip.
// The project format does not change: tracks are a VIEW computed from layer order (z, first = front), group and start/end.
const EPS = 1e-6;
export const overlaps = (a, b) => a.start < b.end - EPS && b.start < a.end - EPS;   // touching (end == start) is NOT an overlap

/** Contiguous runs of the same group in z-order (ungrouped layers form a run with group ''). [{group, from, layers}] */
export function sections(layers) {
  const out = []; let cur = null;
  layers.forEach((l, i) => {
    const g = l.group || '';
    if (!cur || cur.group !== g) { cur = { group: g, from: i, layers: [] }; out.push(cur); }
    cur.layers.push(l);
  });
  return out;
}

/** First-fit packing of one section (front layer first). Returns {track: Map id -> index (0 = top row), count}. */
export function pack(list) {
  const rows = [], track = new Map();
  for (const l of list) {
    let k = rows.findIndex(r => !r.some(o => overlaps(o, l)));
    if (k < 0) { k = rows.length; rows.push([]); }
    rows[k].push(l); track.set(l.id, k);
  }
  return { track, count: rows.length, rows };
}

/** Layout of a whole project: [{group, from, tracks: [[layer, ...] per row, top first], track: Map}] */
export function layout(layers) {
  return sections(layers).map(s => { const p = pack(s.layers); return { group: s.group, from: s.from, tracks: p.rows.map(r => r.slice().sort((a, b) => a.start - b.start)), track: p.track }; });
}

/** Where must `x` (already with its new times) sit in `rest` (the other layers of the target section, z-order) so that it lands on a given track?
 *  goal {track: k} -> row k (free at that time); goal {gap: g} -> row g although clips there overlap it: it goes in front of them and pushes them down a row.
 *  Returns {index, track, grew}: the insertion index into `rest` (0 = front), the row it really lands on and whether that needs one more row than before.
 *  Rows are derived by first fit, so a clip that overlaps nothing always lands on the top row: a vertical move only changes what it must (its z-order against overlapping clips). */
export function place(rest, x, goal) {
  const base = pack(rest);
  const want = goal.gap != null ? goal.gap : goal.track;
  const first = base.rows[Math.min(want, base.rows.length - 1)];
  const p0 = first && first.length ? rest.indexOf(first[0]) : rest.length;
  let best = null;
  for (let p = 0; p <= rest.length; p++) {
    const arr = rest.slice(); arr.splice(p, 0, x);
    const pk = pack(arr), tx = pk.track.get(x.id);
    const score = Math.abs(tx - want) * 1000 + Math.abs(p - p0) * 2 + p * 0.001;
    if (!best || score < best.score) best = { score, index: p, track: tx, grew: pk.count > base.count };
  }
  return { index: best.index, track: best.track, grew: best.grew };
}

/** Does `x` collide with something already on row k of `rest`? */
export function collides(rest, x, k) {
  const rows = pack(rest).rows; return !!rows[k] && rows[k].some(o => overlaps(o, x));
}

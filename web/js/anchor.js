// anchor.js — note anchors: a note follows the content it was written on (SPEC 1.0, "Anchors"). JS twin of note_anchor.py.
//
// The truth is the anchor; a note's `t` is derived from it by follow() below. It runs inside normalizeProject() (every load, reload, undo) and
// inside change() (every edit of the editor, so the cascade of note moves is part of the SAME undo step) in state.js. The server runs the
// Python twin in schema.normalize and in its write funnel. The two twins must give the same answers: tests/anchor_vectors.json pins both.
// Keep this file line-for-line parallel to note_anchor.py; change both, then run tests/make_anchor_vectors.py. No imports, no state, no I/O.

export const EPS_T = 1e-4;   // a resolved time this close to the note's time leaves `t` as it is
export const EPS_IN = 1e-6;  // tolerance at a layer's start for "inside"
const RANK = { video: 1, image: 2, text: 3, color: 4, audio: 5 };   // rank 0 = a code-made unit (video / image with a usable `source`)
const own = (o, k) => Object.prototype.hasOwnProperty.call(o, k);   // like Python's `k in dict`: never "toString" or "constructor"

/** A finite JSON number (not a bool, not a string) or null. */
export function num(v) { return typeof v === 'number' && Number.isFinite(v) ? v : null; }
const rnd = (x, k) => { const s = 10 ** k; return Math.floor(x * s + 0.5) / s; };
const isObj = v => !!v && typeof v === 'object' && !Array.isArray(v);
const str = v => typeof v === 'string';

function span(l) {
  let st = num(l.start); st = st === null ? 0 : st;
  const en = num(l.end);
  return [st, en === null ? st + 3 : en];
}
const srcIn = l => { const v = num(l.src_in); return v === null ? 0 : v; };
const speed = l => { const v = num(l.speed); return v !== null && v > 0 ? v : 1; };
function unitOf(l) {
  const s = l.source;
  if ((l.type !== 'video' && l.type !== 'image') || !isObj(s)) return null;
  const u = s.unit, fps = num(s.fps);
  if (!str(u) || !u || fps === null || fps <= 0) return null;
  return [u, fps, s];
}
function origin(s) {
  let o = num(s.media_start_frame);
  if (o === null) o = num(s.unit_start_frame);
  return o === null ? 0 : o;
}
const unitStart = s => { const v = num(s.unit_start_frame); return v === null ? 0 : v; };

/** What makes a layer the same layer for an anchor: type + src for media, the type alone for text and colour (their content is what notes ask to change). */
export function sig(l) {
  const typ = str(l.type) ? l.type : '';
  if (typ === 'video' || typ === 'audio' || typ === 'image') return typ + ':' + (str(l.src) ? l.src : '');
  return typ;
}
/** Is `l` still the anchored layer? Same signature, or the same type when the src changed (the caller also needs the moment inside `l`). */
const sameKind = (sg, l) => sg === null || sig(l) === sg || sg.split(':')[0] === (str(l.type) ? l.type : '');
/** A layer anchor on layer `l` at project time `t`. */
export function layerAnchor(l, t) {
  const [st] = span(l);
  return { layer: l.id, dt: rnd((t - st) * speed(l) + srcIn(l), 6), sig: sig(l), t };
}

/** Is `a` a usable anchor shape? (normalizeProject drops the others.) */
export function valid(a) {
  if (!isObj(a)) return false;
  if (str(a.unit) && a.unit && num(a.frame) !== null) return true;
  return str(a.layer) && !!a.layer && num(a.dt) !== null;
}

function anchorOn(l, t) {
  const us = unitOf(l);
  if (us) {
    const [u, fps, s] = us;
    const [st] = span(l);
    const f = origin(s) + (t - st + srcIn(l)) * fps;
    return { unit: u, frame: rnd(f, 3), unit_start: unitStart(s), t };
  }
  return layerAnchor(l, t);
}
function onScreen(l, t) {
  if (!isObj(l) || l.hidden === true || !str(l.type) || !own(RANK, l.type)) return false;
  if (!str(l.id) || !l.id) return false;
  const [st, en] = span(l);
  return st <= t && t < en;
}

/** The anchor for a note written at project time `t`. `under`: ids of the layers under the note's mark, top first; the first picture layer
 *  of them on screen wins. Otherwise the scene: front-most code-made unit, else front-most video, image, text, colour, audio layer. */
export function anchorAt(p, t, under = null) {
  t = num(t);
  if (t === null) return null;
  const layers = Array.isArray(p.layers) ? p.layers : [];
  for (const lid of Array.isArray(under) ? under : []) {
    const l = layers.find(x => isObj(x) && x.id === lid);
    if (l !== undefined && onScreen(l, t) && l.type !== 'audio') return anchorOn(l, t);
  }
  let best = null;
  for (let i = 0; i < layers.length; i++) {
    const l = layers[i];
    if (!onScreen(l, t)) continue;
    const r = unitOf(l) ? 0 : RANK[l.type];
    if (best === null || r < best[0]) best = [r, i, l];
  }
  return best === null ? null : anchorOn(best[2], t);
}

const mediaEnd = l => { const [st, en] = span(l); return srcIn(l) + (en - st) * speed(l); };
/** Layers that continue `a`'s media (same signature, video / audio, contiguous src_in ranges, transitively): the pieces of a split. */
function splitChain(layers, a, sg) {
  if (a.type !== 'video' && a.type !== 'audio') return [];
  const pool = layers.filter(l => l !== a && (l.type === 'video' || l.type === 'audio') && sig(l) === sg && str(l.id));
  const chain = [], todo = [a];
  while (todo.length) {
    const x = todo.pop();
    for (const l of pool) {
      if (chain.includes(l)) continue;
      if (Math.abs(mediaEnd(x) - srcIn(l)) < 1e-3 || Math.abs(mediaEnd(l) - srcIn(x)) < 1e-3) { chain.push(l); todo.push(l); }
    }
  }
  return chain;
}
function inside(l, dt) {
  const [st, en] = span(l);
  const tt = st + (dt - srcIn(l)) / speed(l);
  return st - EPS_IN <= tt && tt < en ? Math.max(tt, st) : null;
}

/** [time, layer id] the anchor points at now; [null, null] when that moment is not inside its layer (see note_anchor.locate). */
export function locate(p, a, t) {
  t = num(t); if (t === null) t = 0;
  const layers = (Array.isArray(p.layers) ? p.layers : []).filter(isObj);
  if (str(a.unit) && a.unit && num(a.frame) !== null) {
    let best = null;
    for (const l of layers) {
      const us = unitOf(l);
      if (!us || us[0] !== a.unit) continue;
      const [, fps, s] = us;
      let f = num(a.frame);
      if (num(a.unit_start) !== null) f = f - num(a.unit_start) + unitStart(s);
      const [st, en] = span(l);
      let tt = st + (f - origin(s)) / fps - srcIn(l);
      if (!(st - EPS_IN <= tt && tt < en)) continue;
      tt = Math.max(tt, st);
      if (best === null || Math.abs(tt - t) < Math.abs(best - t)) best = tt;
    }
    return [best, null];
  }
  if (str(a.layer) && a.layer && num(a.dt) !== null) {
    const l = layers.find(x => x.id === a.layer);
    const sg = str(a.sig) ? a.sig : null;
    if (l === undefined || !sameKind(sg, l)) return [null, null];
    const dt = num(a.dt);
    const tt = inside(l, dt);
    if (tt !== null) return [tt, l.id];
    if (sg !== null && sig(l) !== sg) return [null, null];   // src changed AND the moment is not on it: not the same clip
    let best = null;
    for (const x of splitChain(layers, l, sig(l))) {
      const tx = inside(x, dt);
      if (tx !== null && (best === null || Math.abs(tx - t) < Math.abs(best[0] - t))) best = [tx, x.id];
    }
    return best || [null, null];
  }
  return [null, null];
}
/** Project time the anchor points at now, or null. */
export function resolve(p, a, t) { return locate(p, a, t)[0]; }

/** Ids of the layers under the note's first mark, top first. */
export function markLayers(n) {
  const m = Array.isArray(n.marks) && n.marks.length ? n.marks[0] : null;
  const ls = isObj(m) && Array.isArray(m.layers) ? m.layers : [];
  return ls.filter(x => isObj(x) && str(x.id) && x.id).map(x => x.id);
}

/** Re-derive every anchored note's `t` from its anchor, in place (rules in note_anchor.follow). Returns the ids whose `t` changed. */
export function follow(p) {
  const moved = [];
  for (const n of Array.isArray(p.notes) ? p.notes : []) {
    if (!isObj(n) || !isObj(n.anchor)) continue;
    const t = num(n.t);
    if (t === null) continue;
    let a = n.anchor;
    if (!valid(a)) continue;
    if (num(a.t) !== null && num(a.t) !== t) {
      const na = anchorAt(p, t, markLayers(n));
      if (na) n.anchor = na; else delete n.anchor;
      continue;
    }
    let [tt, lid] = locate(p, a, t);
    if (tt === null) { a.stale = true; a.t = n.t; continue; }
    tt = rnd(Math.max(0, tt), 6);
    if (Math.abs(tt - t) > EPS_T) { n.t = tt; moved.push(n.id); }
    if (lid !== null) {
      const lay = p.layers.find(x => isObj(x) && x.id === lid);
      if (lid !== a.layer) a = n.anchor = layerAnchor(lay, n.t);   // a split piece shows it now: anchor to that piece
      else if (str(a.sig) && a.sig !== sig(lay)) a.sig = sig(lay);   // the same layer, relinked / older signature: keep it current
    }
    delete a.stale;
    a.t = n.t;
  }
  return moved;
}

// anim.js — motion maths (keyframes, easing, noise modifiers). JS twin of anim.py: keep them line-for-line parallel.
// Pure functions, no DOM, no state, no Math.random. Both twins are checked against tests/anim_vectors.json (tolerance 1e-9).
// Key model (SPEC §1.8): layer.keys[prop] = [{t, v, ease, hold}], t = layer-local seconds, held outside the first/last key,
// the ease of a key shapes the segment LEAVING it; value = keys (or the static base) + modifiers (layer.mods[prop]), then clamped.
// Effect params use the prop name "fx.<effect id>.<param>".

export const TRANSFORM_PROPS = ['x', 'y', 'scale', 'scale_x', 'scale_y', 'rotation', 'anchor_x', 'anchor_y'];
export const DEFAULTS = { scale: 1, scale_x: 1, scale_y: 1, rotation: 0, anchor_x: 0.5, anchor_y: 0.5, opacity: 1, volume_db: 0 };
const EASE_FAMILIES = ['sine', 'quad', 'cubic', 'quart', 'quint', 'expo', 'circ', 'back', 'elastic', 'bounce'];
const CSS_EASES = { 'ease': [0.25, 0.1, 0.25, 1.0], 'ease-in': [0.42, 0.0, 1.0, 1.0], 'ease-out': [0.0, 0.0, 0.58, 1.0], 'ease-in-out': [0.42, 0.0, 0.58, 1.0] };
const JUMPS = ['end', 'start', 'none', 'both'];
const isNum = v => typeof v === 'number' && Number.isFinite(v);
const numOr = (v, d) => (isNum(v) ? v : d);

// ---------------------------------------------------------------- integer hash + noise
export function lowbias32(x) {
  x >>>= 0;
  x = (x ^ (x >>> 16)) >>> 0;
  x = Math.imul(x, 0x7feb352d) >>> 0;
  x = (x ^ (x >>> 15)) >>> 0;
  x = Math.imul(x, 0x846ca68b) >>> 0;
  x = (x ^ (x >>> 16)) >>> 0;
  return x;
}
export function hash32(seed, a, b) {
  let h = lowbias32(Math.trunc(seed));
  h = lowbias32((h ^ (Math.trunc(a) >>> 0)) >>> 0);
  return lowbias32((h ^ (Math.trunc(b) >>> 0)) >>> 0);
}
export function fnv1a(s) {
  let h = 0x811c9dc5;
  const bytes = new TextEncoder().encode(s);
  for (const ch of bytes) { h = (h ^ ch) >>> 0; h = Math.imul(h, 0x01000193) >>> 0; }
  return h;
}
const lattice = (seed, i, o) => hash32(seed, i, o) / 4294967296.0 * 2.0 - 1.0;
export function fbm(seed, x, octaves) {
  let total = 0, norm = 0, amp = 1, f = 1;
  for (let o = 0; o < octaves; o++) {
    const xx = x * f, i = Math.floor(xx), fr = xx - i, u = fr * fr * (3.0 - 2.0 * fr);
    const a = lattice(seed, i, o), b = lattice(seed, i + 1, o);
    total += amp * (a + (b - a) * u);
    norm += amp; amp *= 0.5; f *= 2.0;
  }
  return total / norm;
}
/** One modifier's contribution at layer-local time t (wiggle = smooth fbm, noise = sample-and-hold). */
export function modValue(mod, prop, index, t) {
  const typ = mod.type;
  if (typ !== 'wiggle' && typ !== 'noise') return 0;
  const t0 = numOr(mod.t0, 0), t1 = mod.t1;
  if (t < t0 || (isNum(t1) && t >= t1)) return 0;
  const amp = numOr(mod.amp, 1), freq = numOr(mod.freq, 5), decay = numOr(mod.decay, 0);
  const seed = hash32(Math.trunc(numOr(mod.seed, 0)), fnv1a(prop), index);
  let n;
  if (typ === 'wiggle') n = fbm(seed, t * freq, Math.max(1, Math.min(6, Math.trunc(numOr(mod.octaves, 1)))));
  else n = lattice(seed, Math.floor(t * freq), 0);
  const env = decay > 0 ? Math.exp(-decay * (t - t0)) : 1.0;
  return amp * n * env;
}

// ---------------------------------------------------------------- easing
const bezX = (ax, bx, cx, t) => ((ax * t + bx) * t + cx) * t;
/** CSS cubic-bezier(x1,y1,x2,y2) at progress x: x1/x2 clamped to 0..1, Newton (8) then bisection (64). */
export function bezier(x1, y1, x2, y2, x) {
  if (x <= 0) return 0;
  if (x >= 1) return 1;
  x1 = Math.min(1, Math.max(0, x1)); x2 = Math.min(1, Math.max(0, x2));
  const cx = 3 * x1, bx = 3 * (x2 - x1) - cx, ax = 1 - cx - bx;
  const cy = 3 * y1, by = 3 * (y2 - y1) - cy, ay = 1 - cy - by;
  let t = x, solved = false;
  for (let i = 0; i < 8; i++) {
    const e = bezX(ax, bx, cx, t) - x;
    if (Math.abs(e) < 1e-12) { solved = true; break; }
    const d = (3 * ax * t + 2 * bx) * t + cx;
    if (Math.abs(d) < 1e-12) break;
    t -= e / d;
    if (t < 0 || t > 1) break;
  }
  if (!solved && Math.abs(bezX(ax, bx, cx, t) - x) >= 1e-12) {
    let lo = 0, hi = 1; t = x;
    for (let i = 0; i < 64; i++) {
      const e = bezX(ax, bx, cx, t);
      if (Math.abs(e - x) < 1e-12) break;
      if (x > e) lo = t; else hi = t;
      t = (hi - lo) * 0.5 + lo;
    }
  }
  return ((ay * t + by) * t + cy) * t;
}
function steps(n, jump, x) {
  n = Math.max(1, Math.trunc(n));
  if (jump === 'start') return Math.ceil(x * n) / n;
  if (jump === 'none') return n > 1 ? Math.min(1, Math.floor(x * n) / Math.max(1, n - 1)) : 0;
  if (jump === 'both') return (Math.floor(x * n) + 1) / (n + 1);
  return Math.floor(x * n) / n;
}
function bounceOut(x) {
  const n1 = 7.5625, d1 = 2.75;
  if (x < 1 / d1) return n1 * x * x;
  if (x < 2 / d1) { x -= 1.5 / d1; return n1 * x * x + 0.75; }
  if (x < 2.5 / d1) { x -= 2.25 / d1; return n1 * x * x + 0.9375; }
  x -= 2.625 / d1; return n1 * x * x + 0.984375;
}
function elasticOut(x, amp, period) {
  if (x <= 0) return 0;
  if (x >= 1) return 1;
  const a = Math.max(1, amp), s = period / (2 * Math.PI) * Math.asin(1 / a);
  return a * Math.pow(2, -10 * x) * Math.sin((x - s) * (2 * Math.PI) / period) + 1;
}
function powIn(n, x) { let r = x; for (let i = 0; i < n - 1; i++) r *= x; return r; }
function family(name, d, x, p) {
  if (name === 'quad' || name === 'cubic' || name === 'quart' || name === 'quint') {
    const n = { quad: 2, cubic: 3, quart: 4, quint: 5 }[name];
    if (d === 'in') return powIn(n, x);
    if (d === 'out') return 1 - powIn(n, 1 - x);
    if (x < 0.5) return powIn(n, 2 * x) * Math.pow(2, n - 1) / Math.pow(2, n);
    return 1 - powIn(n, 2 - 2 * x) / 2;
  }
  if (name === 'sine') {
    if (d === 'in') return 1 - Math.cos(x * Math.PI / 2);
    if (d === 'out') return Math.sin(x * Math.PI / 2);
    return -(Math.cos(Math.PI * x) - 1) / 2;
  }
  if (name === 'expo') {
    if (d === 'in') return Math.pow(2, 10 * x - 10);
    if (d === 'out') return 1 - Math.pow(2, -10 * x);
    if (x < 0.5) return Math.pow(2, 20 * x - 10) / 2;
    return (2 - Math.pow(2, -20 * x + 10)) / 2;
  }
  if (name === 'circ') {
    if (d === 'in') return 1 - Math.sqrt(1 - x * x);
    if (d === 'out') return Math.sqrt(1 - (x - 1) * (x - 1));
    if (x < 0.5) return (1 - Math.sqrt(1 - (2 * x) * (2 * x))) / 2;
    return (Math.sqrt(1 - (-2 * x + 2) * (-2 * x + 2)) + 1) / 2;
  }
  if (name === 'back') {
    const s = p.overshoot ?? 1.70158;
    if (d === 'in') return (s + 1) * x * x * x - s * x * x;
    if (d === 'out') { const y = x - 1; return 1 + (s + 1) * y * y * y + s * y * y; }
    const c2 = s * 1.525;
    if (x < 0.5) return ((2 * x) * (2 * x) * ((c2 + 1) * 2 * x - c2)) / 2;
    const y = 2 * x - 2; return (y * y * ((c2 + 1) * y + c2) + 2) / 2;
  }
  if (name === 'elastic') {
    const a = p.amp ?? 1, per = p.period ?? 0.3;
    if (d === 'out') return elasticOut(x, a, per);
    if (d === 'in') return 1 - elasticOut(1 - x, a, per);
    const per2 = per * 1.5;
    if (x < 0.5) return (1 - elasticOut(1 - 2 * x, a, per2)) / 2;
    return (1 + elasticOut(2 * x - 1, a, per2)) / 2;
  }
  if (name === 'bounce') {
    if (d === 'out') return bounceOut(x);
    if (d === 'in') return 1 - bounceOut(1 - x);
    if (x < 0.5) return (1 - bounceOut(1 - 2 * x)) / 2;
    return (1 + bounceOut(2 * x - 1)) / 2;
  }
  return x;
}
/** Normalise any ease spec to an array, or null if invalid. */
export function parseEase(spec) {
  if (spec === null || spec === undefined) return ['linear'];
  if (typeof spec === 'string') {
    let s = spec.trim().toLowerCase();
    if (s === 'linear') return ['linear'];
    if (s === 'hold') return ['hold'];
    if (CSS_EASES[s]) return ['bezier', ...CSS_EASES[s]];
    if (s.startsWith('cubic-bezier(') && s.endsWith(')')) {
      const v = s.slice(13, -1).split(',').map(a => Number(a));
      return v.length === 4 && v.every(Number.isFinite) && !s.slice(13, -1).split(',').some(a => a.trim() === '') ? ['bezier', ...v] : null;
    }
    if (s.startsWith('steps(') && s.endsWith(')')) {
      const parts = s.slice(6, -1).split(',').map(a => a.trim());
      const n = /^[+-]?\d+$/.test(parts[0]) ? parseInt(parts[0], 10) : NaN;
      let j = parts[1] ?? 'end';
      j = { 'jump-end': 'end', 'jump-start': 'start', 'jump-none': 'none', 'jump-both': 'both' }[j] ?? j;
      return n >= 1 && JUMPS.includes(j) && parts.length <= 2 ? ['steps', n, j] : null;
    }
    if (s.startsWith('ease-') && !CSS_EASES[s]) s = s.slice(5);
    for (const d of ['in-out', 'in', 'out']) if (s.startsWith(d + '-') && EASE_FAMILIES.includes(s.slice(d.length + 1))) return ['fam', s.slice(d.length + 1), d, {}];
    return null;
  }
  if (Array.isArray(spec)) return spec.length === 4 && spec.every(isNum) ? ['bezier', ...spec] : null;
  if (typeof spec === 'object') {
    const typ = spec.type;
    if (typ === 'bezier' && Array.isArray(spec.points)) return parseEase(spec.points);
    if (typ === 'steps') { const j = spec.jump ?? 'end', n = spec.n ?? 1; return isNum(n) && n >= 1 && JUMPS.includes(j) ? ['steps', Math.trunc(n), j] : null; }
    if (typ === 'back' || typ === 'elastic' || typ === 'bounce') {
      const d = spec.mode ?? 'out';
      if (!['in', 'out', 'in-out'].includes(d)) return null;
      const p = {};
      if (typ === 'back') p.overshoot = numOr(spec.overshoot, 1.70158);
      if (typ === 'elastic') { p.amp = numOr(spec.amp, 1); p.period = Math.max(1e-3, numOr(spec.period, 0.3)); }
      return ['fam', typ, d, p];
    }
  }
  return null;
}
export function easeP(parsed, x) {
  const k = parsed[0];
  if (k === 'linear') return x;
  if (x <= 0) return 0;
  if (k === 'hold') return x >= 1 ? 1 : 0;
  if (x >= 1) return 1;
  if (k === 'bezier') return bezier(parsed[1], parsed[2], parsed[3], parsed[4], x);
  if (k === 'steps') return steps(parsed[1], parsed[2], x);
  return family(parsed[1], parsed[2], x, parsed[3]);
}
/** Eased progress for spec at x (clamped to 0..1). Unknown specs behave as linear. */
export function ease(spec, x) {
  const p = parseEase(spec);
  x = Math.min(1, Math.max(0, x));
  return p === null ? x : easeP(p, x);
}

// ---------------------------------------------------------------- keyframes
export function keyValue(keys, t) {
  const n = keys.length;
  if (n === 0) return null;
  if (t < keys[0].t) return keys[0].v;
  let i = 0;
  for (let j = 0; j < n; j++) { if (keys[j].t <= t) i = j; else break; }
  const a = keys[i];
  if (i === n - 1) return a.v;
  const b = keys[i + 1];
  if (a.hold === true) return a.v;
  const f = (t - a.t) / (b.t - a.t);
  return a.v + (b.v - a.v) * ease(a.ease, f);
}
export function propValue(layer, prop, t, base) {
  const ks = layer.keys && layer.keys[prop];
  let v = ks && ks.length ? keyValue(ks, t) : base;
  const mods = layer.mods && layer.mods[prop];
  if (mods) for (let j = 0; j < mods.length; j++) v += modValue(mods[j], prop, j, t);
  return v;
}
/** Animated value of a layer property at layer-local time t (static value / default when nothing animates it). */
export function value(layer, prop, t, dflt) {
  let base = layer[prop];
  if (!isNum(base)) base = dflt === undefined ? (DEFAULTS[prop] ?? 0) : dflt;
  let v = propValue(layer, prop, t, base);
  if (prop === 'opacity') v = Math.min(1, Math.max(0, v));
  return v;
}
export function isAnimated(layer, props) {
  for (const src of [layer.keys || {}, layer.mods || {}]) {
    for (const k of Object.keys(src)) if (src[k] && src[k].length && (!props || props.includes(k))) return true;
  }
  return false;
}
/** Copy of an effect with keyed / modified numeric params at layer-local time t. */
export function effectParams(layer, effect, t) {
  const eid = effect.id;
  if (!eid) return effect;
  const prefix = `fx.${eid}.`, names = new Set();
  for (const src of [layer.keys || {}, layer.mods || {}]) for (const k of Object.keys(src)) if (src[k] && src[k].length && k.startsWith(prefix)) names.add(k.slice(prefix.length));
  if (!names.size) return effect;
  const out = { ...effect };
  for (const n of names) out[n] = propValue(layer, prefix + n, t, numOr(effect[n], 0));
  return out;
}
/** Raster scale for text/media (see anim.py raster_scale). Static layers: = scale. */
export function rasterScale(layer) {
  const ext = (prop, base) => { const ks = layer.keys && layer.keys[prop]; return ks && ks.length ? Math.max(...ks.map(k => Math.abs(k.v))) : Math.abs(base); };
  const st = prop => (isNum(layer[prop]) ? layer[prop] : 1);
  const s = ext('scale', st('scale'));
  const m = Math.max(1, ext('scale_x', st('scale_x')), ext('scale_y', st('scale_y')));
  return Math.max(0.01, s * m);
}

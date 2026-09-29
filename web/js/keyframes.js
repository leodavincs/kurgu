// keyframes.js — keyframe editing model and UI pieces shared by the inspector, the timeline lanes and the viewer
// (After Effects mental model, SPEC §1.8). Keys live in layer.keys[prop] = [{t, v, ease?, hold?}], t = layer-local seconds.
//   • a "spec" names a property of one layer: {id, prop} or, for effect params, {id, fx: {index, param}} (the effect gets an id on first use)
//   • writeProp() is the one place that decides "static value or key at the playhead" (auto-key)
//   • key selection (used by the lanes), key clipboard, Delete / copy / paste / E shortcuts (capture phase, only while keys are selected)
//   • the easing popover (presets + cubic-bezier editor) and the "Animate" presets menu
import { S, change, layer, clamp, loadExternal, setTime } from './state.js';
import { el, icon } from './dom.js';
import { t, getLang } from './i18n.js';
import * as A from './anim.js';
import { saveAndWait } from './api.js';
import { isMod } from './platform.js';

const EPS = 0.0006;                                   // two key times closer than this are the same key
const round = (v, n) => { const c = Math.pow(10, n); return Math.round(v * c) / c; };
export const round3 = v => round(v, 3);
const isNum = v => typeof v === 'number' && Number.isFinite(v);

// ---------------------------------------------------------------- property model
/** Canonical order of lanes / fields. */
export const PROP_ORDER = ['x', 'y', 'scale', 'scale_x', 'scale_y', 'rotation', 'anchor_x', 'anchor_y', 'opacity', 'volume_db'];
export const isFx = prop => prop.startsWith('fx.');
const fxParts = prop => { const m = /^fx\.([^.]+)\.(.+)$/.exec(prop); return m ? { id: m[1], param: m[2] } : null; };
export const propRank = prop => { const i = PROP_ORDER.indexOf(prop); return i >= 0 ? i : 100; };
/** Layer property names that currently have keys, in lane order (transform, opacity, volume, then effect params). */
export function animatedProps(l) {
  return Object.keys(l.keys || {}).filter(k => l.keys[k] && l.keys[k].length).sort((a, b) => propRank(a) - propRank(b) || a.localeCompare(b));
}
export const keysOf = (l, prop) => (l && l.keys && l.keys[prop]) || [];
export const hasAnyKeys = l => !!l && animatedProps(l).length > 0;
const fps = () => (S.project && S.project.fps) || 30;
/** Layer-local time of the playhead, rounded to 1 ms and kept inside the layer (a key outside the layer would never play). */
export const localT = (l, time = S.t) => round3(clamp(time - l.start, 0, Math.max(0, l.end - l.start)));

function ensureFxId(l, index) {
  const e = (l.effects || [])[index]; if (!e) return null;
  if (!e.id) {
    const used = new Set((l.effects || []).map(x => x.id).filter(Boolean));
    let n = index + 1, id; do { id = e.type + n; n++; } while (used.has(id));
    e.id = id;
  }
  return e.id;
}
/** Resolve a spec to the property name used in `keys` (null when an effect has no id yet and `create` is false). */
export function propName(l, spec, create = false) {
  if (spec.prop) return spec.prop;
  const e = (l.effects || [])[spec.fx.index]; if (!e) return null;
  if (!e.id && !create) return null;
  const id = e.id || ensureFxId(l, spec.fx.index);
  return `fx.${id}.${spec.fx.param}`;
}
/** Static (un-keyed) value of a property, with the SPEC defaults. */
export function staticValue(l, prop) {
  const fx = isFx(prop) ? fxParts(prop) : null;
  if (fx) { const e = (l.effects || []).find(x => x.id === fx.id); return e && isNum(e[fx.param]) ? e[fx.param] : 0; }
  if (isNum(l[prop])) return l[prop];
  if (prop === 'x') return S.project.width / 2;
  if (prop === 'y') return S.project.height / 2;
  return A.DEFAULTS[prop] ?? 0;
}
/** Value the fields show and new keys start from: the key curve (or the static value) at `time`, without wiggle modifiers. */
export function valueAt(l, prop, time = S.t) {
  const ks = keysOf(l, prop);
  return ks.length ? A.keyValue(ks, time - l.start) : staticValue(l, prop);
}
export function valueOfSpec(l, spec, time = S.t) {
  const prop = propName(l, spec, false);
  if (prop) return valueAt(l, prop, time);
  const e = (l.effects || [])[spec.fx.index];
  return e && isNum(e[spec.fx.param]) ? e[spec.fx.param] : 0;
}
export const specHasKeys = (l, spec) => { const p = l && propName(l, spec, false); return !!p && keysOf(l, p).length > 0; };

function setStatic(l, prop, v) {
  const fx = isFx(prop) ? fxParts(prop) : null;
  if (fx) { const e = (l.effects || []).find(x => x.id === fx.id); if (e) e[fx.param] = v; } else l[prop] = v;
}
/** Insert or update the key at layer-local time `tl` (mutates the layer; call inside change()). Returns the key. */
export function upsertKey(l, prop, tl, v, extra) {
  const arr = ((l.keys = l.keys || {})[prop] = l.keys[prop] || []);
  let k = arr.find(x => Math.abs(x.t - tl) < EPS);
  if (k) k.v = round(v, 4);
  else { k = { t: tl, v: round(v, 4) }; arr.push(k); arr.sort((a, b) => a.t - b.t); }
  if (extra) Object.assign(k, extra);
  return k;
}
/** Write a value: with keys on the property it adds / updates the key at the playhead (auto-key), else it is the static value.
 *  `l` is the layer object inside a change() callback. */
export function writeProp(l, prop, v, time = S.t) {
  if (keysOf(l, prop).length) upsertKey(l, prop, localT(l, time), v);
  else setStatic(l, prop, v);
}
export function writeSpec(l, spec, v, time = S.t) {
  const prop = propName(l, spec, true); if (prop) writeProp(l, prop, v, time);
}
/** Convenience: one undoable change writing several props of one layer (drags pass a merge key). */
export function writeProps(id, props, key) {
  change(P => { const l = P.layers.find(x => x.id === id); if (!l || l.locked) return; for (const [p, v] of Object.entries(props)) writeProp(l, p, v); }, { key });
}

// ---------------------------------------------------------------- stopwatch / nav
export function toggleStopwatch(spec) {
  change(P => {
    const l = P.layers.find(x => x.id === spec.id); if (!l || l.locked) return;
    const prop = propName(l, spec, true); if (!prop) return;
    if (keysOf(l, prop).length) { const v = valueAt(l, prop); delete l.keys[prop]; if (!Object.keys(l.keys).length) delete l.keys; setStatic(l, prop, v); }
    else upsertKey(l, prop, localT(l), staticValue(l, prop));
  });
}
export function toggleKeyHere(spec) {
  const l = layer(spec.id); if (!l || l.locked) return;
  const prop = propName(l, spec, false), ks = prop ? keysOf(l, prop) : [], tl = localT(l);
  if (ks.some(k => Math.abs(k.t - tl) < EPS)) removeKeys([{ id: l.id, prop, t: ks.find(k => Math.abs(k.t - tl) < EPS).t }]);
  else change(P => { const z = P.layers.find(x => x.id === spec.id); if (z) { const p = propName(z, spec, true); upsertKey(z, p, tl, ks.length ? valueAt(z, p) : staticValue(z, p)); } });
}
export function hopKey(spec, dir) {
  const l = layer(spec.id); if (!l) return;
  const prop = propName(l, spec, false), ks = prop ? keysOf(l, prop) : [], tl = round3(S.t - l.start);
  const k = dir < 0 ? [...ks].reverse().find(x => x.t < tl - EPS) : ks.find(x => x.t > tl + EPS);
  if (k) setTime(l.start + k.t);
}
/** Remove keys ({id, prop, t}); a property left without keys keeps its value at the playhead as static value. */
export function removeKeys(items) {
  if (!items.length) return;
  change(P => {
    for (const it of items) {
      const l = P.layers.find(x => x.id === it.id); if (!l || l.locked || !l.keys || !l.keys[it.prop]) continue;
      const before = valueAt(l, it.prop);
      l.keys[it.prop] = l.keys[it.prop].filter(k => Math.abs(k.t - it.t) >= EPS);
      if (!l.keys[it.prop].length) { delete l.keys[it.prop]; setStatic(l, it.prop, before); }
      if (!Object.keys(l.keys).length) delete l.keys;
    }
  });
  setKeySel(ksel.filter(k => !items.some(it => it.id === k.id && it.prop === k.prop && Math.abs(it.t - k.t) < EPS)));
}

// ---------------------------------------------------------------- inline icons (independent of the sprite)
const NS = 'http://www.w3.org/2000/svg';
function svg(inner, cls = '', base = 'ic ') { const s = document.createElementNS(NS, 'svg'); s.setAttribute('class', base + cls); s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('aria-hidden', 'true'); s.innerHTML = inner; return s; }
const STOPWATCH = '<path d="M10 2h4"/><path d="m12 14 3-3"/><circle cx="12" cy="14" r="8"/>';
const DIAMOND = '<path d="M12 3 21 12 12 21 3 12Z"/>';

/** Stopwatch + ‹ ◆ › controls of one property. Returns {sw, nav, refresh(host?)}; the caller calls refresh() on every update. */
export function controls(spec) {
  const sw = el('button', 'kf-sw'); sw.type = 'button'; sw.append(svg(STOPWATCH));
  const nav = el('span', 'kf-nav');
  const prev = el('button', 'kf-prev'), dia = el('button', 'kf-dia'), next = el('button', 'kf-next');
  for (const b of [prev, dia, next]) b.type = 'button';
  prev.append(icon('chevron-left', 's')); dia.append(svg(DIAMOND, 'kf-diamond')); next.append(icon('chevron-right', 's'));
  nav.append(prev, dia, next);
  sw.addEventListener('click', e => { e.preventDefault(); e.stopPropagation(); toggleStopwatch(spec); });
  dia.addEventListener('click', e => { e.preventDefault(); e.stopPropagation(); toggleKeyHere(spec); });
  prev.addEventListener('click', e => { e.preventDefault(); e.stopPropagation(); hopKey(spec, -1); });
  next.addEventListener('click', e => { e.preventDefault(); e.stopPropagation(); hopKey(spec, 1); });
  const state = { sw, nav, prev, dia, next };
  state.refresh = host => {
    const l = layer(spec.id); if (!l) return;
    const p = propName(l, spec, false), ks = p ? keysOf(l, p) : [], animated = ks.length > 0;
    const tl = round3(S.t - l.start), at = animated && ks.some(k => Math.abs(k.t - tl) < EPS);
    sw.classList.toggle('on', animated); sw.setAttribute('aria-pressed', String(animated));
    sw.title = t(animated ? 'kf.stopwatchOff' : 'kf.stopwatch'); sw.setAttribute('aria-label', sw.title);
    nav.classList.toggle('off', !animated);
    dia.classList.toggle('on', at); dia.title = t('kf.here'); dia.setAttribute('aria-label', dia.title);
    prev.disabled = !ks.some(k => k.t < tl - EPS); next.disabled = !ks.some(k => k.t > tl + EPS);
    prev.title = t('kf.prev'); next.title = t('kf.next'); prev.setAttribute('aria-label', prev.title); next.setAttribute('aria-label', next.title);
    if (host) { host.classList.toggle('kf-on', animated); host.classList.toggle('kf-at', at); }
  };
  return state;
}
/** Shape class of a key by its easing: linear ◆, eased round, hold ■. */
export const keyShape = k => (k.hold === true || k.ease === 'hold' ? 'hold' : k.ease === undefined || k.ease === 'linear' ? 'lin' : 'ease');

// ---------------------------------------------------------------- key selection
let ksel = [];        // [{id, prop, t}]
export const keySel = () => ksel;
export function setKeySel(list) { ksel = list; document.dispatchEvent(new Event('kf-selection')); }
export const isKeySel = (id, prop, tt) => ksel.some(k => k.id === id && k.prop === prop && Math.abs(k.t - tt) < EPS);
/** Drop selected keys that no longer exist (undo, external edits, deleted layers). */
export function pruneKeySel() {
  const keep = ksel.filter(k => { const l = layer(k.id); return l && keysOf(l, k.prop).some(x => Math.abs(x.t - k.t) < EPS); });
  if (keep.length !== ksel.length) setKeySel(keep);
}
const findKey = (l, prop, tt) => keysOf(l, prop).find(k => Math.abs(k.t - tt) < EPS);

// ---------------------------------------------------------------- clipboard / keyboard
let clip = null, lastCopy = '';
function copyKeys() {
  const items = ksel.map(k => { const l = layer(k.id), key = l && findKey(l, k.prop, k.t); return key ? { prop: k.prop, t: k.t, key: JSON.parse(JSON.stringify(key)) } : null; }).filter(Boolean);
  if (!items.length) return false;
  const t0 = Math.min(...items.map(i => i.t));
  clip = items.map(i => ({ prop: i.prop, dt: round3(i.t - t0), key: i.key }));
  return true;
}
function pasteKeys() {
  const tid = (ksel[0] && ksel[0].id) || S.selection[0]; const l0 = layer(tid);
  if (!l0 || !clip || l0.locked) return false;
  const base = round3(S.t - l0.start), fresh = [];
  change(P => {
    const l = P.layers.find(x => x.id === tid);
    for (const c of clip) {
      const fx = isFx(c.prop) ? fxParts(c.prop) : null;
      if (fx && !(l.effects || []).some(e => e.id === fx.id)) continue;   // effect param of another layer's effect: nothing to paste onto
      const tt = Math.max(0, round3(base + c.dt));
      const k = upsertKey(l, c.prop, tt, c.key.v);
      delete k.ease; delete k.hold; if (c.key.ease !== undefined) k.ease = c.key.ease; if (c.key.hold) k.hold = true;
      fresh.push({ id: tid, prop: c.prop, t: tt });
    }
  });
  setKeySel(fresh);
  return true;
}
const typing = e => { const x = e.target; return x && (x.tagName === 'INPUT' || x.tagName === 'TEXTAREA' || x.tagName === 'SELECT' || x.isContentEditable); };
let bound = false;
/** Idempotent: capture-phase shortcuts that only act while keys are selected, so the editor's layer shortcuts keep working otherwise. */
export function initKeyboard() {
  if (bound) return; bound = true;
  const stop = e => { e.preventDefault(); e.stopImmediatePropagation(); };
  window.addEventListener('keydown', e => {
    if (pop) { if (e.key === 'Escape') { stop(e); closePop(); } return; }
    if (typing(e.target)) return;
    const cmd = isMod(e), k = e.key.toLowerCase();
    if (cmd && k === 'c') { if (ksel.length && copyKeys()) { lastCopy = 'keys'; stop(e); } else lastCopy = 'other'; return; }
    if (cmd && k === 'x') { if (ksel.length && copyKeys()) { lastCopy = 'keys'; stop(e); removeKeys(ksel.slice()); } return; }
    if (cmd && k === 'v') { if (lastCopy === 'keys' && clip && ksel.length && pasteKeys()) stop(e); return; }
    if (cmd || e.altKey) return;
    if ((k === 'delete' || k === 'backspace') && ksel.length) { stop(e); removeKeys(ksel.slice()); }
    else if (k === 'e' && ksel.length) { stop(e); const d = document.querySelector('.kf.sel'); const r = d ? d.getBoundingClientRect() : { left: 200, bottom: 200 }; openEasePicker(r.left, r.bottom + 6); }
    else if (k === 'escape' && ksel.length) { stop(e); setKeySel([]); }
  }, true);
  // a click anywhere else ends the key selection (the ruler / transport keep it: scrubbing to the paste position must not lose it)
  window.addEventListener('pointerdown', e => {
    if (!ksel.length) return;
    if (e.target.closest && e.target.closest('.kf, .lane, .kf-pop, .kf-nav, .kf-sw, .lp-ruler, #transport, .lp-bar')) return;
    setKeySel([]);
  }, true);
}

// ---------------------------------------------------------------- easing popover
const HOLD = 'hold';
const EASES = [['linear', 'linear'], ['easeIn', 'ease-in'], ['easeOut', 'ease-out'], ['easeInOut', 'ease-in-out'], ['back', 'out-back'], ['elastic', 'out-elastic'], ['bounce', 'out-bounce'], ['hold', HOLD], ['steps', 'steps(5)']];
export const specOfKey = k => (k.hold === true ? HOLD : k.ease === undefined ? 'linear' : k.ease);
function curvePath(spec, w, h, pad = 3, n = 48) {
  const ys = []; for (let i = 0; i <= n; i++) ys.push(A.ease(spec, i / n));
  const lo = Math.min(0, ...ys), hi = Math.max(1, ...ys), span = hi - lo || 1;
  const step = spec === HOLD || (typeof spec === 'string' && spec.startsWith('steps'));
  let d = '';
  for (let i = 0; i <= n; i++) { const x = pad + (w - 2 * pad) * i / n, y = pad + (h - 2 * pad) * (1 - (ys[i] - lo) / span); d += (i ? (step ? 'L' : 'L') : 'M') + x.toFixed(1) + ' ' + y.toFixed(1); }
  return d;
}
let pop = null, popTimer = 0;
function closePop() { if (pop) { pop.remove(); pop = null; cancelAnimationFrame(popTimer); document.removeEventListener('pointerdown', popOutside, true); } }
const popOutside = e => { if (pop && !pop.contains(e.target) && !(e.target.closest && e.target.closest('.kf'))) closePop(); };
function placePop(node, x, y) {
  document.body.append(node);
  const r = node.getBoundingClientRect();
  node.style.left = clamp(x, 8, window.innerWidth - r.width - 8) + 'px';
  node.style.top = clamp(y, 8, window.innerHeight - r.height - 8) + 'px';
  setTimeout(() => document.addEventListener('pointerdown', popOutside, true), 0);
}
let easeCount = 0;
function applyEase(items, spec, key) {
  change(P => {
    for (const it of items) {
      const l = P.layers.find(x => x.id === it.id); if (!l || l.locked) continue;
      const k = findKey(l, it.prop, it.t); if (!k) continue;
      if (spec === HOLD) { k.hold = true; delete k.ease; } else { delete k.hold; if (spec === 'linear') delete k.ease; else k.ease = spec; }
    }
  }, { key });
}
/** Easing popover for the selected keys, near screen point (x, y). */
export function openEasePicker(x, y) {
  const items = ksel.slice(); if (!items.length) return;
  closePop();
  const first = layer(items[0].id), fk = first && findKey(first, items[0].prop, items[0].t);
  let cur = fk ? specOfKey(fk) : 'linear';
  const box = el('div', 'kf-pop kf-ease-pop'); pop = box;
  box.append(el('div', 'kf-pop-title', t('kf.easing') + ' · ' + t('kf.keysN', { n: items.length })));
  const grid = el('div', 'kf-ease-grid'), tiles = [];
  const showCur = () => { for (const tl of tiles) tl.classList.toggle('on', JSON.stringify(tl._spec) === JSON.stringify(cur)); };
  const seedBez = spec => { const p = A.parseEase(spec); if (p && p[0] === 'bezier') { bez.v = p.slice(1); drawBez(); } };
  for (const [name, spec] of EASES) {
    const b = el('button', 'kf-ease-tile'); b.type = 'button'; b._spec = spec; b.title = t('ease.' + name);
    const s = svg('<path class="grid" d="M3 21H21M3 3V21"/>');
    const path = document.createElementNS(NS, 'path'); path.setAttribute('d', curvePath(spec, 24, 24)); path.setAttribute('class', 'curve'); s.append(path);
    b.append(s, el('span', '', t('ease.' + name)));
    b.addEventListener('click', () => { cur = spec; applyEase(items, spec, 'ease' + (++easeCount)); seedBez(spec); showCur(); });
    grid.append(b); tiles.push(b);
  }
  box.append(grid);
  // cubic-bezier editor
  const bez = { v: [0.25, 0.1, 0.25, 1] };
  { const p = A.parseEase(cur); if (p && p[0] === 'bezier') bez.v = p.slice(1); }
  const X0 = 14, PW = 132, Y1 = 40, PH = 132, VH = 208, VW = 160;
  const ed = svg('', 'kf-bez', ''); ed.setAttribute('viewBox', `0 0 ${VW} ${VH}`);
  ed.innerHTML = `<rect class="frame" x="${X0}" y="${Y1}" width="${PW}" height="${PH}" rx="3"/><path class="diag" d="M${X0} ${Y1 + PH}L${X0 + PW} ${Y1}"/><path class="curve" id="kf-bez-curve"/><path class="arm a1"/><path class="arm a2"/><circle class="hd h1" r="6"/><circle class="hd h2" r="6"/>`;
  const px = u => X0 + PW * u, py = v => Y1 + PH * (1 - v);
  const curve = ed.querySelector('.curve'), a1 = ed.querySelector('.a1'), a2 = ed.querySelector('.a2'), h1 = ed.querySelector('.h1'), h2 = ed.querySelector('.h2');
  const readout = el('div', 'kf-bez-read');
  function drawBez() {
    const [x1, y1, x2, y2] = bez.v;
    let d = ''; for (let i = 0; i <= 60; i++) { const u = i / 60; d += (i ? 'L' : 'M') + px(u).toFixed(1) + ' ' + py(A.bezier(x1, y1, x2, y2, u)).toFixed(1); }
    curve.setAttribute('d', d);
    a1.setAttribute('d', `M${px(0)} ${py(0)}L${px(x1)} ${py(y1)}`); a2.setAttribute('d', `M${px(1)} ${py(1)}L${px(x2)} ${py(y2)}`);
    h1.setAttribute('cx', px(x1)); h1.setAttribute('cy', py(y1)); h2.setAttribute('cx', px(x2)); h2.setAttribute('cy', py(y2));
    readout.textContent = `cubic-bezier(${bez.v.map(v => round(v, 2)).join(', ')})`;
  }
  const drag = (h, idx) => h.addEventListener('pointerdown', e => {
    e.preventDefault(); h.setPointerCapture(e.pointerId);
    const key = 'bez' + (++easeCount);
    const move = ev => {
      const r = ed.getBoundingClientRect(), sc = VW / r.width;
      const u = clamp(((ev.clientX - r.left) * sc - X0) / PW, 0, 1), v = clamp(1 - ((ev.clientY - r.top) * sc - Y1) / PH, -0.35, 1.35);
      bez.v = bez.v.slice(); bez.v[idx] = round(u, 3); bez.v[idx + 1] = round(v, 3);
      cur = bez.v.slice(); drawBez(); applyEase(items, cur, key); showCur();
    };
    const up = () => { h.removeEventListener('pointermove', move); h.removeEventListener('pointerup', up); };
    h.addEventListener('pointermove', move); h.addEventListener('pointerup', up);
  });
  drag(h1, 0); drag(h2, 2);
  const right = el('div', 'kf-bez-side');
  const track = el('div', 'kf-track'), dot = el('div', 'kf-dot'); track.append(dot);
  right.append(readout, el('div', 'kf-pop-sub', t('ease.custom')), track);
  const row = el('div', 'kf-bez-row'); row.append(ed, right); box.append(row);
  drawBez(); showCur();
  const t0 = performance.now();
  const tick = now => {
    if (!pop) return;
    const u = ((now - t0) % 1600) / 1600, e = A.ease(cur === HOLD ? 'hold' : cur, u < 0.85 ? u / 0.85 : 1);
    dot.style.left = `calc(${(clamp(e, -0.3, 1.3) * 100).toFixed(1)}% - 5px)`;
    popTimer = requestAnimationFrame(tick);
  };
  popTimer = requestAnimationFrame(tick);
  placePop(box, x, y);
}
export const easePickerOpen = () => !!pop;

// ---------------------------------------------------------------- "Animate" presets menu
let presetList = null;
async function loadPresets() {
  if (presetList) return presetList;
  try { const r = await fetch('/api/presets'); presetList = r.ok ? await r.json() : []; } catch (e) { presetList = []; }
  return presetList;
}
const GROUPS = ['in', 'out', 'emphasis', 'camera'];
export const presetName = p => { const k = 'preset.' + p.id; const s = t(k); return s !== k ? s : (getLang() === 'tr' && p.name_tr) || p.name; };
async function applyPreset(layerId, p, msg) {
  const l = layer(layerId); if (!l) return false;
  let at = 'in';
  if (p.default_at === 'out') at = 'out';
  else if (p.default_at === 'playhead') at = round3(clamp(S.t, l.start, Math.max(l.start, l.end - 0.05)));
  await saveAndWait();
  try {
    const r = await fetch('/api/presets/apply', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ layer_id: layerId, preset: p.id, at }) });
    const data = await r.json().catch(() => null);
    if (!r.ok || !data || !data.project) { msg.textContent = t('animate.failed', { reason: (data && data.error) || r.status }); return false; }
    loadExternal(data.project, data.version, 'external');
    return true;
  } catch (e) { msg.textContent = t('animate.failed', { reason: String(e.message || e) }); return false; }
}
/** "Animate ▾" menu below `anchor`: In / Out / Emphasis / Camera presets baked into keys by the server. */
export async function openAnimateMenu(anchor, layerId) {
  closePop();
  const box = el('div', 'kf-pop kf-menu'); pop = box;
  const msg = el('div', 'kf-menu-msg');
  const list = (await loadPresets()).filter(p => { const l = layer(layerId); return l && p.applies_to.includes(l.type); });
  if (pop !== box) return;
  for (const g of GROUPS) {
    const items = list.filter(p => p.group === g); if (!items.length) continue;
    box.append(el('div', 'kf-menu-group', t('animate.' + g)));
    for (const p of items) {
      const b = el('button', 'kf-menu-item', presetName(p)); b.type = 'button'; b.dataset.preset = p.id; b.title = p.doc || '';
      b.addEventListener('click', async () => { if (await applyPreset(layerId, p, msg)) closePop(); });
      box.append(b);
    }
  }
  box.append(msg);
  const r = anchor.getBoundingClientRect();
  placePop(box, r.left, r.bottom + 4);
}

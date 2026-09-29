// preview.js — canvas compositor (same geometry as SPEC §1-§2), media element sync, WebAudio gain,
// playback clock, click-to-select / drag-to-move / corner-scale, snapping guides, film grain and the "real frame" comparison.
import { S, emit, change, select, setTime, layer, isActive, fadeFactor, envelopeDb, zoomFactor, isFrozen, clamp, num, VISUAL, HAS_AUDIO } from './state.js';
import { mediaUrl, mediaInfo, frameUrl, saveAndWait } from './api.js';
import { ensureFont, fontStack, fontsEpoch, onFontLoaded } from './fonts.js';
import { t } from './i18n.js';
import { cv as tok } from './theme.js';
import * as A from './anim.js';
import * as KF from './keyframes.js';

const dom = {};
let ctx, ovl, octx, W = 1920, H = 1080, rs = 1;
// viewer geometry (css px): zoom vz, frame size fw×fh, frame origin (vox, voy) inside the stage, stage size stW×stH
let vz = 1, fitZ = 1, fw = 0, fh = 0, vox = 0, voy = 0, stW = 0, stH = 0, dpr = 1;
export const ZOOMS = [0.1, 0.25, 0.5, 0.75, 1, 1.5, 2, 4];
let dirty = true;
export const requestRedraw = () => { dirty = true; };

// ---------------------------------------------------------------- media elements
const entries = new Map(); // layer id -> entry
let audioCtx = null;
export function mediaStatus(id) { const g = entries.get(id); return g ? { failed: g.failed, loading: !g.failed && !g.ready } : { failed: false, loading: false }; }
export function startAudio() {
  try {
    if (!audioCtx) { const AC = window.AudioContext || window.webkitAudioContext; if (!AC) return; audioCtx = new AC(); }
    if (audioCtx.state === 'suspended') audioCtx.resume();
    for (const g of entries.values()) attachAudio(g);
  } catch (e) { console.warn('WebAudio unavailable', e); }
}
function attachAudio(g) {
  if (!audioCtx || g.node || !g.el || g.type === 'image') return;
  try {
    g.node = audioCtx.createMediaElementSource(g.el); g.gain = audioCtx.createGain();
    g.node.connect(g.gain); g.gain.connect(audioCtx.destination); g.el.volume = 1;
  } catch (e) { console.warn('audio node failed', e); }
}
function markFailed(g, v = true) { if (g.failed !== v) { g.failed = v; emit('media'); requestRedraw(); } }

function create(l) {
  const g = { id: l.id, src: l.src, type: l.type, el: null, failed: false, ready: false, info: null, pendingSeek: null, url: '', lastFix: 0 };
  entries.set(l.id, g);
  if (l.type === 'image') {
    const im = new Image(); g.el = im;
    im.onload = () => { g.ready = true; requestRedraw(); emit('media'); };
    im.onerror = () => markFailed(g);
    im.src = mediaUrl(l.src); g.url = im.src;
    mediaInfo(l.src).then(b => { if (b === null && !g.ready) markFailed(g); else if (b) { g.info = b; S.media.set(l.src, b); } });
    return g;
  }
  const el = document.createElement(l.type === 'video' ? 'video' : 'audio');
  el.preload = 'auto'; el.playsInline = true; el.style.display = 'none';
  el.addEventListener('seeked', () => {
    if (g.pendingSeek != null) { const h = g.pendingSeek; g.pendingSeek = null; if (Math.abs(el.currentTime - h) > 0.01) el.currentTime = h; }
    requestRedraw();
  });
  el.addEventListener('loadeddata', () => { g.ready = true; requestRedraw(); emit('media'); });
  el.addEventListener('loadedmetadata', () => { sync(); requestRedraw(); });
  el.addEventListener('error', () => markFailed(g));
  dom.mediaHost.appendChild(el); g.el = el;
  attachAudio(g);
  loadInfo(g, l.src);
  return g;
}
async function loadInfo(g, src) {
  const info = await mediaInfo(src);
  if (g.src !== src) return;
  g.info = info;
  if (!info) { markFailed(g); return; }
  S.media.set(src, info); emit('media');
  const url = info.proxy && info.proxy_ready ? info.proxy : mediaUrl(src); // proxy = 1280 px wide edit copy
  if (g.url !== url) setSource(g, url);
  markFailed(g, false);
}
function setSource(g, url) {
  const el = g.el; const t0 = g.url ? el.currentTime : 0; const wasPlaying = g.url && !el.paused;
  g.url = url; el.src = url;
  if (t0 > 0) {
    const once = () => { el.removeEventListener('loadedmetadata', once); try { el.currentTime = t0; } catch (e) { /* */ } if (wasPlaying) el.play().catch(() => {}); };
    el.addEventListener('loadedmetadata', once);
  }
}
export function syncMedia() {
  const wanted = new Set();
  for (const l of S.project.layers) {
    if (!(l.type === 'video' || l.type === 'image' || l.type === 'audio') || !l.src) continue;
    wanted.add(l.id);
    const g = entries.get(l.id);
    if (g && g.src !== l.src) remove(l.id);
    if (!entries.get(l.id)) create(l);
  }
  for (const id of [...entries.keys()]) if (!wanted.has(id)) remove(id);
}
function remove(id) {
  const g = entries.get(id); if (!g) return;
  if (g.el && g.type !== 'image') { try { g.el.pause(); g.el.removeAttribute('src'); g.el.load(); g.el.remove(); } catch (e) { /* */ } try { g.node?.disconnect(); g.gain?.disconnect(); } catch (e) { /* */ } }
  entries.delete(id);
}
function pollProxies() {
  for (const g of entries.values()) {
    if (g.type === 'image') continue;
    if (!g.info || (g.info.proxy && !g.info.proxy_ready)) loadInfo(g, g.src);
  }
}

// ---------------------------------------------------------------- sync (elements follow the timeline)
function seekTo(g, target) {
  const el = g.el; if (!el || el.readyState < 1) return;
  if (g.info && g.info.duration) target = clamp(target, 0, Math.max(0, g.info.duration - 0.001));
  if (Math.abs(el.currentTime - target) < 0.008) return;
  if (el.seeking) { g.pendingSeek = target; return; }
  try { el.currentTime = target; } catch (e) { /* */ }
}
const fadeOutFactor = time => { const n = S.project.global.fade_out; return n > 0 ? clamp((S.project.duration - time) / n, 0, 1) : 1; };
function sync() {
  const time = S.t;
  const master = fadeOutFactor(time);
  for (const l of S.project.layers) {
    if (!HAS_AUDIO.has(l.type)) continue;
    const g = entries.get(l.id); if (!g || !g.el || g.failed) continue;
    const el = g.el;
    if (el.playbackRate !== rate) el.playbackRate = rate;
    const active = !l.hidden && isActive(l, time);
    const frozen = l.type === 'video' && isFrozen(l);
    const target = frozen ? l.src_in : l.src_in + (time - l.start);
    if (active) {
      if (S.playing && !S.scrubbing && !frozen) {
        const ended = g.info && g.info.duration && target >= g.info.duration - 0.02;
        if (ended) { if (!el.paused) el.pause(); }
        else if (el.paused) { if (el.readyState >= 1) { seekTo(g, target); el.play().catch(() => {}); } }
        else if (!el.seeking && Math.abs(el.currentTime - target) > 0.15 && performance.now() - g.lastFix > 400) { g.lastFix = performance.now(); el.currentTime = target; }
      } else { if (!el.paused) el.pause(); seekTo(g, target); }
    } else {
      if (!el.paused) el.pause();
      if (l.type === 'video' && !l.hidden) { const lead = l.start - time; if (lead > 0 && lead < 0.8) seekTo(g, l.src_in); } // pre-seek the next clip
    }
    // gain: (volume + envelope) dB × fade ramp × global fade-out
    let gain = 0;
    if (active && !l.muted && !frozen && !(l.type === 'video' && g.info && g.info.has_audio === false)) {
      gain = Math.pow(10, (A.value(l, 'volume_db', time - l.start) + envelopeDb(l, time)) / 20) * fadeFactor(l, time) * master;
    }
    if (g.gain) g.gain.gain.setTargetAtTime(gain, audioCtx.currentTime, 0.008);
    else el.volume = clamp(gain, 0, 1);
  }
}

// ---------------------------------------------------------------- playback clock
let clockT0 = 0, clockStart = 0, lastClockT = -1;
let rate = 1;      // forward playback speed (1, 2, 4 — L key)
let rev = 0;       // reverse speed (1, 2, 4 — J key): the playhead steps backwards, media is not played (browsers cannot play video in reverse)
let revLast = 0;
const setShuttle = () => { S.shuttle = rev ? -rev : S.playing ? (rate > 1 ? rate : 0) : 0; };
export function play() {
  if (S.playing) return;
  rev = 0;
  startAudio();
  if (S.t >= S.project.duration - 0.01) setTime(0);
  S.playing = true; clockT0 = performance.now(); clockStart = S.t;
  setShuttle();
  clearTimeout(frameTimer); requestRedraw();
  emit('playing');
}
export function pause() {
  const wasRev = rev;
  rate = 1; rev = 0; setShuttle();
  if (!S.playing) { if (wasRev) { emit('playing'); requestRealFrame(); } return; }
  S.playing = false;
  for (const g of entries.values()) if (g.type !== 'image' && g.el && !g.el.paused) g.el.pause();
  emit('playing'); sync(); requestRedraw(); requestRealFrame();
}
export const togglePlay = () => (S.playing || rev ? pause() : play());
/** J / L shuttle: L plays forward (again: 2×, 4×), J steps backwards (again: 2×, 4×), either one first slows the opposite direction to a stop-and-turn. */
export function shuttle(dir) {
  if (dir > 0) {
    if (rev) { rev = 0; rate = 1; setShuttle(); emit('playing'); }
    if (!S.playing) { rate = 1; play(); }
    else { rate = Math.min(4, rate * 2); clockStart = S.t; clockT0 = performance.now(); }
  } else {
    if (S.playing) { rate = 1; S.playing = false; for (const g of entries.values()) if (g.type !== 'image' && g.el && !g.el.paused) g.el.pause(); rev = 0; }
    rev = Math.min(4, rev ? rev * 2 : 1); revLast = performance.now();
    if (S.t <= 0) rev = 0; // nothing before the start
  }
  setShuttle(); emit('playing'); requestRedraw();
}

function loop(now) {
  requestAnimationFrame(loop);
  if (!S.project) return;
  if (S.playing) {
    // playhead moved by someone else (drag, click, key) → the clock follows it; while dragging it holds still
    if (S.scrubbing || S.t !== lastClockT) { clockStart = S.t; clockT0 = now; }
    if (!S.scrubbing) {
      const time = clockStart + (now - clockT0) / 1000 * rate;
      if (time >= S.project.duration) { setTime(S.project.duration); pause(); }
      else setTime(time);
    }
    lastClockT = S.t;
    sync(); dirty = true;
  }
  if (rev && !S.playing) {
    const dt = Math.min(0.1, (now - revLast) / 1000); revLast = now;
    if (!S.scrubbing) { const nt = S.t - dt * rev; if (nt <= 0) { setTime(0); pause(); } else setTime(nt); }
    sync(); dirty = true;
  }
  if (dirty) { dirty = false; try { draw(); } catch (e) { console.error('draw error', e); } drawOverlay(); }
}

// ---------------------------------------------------------------- drawing
// Animated values (anim.js, shared maths with render.py): every layer read goes through av(l, prop, time).
const av = (l, prop, time = S.t) => A.value(l, prop, time - l.start, prop === 'x' ? W / 2 : prop === 'y' ? H / 2 : undefined);
/** Transform of a layer at `time`: anchor position, signed total scale (scale × scale_x/y × zoom), rotation (deg), anchor fraction. */
function layerTf(l, time, z = 1) {
  const s = av(l, 'scale', time) * z;
  return { x: av(l, 'x', time), y: av(l, 'y', time), sx: s * av(l, 'scale_x', time), sy: s * av(l, 'scale_y', time), rot: av(l, 'rotation', time), ax: av(l, 'anchor_x', time), ay: av(l, 'anchor_y', time) };
}
/** Canvas transform of SPEC §1.8: translate(pos) · rotate · scale · translate(−anchor); content is then drawn centred on the origin at scale 1. */
function applyTf(T, w1, h1) {
  ctx.translate(T.x, T.y); ctx.rotate(T.rot * Math.PI / 180); ctx.scale(T.sx, T.sy); ctx.translate(-(T.ax - 0.5) * w1, -(T.ay - 0.5) * h1);
}
function cssFilter(l, time = S.t) {
  const p = [];
  for (let e of l.effects || []) {
    if (e.enabled === false) continue;
    e = A.effectParams(l, e, time - l.start);
    if (e.type === 'grayscale') p.push(`grayscale(${clamp(num(e.amount, 1), 0, 1)})`);
    else if (e.type === 'brightness') p.push(`brightness(${Math.max(0, 1 + num(e.value, 0))})`);
    else if (e.type === 'contrast') p.push(`contrast(${Math.max(0, 1 + num(e.value, 0))})`);
    else if (e.type === 'saturation') p.push(`saturate(${Math.max(0, 1 + num(e.value, 0))})`);
    else if (e.type === 'blur') { const r = num(e.radius, 0) * rs; if (r > 0) p.push(`blur(${r}px)`); }
  }
  return p.length ? p.join(' ') : 'none';
}

// text layout: block width = widest line; line i glyph top = block_top + i·lh + (lh − size)/2 (SPEC §1.5)
const measureCanvas = document.createElement('canvas'); const mctx = measureCanvas.getContext('2d');
const layoutCache = new Map();
function textLayout(l) {
  const fs = Math.max(1, l.size * A.rasterScale(l)); // rasterised at the largest scale the layer reaches; animation only shrinks the bitmap
  ensureFont(l.font);
  const key = [l.text, l.font, fs, l.letter_spacing, l.line_height, fontsEpoch()].join('|');
  let y = layoutCache.get(key);
  if (y) return y;
  const fontStr = `${fs}px ${fontStack(l.font)}`;
  mctx.font = fontStr; mctx.letterSpacing = '0px';
  const ls = fs * l.letter_spacing, lh = fs * l.line_height;
  const lines = String(l.text).split('\n').map(s => {
    const n = Array.from(s).length;
    return { s, w: n ? mctx.measureText(s).width + (n - 1) * ls : 0 };
  });
  const m = mctx.measureText('Hg');
  const ascent = m.fontBoundingBoxAscent ?? fs * 0.9;
  y = { fs, ls, lh, fontStr, lines, blockW: Math.max(0, ...lines.map(s => s.w)), blockH: lines.length * lh, ascent };
  if (layoutCache.size > 200) layoutCache.clear();
  layoutCache.set(key, y);
  return y;
}
const bitmapCache = new Map();
function textBitmap(l) {
  const y = textLayout(l);
  const sh = l.shadow || {};
  const sigma = sh.enabled ? Math.max(0, sh.blur) : 0;
  const key = JSON.stringify([l.text, l.font, y.fs, l.color, l.letter_spacing, l.line_height, l.align, sh.enabled, sh.blur, sh.opacity, rs, fontsEpoch()]);
  let b = bitmapCache.get(key);
  if (b) return b;
  const pad = Math.ceil(sigma * 3 * rs) + Math.ceil(y.fs * 0.25 * rs) + 4;
  const cw = Math.max(2, Math.ceil(y.blockW * rs + 2 * pad)), ch = Math.max(2, Math.ceil(y.blockH * rs + 2 * pad));
  const c = document.createElement('canvas'); c.width = Math.min(cw, 16000); c.height = Math.min(ch, 16000);
  const x = c.getContext('2d');
  x.scale(rs, rs);
  const ox = (cw / rs - y.blockW) / 2, oy = (ch / rs - y.blockH) / 2;
  x.font = y.fontStr; x.letterSpacing = y.ls + 'px'; x.textBaseline = 'alphabetic'; x.textAlign = 'left';
  const drawLines = color => {
    x.fillStyle = color;
    y.lines.forEach((s, i) => {
      const sx = l.align === 'left' ? 0 : l.align === 'right' ? y.blockW - s.w : (y.blockW - s.w) / 2;
      const top = i * y.lh + (y.lh - y.fs) / 2;
      x.fillText(s.s, ox + sx, oy + top + y.ascent);
    });
  };
  if (sigma > 0 && sh.opacity > 0) { x.save(); x.filter = `blur(${sigma * rs}px)`; x.globalAlpha = sh.opacity; drawLines('#000'); x.restore(); }
  else if (sh.enabled && sh.opacity > 0) { x.save(); x.globalAlpha = sh.opacity; drawLines('#000'); x.restore(); }
  drawLines(l.color);
  b = { c, w: c.width / rs, h: c.height / rs };
  if (bitmapCache.size > 40) bitmapCache.delete(bitmapCache.keys().next().value);
  bitmapCache.set(key, b);
  return b;
}

/** Drawn geometry of a layer at the playhead: unscaled box (w1 × h1), transform T (position, signed totals sx/sy, rotation, anchor fraction), s = scale × zoom.
 *  Local space = box centre origin, unscaled px; project = pos + R(rot) · diag(sx, sy) · (q − anchor). Drawing, hit-testing and the handles share this. */
function geom(l) {
  if (l.type === 'color') return null;
  let w1, h1, dim = null, z = 1;
  if (l.type === 'text') { const y = textLayout(l), R = A.rasterScale(l); w1 = y.blockW / R; h1 = y.blockH / R; }
  else {
    z = l.type === 'video' ? zoomFactor(l, S.t) : 1;
    dim = sourceGeometry(l, entries.get(l.id));
    if (dim) { w1 = dim.dw; h1 = dim.dh; } else { w1 = W; h1 = H; }
  }
  const T = layerTf(l, S.t, z);
  return { w1, h1, T, dim, z, s: av(l, 'scale', S.t) * z, a: { x: (T.ax - 0.5) * w1, y: (T.ay - 0.5) * h1 } };
}
const rad = d => d * Math.PI / 180;
/** Box-local point (relative to the box centre, unscaled) -> project px. */
function toProjectPt(g, qx, qy) {
  const c = Math.cos(rad(g.T.rot)), n = Math.sin(rad(g.T.rot)), ux = g.T.sx * (qx - g.a.x), uy = g.T.sy * (qy - g.a.y);
  return { x: g.T.x + c * ux - n * uy, y: g.T.y + n * ux + c * uy };
}
/** Project px -> box-local point. */
function toLocalPt(g, px, py) {
  const c = Math.cos(rad(g.T.rot)), n = Math.sin(rad(g.T.rot)), dx = px - g.T.x, dy = py - g.T.y;
  return { x: (c * dx + n * dy) / (g.T.sx || 1e-9) + g.a.x, y: (-n * dx + c * dy) / (g.T.sy || 1e-9) + g.a.y };
}
/** Layer box (centre + size) in project pixels at the playhead. cx/cy/w/h are the axis-aligned extents ignoring rotation (snapping uses them);
 *  w1/h1/T/quad describe the real, rotated box. */
export function layerBox(l) {
  if (l.type === 'color') return { cx: W / 2, cy: H / 2, w: W, h: H };
  const g = geom(l), c = toProjectPt(g, 0, 0);
  const w = g.w1 * Math.abs(g.T.sx), h = g.h1 * Math.abs(g.T.sy);
  const out = { cx: c.x, cy: c.y, w: l.type === 'text' ? Math.max(4, w) : w, h: l.type === 'text' ? Math.max(4, h) : h, w1: g.w1, h1: g.h1, T: g.T };
  if (g.dim) out.dim = g.dim;
  return out;
}
// ---- selection handles (project px): 4 corners, 4 side midpoints, rotate handle above the top edge, anchor crosshair
const HANDLES = { nw: [-1, -1], n: [0, -1], ne: [1, -1], e: [1, 0], se: [1, 1], s: [0, 1], sw: [-1, 1], w: [-1, 0] };
function handleSet(l) {
  const g = geom(l); if (!g) return null;
  const pts = {};
  for (const [k, [hx, hy]] of Object.entries(HANDLES)) pts[k] = toProjectPt(g, hx * g.w1 / 2, hy * g.h1 / 2);
  const c = toProjectPt(g, 0, 0), tm = pts.n; let dx = tm.x - c.x, dy = tm.y - c.y; const len = Math.hypot(dx, dy) || 1; dx /= len; dy /= len;
  const off = 26 / vz;
  return { g, pts, centre: c, rot: { x: tm.x + dx * off, y: tm.y + dy * off }, anchor: { x: g.T.x, y: g.T.y } };
}
const cursorFor = (from, to) => { // resize cursor along the screen direction from the box centre to the handle, nearest 45°
  const ang = Math.atan2(to.y - from.y, to.x - from.x) * 180 / Math.PI, b = ((Math.round(ang / 45) % 8) + 8) % 8;
  return ['ew-resize', 'nwse-resize', 'ns-resize', 'nesw-resize', 'ew-resize', 'nwse-resize', 'ns-resize', 'nesw-resize'][b];
};
// source rectangle (element pixels) and drawn size (before scale/zoom) per fit mode (SPEC §1.3)
function sourceGeometry(l, g) {
  if (!g || !g.el) return null;
  const vw = l.type === 'video' ? g.el.videoWidth : g.el.naturalWidth, vh = l.type === 'video' ? g.el.videoHeight : g.el.naturalHeight;
  if (!vw || !vh) return null;
  const c = l.crop || {};
  const cl = clamp(num(c.left, 0), 0, 0.5), cr = clamp(num(c.right, 0), 0, 0.5), ct = clamp(num(c.top, 0), 0, 0.5), cb = clamp(num(c.bottom, 0), 0, 0.5);
  const sx = vw * cl, sy = vh * ct, sw = vw * (1 - cl - cr), sh = vh * (1 - ct - cb);
  const ratio = l.type === 'video' && g.info && g.info.width && vw ? g.info.width / vw : 1; // proxy → real source pixels
  const rw = sw * ratio, rh = sh * ratio;
  let k;
  if (l.fit === 'native') k = 1;
  else if (l.fit === 'contain') k = Math.min(W / rw, H / rh);
  else k = Math.max(W / rw, H / rh);
  return { sx, sy, sw, sh, dw: rw * k, dh: rh * k };
}

const vignetteCache = { g: -1, w: 0, h: 0, c: null };
function vignetteMask(gv) {
  if (vignetteCache.g === gv && vignetteCache.w === W && vignetteCache.h === H) return vignetteCache.c;
  const mw = 320, mh = Math.max(2, Math.round(320 * H / W));
  const c = document.createElement('canvas'); c.width = mw; c.height = mh;
  const x = c.getContext('2d'), im = x.createImageData(mw, mh), d = im.data;
  const rk = Math.hypot(W / 2, H / 2);
  for (let j = 0; j < mh; j++) for (let i = 0; i < mw; i++) {
    const px = ((i + 0.5) / mw - 0.5) * W, py = ((j + 0.5) / mh - 0.5) * H;
    const r = Math.hypot(px, py) / rk; const q = clamp((r - 0.4) / 0.6, 0, 1);
    const v = Math.round(255 * (1 - gv * q * q)); const o = (j * mw + i) * 4;
    d[o] = d[o + 1] = d[o + 2] = v; d[o + 3] = 255;
  }
  x.putImageData(im, 0, 0);
  Object.assign(vignetteCache, { g: gv, w: W, h: H, c });
  return c;
}

// Film grain preview: a random-offset noise tile blended with 'overlay' (≈ ±grain levels at mid-grey). Render uses ffmpeg noise=alls:allf=t.
const grainTile = { g: -1, pattern: null };
function grainPattern(g) {
  if (grainTile.g === g && grainTile.pattern) return grainTile.pattern;
  const n = 512, c = document.createElement('canvas'); c.width = c.height = n;
  const x = c.getContext('2d'), im = x.createImageData(n, n), d = im.data;
  for (let i = 0; i < d.length; i += 4) { const v = clamp(Math.round(128 + (Math.random() * 2 - 1) * g), 0, 255); d[i] = d[i + 1] = d[i + 2] = v; d[i + 3] = 255; }
  x.putImageData(im, 0, 0);
  grainTile.g = g; grainTile.pattern = ctx.createPattern(c, 'repeat');
  return grainTile.pattern;
}

function draw(P = S.project) {
  const time = S.t;
  ctx.setTransform(rs, 0, 0, rs, 0, 0);
  ctx.globalAlpha = 1; ctx.filter = 'none'; ctx.globalCompositeOperation = 'source-over';
  ctx.fillStyle = P.background; ctx.fillRect(0, 0, W, H);
  ctx.imageSmoothingQuality = 'high';
  for (let i = P.layers.length - 1; i >= 0; i--) { // first layer is front-most → draw last
    const l = P.layers[i];
    if (!VISUAL.has(l.type) || l.hidden || !isActive(l, time)) continue;
    const alpha = av(l, 'opacity', time) * fadeFactor(l, time);
    if (alpha <= 0) continue;
    try { drawLayer(l, alpha, time); } catch (e) { console.warn('layer draw failed', l.id, e); }
  }
  ctx.globalAlpha = 1; ctx.filter = 'none';
  const v = P.global.vignette;
  if (v > 0) { ctx.globalCompositeOperation = 'multiply'; ctx.imageSmoothingEnabled = true; ctx.drawImage(vignetteMask(clamp(v, 0, 1)), 0, 0, W, H); ctx.globalCompositeOperation = 'source-over'; }
  const master = fadeOutFactor(time);
  if (master < 1) { ctx.fillStyle = `rgba(0,0,0,${1 - master})`; ctx.fillRect(0, 0, W, H); }
  const grain = P.global.grain;
  if (grain > 0) {
    const ox = Math.floor(Math.random() * 512), oy = Math.floor(Math.random() * 512);
    ctx.save(); ctx.globalCompositeOperation = 'overlay'; ctx.translate(ox, oy); ctx.fillStyle = grainPattern(grain); ctx.fillRect(-ox, -oy, W, H); ctx.restore();
  }
}
function drawLayer(l, alpha, time) {
  ctx.globalAlpha = alpha; ctx.filter = cssFilter(l, time);
  if (l.type === 'color') { ctx.fillStyle = l.color; ctx.fillRect(0, 0, W, H); return; }
  if (l.type === 'text') {
    const b = textBitmap(l), R = A.rasterScale(l), y = textLayout(l);
    ctx.save(); applyTf(layerTf(l, time), y.blockW / R, y.blockH / R); ctx.scale(1 / R, 1 / R);
    ctx.drawImage(b.c, -b.w / 2, -b.h / 2, b.w, b.h); ctx.restore(); return;
  }
  const g = entries.get(l.id);
  if (!g || g.failed || !g.el) return;
  if (l.type === 'video') { if (g.el.readyState < 2 || !g.el.videoWidth) return; }
  else if (!g.ready) return;
  const d = sourceGeometry(l, g); if (!d) return;
  const z = l.type === 'video' ? zoomFactor(l, time) : 1;
  ctx.save(); applyTf(layerTf(l, time, z), d.dw, d.dh);
  ctx.drawImage(g.el, d.sx, d.sy, d.sw, d.sh, -d.dw / 2, -d.dh / 2, d.dw, d.dh); ctx.restore();
}

// ---------------------------------------------------------------- overlay (selection box, guides, real-frame compare, safe areas)
// The overlay canvas covers the whole stage; everything is drawn in frame coordinates after translate(vox, voy), 1 project px = vz css px.
let guides = { x: null, y: null };
const frame = { img: null, status: 'none', t: -1, split: 0.5 };
function drawOverlay() {
  if (!octx) return;
  octx.setTransform(dpr, 0, 0, dpr, 0, 0);
  octx.clearRect(0, 0, stW, stH);
  octx.save(); octx.translate(vox, voy);
  const real = S.realFrame && !S.playing;
  try { if (real) paintRealFrame(); else paintEditing(); } finally { octx.restore(); }
  if (real) paintRealFrameTags();
}
function paintRealFrame() {
  const bx = fw * frame.split;
  if (frame.img && frame.status === 'ready') { octx.save(); octx.beginPath(); octx.rect(bx, 0, fw - bx, fh); octx.clip(); octx.drawImage(frame.img, 0, 0, fw, fh); octx.restore(); }
  else if (frame.status === 'loading' || frame.status === 'error') { octx.fillStyle = tok('--cv-mask'); octx.fillRect(bx, 0, fw - bx, fh); }
  octx.strokeStyle = tok('--cv-sel'); octx.lineWidth = 2; octx.beginPath(); octx.moveTo(bx, 0); octx.lineTo(bx, fh); octx.stroke();
  const hy = clamp(fh / 2, -voy + 20, stH - voy - 20); // the grip stays on screen when zoomed in
  octx.fillStyle = tok('--cv-sel'); octx.beginPath(); octx.arc(bx, hy, 10, 0, 7); octx.fill();
  octx.strokeStyle = tok('--cv-split-fg'); octx.lineWidth = 1.6; octx.lineCap = 'round'; octx.lineJoin = 'round'; // two arrows pointing left / right (drawn, no glyph)
  octx.beginPath(); octx.moveTo(bx - 5, hy); octx.lineTo(bx + 5, hy); octx.moveTo(bx - 5, hy); octx.lineTo(bx - 2.5, hy - 2.5); octx.moveTo(bx - 5, hy); octx.lineTo(bx - 2.5, hy + 2.5);
  octx.moveTo(bx + 5, hy); octx.lineTo(bx + 2.5, hy - 2.5); octx.moveTo(bx + 5, hy); octx.lineTo(bx + 2.5, hy + 2.5); octx.stroke(); octx.lineCap = 'butt';
}
function paintRealFrameTags() {
  const tag = (s, x, align) => { octx.font = '600 11px ' + tok('--font'); octx.textAlign = align; octx.textBaseline = 'top'; const w = octx.measureText(s).width + 12; const rx = align === 'left' ? x : x - w; octx.fillStyle = tok('--cv-tag-bg'); octx.fillRect(rx, 8, w, 20); octx.fillStyle = tok('--cv-tag-fg'); octx.fillText(s, align === 'left' ? x + 6 : x - 6, 12); };
  const l = Math.max(8, vox + 8), r = Math.min(stW - 8, vox + fw - 8);
  tag(t('preview.previewTag'), l, 'left');
  tag(frame.status === 'loading' ? t('preview.renderLoading') : frame.status === 'error' ? t('preview.renderFailed') : t('preview.renderTag'), r, 'right');
}
/** Safe-area (title 80 %, action 90 %) and social-UI (top 12 %, bottom 20 %) guides of the viewer. */
function paintGuides() {
  const V = S.view;
  if (V.social && H / W >= 1.6) {
    octx.fillStyle = tok('--cv-guide-social-fill'); octx.fillRect(0, 0, fw, fh * 0.12); octx.fillRect(0, fh * 0.8, fw, fh * 0.2);
    octx.strokeStyle = tok('--cv-guide-social'); octx.lineWidth = 1; octx.setLineDash([5, 4]);
    octx.beginPath(); octx.moveTo(0, fh * 0.12 + 0.5); octx.lineTo(fw, fh * 0.12 + 0.5); octx.moveTo(0, fh * 0.8 + 0.5); octx.lineTo(fw, fh * 0.8 + 0.5); octx.stroke(); octx.setLineDash([]);
    octx.font = '600 10px ' + tok('--font'); octx.fillStyle = tok('--cv-guide-social-text'); octx.textAlign = 'center'; octx.textBaseline = 'middle';
    if (fh * 0.12 > 26) { octx.fillText(t('guides.socialTop'), fw / 2, fh * 0.06); octx.fillText(t('guides.socialBottom'), fw / 2, fh * 0.9); }
  }
  if (V.safe) {
    octx.lineWidth = 1; octx.font = '600 10px ' + tok('--font'); octx.textAlign = 'left'; octx.textBaseline = 'top';
    for (const [k, label, col] of [[0.9, t('guides.action'), tok('--cv-guide-action')], [0.8, t('guides.title'), tok('--cv-guide-title')]]) {
      const x = fw * (1 - k) / 2, y = fh * (1 - k) / 2;
      octx.strokeStyle = col; octx.strokeRect(Math.round(x) + 0.5, Math.round(y) + 0.5, Math.round(fw * k), Math.round(fh * k));
      octx.fillStyle = col; if (fh > 260) octx.fillText(label, x + 4, y + 3);
    }
    octx.strokeStyle = tok('--cv-guide-centre'); octx.beginPath(); // centre cross
    octx.moveTo(fw / 2 - 8, fh / 2 + 0.5); octx.lineTo(fw / 2 + 8, fh / 2 + 0.5); octx.moveTo(fw / 2 + 0.5, fh / 2 - 8); octx.lineTo(fw / 2 + 0.5, fh / 2 + 8); octx.stroke();
  }
}
function paintEditing() {
  paintGuides();
  const sx = vz;
  const sel = S.selection.map(layer).filter(l => l && VISUAL.has(l.type) && !l.hidden && isActive(l, S.t) && l.type !== 'color');
  const P = p => [p.x * sx, p.y * sx];
  for (const l of sel) {
    const hs = handleSet(l); if (!hs) continue;
    const { pts } = hs;
    octx.strokeStyle = l.locked ? tok('--cv-sel-locked') : tok('--cv-sel'); octx.lineWidth = 1.5; octx.setLineDash(l.locked ? [4, 3] : []);
    octx.beginPath(); for (const [i, k] of ['nw', 'ne', 'se', 'sw'].entries()) { const [x, y] = P(pts[k]); if (i) octx.lineTo(x, y); else octx.moveTo(x, y); } octx.closePath(); octx.stroke(); octx.setLineDash([]);
    if (sel.length === 1 && !l.locked) {
      octx.fillStyle = tok('--cv-handle'); octx.strokeStyle = tok('--cv-sel'); octx.lineWidth = 1.5;
      const [tx, ty] = P(pts.n), [rx, ry] = P(hs.rot);
      octx.beginPath(); octx.moveTo(tx, ty); octx.lineTo(rx, ry); octx.stroke();
      octx.beginPath(); octx.arc(rx, ry, 5, 0, 7); octx.fill(); octx.stroke();
      for (const k of ['nw', 'ne', 'se', 'sw']) { const [x, y] = P(pts[k]); octx.fillRect(x - 4, y - 4, 8, 8); octx.strokeRect(x - 4, y - 4, 8, 8); }
      for (const k of ['n', 'e', 's', 'w']) { const [x, y] = P(pts[k]); octx.fillRect(x - 3, y - 3, 6, 6); octx.strokeRect(x - 3, y - 3, 6, 6); }
      const [ax, ay] = P(hs.anchor);   // anchor crosshair (Alt-drag moves it)
      octx.lineWidth = 3; octx.strokeStyle = 'rgba(0,0,0,.45)'; octx.beginPath(); octx.moveTo(ax - 7, ay); octx.lineTo(ax + 7, ay); octx.moveTo(ax, ay - 7); octx.lineTo(ax, ay + 7); octx.stroke();
      octx.lineWidth = 1.4; octx.strokeStyle = tok('--cv-handle'); octx.beginPath(); octx.moveTo(ax - 7, ay); octx.lineTo(ax + 7, ay); octx.moveTo(ax, ay - 7); octx.lineTo(ax, ay + 7); octx.stroke();
      octx.beginPath(); octx.arc(ax, ay, 3.2, 0, 7); octx.strokeStyle = tok('--cv-sel'); octx.stroke();
    }
  }
  octx.strokeStyle = tok('--cv-guide'); octx.lineWidth = 1;
  if (guides.x != null) { const x = Math.round(guides.x * sx) + 0.5; octx.beginPath(); octx.moveTo(x, 0); octx.lineTo(x, fh); octx.stroke(); }
  if (guides.y != null) { const y = Math.round(guides.y * sx) + 0.5; octx.beginPath(); octx.moveTo(0, y); octx.lineTo(fw, y); octx.stroke(); }
}

let frameTimer = null;
export function requestRealFrame() {
  clearTimeout(frameTimer);
  if (!S.realFrame || S.playing) { requestRedraw(); return; }
  frame.status = 'loading'; requestRedraw();
  frameTimer = setTimeout(async () => {
    if (!S.realFrame || S.playing) return;
    const time = S.t;
    await saveAndWait(); // the render must see the latest project
    if (!S.realFrame || S.playing || time !== S.t) return;
    const im = new Image();
    im.onload = () => { if (time === S.t && S.realFrame) { frame.img = im; frame.status = 'ready'; frame.t = time; requestRedraw(); } };
    im.onerror = () => { if (time === S.t) { frame.status = 'error'; requestRedraw(); } };
    im.src = frameUrl(time);
  }, 350);
}
export function setRealFrame(on) { S.realFrame = on; frame.status = 'none'; if (on) requestRealFrame(); requestRedraw(); emit('settings'); }

// ---------------------------------------------------------------- interaction
function toProject(e) { const r = ovl.getBoundingClientRect(); const cx = e.clientX - r.left, cy = e.clientY - r.top; return { x: (cx - vox) / vz, y: (cy - voy) / vz, cx, cy }; }
const insideFrame = p => p.x >= 0 && p.x <= W && p.y >= 0 && p.y <= H;
function pick(p) {
  let colorCandidate = null;
  const inside = insideFrame(p);
  for (const l of S.project.layers) {
    if (!VISUAL.has(l.type) || l.hidden || l.locked || !isActive(l, S.t)) continue;
    if (l.type === 'color') { if (inside) colorCandidate = colorCandidate || l; continue; }
    if (!inside && !S.selection.includes(l.id)) continue; // outside the frame only an already selected layer can be grabbed
    const g = geom(l); if (!g || Math.abs(g.T.sx) < 1e-6 || Math.abs(g.T.sy) < 1e-6) continue;
    const q = toLocalPt(g, p.x, p.y);   // hit-test in box space: follows rotation, anchor and stretch
    if (Math.abs(q.x) <= Math.max(g.w1, 4 / Math.abs(g.T.sx)) / 2 && Math.abs(q.y) <= Math.max(g.h1, 4 / Math.abs(g.T.sy)) / 2) return l;
  }
  return colorCandidate;
}
/** Handle under the pointer (stage css px in p.cx/p.cy): {kind: 'anchor'|'rotate'|'scale', name, l, hs}. `alt` enables the anchor crosshair. */
function handleAt(p, alt = false) {
  if (S.selection.length !== 1) return null;
  const l = layer(S.selection[0]); if (!l || l.locked || !VISUAL.has(l.type) || l.type === 'color' || l.hidden || !isActive(l, S.t)) return null;
  const hs = handleSet(l); if (!hs) return null;
  const near = (pt, r) => Math.abs(p.cx - (vox + pt.x * vz)) <= r && Math.abs(p.cy - (voy + pt.y * vz)) <= r;
  if (alt && near(hs.anchor, 10)) return { kind: 'anchor', l, hs };
  if (near(hs.rot, 9)) return { kind: 'rotate', l, hs };
  for (const name of ['nw', 'ne', 'se', 'sw']) if (near(hs.pts[name], 8)) return { kind: 'scale', name, l, hs };
  for (const name of ['n', 'e', 's', 'w']) if (near(hs.pts[name], 7)) return { kind: 'scale', name, l, hs };
  return null;
}
/** Test hook: viewport css px of the handles of a layer (tests/ui_smoke.py). */
export function debugHandles(id) {
  const l = layer(id || S.selection[0]); if (!l) return null;
  const hs = handleSet(l); if (!hs) return null;
  const r = ovl.getBoundingClientRect(), f = pt => [r.left + vox + pt.x * vz, r.top + voy + pt.y * vz];
  const out = { rot: f(hs.rot), anchor: f(hs.anchor), centre: f(hs.centre) };
  for (const [k, pt] of Object.entries(hs.pts)) out[k] = f(pt);
  return out;
}

// ---------------------------------------------------------------- viewer zoom / pan
const view = S.view;
let hover = false, lastPointer = null, handHeld = false, handPanned = false;
export const canPan = () => fw > stW - 24 + 0.5 || fh > stH - 24 + 0.5;
/** Anchor for keyboard zoom: the pointer while it is over the viewer, else the stage centre. */
export const viewAnchor = () => (hover && lastPointer ? lastPointer : { x: stW / 2, y: stH / 2 });
export function viewInfo() { return { zoom: view.zoom, z: vz, fit: fitZ, frameW: fw, frameH: fh, ox: vox, oy: voy }; }
/** Zoom to 'fit' or a factor, keeping the project point under `anchor` (stage css px, default centre) fixed. */
export function setZoom(z, anchor) {
  const a = anchor || { x: stW / 2, y: stH / 2 };
  const ox0 = vox, oy0 = voy, z0 = vz;
  if (z === 'fit') { view.zoom = 'fit'; view.px = view.py = 0; }
  else {
    z = clamp(z, ZOOMS[0], ZOOMS[ZOOMS.length - 1]);
    const pxp = (a.x - ox0) / z0, pyp = (a.y - oy0) / z0;
    view.zoom = z;
    view.px = a.x - pxp * z - (stW - W * z) / 2; view.py = a.y - pyp * z - (stH - H * z) / 2;
  }
  layout(); emit('view');
}
export function zoomStep(dir, anchor) {
  const z = vz;
  const next = dir > 0 ? ZOOMS.find(v => v > z * 1.001) : [...ZOOMS].reverse().find(v => v < z * 0.999);
  if (next) setZoom(next, anchor);
}
export function setGuides(g) { Object.assign(view, g); try { localStorage.setItem('kurgu.guides', JSON.stringify({ safe: view.safe, social: view.social })); } catch (e) { /* ignore */ } requestRedraw(); emit('view'); }
/** Space held over a zoomed viewer = temporary hand tool. Returns true when the key was consumed. */
export function handKey(down) {
  if (down) { if (handHeld) return true; if (!(hover && canPan())) return false; handHeld = true; handPanned = false; ovl.style.cursor = 'grab'; return true; }
  if (!handHeld) return false;
  handHeld = false; ovl.style.cursor = 'default';
  if (!handPanned) togglePlay(); // a tap on Space still means play / pause
  return true;
}

// ---------------------------------------------------------------- handle drags (scale / stretch / rotate / anchor); all write through KF.writeProp:
// static value when the property has no keys, a key at the playhead when it is animated. One undo step per drag (merge key).
const r2 = v => Math.round(v * 100) / 100, r4 = v => Math.round(v * 10000) / 10000;
const handleCursor = h => (h.kind === 'rotate' ? 'grab' : h.kind === 'anchor' ? 'move' : cursorFor(h.hs.centre, h.hs.pts[h.name]));
function startHandleDrag(h, p, e) {
  const { l, hs } = h, g = hs.g, key = h.kind + (++dragCount);
  if (h.kind === 'rotate') return { kind: 'rotate', id: l.id, key, rot0: KF.valueAt(l, 'rotation'), prev: Math.atan2(p.y - g.T.y, p.x - g.T.x), total: 0, pos: { x: g.T.x, y: g.T.y } };
  if (h.kind === 'anchor') return { kind: 'anchor', id: l.id, key };
  const [hx, hy] = HANDLES[h.name];
  return { kind: 'scale', id: l.id, key, name: h.name, hx, hy, g, sc0: KF.valueAt(l, 'scale'), base: { x: KF.valueAt(l, 'x'), y: KF.valueAt(l, 'y') },
    Hp: hs.pts[h.name], alt0: e.altKey };
}
function dragScale(d, p, e) {
  const { g, hx, hy } = d, alt = e.altKey, T = g.T, cos = Math.cos(rad(T.rot)), sin = Math.sin(rad(T.rot));
  const F = alt ? { x: g.a.x, y: g.a.y } : { x: -hx * g.w1 / 2, y: -hy * g.h1 / 2 };   // box-local point that stays put: opposite edge / corner, or the anchor with Alt
  const Fp = alt ? { x: T.x, y: T.y } : toProjectPt(g, F.x, F.y);
  const corner = hx !== 0 && hy !== 0, uniform = corner && !e.shiftKey;
  let sx = T.sx, sy = T.sy;
  const props = {};
  if (uniform) {
    const vx = d.Hp.x - Fp.x, vy = d.Hp.y - Fp.y, len2 = vx * vx + vy * vy; if (len2 < 1e-6 || Math.abs(d.sc0) < 1e-9) return;
    const k = clamp(((p.x - Fp.x) * vx + (p.y - Fp.y) * vy) / len2, 0.01 / Math.abs(d.sc0), 30 / Math.abs(d.sc0));
    const s1 = r4(d.sc0 * k), kk = s1 / d.sc0; props.scale = s1; sx = T.sx * kk; sy = T.sy * kk;
  } else {
    const ux = cos, uy = sin, vx = -sin, vy = cos, dx = p.x - Fp.x, dy = p.y - Fp.y;
    if (hx !== 0) { const den = hx * g.w1 / 2 - F.x; if (Math.abs(den) > 1e-6) { sx = (dx * ux + dy * uy) / den; if (Math.abs(sx) < 1e-3) sx = 1e-3 * (T.sx < 0 ? -1 : 1); } }
    if (hy !== 0) { const den = hy * g.h1 / 2 - F.y; if (Math.abs(den) > 1e-6) { sy = (dx * vx + dy * vy) / den; if (Math.abs(sy) < 1e-3) sy = 1e-3 * (T.sy < 0 ? -1 : 1); } }
    if (!g.s || Math.abs(g.s) < 1e-9) return;
    if (hx !== 0) props.scale_x = r4(sx / g.s);
    if (hy !== 0) props.scale_y = r4(sy / g.s);
    if (e.shiftKey && corner) { props.scale_x = r4(sx / g.s); props.scale_y = r4(sy / g.s); }
    sx = (props.scale_x !== undefined ? props.scale_x * g.s : T.sx); sy = (props.scale_y !== undefined ? props.scale_y * g.s : T.sy);
  }
  if (!alt) { // the fixed point does not move: p' = p + R · ((sx − sx')(F.x − a.x), (sy − sy')(F.y − a.y))
    const ox = (T.sx - sx) * (F.x - g.a.x), oy = (T.sy - sy) * (F.y - g.a.y);
    props.x = r2(d.base.x + cos * ox - sin * oy); props.y = r2(d.base.y + sin * ox + cos * oy);
  }
  KF.writeProps(d.id, props, d.key);
}
function dragRotate(d, p, e) {
  const a = Math.atan2(p.y - d.pos.y, p.x - d.pos.x); let delta = a - d.prev;
  if (delta > Math.PI) delta -= 2 * Math.PI; else if (delta < -Math.PI) delta += 2 * Math.PI;
  d.total += delta; d.prev = a;
  let rot = d.rot0 + d.total * 180 / Math.PI;
  rot = e.shiftKey ? Math.round(rot / 15) * 15 : Math.round(rot * 10) / 10;
  KF.writeProps(d.id, { rotation: rot }, d.key);
}
function dragAnchor(d, p) {
  const l = layer(d.id); if (!l) return;
  const g = geom(l); if (!g || !g.w1 || !g.h1) return;
  const q = toLocalPt(g, p.x, p.y);     // the anchor moves to the pointer; the layer stays where it is, so x / y become the pointer
  let ax = q.x / g.w1 + 0.5, ay = q.y / g.h1 + 0.5;
  const tolX = 6 / vz / g.w1, tolY = 6 / vz / g.h1;
  for (const m of [0, 0.5, 1]) { if (Math.abs(ax - m) < tolX) ax = m; if (Math.abs(ay - m) < tolY) ay = m; }
  const nq = { x: (ax - 0.5) * g.w1, y: (ay - 0.5) * g.h1 };                    // snapped anchor in box space
  const P = toProjectPt(g, nq.x, nq.y);                                          // its project position with the old transform
  KF.writeProps(d.id, { anchor_x: r4(ax), anchor_y: r4(ay), x: r2(P.x), y: r2(P.y) }, d.key);
}

let dragCount = 0;
function bindInteraction() {
  let drag = null;
  ovl.addEventListener('mousedown', e => { if (e.button === 1) e.preventDefault(); }); // no middle-click autoscroll
  ovl.addEventListener('pointerenter', () => { hover = true; });
  ovl.addEventListener('pointerleave', () => { hover = false; });
  ovl.addEventListener('wheel', e => {
    const r = ovl.getBoundingClientRect(), a = { x: e.clientX - r.left, y: e.clientY - r.top };
    if (e.ctrlKey || e.metaKey) { e.preventDefault(); setZoom(clamp(vz * Math.exp(-e.deltaY * (e.metaKey ? 0.004 : 0.012)), ZOOMS[0], ZOOMS[ZOOMS.length - 1]), a); }
    else if (canPan()) { const k = e.deltaMode === 1 ? 16 : 1; e.preventDefault(); view.px -= e.deltaX * k; view.py -= e.deltaY * k; layout(); }
  }, { passive: false });
  ovl.addEventListener('pointerdown', e => {
    S.focus = 'preview'; startAudio();
    if (e.button === 1 || (e.button === 0 && handHeld)) {
      e.preventDefault(); ovl.setPointerCapture(e.pointerId);
      drag = { kind: 'pan', x0: e.clientX, y0: e.clientY, px0: view.px, py0: view.py }; handPanned = true; ovl.style.cursor = 'grabbing'; return;
    }
    if (e.button !== 0) return;
    ovl.setPointerCapture(e.pointerId);
    const p = toProject(e);
    if (S.realFrame && !S.playing) { drag = { kind: 'split' }; frame.split = clamp((p.cx - vox) / fw, 0.02, 0.98); requestRedraw(); return; }
    const h = handleAt(p, e.altKey);
    if (h) { drag = startHandleDrag(h, p, e); return; }
    const l = pick(p);
    if (!l) { if (!e.shiftKey) select([]); drag = null; return; }
    if (e.shiftKey) select([l.id], true);
    else if (!S.selection.includes(l.id)) select([l.id]);
    const ids = S.selection.filter(id => { const x = layer(id); return x && VISUAL.has(x.type) && x.type !== 'color' && !x.locked; });
    if (!ids.includes(l.id)) { drag = null; return; }
    drag = { kind: 'move', ids, start: p, first: new Map(ids.map(id => { const x = layer(id); return [id, { x: KF.valueAt(x, 'x'), y: KF.valueAt(x, 'y') }]; })), primary: l.id, key: 'move' + (++dragCount), moved: false };
  });
  ovl.addEventListener('pointermove', e => {
    const p = toProject(e); lastPointer = { x: p.cx, y: p.cy };
    if (!drag) {
      if (handHeld) ovl.style.cursor = 'grab';
      else if (S.realFrame && !S.playing) ovl.style.cursor = 'ew-resize';
      else { const h = handleAt(p, e.altKey); ovl.style.cursor = h ? handleCursor(h) : pick(p) ? 'move' : 'default'; }
      return;
    }
    if (drag.kind === 'pan') { view.px = drag.px0 + e.clientX - drag.x0; view.py = drag.py0 + e.clientY - drag.y0; layout(); return; }
    if (drag.kind === 'split') { frame.split = clamp((p.cx - vox) / fw, 0.02, 0.98); requestRedraw(); return; }
    if (drag.kind === 'scale') { dragScale(drag, p, e); return; }
    if (drag.kind === 'rotate') { dragRotate(drag, p, e); return; }
    if (drag.kind === 'anchor') { dragAnchor(drag, p); return; }
    if (drag.kind === 'move') {
      let dx = p.x - drag.start.x, dy = p.y - drag.start.y;
      if (!drag.moved && Math.hypot(dx, dy) * vz < 3) return; // ignore tiny jitter on click
      drag.moved = true;
      if (e.shiftKey) { if (Math.abs(dx) > Math.abs(dy)) dy = 0; else dx = 0; }
      guides = { x: null, y: null };
      if (!e.altKey && drag.ids.length >= 1) { // snap centre / edges to the centre line and the 5 % safe area
        const bl = layer(drag.primary), i0 = drag.first.get(drag.primary), b = layerBox(bl);
        const tol = 8 / vz;
        const tryAxis = (centre, half, targets) => { let best = null; for (const f of [centre - half, centre, centre + half]) for (const h of targets) { const diff = h - f; if (Math.abs(diff) < tol && (!best || Math.abs(diff) < Math.abs(best.diff))) best = { diff, h }; } return best; };
        const gx = tryAxis(i0.x + dx, b.w / 2, [W * 0.05, W / 2, W * 0.95]);
        const gy = tryAxis(i0.y + dy, b.h / 2, [H * 0.05, H / 2, H * 0.95]);
        if (gx && !(e.shiftKey && dx === 0)) { dx += gx.diff; guides.x = gx.h; }
        if (gy && !(e.shiftKey && dy === 0)) { dy += gy.diff; guides.y = gy.h; }
      }
      change(P => { for (const [id, i0] of drag.first) { const l = P.layers.find(x => x.id === id); if (l) { KF.writeProp(l, 'x', Math.round(i0.x + dx)); KF.writeProp(l, 'y', Math.round(i0.y + dy)); } } }, { key: drag.key });
    }
  });
  const end = () => { drag = null; guides = { x: null, y: null }; ovl.style.cursor = handHeld ? 'grab' : 'default'; requestRedraw(); };
  ovl.addEventListener('pointerup', end); ovl.addEventListener('pointercancel', end);
  ovl.addEventListener('dblclick', e => {
    if (S.realFrame) return;
    const l = pick(toProject(e));
    if (l && l.type === 'text') document.dispatchEvent(new CustomEvent('focus-text'));
  });
  window.addEventListener('keyup', e => { if (e.key === ' ' && handHeld) handKey(false); });
  window.addEventListener('blur', () => { handHeld = false; });
}

// ---------------------------------------------------------------- sizing / start
/** Recompute the viewer geometry (fit zoom, pan limits, frame position) and resize frame + overlay. */
function layout() {
  const r = dom.stage.getBoundingClientRect();
  stW = Math.max(40, r.width); stH = Math.max(40, r.height); dpr = window.devicePixelRatio || 1;
  fitZ = Math.max(0.01, Math.min((stW - 24) / W, (stH - 24) / H));
  vz = view.zoom === 'fit' ? fitZ : view.zoom;
  fw = W * vz; fh = H * vz;
  const lim = (f, s) => (f <= s - 24 ? 0 : (f - s) / 2 + 48); // pan range beyond centred: none while the frame fits
  view.px = clamp(view.px, -lim(fw, stW), lim(fw, stW)); view.py = clamp(view.py, -lim(fh, stH), lim(fh, stH));
  vox = (stW - fw) / 2 + view.px; voy = (stH - fh) / 2 + view.py;
  Object.assign(dom.frame.style, { left: vox + 'px', top: voy + 'px', width: fw + 'px', height: fh + 'px' });
  const ow = Math.round(stW * dpr), oh = Math.round(stH * dpr);
  if (ovl.width !== ow || ovl.height !== oh) { ovl.width = ow; ovl.height = oh; }
  requestRedraw();
}
function resize() { const z = vz, f = fitZ; layout(); if (z !== vz || f !== fitZ) emit('view'); }
export function applyProjectSize() {
  W = S.project.width; H = S.project.height;
  rs = Math.min(1, Math.sqrt(2.5e6 / (W * H))); // internal canvas resolution, capped at ≈2.5 MP (coordinates stay in project pixels)
  dom.canvas.width = Math.round(W * rs); dom.canvas.height = Math.round(H * rs);
  ctx = dom.canvas.getContext('2d', { alpha: false });
  grainTile.pattern = null;
  view.px = view.py = 0;
  layout(); emit('view');
}
/** Draw project `P` (maybe with another frame size) into a small canvas with the real compositor — used by the Sequence dialog. */
export function renderThumb(P, canvas) {
  const keep = { W, H, rs, ctx };
  try { W = P.width; H = P.height; rs = canvas.width / W; ctx = canvas.getContext('2d', { alpha: false }); draw(P); }
  catch (e) { console.warn('thumb failed', e); }
  finally { ({ W, H, rs, ctx } = keep); }
}
export function startPreview(d) {
  Object.assign(dom, d);
  ovl = d.overlay; octx = ovl.getContext('2d');
  try { const g = JSON.parse(localStorage.getItem('kurgu.guides') || '{}'); view.safe = !!g.safe; view.social = !!g.social; } catch (e) { /* ignore */ }
  applyProjectSize();
  new ResizeObserver(resize).observe(dom.stage);
  bindInteraction();
  syncMedia();
  onFontLoaded(() => requestRedraw());
  const signature = () => S.project.layers.map(l => l.id + l.src + l.type).join();
  let lastSig = signature();
  requestAnimationFrame(loop);
  setInterval(pollProxies, 3000);
  return {
    project() { const s = signature(); if (s !== lastSig) { lastSig = s; syncMedia(); } if (S.project.width !== W || S.project.height !== H) applyProjectSize(); if (!S.playing) { sync(); requestRealFrame(); } requestRedraw(); },
    time() { if (!S.playing) { sync(); requestRealFrame(); } requestRedraw(); },
    selection() { requestRedraw(); },
    lang() { requestRedraw(); },
    theme() { requestRedraw(); },
    fonts() { requestRedraw(); },
  };
}

export function debugInfo() { return { audio: audioCtx && audioCtx.state, entries: [...entries.values()].map(g => ({ id: g.id, failed: g.failed, ready: g.ready, url: g.url, gain: g.gain ? g.gain.gain.value : null, paused: g.el && g.el.paused, ct: g.el && g.el.currentTime })) }; }

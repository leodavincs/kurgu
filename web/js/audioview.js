// audioview.js — the "Audio" pane of the bottom panel: only the sound, big and readable.
//   1. overview strip: whole-project waveform, a box for the visible window (drag it to scroll, click outside = jump there)
//   2. large zoomed waveform with a time ruler, tally-red playhead, yellow note markers (click = seek, hover = text), faint scene ticks
//   3. word strip: caption / text layers split evenly over their time (Kurgu stores no word timings in the project)
// Click = seek · drag = scroll · Ctrl/Cmd + wheel or + / - = zoom · two-finger scroll = pan. View + navigate only (no cutting).
// Peaks come from /api/waveform (100 per second of source, cached by the server and in api.js); the project mix is built once per layer signature.
import { S, setTime, clamp, HAS_AUDIO, sceneName } from './state.js';
import { waveform } from './api.js';
import { t } from './i18n.js';
import { el } from './dom.js';
import { cv } from './theme.js';

const R = 100;                       // peaks per second (the server's waveform rate)
const RULER_H = 22, MIN_SPAN = 1.5;
const CAPTION_RE = /altyaz|caption|subtitle|\bsubs?\b|\bcc\b/i;
const st = { v0: 0, span: 12, active: false, drag: null };
const dom = {};
let sceneDirty = true, wordDirty = true, scenes = [], sceneSig = '', lastSceneKey = '', keyBound = false, mix = null, mixSig = '', words = [], wordSig = '', raf = 0, loading = new Map();
const dpr = () => Math.min(2, window.devicePixelRatio || 1);
const dur = () => Math.max(0.1, S.project.duration);
const db = v => Math.max(0, Math.min(1, (20 * Math.log10(v + 1e-9) + 46) / 46));   // -46 dB .. 0 dB: room tone is a hairline, speech fills the height
const label = tt => { const r = Math.round(tt * 10) / 10, m = Math.floor(r / 60), sec = Math.round((r - m * 60) * 10) / 10; return `${m}:${sec < 10 ? '0' : ''}${sec % 1 ? sec.toFixed(1) : sec}`; };

// ---------------------------------------------------------------- data
function audioLayers() {
  const a = S.project.layers.filter(l => l.type === 'audio' && l.src && !l.muted && !l.hidden);
  return a.length ? a : S.project.layers.filter(l => HAS_AUDIO.has(l.type) && l.src && !l.muted && !l.hidden);
}
/** Project-level peak array (R per second) = the mix of every audible audio layer; null when nothing has loaded yet / no audio. */
function buildMix() {
  const ls = audioLayers();
  const sig = JSON.stringify([ls.map(l => [l.src, l.start, l.end, l.src_in, l.volume_db]), S.project.duration, [...loading.keys()].length]);
  if (sig === mixSig) return mix;
  mixSig = sig;
  const n = Math.ceil(dur() * R) + 1, out = new Float32Array(n); let any = false;
  for (const l of ls) {
    if (!loading.has(l.src)) { loading.set(l.src, null); waveform(l.src).then(w => { loading.set(l.src, w && w.peaks && w.peaks.length ? w : false); mixSig = ''; schedule(true); }); }
    const w = loading.get(l.src); if (!w) continue;
    any = true;
    const g = Math.pow(10, (l.volume_db || 0) / 20), pk = w.peaks, rate = w.rate || R;
    const i0 = Math.max(0, Math.floor(l.start * R)), i1 = Math.min(n, Math.ceil(l.end * R));
    for (let i = i0; i < i1; i++) { const k = Math.round(((l.src_in || 0) + i / R - l.start) * rate); if (k >= 0 && k < pk.length) out[i] += pk[k] * g; }
  }
  mix = any ? out : null;
  return mix;
}
function buildWords() {
  if (!wordDirty) return words;
  wordDirty = false;
  const all = S.project.layers.filter(l => l.type === 'text' && l.text && String(l.text).trim());   // hidden caption layers still hold the words
  const caps = all.filter(l => CAPTION_RE.test(l.group || '') || /^cap_/.test(l.id));
  const src = (caps.length ? caps : all).sort((a, b) => a.start - b.start);
  const sig = JSON.stringify(src.map(l => [l.id, l.start, l.end, l.text]));
  if (sig === wordSig) return words;
  wordSig = sig; words = [];
  for (const l of src) {
    const ws = String(l.text).split(/\s+/).filter(Boolean), d = (l.end - l.start) / ws.length;
    ws.forEach((w, i) => words.push({ w, a: l.start + i * d, b: l.start + (i + 1) * d }));
  }
  return words;
}
/** One row of named blocks: what is on screen over time (video layers of every group merged; the front-most layer wins where they overlap). */
function buildScenes() {
  if (!sceneDirty) return scenes;
  sceneDirty = false;
  const vs = S.project.layers.filter(l => l.type === 'video' && !l.hidden && l.end > l.start);
  const sig = JSON.stringify(vs.map(l => [l.id, l.start, l.end, l.name, l.group]));
  if (sig === sceneSig) return scenes;
  sceneSig = sig; scenes = [];
  const cuts = [...new Set(vs.flatMap(l => [l.start, l.end]))].sort((a, b) => a - b);
  for (let i = 0; i + 1 < cuts.length; i++) {
    const a = cuts[i], b = cuts[i + 1], l = vs.find(q => q.start <= a && q.end >= b); if (!l) continue;
    const last = scenes[scenes.length - 1];
    if (last && last.l === l && Math.abs(last.b - a) < 1e-6) last.b = b; else scenes.push({ l, a, b, name: sceneName(l) });
  }
  return scenes;
}
function drawScenes() {
  const sc = buildScenes();
  dom.scenes.hidden = !sc.length;
  if (!sc.length) return;
  const [x, w, h] = size(dom.scenes); x.clearRect(0, 0, w, h);
  x.font = '500 11.5px ' + cv('--font'); x.textBaseline = 'middle';
  const cur = sc.findIndex(q => S.t >= q.a && S.t < q.b);
  lastSceneKey = st.v0 + '|' + st.span + '|' + cur + '|' + sceneSig.length;
  sc.forEach((q, i) => {
    const a = tx(q.a, w), b = tx(q.b, w); if (b < 0 || a > w) return;
    const bw = Math.max(1, b - a - 1);
    x.fillStyle = i === cur ? cv('--acc-soft') : (i % 2 ? cv('--hover') : cv('--press')); x.fillRect(a, 2, bw, h - 4);
    if (i === cur) { x.strokeStyle = cv('--acc-line'); x.lineWidth = 1; x.strokeRect(a + 0.5, 2.5, bw - 1, h - 5); }
    if (bw > 22) { x.save(); x.beginPath(); x.rect(a + 1, 0, bw - 2, h); x.clip(); x.fillStyle = i === cur ? cv('--t1') : cv('--t2'); x.fillText(q.name, Math.max(a, 0) + 6, h / 2 + 0.5); x.restore(); }
  });
}
const noteAt = x => { const r = geom(); return S.project.notes.find(n => Math.abs(tx(n.t, r.w) - x) <= 6); };

// ---------------------------------------------------------------- geometry
function geom() { return { w: dom.main.clientWidth, h: dom.main.clientHeight }; }
const tx = (tt, w) => (tt - st.v0) / st.span * w;
const xt = (x, w) => st.v0 + x / w * st.span;
function clampView() { st.span = clamp(st.span, Math.min(MIN_SPAN, dur()), dur()); st.v0 = clamp(st.v0, 0, Math.max(0, dur() - st.span)); }
function zoomAt(f, anchorT) {
  const frac = (anchorT - st.v0) / st.span;
  st.span = clamp(st.span * f, Math.min(MIN_SPAN, dur()), dur());
  st.v0 = anchorT - frac * st.span; clampView(); schedule(true);
}
function reveal(center) {
  if (st.drag) return;
  if (S.t < st.v0 || S.t > st.v0 + st.span * 0.94) { st.v0 = center ? S.t - st.span / 2 : S.t - st.span * 0.1; clampView(); schedule(true); }
}

// ---------------------------------------------------------------- drawing
function size(cvs) {
  const d = dpr(), w = cvs.clientWidth, h = cvs.clientHeight;
  if (cvs.width !== Math.round(w * d) || cvs.height !== Math.round(h * d)) { cvs.width = Math.round(w * d); cvs.height = Math.round(h * d); }
  const x = cvs.getContext('2d'); x.setTransform(d, 0, 0, d, 0, 0); return [x, w, h];
}
function drawWave() {
  const [x, w, h] = size(dom.wave); x.clearRect(0, 0, w, h);
  const m = buildMix(), mid = RULER_H + (h - RULER_H) / 2, amp = (h - RULER_H) / 2 - 6;
  // scene boundaries: very faint ticks only
  x.fillStyle = cv('--cv-tick-minor');
  for (const l of S.project.layers) if (l.type === 'video' && !l.hidden) for (const b of [l.start, l.end]) { const px = Math.round(tx(b, w)); if (px >= 0 && px <= w) x.fillRect(px, RULER_H, 1, h - RULER_H); }
  // ruler
  const steps = [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600], pxs = w / st.span;
  const major = steps.find(a => a * pxs >= 70) || 600, minor = major / 5;
  x.fillStyle = cv('--cv-tick-label'); x.font = '500 10.5px ' + cv('--font'); x.textBaseline = 'top';
  for (let tt = Math.floor(st.v0 / minor) * minor; tt <= st.v0 + st.span + minor; tt += minor) {
    const px = Math.round(tx(tt, w)) + 0.5, isMajor = Math.abs(tt / major - Math.round(tt / major)) < 1e-6;
    x.fillStyle = isMajor ? cv('--cv-tick') : cv('--cv-tick-minor'); x.fillRect(px, RULER_H - (isMajor ? 9 : 4), 1, isMajor ? 9 : 4);
    if (isMajor) { x.fillStyle = cv('--cv-tick-label'); x.fillText(label(Math.max(0, tt)), px + 3, 3); }
  }
  x.fillStyle = cv('--cv-tick-minor'); x.fillRect(0, mid, w, 1);
  if (!m) { x.fillStyle = cv('--cv-tick-label'); x.font = '13px ' + cv('--font'); x.textAlign = 'center'; x.fillText(t(audioLayers().length ? 'av.loading' : 'av.noAudio'), w / 2, mid - 8); x.textAlign = 'start'; return; }
  x.fillStyle = cv('--cv-wave-audio');
  const per = st.span * R / w;
  for (let px = 0; px < w; px++) {
    const a = Math.max(0, Math.floor((st.v0 + px / w * st.span) * R)), b = Math.min(m.length, Math.max(a + 1, Math.floor(a + per)));
    if (a >= m.length) break;
    let v = 0; for (let i = a; i < b; i++) if (m[i] > v) v = m[i];
    const y = db(v) * amp;
    x.fillRect(px, mid - y, 1, Math.max(1, y * 2));
  }
}
function drawTop() {   // playhead + notes (cheap, redrawn on every time change)
  const [x, w, h] = size(dom.top); x.clearRect(0, 0, w, h);
  for (const n of S.project.notes) {
    const px = Math.round(tx(n.t, w)); if (px < -8 || px > w + 8) continue;
    x.globalAlpha = n.done ? 0.45 : 1; x.fillStyle = cv('--note');
    x.fillRect(px, RULER_H, 1, h - RULER_H);               // thin line through the wave
    x.beginPath(); x.moveTo(px, RULER_H - 11); x.lineTo(px + 6, RULER_H - 5); x.lineTo(px, RULER_H + 1); x.lineTo(px - 6, RULER_H - 5); x.closePath(); x.fill();   // diamond on the ruler
    x.globalAlpha = 1;
  }
  const px = tx(S.t, w);
  if (px >= -2 && px <= w + 2) { x.fillStyle = cv('--tally'); x.fillRect(Math.round(px) - 1, 0, 2, h); x.beginPath(); x.moveTo(px - 5, 0); x.lineTo(px + 5, 0); x.lineTo(px, 7); x.closePath(); x.fill(); }
}
function drawOver() {
  const [x, w, h] = size(dom.over); x.clearRect(0, 0, w, h);
  const m = buildMix(), mid = h / 2, d = dur();
  if (m) {
    x.fillStyle = cv('--t3'); x.globalAlpha = 0.7;
    for (let px = 0; px < w; px++) {
      const a = Math.floor(px / w * m.length), b = Math.max(a + 1, Math.floor((px + 1) / w * m.length)); let v = 0;
      for (let i = a; i < b && i < m.length; i += 2) if (m[i] > v) v = m[i];
      const y = db(v) * (mid - 3); x.fillRect(px, mid - y, 1, Math.max(1, y * 2));
    }
    x.globalAlpha = 1;
  }
  for (const n of S.project.notes) { x.fillStyle = cv('--note'); x.globalAlpha = n.done ? 0.5 : 1; x.fillRect(Math.round(n.t / d * w) - 1, 0, 2, 7); }
  x.globalAlpha = 1;
  const bx = st.v0 / d * w, bw = Math.max(6, st.span / d * w);
  x.fillStyle = cv('--hover'); x.fillRect(bx, 0, bw, h);
  x.strokeStyle = cv('--t1'); x.lineWidth = 1.5; x.strokeRect(bx + 0.75, 0.75, bw - 1.5, h - 1.5);
  x.fillStyle = cv('--tally'); x.fillRect(Math.round(S.t / d * w) - 1, 0, 2, h);
}
let lastWordKey = '';
function drawWords() {
  const ws = buildWords();
  dom.words.hidden = !ws.length; dom.root.classList.toggle('no-words', !ws.length);
  if (!ws.length) return;
  const [x, w, h] = size(dom.words); x.clearRect(0, 0, w, h);
  x.font = '500 13px ' + cv('--font'); x.textBaseline = 'middle';
  const cur = ws.findIndex(q => S.t >= q.a && S.t < q.b);
  lastWordKey = st.v0 + '|' + st.span + '|' + cur + '|' + wordSig.length;
  ws.forEach((q, i) => {
    const a = tx(q.a, w), b = tx(q.b, w); if (b < 0 || a > w) return;
    const bw = Math.max(1, b - a - 2);
    x.fillStyle = i === cur ? cv('--acc-soft') : cv('--hover'); x.fillRect(a + 1, 4, bw, h - 8);
    if (i === cur) { x.strokeStyle = cv('--acc-line'); x.lineWidth = 1; x.strokeRect(a + 1.5, 4.5, bw - 1, h - 9); }
    if (bw > 14) { x.save(); x.beginPath(); x.rect(a + 1, 0, bw, h); x.clip(); x.fillStyle = i === cur ? cv('--t1') : cv('--t2'); x.fillText(q.w, a + 6, h / 2 + 0.5); x.restore(); }
  });
}
function drawAll() { drawScenes(); drawWave(); drawTop(); drawOver(); drawWords(); }
window.addEventListener('kurgu:media', e => { loading.delete(e.detail.src); mixSig = ''; mix = null; schedule(true); });   // a clip was re-rendered: peaks are stale
function schedule(full) {
  if (!st.active || !dom.root) return;
  if (full) schedule.full = true;
  if (raf) return;
  raf = requestAnimationFrame(() => { raf = 0; if (!st.active) return; if (schedule.full) { schedule.full = false; drawAll(); } else { drawTop(); drawOver(); if (buildScenes().findIndex(q => S.t >= q.a && S.t < q.b) !== +lastSceneKey.split('|')[2]) drawScenes(); const k = st.v0 + '|' + st.span + '|' + words.findIndex(q => S.t >= q.a && S.t < q.b) + '|' + wordSig.length; if (k !== lastWordKey) drawWords(); } });
}

// ---------------------------------------------------------------- interaction
function bind() {
  const seek = tt => setTime(clamp(tt, 0, dur()));
  const main = dom.main;
  main.addEventListener('pointerdown', e => {
    if (e.button !== 0) return;
    main.setPointerCapture(e.pointerId);
    const r = main.getBoundingClientRect(), x0 = e.clientX - r.left;
    st.drag = { x0, v0: st.v0, moved: false };
    const move = ev => {
      const dx = ev.clientX - r.left - x0;
      if (Math.abs(dx) > 4) st.drag.moved = true;
      if (st.drag.moved) { st.v0 = st.drag.v0 - dx / r.width * st.span; clampView(); schedule(true); main.style.cursor = 'grabbing'; }
    };
    const up = ev => {
      main.removeEventListener('pointermove', move); main.removeEventListener('pointerup', up); main.removeEventListener('pointercancel', up);
      const moved = st.drag.moved; st.drag = null; main.style.cursor = '';
      if (!moved) { const x = ev.clientX - r.left, n = noteAt(x); seek(n ? n.t : xt(x, r.width)); }
    };
    main.addEventListener('pointermove', move); main.addEventListener('pointerup', up); main.addEventListener('pointercancel', up);
  });
  main.addEventListener('pointermove', e => {
    if (st.drag) { dom.tip.hidden = true; return; }
    const r = main.getBoundingClientRect(), x = e.clientX - r.left, n = noteAt(x);
    if (n) { dom.tip.textContent = `${label(n.t)} · ${n.text}`; dom.tip.style.left = clamp(x + 10, 0, r.width - 240) + 'px'; dom.tip.hidden = false; main.style.cursor = 'pointer'; }
    else { dom.tip.hidden = true; main.style.cursor = ''; }
  });
  main.addEventListener('pointerleave', () => { dom.tip.hidden = true; });
  const wheel = e => {
    e.preventDefault();
    const r = dom.main.getBoundingClientRect();
    if (e.ctrlKey || e.metaKey) zoomAt(Math.exp(e.deltaY * 0.0025), xt(e.clientX - r.left, r.width));
    else { const d = Math.abs(e.deltaX) > Math.abs(e.deltaY) || e.shiftKey ? (e.deltaX || e.deltaY) : e.deltaY; st.v0 += d / r.width * st.span; clampView(); schedule(true); }
  };
  main.addEventListener('wheel', wheel, { passive: false }); dom.words.addEventListener('wheel', wheel, { passive: false });
  dom.words.addEventListener('pointerdown', e => { const r = dom.words.getBoundingClientRect(); seek(xt(e.clientX - r.left, r.width)); });
  // overview: drag the box to scroll, click elsewhere = jump (playhead + view)
  dom.over.addEventListener('pointerdown', e => {
    if (e.button !== 0) return;
    dom.over.setPointerCapture(e.pointerId);
    const r = dom.over.getBoundingClientRect(), d = dur(), at = ev => (ev.clientX - r.left) / r.width * d;
    const inside = at(e) >= st.v0 && at(e) <= st.v0 + st.span, off = at(e) - st.v0;
    if (!inside) { seek(at(e)); st.v0 = at(e) - st.span / 2; clampView(); schedule(true); }
    const move = ev => { st.v0 = at(ev) - (inside ? off : st.span / 2); clampView(); schedule(true); };
    const up = () => { dom.over.removeEventListener('pointermove', move); dom.over.removeEventListener('pointerup', up); dom.over.removeEventListener('pointercancel', up); };
    dom.over.addEventListener('pointermove', move); dom.over.addEventListener('pointerup', up); dom.over.addEventListener('pointercancel', up);
  });
  if (keyBound) return;
  keyBound = true;
  window.addEventListener('keydown', e => {
    if (!st.active || e.metaKey || e.ctrlKey || e.altKey) return;
    const x = e.target; if (x && (x.isContentEditable || x.tagName === 'TEXTAREA' || x.tagName === 'SELECT' || (x.tagName === 'INPUT' && !['checkbox', 'range', 'color', 'button'].includes(x.type)))) return;
    if (document.getElementById('watch') && !document.getElementById('watch').hidden) return;
    if (e.key === '+' || e.key === '=') { zoomAt(1 / 1.5, S.t); e.preventDefault(); }
    else if (e.key === '-' || e.key === '_') { zoomAt(1.5, S.t); e.preventDefault(); }
  });
}

// ---------------------------------------------------------------- module
/** Build the pane inside `host` (called again whenever the panel is rebuilt, e.g. on a language change). */
export function mountAudio(host) {
  dom.root = host; host.classList.add('av');
  dom.over = el('canvas', 'av-over'); dom.over.title = t('av.overTip');
  dom.main = el('div', 'av-main'); dom.wave = el('canvas', 'av-wave'); dom.top = el('canvas', 'av-top'); dom.tip = el('div', 'av-tip'); dom.tip.hidden = true;
  dom.main.append(dom.wave, dom.top, dom.tip);
  dom.scenes = el('canvas', 'av-scenes'); dom.scenes.title = t('av.scenesTip');
  dom.words = el('canvas', 'av-words'); dom.words.title = t('av.wordsTip');
  host.append(dom.over, dom.scenes, dom.main, dom.words);
  dom.scenes.addEventListener('pointerdown', e => { const r = dom.scenes.getBoundingClientRect(), tt = xt(e.clientX - r.left, r.width), q = buildScenes().find(z => tt >= z.a && tt < z.b); setTime(clamp(q ? q.a : tt, 0, dur())); });
  bind();
  if (dom.ro) dom.ro.disconnect();                    // the panel is rebuilt on a language change: do not stack observers
  dom.ro = new ResizeObserver(() => schedule(true)); dom.ro.observe(host);
  mixSig = ''; wordSig = ''; sceneSig = ''; sceneDirty = wordDirty = true;
}
export function setAudioActive(on) {
  st.active = !!on; if (!on) return;
  if (S.project) { st.span = Math.min(st.span, dur()); clampView(); reveal(true); }
  schedule(true);
}
export const audioActive = () => st.active;
export const hasAudioLayer = () => !!(S.project && S.project.layers.some(l => l.type === 'audio'));
export const audioHooks = {
  project() { sceneDirty = wordDirty = true; if (!st.active) return; clampView(); schedule(true); },
  time() { if (!st.active) return; if (S.playing || !st.drag) reveal(!S.playing); schedule(false); },
  theme() { schedule(true); },
};

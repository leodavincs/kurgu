// sequence.js — Sequence settings dialog (frame size / aspect presets, frame rate, how to adapt layers) + the adaptation maths.
// One Apply = one undoable change(); a live thumbnail is drawn with the real compositor (preview.renderThumb).
import { S, change, clamp, num, clone, VISUAL, aspectLabel } from './state.js';
import { el, icon, setIcon } from './dom.js';
import { t, parseNum } from './i18n.js';
import { renderThumb } from './preview.js';

export const PRESETS = [
  { id: 'hd', w: 1920, h: 1080, name: 'seq.p.hd' },
  { id: 'uhd', w: 3840, h: 2160, name: 'seq.p.uhd' },
  { id: 'hd720', w: 1280, h: 720, name: 'seq.p.hd720' },
  { id: 'vertical', w: 1080, h: 1920, name: 'seq.p.vertical' },
  { id: 'square', w: 1080, h: 1080, name: 'seq.p.square' },
  { id: 'feed', w: 1080, h: 1350, name: 'seq.p.feed' },
  { id: 'cinema', w: 2560, h: 1080, name: 'seq.p.cinema' },
];
export const FPS_LIST = [24, 25, 30, 50, 60];
const MIN = 16, MAX = 7680;
export const presetFor = (w, h) => PRESETS.find(p => p.w === w && p.h === h) || null;
const even = v => Math.round(v / 2) * 2;
export const cleanSize = v => clamp(even(v), MIN, MAX);

// ---------------------------------------------------------------- layer adaptation
/** Fit factor k of a contain/cover layer for a frame (needs the source size from the media info); null when unknown. */
function fitK(l, W, H) {
  const info = S.media.get(l.src); if (!info || !info.width || !info.height) return null;
  const c = l.crop || {};
  const rw = info.width * (1 - clamp(num(c.left, 0), 0, 0.5) - clamp(num(c.right, 0), 0, 0.5));
  const rh = info.height * (1 - clamp(num(c.top, 0), 0, 0.5) - clamp(num(c.bottom, 0), 0, 0.5));
  return rw > 0 && rh > 0 ? Math.min(W / rw, H / rh) : null;
}
/**
 * Keep the composition when the frame goes from (ow, oh) to P.width × P.height (mutates P): centres map proportionally,
 * text sizes / native-image scale / contain-image scale follow the uniform factor min(nw/ow, nh/oh) so nothing leaves the frame.
 * Cover-fit videos and colour layers are relative to the frame already.
 */
export function adaptLayers(P, ow, oh) {
  const nw = P.width, nh = P.height, f = Math.min(nw / ow, nh / oh);
  const r3 = v => Math.round(v * 1000) / 1000;
  for (const l of P.layers) {
    if (!VISUAL.has(l.type) || l.type === 'color') continue;
    l.x = Math.round(l.x * nw / ow); l.y = Math.round(l.y * nh / oh);
    if (l.type === 'text') {
      l.size = Math.max(1, Math.round(l.size * f));
      if (l.shadow) l.shadow.blur = Math.round(l.shadow.blur * f * 10) / 10;
    } else if (l.fit === 'native') l.scale = r3(l.scale * f);
    else if (l.fit === 'contain') { const k0 = fitK(l, ow, oh), k1 = fitK(l, nw, nh); if (k0 && k1) l.scale = r3(l.scale * f * k0 / k1); }
    for (const e of l.effects || []) if (e.type === 'blur') e.radius = Math.round(num(e.radius, 0) * f * 10) / 10;
  }
}
/** Apply the dialog result (used by the dialog and by tests). */
export function applySequence({ w, h, fps, keep }) {
  change(P => {
    const ow = P.width, oh = P.height;
    P.width = w; P.height = h; P.fps = fps;
    if (keep && (ow !== w || oh !== h)) adaptLayers(P, ow, oh);
  });
}

// ---------------------------------------------------------------- dialog
let box = null, offKey = null;
export const sequenceOpen = () => !!box;
export function closeSequence() {
  if (!box) return;
  box.remove(); box = null; document.body.classList.remove('seq-open');
  window.removeEventListener('keydown', offKey, true); offKey = null;
}

export function openSequence() {
  if (box) return;
  const P = S.project;
  const st = { w: P.width, h: P.height, fps: P.fps, keep: true, lock: false, ratio: P.height / P.width };
  const cur = { w: P.width, h: P.height, fps: P.fps };

  box = el('div'); box.id = 'seq-modal';
  const dlg = el('div', 'seq-box'); box.append(dlg);
  box.addEventListener('pointerdown', e => { if (e.target === box) closeSequence(); });

  const head = el('div', 'seq-head');
  const closeBtn = el('button', 'icon-btn'); closeBtn.append(icon('x')); closeBtn.addEventListener('click', closeSequence);
  const curLabel = el('span', 'seq-cur');
  head.append(el('b', '', t('seq.title')), curLabel, closeBtn);

  const body = el('div', 'seq-body');
  const presetsCol = el('div', 'seq-presets'); presetsCol.append(el('div', 'seq-h', t('seq.presets')));
  const presetBtns = new Map();
  const iconFor = (w, h) => { const d = el('span', 'seq-ico'), i = el('i'); const r = w / h; const bw = r >= 1 ? 26 : 26 * r, bh = r >= 1 ? 26 / r : 26; i.style.width = Math.max(6, bw) + 'px'; i.style.height = Math.max(6, bh) + 'px'; d.append(i); return d; };
  for (const p of PRESETS) {
    const b = el('button', 'seq-preset'); const txt = el('span', 'seq-pt');
    txt.append(el('b', '', `${aspectLabel(p.w, p.h)} · ${p.w}×${p.h}`), el('span', '', t(p.name)));
    b.append(iconFor(p.w, p.h), txt);
    b.addEventListener('click', () => { st.w = p.w; st.h = p.h; st.ratio = p.h / p.w; sync(); });
    presetsCol.append(b); presetBtns.set(p.id, b);
  }
  const customBtn = el('button', 'seq-preset'); const ctxt = el('span', 'seq-pt'); ctxt.append(el('b', '', t('seq.custom')), el('span', '', t('seq.customSub', { min: MIN, max: MAX })));
  const customIco = el('span', 'seq-ico'); customIco.append(el('i')); customBtn.append(customIco, ctxt);
  customBtn.addEventListener('click', () => { wIn.focus(); wIn.select(); });
  presetsCol.append(customBtn);

  const main = el('div', 'seq-main');
  // frame size
  const sizeSec = el('div'); sizeSec.append(el('div', 'seq-h', t('seq.frameSize')));
  const sizeRow = el('div', 'seq-row');
  const mkIn = () => { const i = el('input', 'seq-in'); i.type = 'text'; i.inputMode = 'numeric'; i.spellcheck = false; i.addEventListener('focus', () => i.select()); return i; };
  const wIn = mkIn(), hIn = mkIn();
  const lockBtn = el('button', 'btn small seq-lock'); setIcon(lockBtn, 'lock-open'); lockBtn.title = t('seq.lockTip');
  const aspectTxt = el('span', 'seq-aspect');
  sizeRow.append(el('span', '', t('seq.width')), wIn, el('span', '', '×'), el('span', '', t('seq.height')), hIn, lockBtn, aspectTxt);
  const sizeHint = el('div', 'seq-hint', t('seq.sizeHint', { min: MIN, max: MAX })); sizeHint.style.marginTop = '6px';
  sizeSec.append(sizeRow, sizeHint);
  const commitSize = (which) => {
    const v = parseNum((which === 'w' ? wIn : hIn).value);
    if (Number.isFinite(v)) {
      const c = cleanSize(v);
      if (which === 'w') { st.w = c; if (st.lock) st.h = cleanSize(c * st.ratio); }
      else { st.h = c; if (st.lock) st.w = cleanSize(c / st.ratio); }
      if (!st.lock) st.ratio = st.h / st.w;
    }
    sync();
  };
  for (const [inp, k] of [[wIn, 'w'], [hIn, 'h']]) {
    inp.addEventListener('change', () => commitSize(k));
    inp.addEventListener('keydown', e => { if (e.key === 'Enter') { e.stopPropagation(); commitSize(k); inp.blur(); } });
  }
  lockBtn.addEventListener('click', () => { st.lock = !st.lock; if (st.lock) st.ratio = st.h / st.w; sync(); });

  // frame rate
  const fpsSec = el('div'); fpsSec.append(el('div', 'seq-h', t('seq.fps')));
  const fpsRow = el('div', 'seq-row'); const fpsGroup = el('div', 'seq-fps'); const fpsBtns = new Map();
  for (const f of FPS_LIST) { const b = el('button', 'btn', String(f)); b.addEventListener('click', () => { st.fps = f; sync(); }); fpsGroup.append(b); fpsBtns.set(f, b); }
  const fpsIn = mkIn(); fpsIn.style.width = '64px';
  fpsIn.addEventListener('change', () => { const v = parseNum(fpsIn.value); if (Number.isFinite(v) && v > 0) st.fps = Math.round(clamp(v, 1, 240) * 1000) / 1000; sync(); });
  fpsIn.addEventListener('keydown', e => { if (e.key === 'Enter') { e.stopPropagation(); fpsIn.blur(); } });
  fpsRow.append(fpsGroup, el('span', '', t('seq.fpsCustom')), fpsIn, el('span', 'seq-hint', t('seq.fpsHint')));
  fpsSec.append(fpsRow);

  // adaptation
  const adaptSec = el('div', 'seq-adapt');
  const radios = el('div'); radios.append(el('div', 'seq-h', t('seq.adapt')));
  const mkRadio = (val, title, sub) => {
    const l = el('label', 'seq-radio'); const r = el('input'); r.type = 'radio'; r.name = 'seq-adapt'; r.value = val;
    const tx = el('div'); tx.append(el('b', '', title), el('span', '', sub)); l.append(r, tx);
    r.addEventListener('change', () => { st.keep = val === 'keep'; sync(); }); radios.append(l); return r;
  };
  const rKeep = mkRadio('keep', t('seq.keepComposition'), t('seq.keepCompositionSub')), rPix = mkRadio('pixels', t('seq.keepPixels'), t('seq.keepPixelsSub'));
  const thumbBox = el('div', 'seq-thumb'); const thumb = el('canvas'); const thumbCap = el('span');
  thumbBox.append(thumb, thumbCap);
  adaptSec.append(radios, thumbBox);

  main.append(sizeSec, fpsSec, adaptSec);
  body.append(presetsCol, main);

  const foot = el('div', 'seq-foot'); const err = el('span', 'seq-error'); err.style.marginRight = 'auto';
  const cancel = el('button', 'btn', t('seq.cancel')), ok = el('button', 'btn primary', t('seq.apply'));
  cancel.addEventListener('click', closeSequence);
  foot.append(err, cancel, ok);
  dlg.append(head, body, foot);
  document.body.append(box); document.body.classList.add('seq-open');

  let thumbTimer = 0;
  const drawThumb = () => {
    thumbTimer = 0;
    const Q = clone(S.project); Q.width = st.w; Q.height = st.h;
    if (st.keep && (st.w !== cur.w || st.h !== cur.h)) adaptLayers(Q, cur.w, cur.h);
    const mw = 240, mh = 170, k = Math.min(mw / st.w, mh / st.h);
    thumb.width = Math.max(8, Math.round(st.w * k)); thumb.height = Math.max(8, Math.round(st.h * k));
    renderThumb(Q, thumb);
    thumbCap.textContent = `${st.w}×${st.h} · ${aspectLabel(st.w, st.h)}`;
  };
  const sizeChanged = () => st.w !== cur.w || st.h !== cur.h;
  function sync() {
    const match = presetFor(st.w, st.h);
    for (const [id, b] of presetBtns) b.classList.toggle('on', !!match && match.id === id);
    customBtn.classList.toggle('on', !match);
    const ci = customIco.firstChild; const r = st.w / st.h; ci.style.width = Math.max(6, r >= 1 ? 26 : 26 * r) + 'px'; ci.style.height = Math.max(6, r >= 1 ? 26 / r : 26) + 'px';
    if (document.activeElement !== wIn) wIn.value = String(st.w);
    if (document.activeElement !== hIn) hIn.value = String(st.h);
    aspectTxt.textContent = aspectLabel(st.w, st.h);
    setIcon(lockBtn, st.lock ? 'lock' : 'lock-open'); lockBtn.classList.toggle('on', st.lock);
    for (const [f, b] of fpsBtns) b.classList.toggle('on', st.fps === f);
    if (document.activeElement !== fpsIn) fpsIn.value = FPS_LIST.includes(st.fps) ? '' : String(st.fps).replace('.', t('lang.code') === 'tr' ? ',' : '.');
    rKeep.checked = st.keep; rPix.checked = !st.keep;
    adaptSec.hidden = !sizeChanged();
    const cm = presetFor(cur.w, cur.h);
    curLabel.textContent = t('seq.current', { size: `${cur.w}×${cur.h}`, ratio: aspectLabel(cur.w, cur.h), fps: String(cur.fps), preset: cm ? t(cm.name) : t('seq.custom') });
    const changed = sizeChanged() || st.fps !== cur.fps;
    ok.disabled = !changed;
    if (sizeChanged() && !thumbTimer) thumbTimer = requestAnimationFrame(drawThumb);
  }
  ok.addEventListener('click', () => {
    if (ok.disabled) return;
    applySequence({ w: st.w, h: st.h, fps: st.fps, keep: st.keep }); closeSequence();
  });
  offKey = e => { // the dialog owns the keyboard while open
    if (e.key === 'Escape') { e.stopPropagation(); e.preventDefault(); closeSequence(); }
    else if (e.target.tagName === 'INPUT') return; // typing: the field's own handlers run (the main shortcuts ignore typing)
    else if (e.key === 'Enter' && e.target.tagName !== 'BUTTON') { e.stopPropagation(); e.preventDefault(); if (!ok.disabled) ok.click(); }
    else e.stopPropagation();
  };
  window.addEventListener('keydown', offKey, true);
  sync();
  setTimeout(() => { wIn.focus(); wIn.select(); }, 0);
}

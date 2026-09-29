// layers.js — bottom panel with two views of the same layers. TRACKS (default, Premiere style): clips that do not overlap in time share a track row (packing: trackpack.js).
// LAYERS (After-Effects style): one row per layer, controls on the left, a thin time strip on the right. The view is display only; project.json is the same either way.
// Move / trim in / trim out, snapping, drag-to-reorder, waveform, video filmstrip, volume-envelope editing on the strip, group folding, zoom.
import { isMod } from './platform.js';
import { S, change, select, setTime, layer, clamp, timecode, TYPE_ICON, HAS_AUDIO, isFrozen, envelopeDb, basename, linkedWith } from './state.js';
import * as LK from './links.js';
import { waveform, thumbs } from './api.js';
import { mediaStatus } from './preview.js';
import { el, icon, setIcon } from './dom.js';
import { t, fmt } from './i18n.js';
import { cv as tok, themeKey } from './theme.js';
import * as KF from './keyframes.js';
import { sections, layout, place, collides } from './trackpack.js';

const LEFT = 248, ROW_H = 28, EDGE = 7, RULER_H = 28, BAR_H = ROW_H - 8, CLIP_H = 28;  // LEFT = label column; keep in sync with .row .left / .lp-ruler-left in app.css
const DB_MIN = -40, DB_MAX = 12;   // vertical range of the envelope line on the strip
let dom = {}, pps = 20, zoom = 1;
const rows = new Map();            // layer id -> row
const lanes = new Map();           // 'layerId|prop' -> keyframe lane row
const expanded = new Set();        // layer ids whose keyframe lanes are shown
const headerCache = new Map();
const waves = new Map();           // src -> {rate, peaks} | false | null (loading)
const filmstrips = new Map();      // src -> {imgs, loaded, failed}
const dpr = () => Math.min(2, window.devicePixelRatio || 1);
let visibleOrder = [];
let lastSelectionKey = '';
// ---- track view state
const VIEW_KEY = 'kurgu.layerView';
let viewMode = (() => { try { return localStorage.getItem(VIEW_KEY) === 'layers' ? 'layers' : 'tracks'; } catch (e) { return 'tracks'; } })();
const clips = new Map();           // layer id -> clip (track view)
const trackRows = new Map();       // 'section|track' -> track row
let trackNodes = [];               // [{el, si, k}] rows currently shown, top to bottom (hit testing)
let layoutCache = null;            // {sig, lay}
let frozen = null;                 // layout kept while a clip is being dragged (rows must not jump)
const barH = () => (viewMode === 'tracks' ? CLIP_H : BAR_H);

const round3 = v => Math.round(v * 1000) / 1000;
const stripW = () => Math.max(50, Math.round(S.project.duration * pps));

// ---------------------------------------------------------------- scale
function computeScale() {
  const visible = dom.body.clientWidth - LEFT - 24;
  const fit = Math.max(4, visible / Math.max(0.1, S.project.duration));
  const max = 14000 / Math.max(0.1, S.project.duration);
  pps = Math.min(fit * zoom, Math.max(fit, max));
}
export function setZoom(z, centerT) {
  const oldPps = pps, oldScroll = dom.body.scrollLeft;
  const centerX = centerT == null ? null : centerT * oldPps - oldScroll;
  zoom = clamp(z, 1, 60);
  computeScale(); redraw(true);
  if (centerT != null) dom.body.scrollLeft = centerT * pps - centerX;
  if (dom.zoomInput) { dom.zoomInput.value = String(Math.log(zoom) / Math.log(60)); dom.zoomInput.style.setProperty('--p', (parseFloat(dom.zoomInput.value) * 100) + '%'); }
}

export function setView(mode) {
  if (mode !== 'tracks' && mode !== 'layers') return;
  if (mode === viewMode) return;
  viewMode = mode; try { localStorage.setItem(VIEW_KEY, mode); } catch (e) { /* ignore */ }
  for (const b of dom.root.querySelectorAll('.lp-seg-btn')) { const on = b.dataset.view === mode; b.classList.toggle('on', on); b.setAttribute('aria-pressed', String(on)); }
  dom.list.classList.toggle('view-tracks', mode === 'tracks'); dom.list.replaceChildren(); rows.clear(); clips.clear(); trackRows.clear(); lanes.clear(); headerCache.clear(); frozen = null; layoutCache = null;
  redraw(true);
  const id = S.selection[S.selection.length - 1];
  const node = id && (viewMode === 'tracks' ? clips.get(id)?.el : rows.get(id)?.el);
  if (node) node.scrollIntoView({ block: 'nearest' });
}
export const getView = () => viewMode;

// ---------------------------------------------------------------- build
function build() {
  const root = dom.root;
  const keepLeft = dom.body ? dom.body.scrollLeft : 0, keepTop = dom.body ? dom.body.scrollTop : 0;
  root.innerHTML = ''; rows.clear(); lanes.clear(); headerCache.clear(); clips.clear(); trackRows.clear(); layoutCache = null; frozen = null;
  const bar = el('div', 'lp-bar');
  bar.append(el('span', 'lp-title', t('layers.title')));
  const right = el('div', 'lp-bar-right');
  const zoomOut = el('span', 'lp-hint'); zoomOut.append(icon('zoom-out', 's')); const zoomIn = el('span', 'lp-hint'); zoomIn.append(icon('zoom-in', 's'));
  const zoomInput = el('input'); zoomInput.type = 'range'; zoomInput.min = '0'; zoomInput.max = '1'; zoomInput.step = '0.01'; zoomInput.value = String(Math.log(zoom) / Math.log(60)); zoomInput.title = t('layers.zoomTip');
  const fillZoom = () => zoomInput.style.setProperty('--p', (parseFloat(zoomInput.value) * 100) + '%'); fillZoom();
  zoomInput.addEventListener('input', () => { fillZoom(); setZoom(Math.pow(60, parseFloat(zoomInput.value)), S.t); });
  const fitBtn = el('button', 'lp-btn', t('layers.fit')); fitBtn.addEventListener('click', () => { setZoom(1); dom.body.scrollLeft = 0; });
  const seg = el('div', 'lp-seg'); seg.setAttribute('role', 'group'); seg.title = t('layers.viewTip');
  for (const [mode, key] of [['tracks', 'layers.viewTracks'], ['layers', 'layers.viewLayers']]) {
    const b = el('button', 'lp-seg-btn' + (viewMode === mode ? ' on' : ''), t(key)); b.type = 'button'; b.dataset.view = mode; b.setAttribute('aria-pressed', String(viewMode === mode));
    b.addEventListener('click', () => setView(mode)); seg.append(b);
  }
  right.append(seg, zoomOut, zoomInput, zoomIn, fitBtn); bar.append(right);
  dom.zoomInput = zoomInput;
  const body = el('div', 'lp-body'); dom.body = body;
  const content = el('div', 'lp-content'); dom.content = content;
  const ruler = el('div', 'lp-ruler');
  const rulerLeft = el('div', 'lp-ruler-left'); rulerLeft.textContent = timecode(0); dom.rulerLeft = rulerLeft;
  const rulerStrip = el('div', 'lp-ruler-strip'); dom.rulerStrip = rulerStrip;
  const rulerCanvas = el('canvas'); dom.rulerCanvas = rulerCanvas; rulerStrip.append(rulerCanvas);
  const noteHost = el('div', 'lp-notes'); dom.noteHost = noteHost; rulerStrip.append(noteHost);
  const head = el('div', 'lp-head'); dom.head = head; rulerStrip.append(head);
  ruler.append(rulerLeft, rulerStrip);
  const list = el('div', 'lp-list' + (viewMode === 'tracks' ? ' view-tracks' : '')); dom.list = list;
  const line = el('div', 'lp-line'); dom.line = line;
  const snap = el('div', 'lp-snap'); dom.snapLine = snap;
  const drop = el('div', 'lp-drop'); dom.drop = drop;
  const tip = el('div', 'lp-tip'); dom.tip = tip; tip.hidden = true;
  content.append(ruler, list, line, snap, drop);
  body.append(content);
  root.append(bar, body, tip);
  list.addEventListener('pointerdown', () => { S.focus = 'layers'; });

  // scrub on the ruler
  rulerStrip.addEventListener('pointerdown', e => {
    if (e.target.closest('.lp-note')) return;
    e.preventDefault(); rulerStrip.setPointerCapture(e.pointerId); S.focus = 'layers'; S.scrubbing = true;
    const go = ev => { const r = rulerStrip.getBoundingClientRect(); setTime(clamp((ev.clientX - r.left) / pps, 0, S.project.duration)); };
    go(e);
    const move = ev => go(ev);
    const done = () => { S.scrubbing = false; rulerStrip.removeEventListener('pointermove', move); rulerStrip.removeEventListener('pointerup', done); rulerStrip.removeEventListener('pointercancel', done); };
    rulerStrip.addEventListener('pointermove', move); rulerStrip.addEventListener('pointerup', done); rulerStrip.addEventListener('pointercancel', done);
  });
  body.addEventListener('wheel', e => {
    if (e.ctrlKey || e.metaKey) {
      e.preventDefault();
      const r = dom.rulerStrip.getBoundingClientRect(); const tt = clamp((e.clientX - r.left) / pps, 0, S.project.duration);
      setZoom(zoom * Math.exp(-e.deltaY * 0.01), tt);
    }
  }, { passive: false });
  if (dom.observer) dom.observer.disconnect();
  dom.observer = new ResizeObserver(() => { const old = pps; computeScale(); if (Math.abs(old - pps) > 0.01) redraw(true); });
  dom.observer.observe(body);
  computeScale();
  redraw(true);
  body.scrollLeft = keepLeft; body.scrollTop = keepTop;
}

export function startLayerPanel(root) {
  dom.root = root;
  build();
  KF.initKeyboard();
  document.addEventListener('kf-selection', () => { if (S.project) redraw(false); });
  return {
    project: () => { KF.pruneKeySel(); redraw(false); },
    time: updateTime,
    selection: onSelection,
    media: () => redraw(false),
    settings: () => {},
    lang: () => build(),
    theme: () => redraw(true),
  };
}

// ---------------------------------------------------------------- group colours
// Each layer group gets a calm accent: by default from the dominant layer type of the group (cards/titles, captions, video, image, colour, audio: --grp-* tokens),
// a project override `groups: {name: {color}}` wins. A second group of the same kind takes a free colour from a stable name hash. Display only.
const GROUP_VARS = ['--grp-titles', '--grp-captions', '--grp-video', '--grp-image', '--grp-color', '--grp-audio', '--grp-coral', '--grp-olive'];
export const GROUP_SWATCHES = ['#c39a52', '#c0708f', '#5077b0', '#8570c4', '#7c8698', '#3fa088', '#c47a5c', '#93a952'];   // picker (theme independent mid tones)
const KIND_ICON = { titles: 'type', captions: 'list', video: 'film', image: 'image', color: 'square', audio: 'music' };
const CAPTION_RE = /altyaz|caption|subtitle|\bsubs?\b|\bcc\b/i;
const TYPE_KIND = { text: 'titles', video: 'video', image: 'image', color: 'color', audio: 'audio' };
const TYPE_PRIORITY = ['video', 'image', 'text', 'audio', 'color'];
const nameHash = str => { let h = 5381; for (const c of str) h = ((h << 5) + h + c.codePointAt(0)) >>> 0; return h; };
const hexToRgb = hex => { const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim()); if (!m) return [128, 128, 128]; const n = parseInt(m[1], 16); return [n >> 16, (n >> 8) & 255, n & 255]; };
const rgba = (hex, a) => { const [r, g, b] = hexToRgb(hex); return `rgba(${r},${g},${b},${a})`; };
const lighten = (hex, k) => { const [r, g, b] = hexToRgb(hex); return `rgb(${Math.round(r + (255 - r) * k)},${Math.round(g + (255 - g) * k)},${Math.round(b + (255 - b) * k)})`; };
/** name -> {name, kind, css (for CSS custom property), hex (for canvas), count, auto}; only groups that have layers. */
function groupInfos() {
  const P = S.project, out = new Map(), types = new Map(), order = [];
  for (const l of P.layers) {
    if (!l.group) continue;
    if (!types.has(l.group)) { types.set(l.group, new Map()); order.push(l.group); }
    const m = types.get(l.group); m.set(l.type, (m.get(l.type) || 0) + 1);
  }
  const taken = new Set();
  const override = name => { const c = P.groups && P.groups[name] && P.groups[name].color; return typeof c === 'string' && /^#[0-9a-f]{6}$/i.test(c) ? c.toLowerCase() : null; };
  for (const name of order) {
    const ov = override(name), m = types.get(name);
    const dominant = [...m.entries()].sort((a, b) => b[1] - a[1] || TYPE_PRIORITY.indexOf(a[0]) - TYPE_PRIORITY.indexOf(b[0]))[0][0];
    const kind = dominant === 'text' && CAPTION_RE.test(name) ? 'captions' : TYPE_KIND[dominant] || 'titles';
    const count = [...m.values()].reduce((a, b) => a + b, 0);
    if (ov) { out.set(name, { name, kind, css: ov, hex: ov, count, auto: false }); }
    else out.set(name, { name, kind, css: null, hex: null, count, auto: true, want: '--grp-' + kind });
  }
  for (const info of out.values()) {       // pass 2: automatic colours, first group of a kind keeps the kind colour, later ones probe from their hash
    if (!info.auto) continue;
    let v = info.want;
    if (taken.has(v)) { let i = nameHash(info.name) % GROUP_VARS.length; for (let n = 0; n < GROUP_VARS.length && taken.has(GROUP_VARS[i]); n++) i = (i + 1) % GROUP_VARS.length; v = GROUP_VARS[i]; }
    taken.add(v); info.css = `var(${v})`; info.hex = tok(v) || '#7c8698';
  }
  return out;
}
let ginfo = new Map();

// ---------------------------------------------------------------- display list
function displayList() {
  const out = []; let prev = null;
  for (const l of S.project.layers) {
    if (l.group && l.group !== prev) out.push({ header: l.group });
    prev = l.group;
    if (l.group && S.collapsed.has(l.group)) continue;
    out.push({ l });
    if (expanded.has(l.id)) KF.animatedProps(l).forEach((prop, i) => out.push({ lane: { l, prop, first: i === 0 } }));
  }
  return out;
}

function redraw(force) {
  if (!S.project) return;
  computeScale();
  const width = LEFT + stripW();
  dom.content.style.width = (width + 20) + 'px';
  dom.list.style.width = width + 'px';
  ginfo = groupInfos();
  if (viewMode === 'tracks') { redrawTracks(force); drawRuler(); updateTime(); return; }
  dom.list.classList.remove('view-tracks');
  const items = displayList();
  visibleOrder = items.filter(x => x.l).map(x => x.l.id);
  const used = new Set(), usedLanes = new Set();
  const nodes = [];
  items.forEach((x, i) => {
    if (x.header !== undefined) { nodes.push(headerRow(x.header, i, ginfo.get(x.header))); return; }
    if (x.lane) {
      const key = x.lane.l.id + '|' + x.lane.prop; let s = lanes.get(key);
      if (!s) { s = buildLane(x.lane.l, x.lane.prop); lanes.set(key, s); }
      usedLanes.add(key); updateLane(s, x.lane.l, x.lane.first); nodes.push(s.el); return;
    }
    let s = rows.get(x.l.id);
    if (!s) { s = buildRow(x.l); rows.set(x.l.id, s); }
    used.add(x.l.id);
    updateRow(s, x.l, force);
    nodes.push(s.el);
  });
  for (const [id, s] of rows) if (!used.has(id)) { s.el.remove(); if (!S.project.layers.some(l => l.id === id)) rows.delete(id); }
  for (const [key, s] of lanes) if (!usedLanes.has(key)) { s.el.remove(); lanes.delete(key); }
  // reconcile DOM order without re-creating nodes
  let cur = dom.list.firstChild;
  for (const n of nodes) { if (n === cur) cur = cur.nextSibling; else dom.list.insertBefore(n, cur); }
  while (cur) { const next = cur.nextSibling; if (!nodes.includes(cur)) cur.remove(); cur = next; }
  drawRuler();
  updateTime();
}

function headerRow(name, i, info) {
  const key = name + '#' + i;
  let h = headerCache.get(key);
  if (!h) {
    h = el('div', 'row group'); const left = el('div', 'left'); const arrow = el('span', 'arrow'); const chip = el('span', 'grp-chip'); const label = el('span', 'name'); const count = el('span', 'grp-count');
    left.append(arrow, chip, label, count); const strip = el('div', 'strip'); h.append(left, strip);
    left.addEventListener('click', () => { if (S.collapsed.has(h._name)) S.collapsed.delete(h._name); else S.collapsed.add(h._name); redraw(false); });
    left.addEventListener('contextmenu', ev => { ev.preventDefault(); openGroupPicker(ev.clientX, ev.clientY, h._name); });
    strip.addEventListener('contextmenu', ev => { ev.preventDefault(); openGroupPicker(ev.clientX, ev.clientY, h._name); });
    left.title = t('groups.menuTip');
    h._arrow = arrow; h._chip = chip; h._label = label; h._count = count; h._strip = strip; headerCache.set(key, h);
  }
  h._name = name; h._label.textContent = name;
  setIcon(h._arrow, S.collapsed.has(name) ? 'chevron-right' : 'chevron-down', 's');
  h._strip.style.width = stripW() + 'px';
  const cls = 'row group' + (info ? ' in-group kind-' + info.kind : '');
  if (h.className !== cls) h.className = cls;
  if (info) { h.style.setProperty('--grp', info.css); setIcon(h._chip, KIND_ICON[info.kind] || 'square', 's'); h._count.textContent = String(info.count); h._count.title = t('groups.count', { n: info.count }); }
  else { h.style.removeProperty('--grp'); h._chip.replaceChildren(); delete h._chip.dataset.ic; h._count.textContent = ''; }
  return h;
}

function buildRow(l) {
  const e = el('div', 'row'); e.dataset.id = l.id;
  const left = el('div', 'left');
  const grip = el('span', 'grip'); grip.append(icon('grip-vertical', 's')); grip.title = t('layers.reorder');
  const twirl = el('button', 'twirl none'); twirl.type = 'button';
  const eye = el('button', 'icon-btn eye', ''); eye.title = t('layers.toggleVisible');
  const lock = el('button', 'icon-btn lock', ''); lock.title = t('layers.toggleLock');
  const spk = el('button', 'icon-btn speaker', ''); spk.title = t('layers.toggleMute');
  const ticon = el('span', 'icon'); setIcon(ticon, TYPE_ICON[l.type] || 'square');
  const name = el('span', 'name'); const warn = el('span', 'warn'); warn.append(icon('triangle-alert', 's')); warn.style.display = 'none';
  left.append(grip, twirl, ticon, name, eye, lock, spk, warn);
  const strip = el('div', 'strip');
  const bar = el('div', 'bar'); const cv = el('canvas'); const eL = el('div', 'edge e-left'); const eR = el('div', 'edge e-right');
  bar.append(cv, eL, eR); strip.append(bar);
  const sum = el('div', 'kf-sum'); strip.append(sum);
  e.append(left, strip);
  const s = { id: l.id, el: e, left, grip, twirl, eye, lock, spk, icon: ticon, name, warn, strip, bar, cv, sum, sig: '', ssig: '' };
  linkBits(s, bar, left);
  const id = l.id;
  const toggle = f => ev => { ev.stopPropagation(); change(P => { const x = P.layers.find(z => z.id === id); if (x) x[f] = !x[f]; }); };
  twirl.addEventListener('click', ev => { ev.stopPropagation(); if (expanded.has(l.id)) expanded.delete(l.id); else expanded.add(l.id); redraw(false); });
  eye.addEventListener('click', toggle('hidden'));
  lock.addEventListener('click', toggle('locked'));
  spk.addEventListener('click', toggle('muted'));
  left.addEventListener('pointerdown', ev => {
    if (ev.target.closest('button') || ev.target.closest('.grip') || ev.target.tagName === 'INPUT') return;
    if (ev.shiftKey || isMod(ev)) select([id], true, { single: ev.altKey }); else if (!S.selection.includes(id) || S.selection.length > 1) select([id], false, { single: ev.altKey });
  });
  name.addEventListener('dblclick', ev => { ev.stopPropagation(); renameLayer(s); });
  grip.addEventListener('pointerdown', ev => reorderDrag(ev, id));
  bar.addEventListener('pointerdown', ev => barPointerDown(ev, id, s));
  bar.addEventListener('pointermove', ev => barHover(ev, id, s));
  bar.addEventListener('dblclick', ev => barDoubleClick(ev, id, s));
  bar.addEventListener('contextmenu', ev => openClipMenu(ev, id));
  strip.addEventListener('pointerdown', ev => { if (ev.target === strip) select([id]); });
  return s;
}

function renameLayer(s) {
  const l = layer(s.id); if (!l) return;
  const input = el('input', 'name-edit'); input.value = l.name; s.name.style.display = 'none'; s.left.insertBefore(input, s.name);
  input.focus(); input.select();
  let done = false;
  const finish = commit => {
    if (done) return; done = true;
    const v = input.value.trim(); input.remove(); s.name.style.display = '';
    if (commit && v && v !== l.name) change(P => { const x = P.layers.find(z => z.id === s.id); if (x) x.name = v; });
  };
  input.addEventListener('keydown', e => { e.stopPropagation(); if (e.key === 'Enter') finish(true); else if (e.key === 'Escape') finish(false); });
  input.addEventListener('blur', () => finish(true));
  input.addEventListener('pointerdown', e => e.stopPropagation());
}

function updateRow(s, l, force) {
  const selected = S.selection.includes(l.id);
  const st = mediaStatus(l.id);
  const gi = l.group ? ginfo.get(l.group) : null;
  const cls = 'row t-' + l.type + (gi ? ' in-group kind-' + gi.kind : '') + (selected ? ' selected' : '') + (l.hidden ? ' hidden' : '') + (l.locked ? ' locked' : '') + (st.failed ? ' missing' : '') + linkClass(l, selected);
  if (s.el.className !== cls) s.el.className = cls;
  if (gi) s.el.style.setProperty('--grp', gi.css); else s.el.style.removeProperty('--grp');
  paintLink(s, l);
  if (s.name.textContent !== l.name) s.name.textContent = l.name;
  setIcon(s.eye, l.hidden ? 'eye-off' : 'eye', 's'); s.eye.classList.toggle('on', !!l.hidden);
  setIcon(s.lock, l.locked ? 'lock' : 'lock-open', 's'); s.lock.classList.toggle('on', !!l.locked);
  s.spk.style.visibility = HAS_AUDIO.has(l.type) ? 'visible' : 'hidden';
  setIcon(s.spk, l.muted ? 'volume-x' : 'volume-2', 's'); s.spk.classList.toggle('on', !!l.muted);
  s.warn.style.display = st.failed ? '' : 'none';
  if (st.failed) s.warn.title = t('layers.missingFile', { path: l.src || t('layers.noSource') });
  s.strip.style.width = stripW() + 'px';
  const animated = KF.hasAnyKeys(l), open = animated && expanded.has(l.id);
  if (!animated) expanded.delete(l.id);
  s.twirl.classList.toggle('none', !animated); setIcon(s.twirl, open ? 'chevron-down' : 'chevron-right', 's'); s.twirl.title = t(open ? 'layers.twirlHide' : 'layers.twirl'); s.twirl.setAttribute('aria-label', s.twirl.title); s.twirl.setAttribute('aria-expanded', String(open));
  updateSummary(s, l, animated);
  setIcon(s.icon, TYPE_ICON[l.type] || 'square');
  s.bar.style.left = (l.start * pps) + 'px';
  const w = Math.max(3, (l.end - l.start) * pps);
  s.bar.style.width = w + 'px';
  if (HAS_AUDIO.has(l.type) && l.src && !waves.has(l.src) && !st.failed) {
    waves.set(l.src, null);
    waveform(l.src).then(v => { waves.set(l.src, v && v.peaks ? v : false); redraw(true); });
  }
  const strip = l.type === 'video' && l.src && !st.failed ? filmstrip(l.src) : null;
  const wave = waves.get(l.src);
  const sig = JSON.stringify([l.type, Math.round(w), dpr(), l.src_in, l.end - l.start, l.fade_in, l.fade_out, l.envelope, l.volume_db, l.text, l.muted, l.start, !!wave, l.src, st.failed, l.name, isFrozen(l), strip ? strip.loaded : -1, S.media.get(l.src)?.duration || 0, themeKey(), selected, gi ? gi.hex : '', gi ? gi.kind : '']);
  if (sig !== s.sig) { s.sig = sig; drawBar(s, l, w, wave, strip, selected, gi); }
}

// ---------------------------------------------------------------- linked clips (SPEC 1.1a): glyph, highlight, sync-offset badge, context menu
/* LINK-BEGIN (helpers; styles in the delimited block at the end of app.css) */
function linkBits(s, bar, left) {
  const glyph = el('span', 'lk-glyph'); glyph.append(icon('link', 's')); glyph.hidden = true; glyph.title = t('link.glyphTip');
  const off = el('button', 'lk-off'); off.type = 'button'; off.hidden = true;
  off.addEventListener('pointerdown', ev => ev.stopPropagation());
  off.addEventListener('click', ev => { ev.stopPropagation(); if (!S.selection.includes(s.id)) select([s.id]); LK.resync([s.id]); });
  if (left) { left.insertBefore(glyph, s.name.nextSibling); } else bar.append(glyph);
  bar.append(off); s.lk = glyph; s.off = off;
}
function linkClass(l, selected) {
  const g = linkedWith(l); if (g.length < 2) return '';
  const hl = selected || g.some(x => S.selection.includes(x.id));
  return ' linked' + (hl ? ' link-hl' : '');
}
function paintLink(s, l) {
  const linked = linkedWith(l).length > 1;
  if (s.lk.hidden === linked) s.lk.hidden = !linked;
  const d = linked ? LK.offsetOf(l) : 0, bad = Math.abs(d) > 0.5 / (S.project.fps || 30);
  s.off.hidden = !bad;
  if (bad) { const txt = LK.offsetText(d); if (s.off.textContent !== txt) s.off.textContent = txt; s.off.title = t('link.offsetTip', { off: txt }); }
}
function openClipMenu(ev, id) {
  ev.preventDefault(); ev.stopPropagation();
  if (!S.selection.includes(id)) select([id], false, { single: ev.altKey });
  const m = document.getElementById('menu'); m.innerHTML = '';
  const av = LK.available(S.selection);
  const item = (label, fn, on) => { if (!on) return; const b = el('button', 'menu-item', label); b.addEventListener('click', () => { m.hidden = true; fn(); }); m.append(b); };
  item(t('link.unlink'), () => LK.unlinkLayers(S.selection), av.unlink);
  item(t('link.link'), () => LK.linkLayers(S.selection), av.link);
  item(t('link.separate'), () => LK.separateAudio(S.selection[0]), av.separate);
  item(t('link.merge'), () => LK.mergeAudio(S.selection), av.merge);
  item(t('link.resync'), () => LK.resync(S.selection), av.resync);
  if (!m.children.length) { const b = el('button', 'menu-item disabled', t('link.none')); b.disabled = true; m.append(b); }
  m.style.left = Math.min(ev.clientX, window.innerWidth - 240) + 'px'; m.style.top = Math.min(ev.clientY, window.innerHeight - 40 - 30 * m.children.length) + 'px'; m.hidden = false;
}
/* LINK-END */

// ---------------------------------------------------------------- track view (Premiere style)
// Rows are derived: sections = runs of one group in z-order, each packed first-fit (trackpack.js), front layers on the upper rows. Numbering is from the bottom (V1 lowest), like Premiere.
const KIND_LETTER = { titles: 'T', captions: 'C', video: 'V', image: 'V', color: 'V', audio: 'A' };
const TYPE_LETTER = { text: 'T', audio: 'A' };
function idLayout(lay) { return lay.map(s => ({ group: s.group, from: s.from, tracks: s.tracks.map(r => r.map(l => l.id)) })); }
function currentLayout() {
  const ls = S.project.layers;
  if (frozen && ls.length === frozen.n && ls.every(l => frozen.ids.has(l.id))) return frozen.lay;
  frozen = null;
  const sig = ls.map(l => l.id + '|' + l.group + '|' + l.start + '|' + l.end).join(';');
  if (!layoutCache || layoutCache.sig !== sig) layoutCache = { sig, lay: idLayout(layout(ls)) };
  return layoutCache.lay;
}
function freezeLayout() { if (viewMode === 'tracks') { const lay = currentLayout(); frozen = { lay, n: S.project.layers.length, ids: new Set(S.project.layers.map(l => l.id)) }; } }
function unfreezeLayout() { frozen = null; }
function trackOf(id) { const lay = currentLayout(); for (let si = 0; si < lay.length; si++) for (let k = 0; k < lay[si].tracks.length; k++) if (lay[si].tracks[k].includes(id)) return { si, k }; return null; }

function buildTrackRow() {
  const e = el('div', 'row track'), left = el('div', 'left'), tag = el('span', 'trk-tag'), name = el('span', 'name trk-name'), n = el('span', 'trk-n');
  left.append(tag, name, n);
  const strip = el('div', 'strip'); e.append(left, strip);
  strip.addEventListener('pointerdown', ev => { if (ev.target === strip && !ev.shiftKey && !isMod(ev)) { S.focus = 'layers'; select([]); } });
  return { el: e, left, tag, name, n, strip };
}
function trackLetter(sec, info, ids) {
  if (info) return KIND_LETTER[info.kind] || 'V';
  const l = layer(ids[0]); return (l && TYPE_LETTER[l.type]) || 'V';
}
function updateTrackRow(tr, sec, info, k, ids, si) {
  const count = sec.tracks.length, num = count - k, letter = trackLetter(sec, info, ids);
  const cls = 'row track' + (info ? ' in-group kind-' + info.kind : '') + (k === 0 ? ' top' : '') + (k === count - 1 ? ' bottom' : '');
  if (tr.el.className !== cls && !tr.el.classList.contains('drop-on')) tr.el.className = cls;
  if (info) tr.el.style.setProperty('--grp', info.css); else tr.el.style.removeProperty('--grp');
  tr.tag.textContent = letter + num;
  tr.n.textContent = count > 1 ? String(ids.length) : '';
  tr.left.title = (sec.group ? sec.group + ' · ' : '') + t('tracks.track', { n: num }) + ' · ' + t('tracks.clips', { n: ids.length });
  tr.el.dataset.si = si; tr.el.dataset.k = k;
  tr.strip.style.width = stripW() + 'px';
}
function redrawTracks(force) {
  const lay = currentLayout();
  dom.list.classList.add('view-tracks');
  const nodes = [], usedClips = new Set(), usedLanes = new Set(), usedRows = new Set();
  trackNodes = []; visibleOrder = [];
  lay.forEach((sec, si) => {
    const info = sec.group ? ginfo.get(sec.group) : null;
    if (sec.group) nodes.push(headerRow(sec.group, si, info));
    if (sec.group && S.collapsed.has(sec.group)) return;
    sec.tracks.forEach((ids, k) => {
      const key = si + '|' + k; usedRows.add(key);
      let tr = trackRows.get(key); if (!tr) { tr = buildTrackRow(); trackRows.set(key, tr); }
      updateTrackRow(tr, sec, info, k, ids, si);
      const want = new Set();
      for (const id of ids) {
        const l = layer(id); if (!l) continue;
        let c = clips.get(id); if (!c) { c = buildClip(l); clips.set(id, c); }
        usedClips.add(id); visibleOrder.push(id); updateClip(c, l, force); want.add(c.el);
        if (c.el.parentNode !== tr.strip) tr.strip.append(c.el);
      }
      for (const ch of [...tr.strip.children]) if (!want.has(ch)) ch.remove();
      nodes.push(tr.el); trackNodes.push({ el: tr.el, si, k });
      for (const id of ids) {
        const l = layer(id); if (!l || !expanded.has(id)) continue;
        KF.animatedProps(l).forEach((prop, i) => {
          const lk = id + '|' + prop; let s = lanes.get(lk);
          if (!s) { s = buildLane(l, prop); lanes.set(lk, s); }
          usedLanes.add(lk); updateLane(s, l, i === 0); nodes.push(s.el);
        });
      }
    });
  });
  for (const [id, c] of clips) if (!usedClips.has(id)) { c.el.remove(); if (!layer(id)) clips.delete(id); }
  for (const [key, tr] of trackRows) if (!usedRows.has(key)) { tr.el.remove(); trackRows.delete(key); }
  for (const [key, s] of lanes) if (!usedLanes.has(key)) { s.el.remove(); lanes.delete(key); }
  let cur = dom.list.firstChild;
  for (const n of nodes) { if (n === cur) cur = cur.nextSibling; else dom.list.insertBefore(n, cur); }
  while (cur) { const next = cur.nextSibling; if (!nodes.includes(cur)) cur.remove(); cur = next; }
}

function buildClip(l) {
  const bar = el('div', 'bar clip'); bar.dataset.id = l.id;
  const cv = l.type === 'text' ? null : el('canvas'), label = el('span', 'clip-label');
  const eL = el('div', 'edge e-left'), eR = el('div', 'edge e-right'), sum = el('div', 'clip-keys'), kfb = el('button', 'clip-kf'); kfb.type = 'button';
  bar.append(...[cv, l.type === 'text' ? label : null, sum, eL, eR, kfb].filter(Boolean));
  const s = { id: l.id, el: bar, bar, cv, label, sum, kfb, h: CLIP_H, sig: '', ssig: '' };
  linkBits(s, bar, null);
  const id = l.id;
  bar.addEventListener('contextmenu', ev => openClipMenu(ev, id));
  bar.addEventListener('pointerdown', ev => barPointerDown(ev, id, s));
  bar.addEventListener('pointermove', ev => barHover(ev, id, s));
  bar.addEventListener('dblclick', ev => { if (barDoubleClick(ev, id, s)) return; toggleKeys(id); });
  kfb.addEventListener('pointerdown', ev => ev.stopPropagation());
  kfb.addEventListener('click', ev => { ev.stopPropagation(); toggleKeys(id); });
  return s;
}
function toggleKeys(id) {
  const l = layer(id); if (!l || !KF.hasAnyKeys(l)) return;
  if (expanded.has(id)) expanded.delete(id); else expanded.add(id);
  if (!S.selection.includes(id)) select([id]);
  redraw(false);
}
function ensureWave(l, st) {
  if (HAS_AUDIO.has(l.type) && l.src && !waves.has(l.src) && !st.failed) {
    waves.set(l.src, null);
    waveform(l.src).then(v => { waves.set(l.src, v && v.peaks ? v : false); redraw(true); });
  }
}
function updateClip(s, l, force) {
  const selected = S.selection.includes(l.id), st = mediaStatus(l.id), gi = l.group ? ginfo.get(l.group) : null;
  const cls = 'bar clip t-' + l.type + (gi ? ' in-group kind-' + gi.kind : '') + (selected ? ' selected' : '') + (l.hidden ? ' hidden' : '') + (l.locked ? ' locked' : '') + (st.failed ? ' missing' : '') + (s.el.classList.contains('drag') ? ' drag' : '') + linkClass(l, selected);
  if (s.el.className !== cls) s.el.className = cls;
  if (gi) s.el.style.setProperty('--grp', gi.css); else s.el.style.removeProperty('--grp');
  paintLink(s, l);
  const title = `${l.name} · ${fmt(l.start, 2)}–${fmt(l.end, 2)} ${t('unit.s')}` + (st.failed ? ' · ' + t('layers.missingFile', { path: l.src || t('layers.noSource') }) : '');
  if (s.el.title !== title) s.el.title = title;
  const w = Math.max(3, (l.end - l.start) * pps);
  s.el.style.left = (l.start * pps) + 'px'; s.el.style.width = w + 'px';
  ensureWave(l, st);
  const animated = KF.hasAnyKeys(l), open = animated && expanded.has(l.id);
  if (!animated) expanded.delete(l.id);
  s.kfb.hidden = !animated || w < 34; s.kfb.classList.toggle('on', open);
  s.kfb.title = t(open ? 'layers.twirlHide' : 'layers.twirl'); s.kfb.setAttribute('aria-label', s.kfb.title); s.kfb.setAttribute('aria-expanded', String(open));
  const times = animated ? [...new Set(KF.animatedProps(l).flatMap(p => KF.keysOf(l, p).map(k => Math.round(k.t * 1000))))].sort((a, b) => a - b) : [];
  const ssig = times.length ? pps + '|' + times.join() : '';
  if (ssig !== s.ssig) {
    s.ssig = ssig; s.sum.replaceChildren();
    for (const ms of times) { const i = document.createElement('i'); i.style.left = (ms / 1000 * pps) + 'px'; s.sum.append(i); }
  }
  if (l.type === 'text') {
    const txt = String(l.text).replace(/\s*\n\s*/g, ' ').trim() || l.name;
    if (s.label.textContent !== txt) s.label.textContent = txt;
    return;
  }
  const strip = l.type === 'video' && l.src && !st.failed ? filmstrip(l.src) : null;
  const wave = waves.get(l.src);
  const sig = JSON.stringify([l.type, Math.round(w), dpr(), l.src_in, l.end - l.start, l.fade_in, l.fade_out, l.envelope, l.volume_db, l.muted, l.start, !!wave, l.src, st.failed, l.name, isFrozen(l), strip ? strip.loaded : -1, S.media.get(l.src)?.duration || 0, themeKey(), selected, gi ? gi.hex : '', gi ? gi.kind : '']);
  if (sig !== s.sig) { s.sig = sig; drawBar(s, l, w, wave, strip, selected, gi); }
}

// ---- vertical moves: which row / gap is under the pointer, and where in the z-order the clip has to go to land there
function hitTrackRow(cy) {
  let best = null, bd = Infinity;
  for (const n of trackNodes) {
    const r = n.el.getBoundingClientRect();
    if (cy >= r.top && cy < r.bottom) return { ...n, f: (cy - r.top) / r.height };
    const d = cy < r.top ? r.top - cy : cy - r.bottom;
    if (d < bd) { bd = d; best = { ...n, f: cy < r.top ? 0 : 1 }; }
  }
  return best;
}
/** x = {id, start, end}. Returns the plan {hit, sec, rest, goal, res} or null. */
function planTrack(cy, x) {
  const hit = hitTrackRow(cy); if (!hit) return null;
  const sec = sections(S.project.layers)[hit.si]; if (!sec) return null;
  const rest = sec.layers.filter(l => l.id !== x.id);
  if (!rest.length) return null;
  const xx = { id: x.id, start: x.start, end: x.end, group: sec.group };
  let goal;
  if (hit.f < 0.22) goal = { gap: hit.k }; else if (hit.f > 0.78) goal = { gap: hit.k + 1 };
  else if (collides(rest, xx, hit.k)) goal = { gap: hit.f < 0.5 ? hit.k : hit.k + 1 };   // would overlap: gets a row of its own next to it
  else goal = { track: hit.k };
  return { hit, sec, rest, goal, res: place(rest, xx, goal) };
}
function paintPlan(plan) {
  if (dom.hl) { dom.hl.classList.remove('drop-on'); dom.hl = null; }
  if (!plan) { dom.drop.style.display = 'none'; return; }
  const secRows = trackNodes.filter(n => n.si === plan.hit.si);
  const idx = Math.min(plan.res.track, secRows.length - 1), row = secRows[idx] && secRows[idx].el;
  const lr = dom.list.getBoundingClientRect();
  if (plan.res.grew && row) {            // needs one more row than there is: line at the gap
    const below = plan.res.track >= secRows.length, r = row.getBoundingClientRect();
    dom.drop.style.display = 'block'; dom.drop.dataset.label = t('tracks.newTrack');
    dom.drop.style.transform = `translateY(${(below ? r.bottom : r.top) - lr.top + dom.list.offsetTop - 1}px)`;
  } else {
    dom.drop.style.display = 'none';
    if (row) { row.classList.add('drop-on'); dom.hl = row; }
  }
}
function applyPlan(P, id, plan) {
  const arr = P.layers, from = arr.findIndex(q => q.id === id); if (from < 0) return;
  const [m] = arr.splice(from, 1);
  const restIds = plan.rest.map(l => l.id);
  const at = plan.res.index < restIds.length ? arr.findIndex(q => q.id === restIds[plan.res.index]) : arr.findIndex(q => q.id === restIds[restIds.length - 1]) + 1;
  m.group = plan.sec.group; arr.splice(at, 0, m);
}

// ---------------------------------------------------------------- keyframe summary (collapsed row) and lanes (twirled-down rows)
function updateSummary(s, l, animated) {
  const times = animated ? [...new Set(KF.animatedProps(l).flatMap(p => KF.keysOf(l, p).map(k => Math.round(k.t * 1000))))].sort((a, b) => a - b) : [];
  const ssig = times.length ? l.start + '|' + pps + '|' + times.join() : '';
  if (ssig === s.ssig) return;
  s.ssig = ssig; s.sum.replaceChildren();
  for (const ms of times) { const i = document.createElement('i'); i.style.left = ((l.start + ms / 1000) * pps) + 'px'; s.sum.append(i); }
}
function propLabel(l, prop) {
  const m = /^fx\.([^.]+)\.(.+)$/.exec(prop);
  if (m) { const e = (l.effects || []).find(x => x.id === m[1]); return e ? t('effect.' + e.type + '.name') + ' · ' + t('effect.param.' + m[2]) : prop; }
  return t('field.' + prop);
}
function buildLane(l, prop) {
  const e = el('div', 'row lane'), left = el('div', 'left'), name = el('span', 'lane-name'); e.dataset.id = l.id; e.dataset.prop = prop;
  const ctl = KF.controls({ id: l.id, prop });
  left.append(name, ctl.nav);
  const strip = el('div', 'strip'); e.append(left, strip);
  const s = { id: l.id, prop, el: e, left, name, strip, ctl, sig: '' };
  strip.title = t('kf.laneTip');
  strip.addEventListener('pointerdown', ev => laneDown(ev, s));
  strip.addEventListener('dblclick', ev => { if (ev.target.closest('.kf')) KF.openEasePicker(ev.clientX, ev.clientY + 10); });
  strip.addEventListener('contextmenu', ev => {
    const d = ev.target.closest('.kf'); if (!d) return; ev.preventDefault();
    if (!KF.isKeySel(s.id, s.prop, parseFloat(d.dataset.t))) KF.setKeySel([{ id: s.id, prop: s.prop, t: parseFloat(d.dataset.t) }]);
    KF.openEasePicker(ev.clientX, ev.clientY + 10);
  });
  return s;
}
function updateLane(s, l, first) {
  const gi = l.group ? ginfo.get(l.group) : null;
  if (gi) s.el.style.setProperty('--grp', gi.css); else s.el.style.removeProperty('--grp');
  const cls = 'row lane t-' + l.type + (gi ? ' in-group' : '') + (S.selection.includes(l.id) ? ' selected' : '') + (first ? ' first' : '') + (l.locked ? ' locked' : '');
  if (s.el.className !== cls) s.el.className = cls;
  const label = (viewMode === 'tracks' ? l.name + ' · ' : '') + propLabel(l, s.prop); if (s.name.textContent !== label) { s.name.textContent = label; s.name.title = label; }
  s.strip.style.width = stripW() + 'px';
  const ks = KF.keysOf(l, s.prop), sel = KF.keySel().filter(k => k.id === l.id && k.prop === s.prop).map(k => k.t);
  const sig = JSON.stringify([ks.map(k => [k.t, KF.keyShape(k)]), l.start, pps, sel]);
  if (sig !== s.sig) {
    s.sig = sig; s.strip.replaceChildren();
    for (const k of ks) {
      const d = el('div', 'kf k-' + KF.keyShape(k) + (KF.isKeySel(l.id, s.prop, k.t) ? ' sel' : ''));
      d.style.left = ((l.start + k.t) * pps) + 'px'; d.dataset.t = String(k.t); d.dataset.id = l.id; d.dataset.prop = s.prop;
      d.title = `${fmt(l.start + k.t, 2)} ${t('unit.s')} · ${fmt(k.v, 3)}`;
      s.strip.append(d);
    }
  }
  s.ctl.refresh();
}
function laneDown(ev, s) {
  if (ev.button !== 0) return;
  const l = layer(s.id); if (!l) return;
  S.focus = 'layers';
  if (!S.selection.includes(l.id)) select([l.id]);
  const d = ev.target.closest('.kf');
  if (d) keyDrag(ev, s, parseFloat(d.dataset.t)); else marquee(ev);
}
const keyTimeOf = e => ({ id: e.dataset.id, prop: e.dataset.prop, t: parseFloat(e.dataset.t) });
function keyDrag(ev, s, t0) {
  const item = { id: s.id, prop: s.prop, t: t0 };
  if (ev.shiftKey || ev.metaKey || ev.ctrlKey) {   // toggle in / out of the selection
    const on = KF.isKeySel(s.id, s.prop, t0);
    KF.setKeySel(on ? KF.keySel().filter(k => !(k.id === s.id && k.prop === s.prop && Math.abs(k.t - t0) < 0.0006)) : [...KF.keySel(), item]); return;
  }
  if (!KF.isKeySel(s.id, s.prop, t0)) KF.setKeySel([item]);
  const l = layer(s.id); if (!l || l.locked) return;
  ev.preventDefault();
  const sel = KF.keySel().slice(), snap = new Map();
  for (const k of sel) {
    const arr = KF.keysOf(layer(k.id), k.prop), key = k.id + '|' + k.prop;
    if (!snap.has(key)) snap.set(key, { id: k.id, prop: k.prop, orig: JSON.parse(JSON.stringify(arr)), idx: new Set() });
    const i = arr.findIndex(x => Math.abs(x.t - k.t) < 0.0006); if (i >= 0) snap.get(key).idx.add(i);
  }
  const fps = S.project.fps || 30, x0 = ev.clientX, dragKey = 'kf' + (++dragCount);
  const own = new Set(sel.filter(k => k.id === s.id).map(k => k.prop + '|' + Math.round(k.t * 1000)));
  const targets = [S.t];
  for (const p of KF.animatedProps(l)) for (const k of KF.keysOf(l, p)) if (!own.has(p + '|' + Math.round(k.t * 1000)) && p !== s.prop) targets.push(l.start + k.t);   // other properties' keys (same-property snaps would make a step)
  const dur = k => { const z = layer(k.id); return z ? Math.max(0, z.end - z.start) : 0; };
  const lo = -Math.min(...sel.map(k => k.t)), hi = Math.min(...sel.map(k => dur(k) - k.t));
  let moved = false;
  const move = e => {
    if (!moved && Math.abs(e.clientX - x0) < 3) return;
    moved = true;
    let dt = (e.clientX - x0) / pps * (e.shiftKey ? 0.2 : 1);
    let g = l.start + t0 + dt, snapped = null;
    if (!e.shiftKey) {
      const sn = snapTo([g], targets);
      if (sn.target != null) { g += sn.diff; snapped = sn.target; } else g = Math.round(g * fps) / fps;
    }
    dt = clamp(g - (l.start + t0), lo, Math.max(lo, hi));
    change(P => {
      for (const en of snap.values()) {
        const L = P.layers.find(x => x.id === en.id); if (!L || !L.keys) continue;
        const moved = en.orig.filter((k, i) => en.idx.has(i)).map(k => round3(k.t + dt));
        // a moved key that lands on another key of its property replaces it (two keys at one time would be a step); built from the snapshot, so dragging away restores it
        const arr = en.orig.map((k, i) => (en.idx.has(i) ? { ...k, t: round3(k.t + dt) } : { ...k })).filter((k, i) => en.idx.has(i) || !moved.some(m => Math.abs(m - k.t) < 0.0006));
        arr.sort((a, b) => a.t - b.t); L.keys[en.prop] = arr;
      }
    }, { key: dragKey });
    KF.setKeySel(sel.map(k => ({ ...k, t: round3(k.t + dt) })));
    dom.snapLine.style.display = snapped != null ? 'block' : 'none';
    if (snapped != null) dom.snapLine.style.transform = `translateX(${LEFT + snapped * pps}px)`;
    showKeyTip(e, l.start + t0 + dt);
  };
  const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); document.body.classList.remove('dragging'); dom.snapLine.style.display = 'none'; dom.tip.hidden = true; };
  window.addEventListener('pointermove', move); window.addEventListener('pointerup', up); document.body.classList.add('dragging');
}
function showKeyTip(ev, time) {
  dom.tip.hidden = false; dom.tip.textContent = `${fmt(time, 3)} ${t('unit.s')}`;
  dom.tip.style.left = (ev.clientX + 12) + 'px'; dom.tip.style.top = (ev.clientY - 30) + 'px';
}
function marquee(ev) {
  const add = ev.shiftKey || ev.metaKey || ev.ctrlKey, base = add ? KF.keySel().slice() : [];
  if (!add) KF.setKeySel([]);
  const x0 = ev.clientX, y0 = ev.clientY; let box = null;
  const move = e => {
    if (!box) { if (Math.hypot(e.clientX - x0, e.clientY - y0) < 3) return; box = el('div', 'kf-marquee'); document.body.append(box); }
    const L = Math.min(x0, e.clientX), T = Math.min(y0, e.clientY), R = Math.max(x0, e.clientX), B = Math.max(y0, e.clientY);
    Object.assign(box.style, { left: L + 'px', top: T + 'px', width: (R - L) + 'px', height: (B - T) + 'px' });
    const hits = [];
    for (const d of dom.list.querySelectorAll('.kf')) { const r = d.getBoundingClientRect(); if (r.right >= L && r.left <= R && r.bottom >= T && r.top <= B) hits.push(keyTimeOf(d)); }
    const all = [...base]; for (const h of hits) if (!all.some(k => k.id === h.id && k.prop === h.prop && Math.abs(k.t - h.t) < 0.0006)) all.push(h);
    if (JSON.stringify(all) !== JSON.stringify(KF.keySel())) KF.setKeySel(all);
  };
  const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); if (box) box.remove(); };
  window.addEventListener('pointermove', move); window.addEventListener('pointerup', up);
}

// ---------------------------------------------------------------- group colour picker (right-click a group header): 8 swatches + automatic, one undo step
let groupPop = null;
function closeGroupPop() { if (groupPop) { groupPop.remove(); groupPop = null; document.removeEventListener('pointerdown', groupOutside, true); document.removeEventListener('keydown', groupEsc, true); } }
const groupOutside = e => { if (groupPop && !groupPop.contains(e.target)) closeGroupPop(); };
const groupEsc = e => { if (e.key === 'Escape') { e.stopPropagation(); closeGroupPop(); } };
function setGroupColor(name, color) {
  change(P => {
    const g = { ...(P.groups || {}) };
    if (color) g[name] = { color }; else delete g[name];
    if (Object.keys(g).length) P.groups = g; else delete P.groups;
  });
}
function openGroupPicker(x, y, name) {
  closeGroupPop();
  const box = el('div', 'kf-pop grp-pop'); groupPop = box;
  const info = ginfo.get(name);
  box.append(el('div', 'kf-pop-title', t('groups.title') + ' · ' + name));
  const grid = el('div', 'grp-swatches');
  for (const c of GROUP_SWATCHES) {
    const b = el('button', 'grp-sw' + (info && !info.auto && info.hex === c ? ' on' : '')); b.type = 'button'; b.style.setProperty('--c', c); b.dataset.color = c; b.title = c; b.setAttribute('aria-label', c);
    b.addEventListener('click', () => { setGroupColor(name, c); closeGroupPop(); });
    grid.append(b);
  }
  const auto = el('button', 'grp-auto', t('groups.auto')); auto.type = 'button';
  auto.addEventListener('click', () => { setGroupColor(name, null); closeGroupPop(); });
  box.append(grid, auto);
  document.body.append(box);
  const r = box.getBoundingClientRect();
  box.style.left = clamp(x, 8, window.innerWidth - r.width - 8) + 'px'; box.style.top = clamp(y, 8, window.innerHeight - r.height - 8) + 'px';
  setTimeout(() => { document.addEventListener('pointerdown', groupOutside, true); document.addEventListener('keydown', groupEsc, true); }, 0);
}

// ---------------------------------------------------------------- filmstrip (video thumbnails from /api/thumbs)
let filmTimer = null;
function filmstrip(src) {
  let f = filmstrips.get(src);
  if (f) return f.imgs.length ? f : null;
  f = { imgs: [], loaded: 0, failed: false };
  filmstrips.set(src, f);
  thumbs(src, 40).then(urls => {
    if (!urls) { f.failed = true; return; } // endpoint unavailable: bars stay plain
    f.imgs = urls.map(u => {
      const im = new Image();
      im.onload = () => { f.loaded++; clearTimeout(filmTimer); filmTimer = setTimeout(() => redraw(false), 120); };
      im.onerror = () => {};
      im.src = u; return im;
    });
  });
  return null;
}
function drawFilmstrip(x, l, w, h, f, tint) {
  const n = f.imgs.length; if (!n) return false;
  const tileW = Math.round(h * 16 / 9);
  const info = S.media.get(l.src);
  const srcDur = info && info.duration > 0 ? info.duration : l.src_in + (l.end - l.start);
  const frozen = isFrozen(l);
  let drew = false;
  for (let px = 0; px < w; px += tileW) {
    const srcT = frozen ? l.src_in : l.src_in + (px + tileW / 2) / pps;
    const idx = clamp(Math.floor(srcT / srcDur * n), 0, n - 1);
    const im = f.imgs[idx];
    if (!im || !im.complete || !im.naturalWidth) continue;
    const tw = Math.min(tileW, w - px);
    const ar = tw / h, ir = im.naturalWidth / im.naturalHeight;
    let sw = im.naturalWidth, sh = im.naturalHeight;
    if (ir > ar) sw = sh * ar; else sh = sw / ar; // cover
    x.drawImage(im, (im.naturalWidth - sw) / 2, (im.naturalHeight - sh) / 2, sw, sh, px, 0, tw, h);
    drew = true;
  }
  if (drew) { x.fillStyle = tint || tok('--cv-film-tint'); x.fillRect(0, 0, w, h); } // bar tint over the thumbnails (group hue when the layer is in a group)
  return drew;
}

// ---------------------------------------------------------------- bar drawing
const yOfDb = (db, h) => h - 2 - (clamp(db, DB_MIN, DB_MAX) + 40) / 52 * (h - 4);
const dbOfY = (y, h) => ((h - 2 - y) / (h - 4)) * 52 - 40;

function drawBar(s, l, w, wave, film, selected, gi) {
  const h = s.h || BAR_H, d = dpr();
  const cv = s.cv; const cw = Math.max(1, Math.round(w * d)), ch = Math.round(h * d);
  if (cv.width !== cw) cv.width = cw; if (cv.height !== ch) cv.height = ch;
  cv.style.width = w + 'px'; cv.style.height = h + 'px';
  const x = cv.getContext('2d'); x.setTransform(d, 0, 0, d, 0, 0); x.clearRect(0, 0, w, h);
  if (film) drawFilmstrip(x, l, w, h, film, gi ? rgba(gi.hex, 0.5) : null);
  if (wave && wave.peaks && HAS_AUDIO.has(l.type)) {
    const rate = wave.rate || 100, pk = wave.peaks, midY = h / 2;
    x.fillStyle = l.type === 'audio' ? (gi ? lighten(gi.hex, 0.35) : tok('--cv-wave-audio')) : tok('--cv-wave-video');
    for (let px = 0; px < w; px++) {
      const t0 = l.src_in + px / pps, t1 = t0 + 1 / pps;
      const i0 = Math.floor(t0 * rate), i1 = Math.max(i0, Math.ceil(t1 * rate) - 1);
      let m = 0; for (let i = i0; i <= i1 && i < pk.length; i++) if (pk[i] > m) m = pk[i];
      if (i0 >= pk.length) break;
      const a = Math.max(1, m * (h / 2 - 1));
      x.fillRect(px, midY - a, 1, a * 2);
    }
  }
  // fade ramps
  x.fillStyle = tok('--cv-fade');
  if (l.fade_in > 0) {
    const fw = Math.min(w, l.fade_in * pps);
    x.beginPath(); x.moveTo(0, 0); x.lineTo(fw, 0); x.lineTo(0, h); x.closePath(); x.fill();
  }
  if (l.fade_out > 0) {
    const fw = Math.min(w, l.fade_out * pps);
    x.beginPath(); x.moveTo(w, 0); x.lineTo(w - fw, 0); x.lineTo(w, h); x.closePath(); x.fill();
  }
  // volume envelope line (total gain in dB, −40..+12)
  if (HAS_AUDIO.has(l.type)) {
    const env = l.envelope;
    x.strokeStyle = env.length ? tok('--cv-env') : tok('--cv-env-dim'); x.lineWidth = 1.2; x.setLineDash(env.length ? [] : [3, 3]);
    x.beginPath();
    if (!env.length) { x.moveTo(0, yOfDb(l.volume_db, h)); x.lineTo(w, yOfDb(l.volume_db, h)); }
    else {
      x.moveTo(0, yOfDb(l.volume_db + env[0][1], h));
      for (const [tt, db] of env) x.lineTo((tt - l.start) * pps, yOfDb(l.volume_db + db, h));
      x.lineTo(w, yOfDb(l.volume_db + env[env.length - 1][1], h));
    }
    x.stroke(); x.setLineDash([]);
    x.fillStyle = tok('--cv-env-handle'); x.strokeStyle = tok('--cv-env-handle-ring'); x.lineWidth = 1;
    if (selected) for (const [tt, db] of env) { const px = (tt - l.start) * pps; if (px >= -3 && px <= w + 3) { x.beginPath(); x.arc(px, yOfDb(l.volume_db + db, h), 3, 0, 7); x.fill(); x.stroke(); } }
  }
  // label
  let label = '';
  if (l.type === 'text') label = String(l.text).replace(/\s*\n\s*/g, ' ').trim();
  else if (l.type === 'video' || l.type === 'image') label = basename(l.src) + (isFrozen(l) ? '  ·  ' + t('layers.frozen') : '');
  if (label && w > 24) {
    const soft = l.type === 'text' || l.type === 'image'; // light clips take dark text in the light theme
    x.font = '500 11px ' + tok('--font'); x.textBaseline = 'middle'; x.fillStyle = tok(soft ? '--cv-label-soft' : '--cv-clip-label');
    x.save(); x.beginPath(); x.rect(5, 0, w - 10, h); x.clip(); x.shadowColor = tok(soft ? '--cv-label-soft-shadow' : '--cv-clip-label-shadow'); x.shadowBlur = 3; x.shadowOffsetY = 1; x.fillText(label, 6, h / 2 + 0.5); x.restore();
  }
}

// ---------------------------------------------------------------- ruler
function drawRuler() {
  const w = stripW(), h = RULER_H, d = dpr();
  const cv = dom.rulerCanvas; cv.width = Math.round(w * d); cv.height = h * d; cv.style.width = w + 'px'; cv.style.height = h + 'px';
  dom.rulerStrip.style.width = w + 'px';
  const x = cv.getContext('2d'); x.setTransform(d, 0, 0, d, 0, 0); x.clearRect(0, 0, w, h);
  const steps = [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600];
  const major = steps.find(a => a * pps >= 64) || 600;
  const minor = major / 5;
  x.strokeStyle = tok('--cv-tick'); x.fillStyle = tok('--cv-tick-label'); x.font = '500 10.5px ' + tok('--font'); x.textBaseline = 'top'; x.lineWidth = 1;
  for (let tt = 0; tt <= S.project.duration + 1e-6; tt += minor) {
    const px = Math.round(tt * pps) + 0.5; const isMajor = Math.abs(tt / major - Math.round(tt / major)) < 1e-6;
    x.strokeStyle = isMajor ? tok('--cv-tick') : tok('--cv-tick-minor');
    x.beginPath(); x.moveTo(px, h); x.lineTo(px, isMajor ? h - 9 : h - 4); x.stroke();
    if (isMajor) x.fillText(rulerLabel(tt, major), px + 3, 3);
  }
  dom.noteHost.innerHTML = '';
  for (const n of S.project.notes) {
    const f = el('div', 'lp-note' + (n.done ? ' done' : '')); f.style.left = (n.t * pps) + 'px'; f.title = `${fmt(n.t, 1)} ${t('unit.s')} — ${n.text}` + (n.done ? ' ✓' : '');
    f.addEventListener('click', e => { e.stopPropagation(); setTime(n.t); });
    dom.noteHost.append(f);
  }
}
function rulerLabel(tt, major) { const m = Math.floor(tt / 60), s = tt - m * 60; if (major < 1) return `${m}:${s.toFixed(1).padStart(4, '0')}`; return `${m}:${String(Math.round(s)).padStart(2, '0')}`; }

function updateTime() {
  if (!S.project) return;
  const px = S.t * pps;
  dom.line.style.transform = `translateX(${LEFT + px}px)`;
  dom.head.style.transform = `translateX(${px}px)`;
  dom.rulerLeft.textContent = timecode(S.t);
  for (const l of lanes.values()) l.ctl.refresh();
  if (S.playing) { // follow the playhead
    const b = dom.body, gx = LEFT + px - b.scrollLeft;
    if (gx > b.clientWidth - 30 || gx < LEFT) b.scrollLeft = Math.max(0, px - (b.clientWidth - LEFT) * 0.15);
  }
}

/** Scroll the timeline sideways so the playhead is visible (Notes panel jumps). */
export function revealPlayhead() {
  if (!dom.body || !S.project) return;
  const b = dom.body, gx = LEFT + S.t * pps - b.scrollLeft;
  if (gx > b.clientWidth - 30 || gx < LEFT) b.scrollLeft = Math.max(0, S.t * pps - (b.clientWidth - LEFT) * 0.15);
}

// ---------------------------------------------------------------- selection: reveal the row in the panel
function onSelection() {
  redraw(false);
  const key = S.selection.join(',');
  if (key === lastSelectionKey) return; // e.g. reload after an external change: do not move the list
  lastSelectionKey = key;
  const id = S.selection[S.selection.length - 1]; if (!id) return;
  const l = layer(id); if (!l) return;
  if (l.group && S.collapsed.has(l.group)) { S.collapsed.delete(l.group); redraw(false); }
  const s = viewMode === 'tracks' ? clips.get(id) : rows.get(id); if (!s) return;
  const body = dom.body, br = body.getBoundingClientRect(), rr = (viewMode === 'tracks' ? s.el.closest('.row') || s.el : s.el).getBoundingClientRect();
  const top = br.top + RULER_H, bottom = br.bottom - 12; // sticky ruler above, scrollbar below
  if (rr.top < top) body.scrollTop -= (top - rr.top);
  else if (rr.bottom > bottom) body.scrollTop += (rr.bottom - bottom);
}

// ---------------------------------------------------------------- bar interaction
function snapTargets(exclude) {
  const h = [S.t, 0, S.project.duration];
  for (const n of S.project.notes) h.push(n.t);
  for (const l of S.project.layers) if (!exclude.has(l.id) && !l.hidden) h.push(l.start, l.end);
  return h;
}
function snapTo(candidates, targets) {
  if (!S.snap) return { diff: 0, target: null };
  const tol = 8 / pps; let best = null;
  for (const a of candidates) for (const h of targets) { const d = h - a; if (Math.abs(d) <= tol && (!best || Math.abs(d) < Math.abs(best.diff))) best = { diff: d, target: h }; }
  return best || { diff: 0, target: null };
}
const roundSnap = (v, snapped) => (snapped ? Math.round(v * 1000) / 1000 : Math.round(v * 100) / 100);
let dragCount = 0;

// ---- envelope points on the strip: drag = time + dB, double-click on the line = add, Alt-click = remove
function envelopeHit(s, l, ev) {
  const r = s.cv.getBoundingClientRect(); const x = ev.clientX - r.left, y = ev.clientY - r.top, h = s.h || BAR_H;
  for (let i = 0; i < l.envelope.length; i++) {
    const [tt, db] = l.envelope[i];
    const px = (tt - l.start) * pps, py = yOfDb(l.volume_db + db, h);
    if (Math.hypot(px - x, py - y) <= 6) return { kind: 'point', i, rect: r };
  }
  const tt = l.start + x / pps;
  if (Math.abs(y - yOfDb(l.volume_db + envelopeDb(l, tt), h)) <= 5) return { kind: 'line', t: tt, rect: r };
  return null;
}
function showTip(ev, tt, db) {
  dom.tip.hidden = false;
  dom.tip.textContent = `${fmt(tt, 2)} ${t('unit.s')} · ${fmt(db, 1)} dB`;
  dom.tip.style.left = (ev.clientX + 12) + 'px'; dom.tip.style.top = (ev.clientY - 30) + 'px';
}
function envelopeDrag(ev, id, hit) {
  const l0 = layer(id); const i = hit.i; const key = 'env' + (++dragCount);
  const y0 = ev.clientY - hit.rect.top, db0 = l0.envelope[i][1] + l0.volume_db;
  ev.preventDefault(); document.body.classList.add('dragging');
  const move = e => {
    const l = layer(id); if (!l || !l.envelope[i]) return;
    const r = hit.rect; const y = e.clientY - r.top;
    let tt = l.start + (e.clientX - r.left) / pps;
    if (S.snap && !e.altKey) { const sn = snapTo([tt], [S.t, l.start, l.end]); tt += sn.diff; }
    const lo = i > 0 ? l.envelope[i - 1][0] + 0.01 : l.start, hi = i < l.envelope.length - 1 ? l.envelope[i + 1][0] - 0.01 : l.end;
    tt = clamp(Math.round(tt * 100) / 100, Math.min(lo, hi), Math.max(lo, hi));
    const bh = barH(); const total = e.shiftKey ? db0 + (dbOfY(y, bh) - dbOfY(y0, bh)) * 0.2 : dbOfY(y, bh); // Shift = fine control
    const db = clamp(Math.round((total - l.volume_db) * 10) / 10, -60, 24);
    change(P => { const x = P.layers.find(z => z.id === id); if (x && x.envelope[i]) x.envelope[i] = [tt, db]; }, { key });
    showTip(e, tt, db);
  };
  const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); document.body.classList.remove('dragging'); dom.tip.hidden = true; };
  window.addEventListener('pointermove', move); window.addEventListener('pointerup', up);
}
function barHover(ev, id, s) {
  if (ev.buttons) return;
  const l = layer(id); if (!l || !HAS_AUDIO.has(l.type) || l.locked) { s.bar.style.cursor = ''; return; }
  const h = envelopeHit(s, l, ev);
  s.bar.style.cursor = h && h.kind === 'point' ? 'ns-resize' : '';
}
function barDoubleClick(ev, id, s) {
  const l = layer(id); if (!l || !HAS_AUDIO.has(l.type) || l.locked) return false;
  const h = envelopeHit(s, l, ev);
  if (!h || h.kind !== 'line') return false;
  const tt = Math.round(clamp(h.t, l.start, l.end) * 100) / 100, db = Math.round(envelopeDb(l, tt) * 10) / 10; // same value: shape unchanged
  change(P => { const x = P.layers.find(z => z.id === id); if (!x || x.envelope.some(p => Math.abs(p[0] - tt) < 0.005)) return; x.envelope.push([tt, db]); x.envelope.sort((a, b) => a[0] - b[0]); });
  return true;
}

function barPointerDown(e, id, s) {
  if (e.button !== 0) return;
  const l = layer(id); if (!l) return;
  S.focus = 'layers';
  // envelope points take priority over moving the bar
  if (HAS_AUDIO.has(l.type) && !l.locked) {
    const hit = envelopeHit(s, l, e);
    if (hit && hit.kind === 'point') {
      if (!S.selection.includes(id)) select([id]);
      if (e.altKey) { e.preventDefault(); change(P => { const x = P.layers.find(z => z.id === id); if (x) x.envelope.splice(hit.i, 1); }); return; }
      envelopeDrag(e, id, hit); return;
    }
  }
  const r = e.currentTarget.getBoundingClientRect();
  const px = e.clientX - r.left;
  const zone = px < EDGE ? 'left' : px > r.width - EDGE ? 'right' : 'move';
  if (e.shiftKey || isMod(e)) { select([id], true, { single: e.altKey }); return; }
  if (e.altKey ? !(S.selection.length === 1 && S.selection[0] === id) : !S.selection.includes(id)) select([id], false, { single: e.altKey });   // Alt = this clip only, not its linked partner
  if (l.locked) return;
  e.preventDefault();
  const ids = zone === 'move' ? S.selection.filter(i => { const z = layer(i); return z && !z.locked; })
    : e.altKey ? [id] : linkedWith(l).filter(z => !z.locked).map(z => z.id);          // a trim on a linked clip trims its partner by the same amount
  const first = new Map(ids.map(i => { const z = layer(i); return [i, { s: z.start, e: z.end, si: z.src_in ?? 0, type: z.type, frozen: isFrozen(z) }]; }));
  const x0 = e.clientX, key = 'bar' + (++dragCount);
  const targets = snapTargets(new Set(ids));
  const sourceDuration = () => { const b = S.media.get(l.src); return b && b.duration ? b.duration : null; };
  const f0 = first.get(id);
  let moved = false, plan = null;
  const y0 = e.clientY, pairDrag = zone === 'move' && ids.length > 1 && LK.isPairSelection(ids);
  const vertical = viewMode === 'tracks' && zone === 'move' && (ids.length === 1 || pairDrag), mine = vertical ? trackOf(id) : null;
  if (viewMode === 'tracks') freezeLayout();                      // rows keep their place while dragging; they are packed again on drop
  const move = ev => {
    let dt = (ev.clientX - x0) / pps;
    if (!moved && Math.hypot(ev.clientX - x0, ev.clientY - y0) < 3) return;
    moved = true;
    let snap = { diff: 0, target: null };
    if (zone === 'move') {
      const minStart = Math.min(...[...first.values()].map(v => v.s));
      if (minStart + dt < 0) dt = -minStart;
      snap = ev.altKey ? snap : snapTo([f0.s + dt, f0.e + dt], targets);
      dt += snap.diff; if (minStart + dt < 0) dt = -minStart;
      dt = roundSnap(dt, snap.target != null);
      change(P => { for (const [i, v] of first) { const z = P.layers.find(q => q.id === i); if (z) { z.start = round3(v.s + dt); z.end = round3(v.e + dt); } } }, { key });
    } else if (zone === 'left') {
      let ns = f0.s + dt; if (!ev.altKey) { const sn = snapTo([ns], targets); ns += sn.diff; snap = sn; }
      const sourced = f0.type === 'video' || f0.type === 'audio';
      ns = clamp(ns, 0, f0.e - 0.05);
      if (sourced && !f0.frozen && ns - f0.s < -f0.si) ns = f0.s - f0.si;
      ns = roundSnap(Math.max(0, ns), snap.target != null); const delta = ns - f0.s;
      change(P => { for (const [i, v] of first) { const z = P.layers.find(q => q.id === i); if (!z) continue; const src = v.type === 'video' || v.type === 'audio';
        z.start = i === id ? ns : round3(Math.max(0, v.s + delta)); if (src && !v.frozen) z.src_in = round3(Math.max(0, v.si + delta)); } }, { key });
    } else {
      let ne = f0.e + dt; if (!ev.altKey) { const sn = snapTo([ne], targets); ne += sn.diff; snap = sn; }
      ne = Math.max(f0.s + 0.05, ne);
      const sd = sourceDuration();
      if ((f0.type === 'video' || f0.type === 'audio') && !f0.frozen && sd) ne = Math.min(ne, f0.s + (sd - f0.si));
      ne = roundSnap(Math.max(f0.s + 0.05, ne), snap.target != null);
      change(P => { for (const [i, v] of first) { const z = P.layers.find(q => q.id === i); if (z) z.end = i === id ? ne : round3(Math.max(v.s + 0.05, v.e + (ne - f0.e))); } }, { key });
    }
    dom.snapLine.style.display = snap.target != null ? 'block' : 'none';
    if (snap.target != null) dom.snapLine.style.transform = `translateX(${LEFT + snap.target * pps}px)`;
    if (vertical) {
      const dy = ev.clientY - y0, cur = layer(id);
      s.el.classList.toggle('drag', Math.abs(dy) > 4); s.el.style.transform = Math.abs(dy) > 4 ? `translateY(${dy}px)` : '';
      plan = Math.abs(dy) > 8 && cur ? planTrack(ev.clientY, cur) : null;
      if (plan && pairDrag && plan.sec.layers.every(z => z.type === 'audio') !== (cur.type === 'audio')) plan = null;   // a linked clip stays among its own kind (picture / sound)
      if (plan && mine && plan.hit.si === mine.si && plan.hit.k === mine.k && plan.goal.track != null) plan = null;   // back on its own row: nothing to change
      paintPlan(plan);
    }
  };
  const up = () => {
    window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); window.removeEventListener('pointercancel', up);
    dom.snapLine.style.display = 'none'; document.body.classList.remove('dragging');
    if (vertical) { s.el.classList.remove('drag'); s.el.style.transform = ''; paintPlan(null); }
    if (plan) { S.lastTime = performance.now(); change(P => applyPlan(P, id, plan), { key }); }   // same key + fresh time: horizontal and vertical part are ONE undo step
    if (viewMode === 'tracks') { unfreezeLayout(); redraw(false); }
  };
  window.addEventListener('pointermove', move); window.addEventListener('pointerup', up); window.addEventListener('pointercancel', up);
  document.body.classList.add('dragging');
}

// ---------------------------------------------------------------- drop target for files dragged from the Media panel
/** Where would a drop at this screen point land? { time, before } (before = layer id it goes above, null = bottom) or null when outside the panel body. */
export function dropInfoAt(cx, cy, file) {
  if (!dom.body || !S.project) return null;
  const b = dom.body.getBoundingClientRect();
  if (cx < b.left || cx > b.right || cy < b.top || cy > b.bottom) return null;
  const time = clamp((cx - dom.rulerStrip.getBoundingClientRect().left) / pps, 0, S.project.duration);
  if (viewMode === 'tracks') {          // x = time, y = track: the z-order / group follow from the row under the pointer
    const m = file && S.media.get(file.path), dur = file && file.kind === 'image' ? 3 : m && m.duration > 0 ? m.duration : 5;
    const plan = planTrack(cy, { id: '\u0000drop', start: time, end: time + dur });
    if (!plan) return { time, before: undefined, plan: null };
    const { sec, rest, res } = plan, ls = S.project.layers;
    const before = res.index < rest.length ? rest[res.index].id : (ls[sec.from + rest.length] ? ls[sec.from + rest.length].id : null);
    return { time, before, group: sec.group || undefined, plan };
  }
  const rs = visibleOrder.map(i => ({ i, r: rows.get(i)?.el.getBoundingClientRect() })).filter(x => x.r);
  let j = rs.length;
  for (let n = 0; n < rs.length; n++) { if (cy < rs[n].r.top + rs[n].r.height / 2) { j = n; break; } }
  const listRect = dom.list.getBoundingClientRect();
  const y = j < rs.length ? rs[j].r.top - listRect.top : (rs.length ? rs[rs.length - 1].r.bottom - listRect.top : 0);
  return { time, before: j < rs.length ? rs[j].i : null, y };
}
export function showDropInfo(info) {
  if (!info) { dom.drop.style.display = 'none'; dom.snapLine.style.display = 'none'; dom.tip.hidden = true; paintPlan(null); return; }
  if (info.plan !== undefined) {
    paintPlan(info.plan);
    dom.snapLine.style.display = 'block'; dom.snapLine.style.transform = `translateX(${LEFT + info.time * pps}px)`; return;
  }
  dom.drop.style.display = 'block'; dom.drop.style.transform = `translateY(${info.y + dom.list.offsetTop - 1}px)`;
  dom.snapLine.style.display = 'block'; dom.snapLine.style.transform = `translateX(${LEFT + info.time * pps}px)`;
}

// ---------------------------------------------------------------- reorder (z-order)
function reorderDrag(e, id) {
  if (e.button !== 0) return;
  e.preventDefault();
  const l = layer(id); if (!l || l.locked) return;
  if (!S.selection.includes(id)) select([id]);
  const listRect = dom.list.getBoundingClientRect();
  let target = null;
  const rects = () => visibleOrder.map(i => ({ i, r: rows.get(i)?.el.getBoundingClientRect() })).filter(x => x.r);
  const move = ev => {
    const rs = rects(); let j = rs.length;
    for (let n = 0; n < rs.length; n++) { if (ev.clientY < rs[n].r.top + rs[n].r.height / 2) { j = n; break; } }
    target = j;
    const y = j < rs.length ? rs[j].r.top - listRect.top : (rs.length ? rs[rs.length - 1].r.bottom - listRect.top : 0);
    dom.drop.style.display = 'block'; dom.drop.style.transform = `translateY(${y + dom.list.offsetTop - 1}px)`;
  };
  const up = () => {
    window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); dom.drop.style.display = 'none'; document.body.classList.remove('dragging');
    if (target == null) return;
    change(P => {
      const arr = P.layers; const from = arr.findIndex(q => q.id === id); if (from < 0) return;
      let to;
      if (target < visibleOrder.length) to = arr.findIndex(q => q.id === visibleOrder[target]);
      else { const last = visibleOrder[visibleOrder.length - 1]; to = arr.findIndex(q => q.id === last) + 1; }
      const [m] = arr.splice(from, 1);
      if (to > from) to--;
      arr.splice(to, 0, m);
      const above = arr[to - 1], below = arr[to + 1];
      if (above && below && above.group === below.group) m.group = above.group; // dropped between two layers of a group: join it
    });
  };
  window.addEventListener('pointermove', move); window.addEventListener('pointerup', up);
  document.body.classList.add('dragging');
}

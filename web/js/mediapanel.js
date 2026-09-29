// mediapanel.js — left "Project" panel (Premiere-style): every media file of the project folder + the project's fonts.
// Media: search / sort / list-grid, thumbnails (video filmstrip scrub on hover), waveform for audio, usage badge,
// missing files, double-click or drag onto the layer panel / preview to add. Collapsible (rail) and resizable; state in localStorage.
import { S, select, layer, clamp, TYPE_ICON, change } from './state.js';
import * as API from './api.js';
import { addMedia, openFilePicker } from './import.js';
import { separateOnImport, setSeparateOnImport } from './links.js';
import { dropInfoAt, showDropInfo } from './layers.js';
import { ensureFont, fontStack } from './fonts.js';
import { t, getLang } from './i18n.js';
import { cv as tok } from './theme.js';
import { $, el, icon, setIcon } from './dom.js';

const MIN_W = 200, MAX_W = 360, DEF_W = 256, RAIL_W = 44;
const MEDIA_TYPES = new Set(['video', 'image', 'audio']);
const store = {
  get(k, d) { try { const v = localStorage.getItem('kurgu.mp.' + k); return v === null ? d : v; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem('kurgu.mp.' + k, String(v)); } catch (e) { /* ignore */ } },
};
const st = {
  open: store.get('open', '1') !== '0',
  width: clamp(parseInt(store.get('width', DEF_W), 10) || DEF_W, MIN_W, MAX_W),
  tab: store.get('tab', 'media') === 'fonts' ? 'fonts' : 'media',
  sort: ['name', 'type', 'recent'].includes(store.get('sort', 'name')) ? store.get('sort', 'name') : 'name',
  view: store.get('view', 'list') === 'grid' ? 'grid' : 'list',
  query: '',
  files: [], loaded: false,
  missing: new Map(),     // src -> true when referenced by the project but not readable
  selected: null,         // selected file path
  fresh: null,            // just imported
};
let root, body, toast = () => {}, listEl, badgeSig = '';
const items = new Map();  // path -> {el, badge, meta, ...}
let io = null;
const nrm = p => String(p || '').replace(/^\.\//, '');

// ---------------------------------------------------------------- layout (grid column of #main)
function applyLayout() {
  const main = $('main');
  main.style.setProperty('--mp-w', (st.open ? st.width : RAIL_W) + 'px');
  main.classList.toggle('mp-collapsed', !st.open);
  const b = $('b-panel'); if (b) b.classList.toggle('on', st.open);
}
export function openPanel() { if (!st.open) togglePanel(); }
export function togglePanel() {
  st.open = !st.open; store.set('open', st.open ? '1' : '0'); applyLayout();
  if (st.open) refresh();
}

// ---------------------------------------------------------------- usage
function usage() {
  const map = new Map();
  for (const l of S.project.layers) {
    if (!MEDIA_TYPES.has(l.type) || !l.src) continue;
    if (l.type === 'audio' && l.link && S.project.layers.some(v => v !== l && v.link === l.link && v.type === 'video' && v.src === l.src)) continue;   // a linked pair is ONE use
    const k = nrm(l.src); if (!map.has(k)) map.set(k, []); map.get(k).push(l.id);
  }
  return map;
}
const usageSig = () => S.project.layers.map(l => (MEDIA_TYPES.has(l.type) ? nrm(l.src) + '@' + l.id : '')).join('|');

// ---------------------------------------------------------------- data
async function refresh() {
  try { st.files = await API.listFiles(); st.loaded = true; } catch (e) { /* keep old list */ }
  await checkMissing();
  render();
}
async function checkMissing() {
  const known = new Set(st.files.map(f => f.path));
  const next = new Map();
  for (const src of usage().keys()) {
    if (known.has(src)) continue;
    // referenced but not in the project folder listing: absolute path elsewhere, or really gone
    const info = await API.mediaInfo(src);
    if (!info) next.set(src, true); else S.media.set(src, info);
  }
  st.missing = next;
}
const infoOf = path => S.media.get(path);
async function ensureInfo(path) {
  if (S.media.has(path)) return S.media.get(path);
  const i = await API.mediaInfo(path); if (i) S.media.set(path, i); return i;
}
const fmtDur = s => { s = Math.max(0, Math.round(s)); const m = Math.floor(s / 60); return m + ':' + String(s % 60).padStart(2, '0'); };
const sizeText = b => (b > 1e6 ? (b / 1e6).toFixed(1) + ' MB' : Math.max(1, Math.round((b || 0) / 1e3)) + ' KB');
const metaText = (f, info) => {
  const parts = [];
  if (info) {
    if (f.kind !== 'image' && info.duration > 0) parts.push(fmtDur(info.duration));
    if (f.kind !== 'audio' && info.width) parts.push(info.width + '×' + info.height);
  }
  if (!parts.length) parts.push(sizeText(f.size));
  return parts.join(' · ');
};

// ---------------------------------------------------------------- skeleton
export function startMediaPanel(host, toastFn) {
  root = host; toast = toastFn || toast;
  build();
  applyLayout();
  window.addEventListener('focus', () => { if (st.open) refresh(); });
  window.addEventListener('keydown', e => { if (e.key === 'Escape' && drag && drag.active) cancelDrag(); }, true);
  refresh();
  return {
    project() { if (usageSig() !== badgeSig) { badgeSig = usageSig(); updateBadges(); checkMissing().then(renderMissing); } },
    files() { st.fresh = S.lastImported || null; refresh().then(() => { const it = items.get(st.fresh); if (it) { it.el.scrollIntoView({ block: 'nearest' }); setTimeout(() => { st.fresh = null; it.el.classList.remove('fresh'); }, 3500); } }); },
    fonts() { if (st.tab === 'fonts') renderFonts(); },
    selection() { updateSelectionMarks(); },
    lang() { build(); render(); },
    theme() { if (st.tab === 'media') render(); },
  };
}

function build() {
  root.innerHTML = '';
  // collapsed rail
  const rail = el('div', 'mp-rail');
  const railBtn = el('button', 'icon-btn'); railBtn.append(icon('panel-left')); railBtn.title = t('mp.toggleTip'); railBtn.addEventListener('click', togglePanel);
  rail.append(railBtn, el('span', 'mp-rail-label', t('mp.title')));
  rail.addEventListener('click', e => { if (e.target === rail || e.target.classList.contains('mp-rail-label')) togglePanel(); });
  // header
  body = el('div', 'mp-body');
  const head = el('div', 'mp-head');
  const imp = el('button', 'ib mp-import'); imp.append(icon('plus')); imp.title = t('top.importTip'); imp.setAttribute('aria-label', t('mp.import')); imp.addEventListener('click', openFilePicker);
  const close = el('button', 'ib'); close.append(icon('chevron-left')); close.title = t('mp.collapseTip'); close.setAttribute('aria-label', t('mp.collapseTip')); close.addEventListener('click', togglePanel);
  // tabs (segmented control)
  const tabs = el('div', 'seg mp-tabs');
  for (const k of ['media', 'fonts']) {
    const b = el('button', 'mp-tab' + (st.tab === k ? ' on' : ''), t('mp.tab.' + k));
    b.addEventListener('click', () => { st.tab = k; store.set('tab', k); build(); render(); });
    tabs.append(b);
  }
  head.append(tabs, imp, close);
  body.append(head);
  if (st.tab === 'media') {
    const tools = el('div', 'mp-tools');
    const qbox = el('label', 'mp-searchbox'); qbox.append(icon('search', 's'));
    const q = el('input', 'mp-search'); q.type = 'search'; q.placeholder = t('mp.search'); q.value = st.query; q.spellcheck = false;
    q.addEventListener('input', () => { st.query = q.value; render(); });
    q.addEventListener('keydown', e => { e.stopPropagation(); if (e.key === 'Escape') { q.value = ''; st.query = ''; render(); q.blur(); } });
    const sort = el('select', 'mp-sort'); sort.title = t('mp.sortTip');
    for (const k of ['name', 'type', 'recent']) { const o = el('option', '', t('mp.sort.' + k)); o.value = k; sort.append(o); }
    sort.value = st.sort; sort.addEventListener('change', () => { st.sort = sort.value; store.set('sort', st.sort); render(); });
    const view = el('button', 'ib mp-view'); view.append(icon(st.view === 'grid' ? 'list' : 'layout-grid')); view.title = t(st.view === 'grid' ? 'mp.viewList' : 'mp.viewGrid');
    view.addEventListener('click', () => { st.view = st.view === 'grid' ? 'list' : 'grid'; store.set('view', st.view); build(); render(); });
    const sep = el('button', 'ib mp-sep' + (separateOnImport() ? ' on' : '')); sep.append(icon('link')); sep.type = 'button';
    const paintSep = () => { const on = separateOnImport(); sep.classList.toggle('on', on); sep.title = t(on ? 'link.importOn' : 'link.importOff'); sep.setAttribute('aria-label', sep.title); sep.setAttribute('aria-pressed', String(on)); };
    sep.addEventListener('click', () => { setSeparateOnImport(!separateOnImport()); paintSep(); }); paintSep();
    qbox.append(q); tools.append(qbox, sort, view, sep); body.append(tools);
  }
  listEl = el('div', 'mp-list ' + (st.tab === 'media' ? 'view-' + st.view : 'fonts'));
  body.append(listEl);
  if (st.tab === 'media') { const dh = el('button', 'mp-drophint'); dh.append(icon('upload'), el('span', '', t('drop.title'))); dh.addEventListener('click', openFilePicker); body.append(dh); }
  const grip = el('div', 'mp-resize'); grip.title = t('mp.resizeTip');
  grip.addEventListener('pointerdown', resizeStart);
  root.append(rail, body, grip);
  if (io) io.disconnect();
  io = new IntersectionObserver(entries => { for (const e of entries) if (e.isIntersecting) { io.unobserve(e.target); e.target._load && e.target._load(); } }, { root: listEl, rootMargin: '120px' });
}

function resizeStart(e) {
  e.preventDefault(); const grip = e.currentTarget; grip.setPointerCapture(e.pointerId);
  const left = root.getBoundingClientRect().left; document.body.classList.add('dragging');
  const move = ev => { st.width = clamp(Math.round(ev.clientX - left), MIN_W, MAX_W); applyLayout(); };
  const up = () => { grip.removeEventListener('pointermove', move); grip.removeEventListener('pointerup', up); document.body.classList.remove('dragging'); store.set('width', st.width); };
  grip.addEventListener('pointermove', move); grip.addEventListener('pointerup', up);
}

// ---------------------------------------------------------------- render
function render() { if (!S.project || !listEl) return; if (st.tab === 'fonts') renderFonts(); else renderMedia(); }

function sortFiles(a, b) {
  if (st.sort === 'type') return a.kind.localeCompare(b.kind) || a.path.localeCompare(b.path, getLang(), { numeric: true });
  if (st.sort === 'recent') return (b.mtime || 0) - (a.mtime || 0) || a.path.localeCompare(b.path, getLang(), { numeric: true });
  return a.path.localeCompare(b.path, getLang(), { numeric: true });
}
function renderMedia() {
  const keepScroll = listEl.scrollTop;
  listEl.innerHTML = ''; items.clear();
  listEl.append(el('div', 'mp-missing')); // filled by renderMissing
  const q = st.query.trim().toLocaleLowerCase(getLang());
  const shown = st.files.filter(f => MEDIA_TYPES.has(f.kind) && (!q || f.path.toLocaleLowerCase(getLang()).includes(q))).sort(sortFiles);
  if (!shown.length) listEl.append(el('div', 'empty mp-empty', st.files.some(f => MEDIA_TYPES.has(f.kind)) ? t('mp.noMatch') : (st.loaded ? t('mp.empty') : t('modal.loading'))));
  const byDir = new Map();
  for (const f of shown) { const i = f.path.lastIndexOf('/'); const dir = i < 0 ? '' : f.path.slice(0, i); if (!byDir.has(dir)) byDir.set(dir, []); byDir.get(dir).push(f); }
  const dirs = [...byDir.keys()].sort((a, b) => (a === '' ? -1 : b === '' ? 1 : a.localeCompare(b, getLang())));
  const use = usage();
  for (const dir of dirs) {
    const collapsed = st.collapsedDirs && st.collapsedDirs.has(dir);
    const h = el('div', 'mp-dir'); const arrow = el('span', 'arrow'); arrow.append(icon(collapsed ? 'chevron-right' : 'chevron-down', 's')); h.append(arrow, el('span', 'name', dir === '' ? t('mp.root') : dir), el('span', 'count', String(byDir.get(dir).length)));
    h.addEventListener('click', () => { st.collapsedDirs = st.collapsedDirs || new Set(); if (st.collapsedDirs.has(dir)) st.collapsedDirs.delete(dir); else st.collapsedDirs.add(dir); renderMedia(); });
    listEl.append(h);
    if (collapsed) continue;
    const box = el('div', 'mp-group'); listEl.append(box);
    for (const f of byDir.get(dir)) box.append(buildItem(f, use));
  }
  listEl.scrollTop = keepScroll;
  badgeSig = usageSig(); renderMissing();
}

function renderMissing() {
  const host = listEl && listEl.querySelector('.mp-missing'); if (!host || st.tab !== 'media') return;
  host.innerHTML = '';
  const use = usage();
  if (!st.missing.size) return;
  host.append(el('div', 'mp-missing-title', t('mp.missing', { n: st.missing.size })));
  for (const src of st.missing.keys()) {
    const ids = use.get(src) || []; if (!ids.length) continue;
    const row = el('div', 'mp-item missing');
    const warn = el('span', 'mp-warn'); warn.append(icon('triangle-alert', 's')); row.append(warn, el('span', 'name', src));
    const b = el('button', 'mp-badge', t('mp.used', { n: ids.length })); b.title = t('mp.badgeTip'); b.addEventListener('click', () => select(ids));
    row.append(b); host.append(row);
  }
}

function buildItem(f, use) {
  const it = el('div', 'mp-item kind-' + f.kind + (st.selected === f.path ? ' sel' : '') + (st.fresh === f.path ? ' fresh' : ''));
  it.dataset.path = f.path;
  const thumb = el('div', 'mp-thumb'); const cover = el('span', 'mp-kind'); cover.append(icon(TYPE_ICON[f.kind], 's'));
  thumb.append(cover);
  const text = el('div', 'mp-text'); const name = el('span', 'name', f.path.split('/').pop()); name.title = f.path;
  const meta = el('span', 'meta', metaText(f, infoOf(f.path)));
  text.append(name, meta);
  const badge = el('button', 'mp-badge'); badge.addEventListener('click', e => { e.stopPropagation(); const ids = usage().get(f.path) || []; if (ids.length) select(ids); });
  badge.addEventListener('pointerdown', e => e.stopPropagation()); badge.addEventListener('dblclick', e => e.stopPropagation());
  it.append(thumb, text, badge);
  const rec = { el: it, badge, meta, thumb, f };
  items.set(f.path, rec);
  setBadge(rec, (use.get(f.path) || []).length);
  // lazy: info + thumbnails only when scrolled into view
  it._load = () => loadItem(rec);
  io.observe(it);
  it.addEventListener('pointerdown', e => itemDown(e, rec));
  it.addEventListener('click', () => { if (justDragged) return; st.selected = f.path; updateSelectionMarks(); });
  it.addEventListener('dblclick', () => { if (justDragged) return; addMedia(f.kind, f.path); });
  it.addEventListener('contextmenu', e => { e.preventDefault(); st.selected = f.path; updateSelectionMarks(); contextMenu(e, f); });
  return it;
}
function setBadge(rec, n) {
  rec.badge.hidden = !n; rec.badge.textContent = n + '×'; rec.badge.title = t('mp.used', { n }) + ' — ' + t('mp.badgeTip');
}
function updateBadges() {
  const use = usage();
  for (const [p, rec] of items) setBadge(rec, (use.get(p) || []).length);
  renderMissing();
}
function updateSelectionMarks() { for (const [p, rec] of items) rec.el.classList.toggle('sel', st.selected === p); }

async function loadItem(rec) {
  const { f, thumb, meta } = rec;
  ensureInfo(f.path).then(i => { if (i) { meta.textContent = metaText(f, i); watchProxy(rec, i); } });
  if (f.kind === 'audio') {
    const cv = el('canvas', 'mp-wave'); thumb.append(cv);
    API.waveform(f.path).then(w => drawWave(cv, w));
    return;
  }
  const urls = await API.thumbs(f.path, f.kind === 'video' ? 40 : 1);
  const first = urls && urls[0] || API.mediaUrl(f.path);
  const img = el('img'); img.draggable = false; img.alt = ''; img.decoding = 'async'; img.src = first; thumb.prepend(img);
  rec.strip = f.kind === 'video' && urls && urls.length > 1 ? urls : null; rec.img = img;
  if (rec.strip) {
    let pre = false;
    rec.el.addEventListener('pointermove', e => {
      if (drag) return;
      if (!pre) { pre = true; for (const u of rec.strip) { const im = new Image(); im.src = u; } }
      const r = rec.el.getBoundingClientRect();
      const i = clamp(Math.floor((e.clientX - r.left) / r.width * rec.strip.length), 0, rec.strip.length - 1);
      if (rec.at !== i) { rec.at = i; img.src = rec.strip[i]; }
    });
    rec.el.addEventListener('pointerleave', () => { rec.at = 0; img.src = rec.strip[0]; });
  }
}
/** First-time preview copy of a big video is made in the background: show "preparing preview…" (with a bar when the server reports
 *  proxy_progress 0-1, else an indeterminate one) and poll /api/media-info until proxy_ready. */
function watchProxy(rec, info) {
  const { f, meta } = rec;
  if (f.kind !== 'video' || !info || !info.proxy || info.proxy_ready || rec.prep) return;
  const box = el('div', 'mp-prep'), bar = el('i'), lab = el('span');
  box.append(bar, lab); rec.el.querySelector('.mp-text').append(box); rec.prep = box;
  const paint = i => {
    const p = i && typeof i.proxy_progress === 'number' ? clamp(i.proxy_progress, 0, 1) : null;
    box.classList.toggle('indeterminate', p === null);
    bar.style.width = p === null ? '' : Math.round(p * 100) + '%';
    lab.textContent = p === null ? t('mp.preparing') : t('mp.preparingPct', { pct: Math.round(p * 100) });
  };
  paint(info);
  const t0 = Date.now();
  const tick = async () => {
    if (items.get(f.path) !== rec || Date.now() - t0 > 30 * 60 * 1000) return;
    const i = await API.mediaInfo(f.path);
    if (items.get(f.path) !== rec) return;
    if (i) S.media.set(f.path, i);
    if (!i || i.proxy_ready || !i.proxy) { box.remove(); rec.prep = null; if (i) meta.textContent = metaText(f, i); return; }
    paint(i); setTimeout(tick, 1500);
  };
  setTimeout(tick, 1500);
}
function drawWave(cv, w) {
  const peaks = w && w.peaks; const d = Math.min(2, window.devicePixelRatio || 1);
  const W = cv.clientWidth || 60, H = cv.clientHeight || 26; cv.width = Math.round(W * d); cv.height = Math.round(H * d);
  const x = cv.getContext('2d'); x.setTransform(d, 0, 0, d, 0, 0);
  if (!peaks || !peaks.length) return;
  x.fillStyle = tok('--cv-wave-audio'); const mid = H / 2;
  for (let i = 0; i < W; i++) {
    const a = Math.floor(i / W * peaks.length), b = Math.max(a + 1, Math.floor((i + 1) / W * peaks.length));
    let m = 0; for (let k = a; k < b && k < peaks.length; k++) { const v = peaks[k]; m = Math.max(m, Math.abs(Array.isArray(v) ? Math.max(Math.abs(v[0]), Math.abs(v[1])) : v)); }
    const h = Math.max(1, Math.min(1, m) * (H - 2)); x.fillRect(i, mid - h / 2, 1, h);
  }
}

// ---------------------------------------------------------------- context menu
function contextMenu(e, f) {
  const m = $('menu'); m.innerHTML = '';
  const ids = usage().get(f.path) || [];
  const add = (label, fn, disabled) => { const b = el('button', 'menu-item', label); if (disabled) { b.disabled = true; b.classList.add('disabled'); } b.addEventListener('click', () => { m.hidden = true; fn(); }); m.append(b); };
  add(t('mp.ctx.add'), () => addMedia(f.kind, f.path));
  add(t('mp.ctx.reveal') + (ids.length ? ' (' + ids.length + ')' : ''), () => select(ids), !ids.length);
  add(t('mp.ctx.copy'), () => copyText(f.path));
  if (f.kind === 'video') add((separateOnImport() ? '✓ ' : '') + t('link.importSetting'), () => setSeparateOnImport(!separateOnImport()));
  m.style.left = Math.min(e.clientX, window.innerWidth - 240) + 'px'; m.style.top = Math.min(e.clientY, window.innerHeight - 120) + 'px'; m.hidden = false;
}
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); }
  catch (e) { const ta = el('textarea'); ta.value = text; document.body.append(ta); ta.select(); try { document.execCommand('copy'); } catch (e2) { /* ignore */ } ta.remove(); }
  toast(t('mp.copied', { path: text }), 'ok', 1800);
}

// ---------------------------------------------------------------- drag from panel to layer panel / preview
let drag = null, justDragged = false;
function itemDown(e, rec) {
  if (e.button !== 0) return;
  drag = { rec, x0: e.clientX, y0: e.clientY, active: false, ghost: null, target: null };
  window.addEventListener('pointermove', dragMove); window.addEventListener('pointerup', dragUp); window.addEventListener('pointercancel', cancelDrag);
}
function targetAt(x, y) {
  const info = dropInfoAt(x, y, drag && drag.rec && drag.rec.f); if (info) return { kind: 'layers', info };
  const r = $('preview-section').getBoundingClientRect();
  if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) return { kind: 'preview' };
  return null;
}
function dragMove(e) {
  if (!drag) return;
  if (!drag.active) {
    if (Math.hypot(e.clientX - drag.x0, e.clientY - drag.y0) < 5) return;
    drag.active = true; document.body.classList.add('dragging');
    const g = el('div', 'mp-ghost'); const gk = el('span', 'mp-kind'); gk.append(icon(TYPE_ICON[drag.rec.f.kind], 's')); g.append(gk, el('span', '', drag.rec.f.path.split('/').pop()));
    document.body.append(g); drag.ghost = g;
  }
  drag.ghost.style.transform = `translate(${e.clientX + 12}px, ${e.clientY + 12}px)`;
  const tg = targetAt(e.clientX, e.clientY); drag.target = tg;
  showDropInfo(tg && tg.kind === 'layers' ? tg.info : null);
  const pv = $('preview-section'); pv.dataset.drop = t('mp.dropAtPlayhead'); pv.classList.toggle('mp-drop', !!tg && tg.kind === 'preview');
  drag.ghost.classList.toggle('no', !tg);
}
function endDrag() {
  window.removeEventListener('pointermove', dragMove); window.removeEventListener('pointerup', dragUp); window.removeEventListener('pointercancel', cancelDrag);
  if (drag && drag.ghost) drag.ghost.remove();
  showDropInfo(null); $('preview-section').classList.remove('mp-drop'); document.body.classList.remove('dragging');
}
function cancelDrag() { endDrag(); if (drag && drag.active) { justDragged = true; setTimeout(() => { justDragged = false; }, 50); } drag = null; }
function dragUp() {
  const d = drag; endDrag(); drag = null;
  if (!d || !d.active) return;
  justDragged = true; setTimeout(() => { justDragged = false; }, 50);
  const { f } = d.rec, tg = d.target; if (!tg) return;
  if (tg.kind === 'layers') addMedia(f.kind, f.path, { time: tg.info.time, before: tg.info.before, group: tg.info.group });
  else addMedia(f.kind, f.path);
}

// ---------------------------------------------------------------- fonts
function renderFonts() {
  listEl.innerHTML = '';
  const faces = S.fonts.filter(f => f.source === 'project');
  const note = el('div', 'mp-note', t('mp.fontsNote'));
  if (!faces.length) listEl.append(el('div', 'empty mp-empty', t('mp.fontsNone')));
  for (const f of faces) {
    ensureFont(f.id);
    const it = el('div', 'mp-font');
    const sample = el('div', 'sample', t('mp.fontSample')); sample.style.fontFamily = fontStack(f.id);
    it.append(sample, el('div', 'meta', f.family + (f.style ? ' · ' + f.style : '')));
    it.title = t('mp.fontTip');
    it.addEventListener('click', () => applyFont(f));
    listEl.append(it);
  }
  listEl.append(note);
}
function applyFont(f) {
  const targets = S.selection.map(layer).filter(l => l && l.type === 'text' && !l.locked);
  if (!targets.length) { toast(t('mp.fontPick'), 'warn', 2500); return; }
  const ids = new Set(targets.map(l => l.id));
  change(P => { for (const l of P.layers) if (ids.has(l.id)) l.font = f.id; });
  toast(t('mp.fontApplied', { name: f.family, n: targets.length }), 'ok', 2000);
}

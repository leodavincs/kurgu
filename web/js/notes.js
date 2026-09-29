// notes.js — Notes panel: a collapsible right-hand drawer listing every timeline note (thumbnail, timecode, text, author, done, layers under it).
// Click = the playhead jumps there. Data lives in project.notes (see SPEC §1.0); every edit goes through change() so it is one undo step
// and shows up in the change summary. Also exports addNote (used by the N box), jumpNote (Alt+→ / Alt+←) and toggleNotes (Shift+N).
// Styling: web/notes.css (Lumen tokens with fallbacks). Icons: ic() below uses the Lumen sprite when present, else a plain glyph.
import { S, change, newId, setTime, select, clamp } from './state.js';
import { pause } from './preview.js';
import { revealPlayhead } from './layers.js';
import { askAbout } from './ai.js';
import { t, fmt, getLang } from './i18n.js';
import { $, el } from './dom.js';

{ const link = document.createElement('link'); link.rel = 'stylesheet'; link.href = '/web/notes.css'; document.head.append(link); }

const MIN_W = 250, MAX_W = 520, DEF_W = 320, NEAR = 0.25;
const store = {
  get(k, d) { try { const v = localStorage.getItem('kurgu.np.' + k); return v === null ? d : v; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem('kurgu.np.' + k, String(v)); } catch (e) { /* ignore */ } },
};
const st = {
  open: store.get('open', '0') === '1',
  width: clamp(parseInt(store.get('width', DEF_W), 10) || DEF_W, MIN_W, MAX_W),
  filter: ['all', 'open', 'done'].includes(store.get('filter', 'all')) ? store.get('filter', 'all') : 'all',
  query: '', editing: null, current: null, reveal: null,
};
let root, listEl, countEl, filterBtns = {}, addInput, addTime, toast = () => {}, sig = '', io = null;
const rows = new Map();   // note id -> { el, sig, thumb }
const sorted = () => [...S.project.notes].sort((a, b) => a.t - b.t);
const rnd = v => Math.round(v * 100) / 100;
const nowIso = () => { const d = new Date(); return new Date(d - d.getTimezoneOffset() * 60000).toISOString().slice(0, 19); };
const tc = v => { const m = Math.floor(v / 60), s = v - m * 60; return `${m}:${s.toFixed(1).padStart(4, '0')}`; };

// ---------------------------------------------------------------- icons: Lumen sprite (<use href="#i-name">) when present, else a plain glyph
export function ic(name, glyph = '') {
  if (document.getElementById('i-' + name)) {
    const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); s.setAttribute('class', 'ic'); s.setAttribute('aria-hidden', 'true'); s.setAttribute('viewBox', '0 0 24 24');
    const u = document.createElementNS('http://www.w3.org/2000/svg', 'use'); u.setAttribute('href', '#i-' + name); s.append(u); return s;
  }
  return el('span', 'np-glyph', glyph);
}

// ---------------------------------------------------------------- data helpers
export const layersAt = (P, time) => P.layers.filter(l => !l.hidden && l.start <= time && time < l.end);
/** Add a user note at `at` (seconds); returns its id. */
export function addNote(text, at) {
  text = String(text || '').trim(); if (!text) return null;
  const id = newId('n'); at = rnd(at);
  change(p => { p.notes.push({ id, t: at, text, created: nowIso(), done: false, author: 'user', resolved_at: '', layer_ids: layersAt(p, at).map(l => l.id), reply: '' }); });
  st.reveal = id; if (st.filter === 'done') setFilter('all');
  return id;
}
const edit = (id, fn, key) => change(p => { const n = p.notes.find(x => x.id === id); if (n) fn(n); }, key ? { key } : {});
const toggleDone = n => edit(n.id, x => { x.done = !x.done; x.resolved_at = x.done ? nowIso() : ''; });
const remove = n => { change(p => { p.notes = p.notes.filter(x => x.id !== n.id); }); toast(t('np.deleted'), 'info', 3500); };

// ---------------------------------------------------------------- navigation
export function goTo(n, { focus = false } = {}) {
  if (!n) return;
  if (S.playing) pause();
  setTime(n.t); revealPlayhead(); st.current = n.id;
  markActive();
  const r = rows.get(n.id);
  if (r && st.open) { r.el.scrollIntoView({ block: 'nearest' }); if (focus) r.el.focus({ preventScroll: true }); }
}
const visibleNotes = () => (st.open ? filtered() : sorted());
/** Alt+→ / Alt+←: next / previous note (the filter of an open panel applies). */
export function jumpNote(dir) {
  const list = visibleNotes(), eps = 0.005;
  const n = dir > 0 ? list.find(x => x.t > S.t + eps) : [...list].reverse().find(x => x.t < S.t - eps);
  if (n) goTo(n); else toast(t(dir > 0 ? 'np.noNext' : 'np.noPrev'), 'info', 1400);
}
export function toggleNotes(force) {
  st.open = force === undefined ? !st.open : !!force; store.set('open', st.open ? '1' : '0'); applyLayout();
  if (st.open) { build(); markActive(); const cur = rows.get(st.current) || activeRows()[0]; if (cur) cur.el.scrollIntoView({ block: 'nearest' }); }
}
function applyLayout() {
  const main = $('main'); main.style.setProperty('--np-w', st.width + 'px'); main.classList.toggle('np-open', st.open);
  const b = $('b-notes'); if (b) { b.classList.toggle('on', st.open); b.setAttribute('aria-pressed', String(st.open)); }
}

// ---------------------------------------------------------------- frame thumbnails (lazy, cached per visual state)
const cache = new Map();  // `${t}|${visualSig}` -> blob URL
let visSig = '', queue = [], busy = 0, sigTimer = null;
const hash = str => { let h = 5381; for (let i = 0; i < str.length; i++) h = ((h << 5) + h + str.charCodeAt(i)) | 0; return (h >>> 0).toString(36); };
const visualSig = () => { const P = S.project; return hash(JSON.stringify([P.width, P.height, P.fps, P.background, P.global, P.layers])); };
function pump() {
  while (busy < 2 && queue.length) {
    const job = queue.shift(); busy++;
    fetch(`/api/frame?t=${job.tm.toFixed(3)}&v=${job.vs}`).then(r => (r.ok ? r.blob() : Promise.reject(new Error(r.status)))).then(b => {
      const url = URL.createObjectURL(b); cache.set(job.key, url);
      if (cache.size > 160) { const k = cache.keys().next().value; URL.revokeObjectURL(cache.get(k)); cache.delete(k); }
      job.done(url);
    }).catch(() => job.done(null)).finally(() => { busy--; pump(); });
  }
}
function loadThumb(th) {
  if (!th.visible) return;
  const key = `${th.tm}|${visSig}`;
  if (cache.has(key)) { th.set(cache.get(key)); return; }
  if (th.pending === key) return;
  th.pending = key;
  queue = queue.filter(j => j.th !== th);
  queue.push({ th, key, tm: th.tm, vs: visSig, done: url => { if (th.pending === key) th.pending = null; if (url && th.tm + '|' + visSig === key) th.set(url); else if (url) th.set(url, true); } });
  pump();
}
function makeThumb(tm) {
  const wrap = el('div', 'np-thumb'), img = el('img'); img.alt = ''; img.draggable = false; img.hidden = true;
  const th = { wrap, img, tm, visible: false, pending: null, set(url, stale) { if (!url) return; img.src = url; img.hidden = false; wrap.classList.toggle('stale', !!stale); } };
  wrap.append(img, el('span', 'np-tc', tc(tm))); wrap._th = th;
  if (io) io.observe(wrap);
  return th;
}

// ---------------------------------------------------------------- rows
const layerChips = n => {
  const box = el('div', 'np-chips'), ls = layersAt(S.project, n.t);
  for (const l of ls.slice(0, 4)) {
    const c = el('button', 'np-chip', l.name || l.type); c.dataset.type = l.type; c.title = l.name; c.type = 'button';
    c.addEventListener('click', e => { e.stopPropagation(); goTo(n); select([l.id]); });
    box.append(c);
  }
  if (ls.length > 4) box.append(el('span', 'np-chip more', '+' + (ls.length - 4)));
  if (!ls.length) box.append(el('span', 'np-chip none', t('np.noLayers')));
  return box;
};
function iconBtn(cls, name, glyph, tip, fn) {
  const b = el('button', 'np-ib ' + cls); b.type = 'button'; b.title = tip; b.setAttribute('aria-label', tip); b.append(ic(name, glyph));
  b.addEventListener('click', e => { e.stopPropagation(); fn(e); }); return b;
}
function rowSig(n) { return JSON.stringify([n.t, n.text, n.done, n.author, n.reply, layersAt(S.project, n.t).map(l => l.id + l.name + l.type), getLang(), st.editing === n.id]); }
function fillRow(r, n) {
  const e = r.el; e.className = 'np-row' + (n.done ? ' done' : '') + (n.author === 'agent' ? ' agent' : '') + (st.current === n.id ? ' current' : '');
  e.dataset.id = n.id; e.dataset.t = n.t; r.thumb.tm = n.t; r.thumb.wrap.querySelector('.np-tc').textContent = tc(n.t);
  const main = el('div', 'np-main');
  const top = el('div', 'np-top');
  const chk = el('button', 'np-check'); chk.type = 'button'; chk.setAttribute('role', 'checkbox'); chk.setAttribute('aria-checked', String(n.done)); chk.title = t(n.done ? 'np.reopen' : 'np.markDone');
  chk.append(ic('check', '✓')); chk.addEventListener('click', ev => { ev.stopPropagation(); toggleDone(n); });
  let body;
  if (st.editing === n.id) {
    body = el('textarea', 'np-edit'); body.rows = 2; body.value = n.text; body.spellcheck = false; let over = false;
    const finish = save => { if (over) return; over = true; const v = body.value.trim(); st.editing = null; if (save && v && v !== n.text) edit(n.id, x => { x.text = v; }); else refresh(true); };
    body.addEventListener('keydown', ev => { ev.stopPropagation(); if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); finish(true); } else if (ev.key === 'Escape') { ev.preventDefault(); finish(false); r.el.focus(); } });
    body.addEventListener('blur', () => finish(true));
    body.addEventListener('click', ev => ev.stopPropagation());
    setTimeout(() => { body.focus(); body.setSelectionRange(body.value.length, body.value.length); }, 0);
  } else {
    body = el('span', 'np-text', n.text);
    body.addEventListener('click', ev => { if (st.current === n.id && S.t === n.t) { ev.stopPropagation(); startEdit(n); } });
    body.addEventListener('dblclick', ev => { ev.stopPropagation(); startEdit(n); });
  }
  top.append(chk, body); main.append(top);
  const meta = el('div', 'np-meta');
  meta.append(el('span', 'np-badge ' + n.author, t(n.author === 'agent' ? 'np.agent' : 'np.you')));
  if (n.done) meta.append(el('span', 'np-state', t('np.done')));
  main.append(meta, layerChips(n));
  if (n.reply) { const rp = el('div', 'np-reply'); rp.append(ic('clapperboard', '›'), el('span', '', n.reply)); main.append(rp); }
  const acts = el('div', 'np-acts');
  acts.append(iconBtn('jump', 'arrow-right-to-line', '→', t('np.jump'), () => goTo(n)),
    iconBtn('edit', 'pencil', '✎', t('np.edit'), () => startEdit(n)),
    iconBtn('ask', 'clapperboard', '›', t('np.ask'), () => { goTo(n); askAbout(n.t, n.text); }),
    iconBtn('del', 'trash-2', '✕', t('np.delete'), () => remove(n)));
  e.replaceChildren(r.thumb.wrap, main, acts);
  r.sig = rowSig(n);
}
function startEdit(n) { st.editing = n.id; const r = rows.get(n.id); if (r) fillRow(r, n); }
function makeRow(n) {
  const e = el('div', 'np-row'); e.tabIndex = 0; e.setAttribute('role', 'listitem');
  const r = { el: e, sig: '', thumb: makeThumb(n.t) };
  e.addEventListener('click', () => goTo(n = S.project.notes.find(x => x.id === r.id) || n));
  r.id = n.id; return r;
}

// ---------------------------------------------------------------- list
function filtered() {
  const q = st.query.trim().toLowerCase();
  return sorted().filter(n => (st.filter === 'all' || (st.filter === 'done') === n.done)
    && (!q || n.text.toLowerCase().includes(q) || (n.reply || '').toLowerCase().includes(q) || layersAt(S.project, n.t).some(l => (l.name || '').toLowerCase().includes(q))));
}
function updateCount() {
  const done = S.project.notes.filter(n => n.done).length, open = S.project.notes.length - done;
  countEl.textContent = t('np.count', { open, done });
  for (const [k, b] of Object.entries(filterBtns)) { b.classList.toggle('on', st.filter === k); b.setAttribute('aria-pressed', String(st.filter === k)); }
}
function setFilter(f) { st.filter = f; store.set('filter', f); refresh(); }
/** Reconcile the DOM with project.notes (rows are kept and only rebuilt when their content changed). */
function refresh(force = false) {
  if (!root || !S.project || !st.open) return;
  updateCount();
  const all = new Map(S.project.notes.map(n => [n.id, n]));
  for (const [id, r] of rows) if (!all.has(id)) { if (io) io.unobserve(r.thumb.wrap); rows.delete(id); }
  const list = filtered(), seq = [];
  for (const n of list) {
    let r = rows.get(n.id); if (!r) { r = makeRow(n); rows.set(n.id, r); }
    const s = rowSig(n); if (force || s !== r.sig) fillRow(r, n);
    seq.push(r.el);
  }
  if (!list.length) { const empty = el('div', 'np-empty', t(S.project.notes.length ? 'np.noMatch' : 'np.empty')); listEl.replaceChildren(empty); }
  else if (listEl.children.length !== seq.length || seq.some((x, i) => listEl.children[i] !== x)) listEl.replaceChildren(...seq);
  addTime.textContent = tc(S.t);
  markActive();
  if (st.reveal) { const r = rows.get(st.reveal); if (r) { r.el.scrollIntoView({ block: 'nearest' }); st.reveal = null; } }
}
const activeRows = () => [...rows.values()].filter(r => r.el.classList.contains('active'));
let lastActive = '';
function markActive() {
  if (!root || !S.project || !st.open) return;
  const on = new Set(S.project.notes.filter(n => Math.abs(S.t - n.t) <= NEAR).map(n => n.id));
  for (const [id, r] of rows) { r.el.classList.toggle('active', on.has(id)); r.el.classList.toggle('current', st.current === id); }
  addTime.textContent = tc(S.t);
  const key = [...on].join(',');
  if (key !== lastActive) { lastActive = key; const f = activeRows()[0]; if (f && !f.el.contains(document.activeElement)) f.el.scrollIntoView({ block: 'nearest' }); }
}

// ---------------------------------------------------------------- build
function build() {
  if (root) return;
  root = el('aside'); root.id = 'notes-panel'; root.setAttribute('aria-label', t('np.title'));
  root.className = 'np';
  const grip = el('div', 'np-resize'); grip.title = t('np.resizeTip');
  grip.addEventListener('pointerdown', e => {
    e.preventDefault(); grip.setPointerCapture(e.pointerId); const x0 = e.clientX, w0 = st.width;
    const move = ev => { st.width = clamp(w0 + (x0 - ev.clientX), MIN_W, MAX_W); $('main').style.setProperty('--np-w', st.width + 'px'); };
    const up = () => { grip.removeEventListener('pointermove', move); grip.removeEventListener('pointerup', up); store.set('width', st.width); };
    grip.addEventListener('pointermove', move); grip.addEventListener('pointerup', up);
  });
  const head = el('div', 'np-head');
  head.append(el('span', 'np-title', t('np.title')));
  countEl = el('span', 'np-count'); head.append(countEl);
  const close = iconBtn('close', 'x', '✕', t('np.close'), () => toggleNotes(false)); head.append(close);
  const add = el('div', 'np-add'); addTime = el('span', 'np-add-t', tc(S.t));
  addInput = el('input', 'np-add-in'); addInput.placeholder = t('np.addPh'); addInput.spellcheck = false;
  addInput.addEventListener('keydown', e => { e.stopPropagation(); if (e.key === 'Enter') { const v = addInput.value.trim(); if (v) { addNote(v, S.t); addInput.value = ''; } } else if (e.key === 'Escape') addInput.blur(); });
  add.append(addTime, addInput);
  const tools = el('div', 'np-tools'), seg = el('div', 'np-seg'); seg.setAttribute('role', 'group');
  for (const k of ['all', 'open', 'done']) { const b = el('button', 'np-seg-b', t('np.f.' + k)); b.type = 'button'; b.addEventListener('click', () => setFilter(k)); filterBtns[k] = b; seg.append(b); }
  const search = el('input', 'np-search'); search.type = 'search'; search.placeholder = t('np.search'); search.spellcheck = false; search.setAttribute('aria-label', t('np.search'));
  search.addEventListener('input', () => { st.query = search.value; refresh(); });
  search.addEventListener('keydown', e => { e.stopPropagation(); if (e.key === 'Escape') { search.value = ''; st.query = ''; refresh(); search.blur(); } });
  tools.append(seg, search);
  listEl = el('div', 'np-list'); listEl.setAttribute('role', 'list');
  listEl.addEventListener('keydown', e => {
    const r = e.target.closest && e.target.closest('.np-row'); if (!r || e.target.tagName === 'TEXTAREA') return;
    const list = filtered(), i = list.findIndex(n => n.id === r.dataset.id); if (i < 0) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); e.stopPropagation(); const n = list[clamp(i + (e.key === 'ArrowDown' ? 1 : -1), 0, list.length - 1)]; goTo(n, { focus: true }); }
    else if (e.key === 'Enter' || e.key === 'F2') { e.preventDefault(); e.stopPropagation(); startEdit(list[i]); }
    else if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); e.stopPropagation(); const nx = list[i + 1] || list[i - 1]; remove(list[i]); if (nx) setTimeout(() => { const rr = rows.get(nx.id); if (rr) rr.el.focus(); }, 0); }
    else if (e.key === ' ' && e.target === r) { e.preventDefault(); e.stopPropagation(); toggleDone(list[i]); }
  });
  root.addEventListener('pointerdown', () => { S.focus = 'general'; }, true);
  root.append(grip, head, add, tools, listEl);
  $('main').append(root);
  io = new IntersectionObserver(entries => { for (const en of entries) { const th = en.target._th; if (!th) continue; th.visible = en.isIntersecting; if (th.visible) loadThumb(th); } }, { root: listEl, rootMargin: '120px' });
  for (const r of rows.values()) io.observe(r.thumb.wrap);
  visSig = visualSig();
  refresh(true);
}

// ---------------------------------------------------------------- module (main.js calls these through its change hooks)
export function startNotes({ toast: toastFn } = {}) {
  if (toastFn) toast = toastFn;
  const b = $('b-notes'); if (b) { const i = ic('list', ''); if (i.tagName === 'svg') b.prepend(i); b.addEventListener('click', () => toggleNotes()); }
  applyLayout();
  if (st.open) build();
  const project = () => {
    if (!root) return;
    const vs = visualSig();
    if (vs !== visSig) { visSig = vs; clearTimeout(sigTimer); sigTimer = setTimeout(() => { for (const r of rows.values()) loadThumb(r.thumb); }, 450); }
    refresh();
  };
  const lang = () => { if (root) { root.remove(); root = null; rows.clear(); filterBtns = {}; if (st.open) build(); } };
  return { project, time: markActive, lang };
}

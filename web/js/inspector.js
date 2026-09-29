// inspector.js — right panel: sections for the selected layer (time, transform, text, source, audio, transition, effects), project and notes.
// Number fields: drag the label horizontally to scrub (Shift ×10, Alt ×0.1); "," and "." both work as the decimal mark.
import { S, aspectLabel, change, select, setTime, layer, clamp, num, clone, VISUAL, HAS_AUDIO, typeName, basename, envelopeDb, removeLayers, duplicateLayers, isFrozen, linkedWith } from './state.js';
import * as LK from './links.js';
import { el, icon } from './dom.js';
import { t, fmt, parseNum } from './i18n.js';
import { ensureFont, fontStack } from './fonts.js';
import { openSequence } from './sequence.js';
import { mountAsk } from './ai.js';
import * as KF from './keyframes.js';

let root, sig = '', updaters = [], counter = 0;
const collapsed = new Set((() => { try { const raw = localStorage.getItem('kurgu.collapsedSections'); return raw === null ? ['transform2'] : JSON.parse(raw); } catch (e) { return []; } })()); // 'transform2' (anchor) starts folded
const mut = (id, fn, key) => change(P => { const l = P.layers.find(x => x.id === id); if (l && !l.locked) fn(l, P); }, { key });
const round = (v, n) => { const c = Math.pow(10, n); return Math.round(v * c) / c; };

export function startInspector(host) {
  root = host;
  document.addEventListener('focus-text', () => { const ta = root.querySelector('textarea.text-input'); if (ta) { ta.focus(); ta.select(); } });
  draw();
  return { project: draw, selection: draw, time: tick, media: draw, fonts: draw, lang: () => { sig = ''; draw(); } };
}

/** Playhead moved: fields of an animated layer show the value at the playhead, key buttons update. */
/** Selected layers; a whole linked pair counts as ONE (the primary = the last clicked clip), Premiere style. */
function pickIds() { const ids = S.selection.filter(layer); return LK.isPairSelection(ids) ? [ids[ids.length - 1]] : ids; }
function tick() {
  const ids = pickIds(); if (ids.length !== 1) return;
  const l = layer(ids[0]); if (!l || !(l.keys && Object.keys(l.keys).length)) return;
  for (const u of updaters) u();
}

function structureSignature() {
  const ids = pickIds();
  if (ids.length !== 1) return ids.join(',') + '|' + (ids.length ? '' : S.project.notes.map(n => n.id).join());
  const l = layer(ids[0]);
  return [l.id, l.type, l.locked, (l.effects || []).map(e => e.type).join(), (l.envelope || []).length, l.shadow?.enabled, cutNeighbour(l)?.id || '', !!S.media.get(l.src), S.fontMap.has(l.font), l.link || '', linkedWith(l).length, LK.outOfSync(l), Object.values(LK.available([l.id])).join(), S.selection.length].join('|');
}
function draw() {
  if (!S.project) return;
  const next = structureSignature();
  if (next !== sig) { sig = next; build(); return; }
  for (const u of updaters) u();
}

// ---------------------------------------------------------------- field helpers
function numberField({ label, read, write, unit = '', n = 2, step = 0.01, min = -1e9, max = 1e9, id, hint, kf, prop }) {
  const w = el('label', 'field');
  if (prop) ({ kf, read, write } = animArgs(id, prop));
  const lab = el('span', 'field-label', label); lab.title = (hint ? hint + ' — ' : '') + t('inspector.scrubTip');
  const inp = el('input', 'field-input'); inp.type = 'text'; inp.inputMode = 'decimal'; inp.spellcheck = false;
  w.append(lab, inp, el('span', 'field-unit', unit));
  const key = 'f:' + id + ':' + label;
  const apply = (v, k) => { v = clamp(round(v, n), min, max); write(v, k); };
  updaters.push(() => { if (document.activeElement !== inp) { const v = read(); inp.value = Number.isFinite(v) ? fmt(v, n) : ''; } });
  inp.value = fmt(read(), n);
  inp.addEventListener('focus', () => inp.select());
  inp.addEventListener('change', () => { const v = parseNum(inp.value); if (Number.isFinite(v)) apply(v, key); inp.value = fmt(read(), n); });
  inp.addEventListener('keydown', e => {
    if (e.key === 'Enter') inp.blur();
    else if (e.key === 'ArrowUp' || e.key === 'ArrowDown') { e.preventDefault(); apply(read() + (e.key === 'ArrowUp' ? 1 : -1) * step * (e.shiftKey ? 10 : 1), key); inp.value = fmt(read(), n); inp.select(); }
    else if (e.key === 'Escape') { inp.value = fmt(read(), n); inp.blur(); }
  });
  lab.addEventListener('pointerdown', e => {
    e.preventDefault(); lab.setPointerCapture(e.pointerId);
    const x0 = e.clientX, v0 = read(), sk = 'scrub' + (++counter);
    const move = ev => { const m = (ev.shiftKey ? 10 : 1) * (ev.altKey ? 0.1 : 1); apply(v0 + (ev.clientX - x0) * step * m, sk); };
    const up = () => { lab.removeEventListener('pointermove', move); lab.removeEventListener('pointerup', up); document.body.classList.remove('dragging'); };
    lab.addEventListener('pointermove', move); lab.addEventListener('pointerup', up); document.body.classList.add('dragging');
  });
  if (kf) {
    const ctl = KF.controls(kf); w.classList.add('kf-field'); w.dataset.prop = kf.prop || `fx:${kf.fx.index}:${kf.fx.param}`; w.prepend(ctl.sw); w.append(ctl.nav);
    updaters.push(() => ctl.refresh(w)); ctl.refresh(w);
  }
  return w;
}
/** read / write / kf of an animatable layer property: fields show the value at the playhead, edits are auto-keyed (keyframes.js). */
function animArgs(id, prop) {
  const spec = { id, prop };
  return { kf: spec, read: () => { const z = layer(id); return z ? KF.valueAt(z, prop) : 0; }, write: (v, key) => mut(id, z => KF.writeProp(z, prop, v), key) };
}
function fxArgs(id, index, param) {
  const spec = { id, fx: { index, param } };
  return { kf: spec, read: () => { const z = layer(id); return z ? KF.valueOfSpec(z, spec) : 0; }, write: (v, key) => mut(id, z => KF.writeSpec(z, spec, v), key) };
}
function slider({ read, write, min, max, step, id, label }) {
  const w = el('div', 'slider');
  const r = el('input'); r.type = 'range'; r.min = min; r.max = max; r.step = step;
  const key = 'r:' + id + ':' + label;
  const fill = () => r.style.setProperty('--p', ((parseFloat(r.value) - min) / (max - min) * 100) + '%'); // rail fill (CSS)
  r.addEventListener('input', () => { fill(); write(parseFloat(r.value), 'rs' + key); });
  updaters.push(() => { r.value = read(); fill(); });
  r.value = read(); fill(); w.append(r); return w;
}
/** Put the slider on the number field's row: label | slider | value. */
function withSlider(field, sl) { field.classList.add('has-slider'); field.insertBefore(sl, field.querySelector('input')); return field; }
function checkbox(label, read, write) {
  const w = el('label', 'check'); const c = el('input'); c.type = 'checkbox'; c.checked = !!read();
  c.addEventListener('change', () => write(c.checked)); w.append(c, el('span', '', label));
  updaters.push(() => { c.checked = !!read(); }); return w;
}
function selectField({ label, options, read, write }) {
  const w = el('label', 'field select'); w.append(el('span', 'field-label', label));
  const s = el('select', 'field-input');
  for (const [v, name] of options) { const o = el('option', '', name); o.value = v; s.append(o); }
  s.value = read(); s.addEventListener('change', () => write(s.value)); updaters.push(() => { if (document.activeElement !== s) s.value = read(); });
  w.append(s); return w;
}
function colorField(label, read, write, id) {
  const w = el('label', 'field color'); w.append(el('span', 'field-label', label));
  const c = el('input'); c.type = 'color'; const tx = el('input', 'field-input'); tx.type = 'text'; tx.spellcheck = false;
  const hex = v => { v = String(v || '#000000'); return /^#[0-9a-f]{6}$/i.test(v) ? v : /^#[0-9a-f]{3}$/i.test(v) ? '#' + v.slice(1).split('').map(h => h + h).join('') : '#000000'; };
  const key = 'c:' + id + ':' + label;
  const refresh = () => { const v = read(); c.value = hex(v); if (document.activeElement !== tx) tx.value = String(v || ''); };
  c.addEventListener('input', () => write(c.value, key));
  tx.addEventListener('change', () => { let v = tx.value.trim(); if (/^[0-9a-f]{6}$/i.test(v)) v = '#' + v; if (/^#[0-9a-f]{3}([0-9a-f]{3})?$/i.test(v)) write(hex(v), key); refresh(); });
  tx.addEventListener('keydown', e => { if (e.key === 'Enter') tx.blur(); });
  updaters.push(refresh); refresh(); w.append(c, tx); return w;
}
function section(id, title) {
  const wrap = el('section', 'section'); const head = el('div', 'section-head'); const arrow = el('span', 'arrow'); arrow.append(icon('chevron-down', 's')); head.append(el('span', '', title), arrow);
  const body = el('div', 'section-body'); wrap.append(head, body);
  const closed = collapsed.has(id);
  wrap.classList.toggle('collapsed', closed);
  head.addEventListener('click', () => {
    const c = wrap.classList.toggle('collapsed');
    if (c) collapsed.add(id); else collapsed.delete(id);
    try { localStorage.setItem('kurgu.collapsedSections', JSON.stringify([...collapsed])); } catch (e) { /* ignore */ }
  });
  root.append(wrap); return body;
}
const pair = (...c) => { const r = el('div', 'pair'); r.append(...c); return r; };
const button = (label, fn, cls = '') => { const b = el('button', 'pbtn ' + cls, label); b.addEventListener('click', fn); return b; };
const readonly = (label, value) => { const w = el('div', 'field readonly'); w.append(el('span', 'field-label', label), el('span', 'readonly-value', value)); return w; };

// ---------------------------------------------------------------- time helpers
export function nudgeLayers(ids, d) {
  const ok = ids.map(layer).filter(l => l && !l.locked); if (!ok.length) return;
  const min = Math.min(...ok.map(l => l.start)); if (min + d < 0) d = -min;
  change(P => { for (const g of ok) { const l = P.layers.find(x => x.id === g.id); if (l) { l.start = round(g.start + d, 3); l.end = round(g.end + d, 3); } } });
}
/** The next layer of the same group that starts where this one ends (for the rolling edit). */
export function cutNeighbour(l) {
  if (!l || !l.group) return null;
  return S.project.layers.find(b => b.id !== l.id && b.group === l.group && !b.locked && Math.abs(b.start - l.end) < 0.02) || null;
}
function rollingEdit(aId, d) {
  change(P => {
    const a = P.layers.find(x => x.id === aId); if (!a || a.locked) return;
    const b = P.layers.find(x => x.id !== a.id && x.group === a.group && !x.locked && Math.abs(x.start - a.end) < 0.02); if (!b) return;
    const sourced = b.type === 'video' || b.type === 'audio';
    if (a.end + d < a.start + 0.05) d = a.start + 0.05 - a.end;
    if (b.start + d > b.end - 0.05) d = b.end - 0.05 - b.start;
    if (sourced && !isFrozen(b) && b.src_in + d < 0) d = -b.src_in;
    a.end = round(a.end + d, 3); b.start = round(b.start + d, 3);
    if (sourced && !isFrozen(b)) b.src_in = round(b.src_in + d, 3);
  });
}
function nudgeButtons(fn) {
  const r = el('div', 'nudge');
  for (const [key, d] of [['inspector.nudge.m1', -1], ['inspector.nudge.m01', -0.1], ['inspector.nudge.p01', 0.1], ['inspector.nudge.p1', 1]]) r.append(button(t(key), () => fn(d)));
  return r;
}

// ---------------------------------------------------------------- build
function build() {
  root.innerHTML = ''; updaters = [];
  const ids = pickIds();
  if (ids.length === 0) return buildProject();
  if (ids.length > 1) return buildMulti(ids);
  buildLayer(layer(ids[0]));
}

/** Project section: frame size (click to open Sequence settings) + frame rate + the settings button. */
function seqRows(P) {
  const w = el('div');
  const size = el('div', 'field readonly'); const v = el('button', 'link', ''); v.style.paddingLeft = '0'; v.title = t('seq.openTip');
  size.append(el('span', 'field-label', t('proj.resolution')), v); v.addEventListener('click', openSequence);
  const fps = readonly(t('proj.fps'), '');
  updaters.push(() => { const q = S.project; v.textContent = `${q.width} × ${q.height} · ${aspectLabel(q.width, q.height)}`; fps.querySelector('.readonly-value').textContent = String(q.fps); });
  v.textContent = `${P.width} × ${P.height} · ${aspectLabel(P.width, P.height)}`; fps.querySelector('.readonly-value').textContent = String(P.fps);
  const open = button(t('seq.open'), openSequence, 'wide'); open.style.marginTop = '4px';
  w.append(size, fps, open); return w;
}
function buildProject() {
  const P = S.project;
  const head = el('div', 'panel-head'); head.append(el('span', 'name', t('inspector.project')), el('span', 'sub', t('inspector.noSelection'))); root.append(head);
  let body = section('project', t('sec.project'));
  const name = el('label', 'field'); name.append(el('span', 'field-label', t('field.name'))); const nameIn = el('input', 'field-input'); nameIn.value = P.name; nameIn.style.textAlign = 'left';
  nameIn.addEventListener('change', () => change(p => { p.name = nameIn.value.trim() || p.name; }, { key: 'project-name' }));
  updaters.push(() => { if (document.activeElement !== nameIn) nameIn.value = S.project.name; }); name.append(nameIn);
  body.append(name,
    numberField({ label: t('proj.duration'), read: () => S.project.duration, write: (v, key) => change(p => { p.duration = Math.max(1, v); }, { key }), unit: t('unit.s'), n: 2, step: 0.1, min: 1, id: 'project' }),
    seqRows(P),
    colorField(t('proj.background'), () => S.project.background, (v, key) => change(p => { p.background = v; }, { key }), 'project'));
  body = section('globalFx', t('sec.globalFx'));
  const g = (k, v, key) => change(p => { p.global[k] = v; }, { key });
  body.append(
    withSlider(numberField({ label: t('global.vignette'), read: () => S.project.global.vignette, write: (v, key) => g('vignette', v, key), n: 2, step: 0.01, min: 0, max: 1, id: 'global' }),
      slider({ read: () => S.project.global.vignette, write: (v, key) => g('vignette', v, key), min: 0, max: 1, step: 0.01, id: 'global', label: 'vignette' })),
    numberField({ label: t('global.grain'), read: () => S.project.global.grain, write: (v, key) => g('grain', v, key), n: 1, step: 0.1, min: 0, max: 10, id: 'global', hint: t('global.grainHint') }),
    numberField({ label: t('global.fade_out'), read: () => S.project.global.fade_out, write: (v, key) => g('fade_out', v, key), unit: t('unit.s'), n: 2, step: 0.1, min: 0, max: 30, id: 'global' }),
    numberField({ label: t('global.loudness_lufs'), read: () => S.project.global.loudness_lufs ?? -14, write: (v, key) => g('loudness_lufs', v, key), unit: 'LUFS', n: 1, step: 0.5, min: -40, max: 0, id: 'global' }),
    checkbox(t('global.normalize'), () => S.project.global.loudness_lufs !== null, v => change(p => { p.global.loudness_lufs = v ? -14 : null; })));
  mountAsk(section('ai', t('ai.section')));
  body = section('notes', t('sec.notes'));
  if (!P.notes.length) body.append(el('div', 'empty', t('notes.empty')));
  [...P.notes].sort((a, b) => a.t - b.t).forEach(n => {
    const r = el('div', 'note-row'); const tm = el('button', 'note-time', `${fmt(n.t, 1)} ${t('unit.s')}`); const tx = el('span', 'note-text', n.text);
    const del = el('button', 'icon-btn'); del.append(icon('x', 's')); del.title = t('notes.delete');
    tm.addEventListener('click', () => setTime(n.t)); tx.addEventListener('click', () => setTime(n.t));
    del.addEventListener('click', () => change(p => { p.notes = p.notes.filter(x => x.id !== n.id); }));
    r.append(tm, tx, del); body.append(r);
  });
}

function buildMulti(ids) {
  const head = el('div', 'panel-head'); head.append(el('span', 'name', t('inspector.multi', { n: ids.length })), el('span', 'sub', t('inspector.batch'))); root.append(head);
  let body = section('time', t('sec.time'));
  body.append(el('div', 'hint', t('inspector.moveTogether')), nudgeButtons(d => nudgeLayers(S.selection, d)));
  body = section('actions', t('sec.actions'));
  const av = LK.available(S.selection);
  if (av.link || av.unlink) body.append(pair(...[av.link ? button(t('link.link'), () => LK.linkLayers(S.selection)) : null, av.unlink ? button(t('link.unlink'), () => LK.unlinkLayers(S.selection)) : null].filter(Boolean)));
  if (av.resync) body.append(button(t('link.resync'), () => LK.resync(S.selection), 'wide'));
  body.append(pair(button(t('inspector.duplicate'), () => duplicateLayers(S.selection)), button(t('inspector.delete'), () => removeLayers(S.selection), 'danger')));
  mountAsk(section('ai', t('ai.section')));
}

// ---------------------------------------------------------------- font picker (grouped: Project / Built-in / System, live preview)
let fontMenu = null;
function closeFontMenu() { if (fontMenu) { fontMenu.remove(); fontMenu = null; document.removeEventListener('pointerdown', outside, true); document.removeEventListener('keydown', escClose, true); } }
const outside = e => { if (fontMenu && !fontMenu.contains(e.target) && !e.target.closest('.font-btn')) closeFontMenu(); };
const escClose = e => { if (e.key === 'Escape') { e.stopPropagation(); closeFontMenu(); } };
const faceLabel = f => f.family + (f.style && !/^regular$/i.test(f.style) ? ' ' + f.style : '');

function openFontMenu(anchor, currentId, pick) {
  closeFontMenu();
  const menu = el('div', 'font-menu'); fontMenu = menu;
  const search = el('input', 'font-search'); search.placeholder = t('font.search'); search.spellcheck = false;
  const list = el('div', 'font-list');
  menu.append(search, list);
  const io = new IntersectionObserver(entries => {
    for (const en of entries) if (en.isIntersecting) { const b = en.target; io.unobserve(b); ensureFont(b.dataset.id); b.firstChild.style.fontFamily = fontStack(b.dataset.id); }
  }, { root: list, rootMargin: '120px' });
  const render = () => {
    const q = search.value.trim().toLocaleLowerCase(); list.innerHTML = '';
    for (const src of ['project', 'bundled', 'system']) {
      const faces = S.fonts.filter(f => f.source === src && (!q || faceLabel(f).toLocaleLowerCase().includes(q))).sort((a, b) => a.family.localeCompare(b.family) || (a.weight || 400) - (b.weight || 400) || (a.italic ? 1 : 0) - (b.italic ? 1 : 0));
      if (!faces.length) continue;
      list.append(el('div', 'font-group', t('font.group.' + src) + ' · ' + faces.length));
      for (const f of faces) {
        const b = el('button', 'font-item' + (f.id === currentId ? ' current' : '')); b.append(el('span', 'font-item-name', faceLabel(f))); if (f.id === currentId) b.append(icon('check', 's')); b.dataset.id = f.id;
        b.addEventListener('click', () => { closeFontMenu(); pick(f.id); });
        list.append(b); io.observe(b);
      }
    }
    if (!list.children.length) list.append(el('div', 'empty', t('font.none')));
  };
  render();
  search.addEventListener('input', render);
  search.addEventListener('keydown', e => { e.stopPropagation(); if (e.key === 'Enter') { const b = list.querySelector('.font-item'); if (b) b.click(); } else if (e.key === 'Escape') closeFontMenu(); });
  document.body.append(menu);
  const r = anchor.getBoundingClientRect(); const w = Math.max(280, r.width);
  menu.style.width = w + 'px'; menu.style.left = clamp(r.right - w, 8, window.innerWidth - w - 8) + 'px';
  const h = Math.min(420, window.innerHeight - 24); menu.style.maxHeight = h + 'px';
  menu.style.top = clamp(r.bottom + 4, 8, window.innerHeight - h - 8) + 'px';
  const cur = list.querySelector('.current'); if (cur) list.scrollTop = Math.max(0, cur.offsetTop - list.clientHeight / 2);
  search.focus();
  setTimeout(() => { document.addEventListener('pointerdown', outside, true); document.addEventListener('keydown', escClose, true); }, 0);
}
function fontField(id) {
  const w = el('div', 'field font'); w.append(el('span', 'field-label', t('field.font')));
  const btn = el('button', 'font-btn'); const nameSpan = el('span', 'font-btn-name'); const arr = el('span', 'arrow'); arr.append(icon('chevron-down', 's')); btn.append(nameSpan, arr);
  const refresh = () => {
    const l = layer(id); if (!l) return;
    const f = S.fontMap.get(l.font);
    nameSpan.textContent = f ? faceLabel(f) : l.font;
    nameSpan.title = f ? '' : t('font.missing', { id: l.font });
    nameSpan.classList.toggle('missing', !f);
    ensureFont(l.font); nameSpan.style.fontFamily = fontStack(l.font);
  };
  btn.addEventListener('click', () => { if (fontMenu) closeFontMenu(); else openFontMenu(btn, layer(id).font, fid => mut(id, z => { z.font = fid; })); });
  updaters.push(refresh); refresh(); w.append(btn); return w;
}

// ---------------------------------------------------------------- stretch (scale_x / scale_y) with a chain toggle
// Drawn scale = scale × scale_x (x) and scale × scale_y (y) (SPEC §1.8). Linked = the two multipliers are edited together (a uniform stretch),
// unlinked = independent. Linked is derived from the data (equal values) unless the user opened the chain for this layer.
const unlinked = new Set();
const CHAIN_ON = '<rect x="2" y="8" width="12" height="8" rx="4"/><rect x="10" y="8" width="12" height="8" rx="4"/>';
const CHAIN_OFF = '<rect x="1" y="8" width="10" height="8" rx="4"/><rect x="13" y="8" width="10" height="8" rx="4"/>';
function stretchPair(id) {
  const wrap = el('div', 'kf-linkpair');
  const isLinked = z => !unlinked.has(id) && Math.abs(KF.valueAt(z, 'scale_x') - KF.valueAt(z, 'scale_y')) < 1e-6;
  const twin = prop => (prop === 'scale_x' ? 'scale_y' : 'scale_x');
  const field = (prop, label) => {
    const a = animArgs(id, prop);
    return numberField({ label, kf: a.kf, read: a.read, n: 3, step: 0.005, min: -30, max: 30, id,
      write: (v, key) => mut(id, z => { const both = isLinked(z); KF.writeProp(z, prop, v); if (both) KF.writeProp(z, twin(prop), v); }, key) });
  };
  const chain = el('button', 'kf-chain'); chain.type = 'button';
  const ic = () => { const l = layer(id); const on = !l || isLinked(l); chain.classList.toggle('on', on); chain.title = t(on ? 'stretch.linked' : 'stretch.unlinked'); chain.setAttribute('aria-label', chain.title); chain.setAttribute('aria-pressed', String(on));
    chain.replaceChildren(); const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); s.setAttribute('class', 'ic s'); s.setAttribute('viewBox', '0 0 24 24'); s.innerHTML = on ? CHAIN_ON : CHAIN_OFF; chain.append(s); };
  chain.addEventListener('click', e => {
    e.preventDefault(); const l = layer(id); if (!l) return;
    if (isLinked(l)) { unlinked.add(id); } else { unlinked.delete(id); mut(id, z => KF.writeProp(z, 'scale_y', KF.valueAt(z, 'scale_x'))); }
    ic();
  });
  updaters.push(ic); ic();
  wrap.append(field('scale_x', t('field.scale_x')), field('scale_y', t('field.scale_y')), chain);
  return wrap;
}

// ---------------------------------------------------------------- single layer
function buildLayer(l) {
  const id = l.id;
  const head = el('div', 'panel-head'); head.append(el('span', 'name', l.name), el('span', 'sub', typeName(l.type))); root.append(head);
  if (l.locked) { const ln = el('div', 'lock-note'); ln.append(icon('lock', 's'), el('span', '', t('inspector.lockedNote'))); root.append(ln); }
  const fields = el('fieldset', 'lock-set'); fields.disabled = !!l.locked; root.append(fields);
  const add = (sid, title) => { const b = section(sid, title); fields.append(b.parentElement); return b; };
  const read = f => () => (layer(id) ? layer(id)[f] : 0);
  const writer = f => (v, key) => mut(id, z => { z[f] = v; }, key);
  const sourced = l.type === 'video' || l.type === 'audio';
  const U = t('unit.s');

  // ---- Link (linked clips, SPEC 1.1a)
  const av = LK.available([id]);
  if (sourced && (linkedWith(l).length > 1 || av.separate)) {
    const lb = add('link', t('sec.link'));
    const partners = linkedWith(l).filter(x => x.id !== id);
    if (partners.length) lb.append(el('div', 'hint', t('link.linkedWith', { name: partners.map(x => x.name).join(', ') })));
    const off = LK.offsetOf(l);
    if (Math.abs(off) > 0.5 / (S.project.fps || 30) || LK.outOfSync(l)) lb.append(el('div', 'hint lk-warn', t('link.outOfSync') + ' ' + LK.offsetText(off || 0)));
    const row = [];
    if (av.unlink) row.push(button(t('link.unlink'), () => LK.unlinkLayers(S.selection)));
    if (av.resync) row.push(button(t('link.resync'), () => LK.resync([id])));
    if (av.separate) row.push(button(t('link.separate'), () => LK.separateAudio(id)));
    if (av.merge) row.push(button(t('link.merge'), () => LK.mergeAudio([id])));
    for (let i = 0; i < row.length; i += 2) lb.append(pair(...row.slice(i, i + 2)));
  }

  // ---- Time
  let body = add('time', t('sec.time'));
  body.append(
    pair(numberField({ label: t('field.start'), read: read('start'), write: (v, key) => mut(id, (z, P) => { const d = z.end - z.start, old = z.start; z.start = Math.max(0, v); z.end = z.start + d;
        if (LK.isPairSelection(S.selection)) for (const q of P.layers) if (q.id !== id && q.link && q.link === z.link && !q.locked) { q.start = round(Math.max(0, q.start + z.start - old), 4); q.end = round(q.end + z.start - old, 4); } }, key), unit: U, step: 0.05, min: 0, id }),
      numberField({ label: t('field.end'), read: read('end'), write: (v, key) => mut(id, z => { z.end = Math.max(z.start + 0.05, v); }, key), unit: U, step: 0.05, min: 0, id })),
    pair(numberField({ label: t('field.duration'), read: () => { const z = layer(id); return z.end - z.start; }, write: (v, key) => mut(id, z => { z.end = z.start + Math.max(0.05, v); }, key), unit: U, step: 0.05, min: 0.05, id }),
      sourced ? numberField({ label: t('field.src_in'), read: read('src_in'), write: writer('src_in'), unit: U, step: 0.05, min: 0, id }) : el('span')),
    el('div', 'hint', t('inspector.shiftLayer')), nudgeButtons(d => nudgeLayers(LK.isPairSelection(S.selection) ? S.selection : [id], d)));
  const neighbour = cutNeighbour(l);
  if (neighbour) body.append(el('div', 'hint', t('inspector.rolling', { a: l.name, b: neighbour.name })), nudgeButtons(d => rollingEdit(id, d)));

  // ---- Transform (every numeric property has a stopwatch: keyframes.js)
  if (VISUAL.has(l.type)) {
    body = add('transform', t('sec.transform'));
    if (l.type !== 'color') {
      const ab = el('button', 'pbtn wide kf-animate'); ab.type = 'button'; ab.append(icon('diamond', 's'), el('span', '', t('animate.button')), icon('chevron-down', 's'));
      ab.addEventListener('click', () => KF.openAnimateMenu(ab, id));
      body.append(ab,
        numberField({ label: 'X', prop: 'x', unit: 'px', n: 0, step: 1, id }),
        numberField({ label: 'Y', prop: 'y', unit: 'px', n: 0, step: 1, id }),
        numberField({ label: t('field.scale'), prop: 'scale', n: 3, step: 0.005, min: 0.01, max: 30, id }),
        stretchPair(id),
        numberField({ label: t('field.rotation'), prop: 'rotation', unit: '°', n: 1, step: 0.5, min: -7200, max: 7200, id }),
        button(t('inspector.center'), () => mut(id, (z, P) => { KF.writeProp(z, 'x', P.width / 2); KF.writeProp(z, 'y', P.height / 2); }), 'wide'));
    }
    body.append(withSlider(numberField({ label: t('field.opacity'), prop: 'opacity', n: 2, step: 0.01, min: 0, max: 1, id }),
      slider({ ...animArgs(id, 'opacity'), min: 0, max: 1, step: 0.01, id, label: 'opacity' })));
    if (l.type !== 'color') {
      body = add('transform2', t('sec.transform2'));
      body.append(numberField({ label: t('field.anchor_x'), prop: 'anchor_x', n: 3, step: 0.005, min: -3, max: 4, id, hint: t('anchor.hint') }),
        numberField({ label: t('field.anchor_y'), prop: 'anchor_y', n: 3, step: 0.005, min: -3, max: 4, id, hint: t('anchor.hint') }));
    }
  }
  // ---- Text
  if (l.type === 'text') {
    body = add('text', t('sec.text'));
    const ta = el('textarea', 'text-input'); ta.rows = 3; ta.value = l.text; ta.spellcheck = false;
    ta.addEventListener('input', () => mut(id, z => { z.text = ta.value; }, 'text:' + id));
    updaters.push(() => { if (document.activeElement !== ta && layer(id)) ta.value = layer(id).text; });
    body.append(ta, fontField(id),
      pair(numberField({ label: t('field.size'), read: read('size'), write: writer('size'), unit: 'px', n: 0, step: 1, min: 1, max: 1000, id }), numberField({ label: t('field.letter_spacing'), read: read('letter_spacing'), write: writer('letter_spacing'), n: 2, step: 0.01, min: -0.5, max: 3, id })),
      pair(numberField({ label: t('field.line_height'), read: read('line_height'), write: writer('line_height'), n: 2, step: 0.01, min: 0.5, max: 4, id }),
        selectField({ label: t('field.align'), options: [['left', t('align.left')], ['center', t('align.center')], ['right', t('align.right')]], read: () => layer(id).align, write: v => mut(id, z => { z.align = v; }) })),
      colorField(t('field.color'), read('color'), writer('color'), id));
    body = add('shadow', t('sec.shadow'));
    body.append(checkbox(t('shadow.enabled'), () => layer(id).shadow.enabled, v => mut(id, z => { z.shadow.enabled = v; })),
      pair(numberField({ label: t('shadow.blur'), read: () => layer(id).shadow.blur, write: (v, key) => mut(id, z => { z.shadow.blur = v; }, key), unit: 'px', n: 1, step: 0.2, min: 0, max: 100, id }),
        numberField({ label: t('field.opacity'), read: () => layer(id).shadow.opacity, write: (v, key) => mut(id, z => { z.shadow.opacity = v; }, key), n: 2, step: 0.01, min: 0, max: 1, id })));
  }
  if (l.type === 'color') { body = add('color', t('sec.color')); body.append(colorField(t('field.color'), read('color'), writer('color'), id)); }
  // ---- Source
  if (l.type === 'video' || l.type === 'image' || l.type === 'audio') {
    body = add('source', t('sec.source'));
    const file = el('div', 'field readonly'); file.append(el('span', 'field-label', t('source.file'))); const fv = el('span', 'readonly-value file', basename(l.src)); fv.title = l.src; file.append(fv); body.append(file);
    const info = S.media.get(l.src);
    if (info) body.append(el('div', 'hint', [info.duration ? fmt(info.duration, 2) + ' ' + U : '', info.width ? `${info.width}×${info.height}` : '', info.has_audio === false ? t('source.noAudio') : ''].filter(Boolean).join(' · ')));
    if (l.type !== 'audio') {
      body.append(selectField({ label: t('field.fit'), options: [['cover', t('fit.cover')], ['contain', t('fit.contain')], ['native', t('fit.native')]], read: () => layer(id).fit, write: v => mut(id, z => { z.fit = v; }) }));
      const crop = (side) => numberField({ label: t('crop.' + side), read: () => (layer(id).crop[side] || 0) * 100, write: (v, key) => mut(id, z => { z.crop[side] = v / 100; }, key), unit: '%', n: 1, step: 0.2, min: 0, max: 50, id });
      body.append(el('div', 'hint', t('crop.hint')), pair(crop('top'), crop('bottom')), pair(crop('left'), crop('right')));
    }
  }
  // ---- Audio
  if (sourced) {
    body = add('audio', t('sec.audio'));
    body.append(withSlider(numberField({ label: t('field.volume_db'), prop: 'volume_db', unit: 'dB', n: 1, step: 0.1, min: -40, max: 12, id }),
      slider({ ...animArgs(id, 'volume_db'), min: -40, max: 12, step: 0.1, id, label: 'volume' })),
      checkbox(t('field.muted'), () => layer(id).muted, v => mut(id, z => { z.muted = v; })),
      el('div', 'hint', t('audio.envelopeHint')));
    body.append(button(t('audio.addPoint'), () => mut(id, z => {
      const tt = round(S.t, 2); const db = round(envelopeDb(z, tt), 1);
      if (z.envelope.some(p => Math.abs(p[0] - tt) < 0.005)) return;
      z.envelope.push([tt, db]); z.envelope.sort((a, b) => a[0] - b[0]);
    }), 'wide'));
    l.envelope.forEach((p, i) => {
      const r = el('div', 'env-row');
      r.append(numberField({ label: 't', read: () => (layer(id).envelope[i] || [0, 0])[0], write: (v, key) => mut(id, z => { if (z.envelope[i]) z.envelope[i][0] = v; }, key), unit: U, n: 2, step: 0.05, min: 0, id: id + 'zt' + i }),
        numberField({ label: 'dB', read: () => (layer(id).envelope[i] || [0, 0])[1], write: (v, key) => mut(id, z => { if (z.envelope[i]) z.envelope[i][1] = v; }, key), n: 1, step: 0.1, min: -60, max: 24, id: id + 'zd' + i }));
      const del = el('button', 'icon-btn'); del.append(icon('x', 's')); del.title = t('audio.deletePoint'); del.addEventListener('click', () => mut(id, z => { z.envelope.splice(i, 1); })); r.append(del); body.append(r);
    });
  }
  // ---- Transition (fades)
  body = add('transition', t('sec.transition'));
  body.append(pair(
    numberField({ label: t('field.fade_in'), read: read('fade_in'), write: writer('fade_in'), unit: U, n: 2, step: 0.05, min: 0, max: 60, id }),
    numberField({ label: t('field.fade_out'), read: read('fade_out'), write: writer('fade_out'), unit: U, n: 2, step: 0.05, min: 0, max: 60, id })));
  // ---- Effects
  if (VISUAL.has(l.type)) {
    body = add('effects', t('sec.effects'));
    l.effects.forEach((e, i) => body.append(effectCard(id, i, e)));
    const addSel = el('select', 'effect-add'); const o0 = el('option', '', t('effect.add')); o0.value = ''; addSel.append(o0);
    for (const type of EFFECT_TYPES) { if ((type === 'zoom' || type === 'freeze') && !(l.type === 'video' || l.type === 'image')) continue; const o = el('option', '', t('effect.' + type + '.name')); o.value = type; addSel.append(o); }
    addSel.addEventListener('change', () => { const type = addSel.value; addSel.value = ''; if (type) mut(id, z => { z.effects.push({ type, ...clone(EFFECT_DEFAULTS[type] || {}), enabled: true }); }); });
    body.append(addSel);
    mountAsk(body);
  } else mountAsk(add('ai', t('ai.section')));
}

const EFFECT_TYPES = ['grayscale', 'brightness', 'contrast', 'saturation', 'blur', 'zoom', 'freeze'];
const EFFECT_DEFAULTS = { grayscale: { amount: 1 }, brightness: { value: 0.1 }, contrast: { value: 0.1 }, saturation: { value: 0.2 }, blur: { radius: 6 }, zoom: { from: 1, to: 1.06 }, freeze: {} };
// [param, min, max, step, decimals, unit]
const EFFECT_PARAMS = {
  grayscale: [['amount', 0, 1, 0.01, 2, '']],
  brightness: [['value', -1, 1, 0.01, 2, '']], contrast: [['value', -1, 1, 0.01, 2, '']], saturation: [['value', -1, 1, 0.01, 2, '']],
  blur: [['radius', 0, 200, 0.5, 1, 'px']],
  zoom: [['from', 0.1, 5, 0.005, 3, '×'], ['to', 0.1, 5, 0.005, 3, '×']], freeze: [],
};
function effectCard(id, i, e) {
  const card = el('div', 'effect'); const head = el('div', 'effect-head');
  const cb = el('input'); cb.type = 'checkbox'; cb.checked = e.enabled !== false; cb.addEventListener('change', () => mut(id, z => { z.effects[i].enabled = cb.checked; }));
  updaters.push(() => { const z = layer(id); if (z && z.effects[i]) cb.checked = z.effects[i].enabled !== false; });
  const up = el('button', 'icon-btn'), down = el('button', 'icon-btn'), del = el('button', 'icon-btn'); up.append(icon('chevron-up', 's')); down.append(icon('chevron-down', 's')); del.append(icon('x', 's'));
  up.title = t('effect.moveUp'); down.title = t('effect.moveDown'); del.title = t('effect.delete');
  up.addEventListener('click', () => mut(id, z => { if (i > 0) [z.effects[i - 1], z.effects[i]] = [z.effects[i], z.effects[i - 1]]; }));
  down.addEventListener('click', () => mut(id, z => { if (i < z.effects.length - 1) [z.effects[i + 1], z.effects[i]] = [z.effects[i], z.effects[i + 1]]; }));
  del.addEventListener('click', () => mut(id, z => { const gone = z.effects.splice(i, 1)[0]; if (gone && gone.id && z.keys) { for (const k of Object.keys(z.keys)) if (k.startsWith('fx.' + gone.id + '.')) delete z.keys[k]; if (!Object.keys(z.keys).length) delete z.keys; } }));
  head.append(cb, el('span', 'effect-name', t('effect.' + e.type + '.name')), up, down, del); card.append(head);
  const params = EFFECT_PARAMS[e.type] || [];
  if (params.length === 1) {
    const [p, mn, mx, st, n, unit] = params[0];
    const a = fxArgs(id, i, p);
    card.append(withSlider(numberField({ label: t('effect.param.' + p), ...a, min: mn, max: mx, step: st, n, unit, id: id + 'e' + i + p }),
      slider({ read: a.read, write: a.write, min: mn, max: e.type === 'blur' ? 60 : mx, step: st, id: id + 'e' + i, label: p })));
  } else for (const [p, mn, mx, st, n, unit] of params) card.append(numberField({ label: t('effect.param.' + p), ...fxArgs(id, i, p), min: mn, max: mx, step: st, n, unit, id: id + 'e' + i + p }));
  return card;
}

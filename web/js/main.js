// main.js — boot, top bar, transport, shortcuts, add-layer menu, notes, render progress, language switch.
import * as LK from './links.js';
import { S, aspectLabel, onChange, emit, change, select, setTime, layer, selectedLayers, undo, redo, addLayer, removeLayers, duplicateLayers, splitLayers, canSplit, rippleDelete, copyPayload, parsePayload, pasteLayers, newLayer, newId, loadExternal, timecode, clamp, VISUAL, basename } from './state.js';
import * as API from './api.js';
import { initI18n, t, fmt, getLang, setLang, onLang, applyStatic } from './i18n.js';
import { refreshFonts, ensureFont } from './fonts.js';
import { startPreview, togglePlay, play, pause, startAudio, setRealFrame, debugInfo, setZoom, zoomStep, viewAnchor, viewInfo, setGuides, handKey, shuttle, ZOOMS, layerBox } from './preview.js';
import { openSequence, sequenceOpen } from './sequence.js';
import { startLayerPanel } from './layers.js';
import { startInspector } from './inspector.js';
import { writeProp, valueAt } from './keyframes.js';
import { startImport } from './import.js';
import { startExport } from './export.js';
import { startMediaPanel, togglePanel, openPanel } from './mediapanel.js';
import { startNotes, toggleNotes, jumpNote, addNote } from './notes.js';
import { $, el, icon, setIcon, copyText } from './dom.js';
import { isMod, IS_MAC } from './platform.js';
import { openFilePicker } from './import.js';
import { initTheme, toggleTheme } from './theme.js';

// ---------------------------------------------------------------- toasts
function toast(text, kind = 'info', ms = 3500) {
  const b = el('div', 'toast ' + kind); b.append(icon(kind === 'ok' ? 'circle-check' : kind === 'warn' || kind === 'error' ? 'triangle-alert' : 'info'), el('span', 'toast-text', text)); $('toasts').append(b);
  while ($('toasts').children.length > 3) $('toasts').firstChild.remove(); // at most 3 stacked
  setTimeout(() => { b.classList.add('leave'); setTimeout(() => b.remove(), 300); }, ms);
}
API.bindToast(toast);
API.bindSaveEmit(() => emit('save'));

// ---------------------------------------------------------------- boot
async function boot() {
  initTheme();
  await initI18n();
  let data;
  try { await refreshFonts(); data = await API.loadProject(); }
  catch (e) { document.body.innerHTML = `<div class="fatal"><h2>${t('fatal.title')}</h2><p>${String(e.message || e)}</p><p>${t('fatal.hint')} <code>kurgu &lt;project_dir&gt;</code></p></div>`; return; }
  try { S.snap = localStorage.getItem('kurgu.snap') !== '0'; } catch (e) { /* ignore */ }
  loadExternal(data.project, data.version);
  for (const l of S.project.layers) if (l.type === 'text') ensureFont(l.font);
  const inspector = startInspector($('inspector'));
  const layers = startLayerPanel($('layers'));
  const mediaHost = el('div', 'media-host'); document.body.append(mediaHost);
  const preview = startPreview({ stage: $('stage'), frame: $('frame'), canvas: $('canvas'), overlay: $('overlay-canvas'), mediaHost });
  const panel = startMediaPanel($('media-panel'), toast);
  const notesPanel = startNotes({ toast });
  const modules = [preview, layers, inspector, panel, notesPanel];
  onChange(kind => {
    for (const m of modules) { try { if (m[kind]) m[kind](); } catch (e) { console.error(kind, e); } }
    if (kind === 'project') { updateTop(); renderScrubNotes(); updateTransport(); updateSequenceUi(); updateSplit(); }
    if (kind === 'selection') updateSplit();
    if (kind === 'view') updateZoomUi();
    if (kind === 'time') { updateTransport(); updateSplit(); }
    if (kind === 'playing') { setIcon($('b-play'), S.playing ? 'pause' : 'play', S.playing ? '' : 'nudge'); updateRate(); }
    if (kind === 'save') updateSaveStatus();
    if (kind === 'settings') { $('b-frame').classList.toggle('on', S.realFrame); $('b-frame').setAttribute('aria-pressed', String(S.realFrame)); }
    if (kind === 'lang') { applyStatic(); buildEmpty(); updateTop(); updateSequenceUi(); updateZoomUi(); updateSaveStatus(); renderScrubNotes(); }
  });
  for (const id of ['inspector', 'topbar', 'transport', 'media-panel']) $(id).addEventListener('pointerdown', () => { S.focus = 'general'; }, true);
  buildEmpty(); bindClipboard(); bindTop(); bindTransport(); bindView(); bindKeys(); bindSplitter(); bindMenu(); bindLang();
  mirrorPressed();
  startImport(toast);
  updateTop(); updateSaveStatus(); renderScrubNotes(); updateTransport(); updateSequenceUi(); updateZoomUi();
  $('b-snap').classList.toggle('on', S.snap);
  emit('project');
  API.startPolling();
  API.startStateSync();
  startExport({ toast });
  window.addEventListener('beforeunload', e => { if (API.savePending()) { API.save(); e.preventDefault(); e.returnValue = ''; } });
  window.addEventListener('pointerdown', startAudio, { once: true });
  window.addEventListener('keydown', startAudio, { once: true });
  window.__kurgu = { S, change, debugInfo, setLang, emit, viewInfo, setZoom, zoomStep, layerBox, LK, select, splitLayers, removeLayers, duplicateLayers, rippleDelete };  // debugging / tests
  const q = new URLSearchParams(location.search); // tests only: ?t=5&sel=l_1
  if (q.has('t')) setTime(parseFloat(q.get('t')));
  if (q.has('sel')) select(q.get('sel').split(','));
}

// ---------------------------------------------------------------- top bar
function bindTop() {
  $('b-panel').addEventListener('click', togglePanel);
  $('b-ask').addEventListener('click', focusAsk);
  $('b-theme').addEventListener('click', toggleTheme);
  $('b-undo').addEventListener('click', undo);
  $('b-redo').addEventListener('click', redo);
  $('b-note').addEventListener('click', openNote);
  $('b-split').addEventListener('click', doSplit);
  $('b-snap').addEventListener('click', toggleSnap);
  $('b-frame').addEventListener('click', () => setRealFrame(!S.realFrame));
  $('b-seq').addEventListener('click', openSequence);
  const name = $('project-name');
  name.addEventListener('change', () => change(p => { p.name = name.value.trim() || p.name; }, { key: 'project-name' }));
  name.addEventListener('keydown', e => { if (e.key === 'Enter') name.blur(); });
  document.addEventListener('click', e => { const b = e.target.closest('button'); if (b) setTimeout(() => b.blur(), 0); });
  document.addEventListener('change', e => { const x = e.target; if (x.tagName === 'SELECT' || (x.tagName === 'INPUT' && ['checkbox', 'range', 'color'].includes(x.type))) setTimeout(() => x.blur(), 0); });
}
/** Toggle buttons keep their look in the `on` class; screen readers get it as aria-pressed. */
function mirrorPressed() {
  for (const b of document.querySelectorAll('#b-snap, #b-frame, #b-guides, #b-panel')) {
    const sync = () => b.setAttribute('aria-pressed', String(b.classList.contains('on')));
    new MutationObserver(sync).observe(b, { attributes: true, attributeFilter: ['class'] }); sync();
  }
}
/** The toolbar's command field opens the Ask-AI box of the inspector (selection scope, or the frame at the playhead) and focuses it. */
function focusAsk() {
  const box = document.querySelector('#inspector .ai-box'); if (!box) return;
  const sec = box.closest('.section'); if (sec) sec.classList.remove('collapsed');
  const ta = box.querySelector('textarea'); box.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); if (ta && !ta.disabled) ta.focus();
}
function bindLang() {
  const sync = () => { for (const b of document.querySelectorAll('#lang button')) b.classList.toggle('on', b.dataset.lang === getLang()); };
  for (const b of document.querySelectorAll('#lang button')) b.addEventListener('click', () => setLang(b.dataset.lang));
  onLang(() => { sync(); emit('lang'); });
  sync();
}
function toggleSnap() {
  S.snap = !S.snap; $('b-snap').classList.toggle('on', S.snap);
  try { localStorage.setItem('kurgu.snap', S.snap ? '1' : '0'); } catch (e) { /* ignore */ }
  toast(t(S.snap ? 'toast.snapOn' : 'toast.snapOff'), 'info', 1200);
}
function updateTop() {
  const name = $('project-name'); if (document.activeElement !== name) name.value = S.project.name;
  document.title = S.project.name + ' — Kurgu';
  $('b-undo').disabled = !S.undo.length; $('b-redo').disabled = !S.redo.length;
}
function updateSaveStatus() {
  const map = { saved: ['save.saved', 'ok'], saving: ['save.saving', 'wait'], dirty: ['save.saving', 'wait'], conflict: ['save.conflict', 'warn'], error: ['save.error', 'error'] };
  const [key, cls] = map[S.saveState] || ['', ''];
  const box = $('save-status'); box.querySelector('b').textContent = key ? t(key) : ''; box.className = 'status ' + cls;
  updateTop();
}

// ---------------------------------------------------------------- transport
function bindTransport() {
  $('b-play').addEventListener('click', togglePlay);
  $('b-start').addEventListener('click', () => setTime(0));
  $('b-end').addEventListener('click', () => setTime(S.project.duration));
  const scrub = $('scrub');
  scrub.addEventListener('pointerdown', e => {
    if (e.target.closest('.scrub-note')) return;
    scrub.setPointerCapture(e.pointerId); S.focus = 'general'; S.scrubbing = true;
    const go = ev => { const r = scrub.getBoundingClientRect(); setTime(clamp((ev.clientX - r.left) / r.width, 0, 1) * S.project.duration); };
    go(e); const move = ev => go(ev); const done = () => { S.scrubbing = false; scrub.removeEventListener('pointermove', move); scrub.removeEventListener('pointerup', done); scrub.removeEventListener('pointercancel', done); };
    scrub.addEventListener('pointermove', move); scrub.addEventListener('pointerup', done); scrub.addEventListener('pointercancel', done);
  });
}
function updateTransport() {
  $('tc-now').textContent = timecode(S.t); $('tc-dur').textContent = '/ ' + timecode(S.project.duration);
  const f = S.project.duration > 0 ? S.t / S.project.duration : 0;
  const s = $('scrub'); s.querySelector('.fill').style.width = (f * 100) + '%'; s.querySelector('.head').style.left = (f * 100) + '%';
}
function renderScrubNotes() {
  const host = $('scrub-notes'); host.innerHTML = '';
  for (const n of S.project.notes) {
    const m = el('div', 'scrub-note' + (n.done ? ' done' : '')); m.style.left = (n.t / S.project.duration * 100) + '%'; m.title = `${fmt(n.t, 1)} ${t('unit.s')} — ${n.text}` + (n.done ? ' ✓' : '');
    m.addEventListener('click', e => { e.stopPropagation(); setTime(n.t); }); host.append(m);
  }
}

// ---------------------------------------------------------------- sequence + viewer zoom
function updateSequenceUi() {
  const P = S.project; $('seq-label').textContent = `${P.width}×${P.height}`;
  const es = $('empty-state'), empty = P.layers.length === 0;
  if (es.hidden === empty) es.hidden = !empty;
  const size = $('es-size'); if (size) size.textContent = t('seq.hint', { size: `${P.width}×${P.height}`, ratio: aspectLabel(P.width, P.height) });
}
/** Empty project: a calm panel over the viewer — drop zone, Import, and example prompts for the coding agent (copyable). */
function buildEmpty() {
  const host = $('empty-state'); host.innerHTML = '';
  const card = el('div', 'es-card');
  const drop = el('div', 'es-drop'); drop.tabIndex = 0; drop.setAttribute('role', 'button');
  const ic = el('div', 'es-icon'); ic.append(icon('upload'));
  const imp = el('button', 'btn primary es-import'); imp.append(icon('folder-open'), el('span', '', t('empty.import')));
  imp.addEventListener('click', e => { e.stopPropagation(); openFilePicker(); });
  drop.append(ic, el('div', 'es-title', t('empty.title')), el('div', 'es-sub', t('empty.sub')), imp);
  drop.addEventListener('click', openFilePicker);
  drop.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); openFilePicker(); } });
  const ask = el('div', 'es-ask'); ask.append(el('div', 'es-ask-title', t('empty.ask')));
  for (const k of ['empty.p1', 'empty.p2', 'empty.p3']) {
    const text = t(k);
    const row = el('button', 'es-prompt'); row.title = t('empty.copyTip'); row.append(el('span', '', text), icon('copy', 's'));
    row.addEventListener('click', async () => { const ok = await copyText(text); toast(t(ok ? 'empty.copied' : 'empty.copyFailed'), ok ? 'ok' : 'warn', 1800); });
    ask.append(row);
  }
  const foot = el('div', 'es-foot'); const size = el('span'); size.id = 'es-size'; const change = el('button', 'link', t('seq.hintChange')); change.addEventListener('click', openSequence);
  foot.append(size, change);
  card.append(drop, ask, foot); host.append(card);
}
function updateZoomUi() {
  const sel = $('zoom-select'), v = viewInfo();
  const want = v.zoom === 'fit' ? 'fit' : String(Math.round(v.z * 100));
  sel.innerHTML = '';
  const add = (val, label) => { const o = el('option', '', label); o.value = val; sel.append(o); };
  add('fit', `${t('view.fit')} (${Math.round(v.fit * 100)} %)`);
  const pcts = ZOOMS.map(z => Math.round(z * 100));
  for (const p of pcts) add(String(p), p + ' %');
  if (want !== 'fit' && !pcts.includes(Number(want))) add(want, want + ' %');
  sel.value = want;
  sel.title = t('view.zoomTip');
  $('b-guides').classList.toggle('on', S.view.safe || S.view.social);
}
function bindView() {
  $('b-zoom-in').addEventListener('click', () => zoomStep(1, viewAnchor())); $('b-zoom-out').addEventListener('click', () => zoomStep(-1, viewAnchor()));
  $('zoom-select').addEventListener('change', e => { const v = e.target.value; setZoom(v === 'fit' ? 'fit' : Number(v) / 100); });
  $('b-guides').addEventListener('click', e => {
    e.stopPropagation(); const m = $('menu');
    if (!m.hidden) { m.hidden = true; return; }
    m.innerHTML = '';
    const portrait = S.project.height / S.project.width >= 1.6;
    const item = (label, on, fn, disabled) => { const b = el('button', 'menu-item' + (disabled ? ' disabled' : '')); const chk = el('span', 'menu-check'); if (on) chk.append(icon('check')); b.append(chk, el('span', '', label)); b.addEventListener('click', () => { m.hidden = true; fn(); }); m.append(b); };
    item(t('guides.safe'), S.view.safe, () => setGuides({ safe: !S.view.safe }));
    item(t('guides.social'), S.view.social, () => setGuides({ social: !S.view.social }), !portrait);
    if (!portrait) m.append(el('div', 'menu-note', t('guides.socialOnly')));
    const r = $('b-guides').getBoundingClientRect(); m.style.left = Math.max(4, r.right - 240) + 'px'; m.style.top = 'auto'; m.style.bottom = (window.innerHeight - r.top + 4) + 'px'; m.hidden = false;
  });
}

// ---------------------------------------------------------------- notes
function openNote() {
  const box = $('note-box'), input = $('note-input'); box.hidden = false; input.value = '';
  const at = S.t; $('note-time').textContent = fmt(at, 1) + ' ' + t('unit.s');
  input.focus();
  const close = () => { input.blur(); box.hidden = true; input.removeEventListener('keydown', onKey); input.removeEventListener('blur', close); };
  const onKey = e => {
    e.stopPropagation();
    if (e.key === 'Enter') {
      const text = input.value.trim();
      if (text) addNote(text, at);
      close();
    } else if (e.key === 'Escape') close();
  };
  input.addEventListener('keydown', onKey); input.addEventListener('blur', close);
}

// ---------------------------------------------------------------- shortcuts
const isTyping = x => x && (x.tagName === 'TEXTAREA' || x.tagName === 'SELECT' || x.isContentEditable || (x.tagName === 'INPUT' && !['checkbox', 'range', 'color', 'button'].includes(x.type)));
function arrowMove(dx, dy) {
  const targets = selectedLayers().filter(l => VISUAL.has(l.type) && l.type !== 'color' && !l.locked);
  if (!targets.length) return false;
  change(P => { for (const a of targets) { const l = P.layers.find(q => q.id === a.id); if (l) { writeProp(l, 'x', valueAt(l, 'x') + dx); writeProp(l, 'y', valueAt(l, 'y') + dy); } } }, { key: 'arrow' });  // keyed x/y → key at the playhead
  return true;
}
function doSplit() {
  if (!S.selection.length) { toast(t('toast.selectToSplit'), 'info', 1800); return; }
  const first = selectedLayers().find(l => !l.locked && S.t > l.start + 0.02 && S.t < l.end - 0.02);
  const n = splitLayers(S.selection, S.t);
  toast(n ? t('toast.split', { n, name: first ? first.name : '' }) : t('toast.splitNone'), n ? 'ok' : 'info', n ? 1800 : 2600);
}
function doRipple() {
  const r = rippleDelete(S.selection);
  toast(r.removed ? t('toast.ripple', { n: r.removed, m: r.moved }) : t('toast.locked'), r.removed ? 'ok' : 'info', 2600);
}
/** Copy / cut / paste of layers through the real clipboard events: the payload is JSON tagged for Kurgu, so it also pastes into another project.
 *  Some browsers (Safari) send no copy / paste event when nothing is selected in the page: then the key itself triggers the async clipboard API. */
function clipBlocked(e) { return (e && isTyping(e.target)) || sequenceOpen() || !$('modal').hidden || !!document.querySelector('.xp-overlay:not([hidden])'); }
function clipCopy(cut, put) {
  if (!S.selection.length || String(window.getSelection() || '')) return false;
  const ids = cut ? S.selection.filter(id => { const l = layer(id); return l && !l.locked; }) : S.selection;
  const p = copyPayload(ids);
  if (!p) { toast(t('toast.locked'), 'info', 2000); return true; }
  put(JSON.stringify(p));
  if (cut) removeLayers(ids);
  toast(t(cut ? 'toast.cut' : 'toast.copied', { n: p.layers.length }), 'ok', 1800);
  return true;
}
function clipPaste(text) {
  const p = parsePayload(text); if (!p) return false;
  const fresh = pasteLayers(p, S.t); if (!fresh.length) return true;
  toast(t('toast.pasted', { n: fresh.length }), 'ok', 1800);
  const srcs = [...new Set(fresh.map(id => layer(id)).filter(l => l && l.src).map(l => l.src))];
  Promise.all(srcs.map(src => API.mediaInfo(src).then(i => (i ? null : src)))).then(miss => { const m = miss.filter(Boolean); if (m.length) toast(t('toast.pasteMissing', { n: m.length, name: m[0] }), 'warn', 8000); });
  return true;
}
let clipSeen = 0;
function bindClipboard() {
  const evt = cut => e => { if (clipBlocked(e)) return; clipSeen = performance.now(); if (clipCopy(cut, txt => e.clipboardData.setData('text/plain', txt))) e.preventDefault(); };
  document.addEventListener('copy', evt(false));
  document.addEventListener('cut', evt(true));
  document.addEventListener('paste', e => {
    if (clipBlocked(e)) return; clipSeen = performance.now();
    if (clipPaste(e.clipboardData ? e.clipboardData.getData('text/plain') : '')) e.preventDefault();
  });
  window.addEventListener('keydown', e => {  // fallback when the browser sends no clipboard event
    if (!isMod(e) || e.shiftKey || e.altKey || isTyping(e.target) || sequenceOpen()) return;
    const k = e.key.toLowerCase(); if (k !== 'c' && k !== 'x' && k !== 'v') return;
    const t0 = performance.now();
    setTimeout(() => {
      if (clipSeen >= t0 || clipBlocked(null) || !navigator.clipboard) return;
      if (k === 'v') navigator.clipboard.readText().then(clipPaste).catch(() => {});
      else clipCopy(k === 'x', txt => navigator.clipboard.writeText(txt).catch(() => {}));
    }, 80);
  });
}
function updateSplit() { const b = $('b-split'); if (b) b.disabled = !canSplit(); }
function updateRate() {
  const b = $('tc-rate'), r = S.shuttle; if (!b) return;
  b.hidden = !r; b.textContent = r < 0 ? `◀ ${-r}×` : r > 0 ? `${r}×` : '';
}
function bindKeys() {
  window.addEventListener('keydown', e => {
    const cmd = isMod(e), typing = isTyping(e.target);
    if (cmd && e.key.toLowerCase() === 's') { e.preventDefault(); API.save(); return; }
    if (typing || sequenceOpen()) return;
    if (cmd) {
      const k = e.key.toLowerCase();
      if (k === '+' || k === '=') { e.preventDefault(); zoomStep(1, viewAnchor()); return; }
      if (k === '-' || k === '_') { e.preventDefault(); zoomStep(-1, viewAnchor()); return; }
      if (k === '0') { e.preventDefault(); setZoom('fit'); return; }
      if (k === '1') { e.preventDefault(); setZoom(1, viewAnchor()); return; }
      if (k === 'z') { e.preventDefault(); e.shiftKey ? redo() : undo(); }
      else if (k === 'y' && !IS_MAC) { e.preventDefault(); redo(); }
      else if (k === 'd') { e.preventDefault(); duplicateLayers(S.selection); }
      else if (k === 'k') { e.preventDefault(); doSplit(); }
      else if (k === 'a') { e.preventDefault(); select(S.project.layers.map(l => l.id)); }
      // c / x / v are left to the browser: they arrive as copy / cut / paste events (bindClipboard)
      return;
    }
    if (e.ctrlKey || e.metaKey) return; // the other platform's modifier: not ours
    if (e.altKey && (e.key === 'ArrowRight' || e.key === 'ArrowLeft')) { e.preventDefault(); jumpNote(e.key === 'ArrowRight' ? 1 : -1); return; }  // next / previous note
    if (e.altKey) return;
    const fps = S.project.fps || 30;
    switch (e.key) {
      case ' ': e.preventDefault(); if (e.repeat) break; if (!handKey(true)) togglePlay(); break;
      case 'j': case 'J': e.preventDefault(); if (!e.repeat) shuttle(-1); break;   // J / K / L: reverse (stepping) · pause · forward, repeat for 2× / 4×
      case 'k': case 'K': e.preventDefault(); pause(); break;
      case 'l': case 'L': e.preventDefault(); if (!e.repeat) shuttle(1); break;
      case 'ArrowLeft': case 'ArrowRight': case 'ArrowUp': case 'ArrowDown': {
        const dx = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0, dy = e.key === 'ArrowDown' ? 1 : e.key === 'ArrowUp' ? -1 : 0;
        if (S.focus === 'preview' && !S.realFrame) { e.preventDefault(); arrowMove(dx * (e.shiftKey ? 10 : 1), dy * (e.shiftKey ? 10 : 1)); }
        else if (dx) { e.preventDefault(); if (S.playing) pause(); setTime(S.t + dx * (e.shiftKey ? 1 : 1 / fps)); }
        break;
      }
      case 'Home': e.preventDefault(); setTime(0); break;
      case 'End': e.preventDefault(); setTime(S.project.duration); break;
      case '\\': e.preventDefault(); togglePanel(); break;
      case 'n': case 'N': e.preventDefault(); if (e.shiftKey) toggleNotes(); else openNote(); break;
      case 's': case 'S': e.preventDefault(); toggleSnap(); break;
      case 'Delete': case 'Backspace': if (S.selection.length) { e.preventDefault(); if (e.shiftKey) doRipple(); else removeLayers(S.selection); } break;
      case 'Escape': if (!$('modal').hidden) $('modal').hidden = true; else if (!$('menu').hidden) $('menu').hidden = true; else select([]); break;
      case '[': setTime(S.t - 1 / fps); break;
      case ']': setTime(S.t + 1 / fps); break;
      default: break;
    }
  });
}

// ---------------------------------------------------------------- panel splitter (default height ≈ 40 % of the window, see CSS)
function bindSplitter() {
  const root = document.documentElement;
  try { const h = parseInt(localStorage.getItem('kurgu.layerPanelHeight') || '', 10); if (h > 100) root.style.setProperty('--lp-h', h + 'px'); } catch (e) { /* ignore */ }
  const bar = $('splitter');
  bar.addEventListener('pointerdown', e => {
    e.preventDefault(); bar.setPointerCapture(e.pointerId);
    const move = ev => { const h = clamp(window.innerHeight - ev.clientY - 15, 110, window.innerHeight - 300); root.style.setProperty('--lp-h', h + 'px'); };
    const up = () => { bar.removeEventListener('pointermove', move); bar.removeEventListener('pointerup', up); try { localStorage.setItem('kurgu.layerPanelHeight', String(parseInt(getComputedStyle(root).getPropertyValue('--lp-h'), 10) || '')); } catch (e2) { /* ignore */ } };
    bar.addEventListener('pointermove', move); bar.addEventListener('pointerup', up);
  });
}

// ---------------------------------------------------------------- add-layer menu
function bindMenu() {
  $('b-add').addEventListener('click', e => {
    e.stopPropagation(); const m = $('menu');
    if (!m.hidden) { m.hidden = true; return; }
    m.innerHTML = '';
    const item = (ic, label, fn) => { const b = el('button', 'menu-item'); b.append(icon(ic), el('span', '', label)); b.addEventListener('click', () => { m.hidden = true; fn(); }); m.append(b); };
    item('type', t('menu.text'), addText); item('square', t('menu.color'), addColor); item('folder-open', t('menu.fromFile'), openPanel);
    const r = $('b-add').getBoundingClientRect(); m.style.left = r.left + 'px'; m.style.top = (r.bottom + 4) + 'px'; m.style.bottom = 'auto'; m.hidden = false;
  });
  document.addEventListener('pointerdown', e => { const m = $('menu'); if (!m.hidden && !m.contains(e.target) && !e.target.closest('#b-add, #b-guides')) m.hidden = true; });
  $('modal-close').addEventListener('click', () => { $('modal').hidden = true; });
  $('modal').addEventListener('pointerdown', e => { if (e.target === $('modal')) $('modal').hidden = true; });
}
const addText = () => addLayer(newLayer('text', { name: t('layer.newText'), start: S.t, end: S.t + 3, text: t('layer.defaultText') }));
const addColor = () => addLayer(newLayer('color', { name: t('layer.black'), start: S.t, end: S.t + 2, color: '#000000' }));

boot();

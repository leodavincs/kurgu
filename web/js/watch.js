// watch.js — "watch + note": the top-bar note box, the Send button and the full-screen watch mode.
// Notes are the ones of notes.js (project.notes, addNote); sending goes through POST /api/notes/send, which stamps `sent`,
// writes NOTLAR.md + a frame JPEG per note and wakes the agent (`kurgu notes wait`). Styling: web/watch.css.
import { S, change, setTime, clamp, loadExternal, timecode, sceneName } from './state.js';
import * as API from './api.js';
import { t, getLang } from './i18n.js';
import { $, el, icon } from './dom.js';
import { play, pause, togglePlay, setZoom, viewInfo, viewGeom } from './preview.js';
import { isReview } from './mode.js';
import { startDraw, endDraw, takeDraft, draftMarks, markKey, makeToolRow, setFocusNote, setHoverNote, drawBadge } from './marks.js';
import { addNote, layersAt } from './notes.js';
import { dative } from './dative.js';
import { answerBlock } from './answer.js';
import { abNoteButton } from './ab.js';
import { wakeBlock, wakeWorking, wakeKick } from './wake.js';

{ const link = document.createElement('link'); link.rel = 'stylesheet'; link.href = '/web/watch.css'; document.head.append(link); }

let toast = () => {};
let agentName = '';
let sending = false;
const tcShort = v => { const m = Math.floor(v / 60), s = v - m * 60; return `${String(m).padStart(2, '0')}:${s.toFixed(1).padStart(4, '0')}`; };
const unsent = () => S.project.notes.filter(n => n.author === 'user' && !n.sent && !n.done);

// ---------------------------------------------------------------- agent label ("Newton'a gönder")
const who = () => (getLang() === 'tr' ? (agentName ? dative(agentName) : 'ajana') : (agentName || t('send.theAgent')));
export const sendLabel = () => (agentName ? t('send.named', { name: agentName, dat: dative(agentName) }) : t('send.default'));

// ---------------------------------------------------------------- send
export async function sendNotes() {
  if (sending) return false;
  const ids = unsent().map(n => n.id);
  if (!ids.length) { toast(t('send.none'), 'info', 2000); return false; }
  sending = true; refreshSend();
  try {
    await API.saveAndWait();
    const r = await API.sendNotes(ids);
    if (!r.ok) {
      if (r.data && r.data.reason) showReason({ code: r.data.reason, params: r.data.params });   // refused for safety: nothing was recorded, NOT a success
      else toast(t('send.failed', { reason: API.errorText(r.status, r.data) }), 'error', 6000);
      return false;
    }
    loadExternal(r.data.project, r.data.version);               // the server wrote project.json: one undo step
    watchSent.push(...r.data.ids);
    const woke = r.data.hook === 'started' || r.data.hook === 'queued';     // the user's on_send hook ran: the agent was started for these notes
    live = { listening: !!r.data.listening, wake: r.data.wake === 'configured' }; refreshSend();
    if (r.data.hook_reason) showReason(r.data.hook_reason);                  // the notes are saved, but the agent was not started: say why
    else if (woke || live.listening) toast(t(r.data.sent === 1 && getLang() === 'en' ? 'send.toast1' : 'send.toast', { n: r.data.sent, who: who() }) + ' · ' + (woke ? wokeText(r.data.wake_route) : t('send.listening')), 'ok', woke ? 6000 : 5000);
    else if (r.data.hook !== 'error') showIdlePanel();                      // nobody will read them: not a success toast
    if (r.data.hook !== 'error' && !r.data.hook_reason) { const np = $('notice-panel'); if (np) np.remove(); shownProblem = ''; }          // this send went through: an older refusal notice is stale
    if (woke) wakeKick(true);
    if (r.data.hook === 'error' && !r.data.hook_reason) toast(t('send.wakeFailed', { error: r.data.hook_error || '' }), 'warn', 9000);
    if (r.data.failed_frames && r.data.failed_frames.length) toast(t('send.noFrames', { n: r.data.failed_frames.length }), 'warn', 7000);
    return true;
  } catch (e) { toast(t('send.failed', { reason: String(e.message || e) }), 'error', 6000); return false; }
  finally { sending = false; refreshSend(); renderLists(); }
}

const wokeText = wr => (wr && wr.route === 'session'
  ? t('send.wokeSession', { agent: wr.agent === 'codex' ? 'Codex' : 'Claude', bg: wr.agent === 'claude' ? t('send.bgWatch') : '' }) : t('send.woke'));

// ---------------------------------------------------------------- "the agent isn't listening" panel + live state (tooltip)
let live = { listening: false, wake: false };
const stateText = () => t(live.listening ? 'send.state.listening' : live.wake ? 'send.state.wake' : 'send.state.none');
function copyText(text) {
  try { if (navigator.clipboard && navigator.clipboard.writeText) return navigator.clipboard.writeText(text).catch(() => legacyCopy(text)); } catch (e) { /* fall through */ }
  legacyCopy(text); return Promise.resolve();
}
function legacyCopy(text) { const a = document.createElement('textarea'); a.value = text; a.style.cssText = 'position:fixed;opacity:0'; document.body.append(a); a.select(); try { document.execCommand('copy'); } catch (e) { /* ignore */ } a.remove(); }
function copyBtn(label, text) {
  const b = el('button', 'btn idle-copy', label); b.type = 'button';
  b.addEventListener('click', () => copyText(text).then(() => { toast(t('send.idle.copied'), 'ok', 1500); }));
  return b;
}
function showIdlePanel() {
  const old = $('idle-panel'); if (old) old.remove();
  const p = el('div', 'idle-panel'); p.id = 'idle-panel'; p.setAttribute('role', 'alert');
  const x = el('button', 'w-ib idle-x'); x.type = 'button'; x.title = t('send.idle.close'); x.append(icon('x')); x.addEventListener('click', () => p.remove());
  p.append(x, el('b', 'idle-title', t('send.idle.title')));
  const row = (k, lbl, text) => { const r = el('div', 'idle-row'); r.dataset.k = k; r.append(el('span', 'idle-lbl', lbl), copyBtn('“' + text + '”', text)); return r; };
  const loop = row('loop', t('send.idle.copyLoop'), t('send.idle.phraseLoop')), once = row('once', t('send.idle.copyOnce'), t('send.idle.phrase'));
  p.append(loop, once, el('small', 'idle-note', t('send.idle.setup')));
  for (const [k, flag] of [['claude', '--claude'], ['codex', '--codex']]) {
    const cmd = `kurgu setup-agent ${flag} --wake`;
    const row = el('div', 'idle-row'); row.append(el('code', 'idle-cmd', cmd), copyBtn(t('send.idle.copyCmd'), cmd)); row.dataset.k = k;
    p.append(row);
  }
  document.body.append(p);
}
// A refusal (symlink under .kurgu, Windows special characters in the path): persistent + dismissible, same component as the "not listening" panel.
export function showReason(r) {
  const code = r && r.code ? r.code : 'unknown', params = Object.assign({ code }, (r && r.params) || {});
  const key = 'reason.' + code, known = t(key, params) !== key;
  const old = $('notice-panel'); if (old) old.remove();
  const p = el('div', 'idle-panel err'); p.id = 'notice-panel'; p.dataset.code = code; p.setAttribute('role', 'alert');
  const x = el('button', 'w-ib idle-x'); x.type = 'button'; x.title = t('reason.close'); x.append(icon('x')); x.addEventListener('click', () => p.remove());
  p.append(x, el('b', 'idle-title', t('reason.title')), el('span', 'idle-body', known ? t(key, params) : t('reason.unknown', params)));
  document.body.append(p);
}
let shownProblem = '';
async function pollLive() {
  if (document.hidden) return;
  const i = await API.notesLive(); if (!i) return;
  live = { listening: !!i.listening, wake: i.wake_configured === 'configured' }; refreshSend();
  noteProblem(i.problem);
  if (live.listening || live.wake) { const ip = $('idle-panel'); if (ip) ip.remove(); }          // somebody (or a wake hook) is there now: the "nobody listens" panel is stale
}
function noteProblem(pr) { const c = pr ? pr.code : ''; if (c && c !== shownProblem) showReason(pr); shownProblem = c; }

function refreshSend() {
  const n = S.project ? unsent().length : 0;
  for (const id of ['b-send', 'wl-send', 'rv-send']) {
    const b = $(id); if (!b) continue;
    b.disabled = n === 0 || sending; b.classList.toggle('busy', sending);
    const lab = b.querySelector('.send-label'); if (lab) lab.textContent = sendLabel();   // the count lives in the badge only
    const bd = b.querySelector('.send-badge'); if (bd) { bd.textContent = n; bd.hidden = n === 0; }
    b.title = (n ? t('send.tip') : t('send.none')) + ' — ' + stateText();
  }
}

// ---------------------------------------------------------------- top-bar note box
let nbInput, nbTime, nbAt = null;
function bindNoteBar() {
  nbInput = $('nb-input'); nbTime = $('nb-time');
  const pin = () => { if (nbAt === null) nbAt = S.t; nbTime.textContent = tcShort(nbAt); if (isReview()) startDraw(); };   // Review: the frame can be drawn on while the note is written
  const release = () => { nbAt = null; nbTime.textContent = tcShort(S.t); endDraw(false); };
  $('note-dock').append(makeToolRow());
  nbInput.addEventListener('focus', pin);
  nbInput.addEventListener('blur', () => { if (!nbInput.value && !draftMarks().length) release(); });
  nbInput.addEventListener('keydown', e => {
    e.stopPropagation();
    if (e.isComposing || markKey(e)) return;
    if (e.key === 'Enter') {
      const v = nbInput.value.trim(), marks = draftMarks();
      if (v || marks.length) { addNote(v || t('mark.only'), nbAt === null ? S.t : nbAt, marks); takeDraft(); nbInput.value = ''; }
      release(); nbInput.blur();
    } else if (e.key === 'Escape') { nbInput.value = ''; release(); nbInput.blur(); }
  });
  $('b-send').addEventListener('click', sendNotes);
}
/** Review mode: the note box sits under the viewer, on the viewer's centre line and about as wide as the video; otherwise it lives in the top bar. */
function placeDock() {
  const dock = $('note-dock'), tr = $('transport');
  if (isReview()) { if (dock.parentNode !== tr.parentNode) tr.parentNode.insertBefore(dock, tr); sizeDock(); }
  else { const c = document.querySelector('.tb-center'); if (dock.parentNode !== c) c.prepend(dock); dock.style.width = ''; }
}
function sizeDock() { if (!isReview()) return; const g = viewGeom(); $('note-dock').style.width = clamp(Math.round(g.W * g.vz), 420, 720) + 'px'; }
/** N in the editor: jump into the top-bar note box (pinned to the playhead). */
export function focusNote() { if (nbInput) { nbInput.focus(); nbInput.select(); } }

// ---------------------------------------------------------------- watch mode
let root, stageHost, stageHome, stageNext, seekEl, fillEl, headEl, marksEl, playBtn, tcNow, tcDur, wInput, wChip, wTime, wPaused, countEl, sceneEl;
let ready = true, typedEarly = '', wantNote = false;   // keys that arrive while the mode is still opening (fullscreen transition) are kept, not lost
let isOpen = false, usedFullscreen = false, togglingFs = false, savedZoom = 'fit', noteAt = null, resumeAfter = false;
let watchSent = [];                                   // ids sent while this mode was open (kept in the list as "sent")
export const watchOpen = () => isOpen;

const sceneAt = time => {
  const ls = layersAt(S.project, time);
  const main = ls.find(l => l.type === 'video') || ls.find(l => l.type === 'image') || ls[0];
  return main ? sceneName(main) : t('watch.noScene');
};

function build() {
  root = el('div'); root.id = 'watch'; root.hidden = true; root.setAttribute('role', 'dialog'); root.setAttribute('aria-label', t('watch.badge'));
  stageHost = el('div', 'w-stage');
  const top = el('div', 'w-top');
  const badge = el('span', 'w-badge'); badge.append(el('i'), el('span', '', ''));
  sceneEl = el('span', 'w-scene');
  const exit = el('button', 'w-exit'); exit.type = 'button'; exit.append(icon('x'), el('span', 'w-exit-l', ''), el('kbd', '', 'Esc'));
  exit.addEventListener('click', closeWatch);
  top.append(badge, sceneEl, exit);
  wPaused = el('div', 'w-paused'); wPaused.hidden = true; wPaused.append(icon('pause'), el('span'));
  seekEl = el('div', 'w-seek'); fillEl = el('div', 'w-fill'); marksEl = el('div', 'w-marks'); headEl = el('div', 'w-head');
  seekEl.append(fillEl, marksEl, headEl);
  seekEl.addEventListener('pointerdown', e => {
    if (e.target.closest('.w-mark')) return;
    seekEl.setPointerCapture(e.pointerId);
    const go = ev => { const r = seekEl.getBoundingClientRect(); setTime(clamp((ev.clientX - r.left) / r.width, 0, 1) * S.project.duration); };
    go(e); const mv = ev => go(ev);
    const up = () => { seekEl.removeEventListener('pointermove', mv); seekEl.removeEventListener('pointerup', up); seekEl.removeEventListener('pointercancel', up); };
    seekEl.addEventListener('pointermove', mv); seekEl.addEventListener('pointerup', up); seekEl.addEventListener('pointercancel', up);
  });
  playBtn = el('button', 'w-play'); playBtn.type = 'button'; playBtn.addEventListener('click', togglePlay);
  tcNow = el('b', 'w-tcnow'); tcDur = el('span', 'w-tcdur');
  const tcBox = el('span', 'w-tc'); tcBox.append(tcNow, tcDur);
  wTime = el('span', 'w-ntime');
  wInput = el('input', 'w-input'); wInput.id = 'w-input'; wInput.spellcheck = false; wInput.autocomplete = 'off';
  const enter = el('kbd', 'w-enter', ''); enter.id = 'w-enter';
  wChip = el('label', 'w-notebox'); wChip.append(wTime, wInput, enter);
  const ctl = el('div', 'w-ctl'); ctl.append(playBtn, tcBox, wChip, makeToolRow());
  const bottom = el('div', 'w-bottom'); bottom.append(seekEl, ctl);
  const main = el('div', 'w-main'); main.append(stageHost, top, wPaused, bottom);
  // side list
  const head = el('header', 'w-lhead'); const title = el('b'); countEl = el('small'); head.append(title, countEl); head.title = '';
  watchList = makeList(watchVisible);
  const foot = el('footer', 'w-lfoot');
  const send = el('button', 'btn primary send w-send'); send.id = 'wl-send'; send.type = 'button';
  send.append(icon('send'), el('span', 'send-label'), el('b', 'send-badge')); send.addEventListener('click', sendNotes);
  const hint = el('small', 'w-hint'); const keys = el('small', 'w-keys');
  foot.append(send, hint, keys);
  const side = el('aside', 'w-side'); side.append(head, watchList.host, foot);
  root.append(main, side);
  document.body.append(root);
  root._t = { badge: badge.lastChild, exitL: exit.querySelector('.w-exit-l'), title, hint, keys, enter, paused: wPaused.lastChild };
  wInput.addEventListener('input', onType);
  wInput.addEventListener('keydown', onInputKey);
  wInput.addEventListener('blur', () => { if (!wInput.value) endType(false); });
  window.addEventListener('keydown', onKey, true);
  document.addEventListener('fullscreenchange', () => {
    if (togglingFs) { togglingFs = false; usedFullscreen = false; return; }          // F left fullscreen on purpose: the overlay stays
    if (isOpen && usedFullscreen && !document.fullscreenElement) closeWatch(true);   // the browser's own Esc: leave the mode too
  });
  applyText();
}
function applyText() {
  if (!root) return;
  const x = root._t;
  x.badge.textContent = t('watch.badge'); x.exitL.textContent = t('watch.exit'); x.title.textContent = t('watch.listTitle');
  x.hint.textContent = t('watch.sendHint'); x.keys.textContent = t('watch.keys'); x.enter.textContent = t('watch.add'); x.paused.textContent = t('watch.typingPaused');
  wInput.placeholder = t('watch.placeholder');
  playBtn.setAttribute('aria-label', t('transport.play'));
}

export async function openWatch() {
  if (isOpen || !S.project) return;
  if (!root) build();
  isOpen = true; watchSent = []; ready = false; typedEarly = ''; wantNote = false;
  savedZoom = viewInfo().zoom === 'fit' ? 'fit' : viewInfo().z;
  stageHome = $('stage').parentNode; stageNext = $('stage').nextSibling;
  stageHost.append($('stage')); root.append($('toasts')); document.body.classList.add('watching');   // toasts must live inside the full-screen element to be seen
  root.hidden = false;
  setZoom('fit');
  try { if (root.requestFullscreen) { await root.requestFullscreen(); usedFullscreen = true; if (navigator.keyboard && navigator.keyboard.lock) navigator.keyboard.lock(['Escape']).catch(() => {}); } } catch (e) { usedFullscreen = false; }
  if (!isOpen) return;                                  // Esc / the exit button during the transition
  noteAt = null; resumeAfter = false;
  applyText(); renderMarks(); renderLists(); updateTime(); updatePlay(); refreshSend();
  window.dispatchEvent(new Event('resize'));
  ready = true;
  if (typedEarly || wantNote) {                         // the person was already typing: the note box gets it all, focused, caret at the end
    const early = typedEarly; typedEarly = ''; wantNote = false;
    startType(); wInput.focus(); wInput.value += early; wInput.setSelectionRange(wInput.value.length, wInput.value.length);
  } else if (document.activeElement === wInput && !wInput.value) wInput.blur();
}
export function closeWatch(fromFullscreen = false) {
  if (!isOpen) return;
  isOpen = false; ready = true; typedEarly = '';
  endDraw(false);
  if (S.playing) pause();
  endType(false); wInput.value = '';
  if (usedFullscreen) { usedFullscreen = false; try { if (navigator.keyboard && navigator.keyboard.unlock) navigator.keyboard.unlock(); } catch (e) { /* ignore */ } if (document.fullscreenElement && fromFullscreen !== true) document.exitFullscreen().catch(() => {}); }
  stageHome.insertBefore($('stage'), stageNext && stageNext.parentNode === stageHome ? stageNext : null);
  document.body.append($('toasts')); document.body.classList.remove('watching');
  root.hidden = true;
  setZoom(savedZoom);
  window.dispatchEvent(new Event('resize'));
}

// ---- typing: pause at the first character, note belongs to that second, Enter adds it and plays on
function startType() {
  if (noteAt === null) { noteAt = S.t; resumeAfter = S.playing; if (S.playing) pause(); }
  wTime.textContent = tcShort(noteAt);
  wPaused.hidden = false; root.classList.add('typing');
  startDraw();
}
function endType(resume) {
  const go = resume && resumeAfter && isOpen;
  noteAt = null; resumeAfter = false; wPaused.hidden = true; if (root) root.classList.remove('typing');
  endDraw(false);
  if (wTime) wTime.textContent = tcShort(S.t);
  if (go) play();
}
function onType() { if (wInput.value) startType(); else if (noteAt !== null) { /* emptied: keep paused until Enter/Esc/blur */ } }
function onInputKey(e) {
  e.stopPropagation();
  if (e.isComposing) return;
  if (markKey(e)) return;
  if (e.key === 'Enter') {
    e.preventDefault();
    const v = wInput.value.trim(), marks = draftMarks();
    if (v || marks.length) { addNote(v || t('mark.only'), noteAt === null ? S.t : noteAt, marks); takeDraft(); wInput.value = ''; endType(true); wInput.blur(); }
  } else if (e.key === 'Escape') {
    e.preventDefault(); wInput.value = ''; endType(true); wInput.blur();
  }
}
const focusInput = () => { startType(); wInput.focus(); };

function onKey(e) {
  if (!isOpen) return;
  if (e.target === wInput || (e.target.closest && e.target.closest('.w-edit'))) return;   // typing: handled by the input itself
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const k = e.key;
  const eat = () => { e.preventDefault(); e.stopPropagation(); };
  if (!ready && k !== 'Escape') {                       // still opening: keep what is typed, hand it to the note box once it is up
    if (k === 'Backspace') { eat(); typedEarly = typedEarly.slice(0, -1); }
    else if (k.length === 1 && (typedEarly || wantNote || (k !== ' ' && !'nN'.includes(k) && /\S/.test(k)))) { eat(); typedEarly += k; }
    else if (k === 'n' || k === 'N') { eat(); wantNote = true; }
    else eat();
    return;
  }
  if (k === 'Escape') { eat(); closeWatch(); }
  else if (k === ' ') { eat(); if (noteAt !== null) { startType(); wInput.focus(); wInput.setRangeText(' ', wInput.selectionStart, wInput.selectionEnd, 'end'); wInput.dispatchEvent(new Event('input')); } else if (!e.repeat) togglePlay(); }
  else if (k === 'n' || k === 'N') { eat(); focusInput(); }
  else if (k === 'ArrowLeft' || k === 'ArrowRight') { eat(); setTime(S.t + (k === 'ArrowRight' ? 1 : -1)); }
  else if (k === 'f' || k === 'F') { eat(); if (document.fullscreenElement) { togglingFs = true; document.exitFullscreen().catch(() => { togglingFs = false; }); } else if (root.requestFullscreen) root.requestFullscreen().then(() => { usedFullscreen = true; }).catch(() => {}); }
  else if (k.length === 1 && /\S/.test(k) && !(e.target.closest && e.target.closest('input, textarea, select, [contenteditable]'))) { startType(); wInput.focus(); }   // only when no other field has the focus   // any letter: start a note (the character lands in the box)
  else e.stopPropagation();
}

// ---- rendering
function watchVisible() {   // this round: not sent yet, or sent while this mode was open
  const keep = new Set(watchSent);
  return S.project.notes.filter(n => n.author === 'user' && ((!n.sent && !n.done) || n.asked || keep.has(n.id))).sort((a, b) => a.t - b.t);
}
const reviewVisible = () => S.project.notes.filter(n => n.author === 'user').sort((a, b) => a.t - b.t);
function renderMarks() {
  if (!root || !S.project) return;
  const d = S.project.duration || 1;
  marksEl.replaceChildren(...S.project.notes.map(n => {
    const m = el('button', 'w-mark' + (n.sent || n.done ? ' sent' : '')); m.type = 'button'; m.style.left = (n.t / d * 100) + '%'; m.title = `${tcShort(n.t)} ${n.text}`;
    m.addEventListener('click', e => { e.stopPropagation(); setTime(n.t); setFocusNote(n.id); }); return m;
  }));
}
/** A list of notes (timecode chip, text, scene, sent / fixed state, reply; click = seek, hover = edit / delete). `visible()` says which notes. */
function makeList(visible) {
  const host = el('div', 'w-list'); host.setAttribute('role', 'list');
  let editing = null;
  const L = {
    host,
    get editing() { return editing; },
    notes: visible,
    render() {
      if (!S.project) return;
      const list = visible();
      const wb = wakeBlock();
      if (!list.length) { host.replaceChildren(...(wb ? [wb] : []), el('div', 'w-empty', t('watch.empty'))); return; }
      host.replaceChildren(...(wb ? [wb] : []), ...list.map(n => {
        const row = el('div', 'w-note' + (n.done ? ' done' : n.asked ? ' asked' : n.sent ? ' sent' : '') + (Math.abs(S.t - n.t) < 0.3 ? ' near' : '')); row.dataset.id = n.id; row.setAttribute('role', 'listitem');
        row.append(el('span', 'w-ntc', tcShort(n.t)));
        const body = el('div', 'w-nbody');
        if (editing === n.id) {
          const inp = el('input', 'w-edit'); inp.value = n.text; inp.spellcheck = false;
          inp.addEventListener('keydown', e => { e.stopPropagation(); if (e.key === 'Enter') { const v = inp.value.trim(); editing = null; if (v) change(p => { const x = p.notes.find(q => q.id === n.id); if (x) x.text = v; }); else L.render(); } else if (e.key === 'Escape') { editing = null; L.render(); } });
          body.append(inp); setTimeout(() => inp.focus(), 0);
        } else body.append(el('p', '', n.text));
        const meta = el('small', 'w-nmeta', sceneAt(n.t));
        if (n.done) meta.append(el('span', 'w-state fixed', ' · ' + t('watch.fixed') + ' ✓'));
        else if (n.asked) meta.append(el('span', 'w-state asked', ' · ' + t('ask.badge')));
        else if (wakeWorking(n)) meta.append(el('span', 'w-state wk-working', ' · ' + t('wake.working')));
        else if (n.sent) meta.append(el('span', 'w-state', ' · ' + t('watch.sentMark')));
        { const db = drawBadge(n, 'w-draw'); if (db) meta.append(' ', db); }
        body.append(meta);
        { const ab = answerBlock(n, toast); if (ab) body.append(ab); }
        if (n.reply) body.append(el('small', 'w-reply', n.reply));
        { const ab = abNoteButton(n, 'w-ab'); if (ab) body.append(ab); }
        row.append(body);
        if (!n.sent && !n.done) {
          const acts = el('div', 'w-acts');
          const eb = el('button', 'w-ib'); eb.type = 'button'; eb.title = t('watch.edit'); eb.append(icon('pencil')); eb.addEventListener('click', e => { e.stopPropagation(); editing = n.id; L.render(); });
          const db = el('button', 'w-ib del'); db.type = 'button'; db.title = t('watch.delete'); db.append(icon('trash-2')); db.addEventListener('click', e => { e.stopPropagation(); change(p => { p.notes = p.notes.filter(q => q.id !== n.id); }); });
          acts.append(eb, db); row.append(acts);
        }
        row.addEventListener('click', () => { if (S.playing) pause(); setTime(n.t); setFocusNote(n.id); });
        row.addEventListener('pointerenter', () => setHoverNote(n.id)); row.addEventListener('pointerleave', () => setHoverNote(null));
        return row;
      }));
    },
    near() { for (const row of host.children) if (row.dataset && row.dataset.id) { const n = S.project.notes.find(q => q.id === row.dataset.id); if (n) row.classList.toggle('near', Math.abs(S.t - n.t) < 0.3); } },
  };
  return L;
}
const countText = list => { const u = unsent().length; return !list.length ? '' : u ? t('watch.status', { n: list.length, u }) : t('watch.allSent', { n: list.length }); };
let watchList = null, reviewList = null, reviewCount = null;
function renderLists() {
  if (watchList && isOpen) { watchList.render(); countEl.textContent = countText(watchList.notes()); }
  if (reviewList) { reviewList.render(); reviewCount.textContent = countText(reviewList.notes()); }
}
function updateTime() {
  if (!root || !isOpen) return;
  const d = S.project.duration || 1, f = clamp(S.t / d, 0, 1) * 100;
  fillEl.style.width = f + '%'; headEl.style.left = f + '%';
  tcNow.textContent = timecode(S.t); tcDur.textContent = '/ ' + timecode(S.project.duration);
  sceneEl.textContent = `${S.project.name} · ${sceneAt(S.t)}`;
  if (noteAt === null && wTime) wTime.textContent = tcShort(S.t);
  watchList.near();
}
function updatePlay() {
  if (!playBtn) return;
  playBtn.replaceChildren(icon(S.playing ? 'pause' : 'play'));
  playBtn.classList.toggle('playing', S.playing);
}

// ---------------------------------------------------------------- review panel (right side of Review mode: the notes of this round + Send)
let reviewHead, reviewHint;
const RV_KEY = 'kurgu.rv.notes';
function buildReview() {
  const side = el('aside'); side.id = 'review-panel'; side.className = 'w-side panel';
  const head = el('header', 'w-lhead'); reviewHead = el('b'); reviewCount = el('small'); head.append(reviewHead, reviewCount);
  reviewList = makeList(reviewVisible);
  const foot = el('footer', 'w-lfoot');
  const send = el('button', 'btn primary send w-send'); send.id = 'rv-send'; send.type = 'button';
  send.append(icon('send'), el('span', 'send-label'), el('b', 'send-badge')); send.addEventListener('click', sendNotes);
  reviewHint = el('small', 'w-hint');
  foot.append(send, reviewHint);
  side.append(head, reviewList.host, foot);
  $('main').append(side);
  applyReviewText();
  let off = false; try { off = localStorage.getItem(RV_KEY) === '0'; } catch (e) { /* ignore */ }
  document.body.classList.toggle('rv-notes-off', off);
}
function applyReviewText() { if (!reviewHead) return; reviewHead.textContent = t('review.notes'); reviewHint.textContent = t('watch.sendHint'); }
export const reviewNotesShown = () => !document.body.classList.contains('rv-notes-off');
export function toggleReviewNotes() {
  const off = reviewNotesShown();
  document.body.classList.toggle('rv-notes-off', off);
  try { localStorage.setItem(RV_KEY, off ? '0' : '1'); } catch (e) { /* ignore */ }
  window.dispatchEvent(new Event('resize'));
}

// ---------------------------------------------------------------- module (main.js calls these through its change hooks)
export function startWatch({ toast: toastFn } = {}) {
  if (toastFn) toast = toastFn;
  bindNoteBar();
  buildReview();
  placeDock();
  $('b-watch').addEventListener('click', openWatch);
  const info = () => API.notesInfo().then(i => { if (!i) { setTimeout(info, 1500); return; } agentName = i.agent_name || ''; live = { listening: !!i.listening, wake: i.wake_configured === 'configured' }; noteProblem(i.problem); refreshSend(); renderLists(); });
  info(); setInterval(pollLive, 10000);
  wakeKick();
  window.addEventListener('kurgu-wake', () => { if (!(watchList && watchList.editing) && !(reviewList && reviewList.editing)) renderLists(); });
  refreshSend(); renderLists();
  return {
    media() { if (!(watchList && watchList.editing) && !(reviewList && reviewList.editing)) renderLists(); },
    project() { refreshSend(); if (isOpen) renderMarks(); if (!(watchList && watchList.editing) && !(reviewList && reviewList.editing)) renderLists(); if (isOpen) updateTime(); },
    time() { if (nbInput && nbAt === null) nbTime.textContent = tcShort(S.t); if (isOpen) updateTime(); if (reviewList) reviewList.near(); },
    playing() { updatePlay(); },
    view() { sizeDock(); },
    mode() { placeDock(); },
    lang() { refreshSend(); applyReviewText(); if (isOpen) { applyText(); updateTime(); } renderLists(); },
  };
}

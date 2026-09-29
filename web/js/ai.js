// ai.js — "Ask AI": describe the effect you want, the user's own coding agent (Claude Code / Codex, run by the server)
// edits project.json through Kurgu's MCP tools. The edit reaches the editor as an external change (api.js polling)
// and is ONE undo step. The state lives here (module level), so the panel can be rebuilt by the inspector at any time.
// The box is a small conversation THREAD: it stays while the selection / playhead stay in the same place, follow-ups reuse the
// agent's session server-side, and threads are stored in .kurgu/ai-threads.json (restored when you come back).
import { isMod } from './platform.js';
import { S, emit, undo, redo, selectedLayers, onChange, select, setTime } from './state.js';
import { el, icon } from './dom.js';
import { t, getLang, onLang } from './i18n.js';
import { checkExternal, saveAndWait } from './api.js';

{ // own stylesheet, so the shared app.css stays untouched
  const link = document.createElement('link'); link.rel = 'stylesheet'; link.href = '/web/ai.css'; document.head.append(link);
}
const HISTORY_KEY = 'kurgu.aiPrompts';
const PLACEHOLDERS = 4;
const ICON = { thinking: 'circle-dot', read: 'eye', fonts: 'type', edit: 'pencil', look: 'scan-eye', working: 'dot', text: 'check' };  // Lucide names

// One model shared by every mounted panel. `R` = the current/last turn; `T` = the conversation on screen.
const R = { status: 'idle', jobId: null, events: [], next: 0, error: null, code: null, changed: false, undone: false, msgId: null, prompt: '' };
const T = { draft: '', thread: null, earlier: [], anchor: null, showEarlier: false };
const views = new Set();
let phIndex = 0;

function loadHistory() { try { const a = JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]'); return Array.isArray(a) ? a.filter(x => typeof x === 'string') : []; } catch (e) { return []; } }
function pushHistory(p) {
  const a = [p, ...loadHistory().filter(x => x !== p)].slice(0, 10);
  try { localStorage.setItem(HISTORY_KEY, JSON.stringify(a)); } catch (e) { /* ignore */ }
}
// ---------------------------------------------------------------- project brief (brief.md, shared with the terminal agent)
const B = { text: '', max: 2000, loaded: false, open: false, gen: 'idle', code: null, error: null, saveError: null };
const briefFirst = () => (B.text.split('\n').map(x => x.replace(/^[\s#>*-]+/, '').trim()).find(Boolean)) || '';
async function loadBrief() {
  try { const r = await fetch('/api/brief', { cache: 'no-store' }); if (r.ok) { const d = await r.json(); B.text = d.text || ''; B.max = d.max || 2000; B.loaded = true; renderAll(); } } catch (e) { /* offline */ }
}
async function saveBrief(text) {
  text = text.trim();
  if (text === B.text) return true;
  try {
    const r = await fetch('/api/brief', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }) });
    if (!r.ok) { B.saveError = r.status === 400 ? 'tooLong' : 'failed'; return false; }
    B.text = (await r.json()).text || ''; B.saveError = null; return true;
  } catch (e) { B.saveError = 'failed'; return false; }
}
async function generateBrief() {
  if (B.gen === 'running') return;
  Object.assign(B, { gen: 'running', code: null, error: null, open: true }); renderAll();
  let d = null;
  try { const r = await post('/api/brief/generate', { lang: getLang() }); d = r.ok ? r.data : { status: 'error', code: r.status === 409 ? 'busy' : 'failed', error: (r.data && r.data.error) || '' }; }
  catch (e) { d = { status: 'error', code: 'failed', error: '' }; }
  while (d && d.status === 'running') {
    await new Promise(res => setTimeout(res, 800));
    try { const r = await fetch('/api/brief/generate', { cache: 'no-store' }); if (r.ok) d = await r.json(); } catch (e) { /* keep polling */ }
  }
  if (d && d.status === 'done') { B.text = (d.brief && d.brief.text) || B.text; Object.assign(B, { gen: 'idle', code: null, error: null }); }
  else if (d && d.status === 'cancelled') Object.assign(B, { gen: 'idle', code: null, error: null });
  else Object.assign(B, { gen: 'idle', code: (d && d.code) || 'failed', error: (d && d.error) || '' });
  renderAll();
}
async function cancelBrief() { try { await post('/api/brief/generate/cancel'); } catch (e) { /* the poll reports it */ } }
const briefErrText = () => t('ai.brief.err.' + (['no_session', 'unsupported', 'no_agent', 'timeout', 'login', 'limit', 'empty', 'busy'].includes(B.code) ? B.code : 'failed'));

function renderAll() { const ask = document.getElementById('b-ask'); if (ask) ask.classList.toggle('busy', R.status === 'running'); for (const v of [...views]) { if (!v.root.isConnected) views.delete(v); else v.render(); } }
onLang(() => renderAll());
setInterval(() => { phIndex = (phIndex + 1) % PLACEHOLDERS; for (const v of views) if (v.root.isConnected) v.placeholder(); }, 4000);

// ---------------------------------------------------------------- job control
async function post(url, body) {
  const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  let data = null; try { data = await r.json(); } catch (e) { /* empty */ }
  return { ok: r.ok, status: r.status, data };
}

/** Is the last AI step still the top of the undo stack? */
const canUndoAi = () => !!(R.changed && !R.undone && S.lastExternal && S.lastExternal.label === 'ai' && S.undo.length === S.lastExternal.depth);
const messages = () => (T.thread && T.thread.messages) || [];
const lastUser = () => [...messages()].reverse().find(m => m.role === 'user');
const markMsg = (id, undone) => { if (T.thread && id) post('/api/ai/threads/' + T.thread.id + '/mark', { message: id, undone }).catch(() => {}); const m = messages().find(x => x.id === id); if (m) m.undone = undone; };

// ---------------------------------------------------------------- which conversation is on screen
const curIds = () => S.selection.filter(id => selectedLayers().some(l => l.id === id));
const curKey = () => { const ids = curIds().sort(); return ids.length ? ids.join(',') : 'moment'; };
async function fetchJson(url, opt) { try { const r = await fetch(url, Object.assign({ cache: 'no-store' }, opt)); return r.ok ? await r.json() : null; } catch (e) { return null; } }
async function loadThread(id) { const th = await fetchJson('/api/ai/threads/' + id); if (th) T.thread = th; return th; }

/** Still the same place? Layers: same selection. No selection: the playhead within 1 s of where the conversation started. */
function anchorHolds() {
  const a = T.anchor; if (!a || a.key !== curKey()) return false;
  return a.key !== 'moment' || S.playing || Math.abs(S.t - a.t) <= 1;
}
let syncTimer = null, syncSeq = 0;
async function syncScope() {
  if (R.status === 'running' || anchorHolds()) return;          // during playback a moment thread is never closed
  const seq = ++syncSeq, ids = curIds(), tm = Math.round(S.t * 1000) / 1000;
  const d = await fetchJson('/api/ai/threads?sel=' + encodeURIComponent(JSON.stringify(ids)) + '&t=' + tm);
  if (!d || seq !== syncSeq || R.status === 'running') return;
  T.thread = d.active; T.earlier = d.earlier || []; T.showEarlier = false;
  T.anchor = { key: ids.length ? [...ids].sort().join(',') : 'moment', t: d.active && d.active.kind === 'moment' && d.active.t != null ? d.active.t : tm };
  Object.assign(R, { status: 'idle', events: [], error: null, code: null, changed: false, undone: false, msgId: null });
  renderAll();
}
const scheduleSync = () => { clearTimeout(syncTimer); syncTimer = setTimeout(syncScope, 250); };
onChange(kind => { if (kind === 'selection' || kind === 'time' || kind === 'playing' || kind === 'project') scheduleSync(); });

async function newConversation() {
  if (R.status === 'running') return;
  const ids = curIds(), tm = Math.round(S.t * 1000) / 1000;
  const th = await fetchJson('/api/ai/threads', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ scope: { selected: ids, time: tm } }) });
  if (th) { if (T.thread && T.thread.messages && T.thread.messages.length) T.earlier = [{ id: T.thread.id, first: (lastUser() || {}).text || '', turns: 1 }, ...T.earlier]; T.thread = th; T.anchor = { key: curKey(), t: tm }; Object.assign(R, { status: 'idle', events: [], error: null, code: null, changed: false, undone: false, msgId: null }); }
  renderAll();
}
async function openEarlier(id) {
  const th = await loadThread(id); if (!th) return;
  T.earlier = T.earlier.filter(e => e.id !== id); T.showEarlier = false;
  T.anchor = { key: curKey(), t: Math.round(S.t * 1000) / 1000 };
  Object.assign(R, { status: 'idle', events: [], error: null, code: null, changed: false, undone: false, msgId: null });
  renderAll();
}

// ---------------------------------------------------------------- job control
export async function run(prompt, { retry = false } = {}) {
  prompt = String(prompt || '').trim();
  if (!prompt || R.status === 'running') return;
  const prevReply = [...messages()].reverse().find(m => m.role === 'agent');
  if (retry && canUndoAi()) { undo(); markMsg(R.msgId, true); }   // "try again" replaces the previous attempt (same conversation)
  await saveAndWait();                                            // the agent reads project.json from disk
  const ids = curIds();
  Object.assign(R, { status: 'running', prompt, events: [], next: 0, error: null, code: null, changed: false, undone: false, jobId: null, msgId: null });
  if (!retry) pushHistory(prompt);
  if (!T.anchor) T.anchor = { key: curKey(), t: Math.round(S.t * 1000) / 1000 };
  renderAll();
  const sent = retry ? prompt + '\n\n(The previous attempt was not right. The user wants a noticeably different or better result.' + (prevReply && prevReply.text ? ' What was done before: ' + prevReply.text : '') + ')' : prompt;
  let r;
  try { r = await post('/api/ai', { prompt: sent, display: prompt, retry, thread: T.thread ? T.thread.id : null, scope: { selected: ids, time: Math.round(S.t * 1000) / 1000 }, lang: getLang() }); }
  catch (e) { r = { ok: false, status: 0, data: null }; }
  if (!r.ok) {
    Object.assign(R, { status: 'error', code: r.status === 424 ? 'noAgent' : r.status === 409 ? 'busy' : 'failed', error: (r.data && r.data.error) || '' });
    return renderAll();
  }
  R.jobId = r.data.id; S.aiJob = R.jobId;
  await loadThread(r.data.thread);                                // shows the user's line right away
  renderAll();
  poll(R.jobId);
}

async function poll(id) {
  while (R.jobId === id && R.status === 'running') {
    let d = null;
    try { const r = await fetch('/api/ai/' + id + '?since=' + R.next, { cache: 'no-store' }); if (r.ok) d = await r.json(); } catch (e) { /* server busy */ }
    if (R.jobId !== id) return;
    if (d) {
      for (const e of d.events || []) R.events.push(e);
      R.next = d.next ?? R.next;
      if (d.status !== 'running') {
        Object.assign(R, { status: d.status, error: d.error, code: d.code, changed: !!d.changed, msgId: d.message || null, events: [] });
        // pick the edit up right now, still labelled as the AI's (S.aiJob is set), then release
        for (let i = 0; i < 6 && R.changed; i++) { await checkExternal(); if (S.lastExternal && S.lastExternal.job === id) break; await new Promise(res => setTimeout(res, 300)); }
        S.aiJob = null; emit('save');
        await loadThread(d.thread);
      }
      renderAll();
      if (d.status !== 'running') { scheduleSync(); return; }
    }
    await new Promise(res => setTimeout(res, 700));
  }
}

async function cancel() {
  if (R.status !== 'running' || !R.jobId) return;
  try { await post('/api/ai/' + R.jobId + '/cancel'); } catch (e) { /* the poll loop reports the outcome */ }
}

// ---------------------------------------------------------------- view
function eventLine(e) {
  const row = el('div', 'ai-ev ' + e.kind);
  const key = e.key.replace('ai.ev.', '');
  const ic = el('span', 'ai-ev-icon'); ic.append(icon(ICON[e.kind === 'text' ? 'text' : key] || ICON.working, 's')); row.append(ic);
  row.append(el('span', 'ai-ev-text', e.kind === 'text' ? e.text : t(e.key, e.params)));
  return row;
}
function visibleEvents() { // collapse runs of thinking / reading into one line
  const out = [];
  for (const e of R.events) { const last = out[out.length - 1]; if (last && last.key === e.key && (e.key === 'ai.ev.thinking' || e.key === 'ai.ev.read')) continue; out.push(e); }
  return out;
}
function scopeText() {
  const n = curIds().length;
  return n ? t('ai.scope.layers', { n }) : t('ai.scope.moment');
}
function errorText(code) {
  const k = { noAgent: 'ai.err.noAgent', login: 'ai.err.login', limit: 'ai.err.limit', timeout: 'ai.err.timeout', busy: 'ai.err.busy' }[code] || 'ai.err.failed';
  return t(k);
}

/** Notes panel: "Ask AI about this". Scopes the box to the moment at `time` (no layer selected) and pre-fills the note text. */
export async function askAbout(time, text) {
  if (curIds().length) select([]);
  setTime(time); clearTimeout(syncTimer);
  T.draft = text || ''; await syncScope(); T.draft = text || ''; renderAll();
  setTimeout(() => { const ta = [...views].map(v => v.root.querySelector('.ai-input:not([rows="8"])')).find(x => x && x.offsetParent); if (ta) { ta.scrollIntoView({ block: 'nearest' }); ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); } }, 60);
}

/** Mount an Ask-AI box into `host` (the inspector calls this under "+ Add effect" and in the project panel). */
export function mountAsk(host) {
  const root = el('div', 'ai-box'); host.append(root);
  const view = { root, render, placeholder };
  views.add(view);
  let ta, editing = false;
  const bx = el('div', 'ai-brief');       // persistent node: survives render() so an open editor keeps its text and focus
  if (!B.loaded) loadBrief();
  if (!T.anchor) syncScope();
  function briefRender() {
    if (editing) return;
    bx.innerHTML = '';
    const head = el('div', 'ai-brief-head');
    const tog = el('button', 'ai-brief-tog', (B.open ? '▾ ' : '▸ ') + t('ai.brief.title'));
    tog.addEventListener('click', () => { B.open = !B.open; renderAll(); });
    head.append(tog);
    if (!B.open) head.append(el('span', 'ai-brief-first', B.gen === 'running' ? t('ai.brief.working') : (briefFirst() || t('ai.brief.none'))));
    bx.append(head);
    if (!B.open) return;
    const body = el('div', 'ai-brief-body');
    body.append(el('div', 'ai-brief-text' + (B.text ? '' : ' empty'), B.text || t('ai.brief.none')));
    const acts = el('div', 'ai-actions');
    const ed = el('button', 'pbtn', t('ai.brief.edit')); ed.disabled = B.gen === 'running';
    ed.addEventListener('click', () => startEdit());
    acts.append(ed);
    if (B.gen === 'running') {
      const w = el('span', 'ai-ev working'); w.append(el('span', 'ai-spin'), el('span', 'ai-ev-text', t('ai.brief.working')));
      const c = el('button', 'pbtn', t('ai.cancel')); c.addEventListener('click', cancelBrief);
      acts.append(w, c);
    } else {
      const g = el('button', 'pbtn', t('ai.brief.summarise')); g.title = t('ai.brief.summariseTip'); g.addEventListener('click', generateBrief);
      acts.append(g);
    }
    body.append(acts);
    if (B.gen !== 'running' && B.code) body.append(el('div', 'ai-final err', briefErrText()));
    if (B.saveError) body.append(el('div', 'ai-final err', t('ai.brief.err.' + B.saveError)));
    bx.append(body);
  }
  function startEdit() {
    editing = true; bx.innerHTML = '';
    const area = el('textarea', 'ai-input'); area.rows = 8; area.spellcheck = false; area.value = B.text; area.maxLength = B.max;
    area.placeholder = t('ai.brief.ph');
    const hint = el('div', 'ai-detail', t('ai.brief.editHint'));
    bx.append(el('div', 'ai-brief-head', '▾ ' + t('ai.brief.title')), area, hint);
    let done = false;
    const finish = async (save) => {
      if (done) return; done = true;
      if (save) await saveBrief(area.value);
      editing = false; renderAll();
    };
    area.addEventListener('blur', () => finish(true));
    area.addEventListener('keydown', e => { if (e.key === 'Escape') { e.preventDefault(); finish(false); } });
    area.focus();
  }
  function placeholder() { if (ta) ta.placeholder = t(messages().length ? 'ai.ph.follow' : 'ai.ph.' + (phIndex + 1)); }

  function render() {
    const busy = R.status === 'running';
    const keepFocus = ta && document.activeElement === ta, sel = ta ? [ta.selectionStart, ta.selectionEnd] : null;
    root.innerHTML = '';
    root.append(bx); briefRender();
    const title = el('div', 'ai-title'); title.append(icon('clapperboard', 's'), el('span', '', t('ai.title'))); root.append(title);
    const hasThread = messages().length > 0;
    const head = el('div', 'ai-row');
    head.append(el('span', 'ai-scope', scopeText()));
    if (T.earlier.length && !busy) { const ec = el('button', 'ai-chip', t('ai.earlier', { n: T.earlier.length })); ec.addEventListener('click', () => { T.showEarlier = !T.showEarlier; renderAll(); }); head.append(ec); }
    if (hasThread && !busy) { const nb = el('button', 'pbtn', t('ai.new')); nb.title = t('ai.newTip'); nb.addEventListener('click', newConversation); head.append(nb); }
    root.append(head);
    if (T.showEarlier && T.earlier.length) {
      const list = el('div', 'ai-chips');
      for (const e of T.earlier) { const c = el('button', 'ai-chip', e.first || '…'); c.title = e.first || ''; c.addEventListener('click', () => openEarlier(e.id)); list.append(c); }
      root.append(list);
    }
    if (hasThread || busy || R.status === 'error') root.append(threadBlock());
    ta = el('textarea', 'ai-input'); ta.rows = 2; ta.spellcheck = false; ta.value = T.draft; ta.disabled = busy;
    ta.addEventListener('input', () => { T.draft = ta.value; go.disabled = !ta.value.trim(); });
    ta.addEventListener('keydown', e => { if (e.key === 'Enter' && isMod(e)) { e.preventDefault(); submit(); } });
    placeholder();
    const go = el('button', 'pbtn ai-go', t('ai.ask')); go.title = t('ai.askTip'); go.disabled = busy || !T.draft.trim();
    go.addEventListener('click', submit);
    const row = el('div', 'ai-row');
    row.append(el('span', 'ai-scope'));
    if (B.text) { const bd = el('span', 'ai-brief-badge', t('ai.brief.badge')); bd.title = t('ai.brief.badgeTip'); row.append(bd); }
    row.append(go);
    root.append(ta, row);
    if (!busy && !hasThread) {
      const hist = loadHistory();
      if (hist.length) {
        const chips = el('div', 'ai-chips');
        for (const p of hist) { const c = el('button', 'ai-chip', p); c.title = p; c.addEventListener('click', () => { T.draft = p; render(); ta.focus(); }); chips.append(c); }
        root.append(chips);
      }
    }
    if (keepFocus && !busy) { ta.focus(); if (sel) ta.setSelectionRange(sel[0], sel[1]); }
  }

  /** Calm thread: your lines and the agent's short replies stacked; progress only for the turn that is running. */
  function threadBlock() {
    const box = el('div', 'ai-thread');
    const msgs = messages();
    const lastAgent = [...msgs].reverse().find(m => m.role === 'agent');
    for (const m of msgs) {
      if (m.role === 'user') { box.append(el('div', 'ai-msg user', (m.retry ? '↻ ' : '') + m.text)); continue; }
      const row = el('div', 'ai-msg agent' + (m.status === 'error' ? ' err' : '') + (m.undone ? ' undone' : ''));
      const txt = m.status === 'error' ? errorText(m.code) : m.status === 'cancelled' ? t('ai.cancelled') : (m.text || '');
      row.append(el('div', 'ai-final' + (m.status === 'error' ? ' err' : ''), txt));
      if (m.status === 'error' && m.text) row.append(el('div', 'ai-detail', String(m.text).slice(0, 300)));
      if (m.status === 'done' && !m.changed) row.append(el('div', 'ai-detail', t('ai.noChange')));
      const acts = el('div', 'ai-actions');
      const isLast = m === lastAgent && R.status !== 'running';
      if (m.changed && isLast && R.msgId === m.id && (canUndoAi() || (R.undone && S.redo.length))) {
        const u = el('button', 'pbtn', R.undone ? t('ai.redo') : t('ai.undo'));
        u.title = R.undone ? t('ai.redoTip') : t('ai.undoTip');
        u.addEventListener('click', () => {
          if (R.undone) { if (S.redo.length) redo(); R.undone = false; markMsg(m.id, false); }
          else if (canUndoAi()) { undo(); R.undone = true; markMsg(m.id, true); }
          renderAll();
        });
        acts.append(u);
      } else if (m.changed && m.undone) acts.append(el('span', 'ai-time', t('ai.undone')));
      if (isLast && lastUser() && m.status !== 'cancelled') { const a = el('button', 'pbtn', t('ai.retry')); a.title = t('ai.retryTip'); a.addEventListener('click', () => run(lastUser().text, { retry: true })); acts.append(a); }
      if (m.status === 'done' && m.elapsed) acts.append(el('span', 'ai-time', t('ai.took', { s: Math.round(m.elapsed) })));
      if (acts.childNodes.length) row.append(acts);
      box.append(row);
    }
    if (R.status === 'running') {
      const st = el('div', 'ai-status running');
      const list = el('div', 'ai-events');
      for (const e of visibleEvents()) list.append(eventLine(e));
      const w = el('div', 'ai-ev working'); w.append(el('span', 'ai-spin'), el('span', 'ai-ev-text', t('ai.working')));
      list.append(w); st.append(list);
      setTimeout(() => { list.scrollTop = list.scrollHeight; }, 0);
      const c = el('button', 'pbtn', t('ai.cancel')); c.addEventListener('click', cancel);
      const acts = el('div', 'ai-actions'); acts.append(c); st.append(acts);
      box.append(st);
    } else if (R.status === 'error' && !R.msgId) {      // the request itself failed (no agent installed, busy ...): no turn was recorded
      const st = el('div', 'ai-status error');
      st.append(el('div', 'ai-final err', errorText(R.code)));
      if (R.error && R.code !== 'noAgent') st.append(el('div', 'ai-detail', String(R.error).slice(0, 300)));
      if (R.code === 'noAgent') st.append(el('div', 'ai-detail', t('ai.err.installHint')));
      box.append(st);
    }
    setTimeout(() => { if (box.isConnected) box.scrollTop = box.scrollHeight; }, 0);
    return box;
  }

  function submit() { const p = T.draft.trim(); if (p && R.status !== 'running') { T.draft = ''; run(p); } }

  render();
  return view;
}

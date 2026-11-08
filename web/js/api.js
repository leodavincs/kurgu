// api.js — server calls (SPEC v2 §5), autosave (600 ms) with 409 handling, external-change polling,
// import upload with progress, and the editor state pushed for Claude (PUT /api/state).
import { S, hooks, onChange, clone, loadExternal, normalizeProject } from './state.js';
import { makeSummary } from './summary.js';
import { mergeProjects } from './merge.js';
import { t, getLang } from './i18n.js';

async function json(url, opt) {
  const r = await fetch(url, opt);
  let data = null; try { data = await r.json(); } catch (e) { /* empty body */ }
  return { ok: r.ok, status: r.status, data, headers: r.headers };
}
/** `keepalive`: the request outlives the page (a save fired while the tab reloads or closes). Browsers cap keepalive bodies at 64 KiB in BYTES,
 *  shared with every other keepalive request in flight (the state push uses one too), so the size is measured in UTF-8 bytes with headroom;
 *  a larger body, or a keepalive fetch the browser refuses, goes out as a normal request (it still leaves while a leave dialog is open). */
const KEEPALIVE_MAX_BYTES = 48000;
const sendJson = async (url, method, body, keepalive = false) => {
  const s = JSON.stringify(body);
  const opt = { method, headers: { 'Content-Type': 'application/json' }, body: s };
  if (keepalive && new TextEncoder().encode(s).length <= KEEPALIVE_MAX_BYTES) {
    try { return await json(url, { ...opt, keepalive: true }); } catch (e) { /* refused (quota): send it the normal way below */ }
  }
  return json(url, opt);
};
/** This tab's id: the server remembers which tab wrote each version of project.json, so the tab knows its own writes when it sees them again. */
const CLIENT = Math.random().toString(36).slice(2, 10) + Date.now().toString(36);

/** `v` = the file's mtime (ns) from /api/media-info or /api/version: a re-rendered file gets a new URL, so no cache (or element) can keep showing the old one. */
export const mediaUrl = (path, v) => '/media?path=' + encodeURIComponent(path) + (v != null && v !== '' ? '&v=' + v : '');

/** Known server errors are translated; anything else is shown as the server sent it. */
export function errorText(status, data) {
  const msg = data && data.error ? String(data.error) : '';
  if (status === 415) return t('err.unsupportedType');
  if (status === 409 && /render/i.test(msg)) return t('err.renderRunning');
  if (status === 404) return t('err.notFound');
  return msg || String(status);
}

export async function loadProject() {
  const r = await json('/api/project');
  if (!r.ok) throw new Error(errorText(r.status, r.data));
  return r.data; // { project, version }
}
export async function fetchFonts() {
  const r = await json('/api/fonts');
  if (!r.ok) return [];
  const list = Array.isArray(r.data) ? r.data : (r.data && Array.isArray(r.data.fonts) ? r.data.fonts : []);
  list.scanning = r.headers.get('X-Kurgu-Fonts-Scanning') === '1';   // system fonts are still being scanned: ask again later
  return list;
}
export async function mediaInfo(path) {
  try { const r = await json('/api/media-info?path=' + encodeURIComponent(path)); return r.ok ? r.data : null; } catch (e) { return null; }
}
/** Versions kept by the server for a media file (newest first; `current` marks the one in place): [{mtime_ns, size, current, url}]. */
export async function mediaVersions(path) {
  try { const r = await json('/api/versions?path=' + encodeURIComponent(path)); return r.ok && r.data ? r.data.versions || [] : []; } catch (e) { return []; }
}
const waveCache = new Map();
export function waveform(path) {
  if (!waveCache.has(path)) waveCache.set(path, json('/api/waveform?path=' + encodeURIComponent(path)).then(r => (r.ok ? r.data : null)).catch(() => null));
  return waveCache.get(path);
}
const thumbCache = new Map();
/** A media file was replaced: forget what was computed from the old one (waveform, filmstrip). */
export function invalidateMedia(path) {
  waveCache.delete(path);
  for (const k of [...thumbCache.keys()]) if (k.startsWith(path + '#')) thumbCache.delete(k);
}
const mediaWatchers = [];
/** fn(media) gets the {src: {m, s}} map of every /api/version answer (the editor's existing 1 s poll). */
export const onMediaStamps = fn => { mediaWatchers.push(fn); };
/** Evenly spaced filmstrip thumbnails for a video: resolves to an array of URLs, or null when unavailable. */
/** `pr` 0 = on screen now (served first by the server's job queue), 1 = the rest. */
export function thumbs(path, n = 40, pr = 1) {
  const key = path + '#' + n;
  if (!thumbCache.has(key)) {
    thumbCache.set(key, json('/api/thumbs?path=' + encodeURIComponent(path) + '&n=' + n + (pr === 0 ? '&pr=0' : '')).then(r => {
      if (!r.ok || !r.data) return null;
      const list = Array.isArray(r.data) ? r.data : (r.data.thumbs || r.data.urls || null);
      return Array.isArray(list) && list.length ? list.map(x => (typeof x === 'string' ? x : x.url)) : null;
    }).catch(() => null));
  }
  return thumbCache.get(key);
}
export async function listFiles() { const r = await json('/api/files'); return r.ok && Array.isArray(r.data) ? r.data : []; }
export const startRender = draft => sendJson('/api/render', 'POST', { draft: !!draft });
export async function renderStatus() { const r = await json('/api/render'); return r.ok ? r.data : null; }
// export dialog (web/js/export.js)
export const startRenderBody = body => sendJson('/api/render', 'POST', body);
export const cancelRender = () => sendJson('/api/render/cancel', 'POST', {});
export const exportPlan = body => sendJson('/api/render/plan', 'POST', body);
export const exportReveal = path => sendJson('/api/exports/reveal', 'POST', { path });
export async function exportPresets() { const r = await json('/api/export-presets'); return r.ok ? r.data : null; }
export async function exportsList() { const r = await json('/api/exports'); return r.ok && Array.isArray(r.data) ? r.data : []; }
// watch + note (web/js/watch.js)
let infoCache = null;
export async function notesInfo(fresh = false) { if (infoCache && !fresh) return infoCache; try { const r = await json('/api/notes/info'); if (r.ok) infoCache = r.data; return r.ok ? r.data : null; } catch (e) { return null; } }
export const answerNote = (id, answer) => serverWrite('/api/notes/answer', { id, answer });
export async function notesLive() { try { const r = await json('/api/notes/info'); return r.ok ? r.data : null; } catch (e) { return null; } }   // uncached: listening / wake change while the editor is open
export const sendNotes = (ids, before) => serverWrite('/api/notes/send', { ids }, before);
export const frameUrl = time => '/api/frame?t=' + time.toFixed(3) + '&v=' + encodeURIComponent(S.version || '');

/** Upload one file (raw body) with progress callback (0-1). Resolves to {ok, status, data}. */
export function importFile(file, onProgress) {
  return new Promise(resolve => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/import?name=' + encodeURIComponent(file.name));
    xhr.upload.onprogress = e => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
    xhr.onload = () => { let data = null; try { data = JSON.parse(xhr.responseText); } catch (e) { /* not JSON */ } resolve({ ok: xhr.status >= 200 && xhr.status < 300, status: xhr.status, data }); };
    xhr.onerror = () => resolve({ ok: false, status: 0, data: { error: 'network error' } });
    xhr.send(file);
  });
}

/** Toast text after a merge: what was kept from each side (at most 3 names per side). */
const listOf = xs => { const u = [...new Set(xs.map(x => (x.name ? `“${x.name}” ${x.field}` : x.field).trim()))]; return u.slice(0, 3).join(', ') + (u.length > 3 ? ' …' : ''); };
function conflictText({ applied, conflicts }) {
  if (!conflicts.length) return applied.length ? t('toast.merged') : t('toast.mergedNothing');
  return t('toast.mergedClash', { mine: applied.length ? listOf(applied) : '—', theirs: listOf(conflicts) });
}
/** A merge with this editor's OWN server write (send / answer stamps, Animate) that could not take the user's change: no agent involved.
 *  The usual case is a note deleted while it was being sent: the server had already stamped it, so it stays. */
const ownClashText = ({ conflicts }) => (conflicts.every(c => c.scope === 'note')
  ? t('toast.keptSent', { notes: listOf(conflicts.map(c => ({ name: c.name, field: '' }))) })
  : t('toast.keptOwn', { what: listOf(conflicts) }));

// ---------- autosave ----------
let timer = null, saving = false, again = false;
let saveCount = 0; // increments after each finished save (avoids a poll/save race)
let toast = () => {};
let emitSave = () => {};
export function bindToast(fn) { toast = fn; }
export function bindSaveEmit(fn) { emitSave = fn; }

hooks.dirty = () => {
  emitSave();
  clearTimeout(timer);
  timer = setTimeout(save, 600);
};

/** `leaving`: the tab is reloading / closing (beforeunload, pagehide): timers stop now, so the PUT goes out at once, even while a server write
 *  of ours is out, and survives the page (keepalive). If it reaches the server before that write, the write builds on it; if after, it is
 *  refused (409) and the server's write stays as it is. */
export async function save(opts) {
  const leaving = !!(opts && opts.leaving);
  clearTimeout(timer);
  if (!S.project) return true;
  if (saving) { again = true; return false; }
  if (ownBusy() && !leaving) { timer = setTimeout(save, 250); return false; }      // the server is writing for us: the edit is merged into its answer, then saved
  const now = clone(S.project);
  if (JSON.stringify(now) === JSON.stringify(S.savedBase)) { S.saveState = 'saved'; emitSave(); return true; }
  const summary = makeSummary(S.savedBase, now);
  if (!summary.length) summary.push(t('sum.projectUpdated'));
  saving = true; S.saveState = 'saving'; emitSave();
  let ok = false;
  try {
    const r = await sendJson('/api/project', 'PUT', { project: now, summary, base_version: S.version, client: CLIENT }, leaving);
    if (r.ok) {
      S.version = r.data.version; S.savedBase = now; ok = true; remember(r.data.version);
      S.saveState = JSON.stringify(S.project) === JSON.stringify(now) ? 'saved' : 'dirty';
    } else if (r.status === 409) {
      let data = r.data;
      if (!data || !data.project) data = await loadProject();
      // the agent edited the file meanwhile: keep the user's unsaved edits, re-apply them on the server's version (M8)
      const mine = (r.data && r.data.writer === CLIENT) || written.includes(data.version);   // ... or it was our own send / answer (one that ran past the limit)
      const server = normalizeProject(clone(data.project));
      const res = mergeProjects(S.savedBase, clone(S.project), server);
      loadExternal(data.project, data.version, S.aiJob ? 'ai' : 'external', res.merged);
      if (!mine) toast(conflictText(res), res.conflicts.length ? 'warn' : 'info', res.conflicts.length ? 9000 : 5000);
      else if (res.conflicts.length) toast(ownClashText(res), 'warn', 9000);
    } else { S.saveState = 'error'; toast(t('toast.saveFailed', { reason: errorText(r.status, r.data) }), 'error', 6000); }
  } catch (e) {
    S.saveState = 'error'; toast(t('toast.saveOffline'), 'error', 6000);
  }
  saving = false; saveCount++;
  emitSave();
  if (S.saveState === 'dirty' || again) { again = false; if (S.saveState === 'dirty') timer = setTimeout(save, 200); }
  if (S.saveState === 'error') timer = setTimeout(save, 3000);
  return ok;
}
/** Wait until nothing is pending (used before render / real-frame requests). */
export async function saveAndWait() {
  clearTimeout(timer);
  while (ownBusy()) await new Promise(r => setTimeout(r, 80));   // a send / answer / Animate of ours is out: its answer first (bounded by ownMax)
  for (let i = 0; i < 20; i++) {
    if (!saving && S.saveState !== 'dirty') return true;
    if (!saving && !ownBusy()) await save(); else await new Promise(r => setTimeout(r, 80));
  }
  return false;
}
export const savePending = () => saving || S.saveState === 'dirty';

// ---------- the editor's own writes done by the server ----------
// Send, answer and "Animate" make the SERVER write project.json for the editor, which then takes the project from the answer. On a slow
// machine that answer can come seconds after the write. Meanwhile the 1 s poll must not take the new file for an outside change (it would
// toast "changed outside", reload a second time and empty the watch list for a moment) and the autosave waits: an edit made meanwhile is
// merged into the answer's project and saved on top of it, never overwritten by it. A request that hangs holds neither back for more than
// a minute (window.__kurguOwnWriteMaxMs, for tests).
const own = new Set();            // requests in flight: {t0}
let ownGen = 0;
const ownMax = () => Number(window.__kurguOwnWriteMaxMs) || 60000;
const ownBusy = () => { const now = Date.now(); for (const w of own) if (now - w.t0 < ownMax()) return true; return false; };
const written = [];               // versions this editor wrote itself (its saves, its server writes): never "outside"
const remember = v => { if (v && !written.includes(v)) { written.push(v); if (written.length > 30) written.shift(); } };
export async function ownWrite(fn) {
  const w = { t0: Date.now() };
  own.add(w); ownGen++;
  try { return await fn(w); } finally { own.delete(w); ownGen++; }
}
/** Toast key for a project that changed underneath the editor (decided on the project as it was before the reload). */
export function outsideKey() {
  if (S.aiJob) return 'toast.aiUpdate';
  return S.project && S.project.notes.some(n => n.author === 'user' && n.sent && !n.done) ? 'toast.agentUpdate' : 'toast.externalUpdate';   // notes are out with the agent: most likely its work
}
/** POST to an endpoint that writes project.json for the editor and answers {project, version, previous_version?} (send, answer, Animate).
 *  The answer's project is taken in here as one undo step: merged with what the user changed meanwhile, with no toast for the server's own
 *  write. A toast only when someone else wrote the file between our last save and this write (`previous_version` is neither our base
 *  nor a version we wrote). `before(data)` runs just before the project is taken in. Resolves to {ok, status, data} like sendJson. */
export async function serverWrite(url, body, before) {
  let resync = false;
  const r = await ownWrite(async w => {
    const base = S.version;
    const r = await sendJson(url, 'POST', { ...body, base_version: base, client: CLIENT });
    if (!r.ok || !r.data || !r.data.project) return r;
    const d = r.data;
    remember(d.version);
    while (saving) await new Promise(z => setTimeout(z, 50));           // a save that was already running when we asked: let it land first
    if (before) before(d);
    // take it only if it builds on what the editor has; else (a hung request the poll has overtaken, or a later write of ours already in)
    // leave the project alone and let the poll compare with the file
    if (!(S.version === base || (d.previous_version && d.previous_version === S.version))) { resync = true; return r; }
    const outside = d.previous_version && d.previous_version !== base && !written.includes(d.previous_version) ? outsideKey() : '';
    if (JSON.stringify(S.project) !== JSON.stringify(S.savedBase)) {   // the user edited meanwhile: keep it, on top of the server's version
      const res = mergeProjects(S.savedBase, clone(S.project), normalizeProject(clone(d.project)));
      loadExternal(d.project, d.version, 'external', res.merged);
      if (res.conflicts.length) toast(ownClashText(res), 'warn', 9000);
    } else loadExternal(d.project, d.version);
    if (outside) toast(t(outside), 'info', 3500);
    return r;
  });
  if (resync) checkExternal();
  return r;
}

// ---------- external change polling ----------
let lastPing = Date.now();
export function startPolling() {
  setInterval(() => {
    if (!document.hidden) { lastPing = Date.now(); checkExternal(); return; }
    // a background tab does not poll, but it is still an open editor: one slow ping tells the server not to stop itself for being idle
    if (Date.now() - lastPing >= (Number(window.__kurguHiddenPingMs) || 60000)) { lastPing = Date.now(); fetch('/api/version', { cache: 'no-store' }).catch(() => {}); }
  }, 1000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) checkExternal(); });   // back in front: look at once
}
/** Look for a changed project.json now (the AI panel also calls this when a job ends). Every reload is one undo step. */
export async function checkExternal() {
  if (!S.project) return;
  const busy = saving || S.saveState === 'dirty' || ownBusy();    // a pending save / own server write only holds back the project reload, not the media refresh
  const count = saveCount, gen = ownGen;
  const moved = () => count !== saveCount || saving || S.saveState === 'dirty' || gen !== ownGen || ownBusy();
  try {
    const r = await json('/api/version');
    if (!r.ok || !r.data) return;
    for (const f of mediaWatchers) { try { f(r.data.media || {}); } catch (e) { console.error(e); } }
    if (busy || moved()) return;
    if (r.data.version === S.version) return;
    const v = await loadProject();
    if (moved() || v.version === S.version) return;     // a save or an own write ran meanwhile, or its answer already loaded this version
    const fresh = normalizeProject(clone(v.project));
    if (JSON.stringify(fresh) === JSON.stringify(S.project)) { S.version = v.version; S.savedBase = clone(S.project); return; }
    if (v.version === r.data.version && r.data.writer === CLIENT) { loadExternal(v.project, v.version); return; }   // our own write (a send that ran past the limit): no toast
    const key = outsideKey();
    loadExternal(v.project, v.version, S.aiJob ? 'ai' : 'external');
    toast(t(key), 'info', 3500);
  } catch (e) { /* server briefly unavailable */ }
}

// ---------- state for Claude (PUT /api/state) ----------
// Debounced 300 ms; while playing at most every 2 s. This is how Claude knows what "this text" / "here" means.
let stateTimer = null, statePending = false, stateLast = 0;
function pushState() {
  statePending = false; stateLast = performance.now();
  const body = { time: Math.round(S.t * 1000) / 1000, selected: S.selection.slice(), playing: S.playing, language: getLang() };
  fetch('/api/state', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), keepalive: true }).catch(() => {});
}
function scheduleState() {
  clearTimeout(stateTimer); statePending = true;
  const wait = S.playing ? Math.max(300, 2000 - (performance.now() - stateLast)) : 300;
  stateTimer = setTimeout(pushState, wait);
}
export function startStateSync() {
  onChange(kind => {
    if (kind === 'selection' || kind === 'lang' || kind === 'playing') scheduleState();
    else if (kind === 'time') { if (!S.playing) scheduleState(); else if (!statePending) scheduleState(); }
  });
  scheduleState();
}

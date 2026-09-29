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
const sendJson = (url, method, body) => json(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

export const mediaUrl = path => '/media?path=' + encodeURIComponent(path);

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
const waveCache = new Map();
export function waveform(path) {
  if (!waveCache.has(path)) waveCache.set(path, json('/api/waveform?path=' + encodeURIComponent(path)).then(r => (r.ok ? r.data : null)).catch(() => null));
  return waveCache.get(path);
}
const thumbCache = new Map();
/** Evenly spaced filmstrip thumbnails for a video: resolves to an array of URLs, or null when unavailable. */
export function thumbs(path, n = 40) {
  const key = path + '#' + n;
  if (!thumbCache.has(key)) {
    thumbCache.set(key, json('/api/thumbs?path=' + encodeURIComponent(path) + '&n=' + n).then(r => {
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
function conflictText({ applied, conflicts }) {
  const list = xs => { const u = [...new Set(xs.map(x => (x.name ? `“${x.name}” ${x.field}` : x.field).trim()))]; return u.slice(0, 3).join(', ') + (u.length > 3 ? ' …' : ''); };
  if (!conflicts.length) return applied.length ? t('toast.merged') : t('toast.mergedNothing');
  return t('toast.mergedClash', { mine: applied.length ? list(applied) : '—', theirs: list(conflicts) });
}

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

export async function save() {
  clearTimeout(timer);
  if (!S.project) return true;
  if (saving) { again = true; return false; }
  const now = clone(S.project);
  if (JSON.stringify(now) === JSON.stringify(S.savedBase)) { S.saveState = 'saved'; emitSave(); return true; }
  const summary = makeSummary(S.savedBase, now);
  if (!summary.length) summary.push(t('sum.projectUpdated'));
  saving = true; S.saveState = 'saving'; emitSave();
  let ok = false;
  try {
    const r = await sendJson('/api/project', 'PUT', { project: now, summary, base_version: S.version });
    if (r.ok) {
      S.version = r.data.version; S.savedBase = now; ok = true;
      S.saveState = JSON.stringify(S.project) === JSON.stringify(now) ? 'saved' : 'dirty';
    } else if (r.status === 409) {
      let data = r.data;
      if (!data || !data.project) data = await loadProject();
      // the agent edited the file meanwhile: keep the user's unsaved edits, re-apply them on the server's version (M8)
      const server = normalizeProject(clone(data.project));
      const res = mergeProjects(S.savedBase, clone(S.project), server);
      loadExternal(data.project, data.version, S.aiJob ? 'ai' : 'external', res.merged);
      toast(conflictText(res), res.conflicts.length ? 'warn' : 'info', res.conflicts.length ? 9000 : 5000);
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
  for (let i = 0; i < 20; i++) {
    if (!saving && S.saveState !== 'dirty') return true;
    if (!saving) await save(); else await new Promise(r => setTimeout(r, 80));
  }
  return false;
}
export const savePending = () => saving || S.saveState === 'dirty';

// ---------- external change polling ----------
export function startPolling() { setInterval(checkExternal, 1000); }
/** Look for a changed project.json now (the AI panel also calls this when a job ends). Every reload is one undo step. */
export async function checkExternal() {
  if (!S.project || saving || S.saveState === 'dirty') return;
  const count = saveCount;
  try {
    const r = await json('/api/version');
    if (!r.ok || !r.data) return;
    if (count !== saveCount || saving || S.saveState === 'dirty') return;
    if (r.data.version === S.version) return;
    const v = await loadProject();
    if (count !== saveCount || saving || S.saveState === 'dirty') return;
    const fresh = normalizeProject(clone(v.project));
    if (JSON.stringify(fresh) === JSON.stringify(S.project)) { S.version = v.version; S.savedBase = clone(S.project); return; }
    const ai = !!S.aiJob;
    loadExternal(v.project, v.version, ai ? 'ai' : 'external');
    toast(t(ai ? 'toast.aiUpdate' : 'toast.externalUpdate'), 'info', 3500);
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

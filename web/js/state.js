// state.js — project state, normalisation (SPEC v2 §1), undo/redo, selection, layer operations, shared helpers.
// Every edit goes through change(): it snapshots for undo, schedules an autosave and notifies the UI.
import { t } from './i18n.js';

export const S = {
  project: null,
  version: null,        // last known server version (sha1 of project.json)
  savedBase: null,      // last saved/loaded project (base for the change summary)
  selection: [],        // selected layer ids (in selection order)
  t: 0,                 // playhead, seconds
  playing: false,
  shuttle: 0,           // J/K/L shuttle speed shown in the transport: -4…-1 reverse, 2 / 4 fast forward, 0 = normal / stopped
  scrubbing: false,   // true while the user drags the playhead
  snap: true,
  focus: 'general',     // 'preview' | 'layers' | 'general' (what arrow keys mean)
  saveState: 'saved',   // 'saved' | 'saving' | 'dirty' | 'conflict' | 'error'
  realFrame: false,     // compare preview with a real render frame
  view: { zoom: 'fit', px: 0, py: 0, safe: false, social: false }, // viewer: zoom ('fit' | factor), pan offset (css px), guide overlays
  fonts: [],            // font registry from /api/fonts
  fontMap: new Map(),   // id -> face
  media: new Map(),     // path -> /api/media-info result
  lastImported: null,   // path of the file imported last (Media panel highlights it)
  undo: [], redo: [],
  lastKey: null, lastTime: 0,
  collapsed: new Set(), // collapsed layer-group names
  aiJob: null,          // id of the running "Ask AI" job (external reloads during it are labelled 'ai')
  lastExternal: null,   // { label, job, depth }: the undo step made by the last external reload
};

// hooks.dirty is set by api.js (autosave scheduling)
export const hooks = { dirty: () => {} };
const listeners = [];
export function onChange(fn) { listeners.push(fn); }
/** Notify: kinds are 'project' 'selection' 'time' 'playing' 'save' 'settings' 'media' 'fonts' 'lang' 'view'. */
export function emit(kind) { for (const f of listeners) { try { f(kind); } catch (e) { console.error(e); } } }

// ---------- helpers ----------
export const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
/** Coerce to a finite number, accepting "12,5". */
export function num(v, dflt) { const n = typeof v === 'string' ? parseFloat(v.replace(',', '.')) : Number(v); return Number.isFinite(n) ? n : dflt; }
export function timecode(s) {
  s = Math.max(0, s || 0); const m = Math.floor(s / 60); const r = s - m * 60;
  return String(m).padStart(2, '0') + ':' + r.toFixed(2).padStart(5, '0');
}
export function newId(prefix = 'l') {
  const used = new Set([...(S.project?.layers || []).map(l => l.id), ...(S.project?.notes || []).map(n => n.id)]);
  let id; do { id = prefix + '_' + Math.random().toString(36).slice(2, 7); } while (used.has(id));
  return id;
}
const KNOWN = [[16, 9], [9, 16], [1, 1], [4, 5], [5, 4], [4, 3], [3, 4], [21, 9], [9, 21], [2, 1], [1, 2], [3, 2], [2, 3]];

/** "16:9", "9:16", "21:9" … for known ratios (2 % tolerance), else "1.85:1" / "1:1.85". */
export function aspectLabel(w, h) {
  const r = w / h;
  for (const [a, b] of KNOWN) if (Math.abs(r / (a / b) - 1) < 0.02) return a + ':' + b;
  const f = v => String(Math.round(v * 100) / 100);
  return r >= 1 ? f(r) + ':1' : '1:' + f(1 / r);
}
/** Snap a time to the project's frame grid (1/fps), tidied to 4 decimals so files stay readable (3.1667, not 3.16666666…). */
export function snapFrame(time, fps = S.project ? S.project.fps : 30) {
  const f = fps > 0 ? fps : 30;
  return Math.round(Math.round(time * f) / f * 1e4) / 1e4;
}
/** Same, rounding UP (a clip's end: never cut its last frame). */
export function snapFrameUp(time, fps = S.project ? S.project.fps : 30) {
  const f = fps > 0 ? fps : 30;
  return Math.round(Math.ceil(time * f - 1e-6) / f * 1e4) / 1e4;
}
export const clone = o => JSON.parse(JSON.stringify(o));
export const VISUAL = new Set(['video', 'image', 'text', 'color']);
export const HAS_AUDIO = new Set(['video', 'audio']);
export const TYPES = ['video', 'image', 'text', 'color', 'audio'];
/** Lucide icon name per layer / media type (see web/icons/). */
export const TYPE_ICON = { video: 'video', image: 'image', text: 'type', color: 'square', audio: 'music' };
export const typeName = type => t('type.' + type);
export const basename = p => String(p || '').split('/').pop();

// ---------- normalisation ----------
export function normalizeLayer(l, P) {
  const W = P.width, H = P.height;
  l = l && typeof l === 'object' ? l : {};
  if (!l.id) l.id = 'l_' + Math.random().toString(36).slice(2, 7);
  if (!TYPES.includes(l.type)) return l; // unknown type: leave untouched
  l.name = l.name || typeName(l.type);
  l.group = typeof l.group === 'string' ? l.group : '';
  if (typeof l.link === 'string' && l.link) { /* linked clips (SPEC §1.1a) */ } else delete l.link;
  l.hidden = !!l.hidden; l.locked = !!l.locked;
  l.start = num(l.start, 0);
  l.end = num(l.end, l.start + 3);
  if (l.end < l.start) l.end = l.start;
  l.fade_in = Math.max(0, num(l.fade_in, 0)); l.fade_out = Math.max(0, num(l.fade_out, 0));
  if (VISUAL.has(l.type)) {
    l.x = num(l.x, W / 2); l.y = num(l.y, H / 2);
    l.scale = num(l.scale, 1); l.opacity = clamp(num(l.opacity, 1), 0, 1);
    l.effects = Array.isArray(l.effects) ? l.effects.filter(e => e && e.type).map(e => ({ ...e, enabled: e.enabled !== false })) : [];
  }
  if (l.type === 'video' || l.type === 'image') {
    l.src = l.src || '';
    const dflt = l.type === 'video' ? 'cover' : 'contain';
    l.fit = ['cover', 'contain', 'native'].includes(l.fit) ? l.fit : dflt;
    const c = l.crop || {};
    l.crop = { top: num(c.top, 0), bottom: num(c.bottom, 0), left: num(c.left, 0), right: num(c.right, 0) };
  }
  if (l.type === 'video' || l.type === 'audio') {
    l.src = l.src || '';
    l.src_in = num(l.src_in, 0);
    l.volume_db = num(l.volume_db, 0);
    l.muted = !!l.muted;
    l.envelope = Array.isArray(l.envelope) ? l.envelope.filter(p => Array.isArray(p) && p.length >= 2).map(p => [num(p[0], 0), num(p[1], 0)]).sort((a, b) => a[0] - b[0]) : [];
  }
  if (l.type === 'text') {
    l.text = typeof l.text === 'string' ? l.text : 'Text';
    l.font = l.font || 'inter-regular'; l.size = num(l.size, 48); l.color = l.color || '#f1efe9';
    l.letter_spacing = num(l.letter_spacing, 0); l.line_height = num(l.line_height, 1.25);
    l.align = ['left', 'center', 'right'].includes(l.align) ? l.align : 'center';
    const s = l.shadow || {};
    l.shadow = { enabled: s.enabled !== false, blur: num(s.blur, 9), opacity: clamp(num(s.opacity, 0.7), 0, 1) };
  }
  if (l.type === 'color') l.color = l.color || '#000000';
  if (l.keys && typeof l.keys === 'object') { // motion keys (SPEC §1.8): numbers coerced, invalid dropped, sorted by t (stable) like schema._norm_keys
    for (const k of Object.keys(l.keys)) {
      const a = (Array.isArray(l.keys[k]) ? l.keys[k] : []).filter(x => x && Number.isFinite(num(x.t, NaN)) && Number.isFinite(num(x.v, NaN))).map(x => ({ ...x, t: num(x.t, 0), v: num(x.v, 0) }));
      a.sort((p, q) => p.t - q.t);
      if (a.length) l.keys[k] = a; else delete l.keys[k];
    }
    if (!Object.keys(l.keys).length) delete l.keys;
  }
  return l;
}
export function normalizeProject(p) {
  p = p && typeof p === 'object' ? p : {};
  p.version = 2; p.name = p.name || t('project.untitled');
  p.width = num(p.width, 1920) || 1920; p.height = num(p.height, 1080) || 1080; p.fps = num(p.fps, 30) || 30;
  p.background = p.background || '#000000'; p.output = p.output || 'output.mp4';
  const g = p.global || {};
  p.global = { grain: num(g.grain, 0), vignette: num(g.vignette, 0), loudness_lufs: g.loudness_lufs === null ? null : num(g.loudness_lufs, -14), fade_out: num(g.fade_out, 0) };
  p.layers = (Array.isArray(p.layers) ? p.layers : []).map(l => normalizeLayer(l, p));
  const ids = new Set(); // repair duplicate ids
  for (const l of p.layers) { while (ids.has(l.id)) l.id += 'x'; ids.add(l.id); }
  p.notes = (Array.isArray(p.notes) ? p.notes : []).filter(n => n && typeof n === 'object').map(n => {  // mirrors schema.normalize_note
    const done = n.done === true;
    return { ...n, id: n.id || 'n_' + Math.random().toString(36).slice(2, 6), t: num(n.t, 0), text: String(n.text ?? ''), created: n.created || '',
      done, author: n.author === 'agent' ? 'agent' : 'user', resolved_at: done && typeof n.resolved_at === 'string' ? n.resolved_at : '',
      layer_ids: Array.isArray(n.layer_ids) ? n.layer_ids.filter(x => typeof x === 'string') : [], reply: typeof n.reply === 'string' ? n.reply : '' };
  });
  const lastEnd = p.layers.reduce((m, l) => Math.max(m, l.end || 0), 0);
  p.duration = num(p.duration, 0) > 0 ? num(p.duration, 0) : Math.max(lastEnd, 1);
  return p;
}
export function newLayer(type, extra = {}) {
  return normalizeLayer({ id: newId('l'), type, ...extra }, S.project);
}

// ---------- queries ----------
export const layer = id => S.project?.layers.find(l => l.id === id) || null;
export const selectedLayers = () => S.selection.map(layer).filter(Boolean);
export const isActive = (l, time) => time >= l.start && time < l.end;
/** Fade ramp factor f(t) (SPEC §1.1). */
export function fadeFactor(l, time) {
  let f = 1;
  if (l.fade_in > 0) f *= clamp((time - l.start) / l.fade_in, 0, 1);
  if (l.fade_out > 0) f *= clamp((l.end - time) / l.fade_out, 0, 1);
  return f;
}
/** Volume envelope in dB at timeline time (linear in dB, held outside the points). */
export function envelopeDb(l, time) {
  const z = l.envelope;
  if (!z || !z.length) return 0;
  if (time <= z[0][0]) return z[0][1];
  const last = z[z.length - 1]; if (time >= last[0]) return last[1];
  for (let i = 1; i < z.length; i++) {
    if (time <= z[i][0]) { const a = z[i - 1], b = z[i]; const d = b[0] - a[0]; return d <= 0 ? b[1] : a[1] + (b[1] - a[1]) * (time - a[0]) / d; }
  }
  return last[1];
}
/** Slow push-in factor z(t) from the zoom effect (SPEC §2). */
export function zoomFactor(l, time) {
  const e = (l.effects || []).find(x => x.type === 'zoom' && x.enabled !== false);
  if (!e) return 1;
  const from = num(e.from, 1), to = num(e.to, 1.06); const d = l.end - l.start;
  return d > 0 ? from + (to - from) * clamp((time - l.start) / d, 0, 1) : from;
}
export const isFrozen = l => (l.effects || []).some(e => e.type === 'freeze' && e.enabled !== false);

// ---------- selection / time ----------
/** Layers that share `l`'s link id (l included), in z-order; just [l] when it is not linked to anything. */
export function linkedWith(l, P = S.project) {
  if (!l || !l.link || !P) return l ? [l] : [];
  const g = P.layers.filter(x => x.link === l.link);
  return g.length > 1 ? g : [l];
}
/** New unique link id (SPEC §1.1a). */
export function newLinkId(P = S.project) {
  const used = new Set((P?.layers || []).map(l => l.link).filter(Boolean));
  let id; do { id = 'lk_' + Math.random().toString(36).slice(2, 7); } while (used.has(id));
  return id;
}
/** Selecting a clip selects its linked partners too (Premiere); `single` (Alt) = only the clicked one. The clicked layer is last (= the primary one). */
function expandLinked(ids) {
  const out = [];
  for (const id of ids) {
    const l = layer(id);
    for (const x of l ? linkedWith(l) : []) if (x.id !== id && !out.includes(x.id) && !ids.includes(x.id)) out.push(x.id);
    if (l) out.push(id);
  }
  return out;
}
export function select(ids, toggle = false, { single = false } = {}) {
  ids = ids.filter(Boolean);
  if (!single) ids = expandLinked(ids);
  if (toggle) { const next = S.selection.slice(); for (const id of ids) { const i = next.indexOf(id); if (i >= 0) next.splice(i, 1); else next.push(id); } S.selection = next; }
  else S.selection = ids;
  emit('selection');
}
export function setTime(time, silent = false) {
  time = clamp(time, 0, S.project ? S.project.duration : 0);
  if (time === S.t) return;
  S.t = time; if (!silent) emit('time');
}

// ---------- change / undo ----------
export function change(fn, { key = null } = {}) {
  if (!S.project) return false;
  const before = JSON.stringify(S.project);
  fn(S.project);
  const after = JSON.stringify(S.project);
  if (before === after) return false;
  const now = performance.now();
  // consecutive edits with the same key within 1.5 s merge into one undo step
  if (!(key && key === S.lastKey && now - S.lastTime < 1500)) {
    S.undo.push(before); if (S.undo.length > 300) S.undo.shift(); S.redo = [];
  }
  S.lastKey = key; S.lastTime = now;
  S.saveState = 'dirty';
  emit('project'); hooks.dirty();
  return true;
}
function restoreSnapshot(json) {
  S.project = normalizeProject(JSON.parse(json));
  S.selection = S.selection.filter(id => layer(id));
  setTime(S.t, true);
  S.lastKey = null; S.saveState = 'dirty';
  emit('project'); emit('time'); emit('selection'); hooks.dirty();
}
export function undo() { if (!S.undo.length) return; S.redo.push(JSON.stringify(S.project)); restoreSnapshot(S.undo.pop()); }
export function redo() { if (!S.redo.length) return; S.undo.push(JSON.stringify(S.project)); restoreSnapshot(S.redo.pop()); }
/** Load from the server (the file changed underneath us, or first load). Keeps selection and playhead. The project we had
 *  becomes ONE undo step (so ⌘Z reverts the whole external change, ⌘⇧Z redoes it); `label` is 'ai' or 'external'.
 *  While an AI job runs (S.aiJob), several loads of that job merge into the same step. */
export function loadExternal(project, version, label = 'external', merged = null) {
  const before = S.project ? JSON.stringify(S.project) : null;
  S.version = version; S.savedBase = clone(normalizeProject(clone(project)));
  S.project = merged ? normalizeProject(merged) : normalizeProject(project);
  if (merged) S.savedBase = normalizeProject(clone(project)); // the server's copy is the base of the next save (the merged edits are still to be saved)
  if (before !== null && before !== JSON.stringify(S.project)) {
    const le = S.lastExternal;
    const merge = label === 'ai' && le && le.label === 'ai' && le.job === S.aiJob && S.undo.length === le.depth;
    if (!merge) { S.undo.push(before); if (S.undo.length > 300) S.undo.shift(); }
    S.lastExternal = { label, job: S.aiJob || null, depth: S.undo.length };
  } else if (before === null) { S.undo = []; }
  S.redo = []; S.lastKey = null; S.saveState = merged && JSON.stringify(S.project) !== JSON.stringify(S.savedBase) ? 'dirty' : 'saved';
  S.selection = S.selection.filter(id => layer(id));
  S.t = clamp(S.t, 0, S.project.duration);
  emit('project'); emit('selection'); emit('time'); emit('save');
  if (S.saveState === 'dirty') hooks.dirty();
}

// ---------- layer operations ----------
/** Group a new layer joins: the group of an existing layer of the same kind, else a default heading. */
function groupFor(p, l) {
  if (l.group) return l.group;
  const kind = type => (type === 'audio' ? 'audio' : type === 'text' ? 'text' : 'picture');
  const sameKind = p.layers.find(x => x.group && kind(x.type) === kind(l.type));
  if (sameKind) return sameKind.group;
  return l.type === 'audio' ? t('group.sound') : l.type === 'text' ? t('group.titles') : t('group.picture');
}
/** Add a layer. `before` (optional): id of the layer it goes above, or null for the very bottom; omitted = default placement by group. */
export function placeLayer(p, l, { before } = {}) {
  {
    if (before !== undefined) {
      let i = before === null ? p.layers.length : p.layers.findIndex(x => x.id === before);
      if (i < 0) i = p.layers.length;
      const above = p.layers[i - 1], below = p.layers[i];
      if (!l.group) l.group = above && below && above.group === below.group ? above.group : groupFor(p, l);
      p.layers.splice(i, 0, l);
    } else {
      l.group = groupFor(p, l);
      if (l.type === 'audio') {
        const last = p.layers.map(x => x.group).lastIndexOf(l.group);
        if (last >= 0) p.layers.splice(last + 1, 0, l); else p.layers.push(l);
      } else {
        const first = p.layers.findIndex(x => x.group === l.group);
        p.layers.splice(first >= 0 ? first : 0, 0, l);
      }
    }
    if (l.end > p.duration) p.duration = Math.ceil(l.end * 100) / 100;
  }
}
export function addLayer(l, { before } = {}) {
  change(p => placeLayer(p, l, { before }));
  select([l.id]);
}
/** Link ids that only one layer still carries mean nothing: drop them. */
export function pruneLinks(p) {
  const n = new Map(); for (const l of p.layers) if (l.link) n.set(l.link, (n.get(l.link) || 0) + 1);
  for (const l of p.layers) if (l.link && n.get(l.link) < 2) delete l.link;
}
export function removeLayers(ids) {
  const set = new Set(ids); if (!set.size) return;
  change(p => { p.layers = p.layers.filter(l => !(set.has(l.id) && !l.locked)); pruneLinks(p); });
  S.selection = S.selection.filter(id => layer(id)); emit('selection');
}
export function duplicateLayers(ids) {
  const fresh = [], links = new Map();
  change(p => {
    for (const id of ids) {
      const i = p.layers.findIndex(l => l.id === id); if (i < 0) continue;
      const c = clone(p.layers[i]); c.id = newId('l'); c.name = p.layers[i].name + ' ' + t('layer.copySuffix'); c.locked = false;
      if (c.link) { if (!links.has(c.link)) links.set(c.link, newLinkId(p)); c.link = links.get(c.link); }   // copies of a pair form a new pair
      p.layers.splice(i, 0, c); fresh.push(c.id);
    }
    pruneLinks(p);
  });
  if (fresh.length) select(fresh);
}
/** Split every listed layer that straddles `time` (snapped to a frame) — ONE undo step. Returns how many were split. */
export function splitLayers(ids, time) {
  const fresh = [], links = new Map();
  time = snapFrame(time);
  change(p => {
    for (const id of ids) {
      const i = p.layers.findIndex(l => l.id === id); if (i < 0) continue;
      const a = p.layers[i];
      if (a.locked || time <= a.start + 0.02 || time >= a.end - 0.02) continue;
      const b = clone(a); b.id = newId('l');
      if (a.type === 'video' || a.type === 'audio') b.src_in = a.src_in + (time - a.start);
      b.start = time; a.end = time;
      a.fade_out = 0; b.fade_in = 0; // fade-in stays with the first half, fade-out with the second
      if (b.link) { if (!links.has(b.link)) links.set(b.link, newLinkId(p)); b.link = links.get(b.link); }   // second halves of a pair form a new pair
      p.layers.splice(i, 0, b); fresh.push(b.id);
    }
    pruneLinks(p);
  });
  if (fresh.length) select(fresh);
  return fresh.length;
}
export const splitLayer = (id, time) => splitLayers([id], time);
/** Can Split do anything now? (some selected, unlocked layer straddles the playhead) */
export const canSplit = (ids = S.selection, time = S.t) => ids.some(id => { const l = layer(id); return l && !l.locked && time > l.start + 0.02 && time < l.end - 0.02; });

/** Move a layer by dt seconds (the volume envelope is timeline-absolute and moves with it; keys are layer-local and stay). */
function shiftLayer(l, dt) {
  l.start = Math.round((l.start + dt) * 1e4) / 1e4; l.end = Math.round((l.end + dt) * 1e4) / 1e4;
  if (Array.isArray(l.envelope)) l.envelope = l.envelope.map(pt => [Math.round((pt[0] + dt) * 1e4) / 1e4, pt[1]]);
}
/** Ripple delete: remove the layers and close the gap — layers of the same group that start after a removed one move left. ONE undo step.
 *  Returns {removed, moved} (counts). Locked layers are neither removed nor moved. */
export function rippleDelete(ids) {
  const set = new Set(ids); const res = { removed: 0, moved: 0 };
  change(p => {
    const gone = p.layers.filter(l => set.has(l.id) && !l.locked);
    if (!gone.length) return;
    const goneIds = new Set(gone.map(l => l.id));
    const byGroup = new Map();
    for (const l of gone) { const g = l.group || ''; if (!byGroup.has(g)) byGroup.set(g, []); byGroup.get(g).push([l.start, l.end]); }
    const merged = new Map(); // union of the removed spans per group
    for (const [g, spans] of byGroup) {
      spans.sort((a, b) => a[0] - b[0]); const out = [];
      for (const s of spans) { const last = out[out.length - 1]; if (last && s[0] <= last[1] + 1e-6) last[1] = Math.max(last[1], s[1]); else out.push([s[0], s[1]]); }
      merged.set(g, out);
    }
    p.layers = p.layers.filter(l => !goneIds.has(l.id)); res.removed = gone.length; pruneLinks(p);
    for (const l of p.layers) {
      if (l.locked || !merged.has(l.group || '')) continue;
      let dt = 0; for (const [a, b] of merged.get(l.group || '')) if (l.start >= b - 1e-6) dt += b - a;
      if (dt > 0) { shiftLayer(l, -dt); res.moved++; }
    }
  });
  S.selection = S.selection.filter(id => layer(id)); emit('selection');
  return res;
}

// ---------- clipboard (copy / cut / paste layers) ----------
export const CLIP_TAG = 'kurgu-layers';
/** JSON payload for the system clipboard: tagged for Kurgu so it also pastes into another project. */
export function copyPayload(ids) {
  const set = new Set(ids);
  const ls = S.project.layers.filter(l => set.has(l.id));
  if (!ls.length) return null;
  return { kurgu: CLIP_TAG, v: 1, fps: S.project.fps, width: S.project.width, height: S.project.height, layers: clone(ls) };
}
export function parsePayload(text) {
  try { const d = JSON.parse(text); return d && d.kurgu === CLIP_TAG && Array.isArray(d.layers) && d.layers.length ? d : null; } catch (e) { return null; }
}
/** Paste the payload's layers with new ids so the earliest starts at `time` (frame-snapped) and the relative timing is kept;
 *  they land above the topmost selected layer (or at the top). ONE undo step. Returns the new ids. */
export function pasteLayers(payload, time) {
  if (!payload || !payload.layers) return [];
  const src = payload.layers.filter(l => l && typeof l === 'object' && TYPES.includes(l.type));
  if (!src.length) return [];
  const t0 = Math.min(...src.map(l => num(l.start, 0)));
  const at = snapFrame(Math.max(0, time)); const dt = at - t0;
  const fresh = [], links = new Map();
  change(p => {
    const idx = S.selection.map(id => p.layers.findIndex(l => l.id === id)).filter(i => i >= 0);
    let pos = idx.length ? Math.min(...idx) : 0;
    for (const raw of src) {
      const l = normalizeLayer(clone(raw), p);
      l.id = newId('l'); l.locked = false;
      if (l.link) { if (!links.has(l.link)) links.set(l.link, newLinkId(p)); l.link = links.get(l.link); }
      shiftLayer(l, dt);
      p.layers.splice(pos++, 0, l); fresh.push(l.id);
      if (l.end > p.duration) p.duration = Math.ceil(l.end * 100) / 100;
    }
    pruneLinks(p);
  });
  if (fresh.length) select(fresh);
  return fresh;
}

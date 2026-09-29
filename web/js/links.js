// links.js — linked clips (SPEC §1.1a): a video and its sound as two layers that share `layer.link`. Pure project edits (no DOM):
// separate / merge / link / unlink / re-sync, and the sync offset shown as a badge. Every command is ONE undo step (one change()).
import { S, change, select, layer, linkedWith, newLinkId, newLayer, placeLayer, pruneLinks } from './state.js';
import { t } from './i18n.js';

const EPS = 0.5e-3;
const r4 = v => Math.round(v * 1e4) / 1e4;

/** Import setting: split a video that has sound into picture + sound layers (default on). */
const SEP_KEY = 'kurgu.importSeparateAudio';
export function separateOnImport() { try { return localStorage.getItem(SEP_KEY) !== '0'; } catch (e) { return true; } }
export function setSeparateOnImport(on) { try { localStorage.setItem(SEP_KEY, on ? '1' : '0'); } catch (e) { /* ignore */ } }

export const isLinked = l => !!l && linkedWith(l).length > 1;
/** True when the selection is exactly the members of one link (the inspector then shows the primary clip). */
export function isPairSelection(ids = S.selection) {
  const ls = ids.map(layer).filter(Boolean);
  return ls.length > 1 && ls.length === ids.length && !!ls[0].link && ls.every(l => l.link === ls[0].link) && linkedWith(ls[0]).length === ls.length;
}

/** Sync offset of `l` against its partner in seconds (Premiere: how far the sound is shifted against its picture); 0 = in sync.
 *  Same source: the difference of source alignment (start − src_in); different sources: the start difference. */
export function offsetOf(l, P = S.project) {
  const g = linkedWith(l, P); if (g.length < 2) return 0;
  const master = masterOf(g);
  if (master.id === l.id) return 0;
  const same = master.src && master.src === l.src;
  const a = same ? l.start - (l.src_in || 0) : l.start, b = same ? master.start - (master.src_in || 0) : master.start;
  const d = a - b;
  return Math.abs(d) < EPS ? 0 : r4(d);
}
/** The clip the others are aligned to: the video (picture leads), else the first layer of the link. */
export const masterOf = g => g.find(x => x.type === 'video') || g[0];
/** Is any member of `l`'s link out of sync by more than half a frame? */
export const outOfSync = (l, P = S.project) => linkedWith(l, P).some(x => Math.abs(offsetOf(x, P)) > 0.5 / (P.fps || 30));

// ---------------------------------------------------------------- commands
/** What can be done with this selection? */
export function available(ids = S.selection) {
  const ls = ids.map(layer).filter(Boolean);
  const out = { unlink: false, link: false, separate: false, merge: false, resync: false };
  if (!ls.length) return out;
  out.unlink = ls.some(isLinked);
  const sameLink = ls.length > 1 && ls[0].link && ls.every(l => l.link === ls[0].link);
  out.link = ls.length > 1 && !sameLink && ls.every(l => !l.locked);
  out.resync = ls.some(l => isLinked(l) && outOfSync(l));
  if (ls.length === 1) {
    const l = ls[0], info = S.media.get(l.src);
    out.separate = l.type === 'video' && !l.locked && !isLinked(l) && !l.muted && !!l.src && !(info && info.has_audio === false);
  }
  out.merge = ls.some(l => mergeTarget(l) !== null);
  return out;
}

/** {video, audio} when `l` is one of a video + audio pair that is a pure copy (the audio can go back into the video). */
function mergeTarget(l) {
  if (!l || !l.link) return null;
  const g = linkedWith(l); if (g.length !== 2) return null;
  const v = g.find(x => x.type === 'video'), a = g.find(x => x.type === 'audio');
  if (!v || !a || v.locked || a.locked || !v.muted) return null;
  const same = v.src === a.src && Math.abs((v.src_in || 0) - (a.src_in || 0)) < EPS && Math.abs(v.start - a.start) < EPS && Math.abs(v.end - a.end) < EPS
    && Math.abs((v.fade_in || 0) - (a.fade_in || 0)) < EPS && Math.abs((v.fade_out || 0) - (a.fade_out || 0)) < EPS;
  return same ? { video: v.id, audio: a.id } : null;
}

/** Build the sound layer of a video layer (its own volume, envelope, fades, volume keyframes). */
export function soundLayerOf(v, link, P = S.project) {
  const a = newLayer('audio', {
    name: v.name + ' ' + t('link.audioSuffix'), src: v.src, src_in: v.src_in || 0, start: v.start, end: v.end,
    volume_db: v.volume_db || 0, envelope: JSON.parse(JSON.stringify(v.envelope || [])), fade_in: v.fade_in || 0, fade_out: v.fade_out || 0, link,
  });
  if (v.keys && v.keys.volume_db) a.keys = { volume_db: JSON.parse(JSON.stringify(v.keys.volume_db)) };
  return a;
}
/** Mute the picture half of a new pair: its sound lives on the audio layer now. */
function silence(v) {
  v.muted = true; v.volume_db = 0; v.envelope = [];
  if (v.keys) { delete v.keys.volume_db; if (!Object.keys(v.keys).length) delete v.keys; }
}

/** Legacy single video with sound -> picture (muted) + sound layer, linked. Returns the new audio layer id or null. */
export function separateAudio(id) {
  const v0 = layer(id); if (!v0 || !available([id]).separate) return null;
  let aid = null;
  change(P => {
    const v = P.layers.find(x => x.id === id);
    const link = newLinkId(P);
    const a = soundLayerOf(v, link, P);
    silence(v); v.link = link;
    placeLayer(P, a, {});
    aid = a.id;
  });
  if (aid) select([aid, id]);
  return aid;
}

/** Inverse of separateAudio, when the sound layer is a pure copy. Returns the video id or null. */
export function mergeAudio(ids = S.selection) {
  const tg = ids.map(layer).map(mergeTarget).find(Boolean); if (!tg) return null;
  change(P => {
    const v = P.layers.find(x => x.id === tg.video), a = P.layers.find(x => x.id === tg.audio);
    v.muted = !!a.muted; v.volume_db = a.volume_db || 0; v.envelope = JSON.parse(JSON.stringify(a.envelope || []));
    if (a.keys && a.keys.volume_db) { v.keys = { ...(v.keys || {}), volume_db: JSON.parse(JSON.stringify(a.keys.volume_db)) }; }
    delete v.link;
    P.layers = P.layers.filter(x => x.id !== a.id);
  });
  select([tg.video]);
  return tg.video;
}

/** Link the given layers (one shared link id). */
export function linkLayers(ids = S.selection) {
  const set = new Set(ids); const n = ids.map(layer).filter(l => l && !l.locked).length;
  if (n < 2) return 0;
  change(P => {
    const link = newLinkId(P);
    for (const l of P.layers) if (set.has(l.id) && !l.locked) l.link = link;
    pruneLinks(P);
  });
  select(ids);
  return n;
}

/** Break the link of the selected layers (the whole pair when both are selected; only the selected ones with Alt-selection). */
export function unlinkLayers(ids = S.selection) {
  const set = new Set(ids); let n = 0;
  change(P => {
    for (const l of P.layers) if (set.has(l.id) && l.link) { delete l.link; n++; }
    pruneLinks(P);
  });
  return n;
}

/** Shift the members of a link so their source alignment matches the master (the video). */
export function resync(ids = S.selection) {
  const links = new Set(ids.map(layer).filter(l => l && l.link).map(l => l.link)); let moved = 0;
  if (!links.size) return 0;
  change(P => {
    for (const lk of links) {
      const g = P.layers.filter(x => x.link === lk); if (g.length < 2) continue;
      const m = masterOf(g);
      for (const x of g) {
        if (x.id === m.id || x.locked) continue;
        const same = m.src && m.src === x.src;
        const d = same ? (x.start - (x.src_in || 0)) - (m.start - (m.src_in || 0)) : x.start - m.start;
        if (Math.abs(d) < EPS) continue;
        x.start = r4(x.start - d); x.end = r4(x.end - d);
        if (Array.isArray(x.envelope)) x.envelope = x.envelope.map(p => [r4(p[0] - d), p[1]]);
        if (x.start < 0) { x.src_in = r4((x.src_in || 0) - x.start); x.start = 0; }
        moved++;
      }
    }
  });
  return moved;
}

/** Text of the offset badge: "+0.4 s" / "−0.4 s". */
export function offsetText(d) { return (d > 0 ? '+' : '−') + String(Math.round(Math.abs(d) * 100) / 100) + ' ' + t('unit.s'); }

/** Link partners other than `id` (used by the drag code). */
export const partnersOf = id => { const l = layer(id); return l ? linkedWith(l).filter(x => x.id !== id) : []; };

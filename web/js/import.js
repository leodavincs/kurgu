// import.js — drag files anywhere onto the window, or use the Import button (multiple files, upload progress).
// Media becomes a layer at the playhead; fonts go into the registry and (if a text layer is selected) onto that layer.
import { S, change, layer, newLayer, addLayer, placeLayer, newLinkId, select, basename, emit, snapFrame, snapFrameUp, typeName } from './state.js';
import { separateOnImport, soundLayerOf } from './links.js';
import { importFile, mediaInfo, errorText } from './api.js';
import { refreshFonts } from './fonts.js';
import { $ } from './dom.js';
import { t, fmt } from './i18n.js';

const EXT = {
  video: ['mp4', 'mov', 'm4v', 'webm', 'mkv', 'avi'],
  image: ['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp'],
  audio: ['mp3', 'wav', 'm4a', 'aac', 'ogg', 'flac', 'aif', 'aiff'],
  font: ['ttf', 'otf', 'ttc'],
};
const extOf = name => (String(name).toLowerCase().match(/\.([a-z0-9]+)$/) || [])[1] || '';
const classify = name => { const e = extOf(name); return Object.keys(EXT).find(k => EXT[k].includes(e)) || null; };

let toast = () => {};
let busy = false;

export function startImport(toastFn) {
  toast = toastFn;
  const overlay = $('drop-overlay'), input = $('file-input');
  let depth = 0;
  const hasFiles = e => e.dataTransfer && [...e.dataTransfer.types].includes('Files');
  window.addEventListener('dragenter', e => { if (!hasFiles(e)) return; e.preventDefault(); depth++; overlay.hidden = false; });
  window.addEventListener('dragover', e => { if (!hasFiles(e)) return; e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; });
  window.addEventListener('dragleave', e => { if (!hasFiles(e)) return; depth = Math.max(0, depth - 1); if (!depth) overlay.hidden = true; });
  window.addEventListener('drop', e => {
    if (!hasFiles(e)) return;
    e.preventDefault(); depth = 0; overlay.hidden = true;
    importFiles([...e.dataTransfer.files]);
  });
  for (const b of document.querySelectorAll('[data-import]')) b.addEventListener('click', () => input.click());
  input.addEventListener('change', () => { importFiles([...input.files]); input.value = ''; });
}
export const openFilePicker = () => $('file-input').click();

function progress(text, frac) {
  const box = $('upload');
  if (text === null) { box.hidden = true; return; }
  box.hidden = false; box.querySelector('i').style.width = Math.round(frac * 100) + '%'; box.querySelector('span').textContent = text;
}

export async function importFiles(files) {
  if (!files.length) return;
  if (busy) { toast(t('import.busy'), 'warn', 2500); return; }
  busy = true;
  const target = S.selection.length === 1 ? layer(S.selection[0]) : null; // text layer that should receive an imported font
  const list = [];
  for (const f of files) {
    const kind = classify(f.name);
    if (kind) list.push({ f, kind });
    else if (/\.woff2?$/i.test(f.name)) toast(t('import.woff', { name: f.name }), 'warn', 6000);
    else toast(t('import.unsupported', { name: f.name }), 'warn', 5000);
  }
  const total = list.reduce((s, x) => s + x.f.size, 0) || 1;
  let done = 0, added = 0; const got = []; // what was added, for the summary toast
  for (let i = 0; i < list.length; i++) {
    const { f, kind } = list[i];
    const label = () => t('import.progress', { i: i + 1, n: list.length, name: f.name });
    progress(label(), done / total);
    const r = await importFile(f, frac => progress(label(), (done + frac * f.size) / total));
    done += f.size;
    if (!r.ok) { toast(t('import.failed', { name: f.name, reason: errorText(r.status, r.data) }), 'error', 6000); continue; }
    try { const k = await afterImport(r.data, kind, f.name, target, list.length > 1); added++; got.push(k); } catch (e) { console.error(e); }
  }
  progress(null);
  busy = false;
  if (added && list.length > 1) {
    const by = {}; for (const k of got) by[k] = (by[k] || 0) + 1;
    const listText = Object.keys(by).map(k => `${by[k]} × ${k === 'font' ? t('import.kind.font') : typeName(k).toLocaleLowerCase(t('lang.code'))}`).join(', ');
    toast(t('import.summary', { n: added, list: listText }), 'ok', 4000);
  }
}

/** Returns the kind that was added ('video' | 'image' | 'audio' | 'font'). `quiet`: several files at once, the summary toast comes afterwards. */
async function afterImport(data, kindFromExt, fileName, target, quiet = false) {
  const kind = (data && data.kind) || kindFromExt;
  if (kind === 'font') {
    await refreshFonts();
    const ids = (data && data.font_ids) || [];
    const first = ids.find(id => /regular/.test(id)) || ids[0];
    const sel = target && layer(target.id);
    if (first && sel && sel.type === 'text' && !sel.locked) {
      change(P => { const l = P.layers.find(x => x.id === sel.id); if (l) l.font = first; });
      if (!quiet) toast(t('import.fontApplied', { name: S.fontMap.get(first)?.family || first, layer: sel.name }), 'ok', 3500);
    } else if (!quiet) toast(t('import.fontAdded', { n: ids.length || 1 }), 'ok', 3000);
    return 'font';
  }
  S.lastImported = data.path; emit('files');
  await addMedia(kind, data.path, { name: fileName, quiet });
  return kind;
}

/** Put a media file on the timeline as a new layer (shared by import, the file picker and the Media panel).
 *  opts: time (default playhead), before (layer id to sit above / null = bottom, see addLayer), name. */
export async function addMedia(kind, path, opts = {}) {
  let info = S.media.get(path);
  if (!info) { info = await mediaInfo(path); if (info) S.media.set(path, info); }
  const fps = S.project.fps || 30;
  const dur = kind === 'image' ? 3 : (info && info.duration > 0 ? snapFrameUp(info.duration, fps) : 5);  // whole frames: the last frame of a clip is never cut
  const start = Math.max(0, snapFrame(opts.time == null ? S.t : opts.time, fps));                        // frame grid, no 3.180834621329212
  const name = basename(path).replace(/\.[^.]+$/, '') || opts.name || path;
  const l = newLayer(kind, { name, src: path, start, end: Math.round((start + dur) * 1e4) / 1e4 });
  if (opts.group) l.group = opts.group;
  const split = kind === 'video' && info && info.has_audio === true && (opts.separate === undefined ? separateOnImport() : opts.separate);
  if (split) {                                    // Premiere: picture (muted) in the picture group + its sound in the sound group, linked
    const link = newLinkId(S.project);
    l.link = link; l.muted = true;
    const a = soundLayerOf(l, link);
    change(p => { placeLayer(p, l, opts.before === undefined ? {} : { before: opts.before }); placeLayer(p, a, {}); });
    select([a.id, l.id]);
  } else addLayer(l, opts.before === undefined ? {} : { before: opts.before });
  if (!opts.quiet) toast(t('import.added', { kind: typeName(kind).toLocaleLowerCase(t('lang.code')), name, time: fmt(start, 2) + ' ' + t('unit.s') }), 'ok', 3000);
  return l;
}

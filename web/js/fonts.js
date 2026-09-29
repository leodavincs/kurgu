// fonts.js — font registry (/api/fonts) and on-demand FontFace loading (SPEC §6).
// The canvas uses the family "kf-<id>" with "kf-inter-regular" as fallback, so preview and render use the same font files.
import { S, emit } from './state.js';
import { fetchFonts } from './api.js';

export const FALLBACK_ID = 'inter-regular';
const status = new Map();   // id -> 'loading' | 'ok' | 'error'
let epoch = 0;              // bumped whenever a face finishes loading (cache key for text layout)
const loadCallbacks = [];

export const fontsEpoch = () => epoch;
export function onFontLoaded(fn) { loadCallbacks.push(fn); }
export const familyName = id => 'kf-' + id;
/** CSS font-family list for canvas / DOM. Unknown ids fall back to inter-regular. */
export function fontStack(id) {
  const first = S.fontMap.has(id) ? `"kf-${id}", ` : '';
  return `${first}"kf-${FALLBACK_ID}", sans-serif`;
}
export const fontReady = id => status.get(id) === 'ok';

/** Start loading a face if it is in the registry and not loaded yet. Redraws happen through onFontLoaded. */
export function ensureFont(id) {
  if (!id || status.has(id)) return;
  const face = S.fontMap.get(id);
  if (!face) return;
  status.set(id, 'loading');
  const ff = new FontFace(familyName(id), `url(${face.url})`);
  ff.load().then(f => {
    document.fonts.add(f); status.set(id, 'ok'); epoch++;
    for (const cb of loadCallbacks) { try { cb(id); } catch (e) { console.error(e); } }
  }).catch(() => { status.set(id, 'error'); });
}

/** (Re)read the registry from the server; keeps already loaded faces. */
export async function refreshFonts() {
  const list = await fetchFonts();
  S.fonts = list; S.fontMap = new Map(list.map(f => [f.id, f]));
  ensureFont(FALLBACK_ID);
  emit('fonts');
  return list;
}

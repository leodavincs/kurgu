// ab.js — the "önce / sonra" (before / after) button of a closed note: shown when a media file under the note was replaced after the note was sent.
// Click = the playhead goes to the note and the A/B toggle (key B, preview.js) shows the version that existed when the note was sent.
import { S, setTime } from './state.js';
import { changedSince, setAB, pause } from './preview.js';
import { t } from './i18n.js';
import { el } from './dom.js';

const sentMs = n => { const ms = Date.parse(n.sent || ''); return Number.isFinite(ms) ? ms : null; };    // local time, no zone: the way notes.js stamps it
const layersOf = n => S.project.layers.filter(l => !l.hidden && l.start <= n.t && n.t < l.end && (l.type === 'video' || l.type === 'image') && l.src);
/** Did the picture under this note change since it was sent? (a done note whose clips were re-rendered afterwards) */
export function noteChanged(n) {
  const ms = n.done ? sentMs(n) : null;
  return ms != null && changedSince([...new Set(layersOf(n).map(l => l.src))], ms);
}
/** The button, or null. `cls` = the host list's own button class. */
export function abNoteButton(n, cls = '') {
  if (!noteChanged(n)) return null;
  const ms = sentMs(n);
  const b = el('button', 'ab-note ' + cls, t('ab.note')); b.type = 'button'; b.title = t('ab.noteTip');
  b.addEventListener('click', e => { e.stopPropagation(); if (S.playing) pause(); setTime(n.t); setAB(true, ms); });
  return b;
}

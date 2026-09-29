// summary.js — human-readable change summary lines (written to changes.md by the server), in the current UI language.
// e.g.  "GO" (text): x 960 → 1040   /   "Music 3": volume −4 → −1 dB   /   Layer added: "Title"
import { t, fmt, fmtShort } from './i18n.js';
import { aspectLabel } from './state.js';

const lc = s => s.toLocaleLowerCase(t('lang.code'));
const FIELDS = ['name', 'group', 'start', 'end', 'src_in', 'x', 'y', 'scale', 'scale_x', 'scale_y', 'rotation', 'anchor_x', 'anchor_y', 'opacity', 'src', 'fit', 'volume_db', 'text', 'font', 'size', 'color', 'letter_spacing', 'line_height', 'align', 'fade_in', 'fade_out'];
const GEOMETRY = new Set(['x', 'y', 'scale', 'size']);
const SECONDS = new Set(['start', 'end', 'src_in', 'fade_in', 'fade_out']);
const unitOf = f => (SECONDS.has(f) ? ' ' + t('unit.s') : f === 'volume_db' ? ' dB' : f === 'rotation' ? '°' : '');
const DEF = { scale_x: 1, scale_y: 1, rotation: 0, anchor_x: 0.5, anchor_y: 0.5 };   // motion fields are absent while static
const pct = v => Math.round(v * 100) + ' %';

const strVal = v => `"${(v.length > 40 ? v.slice(0, 40) + '…' : v).replace(/\n/g, ' ⏎ ')}"`;
function val(v, field) {
  if (typeof v === 'string') return field === 'fit' || field === 'align' ? t('sum.enum.' + v) : strVal(v);
  if (typeof v === 'boolean') return v ? t('sum.on') : t('sum.off');
  if (typeof v !== 'number') return String(v);
  return SECONDS.has(field) ? fmt(v, 2) : fmtShort(v, 3);
}
function label(l) {
  const suffix = l.type === 'text' || l.type === 'color' || l.type === 'image' ? ` (${lc(t('type.' + l.type))})` : '';
  return `"${l.name}"${suffix}`;
}
const effectName = type => t('effect.' + type + '.name');
const point = p => `${fmt(p[0], 2)} ${t('unit.s')} ${fmtShort(p[1], 1)} dB`;
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
function keyLabel(prop, l) {
  const m = /^fx\.([^.]+)\.(.+)$/.exec(prop);
  if (m) { const e = (l.effects || []).find(x => x.id === m[1]); return e ? `${effectName(e.type)} ${lc(t('effect.param.' + m[2]))}` : prop; }
  return lc(t('field.' + prop));
}

export function makeSummary(oldP, newP) {
  const out = [];
  if (!oldP || !newP) return out;
  const dims = oldP.width !== newP.width || oldP.height !== newP.height; // frame size changed: per-layer geometry lines are implied by the sequence line
  const oldMap = new Map(oldP.layers.map(l => [l.id, l]));
  const newMap = new Map(newP.layers.map(l => [l.id, l]));
  for (const l of newP.layers) if (!oldMap.has(l.id)) out.push(t('sum.layerAdded', { name: l.name }));
  for (const l of oldP.layers) if (!newMap.has(l.id)) out.push(t('sum.layerRemoved', { name: l.name }));
  for (const l of newP.layers) {
    const o = oldMap.get(l.id); if (!o) continue;
    const lines = [];
    for (const f of FIELDS) {
      if (dims && GEOMETRY.has(f)) continue;
      const ov = o[f] ?? DEF[f], nv = l[f] ?? DEF[f];
      if (same(ov, nv) || (ov === undefined && nv === undefined)) continue;
      if (f === 'scale_x' || f === 'scale_y') { lines.push(t('sum.change', { field: t(f === 'scale_x' ? 'sum.width' : 'sum.height'), from: pct(ov), to: pct(nv), unit: '' })); continue; }
      lines.push(t('sum.change', { field: lc(t('field.' + f)), from: val(ov, f), to: val(nv, f), unit: unitOf(f) }));
    }
    for (const prop of new Set([...Object.keys(o.keys || {}), ...Object.keys(l.keys || {})])) {   // keyframes: added / removed / retimed / changed
      const a = (o.keys || {})[prop] || [], b = (l.keys || {})[prop] || [];
      if (same(a, b)) continue;
      const name = keyLabel(prop, l);
      if (a.length !== b.length) lines.push(t('sum.keys', { prop: name, from: a.length, to: b.length }));
      else if (a.some((k, i) => k.t !== b[i].t)) lines.push(t('sum.keyMoved', { prop: name }));
      else lines.push(t('sum.keyChanged', { prop: name }));
    }
    for (const [f, on, off] of [['hidden', 'sum.hidden', 'sum.shown'], ['locked', 'sum.locked', 'sum.unlocked'], ['muted', 'sum.muted', 'sum.unmuted']]) {
      if (!!o[f] !== !!l[f]) lines.push(t(l[f] ? on : off));
    }
    if ((o.link || '') !== (l.link || '')) {   // linked clips (SPEC 1.1a)
      const mates = l.link ? newP.layers.filter(x => x.link === l.link && x.id !== l.id).map(x => `"${x.name}"`) : [];
      lines.push(mates.length ? t('sum.linked', { name: mates.join(', ') }) : t('sum.unlinked'));
    }
    if (l.shadow && !same(o.shadow, l.shadow)) {
      const a = o.shadow || {}, b = l.shadow;
      if (a.enabled !== b.enabled) lines.push(t(b.enabled ? 'sum.shadowOn' : 'sum.shadowOff'));
      if (a.blur !== b.blur && !dims) lines.push(t('sum.shadowBlur', { from: fmtShort(a.blur, 3), to: fmtShort(b.blur, 3) }));
      if (a.opacity !== b.opacity) lines.push(t('sum.shadowOpacity', { from: fmtShort(a.opacity, 3), to: fmtShort(b.opacity, 3) }));
    }
    if (l.crop && !same(o.crop, l.crop)) {
      for (const side of ['top', 'bottom', 'left', 'right']) {
        if ((o.crop || {})[side] !== l.crop[side]) lines.push(t('sum.crop', { side: lc(t('crop.' + side)), from: fmtShort((o.crop || {})[side] ?? 0, 3), to: fmtShort(l.crop[side], 3) }));
      }
    }
    if (l.envelope && !same(o.envelope, l.envelope)) {
      const a = o.envelope || [], b = l.envelope;
      if (a.length !== b.length) lines.push(t('sum.envCount', { from: a.length, to: b.length }));
      else b.forEach((p, i) => { if (p[0] !== a[i][0] || p[1] !== a[i][1]) lines.push(t('sum.envPoint', { i: i + 1, from: point(a[i]), to: point(p) })); });
    }
    if (!same(o.effects, l.effects)) {
      const a = o.effects || [], b = l.effects || [];
      if (a.length !== b.length) {
        const added = b.filter(e => !a.some(x => x.type === e.type)).map(e => effectName(e.type));
        const removed = a.filter(e => !b.some(x => x.type === e.type)).map(e => effectName(e.type));
        lines.push(b.length > a.length ? t('sum.effectAdded', { name: added.join(', ') || '?' }) : t('sum.effectRemoved', { name: removed.join(', ') || '?' }));
      } else {
        b.forEach((e, i) => {
          const x = a[i];
          if (x.type !== e.type) { lines.push(t('sum.effectOrder')); return; }
          if (x.enabled !== e.enabled) lines.push(t(e.enabled ? 'sum.effectOn' : 'sum.effectOff', { name: effectName(e.type) }));
          for (const p of Object.keys(e)) if (p !== 'type' && p !== 'enabled' && x[p] !== e[p] && !(dims && p === 'radius')) lines.push(t('sum.effectParam', { name: effectName(e.type), param: lc(t('effect.param.' + p)), from: fmtShort(x[p], 3), to: fmtShort(e[p], 3) }));
        });
      }
    }
    for (const line of lines) out.push(`${label(l)}: ${line}`);
  }
  // z-order (report the first layer whose position changed)
  const common = newP.layers.filter(l => oldMap.has(l.id)).map(l => l.id);
  const oldCommon = oldP.layers.filter(l => newMap.has(l.id)).map(l => l.id);
  if (common.join() !== oldCommon.join()) {
    for (const id of common) {
      const a = oldCommon.indexOf(id), b = common.indexOf(id);
      if (a !== b) { out.push(t('sum.order', { name: newMap.get(id).name, from: a + 1, to: b + 1 })); break; }
    }
  }
  // timeline group colours
  for (const name of new Set([...Object.keys(oldP.groups || {}), ...Object.keys(newP.groups || {})])) {
    const a = (oldP.groups || {})[name]?.color, b = (newP.groups || {})[name]?.color;
    if (a !== b) out.push(t('sum.groupColor', { name, from: a || t('sum.auto'), to: b || t('sum.auto') }));
  }
  // notes
  const oldNotes = new Map((oldP.notes || []).map(n => [n.id, n])), newNotes = new Map((newP.notes || []).map(n => [n.id, n]));
  for (const n of newP.notes || []) {
    const o = oldNotes.get(n.id);
    if (!o) out.push(t('sum.noteAdded', { t: fmt(n.t, 1), text: n.text }));
    else if (o.done !== n.done) out.push(t(n.done ? 'sum.noteDone' : 'sum.noteReopened', { t: fmt(n.t, 1), text: n.text }) + (n.done && n.reply ? ' — ' + n.reply : ''));
    else if (o.text !== n.text || o.t !== n.t) out.push(t('sum.noteChanged', { t: fmt(n.t, 1), text: n.text }));
    else if (o.reply !== n.reply && n.reply) out.push(t('sum.noteReply', { t: fmt(n.t, 1), text: n.reply }));
  }
  for (const n of oldP.notes || []) if (!newNotes.has(n.id)) out.push(t('sum.noteRemoved', { t: fmt(n.t, 1), text: n.text }));
  // sequence (frame size / frame rate)
  if (dims) {
    const moved = newP.layers.some(l => { const o = oldMap.get(l.id); return o && (o.x !== l.x || o.y !== l.y || o.scale !== l.scale || o.size !== l.size); });
    out.push(t('sum.sequence', { from: `${oldP.width}×${oldP.height}`, to: `${newP.width}×${newP.height}`, ratio: aspectLabel(newP.width, newP.height), how: t(moved ? 'sum.seqKept' : 'sum.seqPixels') }));
  }
  if (oldP.fps !== newP.fps) out.push(t('sum.sequenceFps', { from: fmtShort(oldP.fps, 3), to: fmtShort(newP.fps, 3) }));
  // project + global settings
  if (oldP.name !== newP.name) out.push(t('sum.project', { field: lc(t('proj.name')), from: strVal(oldP.name), to: strVal(newP.name) }));
  if (oldP.duration !== newP.duration) out.push(t('sum.project', { field: lc(t('proj.duration')), from: fmt(oldP.duration, 2) + ' ' + t('unit.s'), to: fmt(newP.duration, 2) + ' ' + t('unit.s') }));
  if (oldP.background !== newP.background) out.push(t('sum.project', { field: lc(t('proj.background')), from: oldP.background, to: newP.background }));
  for (const g of ['grain', 'vignette', 'loudness_lufs', 'fade_out']) {
    const a = oldP.global?.[g], b = newP.global?.[g];
    if (a !== b) out.push(t('sum.global', { field: lc(t('global.' + g)), from: a === null ? t('sum.off') : fmtShort(a ?? 0, 3), to: b === null ? t('sum.off') : fmtShort(b ?? 0, 3) }));
  }
  return out;
}

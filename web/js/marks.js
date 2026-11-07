// marks.js — draw on the frame to say WHERE ("circle it"): ellipse (default), rectangle, arrow; undo-last, clear.
// A draft belongs to the note being written (top-bar box in Review mode, the note box of the watch mode). On Enter the shapes become `marks` of the
// note: project pixels (x, y, w, h | x1, y1, x2, y2) AND the same normalised 0-1 (nx, ny, nw, nh | nx1 ...), plus the layers under the shape (names, front-most first).
// Saved marks are drawn back over the viewer while the playhead rests on their note. Colour: cue yellow (--note), 3 px, no fill.
import { S, clamp } from './state.js';
import { t } from './i18n.js';
import { el, icon } from './dom.js';
import { cv } from './theme.js';
import { viewGeom, layersUnder, pause } from './preview.js';

const st = { tool: 'ellipse', active: false, draft: [], cur: null };
let canvas, ctx, raf = 0;
const rows = new Set();          // every tool row (Review dock, watch mode) is kept in sync
export const drawing = () => st.active;
export const draftMarks = () => st.draft.map(m => ({ ...m }));
const dpr = () => Math.min(2, window.devicePixelRatio || 1);
const r4 = v => Math.round(v * 10000) / 10000;

// ---------------------------------------------------------------- shapes
function finish(sh, W, H) {   // -> stored mark
  const rd = Math.round;
  if (sh.type === 'arrow') {
    const m = { type: 'arrow', x1: rd(sh.x1), y1: rd(sh.y1), x2: rd(sh.x2), y2: rd(sh.y2), nx1: r4(sh.x1 / W), ny1: r4(sh.y1 / H), nx2: r4(sh.x2 / W), ny2: r4(sh.y2 / H) };
    m.layers = layersUnder([{ x: sh.x2, y: sh.y2 }, { x: (sh.x1 + sh.x2) / 2, y: (sh.y1 + sh.y2) / 2 }]).map(l => ({ id: l.id, name: l.name || l.id })); return m;
  }
  const x = Math.min(sh.x1, sh.x2), y = Math.min(sh.y1, sh.y2), w = Math.abs(sh.x2 - sh.x1), h = Math.abs(sh.y2 - sh.y1);
  const m = { type: sh.type, x: rd(x), y: rd(y), w: rd(w), h: rd(h), nx: r4(x / W), ny: r4(y / H), nw: r4(w / W), nh: r4(h / H) };
  const cx = x + w / 2, cy = y + h / 2;
  m.layers = layersUnder([{ x: cx, y: cy }, { x: x + w * .25, y: y + h * .25 }, { x: x + w * .75, y: y + h * .25 }, { x: x + w * .25, y: y + h * .75 }, { x: x + w * .75, y: y + h * .75 }]).map(l => ({ id: l.id, name: l.name || l.id }));
  return m;
}
function strokeMark(c, m, g) {   // m: a stored mark or a live shape in project px
  const X = v => g.vox + v * g.vz, Y = v => g.voy + v * g.vz;
  c.beginPath();
  if (m.type === 'arrow') {
    const x1 = X(m.x1), y1 = Y(m.y1), x2 = X(m.x2), y2 = Y(m.y2), a = Math.atan2(y2 - y1, x2 - x1), L = 16;
    c.moveTo(x1, y1); c.lineTo(x2, y2);
    c.moveTo(x2 + L * Math.cos(a + 2.6), y2 + L * Math.sin(a + 2.6)); c.lineTo(x2, y2); c.lineTo(x2 + L * Math.cos(a - 2.6), y2 + L * Math.sin(a - 2.6));
  } else {
    const x = m.w !== undefined ? m.x : Math.min(m.x1, m.x2), y = m.w !== undefined ? m.y : Math.min(m.y1, m.y2), w = m.w !== undefined ? m.w : Math.abs(m.x2 - m.x1), h = m.h !== undefined ? m.h : Math.abs(m.y2 - m.y1);
    if (m.type === 'ellipse') c.ellipse(X(x + w / 2), Y(y + h / 2), Math.max(1, w * g.vz / 2), Math.max(1, h * g.vz / 2), 0, 0, Math.PI * 2);
    else c.rect(X(x), Y(y), w * g.vz, h * g.vz);
  }
  c.stroke();
}
function shown() {   // saved marks of the note the playhead rests on
  if (!S.project || S.playing) return [];
  return S.project.notes.filter(n => n.marks && n.marks.length && Math.abs(S.t - n.t) < 0.04).flatMap(n => n.marks);
}
function redraw() {
  raf = 0; if (!canvas || !S.project) return;
  const d = dpr(), w = canvas.clientWidth, h = canvas.clientHeight;
  if (canvas.width !== Math.round(w * d) || canvas.height !== Math.round(h * d)) { canvas.width = Math.round(w * d); canvas.height = Math.round(h * d); }
  ctx.setTransform(d, 0, 0, d, 0, 0); ctx.clearRect(0, 0, w, h);
  ctx.strokeStyle = cv('--note') || '#f3c34b'; ctx.lineWidth = 3; ctx.lineJoin = 'round'; ctx.lineCap = 'round';
  const g = viewGeom();
  for (const m of shown()) strokeMark(ctx, m, g);
  for (const m of st.draft) strokeMark(ctx, m, g);
  if (st.cur) strokeMark(ctx, st.cur, g);
}
const schedule = () => { if (!raf) raf = requestAnimationFrame(redraw); };

// ---------------------------------------------------------------- session (tied to a note being written)
const DEFAULT = { ellipse: [140, 140], rect: [180, 110] };
export function startDraw() {
  if (st.active) return;
  st.active = true; canvas.style.pointerEvents = 'auto'; canvas.classList.add('drawing');
  if (S.playing) pause();
  syncRows(); schedule();
}
/** Stop drawing; `keep` = leave the draft (the note is still being written). */
export function endDraw(keep = false) {
  st.active = false; st.cur = null; if (canvas) { canvas.style.pointerEvents = 'none'; canvas.classList.remove('drawing'); }
  if (!keep) st.draft = [];
  syncRows(); schedule();
}
export function takeDraft() { const d = draftMarks(); st.draft = []; schedule(); syncRows(); return d; }
function setTool(k) { st.tool = k; syncRows(); }
function undoLast() { st.draft.pop(); syncRows(); schedule(); }
function clearAll() { st.draft = []; syncRows(); schedule(); }
/** Alt shortcuts while writing: Alt+O ellipse, Alt+B rectangle, Alt+A arrow, Alt+Z undo last, Alt+X clear. Returns true when handled. */
export function markKey(e) {
  if (!st.active || !e.altKey || e.metaKey || e.ctrlKey) return false;
  const map = { KeyO: () => setTool('ellipse'), KeyB: () => setTool('rect'), KeyA: () => setTool('arrow'), KeyZ: undoLast, KeyX: clearAll };
  const f = map[e.code]; if (!f) return false;
  e.preventDefault(); e.stopPropagation(); f(); return true;
}

// ---------------------------------------------------------------- tool rows
function syncRows() {
  for (const r of rows) {
    r.el.hidden = !st.active;
    for (const b of r.el.querySelectorAll('[data-tool]')) { const on = b.dataset.tool === st.tool; b.classList.toggle('on', on); b.setAttribute('aria-pressed', String(on)); }
    r.el.querySelector('[data-act=undo]').disabled = !st.draft.length; r.el.querySelector('[data-act=clear]').disabled = !st.draft.length;
    r.count.textContent = st.draft.length ? String(st.draft.length) : '';
  }
}
/** A row of tool chips (ellipse / rectangle / arrow · undo / clear). Buttons never take focus from the note box. */
export function makeToolRow() {
  const box = el('div', 'dt'); box.hidden = true; box.setAttribute('role', 'toolbar'); box.setAttribute('aria-label', t('mark.tools'));
  const btn = (cls, ic, tip, attr, fn) => { const b = el('button', 'dt-b ' + cls); b.type = 'button'; b.title = tip; b.setAttribute('aria-label', tip); Object.assign(b.dataset, attr); b.append(icon(ic)); b.addEventListener('mousedown', e => e.preventDefault()); b.addEventListener('click', fn); return b; };
  box.append(btn('', 'm-ellipse', t('mark.ellipse') + ' (Alt+O)', { tool: 'ellipse' }, () => setTool('ellipse')),
    btn('', 'm-rect', t('mark.rect') + ' (Alt+B)', { tool: 'rect' }, () => setTool('rect')),
    btn('', 'm-arrow', t('mark.arrow') + ' (Alt+A)', { tool: 'arrow' }, () => setTool('arrow')),
    el('span', 'dt-sep'),
    btn('', 'undo-2', t('mark.undo') + ' (Alt+Z)', { act: 'undo' }, undoLast),
    btn('', 'trash-2', t('mark.clear') + ' (Alt+X)', { act: 'clear' }, clearAll));
  const count = el('span', 'dt-count'); box.append(count);
  rows.add({ el: box, count }); syncRows();
  return box;
}

// ---------------------------------------------------------------- pointer
function bind() {
  canvas.addEventListener('mousedown', e => { if (st.active) e.preventDefault(); });   // keep the focus in the note box
  canvas.addEventListener('pointerdown', e => {
    if (!st.active || e.button !== 0) return;
    e.preventDefault(); canvas.setPointerCapture(e.pointerId);
    const g = viewGeom(), r = canvas.getBoundingClientRect();
    const pt = ev => ({ x: clamp((ev.clientX - r.left - g.vox) / g.vz, 0, g.W), y: clamp((ev.clientY - r.top - g.voy) / g.vz, 0, g.H) });
    const a = pt(e), sx = e.clientX, sy = e.clientY;
    st.cur = { type: st.tool, x1: a.x, y1: a.y, x2: a.x, y2: a.y };
    const move = ev => { const p = pt(ev); st.cur.x2 = p.x; st.cur.y2 = p.y; schedule(); };
    const up = ev => {
      canvas.removeEventListener('pointermove', move); canvas.removeEventListener('pointerup', up); canvas.removeEventListener('pointercancel', up);
      const sh = st.cur; st.cur = null; const moved = Math.hypot(ev.clientX - sx, ev.clientY - sy) >= 6;
      if (!moved) {
        if (sh.type === 'arrow') { schedule(); return; }                                  // a click is no arrow; ellipse / rectangle become a default-size shape on the click
        const [dw, dh] = DEFAULT[sh.type]; sh.x1 = a.x - dw / 2; sh.x2 = a.x + dw / 2; sh.y1 = a.y - dh / 2; sh.y2 = a.y + dh / 2;
      }
      st.draft.push(finish(sh, g.W, g.H)); syncRows(); schedule();
    };
    canvas.addEventListener('pointermove', move); canvas.addEventListener('pointerup', up); canvas.addEventListener('pointercancel', up);
    schedule();
  });
}
export function startMarks(stage) {
  canvas = el('canvas'); canvas.id = 'mark-canvas'; ctx = canvas.getContext('2d'); stage.append(canvas); bind();
  new ResizeObserver(schedule).observe(stage);
  return { project: schedule, time: schedule, view: schedule, playing: schedule, theme: schedule };
}

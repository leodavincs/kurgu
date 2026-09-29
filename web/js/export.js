// export.js — Premiere-style Export dialog (⌘E), render progress with elapsed / ETA / cancel (Esc), past exports.
// Presets come from GET /api/export-presets (exports.py is the single source); the server validates every option.
// Last used settings live in localStorage ("kurgu.export"). Styles: end of app.css, block "export dialog".
import { isMod } from './platform.js';
import { S, onChange, clamp } from './state.js';
import * as API from './api.js';
import { t, onLang } from './i18n.js';
import { $, el, icon } from './dom.js';

const KEY = 'kurgu.export';
const PRESET_IDS = ['youtube', 'master4k', 'social', 'fast', 'draft', 'prores', 'web', 'gif', 'audio'];
const CODECS = { h264: ['mp4', 'mov', 'mkv'], h265: ['mp4', 'mov', 'mkv'], prores: ['mov'], vp9: ['webm', 'mkv'], gif: ['gif'], wav: ['wav'], mp3: ['mp3'] };
const CODEC_LABEL = { h264: 'H.264', h265: 'H.265 / HEVC', prores: 'ProRes 422 HQ', vp9: 'VP9', gif: 'GIF', wav: 'WAV (PCM)', mp3: 'MP3' };
const AUDIO_ONLY = c => c === 'wav' || c === 'mp3';
const RES = { seq: null, half: null, fit1920: 1920, fit3840: 3840, fit640: 640, custom: null };
const DEFAULT_CRF = { h264: 18, h265: 22, vp9: 32 };

let presets = {};          // from the server
let cfg = null;            // current dialog state
let dlg = null, refs = {}; // dialog DOM
let plan = null, planSeq = 0, planTimer = null;
let last = null, timer = null, lastState = '';
let toast = () => {};

// ---------------------------------------------------------------- state
function fromPreset(id, keep = {}) {
  const p = presets[id] || {};
  const codec = p.codec || 'h264';
  const res = p.width ? 'custom' : p.fit_long === 1920 ? 'fit1920' : p.fit_long === 3840 ? 'fit3840' : p.fit_long === 640 ? 'fit640' : p.scale === 0.5 ? 'half' : 'seq';
  return {
    preset: id, codec, format: p.format || CODECS[codec][0], qmode: p.bitrate ? 'bitrate' : 'crf',
    crf: p.crf ?? DEFAULT_CRF[codec] ?? 18, bitrate: p.bitrate ? Math.round(parseFloat(p.bitrate)) : 8000,
    res, width: p.width || 1280, fps: p.fps || 0, audio: p.audio !== false, abr: p.audio_bitrate || 192, speed: p.speed || '',
    range: 'all', from: 0, to: 0, name: keep.name || '', folder: keep.folder || 'exports', exists: keep.exists || 'rename', open: !!keep.open,
  };
}
function loadCfg() {
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem(KEY)); } catch (e) { /* ignore */ }
  const base = fromPreset('youtube');
  if (!saved || typeof saved !== 'object') return base;
  const c = Object.assign(base, saved);
  if (!CODECS[c.codec] || !CODECS[c.codec].includes(c.format)) { c.codec = 'h264'; c.format = 'mp4'; }
  c.range = 'all'; c.from = 0; c.to = 0; c.name = String(c.name || '');
  return c;
}
function saveCfg() { try { const { range, from, to, ...rest } = cfg; localStorage.setItem(KEY, JSON.stringify(rest)); } catch (e) { /* ignore */ } }

function buildOptions(c = cfg) {
  const o = { codec: c.codec, format: c.format, audio: c.audio, name: c.name.trim(), folder: (c.folder || 'exports').trim(), exists: c.exists, open_when_done: c.open, fps: c.fps || null };
  if (c.preset !== 'custom') o.preset = c.preset;
  if (c.audio || AUDIO_ONLY(c.codec)) o.audio_bitrate = c.abr;
  if (['h264', 'h265', 'vp9'].includes(c.codec)) { if (c.qmode === 'bitrate') o.bitrate = c.bitrate; else o.crf = c.crf; }
  if (c.speed) o.speed = c.speed;
  if (!AUDIO_ONLY(c.codec)) {
    if (c.res === 'seq') o.scale = 1; else if (c.res === 'half') o.scale = 0.5; else if (c.res === 'custom') o.width = Math.round(c.width) || 1280; else o.fit_long = RES[c.res];
  }
  if (c.range === 'custom') o.range = { from: Math.max(0, +c.from || 0), to: +c.to > 0 ? +c.to : null };
  return o;
}
const rangeError = () => cfg.range === 'custom' && +cfg.to > 0 && +cfg.to <= +cfg.from;

// ---------------------------------------------------------------- helpers
const mmss = s => { s = Math.max(0, Math.round(s)); return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0'); };
function bytes(n) {
  if (n == null) return '—';
  const u = ['B', 'KB', 'MB', 'GB']; let i = 0;
  while (n >= 1024 && i < 3) { n /= 1024; i++; }
  return (n >= 100 || i === 0 ? Math.round(n) : n.toFixed(1)) + ' ' + u[i];
}
function outSize() {
  const P = S.project;
  if (AUDIO_ONLY(cfg.codec)) return null;
  if (plan && plan.size) return plan.size;
  const w0 = P.width, h0 = P.height;
  const k = cfg.res === 'half' ? 0.5 : cfg.res === 'custom' ? (cfg.width || w0) / w0 : RES[cfg.res] ? RES[cfg.res] / Math.max(w0, h0) : 1;
  return [Math.max(2, Math.round(w0 * k / 2) * 2), Math.max(2, Math.round(h0 * k / 2) * 2)];
}
function seconds() {
  const d = S.project.duration;
  if (cfg.range !== 'custom') return d;
  const a = clamp(+cfg.from || 0, 0, d), b = +cfg.to > 0 ? clamp(+cfg.to, 0, d) : d;
  return Math.max(0, b - a);
}
function effFps() { return cfg.fps || (cfg.codec === 'gif' ? Math.min(S.project.fps, 15) : S.project.fps); }
function estimate() {
  const dur = seconds(), sz = outSize();
  const audio = AUDIO_ONLY(cfg.codec) ? (cfg.codec === 'wav' ? 192000 : cfg.abr * 125) : (cfg.audio && cfg.codec !== 'gif' ? (cfg.codec === 'prores' ? 192000 : cfg.abr * 125) : 0);
  let v = 0;
  if (sz) {
    const px = sz[0] * sz[1] * effFps();
    const crf = cfg.crf;
    if (cfg.codec === 'prores') v = px * 3.5 / 8;
    else if (cfg.codec === 'gif') v = px * 0.06;
    else if (cfg.qmode === 'bitrate') v = cfg.bitrate * 125;
    else if (cfg.codec === 'h264') v = px * 0.09 * Math.pow(2, (18 - crf) / 6) / 8;
    else if (cfg.codec === 'h265') v = px * 0.055 * Math.pow(2, (22 - crf) / 6) / 8;
    else v = px * 0.07 * Math.pow(2, (32 - crf) / 6) / 8;
  }
  return (v + audio) * dur;
}

// ---------------------------------------------------------------- dialog
function field(labelKey, ...kids) { const r = el('div', 'xp-row'); r.append(el('label', '', t(labelKey))); const b = el('div', 'xp-ctl'); b.append(...kids); r.append(b); return r; }
function select(opts, value, onch) {
  const s = el('select');
  for (const [v, label] of opts) { const o = el('option', '', label); o.value = v; s.append(o); }
  s.value = value; s.addEventListener('change', () => onch(s.value)); return s;
}
function input(type, value, onch, extra = {}) {
  const i = el('input'); i.type = type; i.value = value; Object.assign(i, extra); i.spellcheck = false;
  i.addEventListener('input', () => onch(i.value)); return i;
}
const edit = (patch, custom = true) => { Object.assign(cfg, patch); if (custom && cfg.preset !== 'custom') cfg.preset = 'custom'; saveCfg(); sync(); wantPlan(); };

function buildDialog() {
  if (dlg) dlg.remove();
  refs = {};
  dlg = el('div', 'xp-overlay'); dlg.hidden = true; dlg.setAttribute('role', 'dialog');
  dlg.addEventListener('pointerdown', e => { if (e.target === dlg) closeDialog(); });
  dlg.addEventListener('keydown', e => {
    if (e.key === 'Escape') { e.preventDefault(); if (last && last.status === 'running') cancelRender(); else closeDialog(); }
    e.stopPropagation();
  });
  const box = el('div', 'xp-box'); dlg.append(box);
  const head = el('div', 'xp-head'); head.append(el('b', '', t('export.title')));
  const x = el('button', 'icon-btn'); x.append(icon('x')); x.title = t('export.close'); x.addEventListener('click', closeDialog); head.append(x); box.append(head);
  const body = el('div', 'xp-body'); box.append(body);

  const presetSel = select([...PRESET_IDS.map(id => [id, t('export.preset.' + id)]), ['custom', t('export.preset.custom')]], cfg.preset, id => {
    if (id === 'custom') { edit({}, true); return; }
    cfg = fromPreset(id, cfg); saveCfg(); sync(); wantPlan();
  });
  refs.preset = presetSel; body.append(field('export.preset', presetSel));
  refs.summary = el('div', 'xp-summary'); body.append(refs.summary);

  refs.name = input('text', cfg.name, v => edit({ name: v }, false), { placeholder: '' });
  refs.ext = el('span', 'xp-ext');
  const nameBox = el('div', 'xp-namebox'); nameBox.append(refs.name, refs.ext);
  body.append(field('export.name', nameBox));
  refs.folder = input('text', cfg.folder, v => edit({ folder: v }, false));
  body.append(field('export.folder', refs.folder));
  refs.path = el('div', 'xp-path'); body.append(refs.path);
  refs.exists = el('div', 'xp-exists');
  refs.existsSel = select([['rename', t('export.existsRename')], ['overwrite', t('export.existsOverwrite')]], cfg.exists, v => edit({ exists: v }, false));
  const exw = el('span', 'xp-warn'); exw.append(icon('triangle-alert', 's'), el('span', '', t('export.exists'))); refs.exists.append(exw, refs.existsSel); body.append(refs.exists);

  const adv = el('details', 'xp-adv'); refs.adv = adv;
  adv.open = (() => { try { return localStorage.getItem(KEY + '.adv') === '1'; } catch (e) { return false; } })();
  adv.addEventListener('toggle', () => { try { localStorage.setItem(KEY + '.adv', adv.open ? '1' : '0'); } catch (e) { /* ignore */ } });
  adv.append(el('summary', '', t('export.advanced')));
  const ab = el('div', 'xp-advbody'); adv.append(ab); body.append(adv);

  refs.codec = select(Object.keys(CODECS).map(c => [c, CODEC_LABEL[c]]), cfg.codec, c => edit({ codec: c, format: CODECS[c][0], crf: DEFAULT_CRF[c] ?? cfg.crf, audio: c === 'gif' ? false : (AUDIO_ONLY(c) ? true : cfg.audio) }));
  ab.append(field('export.codec', refs.codec));
  refs.format = select([], cfg.format, v => edit({ format: v })); refs.rowFormat = field('export.format', refs.format); ab.append(refs.rowFormat);

  refs.qmode = select([['crf', t('export.qualityCrf')], ['bitrate', t('export.qualityBitrate')]], cfg.qmode, v => edit({ qmode: v }));
  refs.crf = input('range', cfg.crf, v => edit({ crf: +v }), { min: 10, max: 45, step: 1 });
  refs.crfVal = el('span', 'xp-val'); refs.crfHint = el('span', 'xp-hint', t('export.crfHint'));
  refs.bitrate = input('number', cfg.bitrate / 1000, v => edit({ bitrate: Math.max(0.1, +v || 8) * 1000 }), { min: 0.1, max: 500, step: 0.5 });
  refs.mbps = el('span', 'xp-hint', t('export.mbps'));
  refs.rowQ = field('export.quality', refs.qmode, refs.crf, refs.crfVal, refs.bitrate, refs.mbps); ab.append(refs.rowQ);

  refs.res = select(Object.keys(RES).map(k => [k, t('export.res.' + k)]), cfg.res, v => edit({ res: v }));
  refs.width = input('number', cfg.width, v => edit({ width: +v || 1280 }), { min: 16, max: 16384, step: 2 });
  refs.resInfo = el('span', 'xp-hint');
  refs.rowRes = field('export.resolution', refs.res, refs.width, refs.resInfo); ab.append(refs.rowRes);

  refs.fps = select([['0', t('export.fpsSeq', { fps: S.project.fps })], ...[15, 24, 25, 30, 48, 50, 60].map(f => [String(f), f + ' fps'])], String(cfg.fps || 0), v => edit({ fps: +v }));
  refs.rowFps = field('export.fps', refs.fps); ab.append(refs.rowFps);

  refs.range = select([['all', t('export.rangeAll')], ['custom', t('export.rangeCustom')]], cfg.range, v => {
    const d = S.project.duration; edit({ range: v, from: cfg.from || 0, to: cfg.to || d }, false);
  });
  refs.from = input('number', cfg.from, v => edit({ from: +v || 0 }, false), { min: 0, step: 0.1 });
  refs.to = input('number', cfg.to, v => edit({ to: +v || 0 }, false), { min: 0, step: 0.1 });
  const useA = el('button', 'btn small xp-mini'); useA.append(icon('arrow-left-to-line', 's')); useA.title = t('export.useTime'); useA.addEventListener('click', () => { refs.from.value = S.t.toFixed(2); edit({ from: S.t }, false); });
  const useB = el('button', 'btn small xp-mini'); useB.append(icon('arrow-right-to-line', 's')); useB.title = t('export.useTime'); useB.addEventListener('click', () => { refs.to.value = S.t.toFixed(2); edit({ to: S.t }, false); });
  refs.rangeBox = el('span', 'xp-rangebox'); refs.rangeBox.append(el('span', 'xp-hint', t('export.from')), refs.from, useA, el('span', 'xp-hint', t('export.to')), refs.to, useB);
  refs.rangeErr = el('div', 'xp-warn xp-rangeerr', t('export.rangeInvalid'));
  refs.rowRange = field('export.range', refs.range, refs.rangeBox); ab.append(refs.rowRange, refs.rangeErr);

  refs.audio = input('checkbox', '', v => { }); refs.audio.checked = cfg.audio; refs.audio.addEventListener('change', () => edit({ audio: refs.audio.checked }));
  refs.audioLbl = el('label', 'xp-check'); refs.audioLbl.append(refs.audio, el('span', '', t('export.audioOn')));
  refs.abr = select([64, 96, 128, 160, 192, 256, 320].map(b => [String(b), b + ' kbps']), String(cfg.abr), v => edit({ abr: +v }));
  refs.rowAudio = field('export.audio', refs.audioLbl, refs.abr); ab.append(refs.rowAudio);

  refs.open = input('checkbox', '', () => { }); refs.open.checked = cfg.open; refs.open.addEventListener('change', () => edit({ open: refs.open.checked }, false));
  const openLbl = el('label', 'xp-check'); openLbl.append(refs.open, el('span', '', t('export.openWhenDone'))); body.append(openLbl);

  refs.est = el('div', 'xp-est'); body.append(refs.est);

  const foot = el('div', 'xp-foot');
  refs.draft = el('button', 'btn', t('export.draftPreview')); refs.draft.addEventListener('click', () => start({ preset: 'draft', options: { exists: 'overwrite', open_when_done: false } }));
  refs.cancel = el('button', 'btn', t('export.cancelRender')); refs.cancel.addEventListener('click', cancelRender);
  refs.go = el('button', 'btn primary', t('export.start')); refs.go.addEventListener('click', () => start({ options: buildOptions() }));
  foot.append(refs.draft, el('span', 'spacer'), refs.cancel, refs.go); box.append(foot);

  const pastHead = el('div', 'xp-pasthead', t('export.past'));
  refs.past = el('div', 'xp-past'); box.append(pastHead, refs.past);
  document.body.append(dlg);
}

function sync() {
  if (!dlg) return;
  const ao = AUDIO_ONLY(cfg.codec), gif = cfg.codec === 'gif', lossy = ['h264', 'h265', 'vp9'].includes(cfg.codec);
  refs.preset.value = cfg.preset;
  if (document.activeElement !== refs.name) refs.name.value = cfg.name;
  refs.name.placeholder = defaultName();
  refs.ext.textContent = '.' + cfg.format;
  if (document.activeElement !== refs.folder) refs.folder.value = cfg.folder;
  refs.codec.value = cfg.codec;
  refs.format.replaceChildren(...CODECS[cfg.codec].map(f => { const o = el('option', '', '.' + f + (f === 'mp4' ? ' (MPEG-4)' : f === 'mov' ? ' (QuickTime)' : f === 'mkv' ? ' (Matroska)' : f === 'webm' ? ' (WebM)' : '')); o.value = f; return o; }));
  refs.format.value = cfg.format;
  refs.rowFormat.hidden = CODECS[cfg.codec].length < 2;
  refs.rowQ.hidden = !lossy;
  refs.qmode.value = cfg.qmode; refs.crf.hidden = refs.crfVal.hidden = refs.crfHint.hidden = cfg.qmode !== 'crf'; refs.bitrate.hidden = refs.mbps.hidden = cfg.qmode !== 'bitrate';
  if (document.activeElement !== refs.crf) refs.crf.value = cfg.crf;
  refs.crf.style.setProperty('--p', ((cfg.crf - 10) / 35 * 100) + '%');
  refs.crfVal.textContent = 'CRF ' + cfg.crf;
  if (document.activeElement !== refs.bitrate) refs.bitrate.value = +(cfg.bitrate / 1000).toFixed(2);
  refs.rowRes.hidden = ao; refs.res.value = cfg.res; refs.width.hidden = cfg.res !== 'custom';
  const sz = outSize(); refs.resInfo.textContent = sz ? sz[0] + '×' + sz[1] : '';
  refs.rowFps.hidden = ao; refs.fps.value = String(cfg.fps || 0);
  if (![...refs.fps.options].some(o => o.value === refs.fps.value)) { const o = el('option', '', cfg.fps + ' fps'); o.value = refs.fps.value; refs.fps.append(o); refs.fps.value = String(cfg.fps); }
  refs.range.value = cfg.range; refs.rangeBox.hidden = cfg.range !== 'custom';
  if (document.activeElement !== refs.from) refs.from.value = +(+cfg.from).toFixed(3);
  if (document.activeElement !== refs.to) refs.to.value = +(+cfg.to).toFixed(3);
  refs.rangeErr.hidden = !rangeError();
  refs.rowAudio.hidden = gif || ao; refs.audio.checked = cfg.audio; refs.abr.value = String(cfg.abr); refs.abr.disabled = !cfg.audio;
  refs.abr.hidden = cfg.codec === 'prores';
  refs.open.checked = cfg.open; refs.existsSel.value = cfg.exists;
  const parts = [];
  if (sz) parts.push(sz[0] + '×' + sz[1], effFps() + ' fps');
  parts.push(CODEC_LABEL[cfg.codec] + ' .' + cfg.format, mmss(seconds()));
  refs.summary.textContent = parts.join(' · ');
  refs.est.textContent = t('export.estimate', { size: bytes(estimate()) });
  const running = last && last.status === 'running';
  refs.go.disabled = running || rangeError() || !!(plan && plan.error);
  refs.draft.disabled = running; refs.cancel.hidden = !running;
  if (plan) {
    refs.path.textContent = plan.error ? plan.error : t('export.saveTo', { path: plan.output });
    refs.path.classList.toggle('bad', !!plan.error);
    refs.exists.hidden = !plan.exists;
  } else { refs.path.textContent = ''; refs.exists.hidden = true; }
}
function defaultName() { return (S.project.name || 'export').replace(/[^\p{L}\p{N} .()+_-]/gu, '_') + '_' + cfg.preset; }

function wantPlan() { clearTimeout(planTimer); planTimer = setTimeout(doPlan, 200); }
async function doPlan() {
  const seq = ++planSeq;
  const r = await API.exportPlan({ options: buildOptions() }).catch(() => null);
  if (seq !== planSeq || !r) return;
  plan = r.ok ? r.data : { error: API.errorText(r.status, r.data), output: '', exists: false };
  sync();
}

async function refreshPast() {
  const list = await API.exportsList().catch(() => []);
  refs.past.replaceChildren();
  if (!list.length) { refs.past.append(el('div', 'xp-empty', t('export.pastEmpty'))); return; }
  for (const f of list) {
    const row = el('div', 'xp-pastrow');
    const n = el('span', 'xp-pn', f.name); n.title = f.path;
    const meta = el('span', 'xp-pm', bytes(f.size) + ' · ' + new Date(f.mtime * 1000).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' }));
    const open = el('a', 'xp-link'); open.append(el('span', '', t('export.open')), icon('external-link', 's')); open.href = f.url; open.target = '_blank';
    const rev = el('button', 'xp-link', t('export.reveal')); rev.addEventListener('click', () => API.exportReveal(f.path));
    row.append(n, meta, open, rev); refs.past.append(row);
  }
}

async function openDialog() {
  if (!S.project) return;
  if (!Object.keys(presets).length) presets = (await API.exportPresets()) || {};
  if (!dlg) buildDialog();
  if (!cfg.to) cfg.to = S.project.duration;
  dlg.hidden = false; plan = null; sync(); wantPlan(); refreshPast();
  refs.preset.focus();
}
function closeDialog() { if (dlg) dlg.hidden = true; }

// ---------------------------------------------------------------- run
async function start(body) {
  if (rangeError()) return;
  if (!(await API.saveAndWait())) { toast(t('toast.saveFirst'), 'warn'); return; }
  const r = await API.startRenderBody(body);
  if (!r.ok) { toast(t('toast.renderFailedStart', { reason: API.errorText(r.status, r.data) }), 'error', 5000); return; }
  toast(t('toast.exportStarted'), 'info', 2000);
  closeDialog(); poll();
  setTimeout(() => $('progress').focus(), 60);
}
async function cancelRender() {
  const r = await API.cancelRender();
  if (r.ok) status(r.data); else poll();
}
function poll() {
  clearInterval(timer);
  const tick = async () => { const d = await API.renderStatus().catch(() => null); status(d); if (d && d.status !== 'running') clearInterval(timer); };
  timer = setInterval(tick, 600); tick();
}
function status(d) {
  last = d;
  if (!d) return;
  const box = $('progress'), open = $('render-open'), running = d.status === 'running';
  box.hidden = $('b-cancel').hidden = !running;
  if (running) {
    const p = clamp(d.progress || 0, 0, 1);
    box.querySelector('i').style.width = (p * 100) + '%';
    const parts = [t('render.progress', { pct: Math.round(p * 100) }), mmss(d.elapsed || 0), d.eta != null ? t('render.eta', { eta: mmss(d.eta) }) : t('render.etaUnknown')];
    box.querySelector('span').textContent = parts.join(' · ');
    box.title = (d.name || '') + (d.output_size ? ' — ' + bytes(d.output_size) : '');
  }
  open.hidden = !(d.status === 'done' && d.output); if (d.output) open.href = d.output;
  if (d.status !== lastState) {
    if (d.status === 'done' && lastState === 'running') { toast(t('toast.renderDone'), 'ok', 5000); if (dlg && !dlg.hidden) refreshPast(); }
    if (d.status === 'error') toast(t('toast.renderError', { reason: d.error || t('toast.unknown') }), 'error', 8000);
    if (d.status === 'cancelled' && lastState === 'running') toast(t('toast.renderCancelled'), 'info', 2500);
    lastState = d.status;
  }
  sync();
}

// ---------------------------------------------------------------- boot
export async function startExport(opts) {
  toast = opts.toast;
  cfg = loadCfg();
  $('b-export').addEventListener('click', openDialog);
  $('b-cancel').addEventListener('click', cancelRender);
  $('progress').addEventListener('keydown', e => { if (e.key === 'Escape' && last && last.status === 'running') { e.preventDefault(); cancelRender(); } });
  window.addEventListener('keydown', e => {
    if (isMod(e) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === 'e') { e.preventDefault(); e.stopPropagation(); if (dlg && !dlg.hidden) closeDialog(); else openDialog(); }
  }, true);
  onLang(() => { if (dlg) { const was = !dlg.hidden; buildDialog(); dlg.hidden = !was; sync(); if (was) refreshPast(); } status(last); });
  onChange(kind => { if (kind === 'project' && dlg && !dlg.hidden) { sync(); wantPlan(); } });
  const d = await API.renderStatus().catch(() => null);
  if (d && d.status === 'running') poll(); else status(d);
  window.__kurguExport = { openDialog, get cfg() { return cfg; } };  // tests
}

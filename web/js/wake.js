// wake.js — what happened to the agent after Send: the server records the wake run (.kurgu/wake.json, shown by /api/notes/info as `wake`).
// While it runs the sent notes say "Agent is working…"; when it failed a persistent banner says why (with a hint for the usual causes and a link to
// hook.log); when it finished fine but notes are still open, the banner says that. Lists (notes panel, watch / review lists) call wakeBlock() /
// wakeWorking(n) while rendering and re-render on the `kurgu-wake` window event.
import { S } from './state.js';
import * as API from './api.js';
import { t } from './i18n.js';
import { el } from './dom.js';

{ const link = document.createElement('link'); link.rel = 'stylesheet'; link.href = '/web/wake.css'; document.head.append(link); }

let status = {}, timer = null, kicks = 0;
const sentOpen = () => (S.project ? S.project.notes.filter(n => n.author === 'user' && n.sent && !n.done && !n.asked) : []);
const isBad = w => w.state === 'stopped' || (w.state === 'finished' && w.exit_code !== 0 && w.exit_code !== null);
export const wakeStatus = () => status;
export const wakeRunning = () => status.state === 'running';
/** Is the agent working on this note right now? */
export const wakeWorking = n => wakeRunning() && n.author === 'user' && !!n.sent && !n.done && !n.asked;
/** A signature of what the banner / chips show, for row-diffing lists. */
export const wakeSig = () => JSON.stringify([status.state, status.exit_code, status.started, sentOpen().length]);

/** `x` in backticks becomes <code>. */
function richText(s) {
  const f = document.createDocumentFragment();
  String(s).split('`').forEach((part, i) => f.append(i % 2 ? el('code', '', part) : document.createTextNode(part)));
  return f;
}
/** The banner element for the current status, or null (nothing to say). */
export function wakeBlock() {
  const w = status, open = sentOpen();
  if (!w.state || w.state === 'running') return null;
  if (isBad(w)) {
    if (!open.length) return null;
    const box = el('div', 'wk-banner err'); box.setAttribute('role', 'alert');
    const why = w.state === 'stopped' ? t('wake.timeout') : (w.summary || (w.tail && w.tail[w.tail.length - 1]) || '');
    box.append(el('b', 'wk-title', why ? t('wake.failed', { why: why.length > 140 ? why.slice(0, 139) + '…' : why }) : t('wake.failedNoWhy')));
    const hint = t(['login', 'missing', 'codex_no_auth_file', 'codex_auth_replaced', 'codex_prepare_failed', 'codex_policy'].includes(w.reason) ? 'wake.hint.' + w.reason : 'wake.hint.other');
    const p = el('span', 'wk-hint'); p.append(richText(hint)); box.append(p);
    const a = el('a', 'wk-log', t('wake.log')); a.href = '/api/notes/hook-log'; a.target = '_blank'; a.rel = 'noopener'; box.append(a);
    return box;
  }
  const left = open.filter(n => !w.started || n.sent <= w.started).length;
  if (w.state === 'finished' && left) {
    const box = el('div', 'wk-banner warn'); box.setAttribute('role', 'status');
    box.append(el('b', 'wk-title', t(left === 1 ? 'wake.openLeft1' : 'wake.openLeftN', { n: left })));
    const a = el('a', 'wk-log', t('wake.log')); a.href = '/api/notes/hook-log'; a.target = '_blank'; a.rel = 'noopener'; box.append(a);
    return box;
  }
  return null;
}

const announce = () => window.dispatchEvent(new Event('kurgu-wake'));
async function poll() {
  timer = null;
  let i = null;
  try { i = await API.notesInfo(true); } catch (e) { /* offline: try again */ }
  const next = (i && i.wake) || {};
  const changed = JSON.stringify(next) !== JSON.stringify(status);
  status = next;
  if (changed) announce();
  if (status.state === 'running' || (kicks > 0 && !status.state)) { kicks = Math.max(0, kicks - 1); timer = setTimeout(poll, 1500); }
}
/** Look at the wake status now and keep looking while the agent runs (call after Send, and once at start-up). */
export function wakeKick(afterSend = false) {
  if (afterSend) kicks = 3;                      // the record is written just before the send answers; a few looks cover a slow disk
  if (timer) clearTimeout(timer);
  timer = setTimeout(poll, afterSend ? 200 : 0);
}

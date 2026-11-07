// report.js — "Hata raporu": shows the plain-text report (GET /api/report) in a window BEFORE anything is copied; nothing is sent anywhere.
// Also forwards browser errors (window.onerror / unhandledrejection) to the project's event log (POST /api/diag/client, rate limited on both sides).
import { t } from './i18n.js';
import { el } from './dom.js';

let open = null;
export function closeReport() { if (open) { open.remove(); open = null; } }
export async function openReport() {
  closeReport();
  const back = el('div', 'rp-back'); back.id = 'report-modal'; back.setAttribute('role', 'dialog'); back.setAttribute('aria-modal', 'true'); back.setAttribute('aria-label', t('report.title'));
  const box = el('div', 'rp-box');
  const ta = el('textarea', 'rp-text'); ta.readOnly = true; ta.spellcheck = false; ta.value = t('report.loading');
  const copy = el('button', 'btn primary rp-copy', t('report.copy')); copy.type = 'button'; copy.disabled = true;
  const close = el('button', 'btn rp-close', t('report.close')); close.type = 'button';
  const done = () => { copy.textContent = t('report.copied'); setTimeout(() => { copy.textContent = t('report.copy'); }, 1500); };
  copy.addEventListener('click', () => {
    ta.select();
    try { if (navigator.clipboard && navigator.clipboard.writeText) { navigator.clipboard.writeText(ta.value).then(done, () => { document.execCommand('copy'); done(); }); return; } } catch (e) { /* fall through */ }
    try { document.execCommand('copy'); } catch (e) { /* ignore */ } done();
  });
  close.addEventListener('click', closeReport);
  back.addEventListener('keydown', e => { e.stopPropagation(); if (e.key === 'Escape') closeReport(); });
  back.addEventListener('pointerdown', e => { if (e.target === back) closeReport(); });
  box.append(el('h2', 'rp-title', t('report.title')), el('p', 'rp-hint', t('report.hint')), ta, el('div', 'rp-actions', ''));
  box.lastChild.append(copy, close);
  back.append(box); document.body.append(back); open = back; close.focus();
  try {
    const r = await fetch('/api/report', { cache: 'no-store' });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || r.status);
    ta.value = d.text; copy.disabled = false;
  } catch (e) { ta.value = t('report.failed', { reason: String(e.message || e) }); }
}

let sent = [];
function forward(message, source, line, col) {
  const now = Date.now(); sent = sent.filter(x => now - x < 60000);
  if (sent.length >= 5) return;
  sent.push(now);
  try { fetch('/api/diag/client', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: String(message).slice(0, 200), source: String(source || ''), line, col }), keepalive: true }).catch(() => {}); } catch (e) { /* ignore */ }
}
window.addEventListener('error', e => forward(e.message || 'error', e.filename, e.lineno, e.colno));
window.addEventListener('unhandledrejection', e => forward((e.reason && e.reason.message) || e.reason || 'unhandled rejection', '', null, null));

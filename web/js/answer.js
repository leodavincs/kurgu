// answer.js — the agent asked a question on a note (state `asked`): show it and let the human answer. The answer goes to POST /api/notes/answer,
// which puts the note back in the agent's inbox (new seq) with the answer attached. Used by the Notes panel and the watch / review lists.
import * as API from './api.js';
import { t } from './i18n.js';
import { el } from './dom.js';
import { loadExternal } from './state.js';

/** Question text + answer box for one note (or just the answer once it is answered). `toast(msg, kind, ms)` is optional. */
export function answerBlock(n, toast = () => {}) {
  if (!n.question) return null;
  const box = el('div', 'ask-box' + (n.asked && !n.done ? ' open' : ''));
  const q = el('div', 'ask-q'); q.append(el('b', '', t('ask.question') + ' '), el('span', '', n.question)); box.append(q);
  if (n.answer) { const a = el('div', 'ask-a'); a.append(el('b', '', t('ask.answer') + ' '), el('span', '', n.answer)); box.append(a); }
  if (n.asked && !n.done) {
    const row = el('div', 'ask-row'), inp = el('input', 'ask-in'); inp.placeholder = t('ask.placeholder'); inp.spellcheck = false;
    const go = el('button', 'ask-go', t('ask.send')); go.type = 'button';
    let busy = false;
    const submit = async () => {
      const v = inp.value.trim(); if (!v || busy) return; busy = true; go.disabled = true;
      try {
        const r = await API.answerNote(n.id, v);
        if (!r.ok) { toast(t('ask.failed', { reason: API.errorText(r.status, r.data) }), 'error', 6000); return; }
        loadExternal(r.data.project, r.data.version);
        toast(t('ask.sent'), 'ok', 4000);
      } catch (e) { toast(t('ask.failed', { reason: String(e.message || e) }), 'error', 6000); }
      finally { busy = false; go.disabled = false; }
    };
    inp.addEventListener('keydown', e => { e.stopPropagation(); if (e.key === 'Enter') { e.preventDefault(); submit(); } });
    inp.addEventListener('click', e => e.stopPropagation());
    go.addEventListener('click', e => { e.stopPropagation(); submit(); });
    row.append(inp, go); box.append(row);
  }
  return box;
}

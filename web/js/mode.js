// mode.js — Review ("İnceleme", the calm default: watch, note, send) vs Edit ("Düzen", the full editor). Pure presentation: body[data-mode]
// switches what is shown (web/review.css); nothing is removed and no state is lost. Remembered in localStorage; the first-visit default comes
// from the server (env KURGU_MODE or `default_mode` in .kurgu/config.json), else review.
import { emit } from './state.js';

{ const link = document.createElement('link'); link.rel = 'stylesheet'; link.href = '/web/review.css'; document.head.append(link); }
const KEY = 'kurgu.mode';
const ok = m => m === 'review' || m === 'edit';
export const getMode = () => (document.body.dataset.mode === 'edit' ? 'edit' : 'review');
export const isReview = () => getMode() === 'review';
/** Before first paint: remembered choice, else the server default. */
export function initMode(serverDefault) {
  let m = null; try { m = localStorage.getItem(KEY); } catch (e) { /* ignore */ }
  document.body.dataset.mode = ok(m) ? m : ok(serverDefault) ? serverDefault : 'review';
}
export function setMode(m) {
  if (!ok(m) || m === getMode()) return;
  document.body.dataset.mode = m;
  try { localStorage.setItem(KEY, m); } catch (e) { /* ignore */ }
  emit('mode'); emit('view');
  window.dispatchEvent(new Event('resize'));
}
export const toggleMode = () => setMode(isReview() ? 'edit' : 'review');

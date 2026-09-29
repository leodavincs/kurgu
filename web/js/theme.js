// theme.js — light / dark theme (data-theme on <html>) and the canvas colours read from the --cv-* tokens (web/tokens.css).
// The choice is stored in localStorage ("kurgu.theme"); without one the system preference decides and is followed live.
// index.html applies the initial theme in an inline script before first paint; this module keeps it in sync and toggles it.
import { emit } from './state.js';

const KEY = 'kurgu.theme';
const mq = window.matchMedia ? window.matchMedia('(prefers-color-scheme: light)') : null;
let cache = null;

function stored() { try { const v = localStorage.getItem(KEY); return v === 'light' || v === 'dark' ? v : null; } catch (e) { return null; } }
const system = () => (mq && mq.matches ? 'light' : 'dark');
export const getTheme = () => (document.documentElement.dataset.theme === 'light' ? 'light' : 'dark');

function apply(theme) {
  if (getTheme() === theme && document.documentElement.dataset.theme) return;
  document.documentElement.dataset.theme = theme;
  cache = null;
  emit('theme');
}
export function initTheme() {
  const t0 = stored() || system();
  document.documentElement.dataset.theme = t0; cache = null;
  if (mq) mq.addEventListener('change', () => { if (!stored()) apply(system()); }); // follows the system until the user picks one
}
export function setTheme(theme) {
  try { localStorage.setItem(KEY, theme); } catch (e) { /* ignore */ }
  apply(theme);
}
export const toggleTheme = () => setTheme(getTheme() === 'light' ? 'dark' : 'light');

/** Canvas colour token (e.g. cv('--cv-tick')). Read once per theme from getComputedStyle. */
export function cv(name) {
  if (!cache) cache = { theme: getTheme(), cs: getComputedStyle(document.documentElement), v: new Map() };
  let v = cache.v.get(name);
  if (v === undefined) { v = cache.cs.getPropertyValue(name).trim(); cache.v.set(name, v); }
  return v;
}
/** Changes whenever the token set changes (use in cache signatures of canvas drawings). */
export const themeKey = () => getTheme();

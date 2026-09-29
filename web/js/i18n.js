// i18n.js — message catalogue (flat dotted keys, {name} placeholders), language switch, locale-aware numbers.
// Catalogues live in web/i18n/<lang>.json. English is always loaded as the fallback.
import { shortcutText } from './platform.js';
export const LANGS = ['en', 'tr'];
let lang = 'en';
let dict = {};
let fallback = {};
const listeners = [];
const formatters = new Map();

async function loadCatalogue(l) {
  const r = await fetch('/web/i18n/' + l + '.json', { cache: 'no-store' });
  if (!r.ok) throw new Error('cannot load i18n/' + l + '.json');
  return r.json();
}

/** Initial language: localStorage, else Turkish for tr* browsers, else English. */
export function detectLang() {
  try { const s = localStorage.getItem('kurgu.lang'); if (LANGS.includes(s)) return s; } catch (e) { /* storage unavailable */ }
  return String(navigator.language || '').toLowerCase().startsWith('tr') ? 'tr' : 'en';
}

export async function initI18n() {
  fallback = await loadCatalogue('en');
  lang = detectLang();
  dict = lang === 'en' ? fallback : await loadCatalogue(lang);
  document.documentElement.lang = lang;
  applyStatic();
}

export const getLang = () => lang;
export function onLang(fn) { listeners.push(fn); }

export async function setLang(l) {
  if (!LANGS.includes(l) || l === lang) return;
  dict = l === 'en' ? fallback : await loadCatalogue(l);
  lang = l;
  try { localStorage.setItem('kurgu.lang', l); } catch (e) { /* ignore */ }
  document.documentElement.lang = l;
  applyStatic();
  for (const f of listeners) { try { f(l); } catch (e) { console.error(e); } }
}

/** Translate a key; {name} placeholders are replaced from params. Missing keys fall back to English, then to the key. */
export function t(key, params) {
  let s = dict[key];
  if (s === undefined) s = fallback[key];
  if (s === undefined) return key;
  if (params) s = s.replace(/\{(\w+)\}/g, (m, k) => (params[k] !== undefined ? params[k] : m));
  return shortcutText(s); // {mod} {shift} {alt} {modkey} → ⌘ / Ctrl by platform
}

/** Fill static markup: data-i18n (text), data-i18n-title, data-i18n-placeholder. */
export function applyStatic(root = document) {
  for (const e of root.querySelectorAll('[data-i18n]')) e.textContent = t(e.dataset.i18n);
  for (const e of root.querySelectorAll('[data-i18n-title]')) { e.title = t(e.dataset.i18nTitle); if (!e.textContent.trim()) e.setAttribute('aria-label', e.title); } // icon-only controls get their name from the tooltip
  for (const e of root.querySelectorAll('[data-i18n-placeholder]')) e.placeholder = t(e.dataset.i18nPlaceholder);
}

// ---------- numbers ----------
function formatter(min, max) {
  const key = lang + min + '/' + max;
  let f = formatters.get(key);
  if (!f) { f = new Intl.NumberFormat(lang, { minimumFractionDigits: min, maximumFractionDigits: max, useGrouping: false }); formatters.set(key, f); }
  return f;
}
const fixMinus = s => s.replace('-', '−');
/** Fixed decimals in the UI language: 12.40 (en) / 12,40 (tr). */
export function fmt(v, n = 2) { return Number.isFinite(v) ? fixMinus(formatter(n, n).format(v)) : ''; }
/** Up to n decimals, trailing zeros dropped. */
export function fmtShort(v, n = 3) { return Number.isFinite(v) ? fixMinus(formatter(0, n).format(v)) : ''; }
/** Parse user input; accepts both "," and "." as the decimal mark and the typographic minus. */
export function parseNum(s) {
  const n = parseFloat(String(s).replace('−', '-').replace(',', '.'));
  return Number.isFinite(n) ? n : NaN;
}

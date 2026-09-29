// dom.js — tiny DOM helpers shared by all modules.
export const $ = id => document.getElementById(id);
/** Create an element: el('div', 'class', 'text'). */
export const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
};

const SVGNS = 'http://www.w3.org/2000/svg';
/** Lucide icon from the inline sprite in index.html (web/icons/, built by scripts/build_icons.mjs): icon('play', 'small'). */
export function icon(name, cls = '') {
  const s = document.createElementNS(SVGNS, 'svg');
  s.setAttribute('class', 'ic' + (cls ? ' ' + cls : '')); s.setAttribute('aria-hidden', 'true'); s.setAttribute('viewBox', '0 0 24 24');
  const u = document.createElementNS(SVGNS, 'use'); u.setAttribute('href', '#i-' + name); s.append(u);
  return s;
}
/** Replace the content of `host` by one icon (no-op when it already shows it). */
export function setIcon(host, name, cls = '') {
  const key = name + '|' + cls;
  if (host.dataset.ic !== key) { host.dataset.ic = key; host.replaceChildren(icon(name, cls)); }
  return host;
}
/** Copy text to the clipboard (falls back to a hidden textarea); resolves to true when it worked. */
export async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; }
  catch (e) {
    const ta = el('textarea'); ta.value = text; document.body.append(ta); ta.select();
    let ok = false; try { ok = document.execCommand('copy'); } catch (e2) { /* ignore */ }
    ta.remove(); return ok;
  }
}

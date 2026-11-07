// menu.js — the hamburger menu (top-right): everything that is not in the Review top bar, grouped under small headings.
// Keyboard: Enter / Space opens, Up / Down / Home / End move, Esc closes and gives focus back, a click outside closes.
import { t } from './i18n.js';
import { el, icon } from './dom.js';
import { shortcutText } from './platform.js';

let box = null, btn = null, A = null;

export function closeMenu(refocus = false) {
  if (!box) return;
  box.remove(); box = null; btn.setAttribute('aria-expanded', 'false');
  document.removeEventListener('pointerdown', outside, true);
  if (refocus) btn.focus();
}
function outside(e) { if (box && !box.contains(e.target) && !btn.contains(e.target)) closeMenu(); }
export const menuOpen = () => !!box;

function build() {
  const m = el('div'); m.id = 'main-menu'; m.setAttribute('role', 'menu'); m.setAttribute('aria-label', t('menu.tip'));
  const head = txt => m.append(el('div', 'mm-head', txt));
  const item = (label, { on = null, kbd = '', fn, disabled = false, role = on === null ? 'menuitem' : 'menuitemcheckbox', hint = '' } = {}) => {
    const b = el('button', 'mm-item'); b.type = 'button'; b.setAttribute('role', role); b.tabIndex = -1; b.disabled = disabled;
    if (on !== null) b.setAttribute('aria-checked', String(!!on));
    const chk = el('span', 'mm-check'); if (on) chk.append(icon('check')); b.append(chk, el('span', 'mm-label', label));
    if (hint) b.append(el('span', 'mm-hint', hint));
    if (kbd) b.append(el('kbd', '', shortcutText(kbd)));
    b.addEventListener('click', () => { closeMenu(true); fn(); });
    m.append(b); return b;
  };
  head(t('menu.mode'));
  item(t('mode.review'), { on: A.mode() === 'review', role: 'menuitemradio', kbd: A.mode() === 'review' ? '' : A.modeKey, fn: () => A.setMode('review') });
  item(t('mode.edit'), { on: A.mode() === 'edit', role: 'menuitemradio', kbd: A.mode() === 'edit' ? '' : A.modeKey, fn: () => A.setMode('edit') });
  head(t('menu.project'));
  item(t('menu.sequence'), { hint: A.seqLabel(), fn: A.openSequence });
  head(t('menu.edit'));
  item(t('menu.addText'), { fn: A.addText }); item(t('menu.addColor'), { fn: A.addColor }); item(t('menu.fromFile'), { fn: A.fromFile });
  item(t('top.split'), { fn: A.split, kbd: A.splitKey }); item(t('top.snap'), { on: A.snap(), fn: A.toggleSnap, kbd: 'S' });
  head(t('menu.view'));
  item(t('guides.safe'), { on: A.safe(), fn: A.toggleSafe });
  item(t('guides.social'), { on: A.social(), fn: A.toggleSocial, disabled: !A.portrait() });
  item(t('guides.realFrame'), { on: A.realFrame(), fn: A.toggleRealFrame, kbd: 'R' });
  for (const z of A.zooms()) item(z.label, { on: z.on, role: 'menuitemradio', fn: z.fn });
  item(A.notesLabel(), { on: A.notesOn(), fn: A.toggleNotes, kbd: A.notesKey });
  head(t('menu.app'));
  item(t(A.theme() === 'light' ? 'menu.themeDark' : 'menu.themeLight'), { fn: A.toggleTheme });
  item('Türkçe', { on: A.lang() === 'tr', role: 'menuitemradio', fn: () => A.setLang('tr') });
  item('English', { on: A.lang() === 'en', role: 'menuitemradio', fn: () => A.setLang('en') });
  item(t('top.export'), { fn: A.exportVideo });
  m.addEventListener('keydown', e => {
    const items = [...m.querySelectorAll('.mm-item:not(:disabled)')], i = items.indexOf(document.activeElement);
    if (e.key === 'ArrowDown') { e.preventDefault(); items[(i + 1) % items.length].focus(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); items[(i - 1 + items.length) % items.length].focus(); }
    else if (e.key === 'Home') { e.preventDefault(); items[0].focus(); }
    else if (e.key === 'End') { e.preventDefault(); items[items.length - 1].focus(); }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeMenu(true); }
    else if (e.key === 'Tab') closeMenu();
    e.stopPropagation();
  });
  return m;
}
export function openMenu() {
  if (box) { closeMenu(); return; }
  box = build(); document.body.append(box);
  const r = btn.getBoundingClientRect();
  box.style.top = (r.bottom + 6) + 'px'; box.style.right = Math.max(8, window.innerWidth - r.right) + 'px'; box.style.maxHeight = (window.innerHeight - r.bottom - 16) + 'px';
  btn.setAttribute('aria-expanded', 'true');
  (box.querySelector('[aria-checked=true]') || box.querySelector('.mm-item')).focus();
  document.addEventListener('pointerdown', outside, true);
}
export function initMenu(button, actions) {
  btn = button; A = actions;
  btn.addEventListener('click', openMenu);
  btn.addEventListener('keydown', e => { if (e.key === 'ArrowDown' && !box) { e.preventDefault(); openMenu(); } });
}

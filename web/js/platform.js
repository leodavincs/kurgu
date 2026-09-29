// platform.js — one place that knows Mac vs. Windows/Linux: which modifier key means "command", and how shortcut hints are written.
// Strings in web/i18n/*.json use placeholders instead of hard-coded symbols: {mod} {shift} {alt} {modkey}  (see shortcutText).
const nav = typeof navigator !== 'undefined' ? navigator : {};
export const IS_MAC = /mac|iphone|ipad|ipod/i.test(nav.platform || (nav.userAgentData && nav.userAgentData.platform) || nav.userAgent || '');

/** True when the platform's command modifier is held: ⌘ on macOS, Ctrl elsewhere (Ctrl-click is a right-click on a Mac, Win is the OS key). */
export const isMod = e => (IS_MAC ? !!e.metaKey : !!e.ctrlKey);

/** Replace the modifier placeholders of a message: {mod}Z → ⌘Z / Ctrl+Z, {mod}{shift}Z → ⌘⇧Z / Ctrl+Shift+Z, {alt}→ ⌥ / Alt+, {modkey} → ⌘ / Ctrl. */
export function shortcutText(s) {
  if (s.indexOf('{') < 0) return s;
  return s.replace(/\{(mod|shift|alt|modkey)\}/g, (m, k) => {
    if (IS_MAC) return k === 'mod' || k === 'modkey' ? '⌘' : k === 'shift' ? '⇧' : '⌥';
    return k === 'mod' ? 'Ctrl+' : k === 'shift' ? 'Shift+' : k === 'alt' ? 'Alt+' : 'Ctrl';
  });
}
/** Shortcut hint from a spec such as 'mod+shift+z' → "⌘⇧Z" (Mac) / "Ctrl+Shift+Z" (elsewhere). */
export function keys(spec) {
  const parts = String(spec).split('+');
  const key = parts.pop();
  const mods = parts.map(p => (p === 'mod' ? '{mod}' : p === 'shift' ? '{shift}' : p === 'alt' ? '{alt}' : ''));
  const k = key.length === 1 ? key.toUpperCase() : key;
  return shortcutText(mods.join('') + k);
}

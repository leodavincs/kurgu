#!/usr/bin/env node
// Builds the Lucide (ISC) icon sprite: web/icons/icons.svg, and the copy inlined in web/index.html between the
// <!-- icons:begin --> / <!-- icons:end --> markers. Source: an npm install of lucide-react (or lucide-static's icon nodes).
//   node scripts/build_icons.mjs [path/to/node_modules/lucide-react]
// Add a name to NAMES, run again, then use icon('name') from web/js/dom.js.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const SRC = process.argv[2] || process.env.LUCIDE_DIR || 'node_modules/lucide-react';
const NAMES = `arrow-down arrow-left-to-line arrow-right-to-line arrow-up check chevron-down chevron-left chevron-right chevron-up circle-check circle-dot
clapperboard copy corner-down-left diamond dot download external-link eye eye-off film flag folder-open gauge grid-3x3 grip-vertical image info languages layers
layout-grid link list lock lock-open magnet maximize-2 move-horizontal moon music panel-left pause pencil play plus redo-2 scan-eye scissors search settings-2
skip-back skip-forward sliders-horizontal snowflake square sun trash-2 triangle-alert type undo-2 unlink upload video volume-2 volume-x x zoom-in zoom-out`.split(/\s+/);

const attr = a => Object.entries(a).filter(([k]) => k !== 'key').map(([k, v]) => `${k}="${v}"`).join(' ');
let out = '';
for (const n of NAMES) {
  const f = path.join(SRC, 'dist/esm/icons', n + '.js');
  const m = fs.readFileSync(f, 'utf8').match(/const __iconNode = (\[[\s\S]*?\]);\n/);
  if (!m) throw new Error('cannot parse ' + n);
  const nodes = new Function('return ' + m[1])();
  out += `<symbol id="i-${n}" viewBox="0 0 24 24">${nodes.map(([tag, a]) => `<${tag} ${attr(a)}/>`).join('')}</symbol>\n`;
}
const sprite = `<svg xmlns="http://www.w3.org/2000/svg" width="0" height="0" style="position:absolute" aria-hidden="true" focusable="false">\n${out}</svg>`;
fs.mkdirSync(path.join(ROOT, 'web/icons'), { recursive: true });
fs.writeFileSync(path.join(ROOT, 'web/icons/icons.svg'), sprite + '\n');
fs.copyFileSync(path.join(SRC, 'LICENSE'), path.join(ROOT, 'web/icons/LICENSE'));
const htmlPath = path.join(ROOT, 'web/index.html');
let html = fs.readFileSync(htmlPath, 'utf8');
html = html.replace(/<!-- icons:begin -->[\s\S]*?<!-- icons:end -->/, `<!-- icons:begin -->\n${sprite}\n<!-- icons:end -->`);
fs.writeFileSync(htmlPath, html);
console.log(NAMES.length + ' icons');

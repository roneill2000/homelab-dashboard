import { build } from 'esbuild';
import { readFile, writeFile } from 'node:fs/promises';
// m3-tokens.mjs is bundled first: @material/material-color-utilities 0.4.0 cannot be imported by plain Node.
const tokensJs = (await build({entryPoints:['m3-tokens.mjs'],bundle:true,platform:'node',format:'esm',write:false})).outputFiles[0].text;
const { m3TokensCss } = await import('data:text/javascript;base64,' + Buffer.from(tokensJs).toString('base64'));
import { ICONS } from './m3-icons.mjs';
import { m3ShapesCss } from './m3-shapes.mjs';
const result = await build({entryPoints:['src/app.jsx'],bundle:true,write:false,jsxFactory:'React.createElement',jsxFragment:'React.Fragment',target:'es2020',minify:true});
// CSS order matters: generated colour roles, M3 system tokens, legacy rules, M3 components, then m3-controls.css
// (buttons, button groups, chips) and m3-motion.css (Expressive motion, all of it off under Reduce motion) last.
const parts = ['src/m3-sys.css', 'src/m3-expressive.css', 'src/styles.css', 'src/m3-legacy.css', 'src/m3-components.css', 'src/m3-expressive-components.css', 'src/m3-controls.css', 'src/m3-motion.css'];

function wrapHoverInCss(css) {
  let result = '', i = 0, n = css.length, stack = [];
  while (i < n) {
    if (css[i] === '/' && css[i+1] === '*') {
      const end = css.indexOf('*/', i + 2);
      if (end === -1) { result += css.slice(i); break; }
      result += css.slice(i, end + 2); i = end + 2; continue;
    }
    if (css[i] === '@') {
      let start = i;
      while (i < n && css[i] !== '{' && css[i] !== ';') i++;
      if (i < n && css[i] === ';') { result += css.slice(start, i + 1); i++; continue; }
      if (i < n && css[i] === '{') {
        const prelude = css.slice(start, i).trim();
        stack.push({ prelude, isHoverMedia: /@media[^{]*\(\s*hover\s*:\s*hover\s*\)/i.test(prelude) });
        result += css.slice(start, i + 1); i++; continue;
      }
    }
    if (css[i] === '}') {
      if (stack.length > 0) stack.pop();
      result += '}'; i++; continue;
    }
    let selStart = i;
    while (i < n && css[i] !== '{' && css[i] !== '}' && css[i] !== '@') {
      if (css[i] === '/' && css[i+1] === '*') {
        const end = css.indexOf('*/', i + 2);
        if (end === -1) break;
        i = end + 2;
      } else { i++; }
    }
    if (i < n && css[i] === '{') {
      const selector = css.slice(selStart, i);
      let bodyStart = i + 1, depth = 1; i++;
      while (i < n && depth > 0) {
        if (css[i] === '/' && css[i+1] === '*') {
          const end = css.indexOf('*/', i + 2);
          if (end === -1) break;
          i = end + 2;
        } else if (css[i] === '{') { depth++; i++; }
        else if (css[i] === '}') { depth--; i++; }
        else { i++; }
      }
      const body = css.slice(bodyStart, i - 1);
      if (selector.includes(':hover') && !stack.some(s => s.isHoverMedia)) {
        result += `@media (hover: hover) { ${selector} { ${body} } }`;
      } else {
        result += `${selector}{${body}}`;
      }
      continue;
    }
    if (i < n) result += css[i]; // trailing whitespace once left i === n and appended "undefined"
    i++;
  }
  return result;
}

function checkNoUnwrappedHover(css) {
  let i = 0, n = css.length, stack = [];
  while (i < n) {
    if (css[i] === '/' && css[i+1] === '*') {
      const end = css.indexOf('*/', i + 2);
      if (end === -1) break;
      i = end + 2; continue;
    }
    if (css[i] === '@') {
      let start = i;
      while (i < n && css[i] !== '{' && css[i] !== ';') i++;
      if (i < n && css[i] === ';') { i++; continue; }
      if (i < n && css[i] === '{') {
        const prelude = css.slice(start, i).trim();
        stack.push({ prelude, isHoverMedia: /@media[^{]*\(\s*hover\s*:\s*hover\s*\)/i.test(prelude) });
        i++; continue;
      }
    }
    if (css[i] === '}') {
      if (stack.length > 0) stack.pop();
      i++; continue;
    }
    let selStart = i;
    while (i < n && css[i] !== '{' && css[i] !== '}' && css[i] !== '@') {
      if (css[i] === '/' && css[i+1] === '*') {
        const end = css.indexOf('*/', i + 2);
        if (end === -1) break;
        i = end + 2;
      } else { i++; }
    }
    if (i < n && css[i] === '{') {
      const selector = css.slice(selStart, i);
      let depth = 1; i++;
      while (i < n && depth > 0) {
        if (css[i] === '/' && css[i+1] === '*') {
          const end = css.indexOf('*/', i + 2);
          if (end === -1) break;
          i = end + 2;
        } else if (css[i] === '{') { depth++; i++; }
        else if (css[i] === '}') { depth--; i++; }
        else { i++; }
      }
      if (selector.includes(':hover') && !stack.some(s => s.isHoverMedia)) {
        throw new Error(`Build failed: unwrapped :hover selector found in CSS: "${selector.trim()}"`);
      }
      continue;
    }
    i++;
  }
}

import crypto from 'node:crypto';

let manifest;
try {
  manifest = JSON.parse(await readFile('fonts/manifest.json', 'utf8'));
} catch (e) {
  throw new Error('fonts/manifest.json missing. Run node fonts/fetch-fonts.mjs');
}
const sortedIcons = [...new Set(ICONS)].sort().join(',');
const iconsHash = crypto.createHash('sha256').update(sortedIcons).digest('hex');
if (manifest.icons_hash !== iconsHash) {
  throw new Error('ICONS hash does not match fonts/manifest.json. Run node fonts/fetch-fonts.mjs');
}

const fontsCss = await readFile('fonts/fonts.css', 'utf8');
const preloads = `<link rel="preload" as="font" type="font/woff2" crossorigin href="/static/fonts/${manifest.fonts['google-sans-flex']}"><link rel="preload" as="font" type="font/woff2" crossorigin href="/static/fonts/${manifest.fonts['roboto-flex']}"><link rel="preload" as="font" type="font/woff2" crossorigin href="/static/fonts/${manifest.fonts['material-symbols-rounded']}">`;

const rawCss = (await Promise.all(parts.map(p => readFile(p, 'utf8').catch(() => '')))).join('\n');
const processedCss = wrapHoverInCss(rawCss);
checkNoUnwrappedHover(processedCss);
const css = fontsCss + m3TokensCss() + m3ShapesCss() + processedCss;
const js = result.outputFiles[0].text.replaceAll('</script','<\\/script');
const html = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"><meta name="theme-color" content="#091613"><meta name="apple-mobile-web-app-capable" content="yes"><link rel="manifest" href="/manifest.json"><link rel="icon" href="/icon.svg">${preloads}<title>Homelab · Control room</title><style>${css}</style><script src="/static/react.js"></script><script src="/static/react-dom.js"></script><script>/*__INITIAL_TELEMETRY_SLOT__*/</script></head><body><div id="root"></div><noscript>This dashboard needs JavaScript to display live telemetry.</noscript><script>${js}</script></body></html>`;
await writeFile('index.html',html);
console.log(`Built index.html (${Math.round(html.length/1024)} KiB)`);

import { build } from 'esbuild';
import { readFile, writeFile } from 'node:fs/promises';
// m3-tokens.mjs is bundled first: @material/material-color-utilities 0.4.0 cannot be imported by plain Node.
const tokensJs = (await build({entryPoints:['m3-tokens.mjs'],bundle:true,platform:'node',format:'esm',write:false})).outputFiles[0].text;
const { m3TokensCss } = await import('data:text/javascript;base64,' + Buffer.from(tokensJs).toString('base64'));
import { ICONS } from './m3-icons.mjs';
const result = await build({entryPoints:['src/app.jsx'],bundle:true,write:false,jsxFactory:'React.createElement',jsxFragment:'React.Fragment',target:'es2020',minify:true});
// CSS order matters: generated colour roles, M3 system tokens, legacy bridge, legacy rules, M3 components (win ties).
const parts = ['src/m3-sys.css', 'src/m3-expressive.css', 'src/styles.css', 'src/m3-legacy.css', 'src/m3-components.css', 'src/m3-expressive-components.css'];
const css = m3TokensCss() + (await Promise.all(parts.map(p => readFile(p, 'utf8').catch(() => '')))).join('\n');
const symbols = `https://fonts.googleapis.com/css2?family=Material+Symbols+Rounded:opsz,wght,FILL,GRAD@20..48,400,0..1,0&icon_names=${[...new Set(ICONS)].sort().join(',')}&display=block`;
const js = result.outputFiles[0].text.replaceAll('</script','<\\/script');
const html = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"><meta name="theme-color" content="#0b0f0d"><meta name="apple-mobile-web-app-capable" content="yes"><link rel="manifest" href="/manifest.json"><link rel="icon" href="/icon.svg"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Google+Sans+Flex:opsz,wght@6..144,1..1000&family=Google+Sans+Code:wght@300..800&family=Roboto+Flex:opsz,wght@8..144,100..1000&display=swap"><link rel="stylesheet" href="${symbols}"><title>Homelab · Control room</title><style>${css}</style><script src="/static/react.js"></script><script src="/static/react-dom.js"></script><script>/*__INITIAL_TELEMETRY_SLOT__*/</script></head><body><div id="root"></div><noscript>This dashboard needs JavaScript to display live telemetry.</noscript><script>${js}</script></body></html>`;
await writeFile('index.html',html);
console.log(`Built index.html (${Math.round(html.length/1024)} KiB)`);

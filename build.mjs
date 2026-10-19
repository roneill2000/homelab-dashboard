import { build } from 'esbuild';
import { readFile, writeFile } from 'node:fs/promises';
const result = await build({entryPoints:['src/app.jsx'],bundle:true,write:false,jsxFactory:'React.createElement',jsxFragment:'React.Fragment',target:'es2020',minify:true});
const css = await readFile('src/styles.css','utf8');
const js = result.outputFiles[0].text.replaceAll('</script','<\\/script');
const html = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"><meta name="theme-color" content="#0b1119"><meta name="apple-mobile-web-app-capable" content="yes"><link rel="manifest" href="/manifest.json"><link rel="icon" href="/icon.svg"><title>Homelab · Control room</title><style>${css}</style><script src="/static/react.js"></script><script src="/static/react-dom.js"></script><script>/*__INITIAL_TELEMETRY_SLOT__*/</script></head><body><div id="root"></div><noscript>This dashboard needs JavaScript to display live telemetry.</noscript><script>${js}</script></body></html>`;
await writeFile('index.html',html);
console.log(`Built index.html (${Math.round(html.length/1024)} KiB)`);

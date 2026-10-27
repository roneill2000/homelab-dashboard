// Computed-style snapshot of every element on every view (412/1000/1440, dark/light), for refactoring CSS safely.
// Usage: node style-snapshot.mjs <out.json>    then    node style-snapshot.mjs --diff a.json b.json
// Needs the preview running (python3 preview.py). Live data is frozen: /api/* responses are recorded on the first
// visit and replayed, and the clock is fixed, so two runs differ only when the CSS does.
import { chromium } from '@playwright/test';
import { readFileSync, writeFileSync, existsSync } from 'node:fs';

const PROPS = ['display', 'position', 'width', 'height', 'margin', 'padding', 'gap', 'grid-template-columns', 'flex-direction',
  'align-items', 'justify-content', 'color', 'background-color', 'background-image', 'border', 'border-radius', 'box-shadow',
  'outline', 'outline-offset', 'opacity', 'font-family', 'font-size', 'font-weight', 'line-height', 'letter-spacing',
  'text-align', 'white-space', 'overflow', 'transform', 'z-index', 'visibility', 'min-height', 'max-width'];

if (process.argv[2] === '--diff') {
  const [a, b] = [process.argv[3], process.argv[4]].map(f => JSON.parse(readFileSync(f, 'utf8')));
  let diffs = 0;
  for (const key of Object.keys(a)) {
    const A = a[key], B = b[key] || [];
    if (A.length !== B.length) { console.log(`${key}: element count ${A.length} -> ${B.length}`); diffs++; continue; }
    A.forEach((e, i) => { for (const p of Object.keys(e.s)) if (e.s[p] !== B[i].s[p]) { if (diffs++ < 40) console.log(`${key} ${e.path}: ${p} ${e.s[p]} -> ${B[i].s[p]}`); } });
  }
  console.log(diffs ? `${diffs} differences` : 'IDENTICAL'); process.exit(diffs ? 1 : 0);
}

const out = process.argv[2] || '.preview/styles.json';
const FIX = '.preview/style-fixtures.json';
const fixtures = existsSync(FIX) ? JSON.parse(readFileSync(FIX, 'utf8')) : {};
const VIEWS = ['overview', 'services', 'ai', 'media', 'network', 'fleet', 'storage', 'infra', 'updates'];
const browser = await chromium.launch();
const result = {};
for (const [w, h] of [[412, 915], [1000, 800], [1440, 900]]) for (const theme of ['dark', 'light']) {
  const page = await browser.newPage({ viewport: { width: w, height: h }, isMobile: w < 600, reducedMotion: 'reduce' });
  await page.clock.install({ time: new Date('2026-10-26T20:00:00-04:00') });
  await page.route('**/api/**', async r => {
    const u = new URL(r.request().url()); const k = r.request().method() + ' ' + u.pathname + u.search;
    if (r.request().method() !== 'GET') return r.abort();
    if (!fixtures[k]) { const resp = await r.fetch(); fixtures[k] = { status: resp.status(), body: await resp.text(), type: resp.headers()['content-type'] || 'application/json' }; }
    r.fulfill({ status: fixtures[k].status, body: fixtures[k].body, contentType: fixtures[k].type });
  });
  await page.addInitScript(t => localStorage.setItem('homelab.theme.v2', JSON.stringify(t)), theme);
  for (const v of VIEWS) {
    await page.goto('http://127.0.0.1:8799/#/' + v); await page.clock.runFor(3000); await page.waitForTimeout(300);
    // Interactive states: force :hover / :active / :focus-visible on the first few of each control type.
    const cdp = await page.context().newCDPSession(page); await cdp.send('DOM.enable'); await cdp.send('CSS.enable');
    const { root } = await cdp.send('DOM.getDocument', { depth: -1 });
    for (const sel of ['.m3-btn', '.segments button', '.m3-icon-btn', '.issue-chip', '.m3-chip', '.service-card', '.m3-xrail__item', '.m3-nav-bar__item', '.pin-button']) {
      const { nodeIds } = await cdp.send('DOM.querySelectorAll', { nodeId: root.nodeId, selector: sel });
      for (const [i, nodeId] of nodeIds.slice(0, 3).entries()) for (const state of ['hover', 'active', 'focus-visible']) {
        await cdp.send('CSS.forcePseudoState', { nodeId, forcedPseudoClasses: state === 'focus-visible' ? ['focus', 'focus-visible'] : [state] });
        await page.waitForTimeout(40); // let 1ms (reduced-motion) transitions finish
        const { object } = await cdp.send('DOM.resolveNode', { nodeId });
        const { result: r } = await cdp.send('Runtime.callFunctionOn', { objectId: object.objectId, returnByValue: true, arguments: [{ value: PROPS }],
          functionDeclaration: 'function(P){const o={};const b=getComputedStyle(this,"::before");for(const p of P)o[p]=getComputedStyle(this).getPropertyValue(p);o["before-opacity"]=b.opacity;o["before-bg"]=b.backgroundColor;return o}' });
        (result[`${w}-${theme}-${v}-states`] ||= []).push({ path: `${sel}[${i}]:${state}`, s: r.value });
        await cdp.send('CSS.forcePseudoState', { nodeId, forcedPseudoClasses: [] });
      }
    }
    await cdp.detach(); await page.waitForTimeout(150);
    result[`${w}-${theme}-${v}`] = await page.evaluate(PROPS => {
      const path = e => { const p = []; for (; e && e !== document.body; e = e.parentElement) p.unshift(e.tagName.toLowerCase() + (e.classList[0] ? '.' + e.classList[0] : '')); return p.join('>'); };
      return [...document.querySelectorAll('#root *')].filter(e => !e.closest('svg') || e.tagName === 'svg').map(e => {
        const cs = getComputedStyle(e), s = {}; for (const p of PROPS) s[p] = cs.getPropertyValue(p); return { path: path(e), s };
      });
    }, PROPS);
  }
  await page.close();
}
await browser.close();
writeFileSync(FIX, JSON.stringify(fixtures)); writeFileSync(out, JSON.stringify(result));
console.log(`wrote ${out}: ${Object.keys(result).length} page states, ${Object.values(result).reduce((n, a) => n + a.length, 0)} elements`);

// Rebuilds the showcase: captures fresh screenshots from the live dashboard, then prints showcase.html to the PDF.
// Usage: node showcase/build-showcase.mjs            (screenshots + PDF)
//        node showcase/build-showcase.mjs --pdf-only (PDF from the existing screenshots)
// The repo is public: the WAN address is masked before capture and the device map is never photographed.
// Every POST is aborted, so nothing on the dashboard is triggered. Motion is reduced so captures are settled frames.
// The electricity rate is private: /api/storage is rewritten without it, so Energy shows kWh only, never money.
import { chromium } from '@playwright/test';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const URL = process.env.DASHBOARD_URL || 'http://192.168.0.2/';
const pdfOnly = process.argv.includes('--pdf-only');
const browser = await chromium.launch();
const stripCost = async route => { // drop the private rate and every cost figure from the energy block
  const res = await route.fetch(); const j = await res.json();
  const strip = o => { if (o && typeof o === 'object') for (const k of Object.keys(o)) { if (/cost|rate_/.test(k)) delete o[k]; else strip(o[k]); } };
  strip(j.energy); await route.fulfill({ response: res, json: j });
};
const scrollToId = id => async p => { await p.evaluate(i => document.getElementById(i)?.scrollIntoView({ block: 'start' }), id); await p.waitForTimeout(900); };

// [file, hash, width, height, theme, prepare]
const SHOTS = [
  ['overview.png', '', 1440, 900, 'dark'],
  ['overview-light.png', '', 1440, 900, 'light'],
  ['phone.png', '', 412, 915, 'dark'],
  ['storage.png', '#/storage', 1440, 900, 'dark'],
  ['network.png', '#/network', 1440, 900, 'dark'],
  ['ai.png', '#/ai', 1440, 900, 'dark'],
  ['ai-history.png', '#/ai', 1440, 900, 'dark', async p => { await p.getByRole('heading', { name: 'Request history' }).scrollIntoViewIfNeeded(); await p.evaluate(() => scrollBy(0, 300)); await p.waitForTimeout(900); }],
  ['energy.png', '#/infra', 1440, 900, 'dark', scrollToId('infra-energy')],
  ['colours.png', '', 1440, 900, 'dark', async p => { await p.getByRole('button', { name: 'Colours' }).first().click(); await p.waitForTimeout(800); }],
  ['infra.png', '#/infra', 1440, 900, 'dark'],
  ['updates.png', '#/updates', 1440, 900, 'dark'],
  ['phone-updates.png', '#/updates', 412, 915, 'dark'],
  ['fleet.png', '#/fleet', 1440, 900, 'dark'],
  ['services.png', '#/services', 412, 915, 'dark'],
  ['phone-sheet.png', '#/storage', 412, 915, 'dark', async p => { await p.getByRole('button', { name: 'More' }).click(); await p.waitForTimeout(700); }],
];

if (!pdfOnly) for (const [file, hash, w, h, theme, prepare] of SHOTS) {
  const phone = w < 600;
  const page = await browser.newPage({ viewport: { width: w, height: h }, deviceScaleFactor: 2, isMobile: phone, hasTouch: phone, reducedMotion: 'reduce', colorScheme: theme });
  await page.route('**/*', r => r.request().method() === 'GET' ? r.continue() : r.abort());
  await page.route('**/api/storage*', stripCost);
  await page.addInitScript(t => localStorage.setItem('homelab.theme.v3', JSON.stringify(t)), theme);
  await page.goto(URL + hash); await page.waitForTimeout(3500);
  await page.evaluate(() => { // public repo: hide the WAN address wherever it appears
    for (const el of document.querySelectorAll('body *')) if (el.children.length === 0 && /\b\d{1,3}(\.\d{1,3}){3}\b/.test(el.textContent) && !/^(192\.168|10\.|127\.)/.test(el.textContent.trim().match(/\d{1,3}(\.\d{1,3}){3}/)?.[0] || '')) el.textContent = el.textContent.replace(/\b(?!192\.168)(?!10\.)\d{1,3}(\.\d{1,3}){3}\b/g, '•••.•••.•••.•••');
  });
  if (prepare) await prepare(page);
  await page.screenshot({ path: join(here, file) });
  console.log('captured', file); await page.close();
}

const page = await browser.newPage();
await page.goto('file://' + join(here, 'showcase.html'));
await page.evaluate(() => document.fonts.ready); await page.waitForTimeout(800);
await page.pdf({ path: join(here, 'Homelab_Command_Center_Showcase.pdf'), width: '297mm', height: '210mm', printBackground: true });
console.log('wrote showcase/Homelab_Command_Center_Showcase.pdf');
await browser.close();

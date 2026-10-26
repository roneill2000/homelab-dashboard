// Full-page screenshots of every view, dark and light, at phone (412px), tablet (768px) and desktop (1440px).
// Usage: node screenshots.mjs <label>     -> .preview/shots/<label>/<width>-<theme>-<view>.png
// Needs the preview running (python3 preview.py) unless DASHBOARD_URL is set.
import { chromium } from '@playwright/test';
import { mkdirSync } from 'node:fs';

const URL = process.env.DASHBOARD_URL || 'http://127.0.0.1:8799/';
const label = process.argv[2] || 'now';
const only = process.argv.slice(3);
const VIEWS = ['Overview', 'All services', 'Local AI', 'Media & downloads', 'Network & DNS', 'Hosts & containers', 'Storage', 'Infrastructure', 'Updates'];
const WIDTHS = [[412, 915, true], [768, 1024, true], [1440, 900, false]];
const dir = `.preview/shots/${label}`; mkdirSync(dir, { recursive: true });
const slug = s => s.toLowerCase().replace(/[^a-z]+/g, '-').replace(/-$/, '');
const browser = await chromium.launch({ headless: true });
let errors = 0;
for (const [w, h, mobile] of WIDTHS) for (const theme of ['dark', 'light']) {
  const page = await browser.newPage({ viewport: { width: w, height: h }, isMobile: mobile, hasTouch: mobile });
  page.on('pageerror', e => { errors++; console.log('JS ERROR', w, theme, e.message.slice(0, 200)); });
  await page.route('**/api/action/**', r => r.request().method() === 'POST' ? r.abort() : r.continue());
  await page.addInitScript(t => localStorage.setItem('homelab.theme.v2', JSON.stringify(t)), theme);
  await page.goto(URL); await page.waitForTimeout(1500);
  for (const view of VIEWS) {
    if (only.length && !only.includes(slug(view))) continue;
    // Works with the old sidebar and the new M3 navigation: open the menu if the destination is not visible.
    const target = page.getByRole('button', { name: new RegExp('^' + view.replace(/[&]/g, '\\&') + '( \\d+)?$') }).first();
    if (!(await target.isVisible().catch(() => false))) {
      const opener = page.getByRole('button', { name: 'Open navigation' }).first();
      if (await opener.isVisible().catch(() => false)) await opener.click();
    }
    await target.click({ timeout: 5000 }).catch(e => console.log('NAV FAIL', w, theme, view, e.message.split('\n')[0]));
    await page.waitForTimeout(700); await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: `${dir}/${w}-${theme}-${slug(view)}.png`, fullPage: true });
  }
  await page.close();
}
await browser.close();
console.log(`Screenshots in ${dir}${errors ? ` (${errors} JS errors)` : ''}`);

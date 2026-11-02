import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';

const hasChrome = existsSync('/opt/google/chrome/chrome');
const browser = await chromium.launch({
  headless: true,
  ...(hasChrome && !process.env.PW_NO_CHROME ? { channel: 'chrome' } : {}),
});

const context = await browser.newContext({
  viewport: { width: 412, height: 915 },
  deviceScaleFactor: 2,
});

// Route abort any external Google Fonts requests to simulate offline/no-internet
await context.route('**/fonts.g*', route => route.abort());
await context.route('**/api/action/**', route => route.abort());

const page = await context.newPage();
const requestedUrls = [];
page.on('request', req => requestedUrls.push(req.url()));

// Measure cold load at 412 px
await page.goto(process.env.DASHBOARD_URL || 'http://127.0.0.1:8799/', { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(50);

const h1 = page.getByRole('heading', { name: 'Overview', exact: true });
await h1.waitFor();

// Measure the rendered text, not the element: the heading's box also stretches when header content beside it
// loads, which is layout, not a font swap.
const textBox = () => h1.evaluate(el => { const r = document.createRange(); r.selectNodeContents(el); const b = r.getBoundingClientRect(); return { width: b.width, height: b.height }; });
const boxAt50ms = await textBox();
assert.ok(boxAt50ms.width > 0, 'h1 text must render at DOMContentLoaded+50ms');

await page.waitForTimeout(2000);
const boxAt2s = await textBox();
assert.ok(boxAt2s.width > 0, 'h1 text must render at 2s');

// Assert identical glyph / bounding box widths (no font swap)
console.log(`h1 width at 50ms: ${boxAt50ms.width}, at 2s: ${boxAt2s.width}`);
assert.equal(boxAt50ms.width, boxAt2s.width, 'h1 width must not change (zero font swap)');

// Verify no external font requests
const externalFontRequests = requestedUrls.filter(u => u.includes('fonts.googleapis.com') || u.includes('gstatic.com'));
assert.equal(externalFontRequests.length, 0, `Unexpected external font requests: ${externalFontRequests.join(', ')}`);

// Verify local fonts were requested
const localFontRequests = requestedUrls.filter(u => u.includes('/static/fonts/'));
assert.ok(localFontRequests.length > 0, 'Must request local fonts');
console.log(`Local fonts requested: ${localFontRequests.length}`);

// Verify Material Symbols icons rendered
const icons = page.locator('.material-symbols-rounded:visible');
const count = await icons.count();
assert.ok(count > 0, 'Icons must be present');
const iconBox = await icons.first().boundingBox();
assert.ok(iconBox && iconBox.width > 0 && iconBox.height > 0, 'Icon must have positive dimensions');

console.log('PASS: fonts bundled locally, no external requests, zero font swap, icons rendered');
await browser.close();

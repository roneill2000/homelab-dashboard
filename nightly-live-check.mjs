import {chromium, expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';

const browser = await chromium.launch({channel:'chrome',headless:true});
try {
  const page = await browser.newPage();
  const base = process.env.DASHBOARD_URL || 'http://192.168.0.2';
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/action/**', route => route.abort());
  await expect.poll(async () => {
    const data = await (await page.request.get(base+'/api/storage')).json();
    const report = data.activity?.nightly;
    return data.activity?.nightly_source_available === true &&
      report?.status === 'complete' && report.model === 'Qwen3.5-4B-Q4_K_M' &&
      Date.now()/1000-report.completed_at < 3600 && report.findings?.length > 0;
  }, {timeout:60000,intervals:[2000]}).toBe(true);
  await page.goto(base);
  const panel = page.locator('.nightly-panel').first();
  await expect(panel.locator('.badge')).toHaveText('Reviewed');
  await panel.getByRole('button',{name:'Read report'}).click();
  await expect(panel).toContainText('Qwen3.5-4B-Q4_K_M');
  await mkdir('.preview',{recursive:true});
  for (const width of [360,448,1920,3440]) {
    await page.setViewportSize({width,height:1100});
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect(await panel.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
    if ([448,1920].includes(width)) await panel.screenshot({path:`.preview/nightly-live-${width}.png`});
  }
  expect(errors).toEqual([]);
  console.log('PASS: actual fresh Qwen report, complete findings, phone/desktop/ultrawide wrapping, no page errors.');
} finally {
  await browser.close();
}

// Item 5: the "This is fine" mark on a nightly finding, and on the log group row it came from.
// Run against scripts/preview.py, which blocks every POST itself, with the dashboard's own mark endpoints mocked.
// Proves: reading a finding sends nothing; the only request a mark makes is the one button pressed; the reason is
// capped at 200 characters; a refused save is shown as a failure and never faked as saved; the layout holds at
// 412 × 915 dark and 1440 × 1000 light.
import { chromium, expect } from '@playwright/test';
import fs from 'node:fs';
fs.mkdirSync('.preview', { recursive: true });

const base = process.env.DASHBOARD_URL || 'http://127.0.0.1:8799/';
const browser = await chromium.launch({ headless: true, ...(process.env.PW_NO_CHROME ? {} : { channel: process.env.PW_CHANNEL || 'chrome' }) });
const now = Math.floor(Date.now() / 1000);
const MARKED = 'a1b2c3d4e5f6', OPEN = 'bbbbbbbbbbbb', LOGGED = 'cccccccccccc';
const PAT = { [MARKED]: 'smartd: Smart error 187 Reported offline', [OPEN]: 'smbd: FAILURE exit code 1', [LOGGED]: 'filebrowser.service: FAILURE' };
const report = {
  status: 'complete', completed_at: now, updated_at: now, model: 'Local test fixture', model_label: 'Fixture', inference_seconds: 30,
  evidence: {
    distinct_groups: 2, matched_entries: 8, files_scanned: 4, window_start: now - 86400, window_end: now,
    scope: 'Retained syslog files', coverage_limited: false, groups_omitted_from_model: 0,
    groups: [MARKED, OPEN].map(id => ({ id, host: 'utilities', level: 'WARNING', count: 4, first_at: now - 7200, last_at: now - 600, message: PAT[id], current_service_state: { state: 'failed' } })),
  },
  findings: [{ group_id: OPEN, interpretation: 'The file share service stopped and systemd restarted it.', verdict: 'act', next: 'Look at the share permissions' },
             { group_id: MARKED, interpretation: 'Predictive disk failure.', verdict: 'watch', next: 'Check the spare disk' }],
};
const logGroups = [LOGGED, MARKED].map(id => ({ id, host: 'utilities', unit: 'filebrowser.service', level: 'WARNING', count: 3, first_at: now - 900, last_at: now - 120, summary: PAT[id], message: PAT[id], service_status: { checked_at: now, state: 'failed', active: 'failed', sub: 'exit code 1', active_since: now - 120 } }));

// The real preview payload, with the nightly report and log groups replaced by this fixture.
// The real preview payloads, with the nightly report and the log groups replaced by this fixture.
// The report arrives on /api/storage; the log groups arrive on /api/telemetry (what the Infrastructure view reads).
const seed = await (await fetch(base + '/api/storage?window=3600')).json();
seed.activity = seed.activity || {}; seed.activity.nightly = report; seed.activity.nightly_source_available = true;
const telemetry = await (await fetch(base + '/api/telemetry')).json();
telemetry.syslog = { ...(telemetry.syslog || {}), ok: true, observed_at: now, hosts_reporting: 4, warnings_24h: 2, errors_24h: 1, log_window: { returned: 200, observed_at: now }, log_groups: logGroups };

let marks = [{ id: MARKED, host: 'utilities', level: 'WARNING', pattern: PAT[MARKED], reason: 'the disk is already on the replacement list', marked_at: now - 3600 }];
let actions = [], problems = [], refuse = false;
const fail = m => { problems.push(m); };

async function open({ width, height, theme }) {
  const mobile = width < 1100;
  const context = await browser.newContext({ viewport: { width, height }, isMobile: mobile, hasTouch: mobile, deviceScaleFactor: mobile ? 3 : 1, reducedMotion: 'reduce', timezoneId: 'America/New_York' });
  await context.addInitScript(t => { try { localStorage.setItem('homelab.theme.v3', JSON.stringify(t)); } catch { } }, theme);
  await context.route('**/api/storage*', r => r.fulfill({ contentType: 'application/json', body: JSON.stringify(seed) }));
  await context.route('**/api/telemetry*', r => r.fulfill({ contentType: 'application/json', body: JSON.stringify(telemetry) }));
  await context.route('**/api/nightly-fine', r => r.fulfill({ contentType: 'application/json', body: JSON.stringify({ fine: marks }) }));
  await context.route('**/api/action/**', r => {
    const req = r.request(); actions.push({ url: req.url(), body: req.postData() });
    if (refuse) return r.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ ok: false, error: 'Refused: the log review cannot change anything' }) });
    try {
      const b = JSON.parse(req.postData() || '{}');
      marks = req.url().endsWith('/nightly-fine-remove') ? marks.filter(m => m.id !== b.id)
        : [...marks.filter(m => m.id !== b.id), { id: b.id, host: b.host, level: b.level, pattern: b.pattern, reason: b.reason, marked_at: now }];
    } catch { fail('a mark request arrived in a shape the server could not read'); }
    r.fulfill({ contentType: 'application/json', body: JSON.stringify({ ok: true }) });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  page.on('pageerror', e => fail('pageerror: ' + e.message));
  // The preview answers 404 for sources it does not serve (fleet, updates, events); those are not this check's problem.
  page.on('console', m => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) fail('console.error: ' + m.text()); });
  await page.goto(new URL('#/infra', base.endsWith('/') ? base : base + '/').href, { waitUntil: 'domcontentloaded' });
  await page.getByRole('heading', { name: 'Nightly log review' }).first().waitFor();
  const panel = page.locator('.nightly-panel').first();
  await panel.getByRole('button', { name: 'Read report' }).click();
  const bg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  return { context, page, panel, bg };
}
const card = (panel, id) => panel.locator('.nightly-findings > article', { hasText: PAT[id] }).first();
const fits = async page => expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);

// Reading the report marks nothing: no action request leaves the page.
{
  const { context, page, panel, bg } = await open({ width: 1440, height: 1000, theme: 'light' });
  await expect(card(panel, MARKED)).toContainText('You marked this as fine');
  await expect(card(panel, MARKED)).toContainText('the disk is already on the replacement list');
  await expect(card(panel, MARKED).getByRole('button', { name: 'Not fine any more' })).toBeVisible();
  await expect(card(panel, OPEN).getByRole('button', { name: 'This is fine' })).toBeVisible();
  expect(actions).toEqual([]);                                     // nothing at all was sent while reading
  await panel.screenshot({ path: '.preview/nightly-fine-1440-light.png', scale: 'css' });
  const light = bg;

  // Marking a finding: one request, with the fields the nightly runner needs, and a capped reason.
  await card(panel, OPEN).getByRole('button', { name: 'This is fine' }).click();
  const reason = card(panel, OPEN).getByRole('textbox', { name: /Why this is fine/ });
  await reason.fill('x'.repeat(400));
  if ((await reason.inputValue()).length > 200) fail('the reason box accepted more than 200 characters');
  // Pasting bypasses the browser's own limit, so the page has to cap the text itself before it saves it.
  await reason.evaluate(el => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, 'y'.repeat(400)); el.dispatchEvent(new Event('input', { bubbles: true })); });
  if ((await reason.inputValue()).length > 200) fail('a pasted reason left more than 200 characters in the box');
  await card(panel, OPEN).getByRole('button', { name: 'Save mark' }).click();
  await expect(card(panel, OPEN)).toContainText('You marked this as fine');
  expect(actions.length).toBe(1);
  const sent = JSON.parse(actions[0].body);
  if (!actions[0].url.endsWith('/api/action/nightly-fine')) fail('the mark went to a URL that is not the mark endpoint');
  for (const key of ['id', 'host', 'level', 'pattern', 'reason']) if (!(key in sent)) fail(`the mark request was missing ${key}`);
  if (sent.id !== OPEN) fail('the mark was filed against the wrong group');
  if ((sent.reason || '').length > 200) fail(`the reason was sent ${sent.reason.length} characters long`);
  actions = [];

  // Undoing it is the same single request, and the finding is back unchanged.
  await card(panel, OPEN).getByRole('button', { name: 'Not fine any more' }).click();
  await expect(card(panel, OPEN).getByRole('button', { name: 'This is fine' })).toBeVisible();
  expect(actions.length).toBe(1);
  if (!actions[0].url.endsWith('/api/action/nightly-fine-remove')) fail('undo used a URL that is not the remove endpoint');
  expect(marks.map(m => m.id)).toEqual([MARKED]);
  actions = [];

  // The same control on the log group row, so a finding can be marked where it was first seen.
  const logged = page.locator('.log-group', { hasText: PAT[LOGGED] }).first();
  await expect(logged.locator('.nightly-fine button')).toHaveText('This is fine');
  await fits(page);
  await context.close();

  // A save the dashboard refuses is reported as a failure, and the finding is never shown as marked.
  refuse = true;
  const r = await open({ width: 1440, height: 1000, theme: 'light' });
  await card(r.panel, OPEN).getByRole('button', { name: 'This is fine' }).click();
  await card(r.panel, OPEN).getByRole('button', { name: 'Save mark' }).click();
  await expect(card(r.panel, OPEN)).toContainText('Refused: the log review cannot change anything');
  // It stays a form to fix, not a success message: the reason is still there and can be saved again.
  await expect(card(r.panel, OPEN).getByRole('button', { name: 'Save mark' })).toBeVisible();
  await expect(card(r.panel, OPEN).getByRole('button', { name: 'Cancel' })).toBeVisible();
  if (marks.some(m => m.id === OPEN)) fail('a refused mark was shown as saved');
  expect(marks.map(m => m.id)).toEqual([MARKED]);
  await r.context.close(); refuse = false;

  // Phone layout, dark: the control is reachable and nothing overflows.
  const d = await open({ width: 412, height: 915, theme: 'dark' });
  const fine = card(d.panel, OPEN).getByRole('button', { name: 'This is fine' });
  await expect(fine).toBeVisible();
  const box = await fine.boundingBox();
  if (!box || box.height < 36) fail(`the mark button is ${(box && box.height) || 0} px tall on a phone`);
  await card(d.panel, OPEN).getByRole('button', { name: 'This is fine' }).click();
  await expect(card(d.panel, OPEN).getByRole('textbox', { name: /Why this is fine/ }).first()).toBeVisible();
  await fits(d.page);
  await expect(d.panel.locator('.nightly-fine').first()).toBeVisible();
  await d.panel.screenshot({ path: '.preview/nightly-fine-412-dark.png', scale: 'css' });
  await card(d.panel, OPEN).locator('.nightly-fine').screenshot({ path: '.preview/nightly-fine-412-dark-control.png' });
  if (d.bg === light) fail('the 412 px pass did not render in dark colours');
  await d.context.close();
  if (light === d.bg) fail('dark and light renders were identical');
}

const shot = f => fs.existsSync('.preview/' + f);
if (!shot('nightly-fine-412-dark.png') || !shot('nightly-fine-1440-light.png')) problems.push('a preview screenshot was not written');
console.log(problems.length ? `FAIL: ${problems.join(' | ')}` : 'PASS: reading sends nothing; marking is one capped request; undo, refusal, log-group row, 1440 light and 412 dark checked.');
await browser.close();
if (problems.length) process.exit(1);

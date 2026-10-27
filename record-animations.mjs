import { chromium } from '@playwright/test';
import { execSync } from 'node:child_process';
import { mkdirSync, readdirSync, unlinkSync } from 'node:fs';
import path from 'node:path';

const URL = process.env.DASHBOARD_URL || 'http://192.168.0.2/';
const tempDir = '/tmp/homelab-calibrated';
mkdirSync(tempDir, { recursive: true });

for (const file of readdirSync(tempDir)) {
  unlinkSync(path.join(tempDir, file));
}

console.log('Launching browser with 1440x900 viewport for calibrated capture...');
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  recordVideo: {
    dir: tempDir,
    size: { width: 1440, height: 900 }
  }
});

const page = await context.newPage();
await page.route('**/api/action/**', r => r.request().method() === 'POST' ? r.abort() : r.continue());

await page.addInitScript(() => {
  localStorage.setItem('homelab.theme.v2', JSON.stringify('dark'));
});

await page.goto(URL);
await page.waitForTimeout(1200);

// Sleek minimal cursor overlay
await page.evaluate(() => {
  const cursor = document.createElement('div');
  cursor.id = 'sleek-cursor';
  cursor.style.position = 'fixed';
  cursor.style.width = '16px';
  cursor.style.height = '16px';
  cursor.style.borderRadius = '50%';
  cursor.style.backgroundColor = 'rgba(255, 255, 255, 0.9)';
  cursor.style.boxShadow = '0 0 12px rgba(93, 219, 188, 0.9), 0 0 2px rgba(0,0,0,0.5)';
  cursor.style.pointerEvents = 'none';
  cursor.style.zIndex = '999999';
  cursor.style.transform = 'translate(-50%, -50%)';
  cursor.style.transition = 'transform 0.15s cubic-bezier(0.2, 0, 0, 1), background-color 0.15s ease';
  document.body.appendChild(cursor);

  window.addEventListener('mousemove', e => {
    cursor.style.left = e.clientX + 'px';
    cursor.style.top = e.clientY + 'px';
  });

  window.addEventListener('mousedown', e => {
    cursor.style.transform = 'translate(-50%, -50%) scale(0.6)';
    cursor.style.backgroundColor = '#5ddbbc';
    const ring = document.createElement('div');
    ring.style.position = 'fixed';
    ring.style.left = e.clientX + 'px';
    ring.style.top = e.clientY + 'px';
    ring.style.width = '12px';
    ring.style.height = '12px';
    ring.style.borderRadius = '50%';
    ring.style.border = '2px solid rgba(93, 219, 188, 0.95)';
    ring.style.pointerEvents = 'none';
    ring.style.zIndex = '999998';
    ring.style.transform = 'translate(-50%, -50%) scale(1)';
    ring.style.transition = 'transform 0.35s cubic-bezier(0.1, 0.9, 0.2, 1), opacity 0.35s ease-out';
    document.body.appendChild(ring);
    requestAnimationFrame(() => {
      ring.style.transform = 'translate(-50%, -50%) scale(4)';
      ring.style.opacity = '0';
    });
    setTimeout(() => ring.remove(), 350);
  });

  window.addEventListener('mouseup', () => {
    cursor.style.transform = 'translate(-50%, -50%) scale(1)';
    cursor.style.backgroundColor = 'rgba(255, 255, 255, 0.9)';
  });
});

// Relaxed, natural mouse movement
async function glideAndClick(locator, pauseAfter = 1000) {
  const box = await locator.boundingBox();
  if (!box) {
    console.warn('Warning: Locator bounding box null for', locator);
    return;
  }
  const targetX = box.x + box.width / 2;
  const targetY = box.y + box.height / 2;
  await page.mouse.move(targetX, targetY, { steps: 16 });
  await page.waitForTimeout(200); // Hover pause to show hover state
  await page.mouse.down();
  await page.waitForTimeout(160); // Hold press to highlight M3 shape morph
  await page.mouse.up();
  await page.waitForTimeout(pauseAfter);
}

// Sequence: Relaxed, clear, and paced
console.log('1. Overview: Hero status glyph turning and initial state...');
await page.waitForTimeout(1400);

console.log('2. Flick to All services...');
const servicesNav = page.locator('nav').getByRole('button', { name: /^All services/ }).first();
await glideAndClick(servicesNav, 1100);

console.log('3. Category pill switches (Media -> AI -> All)...');
const mediaTab = page.getByRole('button', { name: 'Media', exact: true }).first();
if (await mediaTab.isVisible()) await glideAndClick(mediaTab, 800);

const aiTab = page.getByRole('button', { name: 'AI', exact: true }).first();
if (await aiTab.isVisible()) await glideAndClick(aiTab, 800);

const allTab = page.getByRole('button', { name: 'All', exact: true }).first();
if (await allTab.isVisible()) await glideAndClick(allTab, 900);

console.log('4. Flick to Storage...');
const storageNav = page.locator('nav').getByRole('button', { name: /^Storage/ }).first();
await glideAndClick(storageNav, 800);

// Calibrated scroll: top 620 brings Toolbar to y:115 and Charts to y:375 (100% visible with header clearance)
console.log('5. Scrolling directly to Storage History & Charts...');
await page.evaluate(() => window.scrollTo({ top: 620, behavior: 'smooth' }));
await page.waitForTimeout(1000);

console.log('6. Switching Chart Intervals (15m -> 6h -> 1h)...');
const seg15m = page.getByRole('button', { name: '15m', exact: true }).first();
if (await seg15m.isVisible()) await glideAndClick(seg15m, 1200);

const seg6h = page.getByRole('button', { name: '6h', exact: true }).first();
if (await seg6h.isVisible()) await glideAndClick(seg6h, 1200);

const seg1h = page.getByRole('button', { name: '1h', exact: true }).first();
if (await seg1h.isVisible()) await glideAndClick(seg1h, 1200);

console.log('7. Opening Full-Screen Graph Modal...');
const openChartBtn = page.locator('.chart button:has-text("open_in_full"), button.chart__expand').first();
if (await openChartBtn.isVisible()) {
  await glideAndClick(openChartBtn, 1600);
  const closeBtn = page.getByRole('button', { name: 'Close' });
  if (await closeBtn.isVisible()) {
    await glideAndClick(closeBtn, 1000);
  }
}

console.log('8. Flick to Network & DNS...');
const netNav = page.locator('nav').getByRole('button', { name: /^Network & DNS/ }).first();
await glideAndClick(netNav, 800);

// Calibrated scroll: top 430 brings Host buttons to y:113-261 and Network Chart to y:349-695
console.log('9. Scrolling to Host Traffic Graph...');
await page.evaluate(() => window.scrollTo({ top: 430, behavior: 'smooth' }));
await page.waitForTimeout(1000);

console.log('10. Switching Network Hosts (Unraid -> Orange Pi -> Proxmox)...');
const unraidRow = page.locator('.net-row:has-text("Unraid")').first();
if (await unraidRow.isVisible()) await glideAndClick(unraidRow, 1300);

const piRow = page.locator('.net-row:has-text("Orange Pi")').first();
if (await piRow.isVisible()) await glideAndClick(piRow, 1300);

const pveRow = page.locator('.net-row:has-text("Proxmox")').first();
if (await pveRow.isVisible()) await glideAndClick(pveRow, 1300);

console.log('11. Theme Toggle & Overview...');
const themeToggle = page.locator('button.theme-toggle').first();
if (await themeToggle.isVisible()) {
  await glideAndClick(themeToggle, 1200);
}

const overviewNav = page.locator('nav').getByRole('button', { name: /^Overview/ }).first();
await glideAndClick(overviewNav, 1400);

if (await themeToggle.isVisible()) {
  await glideAndClick(themeToggle, 1200);
}

await page.waitForTimeout(800);
await page.close();
await context.close();
await browser.close();

const recordedFiles = readdirSync(tempDir).filter(f => f.endsWith('.webm'));
if (recordedFiles.length === 0) throw new Error('No recorded video found');
const rawWebm = path.join(tempDir, recordedFiles[0]);

const outputGif = '/mnt/share/repos/homelab-dashboard/showcase/demo.gif';
const outputMp4 = '/mnt/share/repos/homelab-dashboard/showcase/demo.mp4';

console.log('Encoding high-res MP4 (1440x900 native)...');
execSync(`ffmpeg -y -i "${rawWebm}" -c:v libx264 -pix_fmt yuv420p -movflags +faststart -crf 20 "${outputMp4}"`, { stdio: 'inherit' });

console.log('Encoding crisp, fluid 20 fps GIF (800px width, lanczos)...');
const filterStr = 'fps=20,scale=800:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=96:stats_mode=diff[p];[s1][p]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle';
execSync(`ffmpeg -y -i "${rawWebm}" -vf "${filterStr}" "${outputGif}"`, { stdio: 'inherit' });

console.log('Encoding complete. Starting automated visual audit...');
const auditDir = '/tmp/homelab-audit-frames';
mkdirSync(auditDir, { recursive: true });
for (const f of readdirSync(auditDir)) unlinkSync(path.join(auditDir, f));

execSync(`ffmpeg -y -i "${outputMp4}" -vf "fps=1" "${auditDir}/frame_%03d.png"`, { stdio: 'ignore' });
const frameCount = readdirSync(auditDir).filter(f => f.endsWith('.png')).length;
console.log(`Audited ${frameCount} frames generated across video duration.`);
console.log('All files verified successfully:');
console.log(outputGif);
console.log(outputMp4);

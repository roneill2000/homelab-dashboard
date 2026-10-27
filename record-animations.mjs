import { chromium } from '@playwright/test';
import { execSync } from 'node:child_process';
import { mkdirSync, readdirSync, unlinkSync } from 'node:fs';
import path from 'node:path';

const URL = process.env.DASHBOARD_URL || 'http://192.168.0.2/';
const tempDir = '/tmp/homelab-graphs';
mkdirSync(tempDir, { recursive: true });

for (const file of readdirSync(tempDir)) {
  unlinkSync(path.join(tempDir, file));
}

console.log('Launching browser to capture graphs & sleek transitions...');
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1366, height: 768 },
  recordVideo: {
    dir: tempDir,
    size: { width: 1366, height: 768 }
  }
});

const page = await context.newPage();
await page.route('**/api/action/**', r => r.request().method() === 'POST' ? r.abort() : r.continue());

await page.addInitScript(() => {
  localStorage.setItem('homelab.theme.v2', JSON.stringify('dark'));
});

await page.goto(URL);
await page.waitForTimeout(1000);

// Sleek minimal cursor overlay
await page.evaluate(() => {
  const cursor = document.createElement('div');
  cursor.id = 'sleek-cursor';
  cursor.style.position = 'fixed';
  cursor.style.width = '14px';
  cursor.style.height = '14px';
  cursor.style.borderRadius = '50%';
  cursor.style.backgroundColor = 'rgba(255, 255, 255, 0.9)';
  cursor.style.boxShadow = '0 0 10px rgba(93, 219, 188, 0.8), 0 0 2px rgba(0,0,0,0.4)';
  cursor.style.pointerEvents = 'none';
  cursor.style.zIndex = '999999';
  cursor.style.transform = 'translate(-50%, -50%)';
  cursor.style.transition = 'transform 0.12s cubic-bezier(0.2, 0, 0, 1), background-color 0.12s ease';
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
    ring.style.width = '10px';
    ring.style.height = '10px';
    ring.style.borderRadius = '50%';
    ring.style.border = '2px solid rgba(93, 219, 188, 0.9)';
    ring.style.pointerEvents = 'none';
    ring.style.zIndex = '999998';
    ring.style.transform = 'translate(-50%, -50%) scale(1)';
    ring.style.transition = 'transform 0.3s cubic-bezier(0.1, 0.9, 0.2, 1), opacity 0.3s ease-out';
    document.body.appendChild(ring);
    requestAnimationFrame(() => {
      ring.style.transform = 'translate(-50%, -50%) scale(3.5)';
      ring.style.opacity = '0';
    });
    setTimeout(() => ring.remove(), 320);
  });

  window.addEventListener('mouseup', () => {
    cursor.style.transform = 'translate(-50%, -50%) scale(1)';
    cursor.style.backgroundColor = 'rgba(255, 255, 255, 0.9)';
  });
});

async function glideAndClick(locator, pauseAfter = 500) {
  const box = await locator.boundingBox();
  if (!box) return;
  const targetX = box.x + box.width / 2;
  const targetY = box.y + box.height / 2;
  await page.mouse.move(targetX, targetY, { steps: 8 });
  await page.waitForTimeout(60);
  await page.mouse.down();
  await page.waitForTimeout(90);
  await page.mouse.up();
  await page.waitForTimeout(pauseAfter);
}

console.log('1. Overview hero ready');
await page.waitForTimeout(800);

console.log('2. Flick to All services');
const servicesNav = page.locator('nav').getByRole('button', { name: /^All services/ }).first();
await glideAndClick(servicesNav, 650);

const mediaTab = page.getByRole('button', { name: 'Media', exact: true }).first();
if (await mediaTab.isVisible()) await glideAndClick(mediaTab, 400);

const aiTab = page.getByRole('button', { name: 'AI', exact: true }).first();
if (await aiTab.isVisible()) await glideAndClick(aiTab, 400);

const allTab = page.getByRole('button', { name: 'All', exact: true }).first();
if (await allTab.isVisible()) await glideAndClick(allTab, 500);

console.log('3. Flick to Storage & Center Graphs');
const storageNav = page.locator('nav').getByRole('button', { name: /^Storage/ }).first();
await glideAndClick(storageNav, 500);

// Smoothly position the telemetry charts front and center
await page.locator('.chart').first().evaluate(el => el.scrollIntoView({ behavior: 'smooth', block: 'center' }));
await page.waitForTimeout(700);

console.log('4. Interactive chart interval switching (15m -> 6h -> 1h)');
const seg15m = page.getByRole('button', { name: '15m', exact: true }).first();
if (await seg15m.isVisible()) await glideAndClick(seg15m, 600);

const seg6h = page.getByRole('button', { name: '6h', exact: true }).first();
if (await seg6h.isVisible()) await glideAndClick(seg6h, 600);

const seg1h = page.getByRole('button', { name: '1h', exact: true }).first();
if (await seg1h.isVisible()) await glideAndClick(seg1h, 600);

console.log('5. Full-Screen Graph Expand Modal (open_in_full)');
const openChartBtn = page.locator('.chart button:has-text("open_in_full"), button.chart__expand').first();
if (await openChartBtn.isVisible()) {
  await glideAndClick(openChartBtn, 900);
  // Hover over the modal graph
  const dialog = page.locator('.m3-dialog, [role="dialog"]').first();
  if (await dialog.isVisible()) {
    const closeBtn = page.getByRole('button', { name: 'Close' });
    await glideAndClick(closeBtn, 600);
  }
}

console.log('6. Flick to Network & DNS (Host Traffic Graph)');
const netNav = page.locator('nav').getByRole('button', { name: /^Network & DNS/ }).first();
await glideAndClick(netNav, 500);

// Position network traffic chart center
await page.locator('.net-card.net-history, .chart').first().evaluate(el => el.scrollIntoView({ behavior: 'smooth', block: 'center' }));
await page.waitForTimeout(600);

console.log('7. Switch Network Host Graphs (Unraid -> Orange Pi -> Proxmox)');
const unraidRow = page.locator('.net-row:has-text("Unraid")').first();
if (await unraidRow.isVisible()) await glideAndClick(unraidRow, 650);

const piRow = page.locator('.net-row:has-text("Orange Pi")').first();
if (await piRow.isVisible()) await glideAndClick(piRow, 650);

const pveRow = page.locator('.net-row:has-text("Proxmox")').first();
if (await pveRow.isVisible()) await glideAndClick(pveRow, 650);

console.log('8. Theme Toggle & Back to Overview');
const themeToggle = page.locator('button.theme-toggle').first();
if (await themeToggle.isVisible()) {
  await glideAndClick(themeToggle, 700);
}

const overviewNav = page.locator('nav').getByRole('button', { name: /^Overview/ }).first();
await glideAndClick(overviewNav, 800);

if (await themeToggle.isVisible()) {
  await glideAndClick(themeToggle, 700);
}

await page.waitForTimeout(600);
await page.close();
await context.close();
await browser.close();

const recordedFiles = readdirSync(tempDir).filter(f => f.endsWith('.webm'));
if (recordedFiles.length === 0) throw new Error('No recorded video found');
const rawWebm = path.join(tempDir, recordedFiles[0]);

const outputGif = '/mnt/share/repos/homelab-dashboard/showcase/demo.gif';
const outputMp4 = '/mnt/share/repos/homelab-dashboard/showcase/demo.mp4';

console.log('Encoding high-res MP4...');
execSync(`ffmpeg -y -i "${rawWebm}" -c:v libx264 -pix_fmt yuv420p -movflags +faststart -crf 20 "${outputMp4}"`, { stdio: 'inherit' });

console.log('Encoding crisp, vibrant GIF with lanczos palette...');
const filterStr = 'fps=14,scale=880:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=128:stats_mode=diff[p];[s1][p]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle';
execSync(`ffmpeg -y -i "${rawWebm}" -vf "${filterStr}" "${outputGif}"`, { stdio: 'inherit' });

console.log('Done! Created:');
console.log(outputGif);
console.log(outputMp4);

import { chromium } from '@playwright/test';
import { execSync } from 'node:child_process';
import { mkdirSync, readdirSync, unlinkSync } from 'node:fs';
import path from 'node:path';

const URL = process.env.DASHBOARD_URL || 'http://192.168.0.2/';
const tempDir = '/tmp/homelab-animations';
mkdirSync(tempDir, { recursive: true });

for (const file of readdirSync(tempDir)) {
  unlinkSync(path.join(tempDir, file));
}

console.log('Launching browser to capture fancy animations...');
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1280, height: 780 },
  recordVideo: {
    dir: tempDir,
    size: { width: 1280, height: 780 }
  }
});

const page = await context.newPage();
await page.route('**/api/action/**', r => r.request().method() === 'POST' ? r.abort() : r.continue());

await page.goto(URL);
await page.waitForTimeout(1000);

// Helper for visible cursor and click ripple
await page.evaluate(() => {
  const cursor = document.createElement('div');
  cursor.id = 'demo-cursor';
  cursor.style.position = 'fixed';
  cursor.style.width = '24px';
  cursor.style.height = '24px';
  cursor.style.borderRadius = '50%';
  cursor.style.backgroundColor = 'rgba(93, 219, 188, 0.4)';
  cursor.style.border = '2px solid #5ddbbc';
  cursor.style.pointerEvents = 'none';
  cursor.style.zIndex = '999999';
  cursor.style.transform = 'translate(-50%, -50%)';
  cursor.style.transition = 'transform 0.15s cubic-bezier(0.2, 0, 0, 1), background-color 0.15s ease';
  cursor.style.boxShadow = '0 0 12px rgba(93, 219, 188, 0.6)';
  document.body.appendChild(cursor);

  window.addEventListener('mousemove', e => {
    cursor.style.left = e.clientX + 'px';
    cursor.style.top = e.clientY + 'px';
  });

  window.addEventListener('mousedown', e => {
    cursor.style.transform = 'translate(-50%, -50%) scale(0.65)';
    cursor.style.backgroundColor = 'rgba(93, 219, 188, 0.9)';
    const ripple = document.createElement('div');
    ripple.style.position = 'fixed';
    ripple.style.left = e.clientX + 'px';
    ripple.style.top = e.clientY + 'px';
    ripple.style.width = '14px';
    ripple.style.height = '14px';
    ripple.style.borderRadius = '50%';
    ripple.style.border = '2px solid #5ddbbc';
    ripple.style.pointerEvents = 'none';
    ripple.style.zIndex = '999998';
    ripple.style.transform = 'translate(-50%, -50%) scale(1)';
    ripple.style.transition = 'transform 0.35s cubic-bezier(0.2, 0, 0, 1), opacity 0.35s ease-out';
    document.body.appendChild(ripple);
    requestAnimationFrame(() => {
      ripple.style.transform = 'translate(-50%, -50%) scale(4.5)';
      ripple.style.opacity = '0';
    });
    setTimeout(() => ripple.remove(), 400);
  });

  window.addEventListener('mouseup', () => {
    cursor.style.transform = 'translate(-50%, -50%) scale(1)';
    cursor.style.backgroundColor = 'rgba(93, 219, 188, 0.4)';
  });
});

// Smooth click helper with humanized movement
async function clickElement(locator, delayAfter = 600) {
  const box = await locator.boundingBox();
  if (!box) return;
  const targetX = box.x + box.width / 2;
  const targetY = box.y + box.height / 2;
  await page.mouse.move(targetX, targetY, { steps: 12 });
  await page.waitForTimeout(100);
  await page.mouse.down();
  await page.waitForTimeout(140); // hold press to highlight M3 shape morph
  await page.mouse.up();
  await page.waitForTimeout(delayAfter);
}

console.log('1. Overview hero & refresh button animation...');
const refreshBtn = page.getByRole('button', { name: 'Refresh telemetry' }).first();
await clickElement(refreshBtn, 700);

console.log('2. Flicking to All services (page slide transition & stagger arrive)...');
const servicesNav = page.locator('nav').getByRole('button', { name: /^All services/ }).first();
await clickElement(servicesNav, 900);

console.log('3. Pressing segmented filter buttons (Media, AI, Tools, All)...');
const mediaTab = page.getByRole('button', { name: 'Media', exact: true }).first();
if (await mediaTab.isVisible()) await clickElement(mediaTab, 500);

const aiTab = page.getByRole('button', { name: 'AI', exact: true }).first();
if (await aiTab.isVisible()) await clickElement(aiTab, 500);

const toolsTab = page.getByRole('button', { name: 'Tools', exact: true }).first();
if (await toolsTab.isVisible()) await clickElement(toolsTab, 500);

const allTab = page.getByRole('button', { name: 'All', exact: true }).first();
if (await allTab.isVisible()) await clickElement(allTab, 600);

console.log('4. Flicking to Storage view...');
const storageNav = page.locator('nav').getByRole('button', { name: /^Storage/ }).first();
await clickElement(storageNav, 800);

console.log('5. Pressing Storage time segments (15m, 6h, 1h)...');
const seg15m = page.getByRole('button', { name: '15m', exact: true }).first();
if (await seg15m.isVisible()) await clickElement(seg15m, 450);

const seg6h = page.getByRole('button', { name: '6h', exact: true }).first();
if (await seg6h.isVisible()) await clickElement(seg6h, 450);

const seg1h = page.getByRole('button', { name: '1h', exact: true }).first();
if (await seg1h.isVisible()) await clickElement(seg1h, 500);

console.log('6. Flicking to Local AI view...');
const aiNav = page.locator('nav').getByRole('button', { name: /^Local AI/ }).first();
await clickElement(aiNav, 800);

console.log('7. Pressing Dark / Light theme toggle (90deg icon spin + palette transition)...');
const themeToggle = page.locator('button.theme-toggle').first();
if (await themeToggle.isVisible()) {
  await clickElement(themeToggle, 800);
  await clickElement(themeToggle, 800);
}

console.log('8. Toggling Navigation Rail collapse/expand...');
const collapseNav = page.getByRole('button', { name: /Collapse navigation|Open navigation/ }).first();
if (await collapseNav.isVisible()) {
  await clickElement(collapseNav, 700);
  const openNav = page.getByRole('button', { name: /Open navigation|Expand navigation/ }).first();
  if (await openNav.isVisible()) {
    await clickElement(openNav, 700);
  }
}

console.log('9. Flicking back to Overview...');
const overviewNav = page.locator('nav').getByRole('button', { name: /^Overview/ }).first();
await clickElement(overviewNav, 1000);

await page.close();
await context.close();
await browser.close();

const recordedFiles = readdirSync(tempDir).filter(f => f.endsWith('.webm'));
if (recordedFiles.length === 0) throw new Error('No recorded video found');
const rawWebm = path.join(tempDir, recordedFiles[0]);

const outputGif = '/mnt/share/repos/homelab-dashboard/showcase/demo.gif';
const outputMp4 = '/mnt/share/repos/homelab-dashboard/showcase/demo.mp4';

console.log('Converting to MP4...');
execSync(`ffmpeg -y -i "${rawWebm}" -c:v libx264 -pix_fmt yuv420p -movflags +faststart -crf 22 "${outputMp4}"`, { stdio: 'inherit' });

console.log('Converting to punchy animated GIF...');
// 12 fps, lanczos 860px width, max 96 colors for vibrant theme colors and crisp UI text
const filterStr = 'fps=12,scale=860:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=96[p];[s1][p]paletteuse=dither=bayer:bayer_scale=3';
execSync(`ffmpeg -y -i "${rawWebm}" -vf "${filterStr}" "${outputGif}"`, { stdio: 'inherit' });

console.log('Successfully generated:');
console.log(outputGif);
console.log(outputMp4);

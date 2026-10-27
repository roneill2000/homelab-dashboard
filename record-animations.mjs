import { chromium } from '@playwright/test';
import { execSync } from 'node:child_process';
import { mkdirSync, readdirSync, unlinkSync } from 'node:fs';
import path from 'node:path';

const URL = process.env.DASHBOARD_URL || 'http://192.168.0.2/';
const tempDir = '/tmp/homelab-sleek';
mkdirSync(tempDir, { recursive: true });

for (const file of readdirSync(tempDir)) {
  unlinkSync(path.join(tempDir, file));
}

console.log('Launching browser for sleek transitions recording...');
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1280, height: 750 },
  recordVideo: {
    dir: tempDir,
    size: { width: 1280, height: 750 }
  }
});

const page = await context.newPage();
await page.route('**/api/action/**', r => r.request().method() === 'POST' ? r.abort() : r.continue());

// Lock theme to dark initially
await page.addInitScript(() => {
  localStorage.setItem('homelab.theme.v2', JSON.stringify('dark'));
});

await page.goto(URL);
await page.waitForTimeout(1000);

// Inject an ultra-sleek, minimal translucent cursor
await page.evaluate(() => {
  // Prevent any accidental page scrolling
  window.scrollTo(0, 0);
  window.addEventListener('scroll', () => window.scrollTo(0, 0));

  const cursor = document.createElement('div');
  cursor.id = 'sleek-cursor';
  cursor.style.position = 'fixed';
  cursor.style.width = '14px';
  cursor.style.height = '14px';
  cursor.style.borderRadius = '50%';
  cursor.style.backgroundColor = 'rgba(255, 255, 255, 0.85)';
  cursor.style.boxShadow = '0 0 10px rgba(93, 219, 188, 0.8), 0 0 2px rgba(0,0,0,0.5)';
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
    cursor.style.backgroundColor = 'rgba(255, 255, 255, 0.85)';
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

console.log('Step 1: Overview ready');
await page.waitForTimeout(800);

console.log('Step 2: Flick to All services');
const servicesNav = page.locator('nav').getByRole('button', { name: /^All services/ }).first();
await glideAndClick(servicesNav, 650);

console.log('Step 3: Flick category pills: Media -> AI -> Tools -> All');
const mediaTab = page.getByRole('button', { name: 'Media', exact: true }).first();
if (await mediaTab.isVisible()) await glideAndClick(mediaTab, 400);

const aiTab = page.getByRole('button', { name: 'AI', exact: true }).first();
if (await aiTab.isVisible()) await glideAndClick(aiTab, 400);

const toolsTab = page.getByRole('button', { name: 'Tools', exact: true }).first();
if (await toolsTab.isVisible()) await glideAndClick(toolsTab, 400);

const allTab = page.getByRole('button', { name: 'All', exact: true }).first();
if (await allTab.isVisible()) await glideAndClick(allTab, 500);

console.log('Step 4: Flick to Storage');
const storageNav = page.locator('nav').getByRole('button', { name: /^Storage/ }).first();
await glideAndClick(storageNav, 650);

console.log('Step 5: Storage time segment clicks');
const seg6h = page.getByRole('button', { name: '6h', exact: true }).first();
if (await seg6h.isVisible()) await glideAndClick(seg6h, 450);

const seg1h = page.getByRole('button', { name: '1h', exact: true }).first();
if (await seg1h.isVisible()) await glideAndClick(seg1h, 450);

console.log('Step 6: Flick to Local AI');
const aiNav = page.locator('nav').getByRole('button', { name: /^Local AI/ }).first();
await glideAndClick(aiNav, 700);

console.log('Step 7: Flick to Network & DNS');
const netNav = page.locator('nav').getByRole('button', { name: /^Network & DNS/ }).first();
await glideAndClick(netNav, 700);

console.log('Step 8: Flick to Infrastructure');
const infraNav = page.locator('nav').getByRole('button', { name: /^Infrastructure/ }).first();
await glideAndClick(infraNav, 700);

console.log('Step 9: Flick to Updates');
const updatesNav = page.locator('nav').getByRole('button', { name: /^Updates/ }).first();
await glideAndClick(updatesNav, 700);

console.log('Step 10: Theme toggle transition (Dark -> Light)');
const themeToggle = page.locator('button.theme-toggle').first();
if (await themeToggle.isVisible()) {
  await glideAndClick(themeToggle, 700);
}

console.log('Step 11: Flick back to Overview in light theme');
const overviewNav = page.locator('nav').getByRole('button', { name: /^Overview/ }).first();
await glideAndClick(overviewNav, 800);

console.log('Step 12: Theme toggle transition (Light -> Dark)');
if (await themeToggle.isVisible()) {
  await glideAndClick(themeToggle, 800);
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

console.log('Encoding MP4 (crisp, 60fps capable)...');
execSync(`ffmpeg -y -i "${rawWebm}" -c:v libx264 -pix_fmt yuv420p -movflags +faststart -crf 20 "${outputMp4}"`, { stdio: 'inherit' });

console.log('Encoding sleek animated GIF (14 fps, Lanczos 880px, paletteuse)...');
// 14 fps, 880px wide, lanczos scaling, diff-optimized palette
const filterStr = 'fps=14,scale=880:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=128:stats_mode=diff[p];[s1][p]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle';
execSync(`ffmpeg -y -i "${rawWebm}" -vf "${filterStr}" "${outputGif}"`, { stdio: 'inherit' });

console.log('Done! Output files:');
console.log(outputGif);
console.log(outputMp4);

import { chromium } from '@playwright/test';
import { execSync } from 'node:child_process';
import { mkdirSync, readdirSync, unlinkSync } from 'node:fs';
import path from 'node:path';

const URL = process.env.DASHBOARD_URL || 'http://192.168.0.2/';
const tempDir = '/tmp/homelab-recording';
mkdirSync(tempDir, { recursive: true });

// Clean old files in tempDir
for (const file of readdirSync(tempDir)) {
  unlinkSync(path.join(tempDir, file));
}

console.log('Launching browser to record desktop walkthrough...');
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1280, height: 800 },
  recordVideo: {
    dir: tempDir,
    size: { width: 1280, height: 800 }
  }
});

const page = await context.newPage();
// Prevent POST actions during recording
await page.route('**/api/action/**', r => r.request().method() === 'POST' ? r.abort() : r.continue());

console.log('Navigating to', URL);
await page.goto(URL);
await page.waitForTimeout(2000);

// Helper to switch view
async function goToView(viewName) {
  const name = new RegExp('^' + viewName.replace(/[&]/g, '\\&') + '( \\d+)?( \\d+ pending)?$');
  const navTarget = page.locator('nav').getByRole('button', { name }).filter({ visible: true }).first();
  if (!(await navTarget.isVisible().catch(() => false))) {
    const opener = page.getByRole('button', { name: 'Open navigation' }).first();
    if (await opener.isVisible().catch(() => false)) await opener.click();
  }
  await page.locator('nav').getByRole('button', { name }).filter({ visible: true }).first().click({ timeout: 5000 });
  await page.waitForTimeout(1800);
}

// 1. Overview: initial pause, scroll down slightly, scroll up
console.log('Recording Overview...');
await page.evaluate(() => window.scrollBy({ top: 320, behavior: 'smooth' }));
await page.waitForTimeout(1400);
await page.evaluate(() => window.scrollBy({ top: -320, behavior: 'smooth' }));
await page.waitForTimeout(1200);

// 2. All Services
console.log('Recording All services...');
await goToView('All services');
await page.evaluate(() => window.scrollBy({ top: 250, behavior: 'smooth' }));
await page.waitForTimeout(1200);
await page.evaluate(() => window.scrollBy({ top: -250, behavior: 'smooth' }));
await page.waitForTimeout(800);

// 3. Storage & Disks
console.log('Recording Storage...');
await goToView('Storage');
await page.evaluate(() => window.scrollBy({ top: 200, behavior: 'smooth' }));
await page.waitForTimeout(1200);
await page.evaluate(() => window.scrollBy({ top: -200, behavior: 'smooth' }));
await page.waitForTimeout(800);

// 4. Local AI
console.log('Recording Local AI...');
await goToView('Local AI');
await page.waitForTimeout(1800);

// 5. Back to Overview
console.log('Returning to Overview...');
await goToView('Overview');
await page.waitForTimeout(1500);

await page.close();
await context.close();
await browser.close();

// Find recorded webm file
const recordedFiles = readdirSync(tempDir).filter(f => f.endsWith('.webm'));
if (recordedFiles.length === 0) {
  throw new Error('No recorded video found');
}
const rawWebm = path.join(tempDir, recordedFiles[0]);
console.log('Recorded video at', rawWebm);

const outputGif = '/mnt/share/repos/homelab-dashboard/showcase/demo.gif';
const outputMp4 = '/mnt/share/repos/homelab-dashboard/showcase/demo.mp4';

console.log('Converting to MP4...');
execSync(`ffmpeg -y -i "${rawWebm}" -c:v libx264 -pix_fmt yuv420p -movflags +faststart -crf 23 "${outputMp4}"`, { stdio: 'inherit' });

console.log('Converting to optimized GIF...');
// Palettegen with lanczos scale to 960px width for sharp, crisp visuals and reasonable file size
const filterStr = 'fps=12,scale=900:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=96[p];[s1][p]paletteuse=dither=bayer:bayer_scale=3';
execSync(`ffmpeg -y -i "${rawWebm}" -vf "${filterStr}" "${outputGif}"`, { stdio: 'inherit' });

console.log('Done! Created:');
console.log(outputGif);
console.log(outputMp4);

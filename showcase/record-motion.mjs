// Records the README motion clips from the live dashboard: showcase/motion-desktop.{gif,mp4} and
// showcase/motion-phone.{gif,mp4}.
// Usage: node showcase/record-motion.mjs [desktop|phone]   (both when omitted; needs ffmpeg)
//
// Screen recording in a headless browser is low frame rate and low bitrate, so springs look choppy. Instead, every CSS
// animation and view transition is slowed to a tenth of its speed (CDP Animation.setPlaybackRate) and the page is
// screenshotted as fast as possible. Each frame gets a timestamp in animation time, and the frames are resampled to a
// steady 50 fps. The motion in the clips plays at real speed.
// Public repo: every non-GET request is aborted, public IPv4 addresses are masked as they render, the device map is
// never shown, and /api/storage is rewritten without the private electricity rate (Energy shows kWh, never money).
import { chromium } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const URL = process.env.DASHBOARD_URL || 'http://192.168.0.2/';
const RATE = 0.1, FPS = 50;
const only = process.argv[2];
const stripCost = async route => {
  const res = await route.fetch(); const j = await res.json();
  const strip = o => { if (o && typeof o === 'object') for (const k of Object.keys(o)) { if (/cost|rate_/.test(k)) delete o[k]; else strip(o[k]); } };
  strip(j.energy); await route.fulfill({ response: res, json: j });
};

// Runs in the page before any script: masks public IPv4 addresses and adds a cursor/touch marker that the clip shows.
function pageSetup(touch) {
  const PUBLIC = /\b(?!(?:10|127)\.)(?!192\.168\.)(?!172\.(?:1[6-9]|2\d|3[01])\.)\d{1,3}(?:\.\d{1,3}){3}\b/g;
  const mask = root => { const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT); let n; while ((n = w.nextNode())) if (PUBLIC.test(n.nodeValue)) n.nodeValue = n.nodeValue.replace(PUBLIC, '•••.•••.•••.•••'); };
  new MutationObserver(ms => ms.forEach(m => m.type === 'characterData' ? mask(m.target.parentNode || document.body) : m.addedNodes.forEach(x => mask(x.nodeType === 3 ? x.parentNode : x))))
    .observe(document, { subtree: true, childList: true, characterData: true });
  addEventListener('DOMContentLoaded', () => {
    mask(document.body);
    const c = document.createElement('div'); c.id = 'demo-cursor';
    c.style.cssText = touch
      ? 'position:fixed;left:0;top:0;width:44px;height:44px;margin:-22px 0 0 -22px;border-radius:50%;background:rgb(255 255 255 / .35);border:2px solid rgb(255 255 255 / .8);box-shadow:0 2px 10px rgb(0 0 0 / .35);pointer-events:none;z-index:2147483647;opacity:0;transform:translate(-100px,-100px) scale(.6);transition:transform .45s cubic-bezier(.2,.8,.2,1),opacity .2s;view-transition-name:demo-cursor'
      : 'position:fixed;left:0;top:0;width:18px;height:18px;margin:-9px 0 0 -9px;border-radius:50%;background:rgb(255 255 255 / .9);border:2px solid rgb(20 30 28 / .85);box-shadow:0 2px 8px rgb(0 0 0 / .4);pointer-events:none;z-index:2147483647;transform:translate(-100px,-100px);transition:transform .55s cubic-bezier(.2,.8,.2,1);view-transition-name:demo-cursor';
    document.documentElement.appendChild(c);
    const st = document.createElement('style'); st.textContent = '::view-transition-group(demo-cursor){animation:none}::view-transition-old(demo-cursor){display:none}::view-transition-new(demo-cursor){animation:none}'; document.head.appendChild(st);
    window.__cursor = (x, y, down) => { c.style.transform = `translate(${x}px,${y}px) scale(${down ? (touch ? 1 : .75) : (touch ? .6 : 1)})`; if (touch) c.style.opacity = down ? '1' : '0'; };
  });
}

async function record(name, { viewport, scale, touch, theme, script }) {
  const dir = join(tmpdir(), 'homelab-motion-' + name); rmSync(dir, { recursive: true, force: true }); mkdirSync(dir, { recursive: true });
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport, deviceScaleFactor: scale, isMobile: touch, hasTouch: touch, colorScheme: theme });
  await page.route('**/*', r => r.request().method() === 'GET' ? r.continue() : r.abort());
  await page.route('**/api/storage*', stripCost);
  await page.addInitScript(t => localStorage.setItem('homelab.theme.v3', JSON.stringify(t)), theme);
  await page.addInitScript(pageSetup, touch);
  await page.goto(URL); await page.waitForTimeout(3500);
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Animation.enable'); await cdp.send('Animation.setPlaybackRate', { playbackRate: RATE });

  const frames = []; let clock = 0, n = 0;
  const shot = async () => { const t0 = Date.now(); try { const buf = await page.screenshot({ type: 'png' }); return [t0, Date.now(), buf]; } catch { return null; } };
  const save = (t, buf) => { const f = join(dir, `${String(n++).padStart(5, '0')}.png`); writeFileSync(f, buf); frames.push([t, f]); };
  // Run `action` and capture for `ms` of animation time (ms / RATE of real time).
  const capture = async (ms, action = async () => {}) => {
    const start = Date.now(); const done = action();
    while (Date.now() - start < ms / RATE) { const s = await shot(); if (s) { const [a, b, buf] = s; save(clock + ((a + b) / 2 - start) * RATE, buf); } else { await new Promise(r => setTimeout(r, 40)); } }
    await done; clock += ms;
  };
  const hold = async ms => { const s = await shot(); if (s) save(clock, s[2]); clock += ms; };
  const center = async loc => { const b = await loc.boundingBox(); return [b.x + b.width / 2, b.y + b.height / 2]; };
  const cursor = (x, y, down = false) => page.evaluate(([x, y, d]) => window.__cursor?.(x, y, d), [x, y, down]);
  const api = {
    page, capture, hold, cursor,
    // Glide the cursor to an element, then click it (desktop) or tap it (phone).
    async moveTo(loc, ms = 600) { const [x, y] = await center(loc); await capture(ms, async () => { await cursor(x, y); if (!touch) await page.mouse.move(x, y, { steps: 8 }); }); return [x, y]; },
    async click(loc, ms) { const [x, y] = await center(loc); await capture(180, () => cursor(x, y, true)); await capture(ms, async () => { if (touch) await page.touchscreen.tap(x, y); else await page.mouse.click(x, y); await cursor(x, y, false); }); },
    // Press and hold (the Expressive press morph), then release.
    async press(loc, holdMs = 450, afterMs = 700) { const [x, y] = await center(loc); await page.mouse.move(x, y); await capture(holdMs, async () => { await cursor(x, y, true); await page.mouse.down(); }); await capture(afterMs, async () => { await page.mouse.up(); await cursor(x, y, false); }); },
    // Scroll so `loc` sits `top` px from the top, eased over `ms` of clip time (scrolling itself is not slowed by RATE).
    async scrollTo(loc, top = 96, ms = 700) {
      const dy = (await loc.boundingBox()).y - top, y0 = await page.evaluate(() => scrollY), steps = Math.round(ms / 40);
      for (let i = 1; i <= steps; i++) { const k = i / steps, e = 1 - (1 - k) ** 3; await page.evaluate(y => scrollTo(0, y), y0 + dy * e); await capture(ms / steps); }
    },
    // Mouse drag from x0 to x1 at height y (drag-to-select zoom on a chart), eased over `ms` of clip time.
    async brush(x0, x1, y, ms = 900) {
      await capture(500, async () => { await cursor(x0, y); await page.mouse.move(x0, y, { steps: 8 }); });
      await capture(160, async () => { await cursor(x0, y, true); await page.mouse.down(); });
      const steps = 16; for (let i = 1; i <= steps; i++) { const k = i / steps, x = x0 + (x1 - x0) * (1 - (1 - k) ** 2); await page.mouse.move(x, y); await cursor(x, y, true); await capture(ms / steps); }
      await capture(900, async () => { await page.mouse.up(); await cursor(x1, y, false); });
    },
    async drag(x, y0, y1, ms = 500) {
      const steps = 14; await cursor(x, y0, true); await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y: y0 }] });
      for (let i = 1; i <= steps; i++) { const y = y0 + (y1 - y0) * i / steps; await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x, y }] }); await cursor(x, y, true); await capture(ms / steps); }
      return async after => capture(after, async () => { await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] }); await cursor(x, y1, false); });
    },
  };
  await script(api);
  await browser.close();

  // Resample to a steady frame rate, then encode MP4 (H.264) and an optimised GIF.
  frames.sort((a, b) => a[0] - b[0]); const list = []; let i = 0;
  for (let t = 0; t < clock; t += 1000 / FPS) { while (i + 1 < frames.length && frames[i + 1][0] <= t) i++; list.push(`file '${frames[i][1]}'\nduration ${1 / FPS}`); }
  writeFileSync(join(dir, 'list.txt'), list.join('\n') + '\n');
  // GIFs play at the full 50 fps (the fastest a GIF can: delays are whole centiseconds and browsers slow 1 cs frames).
  // Bayer dithering compresses far better than error diffusion. GitHub embeds repo images directly (no 10 MB proxy cap),
  // so the desktop clip is 1100 px / 256 colours, ~23 MB.
  const out = join(here, 'motion-' + name), [w, gfps, colours] = name === 'phone' ? [360, FPS, 160] : [1100, FPS, 256];
  execFileSync('ffmpeg', ['-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', join(dir, 'list.txt'), '-vf', `fps=${FPS},scale=${viewport.width}:-2:flags=lanczos`, '-c:v', 'libx264', '-preset', 'slow', '-crf', '18', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', out + '.mp4']);
  execFileSync('ffmpeg', ['-v', 'error', '-y', '-i', out + '.mp4', '-vf', `fps=${gfps},scale=${w}:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=${colours}:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle`, out + '.gif']);
  console.log(`wrote showcase/motion-${name}.mp4 and .gif (${(clock / 1000).toFixed(1)} s, ${frames.length} captured frames)`);
  rmSync(dir, { recursive: true, force: true });
}

const nav = (page, label) => page.locator('nav, .m3-nav-bar').getByRole('button', { name: new RegExp('(^' + label + '|All ' + label + ')', 'i') }).filter({ visible: true }).first();

if (!only || only === 'desktop') await record('desktop', {
  viewport: { width: 1440, height: 900 }, scale: 1, touch: false, theme: 'dark',
  async script({ page, capture, hold, moveTo, click, press, scrollTo, brush }) {
    await capture(1300, () => page.reload());                       // Overview arrives: staggered cards, hero shape pops in
    await hold(900);
    await moveTo(nav(page, 'Storage')); await click(nav(page, 'Storage'), 1100);   // page change + nav indicator
    await hold(600);
    const cap = page.getByRole('button', { name: 'Capacity used' }), thr = page.getByRole('button', { name: 'Throughput' });
    await scrollTo(page.locator('.storage-toolbar'), 72, 900);      // bring the toggles and the four charts into view
    await hold(500);
    await moveTo(cap); await press(cap, 400, 1100);                 // button morphs; charts switch to % used, headline to Used / Free
    await hold(900);
    await hold(600);
    await moveTo(thr); await press(thr, 350, 1000);                 // back to read/write throughput
    await hold(600);
    await moveTo(nav(page, 'Network')); await click(nav(page, 'Network'), 1100);
    await scrollTo(page.locator('.net-section'), 72);
    const rows = page.locator('.net-row');
    await moveTo(rows.nth(1)); await click(rows.nth(1), 900);        // picked host morphs into the chart card
    await hold(500);
    await moveTo(rows.nth(0)); await click(rows.nth(0), 900);        // back to Proxmox, the busiest link
    await hold(700);
    const expand = page.locator('.net-history').getByRole('button', { name: 'Open larger chart' });
    await moveTo(page.locator('.net-history .chart')); await click(expand, 900);   // chart opens in a dialog
    await hold(900);
    // Drag-select the busiest stretch of the last hour: the axis and labels follow the zoom.
    const plot = await page.getByRole('dialog').locator('.chart svg').first().boundingBox();
    const busy = await page.evaluate(() => { // x (0..1) of the largest receive/send value in the dialog's data, if exposed
      const pts = [...document.querySelectorAll('[role=dialog] .chart svg path')].map(p => p.getAttribute('d') || '').join(' ');
      const nums = [...pts.matchAll(/(-?\d+\.?\d*)[ ,](-?\d+\.?\d*)/g)].map(m => [+m[1], +m[2]]); if (!nums.length) return null;
      const minY = Math.min(...nums.map(n => n[1])), xs = nums.map(n => n[0]), lo = Math.min(...xs), hi = Math.max(...xs);
      return hi > lo ? (nums.find(n => n[1] === minY)[0] - lo) / (hi - lo) : null;
    });
    const c = Math.min(0.9, Math.max(0.1, busy ?? 0.85)), y = plot.y + plot.height * 0.55;
    await brush(plot.x + plot.width * (c - 0.1), plot.x + plot.width * Math.min(0.99, c + 0.1), y, 1000);
    await hold(1600);
    const reset = page.getByRole('button', { name: 'Reset zoom' });
    await moveTo(reset); await click(reset, 1000);                   // springs back to the full hour
    await hold(500);
    await click(page.getByRole('button', { name: 'Close' }).last(), 700);
    await moveTo(nav(page, 'Local AI')); await click(nav(page, 'Local AI'), 1100);
    await hold(1100);                                                // loaded model card (Strata) first
    await scrollTo(page.getByRole('heading', { name: 'Request history' }), 72, 1000);   // every request through the GPU broker
    await hold(1200);
    await moveTo(nav(page, 'Infrastructure')); await click(nav(page, 'Infrastructure'), 1100);
    const jump = page.getByRole('button', { name: 'Energy' }).first();
    await moveTo(jump); await click(jump, 1000);                     // section jump to Energy (kWh only, no cost)
    await hold(1300);
    await moveTo(nav(page, 'Overview')); await click(nav(page, 'Overview'), 1100);
    await hold(500);
    const colours = page.getByRole('button', { name: 'Colours' }).first();
    await moveTo(colours); await click(colours, 900);                // Material You picker
    const ocean = page.getByRole('radio', { name: 'Ocean' }), pine = page.getByRole('radio', { name: 'Pine' });
    await moveTo(ocean); await click(ocean, 1100);                   // whole scheme regenerates from the seed (saved in this browser only)
    await hold(500);
    await moveTo(pine); await click(pine, 1000);
    await click(page.getByRole('button', { name: 'Done' }), 700);
    const toggle = page.getByRole('button', { name: /Switch to (light|dark)/ });
    await moveTo(toggle); await press(toggle, 350, 900);            // icon turns, theme switches
    await hold(1400);
  },
});

if (!only || only === 'phone') await record('phone', {
  viewport: { width: 412, height: 915 }, scale: 2, touch: true, theme: 'dark',
  async script({ page, capture, hold, moveTo, click, drag }) {
    await capture(1300, () => page.reload());
    await hold(900);
    await click(nav(page, 'Services'), 1100);
    await hold(500);
    await click(page.getByRole('button', { name: /^Media/ }).first(), 800);
    await hold(600);
    await click(nav(page, 'Local AI'), 1100);
    await hold(700);
    await click(nav(page, 'More'), 1000);                           // bottom sheet rises, destinations arrive in a stagger
    await hold(700);
    const release = await drag(206, 640, 840, 450); await release(800);   // swipe the sheet away
    await hold(400);
    await click(nav(page, 'Overview'), 1100);
    await hold(1200);
  },
});

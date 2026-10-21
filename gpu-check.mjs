import {chromium,expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
const base=process.env.DASHBOARD_URL||'http://192.168.0.2';
const browser=await chromium.launch({channel:'chrome',headless:true});
try{
 const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/action/**',r=>r.abort());await page.route('**/api/qwen',r=>r.request().method()==='POST'?r.abort():r.continue());
 await page.goto(base);await page.getByRole('button',{name:'Local AI',exact:true}).click();
 const panel=page.locator('.gpu-panel');await expect(panel).toBeVisible();await expect(panel).toContainText('Live');
 const real=await(await page.request.get(base+'/api/storage')).json();expect(real.gpu.latest.online).toBe(true);expect(real.gpu.latest.pci).toBe('0000:03:00.0');expect(real.gpu.latest.vram_total_mib).toBeGreaterThan(3000);
 await mkdir('.preview',{recursive:true});
 for(const width of [360,448,1920,3440]){
  await page.setViewportSize({width,height:1000});
  for(const metric of ['Engines','VRAM','Temperature']){
   await panel.getByRole('button',{name:metric,exact:true}).click();await expect(panel.getByRole('button',{name:metric,exact:true})).toHaveAttribute('aria-pressed','true');
   expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);expect(await panel.evaluate(e=>e.scrollWidth<=e.clientWidth)).toBe(true);
   const chart=panel.locator('.chart svg');await chart.focus();await page.keyboard.press('End');await expect(panel.locator('.chart-tooltip')).toBeVisible();
  }
  await panel.getByRole('button',{name:'Engines',exact:true}).click();await panel.screenshot({path:`.preview/gpu-${width}.png`});
 }
 await page.setViewportSize({width:1920,height:1080});
 for(const range of ['15m','1h','6h','24h']){
  const response=page.waitForResponse(r=>r.url().includes('/api/storage?window=')&&r.ok());await page.getByRole('button',{name:range,exact:true}).click();await response;
 }
 // Intercept only this browser to prove stale and partial samples do not become healthy zeros.
 let fixture=structuredClone(real);fixture.gpu.latest={...fixture.gpu.latest,online:false,observed_at:1};
 await page.route('**/api/storage?*',r=>r.fulfill({json:fixture}));
 await page.getByRole('button',{name:'Refresh telemetry'}).click();await expect(panel).toContainText('Unavailable',{timeout:15000});await expect(panel.locator('.gpu-readings')).toContainText('—');
 fixture.gpu.latest={...real.gpu.latest,observed_at:Math.floor(Date.now()/1000),compute_pct:null};
 await page.getByRole('button',{name:'Refresh telemetry'}).click();await expect(panel).toContainText('Partial readings',{timeout:15000});
 expect(errors).toEqual([]);console.log('PASS: actual A310 source, all chart modes and history ranges, 360/448/1920/3440px, keyboard inspection, stale/partial states, no overflow/errors; no inference started.');
}finally{await browser.close()}

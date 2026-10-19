import {chromium,expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
const base=process.env.DASHBOARD_URL||'http://192.168.0.2';
const browser=await chromium.launch({channel:'chrome',headless:true});
try{
 const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/action/**',r=>r.abort());await page.route('**/api/qwen',r=>r.request().method()==='POST'?r.abort():r.continue());
 await page.goto(base);await expect(page.getByRole('heading',{name:'Overview',exact:true})).toBeVisible();
 const data=await(await page.request.get(base+'/api/storage')).json();
 expect(data.ai.webui.online).toBe(true);expect(data.ai.desktop.online).toBe(true);
 expect(data.ai.desktop.models.map(m=>m.id).sort()).toEqual(['qwen2.5-coder-14b','qwen3.6-35b-moe','qwen3.8-27b']);
 expect(JSON.stringify(data.ai)).not.toMatch(/preset|api.key|C:\\\\llm|--ctx-size/);
 await page.getByRole('button',{name:'Local AI',exact:true}).click();const panel=page.locator('.local-ai-section');
 await expect(panel.locator('.ai-model')).toHaveCount(3);
 await expect(panel.getByText('Recorded benchmark',{exact:true})).toHaveCount(3);
 await expect(panel.locator('a[href*="192.168.0.12"]')).toHaveCount(0);
 await expect(panel).toContainText('32,768');await expect(panel).toContainText('16,384');
 for(const model of data.ai.desktop.models){
  const card=panel.locator('.ai-model').filter({has:page.getByRole('heading',{name:model.id,exact:true})});
  await expect(card.getByRole('link',{name:'Open chat'})).toHaveAttribute('href',`http://192.168.0.16:3000/?model=${encodeURIComponent(model.id)}`);
 }
 await mkdir('.preview',{recursive:true});
 for(const width of [360,448,1920,3440]){
  await page.setViewportSize({width,height:1100});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  for(const card of await panel.locator('.ai-model,.ai-host').all())expect(await card.evaluate(e=>e.scrollWidth<=e.clientWidth)).toBe(true);
  await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:`.preview/local-ai-${width}.png`,fullPage:true});
 }
 await page.setViewportSize({width:1920,height:1100});await page.getByRole('button',{name:'All services 27',exact:true}).click();
 await page.getByRole('textbox',{name:'Filter services',exact:true}).fill('Open WebUI');await expect(page.locator('.service-card')).toHaveCount(1);
 await expect(page.locator('.service-card .service-link')).toHaveAttribute('href','http://192.168.0.16:3000');
 await expect(page.locator('.service-card').getByRole('img',{name:'Online',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Find a service'}).click();await page.getByRole('textbox',{name:'Search service names and IPs'}).fill('192.168.0.16');
 await expect(page.locator('.command-results a')).toHaveCount(1);await page.keyboard.press('Escape');
 await page.getByRole('button',{name:'Local AI',exact:true}).click();
 const broken=structuredClone(data);broken.ai.desktop={...broken.ai.desktop,online:false,error:'Source unavailable'};
 await page.route('**/api/storage?*',r=>r.fulfill({json:broken}));await page.getByRole('button',{name:'Refresh telemetry'}).click();
 await expect(panel.getByText('Unverified',{exact:true})).toHaveCount(3,{timeout:15000});await expect(panel).toContainText('last seen');
 expect(errors).toEqual([]);console.log('PASS: live Open WebUI and three desktop models, configured context, model links, service/search integration, stale states, 360/448/1920/3440px; no inference or actions.');
}finally{await browser.close()}

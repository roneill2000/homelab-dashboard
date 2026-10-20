import {chromium,expect} from '@playwright/test';
import {mkdir} from 'node:fs/promises';
const base=process.env.DASHBOARD_URL||'http://192.168.0.2';
const browser=await chromium.launch({channel:'chrome',headless:true});
try{
 const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/action/**',r=>r.abort());await page.route('**/api/qwen',r=>r.request().method()==='POST'?r.abort():r.continue());
 const data=await(await page.request.get(base+'/api/storage')).json();
 const now=Date.now()/1000;
 data.activity.latest.ts=now;data.activity.recovery.backup={observed_at:now,running:false,latest:{status:'OK',finished_at:now-100},last_success:{status:'OK',finished_at:now-100},coverage:{status:'checked',covered:8,total:13,uncovered:[{vmid:103,name:'kasm',status:'running'},{vmid:100,name:'Kali',status:'stopped'},{vmid:101,name:'ubuntu',status:'stopped'},{vmid:109,name:'VM 109',status:'stopped'},{vmid:110,name:'Windows',status:'stopped'}]}};
 await page.route('**/api/storage?*',r=>r.fulfill({json:data}));
 await page.goto(base);
 const card=page.locator('.recovery-card').filter({hasText:'Proxmox backup jobs'});
 await expect(card).toContainText('8 / 13 guests');await expect(card.locator('[role=alert]')).toContainText('kasm · 103 · running');
 await expect(card).toContainText('Kali · 100 · stopped');
 await mkdir('.preview',{recursive:true});
 for(const width of [360,448,1920,3440]){
  await page.setViewportSize({width,height:1000});expect(await card.evaluate(e=>e.scrollWidth<=e.clientWidth)).toBe(true);await card.screenshot({path:`.preview/coverage-${width}.png`});
 }
 data.activity.recovery.backup.coverage={status:'unknown'};await page.getByRole('button',{name:'Refresh telemetry'}).click();
 await expect(card).toContainText('Backup coverage unknown');await expect(card.locator('[role=alert]')).toHaveCount(0);
 data.activity.recovery.backup.coverage={status:'checked',covered:13,total:13,uncovered:[]};await page.getByRole('button',{name:'Refresh telemetry'}).click();
 await expect(card).toContainText('13 / 13 guests');await expect(card.locator('[role=alert]')).toHaveCount(0);
 data.activity.recovery.backup.observed_at=now-1000;await page.getByRole('button',{name:'Refresh telemetry'}).click();
 await expect(card).toContainText('Backup coverage unknown');await expect(card).not.toContainText('13 / 13 guests');
 expect(errors).toEqual([]);console.log('PASS coverage alerts, uncovered names, unknown/stale/all-covered states and 360/448/1920/3440px; no mutations.');
}finally{await browser.close()}

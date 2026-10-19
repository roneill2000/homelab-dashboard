import {chromium,expect} from '@playwright/test';
const browser=await chromium.launch({channel:'chrome',headless:true});
const page=await browser.newPage();const base=process.env.DASHBOARD_URL||'http://192.168.0.2';
let data;
await expect.poll(async()=>{data=await (await page.request.get(base+'/api/telemetry')).json();return data.syslog?.last_alert_full;},{timeout:30000}).toBe(true);
const actual=data.syslog.latest_event.msg;
await page.route('**/api/action/**',r=>r.abort());
await page.goto(base);await expect(page.locator('.log-message')).toHaveText(actual);
expect(actual.endsWith('...')).toBe(false);
const now=Math.floor(Date.now()/1000),long='systemd: '+('long-log-field/'.repeat(100))+' status=1/FAILURE';
data.syslog={...data.syslog,online:true,error:null,last_success_at:now,last_attempt_at:now,warnings_24h:1,last_alert:long,last_alert_full:true,latest_event:{msg:long,ts:now-3600,host:'utilities',level:'WARNING'}};
data.updated_at=now;
await page.route('**/api/telemetry',r=>r.fulfill({contentType:'application/json',body:JSON.stringify(data)}));
for(const width of [360,448,1920]){
 await page.setViewportSize({width,height:1000});await page.reload();
 const button=page.getByRole('button',{name:'Show full entry'});await expect(button).toBeVisible();
 await button.focus();await page.keyboard.press('Enter');await expect(page.locator('.log-message')).toHaveText(long);
 expect(await page.locator('.log-notice').evaluate(e=>e.scrollWidth<=e.clientWidth)).toBe(true);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await expect(page.getByRole('button',{name:'Show less'})).toHaveAttribute('aria-expanded','true');
 await page.locator('.log-notice').screenshot({path:`.preview/log-entry-${width}.png`});
 await page.getByRole('button',{name:'Show less'}).click();await expect(page.getByRole('button',{name:'Show full entry'})).toHaveAttribute('aria-expanded','false');
}
console.log('PASS: live complete log entry, keyboard expand/collapse, 360/448/1920px long-line wrapping without overflow.');
await browser.close();

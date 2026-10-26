import {chromium,expect} from '@playwright/test';
const b=await chromium.launch({...(process.env.PW_NO_CHROME?{}:{channel:'chrome'}),headless:true});const p=await b.newPage();const base=process.env.DASHBOARD_URL||'http://192.168.0.2';
await p.route('**/api/action/**',r=>r.abort());let data;
await expect.poll(async()=>{data=await(await p.request.get(base+'/api/telemetry')).json();return data.syslog?.log_groups?.length||0},{timeout:30000}).toBeGreaterThan(0);
await p.goto(base);const group=p.locator('.log-group').first();
await expect(group.locator('.badge')).toHaveText('Running again');await expect(group).toContainText('60 matching entries');
await expect(p.locator('.log-window-note')).toContainText('60 latest entries');
const latest=data.syslog.log_groups[0].message;
for(const width of [360,448,1920,3440]){
 await p.setViewportSize({width,height:1000});
 await group.locator('summary').focus();await p.keyboard.press('Enter');await expect(group.locator('.log-message')).toBeVisible();await expect(group.locator('.log-message')).toHaveText(latest);
 expect(await group.evaluate(e=>e.scrollWidth<=e.clientWidth)).toBe(true);expect(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await group.screenshot({path:`.preview/log-group-${width}.png`});await group.locator('summary').click();
}
const now=Math.floor(Date.now()/1000);data.updated_at=now;data.syslog.last_success_at=now;data.syslog.log_window.observed_at=now;
data.syslog.log_groups[0].service_status.checked_at=now-120;
await p.route('**/api/telemetry',r=>r.fulfill({contentType:'application/json',body:JSON.stringify(data)}));await p.getByRole('button',{name:'Refresh telemetry'}).click();await expect(group.locator('.badge')).toHaveText('Status unverified');
data.syslog.log_groups[0].service_status={state:'failed',checked_at:now,active:'failed',sub:'failed'};
await p.getByRole('button',{name:'Refresh telemetry'}).click();await expect(group.locator('.badge')).toHaveText('Failed now');
data.syslog.online=false;data.syslog.error='Source poll failed';data.syslog.last_success_at=now-120;await p.getByRole('button',{name:'Refresh telemetry'}).click();await expect(group).toBeVisible();await expect(group.locator('.badge')).toHaveText('Status unverified');await expect(p.locator('.log-groups .source-note')).toContainText('stale');
console.log('PASS: live grouping and recovery evidence, bounded counts, keyboard detail expansion, stale status, current failure, 360/448/1920/3440px wrapping.');await b.close();

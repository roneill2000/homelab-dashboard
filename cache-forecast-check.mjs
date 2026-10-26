import {chromium,expect} from '@playwright/test';
const b=await chromium.launch({...(process.env.PW_NO_CHROME?{}:{channel:'chrome'}),headless:true});const p=await b.newPage();
const base=process.env.DASHBOARD_URL||'http://192.168.0.2';await p.route('**/api/action/**',r=>r.abort());
const data=await(await p.request.get(base+'/api/storage')).json();
await p.goto(base);await p.getByRole('button',{name:'All disks',exact:true}).click();const card=p.locator('.storage-card').filter({has:p.getByRole('heading',{name:'Unraid cache',exact:true})}).first();
await expect(card.locator('.capacity-forecast')).toBeVisible();
for(const width of [360,448,1920,3440]){
 await p.setViewportSize({width,height:1000});await expect(card).toBeVisible();
 expect(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 expect(await card.evaluate(e=>e.scrollWidth<=e.clientWidth)).toBe(true);
 await card.screenshot({path:`.preview/cache-forecast-${width}.png`});
}
await p.route('**/api/storage*',r=>r.fulfill({contentType:'application/json',body:JSON.stringify(data)}));
const cache=data.disks.find(d=>d.id==='cache');
for(const [state,seconds,text] of [['filling',720,'~12 minutes to full'],['flat',null,'No recent space growth'],['draining',null,'Free space increasing'],['full',0,'Cache full'],['collecting',null,'Collecting fill-rate samples']]){
 cache.sampled_at=Math.floor(Date.now()/1000);cache.exhaustion={state,seconds,growth_bps:1e7,window_seconds:300};
 await p.reload();await p.getByRole('button',{name:'All disks',exact:true}).click();await expect(card.locator('.capacity-forecast strong')).toHaveText(text);
}
cache.sampled_at=1;await p.reload();await p.getByRole('button',{name:'All disks',exact:true}).click();await expect(card.locator('.capacity-forecast strong')).toHaveText('Fill estimate unavailable');
console.log('PASS: live capacity estimate, filling/flat/draining/full/collecting/stale states, 360/448/1920/3440px.');await b.close();

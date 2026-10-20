// Updates view against fixtures; every POST is captured and aborted.
import {chromium} from 'playwright';import {expect} from '@playwright/test';
const base=process.env.DASHBOARD_URL||'http://127.0.0.1:8799/';
const b=await chromium.launch({channel:process.env.PW_CHANNEL,headless:true});
const now=Math.floor(Date.now()/1000);
const items=[{id:'docker:Jellyfin',name:'Jellyfin',kind:'Docker',state:'pending',detail:'New image available'},
 {id:'lxc:103',vmid:103,name:'kasm',kind:'LXC',state:'pending',detail:'12 packages'},
 {id:'lxc:113',vmid:113,name:'memos',kind:'LXC',state:'current',detail:'Up to date'},
 {id:'docker:zurg',name:'zurg',kind:'Docker',state:'skipped',detail:'No Unraid template; update it by hand'}];
let fixture={job:{kind:'check',status:'complete',started_at:now-60,finished_at:now-30},checked_at:now-30,items};
const posts=[];
try{for(const width of [360,448,1920]){
 const p=await b.newPage({viewport:{width,height:900}});const errors=[];p.on('pageerror',e=>errors.push(e.message));
 await p.route('**/api/updates',r=>{if(r.request().method()==='POST'){posts.push([r.request().postData(),r.request().headers()['x-dashboard-action']]);return r.abort()}return r.fulfill({json:fixture})});
 await p.route('**/api/action/**',r=>r.abort());await p.route('**/api/qwen',r=>r.request().method()==='POST'?r.abort():r.continue());
 await p.goto(base);
 if(width<760)await p.getByRole('button',{name:'Open navigation'}).click();
 await p.getByRole('button',{name:'Updates',exact:true}).click();
 const panel=p.locator('.updates-panel');
 await expect(panel).toContainText('kasm');await expect(panel).toContainText('No Unraid template');
 await expect(p.getByRole('button',{name:'Update 2'})).toBeEnabled();
 expect(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await p.screenshot({path:`.preview/updates-${width}.png`,fullPage:true});
 await p.getByRole('button',{name:'Update 2'}).click();await expect(p.getByRole('dialog')).toContainText('Jellyfin, kasm');
 await p.getByRole('button',{name:'Confirm'}).click();await p.waitForTimeout(300);
 fixture={...fixture,checked_at:now-7200};await p.getByRole('button',{name:'Check for updates'}).click();await p.waitForTimeout(1500);
 await expect(p.getByRole('button',{name:'Update 2'})).toBeDisabled();await expect(panel).toContainText('over an hour old');
 fixture={job:{kind:'update',status:'running',started_at:now},checked_at:now,items:items.map((i,n)=>n===0?{...i,state:'updating',detail:'Pulling and recreating'}:i)};
 await p.reload();if(width<760)await p.getByRole('button',{name:'Open navigation'}).click();await p.getByRole('button',{name:'Updates',exact:true}).click();
 await expect(panel).toContainText('Updating one guest at a time');await expect(p.getByRole('button',{name:'Check for updates'})).toBeDisabled();
 fixture={job:{kind:'update',status:'failed',error:'apt-get upgrade exited 100',finished_at:now},checked_at:now,items:[{...items[1],state:'failed',detail:'apt-get upgrade exited 100',rollback:'Roll back: pct rollback 103 preupd_202610201300'}]};
 await p.reload();if(width<760)await p.getByRole('button',{name:'Open navigation'}).click();await p.getByRole('button',{name:'Updates',exact:true}).click();
 await expect(panel).toContainText('pct rollback 103 preupd_202610201300');await expect(panel).toContainText('Update run failed');
 expect(errors).toEqual([]);fixture={job:{kind:'check',status:'complete',started_at:now-60,finished_at:now-30},checked_at:now-30,items};await p.close();
}
expect(posts.length).toBe(6);expect(posts.every(([body,h])=>h==='1')).toBe(true);
expect(posts.filter(([b])=>b.includes('start')).length).toBe(3);
console.log('PASS updates view: pending/stale/running/failed states, confirm lists guests, header on every POST (all aborted), 360/448/1920px no overflow.');
}finally{await b.close()}

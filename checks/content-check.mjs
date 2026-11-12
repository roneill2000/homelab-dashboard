import { chromium,expect } from '@playwright/test';
const browser=await chromium.launch({...(process.env.PW_NO_CHROME?{}:{channel:'chrome'}),headless:true});
const context=await browser.newContext({viewport:{width:1440,height:1000}});
const api=await context.request.get('http://192.168.0.2/api/telemetry');
const original=await api.json();
// The preview has no /api/fleet (it 404s), so Recovery readiness renders its 'no data' note. Feed the live
// fleet reading so the panel that names what stays off-site is on screen.
const liveFleet=await(await context.request.get('http://192.168.0.2/api/fleet')).json();
let data=structuredClone(original);const now=Math.floor(Date.now()/1000);
const stamp=s=>Object.assign(s,{online:true,last_attempt_at:now,last_success_at:now,error:null});
stamp(data.pve);stamp(data.unraid);stamp(data.scrutiny);stamp(data.pihole.primary);stamp(data.pihole.secondary);stamp(data.media.sabnzbd);
data.updated_at=now;data.media.readarr=stamp({books:113,queue:8});delete data.media.radarr;
data.pve.online=false;data.pve.error='Source poll failed';data.pve.last_success_at=now-300;
data.pihole.status='mixed';data.pihole.blocking=null;data.pihole.secondary.blocking=false;
data.scrutiny.last_success_at=now-300;
data.media.sabnzbd.online=false;data.media.sabnzbd.last_success_at=now-300;data.media.sabnzbd.error='Source poll failed';
// Network map after the 11 Nov 2026 backend patch (patch_network_map_rows): the fixture is fixed here
// so the gate does not depend on whether the dashboard host has been redeployed yet.
const MAP=[['.1','Flint 3','Gateway'],['.2','Homelab Command','LXC 104'],['.3','Proxmox VE 9','Hypervisor'],['.4','Unraid NAS','Storage'],['.6','Guacamole','LXC 111'],['.8','Pi-hole DNS VIP','VRRP Floating IP'],['.10','Orange Pi Zero 2W','Replica DNS'],['.11','Pi-hole Primary','LXC 106'],['.12','Gaming PC','RTX 5070 Ti'],['.13','Utilities Stack','LXC 102'],['.14','Speedtest Tracker','LXC 105'],['.16','Open WebUI','LXC 112'],['.17','Jellyfin Server','Arc A310 br0'],['.18','Memos','LXC 113'],['.20','Caddy proxy','LXC 115'],['.21','pi','LXC 116'],['.22','Roam 6','Living-room Wi-Fi'],['.25','Minecraft Server','Unraid br0'],['.30','WireGuard Gateway','LXC 117'],['.254','Macvlan Shim','Unraid Route']];
data.ip_matrix=MAP.map(([o,n,r])=>({octet:o,ip:`192.168.0.${o.slice(1)}`,name:n,role:r,url:null,online:true}));
const p=await context.newPage();p.setDefaultTimeout(15000);
await p.route('**/api/telemetry',r=>r.fulfill({contentType:'application/json',body:JSON.stringify(data)}));
await p.route('**/api/fleet',r=>r.fulfill({contentType:'application/json',body:JSON.stringify(liveFleet)}));
await p.route('**/api/action/**',r=>r.abort());
await p.goto(process.env.DASHBOARD_URL||'http://127.0.0.1:8799/');
await expect(p.getByRole('heading',{name:'Overview',exact:true})).toBeVisible();
await expect(p.locator('.summary-stat').filter({hasText:'DNS protection'})).toContainText('Mixed');
// Overview v2 (26 Oct 2026): host cards live on Hosts & containers; same assertions there.
await p.evaluate(()=>{location.hash='#/fleet'});await p.waitForTimeout(700);
const pve=p.locator('.host-card').filter({hasText:'Proxmox'});
await expect(pve.locator('.badge')).toHaveText('Stale');
await expect(pve.locator('.host-stats')).toContainText('—');
await expect(pve.locator('.source-note')).toContainText('Last successful read');
await p.getByRole('button',{name:'Network & DNS',exact:true}).click();
await expect(p.getByRole('button',{name:/Pause DNS|Resume blocking|Resume DNS/})).toBeDisabled();
await p.getByRole('button',{name:'Media & downloads',exact:true}).click();
await expect(p.locator('.service-card').filter({hasText:'SABnzbd'}).locator('.service-dot')).toHaveAttribute('aria-label','Stale');
await expect(p.locator('.service-card').filter({hasText:'Readarr'})).toContainText('113 books · 8 queued');
await expect(p.locator('.activity-panel')).toContainText('MB/s');
await p.getByRole('button',{name:'Storage',exact:true}).click();
for(const badge of await p.locator('.drive-row .badge').all())await expect(badge).toHaveText('Unknown');
// Network map (11 Nov 2026): 20 hosts in address order, .21 between .20 and .22, no retired names.
await p.getByRole('button',{name:'Network & DNS',exact:true}).click();
expect(await p.locator('.network-map a .ip-square').allTextContents()).toEqual(MAP.map(r=>r[0]));
await expect(p.locator('.network-map a').filter({hasText:'Flint 3'})).toHaveCount(1);
await expect(p.locator('.network-map a').filter({hasText:'Roam 6'})).toHaveCount(1);
await p.getByRole('button',{name:'Infrastructure',exact:true}).click();
await expect(await p.evaluate(()=>document.body.innerText)).toContain('Not backed up off-site, by choice: the media library and the Private share.');
for(const phrase of ['GL-BE9300','Proxmox VE 8','109 and 110']){
 await expect(await p.evaluate(()=>document.body.innerText)).not.toContain(phrase);
}
console.log('PASS: stale source metrics, mixed DNS disabled, Readarr books, SAB source failure and units, stale SMART status, network map 20 rows in address order, recovery footnote wording.');
await browser.close();

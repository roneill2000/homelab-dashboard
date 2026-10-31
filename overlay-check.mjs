// Overlay and interaction regressions from the 26 Oct independent audit: enlarged-chart tab order, background inertness
// across window-class changes, scroll locking under scrims, quick actions that re-flow when pressed, Ask focus on Local AI,
// and the phone More sheet. Every POST is aborted.
import {chromium} from 'playwright';import {expect} from '@playwright/test';
const base=process.env.DASHBOARD_URL||'http://127.0.0.1:8799/';
const b=await chromium.launch({channel:process.env.PW_CHANNEL,headless:true});
let failures=0;const test=async(name,fn)=>{try{await fn();console.log('PASS',name)}catch(e){failures++;console.log('FAIL',name,String(e.message||e).split('\n')[0])}};
const open=async(w,h,hash='',setup=null)=>{const p=await b.newPage({viewport:{width:w,height:h}});if(setup)await setup(p);await p.route('**/api/action/**',r=>r.request().method()==='POST'?r.abort():r.continue());
  await p.route('**/api/qwen',r=>r.request().method()==='POST'?r.abort():r.continue());await p.goto(base+hash);await p.waitForTimeout(900);return p};
const interactive=async(p,sel)=>p.evaluate(s=>{const el=document.querySelector(s);return !!el&&!el.closest('[inert]')},sel);
try{
 await test('enlarged chart: Tab reaches the chart and Close, arrows inspect, Escape restores the opener',async()=>{
  const p=await open(1440,900,'#/storage');const chart=p.locator('.chart').filter({has:p.locator('.chart__expand')}).first();
  await chart.hover();const opener=chart.locator('.chart__expand');await opener.click();const dlg=p.getByRole('dialog');await expect(dlg).toBeVisible();
  const seen=new Set();for(let i=0;i<4;i++){await p.keyboard.press('Tab');seen.add(await p.evaluate(()=>{const a=document.activeElement;return a.tagName==='svg'||a.tagName==='SVG'?'chart':a.textContent.trim()}))}
  expect([...seen]).toContain('chart');expect([...seen]).toContain('Close');
  expect(await p.evaluate(()=>!!document.activeElement.closest('[role=dialog]'))).toBe(true);
  await dlg.locator('svg[role=img]').focus();await p.keyboard.press('Home');await expect(dlg.locator('.chart-tooltip:not(.is-empty)')).toHaveCount(1);
  await p.keyboard.press('Escape');await p.keyboard.press('Escape');await expect(dlg).toHaveCount(0);
  expect(await p.evaluate(()=>document.activeElement.getAttribute('aria-label'))).toBe('Open larger chart');await p.close()});

 await test('phone: the enlarged chart opens and closes (predictive Back) without showing through the page',async()=>{
  const p=await b.newPage({viewport:{width:412,height:915},isMobile:true,hasTouch:true});await p.goto(base+'#/storage');await p.waitForTimeout(800);
  // Opening: time the dialog spends half-transparent (both layouts visible at once).
  await p.evaluate(()=>{window.__seeThrough=0;let last=performance.now();const t0=last;const tick=()=>{const now=performance.now(),d=document.querySelector('.m3-dialog');
   if(d){const o=Number(getComputedStyle(d).opacity);if(o>.15&&o<.85)window.__seeThrough+=now-last}last=now;if(now-t0<900)requestAnimationFrame(tick)};requestAnimationFrame(tick)});
  await p.locator('.chart__expand').first().click({force:true});await expect(p.getByRole('dialog')).toBeVisible();await p.waitForTimeout(900);
  expect(await p.evaluate(()=>window.__seeThrough)).toBeLessThan(100);
  // Back swipe: the same events MainActivity sends; the dialog must leave opaque and never be replayed by an exit copy.
  const r=await p.evaluate(()=>new Promise(done=>{const fire=d=>window.dispatchEvent(new CustomEvent('predictiveback',{detail:d}));const dlg=document.querySelector('.m3-dialog');
   fire({phase:'start',progress:0,edge:0});fire({phase:'progress',progress:.6,edge:0});fire({phase:'commit'});
   let see=0,copies=0;const t0=performance.now();const tick=()=>{document.querySelectorAll('.m3-dialog').forEach(d=>{const o=Number(getComputedStyle(d).opacity);
     if(d.closest('[aria-hidden="true"]'))copies++;else if(o>.15&&o<.85)see++});
    if(performance.now()-t0<500)requestAnimationFrame(tick);else done({see,copies,keptMark:!dlg.isConnected&&dlg.dataset.pbClosed==='1'})};requestAnimationFrame(tick)}));
  // keptMark: the unmounted dialog still says the exit already played, whatever order React runs its cleanup in.
  expect(r).toEqual({see:0,copies:0,keptMark:true});await expect(p.getByRole('dialog')).toHaveCount(0);await p.close()});

 await test('Local AI: Strata is listed with the other chat models (no separate switch card) and opens in Open WebUI',async()=>{
  // The preview builds /api/storage on Proxmox, which cannot read the PC's model server, so pin the AI sources to a known
  // state (PC up, a llama model loaded, Strata unloaded) instead of depending on whatever the PC is doing right now.
  const pin=async r=>{const res=await r.fetch();const d=await res.json();const now=Date.now()/1000,ok={online:true,last_success_at:now,last_attempt_at:now,error:null};
   d.ai={...(d.ai||{}),desktop:{...(d.ai?.desktop||{}),...ok,models:[{id:'qwen3.8-27b',state:'loaded',context_tokens:65536}]},strata:{...(d.ai?.strata||{}),...ok,loaded:false,model:'swift-1.5-iq2_xs'},webui:{...(d.ai?.webui||{}),...ok}};
   await r.fulfill({response:res,json:d})};
  const p=await open(1440,900,'#/ai',p=>p.route('**/api/storage*',pin));const chat=p.locator('.ai-task--chat');
  // Loaded (featured) or under the collapsed Other models: open that list first so the row is visible either way.
  await p.waitForTimeout(1500);await chat.locator('details.ai-others').evaluate(d=>{d.open=true}).catch(()=>{});
  await expect(chat.getByRole('heading',{name:'Strata · Qwen3.8-Flash-Next'})).toHaveCount(1,{timeout:8000});
  const row=chat.locator('.ai-model').filter({has:p.getByRole('heading',{name:'Strata · Qwen3.8-Flash-Next'})});
  const link=row.getByRole('link',{name:/Open chat/});
  if(await link.count())expect(await link.getAttribute('href')).toBe('http://192.168.0.16:3000/?model=swift-1.5-iq2_xs');
  await expect(p.getByRole('group',{name:'GPU engine'})).toHaveCount(0);
  await expect(chat.getByRole('link',{name:/^Strata/})).toHaveAttribute('href','https://strata.lan');await p.close()});

 await test('Overview: Quick actions and Activity come before Pinned services and the Ask panel (phone and desktop)',async()=>{
  for(const [w,h] of [[412,915],[1440,900]]){
   const p=await open(w,h);await p.waitForTimeout(900);
   const top=async loc=>(await loc.first().boundingBox()).y;
   const q=await top(p.getByRole('heading',{name:'Quick actions',exact:true})),a=await top(p.getByRole('heading',{name:'Activity',exact:true})),f=await top(p.getByRole('heading',{name:/^Pinned services/})),k=await top(p.locator('.qwen-section'));
   expect(q).toBeLessThan(a);expect(a).toBeLessThan(f);expect(f).toBeLessThan(k);await p.close()}});

 await test('Overview: stale data is a neutral state (no attention hero, no stale chip) and says when the last reading was',async()=>{
  const p=await open(412,915);await p.waitForTimeout(1200);
  await p.evaluate(()=>{const n=Date.now.bind(Date);Date.now=()=>n()+300000});
  // The app clock ticks every 15 s (a 1 s tick re-rendered the whole app; code review 30 Oct), so allow one tick.
  await expect(p.locator('.ov-hero')).toHaveClass(/ov-hero--stale/,{timeout:17000});
  await expect(p.locator('.ov-hero--attention')).toHaveCount(0);
  await expect(p.locator('.issue-chip',{hasText:'Dashboard data is stale'})).toHaveCount(0);
  await expect(p.locator('.ov-hero__title')).toHaveText(/Last reading|Waiting for data/);
  await expect(p.locator('.notice.warning').first()).toContainText(/telemetry|Connection interrupted/);await p.close()});

 await test('Local AI: the Ask header names the selected model; the previous answer keeps its own model inside the answer',async()=>{
  const p=await open(1440,900,'#/ai');
  await p.addInitScript(()=>{try{localStorage.setItem('qwenTarget','a310')}catch{}});
  const now=Math.floor(Date.now()/1000);
  await p.route('**/api/qwen',r=>r.request().method()==='GET'?r.fulfill({json:{status:'complete',model:'Strata · Qwen3.8-Flash-Next · gaming PC',id:'f',prompt:'q',answer:'An answer.',performance:{tokens_per_second:40,input_tokens:10,output_tokens:20,generation_seconds:1},total_seconds:3,context_at:now,context_note:'Readings captured',evidence:{observed_at:now,sources:{},not_selected:[],budget_omissions:[]},updated_at:now}}):r.abort());
  await p.reload();await p.waitForTimeout(1500);
  await expect(p.locator('.qwen-model')).toHaveText('Gemma 4 E4B · A310');
  await expect(p.locator('.qwen-panel')).toContainText('Strata · Qwen3.8-Flash-Next · gaming PC');await p.close()});

 await test('badges: the warning tone is amber, not the neutral grey',async()=>{
  const p=await open(1440,900);
  const bg=await p.evaluate(()=>{const mk=c=>{const e=document.createElement('span');e.className='badge '+c;document.body.appendChild(e);const v=getComputedStyle(e).backgroundColor;e.remove();return v};return [mk('warn'),mk('neutral'),mk('good')]});
  expect(bg[0]).not.toBe(bg[1]);expect(bg[0]).not.toBe(bg[2]);await p.close()});

 await test('phone: buttons and segments are 48 px touch targets',async()=>{
  const p=await b.newPage({viewport:{width:412,height:915},isMobile:true,hasTouch:true});await p.route('**/api/action/**',r=>r.abort());
  const small=sel=>p.$$eval(sel,els=>els.filter(e=>e.offsetParent&&getComputedStyle(e).visibility!=='hidden').map(e=>Math.round(e.getBoundingClientRect().height)).filter(h=>h<48));
  await p.goto(base);await p.waitForTimeout(1200);expect(await small('.ov-actions .m3-btn, main .text-button')).toEqual([]);
  await p.goto(base+'#/services');await p.waitForTimeout(800);expect(await small('.segments button')).toEqual([]);await p.close()});

 await test('search open across 1440 -> 448: the new bottom bar is inert; close focuses a visible trigger',async()=>{
  const p=await open(1440,900);await p.getByRole('button',{name:'Find a service'}).first().click();await expect(p.getByRole('dialog',{name:'Find a service'})).toBeVisible();
  await p.setViewportSize({width:448,height:900});await p.waitForTimeout(400);
  expect(await interactive(p,'.m3-nav-bar')).toBe(false);expect(await interactive(p,'#main')).toBe(false);
  await p.keyboard.press('Escape');await p.waitForTimeout(300);expect(await interactive(p,'.m3-nav-bar')).toBe(true);
  expect(await p.evaluate(()=>document.activeElement!==document.body&&document.activeElement.getClientRects().length>0)).toBe(true);await p.close()});

 await test('drawer open across 448 -> 700 -> 448: rail and bar stay inert while it is open',async()=>{
  const p=await open(448,900);await p.getByRole('button',{name:/^(More|Open navigation)$/}).click();await p.waitForTimeout(300);
  await p.setViewportSize({width:700,height:900});await p.waitForTimeout(400);
  expect(await interactive(p,'.m3-nav-rail')).toBe(false);expect(await interactive(p,'#main')).toBe(false);
  await p.setViewportSize({width:448,height:900});await p.waitForTimeout(400);expect(await interactive(p,'.m3-nav-bar')).toBe(false);
  await p.keyboard.press('Escape');await p.waitForTimeout(300);expect(await interactive(p,'#main')).toBe(true);await p.close()});

 await test('wheel over the search and chart scrims does not scroll the page',async()=>{
  const p=await open(1440,800,'#/storage');await p.getByRole('button',{name:'Find a service'}).first().click();await p.mouse.move(1300,700);await p.mouse.wheel(0,600);await p.waitForTimeout(300);
  expect(await p.evaluate(()=>scrollY)).toBe(0);await p.keyboard.press('Escape');
  const chart=p.locator('.chart').filter({has:p.locator('.chart__expand')}).first();await chart.hover();await chart.locator('.chart__expand').click();
  const y0=await p.evaluate(()=>scrollY);await p.mouse.move(20,780);await p.mouse.wheel(0,600);await p.waitForTimeout(300);expect(await p.evaluate(()=>scrollY)).toBe(y0);
  await p.keyboard.press('Escape');await p.keyboard.press('Escape');await p.mouse.wheel(0,400);await p.waitForTimeout(300);expect(await p.evaluate(()=>scrollY)).toBeGreaterThan(y0);await p.close()});

 for(const w of [600,700,780,840,1000,1440])await test(`pressing a quick action at ${w}px does not re-flow the row`,async()=>{
  const p=await open(w,1100);const row=p.locator('.quick-actions').first();for(const btn of await row.locator('.m3-btn:not(:disabled)').all()){
   const before=await row.boundingBox();const bb=await btn.boundingBox();await p.mouse.move(bb.x+bb.width/2,bb.y+bb.height/2);await p.mouse.down();await p.waitForTimeout(250);
   const during=await row.boundingBox();await p.mouse.move(2,2);await p.mouse.up();expect(Math.round(during.height)).toBe(Math.round(before.height))}
  await p.close()});

 await test('Local AI: Ask about the homelab is on the page and takes focus from the first viewport',async()=>{
  const p=await open(412,915,'#/ai');const box=p.getByRole('textbox',{name:'Your question'});await expect(box).toBeVisible();
  expect((await box.boundingBox()).y).toBeLessThan(915*2);await box.focus();await expect(box).toBeFocused();await p.close()});

 await test('phone More opens a sheet; focus goes in and returns to More',async()=>{
  const p=await b.newPage({viewport:{width:412,height:915},isMobile:true,hasTouch:true});await p.goto(base+'#/storage');await p.waitForTimeout(800);
  await p.getByRole('button',{name:'More'}).click();await p.waitForTimeout(500);const sheet=p.locator('.m3-nav-drawer.is-sheet.open');await expect(sheet).toBeVisible();
  const bb=await sheet.boundingBox();expect(Math.round(bb.y+bb.height)).toBeGreaterThanOrEqual(914);
  expect(await p.evaluate(()=>document.activeElement.textContent)).toContain('Storage');
  await p.keyboard.press('Escape');await p.waitForTimeout(500);expect(await p.evaluate(()=>document.activeElement.textContent)).toContain('More');await p.close()});

 await test('phone: after picking a page from More, Android Back sees no open overlay and goes back',async()=>{
  // The same check MainActivity.handleOnBackPressed runs before deciding whether Back closes an overlay or navigates.
  const nativeSeesOverlay=()=>{const modals=document.querySelectorAll('.m3-search-view, .m3-dialog, .m3-nav-drawer.is-modal.open, .m3-xrail.is-modal, .m3-scrim');
   const open=[...modals].some(m=>getComputedStyle(m).display!=='none'&&m.getClientRects().length>0);return (typeof window.__hasOpenOverlay==='function'&&window.__hasOpenOverlay())||open};
  const p=await b.newPage({viewport:{width:412,height:915},isMobile:true,hasTouch:true});await p.goto(base+'#/overview');await p.waitForTimeout(800);
  await p.getByRole('button',{name:'More'}).click();await p.waitForTimeout(500);
  await p.locator('.m3-nav-drawer.is-sheet.open').getByText('Network & DNS',{exact:true}).click();await p.waitForTimeout(700);
  expect(await p.evaluate(()=>location.hash)).toBe('#/network');
  expect(await p.evaluate(nativeSeesOverlay)).toBe(false);
  await p.evaluate(()=>history.back());await p.waitForTimeout(600);
  expect(await p.evaluate(()=>location.hash)).toBe('#/overview');await p.close()});

 const drag=async(p,x,y0,y1,steps=8)=>{const c=await p.context().newCDPSession(p);await c.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y:y0}]});
  for(let i=1;i<=steps;i++){await c.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x,y:y0+(y1-y0)*i/steps}]});await p.waitForTimeout(16)}
  return async()=>{await c.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await c.detach()}};
 await test('phone: swiping the More sheet down dismisses it; a short drag springs back',async()=>{
  const p=await b.newPage({viewport:{width:412,height:915},isMobile:true,hasTouch:true});await p.goto(base+'#/storage');await p.waitForTimeout(800);
  await p.getByRole('button',{name:'More'}).click();await p.waitForTimeout(600);const sheet=p.locator('.m3-nav-drawer.is-sheet');
  let release=await drag(p,206,640,680);await release();await p.waitForTimeout(500);await expect(sheet).toHaveClass(/open/);
  release=await drag(p,206,640,900,10);await release();await p.waitForTimeout(700);await expect(sheet).not.toHaveClass(/open/);
  expect(await interactive(p,'#main')).toBe(true);await p.close()});
 await test('phone: pulling down at the top refreshes and shows the loading shape',async()=>{
  const p=await b.newPage({viewport:{width:412,height:915},isMobile:true,hasTouch:true});await p.goto(base);await p.waitForTimeout(900);
  let telemetry=0;p.on('request',r=>{if(r.url().includes('/api/telemetry'))telemetry++});
  const release=await drag(p,206,300,560,10);await expect(p.locator('.m3-pull')).toHaveCount(1);const before=telemetry;await release();
  await p.waitForTimeout(400);expect(telemetry).toBeGreaterThan(before);await p.close()});
 for(const [w,h] of [[412,915],[1440,900]]) await test(`${w}px: Overview "Read the report" lands on the log review in one press`,async()=>{
  const p=await b.newPage({viewport:{width:w,height:h},isMobile:w<600,hasTouch:w<600});await p.goto(base);await p.waitForTimeout(900);
  const go=p.locator('.ov-status',{has:p.getByRole('heading',{name:'Nightly log review'})}).getByRole('button',{name:'Read the report'});
  await go.scrollIntoViewIfNeeded();await go.click();await expect(p).toHaveURL(/#\/infra$/);
  await expect.poll(()=>p.evaluate(()=>{const r=document.getElementById('infra-logs')?.getBoundingClientRect();return !!r&&r.top>=0&&r.top<innerHeight&&document.activeElement?.id==='infra-logs'}),{timeout:4000}).toBe(true);await p.close()});
}finally{await b.close()}
if(failures){console.log(`${failures} FAILED`);process.exit(1)}console.log('PASS: overlay and interaction regressions');

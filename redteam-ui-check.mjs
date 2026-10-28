// Red-team UI regressions from the 27 Oct audit: chart-dialog focus, malformed telemetry fields, the services-down
// chip, sheet resize, empty telemetry, Escape+Back, the forward phantom, null weather, the unknown hash and the
// sheet pill. Every non-GET request is aborted; telemetry and storage baselines come from DASHBOARD_URL.
import {chromium} from 'playwright';import {expect} from '@playwright/test';
let base=process.env.DASHBOARD_URL||'http://127.0.0.1:8799/';if(!base.endsWith('/'))base+='/';
const b=await chromium.launch({channel:process.env.PW_CHANNEL,headless:true});
let failures=0;const test=async(name,fn)=>{try{await fn();console.log('PASS',name)}catch(e){failures++;console.log('FAIL',name,String(e.message||e).split('\n')[0])}};
const WV='Mozilla/5.0 (Linux; Android 15; Pixel 9; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/130.0.0.0 Mobile Safari/537.36';
const live=await (await fetch(base+'api/telemetry')).json();
const liveStorage=await (await fetch(base+'api/storage?window=3600')).json();
const page=async({w=1440,h=900,phone=false,tele=null,storage=null}={})=>{const c=await b.newContext({viewport:{width:w,height:h},...(phone?{userAgent:WV,isMobile:true,hasTouch:true}:{})});const p=await c.newPage();
 p.on('pageerror',()=>{});
 await p.route('**/*',async r=>{const u=r.request().url();if(r.request().method()!=='GET')return r.abort();
  if(tele&&u.includes('/api/telemetry'))return r.fulfill({json:typeof tele==='function'?tele():tele});
  if(storage&&u.includes('/api/storage'))return r.fulfill({json:storage});return r.continue()});
 return p};
const fatal=p=>p.evaluate(()=>!!document.querySelector('main.fatal'));
const T=o=>({...structuredClone(live),updated_at:Math.floor(Date.now()/1000),...o});
try{
 await test('1 chart dialog keeps focus 3 s',async()=>{
  const p=await page();await p.goto(base+'#/storage');await p.waitForTimeout(1800);
  const ch=p.locator('.chart').filter({has:p.locator('.chart__expand')}).first();await ch.hover();await ch.locator('.chart__expand').click();await p.waitForTimeout(500);
  const ev=await p.evaluate(async()=>{let n=0;addEventListener('m3:dialog-open',()=>n++);document.querySelector('[role=dialog] svg[role=img]').focus();await new Promise(r=>setTimeout(r,3000));return {n,focusOnChart:document.activeElement?.matches?.('svg[role=img]')}});
  expect(ev.focusOnChart).toBe(true);expect(ev.n).toBe(0);await p.context().close()});

 for(const [name,view,tele,storage] of [
  ['log ts bad','#/infra',T({syslog:{...live.syslog,latest_event:{...(live.syslog?.latest_event||{}),ts:'abc',msg:'x',level:'ERROR',host:'h'},log_groups:undefined}}),null],
  ['disk no name','#/overview',null,{...liveStorage,disks:[{id:'x',used_pct:50}]}],
  ['ai speed string','#/ai',null,{...liveStorage,ai:{...(liveStorage.ai||{}),desktop:{...(liveStorage.ai?.desktop||{}),is_active:true,active_speed:'42.5',active_model:(liveStorage.ai?.desktop?.models||[])[0]?.id}}}],
 ]){await test('2 '+name+' does not blank the app',async()=>{
  const p=await page({tele,storage});await p.goto(base+view);await p.waitForTimeout(2200);
  expect(await fatal(p)).toBe(false);
  expect(await p.locator('.m3-nav-rail,.m3-xrail,.m3-nav-bar').count()).toBeGreaterThan(0);
  await p.context().close()})}

 await test('3 services-down chip filters on desktop',async()=>{
  const tele=T({});const ids=Object.keys(tele.services||{}).filter(k=>typeof tele.services[k]==='boolean').slice(0,2);
  expect(ids.length).toBeGreaterThan(0);
  ids.forEach(k=>tele.services[k]=false);
  const p=await page({tele:()=>({...tele,updated_at:Math.floor(Date.now()/1000)})});await p.goto(base);await p.waitForTimeout(2000);
  const chip=p.locator('.ov-hero .issue-chip',{hasText:/down/});
  expect(await chip.count()).toBeGreaterThan(0);
  await chip.first().click();await p.waitForTimeout(1200);
  expect(await p.locator('select[aria-label="Service status filter"]').inputValue()).toBe('offline');
  await p.context().close()});

 await test('4 resize closes the More sheet',async()=>{
  const p=await page({w:412,h:915,phone:true});await p.goto(base+'#/storage');await p.waitForTimeout(1500);
  await p.getByRole('button',{name:/More/}).last().click();await p.waitForTimeout(600);
  await p.setViewportSize({width:1000,height:900});await p.waitForTimeout(600);
  const st=await p.evaluate(()=>({overlay:window.__hasOpenOverlay?.(),state:history.state}));
  await p.setViewportSize({width:412,height:915});await p.waitForTimeout(600);
  expect(st.overlay).toBeFalsy();
  expect(await p.locator('.m3-nav-drawer.is-sheet.open').count()).toBe(0);
  await p.context().close()});

 await test('5 no telemetry says No reading, not Unmonitored',async()=>{
  const p=await page({tele:{}});await p.goto(base+'#/services');await p.waitForTimeout(2000);
  const txt=await p.evaluate(()=>document.body.innerText);
  expect(/No reading/.test(txt)).toBe(true);
  expect(txt.replace(/All statuses[\s\S]*?Stale/,'')).not.toMatch(/Unmonitored\n/);
  await p.context().close()});

 await test('5 services tile shows — / —',async()=>{
  const p=await page({tele:{}});await p.goto(base+'#/overview');await p.waitForTimeout(1500);
  const tile=await p.locator('.ov-hero .summary-link',{hasText:'Services reachable'}).innerText();
  expect(/—\s*\/\s*—/.test(tile)).toBe(true);
  await p.context().close()});

 await test('6 Escape+Back stays on the page',async()=>{
  const p=await page({w:412,h:915,phone:true});await p.goto(base+'#/overview');await p.waitForTimeout(1200);
  await p.goto(base+'#/storage');await p.waitForTimeout(1200);
  await p.getByRole('button',{name:/More/}).last().click();await p.waitForTimeout(600);
  await p.keyboard.press('Escape');await p.evaluate(()=>history.back());await p.waitForTimeout(1200);
  expect(await p.evaluate(()=>location.hash)).toBe('#/storage');
  await p.context().close()});

 await test('7 Forward then one Back leaves the page',async()=>{
  const p=await page({w:412,h:915,phone:true});await p.goto(base+'#/overview');await p.waitForTimeout(1000);
  await p.goto(base+'#/storage');await p.waitForTimeout(1200);
  await p.getByRole('button',{name:/More/}).last().click();await p.waitForTimeout(600);
  await p.locator('.m3-scrim').last().click({force:true});await p.waitForTimeout(800);
  await p.evaluate(()=>history.forward());await p.waitForTimeout(800);
  await p.evaluate(()=>history.back());await p.waitForTimeout(1000);
  expect(await p.evaluate(()=>location.hash)).toBe('#/overview');
  await p.context().close()});

 await test('10 no "null" in the header',async()=>{
  const p=await page({tele:T({weather:{temp_f:null}})});await p.goto(base+'#/does-not-exist');await p.waitForTimeout(1500);
  const sub=await p.locator('.m3-page-header__subtitle').first().innerText().catch(()=>'');
  expect(sub).not.toMatch(/null/);
  await p.context().close()});

 await test('17 unknown hash replaced',async()=>{
  const p=await page({tele:T({weather:{temp_f:null}})});await p.goto(base+'#/does-not-exist');await p.waitForTimeout(1500);
  expect(await p.evaluate(()=>location.hash)).toBe('#/overview');
  await p.context().close()});

 await test('12 current-page pill inside the sheet',async()=>{
  const p=await page({w:412,h:915,phone:true});await p.goto(base+'#/storage');await p.waitForTimeout(1500);
  await p.getByRole('button',{name:/More/}).last().click();await p.waitForTimeout(700);
  const r=await p.evaluate(()=>{const a=document.querySelector('.is-sheet .m3-nav-drawer__item[aria-current=page]');const s=document.querySelector('.m3-nav-drawer.is-sheet');if(!a||!s)return null;const x=a.getBoundingClientRect(),y=s.getBoundingClientRect();return {left:Math.round(x.left-y.left),right:Math.round(y.right-x.right)}});
  expect(r).not.toBeNull();expect(r.left).toBeGreaterThanOrEqual(8);expect(r.right).toBeGreaterThanOrEqual(8);
  await p.context().close()});
}finally{await b.close()}
if(failures){console.log(`${failures} FAILED`);process.exit(1)}
console.log('PASS: red-team UI regressions');

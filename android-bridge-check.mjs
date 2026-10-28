// The web side of the Android wrapper (27 Oct 2026): system bar colours follow the theme, ready() fires after the
// first paint, deep links accept only real page names, and the Wake PC shortcut sends exactly one wake request.
// AndroidBridge is stubbed; every non-GET is answered by a stub (never reaches a service).
import {chromium} from 'playwright';import {expect} from '@playwright/test';
const base=process.env.DASHBOARD_URL||'http://127.0.0.1:8799/';
const b=await chromium.launch({channel:process.env.PW_CHANNEL,headless:true});
let failures=0;const test=async(name,fn)=>{try{await fn();console.log('PASS',name)}catch(e){failures++;console.log('FAIL',name,String(e.message||e).split('\n')[0])}};
const open=async()=>{const p=await b.newPage({viewport:{width:412,height:915},isMobile:true,hasTouch:true});p.wakes=0;
 await p.route('**/*',r=>{const rq=r.request();if(rq.method()==='GET')return r.continue();if(rq.url().includes('/api/action/wake-pc')){p.wakes++;return r.fulfill({json:{ok:true}})}return r.abort()});
 await p.addInitScript(()=>{window.__calls=[];window.AndroidBridge={setSystemBars:(s,n,l)=>window.__calls.push(['bars',s,n,l]),ready:()=>window.__calls.push(['ready']),setBackEnabled:()=>{}}});
 await p.goto(base);await p.waitForTimeout(1200);return p};
try{
 await test('bars get valid colours on load and follow a theme toggle; ready() fires once',async()=>{const p=await open();
  const first=await p.evaluate(()=>window.__calls);const bars=first.filter(c=>c[0]==='bars');expect(bars.length).toBeGreaterThan(0);
  for(const [,s,n] of bars){expect(s).toMatch(/^#[0-9a-f]{6}$/i);expect(n).toMatch(/^#[0-9a-f]{6}$/i)}
  expect(first.filter(c=>c[0]==='ready').length).toBe(1);
  const before=bars.at(-1);await p.locator('.theme-toggle').first().click();await p.waitForTimeout(400);
  const after=(await p.evaluate(()=>window.__calls)).filter(c=>c[0]==='bars').at(-1);expect(after[1]).not.toBe(before[1]);expect(after[3]).toBe(!before[3]);
  const meta=await p.evaluate(()=>document.querySelector('meta[name="theme-color"]').content);expect(meta).toBe(after[1]);await p.close()});
 await test('deep links open real pages only',async()=>{const p=await open();
  await p.evaluate(()=>window.__homelabOpen('network'));await p.waitForTimeout(800);expect(await p.evaluate(()=>location.hash)).toBe('#/network');
  for(const bad of ['../x','<script>','constructor','__proto__','']){await p.evaluate(v=>window.__homelabOpen(v),bad);await p.waitForTimeout(150);expect(await p.evaluate(()=>location.hash)).toBe('#/network')}
  await p.evaluate(()=>window.__homelabShortcut('rm -rf'));await p.waitForTimeout(300);expect(await p.evaluate(()=>location.hash)).toBe('#/network');expect(p.wakes).toBe(0);await p.close()});
 await test('Wake PC shortcut: one request and a toast (or "already on")',async()=>{const p=await open();await p.evaluate(()=>{location.hash='#/storage'});await p.waitForTimeout(600);
  await p.evaluate(()=>window.__homelabShortcut('wake-pc'));await p.waitForTimeout(1500);expect(await p.evaluate(()=>location.hash)).toBe('#/overview');
  const toast=await p.locator('.m3-snackbar,[role=status]').allInnerTexts();const txt=toast.join(' ');
  expect(p.wakes===1&&/Wake signal sent/.test(txt)||p.wakes===0&&/already on/.test(txt)).toBe(true);await p.close()});
}finally{await b.close()}
process.exit(failures?1:0);

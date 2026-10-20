import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
fs.mkdirSync('.preview',{recursive:true});
const browser=await chromium.launch({headless:true,channel:process.env.PW_CHANNEL||'chrome'});
const results=[];
for(const [width,height] of [[360,800],[412,915],[448,998],[998,448],[1440,1000],[1920,1080],[2560,1440],[3440,1440]]){
 const mobile=width<1100;
 const context=await browser.newContext({viewport:{width,height},isMobile:mobile,hasTouch:mobile,deviceScaleFactor:mobile?3:1,reducedMotion:'reduce',timezoneId:'America/New_York'});
 await context.route('**/api/action/**',r=>r.abort());
 const page=await context.newPage();page.setDefaultTimeout(15000);
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(process.env.DASHBOARD_URL||'http://127.0.0.1:8799/',{waitUntil:'domcontentloaded'});
 await page.getByRole('heading',{name:'Overview',exact:true}).waitFor();
 for(const view of ['Overview','Storage','All services 28','Hosts & containers']){
  if(width<=760)await page.getByRole('button',{name:'Open navigation',exact:true}).click();
  await page.getByRole('button',{name:view,exact:true}).click();
  await page.evaluate(()=>document.fonts.ready);
  const overflow=await page.evaluate(()=>{
   const bad=[...document.querySelectorAll('.panel,.service-card,.fleet-row,.summary-stat')].filter(e=>e.scrollWidth>e.clientWidth+2).map(e=>({class:e.className,width:e.clientWidth,scroll:e.scrollWidth}));
   return {page:document.documentElement.scrollWidth>innerWidth+1,bad};
  });
  assert.equal(overflow.page,false,`${width} ${view} page overflow`);
  assert.deepEqual(overflow.bad,[],`${width} ${view} card overflow`);
  const text=await page.locator('main').innerText();assert.ok(!/A clear view|frequent stops|as it happens|corner of the internet|at a glance/.test(text));
  if([448,1920,3440].includes(width)&&['Overview','Storage'].includes(view))await page.screenshot({path:`.preview/${width}-${view.toLowerCase()}.png`,fullPage:true});
 }
 if(width<=760){
  const small=await page.locator('.topbar button').evaluateAll(es=>es.filter(e=>e.getBoundingClientRect().width<44||e.getBoundingClientRect().height<44).map(e=>e.ariaLabel));assert.deepEqual(small,[]);
 }
 assert.deepEqual(errors,[]);
 results.push(`${width}x${height}: four views, no page/card overflow or JS errors`);
 await context.close();
}
console.log(results.join('\n'));await browser.close();

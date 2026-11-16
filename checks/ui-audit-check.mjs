// Regressions for the four UI bugs from the 15 Nov audit (SPEC-ui-audit-fixes-20261115):
// 1) download history never opened (Response read as a body), 2) an empty GPU reading printed
//    "Last reading Invalid Date", 3) disabled/snoozed quick actions lost their button state,
// 4) llama-server offline was shown as "Gaming PC unavailable" with a wake button.
// Each test pins the API state through route interception, so it does not depend on what the PC is doing.
import {chromium} from 'playwright';import {expect} from '@playwright/test';
const base=process.env.DASHBOARD_URL||'http://127.0.0.1:8799/';
const b=await chromium.launch({channel:process.env.PW_CHANNEL,headless:true});
let failures=0;const test=async(name,fn)=>{try{await fn();console.log('PASS',name)}catch(e){failures++;console.log('FAIL',name,String(e.message||e).split('\n')[0])}};
const open=async(w,h,hash='',setup=null)=>{const p=await b.newPage({viewport:{width:w,height:h}});if(setup)await setup(p);
  await p.route('**/api/action/**',r=>r.request().method()==='POST'?r.abort():r.continue());
  await p.route('**/api/qwen',r=>r.request().method()==='POST'?r.abort():r.continue());
  await p.goto(base+hash);await p.waitForTimeout(900);return p};
try{
 await test('downloads: the history opens and rows show title and finish time (no Invalid Date)',async()=>{
  const now=Math.floor(Date.now()/1000);
  const setup=async p=>p.route('**/api/debrid*',r=>r.fulfill({json:{ok:true,features:['search','preview','dismiss','space','cached','history','queue'],downloads:[],history:[
   {id:'h1',title:'Severance',provider:'RD',finished:now-1800,kind:'movie',size_bytes:2600000000,files:3,seconds:312,avg_bps:8388608},
   {id:'h2',title:'Severance',provider:'RD',finished:now-5400,kind:'show',size_bytes:900000000,files:1,seconds:108,avg_bps:8388608}]}}));
  const p=await open(1440,900,'#/media',setup);
  const btn=p.getByRole('button',{name:'Open the download history'});
  await expect(btn).toBeVisible({timeout:8000});
  await btn.click();
  const rows=p.locator('.dl-history__line');
  await expect(rows).toHaveCount(2,{timeout:5000});
  const texts=await rows.allInnerTexts();
  for(const t of texts){expect(t).not.toContain('Invalid Date')}
  expect(texts[0]).toContain('Severance');
  expect(texts[0]).toMatch(/\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b/);  // the formatted finish time
  await p.close()});

 await test('gpu: an empty reading says Unavailable (no Invalid Date, no NaN)',async()=>{
  const setup=async p=>p.route('**/api/storage*',async r=>{const res=await r.fetch();const d=await res.json();
   d.pc_gpu={latest:{},history:[]};await r.fulfill({response:res,json:d})});
  const p=await open(1440,900,'#/ai',setup);
  const panel=p.locator('.gpu-section.pc-gpu');
  await expect(panel).toContainText('Unavailable',{timeout:8000});
  expect(await panel.innerText()).not.toContain('Invalid Date');
  expect(await panel.innerText()).not.toMatch(/\bNaN\b/);
  await p.close()});

 await test('quick actions: a snoozed button keeps its highlight and a disabled one looks disabled',async()=>{
  const p=await open(1440,900);
  await p.waitForTimeout(600);
  const r=await p.evaluate(()=>{
   const row=document.querySelector('.ov-actions .quick-actions');
   if(!row)return{found:false};
   const btn=cls=>{const e=document.createElement('button');e.type='button';
    e.className='m3-btn m3-btn--tonal m3-state secondary-button'+(cls?' '+cls:'');return e};
   const bg=e=>{row.appendChild(e);const v=getComputedStyle(e).backgroundColor;e.remove();return v};
   const ref=css=>{const d=document.createElement('div');d.style.cssText='background:'+css+';position:absolute;visibility:hidden';
    document.body.appendChild(d);const v=getComputedStyle(d).backgroundColor;d.remove();return v};
   const dis=btn('');dis.disabled=true;
   return{found:true,tonal:bg(btn('')),snoozed:bg(btn('is-snoozed')),disabled:bg(dis),
    tertiary:ref('var(--md-sys-color-tertiary-container)'),
    dim:ref('color-mix(in srgb,var(--md-sys-color-on-surface) 10%,transparent)')}});
  expect(r.found).toBe(true);
  expect(r.snoozed).toBe(r.tertiary);   // the snoozed state keeps its highlight
  expect(r.snoozed).not.toBe(r.tonal);
  expect(r.disabled).toBe(r.dim);       // a disabled action no longer looks pressed-and-enabled
  expect(r.disabled).not.toBe(r.tonal);
  await p.close()});

 await test('ai: llama-server offline is a server state (no "PC unavailable", no wake button, group not "off")',async()=>{
  const setup=async p=>p.route('**/api/storage*',async r=>{const res=await r.fetch();const d=await res.json();
   const now=Date.now()/1000,ok={online:true,last_success_at:now,last_attempt_at:now,error:null};
   d.ai={...(d.ai||{}),
    desktop:{online:false,last_attempt_at:now,error:'llm-router down',models:[]},
    strata:{...ok,loaded:false,model:'qwen3.8-flash-next-iq3_xxs'},
    webui:{...ok}};
   await r.fulfill({response:res,json:d})});
  const p=await open(1440,900,'#/ai',setup);
  const card=p.locator('.ai-task--chat');
  await expect(card.locator('.ai-offline')).toContainText('llama-server offline',{timeout:8000});
  await expect(card).not.toContainText('Gaming PC unavailable');
  await expect(card.locator('.pc-power')).toHaveCount(0);   // no wake/sleep button while the PC is reachable
  // Ask model menu: the Gaming PC group is not marked off (the PC is on; only llama-server is down).
  await p.getByRole('combobox',{name:'Which model answers'}).first().click();
  await expect(p.getByRole('listbox')).toBeVisible();
  const groups=await p.locator('.m3-menu-select__group').allInnerTexts();
  expect(groups).toContain('Gaming PC');
  expect(groups).not.toContain('Gaming PC · off');
  await p.close()});
}finally{await b.close()}
if(failures){console.log(`${failures} FAILED`);process.exit(1)}console.log('PASS: ui-audit regressions');

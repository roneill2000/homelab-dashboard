import {chromium,expect} from '@playwright/test';
import {build} from 'esbuild';
import {readFile,mkdir} from 'node:fs/promises';
const base=process.env.DASHBOARD_URL||'http://192.168.0.2';
const browser=await chromium.launch({...(process.env.PW_NO_CHROME?{}:{channel:'chrome'}),headless:true});
try{
 const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/action/**',r=>r.abort());
 // Compile only in memory: never overwrites the deployable index.html.
 const bundle=await build({entryPoints:['src/app.jsx'],bundle:true,write:false,jsxFactory:'React.createElement',jsxFragment:'React.Fragment',target:'es2020'});
 const css=await readFile('src/styles.css','utf8');
 const react=await(await page.request.get(base+'/static/react.js')).text();const reactDom=await(await page.request.get(base+'/static/react-dom.js')).text();
 const html=`<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"><style>${css}</style><script>${react}</script><script>${reactDom}</script></head><body><div id="root"></div><script>${bundle.outputFiles[0].text.replaceAll('</script','<\\/script')}</script></body></html>`;
 await page.route(base+'/',r=>r.fulfill({contentType:'text/html',body:html}));
 let job={status:'idle',model:'Qwen3.5-4B'},posts=0,mode='accepted',statusFailure=false;
 await page.route('**/api/qwen',async r=>{
  if(r.request().method()==='POST'){
   posts++;expect(r.request().headers()['x-qwen-request']).toBe('1');const body=r.request().postDataJSON();expect(Object.keys(body)).toEqual(['prompt','target','homelab']);expect(typeof body.homelab).toBe('boolean');expect(body.target).toMatch(/^(a310|a310-12b|auto|strata|pc|pc:[a-z0-9.\-]+)$/);expect(body.prompt.length).toBeLessThanOrEqual(3000);
   if(mode==='invalid')return r.fulfill({status:400,json:{error:'Question rejected by fixture'}});
   if(mode==='busy'){job={status:'busy',model:'Qwen3.5-4B',error:'Nightly review is using the model.'};return r.fulfill({status:409,json:job})}
   job={status:'queued',model:'Qwen3.5-4B',id:'fixture',prompt:body.prompt};return r.fulfill({status:202,json:job});
  }
  return statusFailure?r.fulfill({status:503,json:{error:'fixture'}}):r.fulfill({json:job});
 });
 await page.goto(base);const panel=page.locator('.qwen-panel');const input=page.getByLabel('Your question');const ask=panel.getByRole('button',{name:'Ask',exact:true});
 await expect(ask).toBeDisabled();await input.fill('What do the latest readings show?');await expect(ask).toBeEnabled();await ask.click();await expect(panel).toContainText('Queued');await expect(input).toHaveValue('What do the latest readings show?');await expect(panel.getByRole('button',{name:'Please wait'})).toBeDisabled();expect(posts).toBe(1);
 job={...job,status:'loading'};await expect(panel).toContainText('Loading model',{timeout:6000});await expect(panel).toContainText('loads in about 12 seconds');job={...job,status:'generating'};await expect(panel).toContainText('Writing answer',{timeout:6000});
 const answer='<script>window.qwenInjected=true</script>\n'+('A_long_unbroken_model_word'.repeat(55));job={...job,status:'complete',model:'Qwen3.8-27B · gaming PC',answer,performance:{tokens_per_second:16.2,input_tokens:123,output_tokens:850,generation_seconds:52.5},load_seconds:24,total_seconds:80,context_at:Math.floor(Date.now()/1000)-180,context_note:'Readings may be unavailable.',evidence:{observed_at:Math.floor(Date.now()/1000),sources:{logs:{fresh:false,observed_at:1,message:'<img src=x onerror=window.evidenceInjected=true>'}},not_selected:['hosts'],budget_omissions:['logs detail']}};await expect(panel).toContainText(answer,{timeout:6000});expect(await page.evaluate(()=>window.qwenInjected)).toBeUndefined();await expect(panel).toContainText('Readings captured');await expect(panel).toContainText('16.2');await expect(panel).toContainText('850');await expect(panel).toContainText('tokens/s');await expect(panel).toContainText('Answered on the gaming PC (RTX 5070 Ti)');await expect(panel.locator('.qwen-gpu')).not.toContainText('A310');await expect(panel).toContainText('Qwen3.8-27B · gaming PC');
 await panel.getByText('Show evidence',{exact:true}).click();await panel.getByText('Historical warning logs',{exact:true}).click();await expect(panel.locator('.evidence-source pre')).toContainText('\"fresh\": false');await expect(panel).toContainText('Trimmed to fit: logs detail');await expect(panel).toContainText('Not selected for this question: Hosts & containers');expect(await page.evaluate(()=>window.evidenceInjected)).toBeUndefined();
 await panel.getByRole('button',{name:'Hide answer'}).click();await expect(panel.locator('.qwen-answer')).toHaveCount(0);await panel.getByRole('button',{name:'Read answer'}).click();await expect(panel.locator('.qwen-answer')).toHaveText(answer);
 await mkdir('.preview',{recursive:true});
 for(const width of [360,448,1920,3440]){
  await page.setViewportSize({width,height:1000});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);expect(await panel.evaluate(e=>e.scrollWidth<=e.clientWidth)).toBe(true);expect(await input.evaluate(e=>parseFloat(getComputedStyle(e).fontSize))).toBeGreaterThanOrEqual(16);expect((await ask.boundingBox()).height).toBeGreaterThanOrEqual(44);await panel.screenshot({path:`.preview/qwen-${width}.png`});
 }
 job={status:'idle',model:'Qwen3.5-4B'};await expect(panel.locator('.qwen-answer')).toHaveCount(0,{timeout:18000});await expect(panel).toContainText('Ready');
 mode='invalid';await ask.click();await expect(panel).toContainText('Question rejected by fixture');await expect(ask).toBeEnabled();
 mode='busy';await ask.click();await expect(panel).toContainText('Nightly review is using the model.');await expect(panel.getByRole('button',{name:'Please wait'})).toBeDisabled();
 job={status:'failed',error:'Fixture model load failed',model:'Qwen3.5-4B'};await expect(panel).toContainText('Fixture model load failed',{timeout:6000});await expect(ask).toBeEnabled();
 await input.fill('x'.repeat(3100));expect((await input.inputValue()).length).toBe(3000);
 statusFailure=true;await page.reload();await expect(panel).toContainText('Model status unavailable');await input.fill('Check again');await expect(ask).toBeDisabled();
 expect(errors).toEqual([]);console.log('PASS: isolated Qwen fixtures, POST interception, lifecycle polling, busy/invalid/failure/unavailable states, no model execution, escaped expandable answer, 1000-character limit, 360/448/1920/3440px, large controls/type, no JS errors.');
}finally{await browser.close()}

// Per-browser-tab chat history in the Ask AI box (docs/SPEC-ask-history.md, items 1-5).
// The Ask service is a fixture here: nothing reaches 192.168.0.2 or Unraid :11438, so the A310 run lock is untouched.
import {chromium,expect} from '@playwright/test';
import {build} from 'esbuild';
import {readFile,mkdir} from 'node:fs/promises';
const base=process.env.DASHBOARD_URL||'http://192.168.0.2';
const THINKING='MODEL THINKING MUST NEVER TRAVEL';
const MODEL='Nemotron Bolt E4B · Unraid';
const browser=await chromium.launch({...(process.env.PW_NO_CHROME?{}:{channel:'chrome'}),headless:true});
try{
 // Compile in memory only: the deployable index.html is never overwritten.
 const bundle=await build({entryPoints:['src/app.jsx'],bundle:true,write:false,jsxFactory:'React.createElement',jsxFragment:'React.Fragment',target:'es2020'});
 const css=await readFile('src/styles.css','utf8');
 const react=await(await (await browser.newPage()).request.get(base+'/static/react.js')).text();
 const reactDom=await(await (await browser.newPage()).request.get(base+'/static/react-dom.js')).text();
 const html=`<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"><style>${css}</style><script>${react}</script><script>${reactDom}</script></head><body><div id="root"></div><script>${bundle.outputFiles[0].text.replaceAll('</script','<\\/script')}</script></body></html>`;

 const mock=async page=>{
  const posts=[];let job={status:'idle',model:MODEL},id=0;
  await page.route(base+'/',r=>r.fulfill({contentType:'text/html',body:html}));
  await page.route('**/api/action/**',r=>r.abort());
  await page.route('**/api/qwen',async r=>{
   if(r.request().method()==='POST'){
    const body=r.request().postDataJSON();posts.push(body);
    expect(body.target).toMatch(/^(a310|a310-12b|auto|strata|pc|pc:[a-z0-9.\-]+)$/);
    expect(Array.isArray(body.history)).toBe(true);   // item 4: history always travels as [{q,a},…]
    expect(body.history.every(t=>Object.keys(t).sort().join()==='a,q')).toBe(true);
    // Long answers make the 10,000-character cap bite; the service's own limit for an answer is 6,000 characters.
    const answer=body.prompt.startsWith('Long chat question')?'Answer for · '+body.prompt+' · '+'detail '.repeat(600):'Answer for · '+body.prompt;
    job={status:'queued',id:'job-'+(++id),prompt:body.prompt,model:MODEL};
    setTimeout(()=>{job={...job,status:'complete',answer,model:MODEL,thinking:THINKING,
      performance:{tokens_per_second:12.4,input_tokens:400,output_tokens:220},total_seconds:3}},300);
    return r.fulfill({status:202,json:job});
   }
   return r.fulfill({json:job});
  });
  return posts;
 };

 const page=await browser.newPage({viewport:{width:1280,height:900}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const posts=await mock(page);
 await page.goto(base);
 const panel=page.locator('.qwen-panel'),input=page.getByLabel('Your question'),ask=panel.getByRole('button',{name:'Ask',exact:true});
 const askQuestion=async text=>{await input.fill(text);await expect(ask).toBeEnabled();await ask.click();await expect(panel).toContainText('Answer for · '+text,{timeout:9000})};

 // ---- a fresh tab has no history; the first question sends none ----------------
 await expect(panel).toContainText('Your question');
 await expect(panel.locator('.qwen-history')).toHaveCount(0);
 await expect(panel.getByRole('button',{name:'New chat'})).toHaveCount(0);   // item 3: only when there is history
 await askQuestion('What is free on the scratch disk?');
 expect(posts.length).toBe(1);
 expect(posts[0].history).toEqual([]);

 // ---- the follow-up carries the first question and answer (item 4) -------------
 await askQuestion('And what about the cache?');
 expect(posts.length).toBe(2);
 expect(posts[1].history).toEqual([{q:'What is free on the scratch disk?',a:'Answer for · What is free on the scratch disk?'}]);
 const historyBlock=panel.locator('.qwen-history');
 await expect(historyBlock).toContainText('What is free on the scratch disk?');
 await expect(historyBlock).toContainText('Answer for · What is free on the scratch disk?');
 await expect(historyBlock).toContainText(MODEL);                                   // the model that answered it
 const historyBox=await historyBlock.boundingBox(),inputBox=await input.boundingBox();
 expect(historyBox.y+historyBox.height).toBeLessThanOrEqual(inputBox.y+2);           // item 2: above the input
 expect(await historyBlock.locator('.qwen-turn').count()).toBe(1);   // the answer on screen is not repeated above it

 // ---- thinking is never stored or sent; the answer text is what is kept -------
 expect(JSON.stringify(posts)).not.toContain(THINKING);
 expect(posts[1].history[0].a).toBe('Answer for · What is free on the scratch disk?');

 // ---- a reload keeps the chat (item 1) ----------------------------------------
 await page.reload();
 await expect(panel.locator('.qwen-history')).toContainText('What is free on the scratch disk?',{timeout:9000});
 await askQuestion('Third question?');
 expect(posts.length).toBe(3);
 expect(posts[2].history.length).toBe(2);
 expect(posts[2].history.map(t=>t.q)).toEqual(['What is free on the scratch disk?','And what about the cache?']);

 // ---- a closed tab (a new browser context) starts empty ----------------------
 const closed=await browser.newContext({viewport:{width:1280,height:900}});
 const fresh=await closed.newPage();
 const freshPosts=await mock(fresh);
 await fresh.goto(base);
 await expect(fresh.locator('.qwen-history')).toHaveCount(0);
 expect(await fresh.evaluate(()=>sessionStorage.getItem('ask-history-v1'))).toBe(null);
 await fresh.getByLabel('Your question').fill('Does a closed tab remember?');
 await fresh.locator('.qwen-panel').getByRole('button',{name:'Ask',exact:true}).click();
 expect(freshPosts.length).toBe(1);
 expect(freshPosts[0].history).toEqual([]);
 await closed.close();

 // ---- "New chat" clears it (item 3) ------------------------------------------
 await panel.getByRole('button',{name:'New chat'}).click();
 await expect(panel.locator('.qwen-history')).toHaveCount(0);
 await askQuestion('Back to a single question?');
 expect(posts.length).toBe(4);
 expect(posts[3].history).toEqual([]);

 // ---- switching the model keeps the chat, and the UI says so (item 3) ------
 await askQuestion('First for this chat?');
 await panel.locator('.qwen-target .m3-menu-select__trigger').click();
 await page.locator('li[role=option]',{hasText:'Auto'}).first().click();
 await expect(panel.locator('.qwen-history-bar')).toContainText('Switching the model keeps this chat.');
 await askQuestion('Second for this chat?');
 const switched=posts[posts.length-1];
 expect(switched.target).toBe('auto');
 expect(switched.history.map(t=>t.q)).toEqual(['Back to a single question?','First for this chat?']);   // the new model gets the chat

 // ---- the 10,000-character cap drops the oldest turns first (item 4) ------
 for(let i=1;i<=4;i++)await askQuestion('Long chat question '+i+'?');
 await askQuestion('Which turns survive the cap?');
 const capped=posts[posts.length-1];
 const sentChars=capped.history.reduce((n,t)=>n+t.q.length+t.a.length,0);
 expect(capped.history.length).toBeLessThan(4);                              // the chat no longer fits whole
 expect(sentChars).toBeLessThanOrEqual(10000);
 expect(JSON.stringify(capped.history)).not.toContain('Long chat question 1?');   // the oldest went first
 expect(capped.history[capped.history.length-1].q).toBe('Long chat question 4?'); // the newest stayed
 expect(capped.history.every(t=>t.a.includes('detail'))).toBe(true);

 // ---- homelab data still applies to the new question only (item 5) ---------
 await panel.getByRole('button',{name:'Include homelab data'}).click();
 await askQuestion('How busy is the scratch disk right now?');
 const withData=posts[posts.length-1];
 expect(withData.homelab).toBe(true);
 expect(withData.history.every(t=>!t.a.includes('Observations:'))).toBe(true);

 // ---- a 412 px phone: the history scrolls, the input stays on screen (item 2)
 await page.setViewportSize({width:412,height:640});
 await askQuestion('Does the box still fit a phone?');
 const phoneInput=await input.boundingBox();
 expect(phoneInput.y).toBeLessThanOrEqual(640);
 expect(phoneInput.y+phoneInput.height).toBeLessThanOrEqual(640);
 expect(await panel.locator('.qwen-history').evaluate(e=>e.scrollHeight>=e.clientHeight)).toBe(true);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 expect(await panel.evaluate(e=>e.scrollWidth<=e.clientWidth)).toBe(true);
 await mkdir('.preview',{recursive:true});
 await panel.screenshot({path:'.preview/ask-history-phone.png'});
 await page.setViewportSize({width:1280,height:900});
 await panel.screenshot({path:'.preview/ask-history-desktop.png'});
 expect(errors).toEqual([]);
 console.log('PASS: per-tab chat history - empty first question, follow-up carries the first Q/A, turns above the input with the model that answered, no thinking sent, reload keeps it, a new context starts empty, New chat clears it, a model switch keeps it, the 10,000-character cap drops the oldest turns, homelab data stays on the new question, 412px input on screen and history scrolls, no JS errors.');
}finally{await browser.close()}

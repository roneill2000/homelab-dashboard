// M3 MenuSelect (replaced the native <select>s, 27 Oct 2026): keyboard selection, Back and outside tap close it without
// changing page, it flips above the trigger near the bottom of a small screen, disabled options can't be chosen.
import {chromium} from 'playwright';import {expect} from '@playwright/test';
const base=process.env.DASHBOARD_URL||'http://127.0.0.1:8799/';
const b=await chromium.launch({channel:process.env.PW_CHANNEL,headless:true});
let failures=0;const test=async(name,fn)=>{try{await fn();console.log('PASS',name)}catch(e){failures++;console.log('FAIL',name,String(e.message||e).split('\n')[0])}};
const open=async(w,h,hash='')=>{const p=await b.newPage({viewport:{width:w,height:h}});await p.route('**/*',r=>r.request().method()==='GET'?r.continue():r.abort());await p.goto(base+hash);await p.waitForTimeout(900);return p};
try{
 await test('keyboard: open, arrow, Enter selects and returns focus',async()=>{const p=await open(1440,900,'#/services');const t=p.getByRole('combobox',{name:'Service status filter'});
  await t.focus();await p.keyboard.press('ArrowDown');const box=p.getByRole('listbox',{name:'Service status filter'});await expect(box).toBeVisible();
  await expect(box).toBeFocused();await p.keyboard.press('ArrowDown');await p.keyboard.press('ArrowDown');await p.keyboard.press('Enter');
  await expect(box).toHaveCount(0);await expect(t).toHaveText('Online');await expect(t).toBeFocused();await p.close()});
 await test('typeahead and Escape',async()=>{const p=await open(1440,900,'#/services');const t=p.getByRole('combobox',{name:'Service status filter'});
  await t.click();await p.keyboard.type('st');await p.keyboard.press('Enter');await expect(t).toHaveText('Stale');
  await t.click();await p.keyboard.press('Escape');await expect(p.getByRole('listbox')).toHaveCount(0);await expect(t).toHaveText('Stale');await p.close()});
 await test('Back closes the menu and keeps the page',async()=>{const p=await open(412,915,'#/services');const t=p.getByRole('combobox',{name:'Service status filter'});
  await t.click();await expect(p.getByRole('listbox')).toBeVisible();await p.waitForTimeout(200);await p.goBack();await p.waitForTimeout(500);
  await expect(p.getByRole('listbox')).toHaveCount(0);expect(await p.evaluate(()=>location.hash)).toBe('#/services');await p.close()});
 await test('outside tap closes without selecting',async()=>{const p=await open(412,915,'#/services');const t=p.getByRole('combobox',{name:'Service status filter'});
  await t.click();await p.mouse.click(200,120);await expect(p.getByRole('listbox')).toHaveCount(0);await expect(t).toHaveText('All statuses');await p.close()});
 await test('flips above the trigger when there is no room below, and stays on screen',async()=>{const p=await open(360,320,'#/services');const t=p.getByRole('combobox',{name:'Service status filter'});
  await t.scrollIntoViewIfNeeded();await t.evaluate(el=>window.scrollBy(0,el.getBoundingClientRect().bottom-310));await p.waitForTimeout(300);await t.click();const box=p.getByRole('listbox');
  const [tb,bb]=[await t.boundingBox(),await box.boundingBox()];expect(bb.y+bb.height).toBeLessThanOrEqual(tb.y+1);expect(bb.y).toBeGreaterThanOrEqual(0);expect(bb.x+bb.width).toBeLessThanOrEqual(361);await p.close()});
 await test('disabled options cannot be chosen (model picker with the PC off)',async()=>{const p=await b.newPage({viewport:{width:1440,height:900}});
  await p.route('**/*',async r=>{if(r.request().method()!=='GET')return r.abort();if(r.request().url().includes('/api/storage')){const resp=await r.fetch();const d=await resp.json();d.ai={...(d.ai||{}),desktop:{...(d.ai?.desktop||{}),online:false,last_attempt_at:Math.floor(Date.now()/1000)}};return r.fulfill({json:d})}return r.continue()});
  await p.goto(base+'#/ai');await p.waitForTimeout(1500);const t=p.getByRole('combobox',{name:'Which model answers'}).first();const before=await t.textContent();await t.click();
  const off=p.getByRole('option').filter({has:p.locator('xpath=self::*[@aria-disabled="true"]')});expect(await off.count()).toBeGreaterThan(0);await off.first().click({force:true});
  await expect(t).toHaveText(before);await p.close()});
}finally{await b.close()}
process.exit(failures?1:0);

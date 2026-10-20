import {chromium,expect} from '@playwright/test';
const browser=await chromium.launch({channel:process.env.PW_CHANNEL||'chrome'});
try{
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 page.setDefaultTimeout(15000);
 await page.goto('http://127.0.0.1:8818/');
 await page.waitForTimeout(1000);
 await expect(page.getByRole('textbox').first()).toBeVisible({timeout:15000});
 for(const width of [1440,448]){
  await page.setViewportSize({width,height:998});
  await page.waitForTimeout(300);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:'.preview/memos-'+width+'.png'});
 }
 await page.setViewportSize({width:1440,height:1000});
 await page.goto('http://127.0.0.1:8802/');
 await page.getByRole('button',{name:'All services 28',exact:true}).click();
 const card=page.locator('.service-card').filter({hasText:'Memos'});
 await expect(card).toBeVisible();
 await expect(card.locator('a').first()).toHaveAttribute('href','http://192.168.0.18:5230');
 await expect(card.getByRole('img',{name:'Online',exact:true})).toBeVisible({timeout:15000});
 console.log('PASS: Memos automatic login, desktop/phone overflow and Homepage link/status');
}finally{await browser.close()}

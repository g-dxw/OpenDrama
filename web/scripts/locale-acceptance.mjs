import { respondSiteBootstrap } from "../../tests/frontend-site-fixture.mjs";
/** Desktop language acceptance with explicit local API fixtures; no provider calls. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer, transformWithEsbuild } from 'vite';
import puppeteer from 'puppeteer-core';
import ts from 'typescript';
const ROOT=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const OUT=process.env.SHOTS_DIR||path.join(os.tmpdir(),'jjtv-pc-locales');
await fs.mkdir(OUT,{recursive:true});
const languages=['en','de','fr','es','pt','it','ja','ru','zh'];
const tags={en:'en-US',de:'de-DE',fr:'fr-FR',es:'es-ES',pt:'pt-PT',it:'it-IT',ja:'ja-JP',ru:'ru-RU',zh:'zh-CN'};
const dictionaries={en:{}};
for(const file of (await fs.readdir(path.join(ROOT,'src'))).filter(file=>/^locale-.*\.ts$/.test(file)).sort()){
  const source=await fs.readFile(path.join(ROOT,'src',file),'utf8');
  const {code}=await transformWithEsbuild(source,file,{loader:'ts',format:'esm'});
  const section=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));
  for(const [name,value] of Object.entries(section)){
    if(name.endsWith('English'))Object.assign(dictionaries.en,value);
    if(name.endsWith('Translations'))for(const [locale,table] of Object.entries(value))Object.assign(dictionaries[locale]||={},table);
  }
}
const englishKeys=new Map(Object.entries(dictionaries.en).map(([key,value])=>[value,key]));
const copy=(locale,source,vars={})=>{
  const key=Object.prototype.hasOwnProperty.call(dictionaries.en,source)?source:englishKeys.get(source)||source;
  const value=dictionaries[locale]?.[key]??dictionaries.en[key]??source;
  return value.replace(/\{([A-Za-z0-9_]+)\}/g,(match,key)=>Object.prototype.hasOwnProperty.call(vars,key)?String(vars[key]):match);
};
const pageCopy=(locale,source)=>{const key=Object.keys(dictionaries.en).find(key=>key.startsWith('pages.')&&dictionaries.en[key]===source);assert(key,'Missing page phrase '+source);return dictionaries[locale][key];};
const checks=[];
const check=(name)=>checks.push(name);
const signatures=value=>[...value.matchAll(/\{([A-Za-z0-9_]+)\}/g)].map(item=>item[1]).sort();
const keys=Object.keys(dictionaries.en);
assert(keys.length>=400,'A partial vocabulary must not pass the full-site gate');
for(const locale of languages){
  for(const key of keys){
    assert.equal(typeof dictionaries[locale]?.[key],'string',locale+' missing '+key);
    assert(dictionaries[locale][key].length>0,locale+' empty '+key);
    assert.deepEqual(signatures(dictionaries[locale][key]),signatures(dictionaries.en[key]),locale+' interpolation '+key);
  }
  check(locale+' complete vocabulary ('+keys.length+' keys)');
}
for(const file of ['App','browse','components','account','support','login','player','desktop-video-controls','commerce']){
  const source=await fs.readFile(path.join(ROOT,'src',file+'.tsx'),'utf8');
  const ast=ts.createSourceFile(file+'.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
  const issues=[];
  const allowed=new Set(['JJ','TV','JJTV','JJTV · NO.']);
  function walk(node){
    if(ts.isJsxText(node)&&/[A-Za-z]/.test(node.text)&&!allowed.has(node.text.trim()))issues.push('raw text '+node.text.trim());
    if(ts.isJsxAttribute(node)&&['aria-label','placeholder','title','message'].includes(node.name.text)&&node.initializer&&ts.isStringLiteral(node.initializer)&&/[A-Za-z ]{2}/.test(node.initializer.text)&&!/^you@example\.com$/.test(node.initializer.text))issues.push('raw '+node.name.text+' '+node.initializer.text);
    if(ts.isCallExpression(node)&&node.expression.getText(ast)==='t'&&node.arguments[0]&&ts.isStringLiteral(node.arguments[0]))assert(Object.prototype.hasOwnProperty.call(dictionaries.en,node.arguments[0].text),file+' undeclared key '+node.arguments[0].text);
    ts.forEachChild(node,walk);
  }
  walk(ast);assert.deepEqual(issues,[],file+' contains unlocalized interface text');
}
check('all desktop modules use declared UI keys');
const helperSource=await fs.readFile(path.join(ROOT,'src','plan-display.ts'),'utf8');
const helper=await transformWithEsbuild(helperSource,'plan-display.ts',{loader:'ts',format:'esm'});
const {planPriceDisplay}=await import('data:text/javascript;base64,'+Buffer.from(helper.code).toString('base64'));
const base={currency:'USD',billing_mode:'subscription',name_en:'Server plan',name_zh:'服务端套餐',benefits:{items:[]}};
const monthly={...base,code:'month',price_cents:1999,recurring_interval:'month'};
const annual={...base,code:'year',price_cents:7999,recurring_interval:'year'};
const discount=planPriceDisplay(annual,[monthly,annual], 'subscription');
assert.equal(discount.priceCents,7999/12);assert.equal(discount.annualChargeCents,7999);assert.equal(discount.discountPercent,66);assert.equal(annual.price_cents,7999);
assert.equal(planPriceDisplay({...annual,billing_mode:'one_time'},[monthly], 'subscription').priceCents,7999);
assert.equal(planPriceDisplay({...annual,recurring_interval:'week'},[monthly], 'subscription').priceCents,7999);
assert.equal(planPriceDisplay({...annual,recurring_interval:'quarter'},[monthly], 'subscription').priceCents,7999);
assert.equal(planPriceDisplay(annual,[{...monthly,currency:'EUR'}], 'subscription').priceCents,7999);
assert.equal(planPriceDisplay(annual,[{...monthly,price_cents:500}], 'subscription').discountPercent,undefined);
assert.equal(planPriceDisplay(annual,[{...monthly,price_cents:999},monthly], 'subscription').monthlyComparisonCents,999);
check('annual display uses same-currency conservative monthly comparison and keeps full charge');
const googleSource=await fs.readFile(path.join(ROOT,'src','google.ts'),'utf8');
const googleModule=await transformWithEsbuild(googleSource,'google.ts',{loader:'ts',format:'esm'});
const previousWindow=globalThis.window;const googleRenders=[];let credentials=0,callback;
globalThis.window={google:{accounts:{id:{initialize(config){callback=config.callback;},renderButton(host,options){googleRenders.push(options.locale);}}}}};
try{
  const {renderGoogleButton}=await import('data:text/javascript;base64,'+Buffer.from(googleModule.code).toString('base64'));
  let current=true;const stale=renderGoogleButton({innerHTML:''},'fixture',()=>credentials++,'de-DE',()=>current);current=false;await stale;
  assert.deepEqual(googleRenders,[]);
  current=true;await renderGoogleButton({innerHTML:''},'fixture',()=>credentials++,'fr-FR',()=>current);
  assert.deepEqual(googleRenders,['fr_FR']);callback({credential:'fixture'});assert.equal(credentials,1);
  current=false;callback({credential:'stale-fixture'});assert.equal(credentials,1);
}finally{if(previousWindow===undefined)delete globalThis.window;else globalThis.window=previousWindow;}
check('Google button ignores stale language renders and credential callbacks');
const port=Number(process.env.LOCALE_PORT||5192),origin='http://127.0.0.1:'+port;
const server=await createServer({root:ROOT,server:{host:'127.0.0.1',port,strictPort:true},logLevel:'error'});await server.listen();
const browser=await puppeteer.launch({executablePath:process.env.CHROME_PATH||(process.platform==='win32'?'C:/Program Files/Google/Chrome/Application/chrome.exe':'/usr/bin/google-chrome'),headless:true,args:['--autoplay-policy=no-user-gesture-required']});
const page=await browser.newPage();await page.setViewport({width:1440,height:1000});
const errors=[],unknown=[],requests=[];page.on('pageerror',error=>errors.push(error.message));
const createdAt='2026-11-13T15:12:00Z';
const profile={id:71,public_id:'LOCALE-FIXTURE',email:'locale@example.test',display_name:'Server User',bio:'Server biography',avatar_url:'',coins:12345,membership:{active:false,until:null,plan_code:null,canceling:false}};
const drama={id:1,title:'Server Drama Name',cover_url:null,synopsis:'Operator synopsis stays unchanged.',tags:['Server tag'],episode_count:12,free_episode_count:0,play_count:12345,coin_price_per_episode:60,like_count:12345,comment_count:0,liked:false,favorited:false};
const plans=[{...base,code:'week',name_en:'Server Weekly',name_zh:'运营周套餐',price_cents:999,recurring_interval:'week',duration_days:7}, {...monthly,name_en:'Server Monthly',name_zh:'运营月套餐',duration_days:30}, {...annual,name_en:'Server Yearly',name_zh:'运营年套餐',duration_days:365}];
const packs=['Popular','Best value','Operator label'].map((badge,index)=>({code:'coins-'+index,name_en:'Server Coin Pack',name_zh:'运营金币包',price_cents:499,currency:'USD',coins:330,base_coins:300,bonus_coins:30,version_id:1,badge}));
const tasks={balance:12345,checkin:{checked_today:false,streak:1,amount:30},items:[{code:'like_5',name:'Server task',description:'Operator task description',amount:20,progress:1000,target:5,claimed:false,metric:'like'}]};
await page.setRequestInterception(true);
page.on('request',async request=>{
  if(await respondSiteBootstrap(request))return;
  const url=new URL(request.url());if(!url.pathname.startsWith('/v1/'))return request.continue();
  requests.push({path:url.pathname,query:url.search,method:request.method()});
  let status=200,data;const p=url.pathname;
  if(p==='/v1/me')data=profile;
  else if(p==='/v1/home/groups')data={banners:[{id:1,drama_id:1,title:drama.title,headline:drama.title,cover_url:null,slot:'hero'}],groups:[{id:1,name:'Operator Collection',style:'poster3',position:0,dramas:[drama,{...drama,id:2}]}]};
  else if(p==='/v1/config')data={tabs:[],copies:{}};
  else if(p==='/v1/feed')data={session_id:'locale',page_size:1,items:[{item_id:'locale-feed',drama:{...drama,published_episode_count:12},episode:{id:11,episode_number:1,title:'Server episode',duration_seconds:60,free:false,comment_count:0},resume_seconds:0}],has_more:false,next_cursor:null,preload:{fetch_when_remaining:2}};
  else if(p==='/v1/playback')data={allowed:false,reason:'membership_required'};
  else if(p==='/v1/catalog')data={items:[drama],total:1,limit:24,offset:0};
  else if(p==='/v1/plans')data={items:plans,billing_mode:'subscription'};
  else if(p==='/v1/coin-packs')data={items:packs};
  else if(p==='/v1/billing/channels')data={items:[{code:'fixture',name:'Stripe',enabled:true}]};
  else if(p==='/v1/tasks'||p==='/v1/me/tasks')data=tasks;
  else if(p==='/v1/auth/providers')data={email:{enabled:true},google:{enabled:false,client_id:null},facebook:{enabled:false,app_id:null}};
  else if(p==='/v1/me/history'||p==='/v1/me/favorites')data={items:[],total:0,limit:20,offset:0};
  else if(p==='/v1/commerce/orders')data={items:[{order_no:'ORDER-LOCALE',product_name:'Server Order Product',product_code:'server-product',product_type:'coin_pack',amount_cents:123456,currency:'EUR',coins:12345,status:'paid',billing_mode:'one_time',paid_at:createdAt,created_at:createdAt}],total:1,has_more:false};
  else if(p==='/v1/support/meta')data={categories:[{code:'general',name_en:'Server category',name_zh:'运营分类',fields:[]}]};
  else if(p==='/v1/support/tickets')data={items:[],total:0,page:1,page_size:25};
  else if(p==='/v1/dramas/1/play')data={drama,items:[{id:11,episode_number:1,title:'Server Episode Title',duration_seconds:60,free:false,locked:true,coin_price:60,progress_seconds:0,comment_count:0,unlock:{coin_price:60}}],resume:null,entitlement:{member:false,unlocked_episode_ids:[]}};
  else {unknown.push(p);status=404;data={detail:'Unexpected locale fixture API '+p};}
  return request.respond({status,contentType:'application/json',body:JSON.stringify(data)});
});
async function route(route,selector){await page.evaluate(value=>{window.location.hash=value},route);if(selector)await page.waitForSelector(selector);}
async function waitText(selector,text){await page.waitForFunction((selector,text)=>document.querySelector(selector)?.textContent.trim()===text,{},selector,text);}
async function choose(locale){await page.click('.header-language');await page.waitForSelector('[role="menuitemradio"]');assert.equal(await page.$$eval('[role="menuitemradio"]',nodes=>nodes.length),9);await page.click('[role="menuitemradio"][lang="'+tags[locale]+'"]');await page.waitForFunction(tag=>document.documentElement.lang===tag,{},tags[locale]);}
async function formatted(locale,value,currency){return page.evaluate((tag,value,currency)=>new Intl.NumberFormat(tag,currency?{style:'currency',currency}:{}).format(value),tags[locale],value,currency);}
try{
  await page.goto(origin+'/#home',{waitUntil:'networkidle0'});
  for(const locale of languages){
    await choose(locale);
    await waitText('.consumer-header nav a:first-child',copy(locale,'Home'));
    assert.equal(await page.evaluate(()=>localStorage.getItem('jjtv_pc_locale')),locale);
    assert.equal(await page.$eval('.feature-copy h1',node=>node.textContent),drama.title);
    assert.equal(await page.$eval('.home-section-title h2',node=>node.textContent),'Operator Collection');
    check(locale+' actual menu changes home and preserves operator content');
    await route('price','.membership-card');
    await waitText('.price-heading h1',copy(locale,'Price'));
    await waitText('.membership-card:first-child .plan-price-row strong',await formatted(locale,9.99,'USD'));
    await waitText('.membership-card:last-child .plan-price-row strong',await formatted(locale,7999/1200,'USD'));
    await waitText('.membership-card:last-child .plan-billing-note',copy(locale,'Billed {price} / year',{price:await formatted(locale,79.99,'USD')}));
    await waitText('.membership-card:last-child .plan-discount-badge',copy(locale,'Save {percent}% vs monthly',{percent:await formatted(locale,66)}));
    await waitText('.coin-breakdown',copy(locale,'{base} base + {bonus} bonus coins',{base:await formatted(locale,300),bonus:await formatted(locale,30)}));
    assert.equal(await page.$eval('.membership-card:first-child h3',node=>node.textContent),locale==='zh'?'运营周套餐':'Server Weekly');
    assert.equal(await page.$$eval('.coin-pack-card',nodes=>nodes.map(node=>node.classList.contains('featured')?'featured':node.classList.contains('popular')?'popular':'neutral').join(',')),'popular,featured,neutral');
    assert.deepEqual(await page.$$eval('.coin-pack-card .purchase-badge',nodes=>nodes.map(node=>node.textContent)),['Popular','Best value','Operator label']);
    await page.screenshot({path:path.join(OUT,'locale-'+locale+'-price.png'),fullPage:false});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),locale+' desktop overflow');
    check(locale+' store copy, currency, full annual charge and coin breakdown');
    await page.evaluate(()=>localStorage.setItem('jjtv_pc_token','locale-fixture-token'));
    await page.evaluate(()=>{location.hash='account/orders'});await page.reload({waitUntil:'networkidle0'});
    await page.waitForSelector('table tbody tr');
    assert((await page.$eval('table tbody tr td:nth-child(2)',node=>node.textContent)).includes('Server Order Product'));
    await waitText('table tbody tr td:nth-child(3)',await formatted(locale,1234.56,'EUR'));
    await waitText('table tbody tr .status-tag',copy(locale,'Paid'));
    const date=await page.evaluate((tag,value)=>new Intl.DateTimeFormat(tag,{dateStyle:'medium',timeStyle:'short'}).format(new Date(value)),tags[locale],createdAt);
    await waitText('table tbody tr td:first-child small',date);
    check(locale+' order status/date/amount and immutable product label');
    await route('support?new=1','.support-main form');
    await waitText('.support-page h1',pageCopy(locale,'Help & support'));
    assert((await page.$eval('.support-main form',node=>node.textContent)).includes(pageCopy(locale,'Subject')));
    assert.equal(await page.$eval('.support-main select option',node=>node.textContent),locale==='zh'?'运营分类':'Server category');
    check(locale+' support form and server category');
    await route('watch/1','.locked-player');
    await waitText('.watch-sidebar h1',drama.title);
    assert((await page.$eval('.watch-page',node=>node.textContent)).includes(pageCopy(locale,'Unlock episode')));
    check(locale+' locked player interface preserves real paywall');
    await route('rewards','.rewards-modal');
    await waitText('.rewards-modal .modal-head h2',pageCopy(locale,'Daily tasks'));
    await page.waitForSelector('.rewards-modal progress');
    assert.equal(await page.$eval('.rewards-modal progress',node=>node.getAttribute('value')),'1000');
    assert.equal(await page.$eval('.rewards-modal progress',node=>node.position),1);
    assert.equal(await page.$eval('.rewards-modal progress',node=>node.getAttribute('aria-label')),copy(locale,'{title} progress',{title:'Server task'}));
    await page.click('.rewards-modal .modal-head button');
    await page.waitForSelector('.rewards-modal',{hidden:true});
    check(locale+' rewards modal and numeric progress attribute');
    await page.evaluate(()=>localStorage.removeItem('jjtv_pc_token'));
    await page.evaluate(()=>{location.hash='home'});await page.reload({waitUntil:'networkidle0'});
    await page.waitForSelector('.consumer-header .sign-in:not(:disabled)');
    await page.click('.consumer-header .sign-in');
    await page.waitForSelector('.auth-sheet input[type="email"]');
    await waitText('.auth-sheet .modal-head h2',pageCopy(locale,'Welcome to JJTV'));
    assert((await page.$eval('.auth-sheet',node=>node.textContent)).includes(pageCopy(locale,'Email address')));
    await page.click('.auth-sheet .modal-head button');
    assert.equal(await page.evaluate(()=>document.documentElement.lang),tags[locale]);
    assert.equal(await page.$eval('.header-language',node=>node.textContent.replace('⌄','').trim()),locale.toUpperCase());
    check(locale+' login copy and locale survives reload');console.log('Verified '+locale+' desktop pages');
  }
  await page.evaluate(()=>localStorage.setItem('jjtv_pc_token','locale-fixture-token'));
  await page.evaluate(()=>{location.hash='support?new=1'});await page.reload({waitUntil:'networkidle0'});
  await page.waitForSelector('.support-main form input');
  await page.type('.support-main form input','Keep this draft');
  await choose('fr');
  assert.equal(await page.$eval('.support-main form input',node=>node.value),'Keep this draft');
  assert((await page.$eval('.support-main form',node=>node.textContent)).includes(pageCopy('fr','Subject')));
  check('changing language preserves an unsent support draft');
  await page.focus('.header-language');await page.keyboard.press('Enter');await page.waitForSelector('[role="menuitemradio"]');
  await page.keyboard.press('Home');await page.keyboard.press('ArrowDown');await page.keyboard.press('Enter');
  await page.waitForFunction(()=>document.documentElement.lang==='de-DE');
  await page.keyboard.press('Tab');
  check('language menu supports keyboard selection');
  await page.evaluate(()=>{Object.defineProperty(navigator,'share',{value:undefined,configurable:true});Object.defineProperty(navigator,'clipboard',{value:{writeText:async()=>{}},configurable:true});});
  await route('foryou','.feed-item');await page.click('.feed-actions button:last-child');
  await waitText('.feed-notice span',copy('de','Link copied.'));
  await choose('es');await waitText('.feed-notice span',copy('es','Link copied.'));
  check('visible feed notices update immediately with the selected language');
  assert(!requests.some(request=>request.method!=='GET'&&!(request.method==='POST'&&request.path==='/v1/playback')),'Language checks may only revalidate playback; checkout, unlock and other financial writes remain forbidden');
  assert(!requests.some(request=>request.path==='/v1/catalog'&&new URLSearchParams(request.query).has('language')),'UI language must not silently filter catalog content');
  assert.deepEqual(unknown,[]);assert.deepEqual(errors,[]);
  await fs.rm(path.join(OUT,'locale-failure.png'),{force:true});await fs.rm(path.join(OUT,'locale-partial.json'),{force:true});
  await fs.writeFile(path.join(OUT,'locale-results.json'),JSON.stringify({passed:checks.length,keys:keys.length,languages,checks,apiMode:'explicit local fixtures',unknown,browserErrors:errors},null,2));
  console.log(JSON.stringify({passed:checks.length,keys:keys.length,languages:languages.length,screenshots:OUT}));
}catch(error){await fs.writeFile(path.join(OUT,'locale-partial.json'),JSON.stringify({checks,requests,unknown,browserErrors:errors},null,2));await page.screenshot({path:path.join(OUT,'locale-failure.png'),fullPage:true}).catch(()=>{});throw error;}
finally{await browser.close();await server.close();}

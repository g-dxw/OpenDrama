import { respondSiteBootstrap } from "../../tests/frontend-site-fixture.mjs";
/** Isolated visual geometry fixtures. No provider or production requests. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import puppeteer from 'puppeteer-core';
import {createServer} from 'vite';
const ROOT=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const OUT=process.env.SHOTS_DIR||path.join(os.tmpdir(),'jjtv-pc-visual-parity');await fs.mkdir(OUT,{recursive:true});await fs.rm(path.join(OUT,'visual-metrics.json'),{force:true});
const port=Number(process.env.VISUAL_PORT||5189),origin='http://127.0.0.1:'+port;
const server=await createServer({root:ROOT,server:{host:'127.0.0.1',port,strictPort:true},logLevel:'error'});await server.listen();
const browser=await puppeteer.launch({executablePath:process.env.CHROME_PATH||(process.platform==='win32'?'C:/Program Files/Google/Chrome/Application/chrome.exe':'/usr/bin/google-chrome'),headless:true,args:['--autoplay-policy=no-user-gesture-required']});
const page=await browser.newPage();const errors=[],requests=[],metrics=[],checks=[];page.on('pageerror',e=>errors.push(e.message));
const cover=(name,index)=>'data:image/svg+xml,'+encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="600" height="800"><defs><linearGradient id="g" x2="1" y2="1"><stop stop-color="'+['#61473e','#40394f','#354740'][index%3]+'"/><stop offset="1" stop-color="#171016"/></linearGradient></defs><rect width="600" height="800" fill="url(#g)"/><circle cx="310" cy="260" r="110" fill="#f6d7bd" opacity=".25"/><path d="M80 800Q110 400 310 400T570 800" fill="#0b090b" opacity=".6"/><text x="40" y="660" fill="white" font-family="Arial" font-size="38" font-weight="700">'+name+'</text><text x="42" y="710" fill="#d4bfae" font-family="Arial" font-size="18">JJTV · VISUAL FIXTURE</text></svg>');
const dramas=Array.from({length:8},(_,i)=>({id:i+1,title:['But Daddy I Love Him','The Secret Promise','Love, Once Again','A Royal Return','One More Summer','The Last Letter','Hidden Hearts','Against the Tide'][i],cover_url:cover(['But Daddy I Love Him','The Secret Promise','Love, Once Again'][i%3],i),episode_count:12,free_episode_count:2,play_count:12400+i*924,synopsis:'A chance encounter. A promise that changes everything.',tags:['Romance','Drama']}));
const groups=[{id:1,name:'Billionaire & CEO',style:'poster3',badge:'label',position:0,dramas},{id:2,name:'New Releases',style:'poster3',badge:'label',position:1,dramas:dramas.slice(1)},{id:3,name:'Most Watched',style:'rank',badge:'rank',position:2,dramas}];
const plans=['Weekly','Monthly','Yearly'].map((name,i)=>({code:name.toLowerCase(),name_en:name,price_cents:[999,1999,7999][i],currency:'USD',billing_mode:'subscription',recurring_interval:['week','month','year'][i],base_coins:100,bonus_coins:i*10,benefits:{best:i<2,items:['Access to member-exclusive dramas','Free episodes are on us','Coins included for later episodes']}}));
const packs=[50,110,600,1300].map((coins,i)=>({code:'pack_'+i,name_en:coins+' coins',price_cents:[499,999,4999,9999][i],currency:'USD',coins,base_coins:[50,100,500,1000][i],bonus_coins:[0,10,100,300][i],version_id:i+1,badge:i===3?'Best value':i===2?'Popular':''}));
const tasks={balance:0,checkin:{checked_today:false,streak:1,amount:30},items:[{code:'like_5',name:'Like 5 dramas',description:'Like 5 different dramas today',amount:20,progress:0,target:5,claimed:false,metric:'like'},{code:'favorite_5',name:'Save 5 dramas',description:'Add 5 different dramas to favorites today',amount:30,progress:0,target:5,claimed:false,metric:'favorite'},{code:'watch_8',name:'Finish 8 episodes',description:'Finish 8 different episodes today',amount:50,progress:0,target:8,claimed:false,metric:'complete'}]};
// Keep recording actual frames: a single initial canvas frame can produce a
// near-zero-duration WebM on Linux even when recording stops three seconds later.
await page.goto('about:blank');
const media=Buffer.from(await page.evaluate(async()=>{
  const canvas=document.createElement('canvas');canvas.width=360;canvas.height=640;
  const ctx=canvas.getContext('2d');let frame=0;
  const draw=()=>{ctx.fillStyle='#32232b';ctx.fillRect(0,0,360,640);ctx.fillStyle='#e3c9b9';ctx.font='24px Arial';ctx.fillText('JJTV visual fixture',60,310);ctx.fillRect(30,350,Math.min(300,++frame*8),3);};
  draw();const stream=canvas.captureStream(15);
  const recorder=new MediaRecorder(stream,{mimeType:'video/webm;codecs=vp8'}),chunks=[];
  recorder.ondataavailable=e=>chunks.push(e.data);
  const done=new Promise(resolve=>{recorder.onstop=resolve;});
  recorder.start();const timer=setInterval(draw,80);
  await new Promise(resolve=>setTimeout(resolve,3000));
  recorder.stop();clearInterval(timer);await done;
  stream.getTracks().forEach(track=>track.stop());
  return [...new Uint8Array(await new Blob(chunks,{type:'video/webm'}).arrayBuffer())];
}));
// Visual snapshots need a deterministic still, not a race against autoplay and
// onEnded. This hook is isolated to this script's exact same-origin fixture URL.
// The consumer acceptance suite independently exercises real decoding, manual
// navigation and end-of-episode advancement without this hook.
await page.evaluateOnNewDocument(()=>{
  const originalPlay=HTMLMediaElement.prototype.play;
  HTMLMediaElement.prototype.play=function(...args){
    const source=this.currentSrc||this.getAttribute('src');
    if(this instanceof HTMLVideoElement&&source){
      const url=new URL(source,location.href);
      if(url.origin===location.origin&&url.pathname==='/fixture.webm'){
        this.pause();this.dataset.visualFrozen='true';
        return Promise.resolve();
      }
    }
    return originalPlay.apply(this,args);
  };
});
await page.setRequestInterception(true);page.on('request',async request=>{if(await respondSiteBootstrap(request))return;const url=new URL(request.url());if(url.pathname==='/fixture.webm')return request.respond({status:200,contentType:'video/webm',body:media});if(!url.pathname.startsWith('/v1/')){if(url.origin===origin||url.protocol==='data:'||url.protocol==='blob:')return request.continue();return request.abort();}const p=url.pathname;let body={};try{body=JSON.parse(request.postData()||'{}');}catch{}requests.push({path:p,method:request.method(),body});let data,status=200;
if(p==='/v1/home/groups')data={groups,banners:dramas.slice(0,3).map((drama,i)=>({id:i+1,drama_id:drama.id,title:drama.title,headline:drama.title,kicker:'Featured this week',cover_url:drama.cover_url,slot:'hero'}))};
else if(p==='/v1/config')data={tabs:[{code:'discover',title_en:'Discover',position:0,locked:true,group_id:null},{code:'english',title_en:'English',position:1,locked:false,group_id:2},{code:'ranking',title_en:'Ranking',position:2,locked:false,group_id:3}],copies:{}};
else if(p==='/v1/catalog'){const q=url.searchParams.get('q')||'';const list=dramas.filter(d=>d.title.toLowerCase().includes(q.toLowerCase()));data={items:list,total:list.length,limit:24,offset:0};}
else if(p==='/v1/plans')data={items:plans,billing_mode:'subscription'};
else if(p==='/v1/coin-packs')data={items:packs};
else if(p==='/v1/billing/channels')data={items:[{code:'fixture',name:'Stripe',enabled:true}]};
else if(p==='/v1/tasks')data=tasks;
else if(p==='/v1/auth/providers')data={email:{enabled:true},google:{enabled:false,client_id:null},facebook:{enabled:false,app_id:null}};
else if(p==='/v1/feed')data={session_id:'visual',page_size:3,items:dramas.slice(0,3).map((drama,i)=>({item_id:'visual-'+i,drama:{...drama,liked:false,like_count:39,comment_count:3,published_episode_count:12,coin_price_per_episode:60},episode:{id:10+i,episode_number:1,title:'Episode 1',duration_seconds:40,free:i<2,comment_count:3},resume_seconds:0,entry_reason:'fixture',score:1})),has_more:false,next_cursor:null,preload:{fetch_when_remaining:2}};
else if(p==='/v1/playback')data={allowed:true,url:origin+'/fixture.webm',episode_id:body.episode_id,subtitles:[]};
else if(p.startsWith('/v1/playback/'))data={ok:true};
else if(/\/v1\/episodes\/\d+\/comments/.test(p))data={items:[],total:0,next_cursor:null};
else{status=404;data={detail:'Unmocked visual API '+p};}
return request.respond({status,contentType:'application/json',body:JSON.stringify(data)});});
async function go(route){await page.goto(origin+'/#'+route,{waitUntil:'networkidle0'});}
async function measure(name,selectors){const result=await page.evaluate(list=>Object.fromEntries(list.map(selector=>{const el=document.querySelector(selector);if(!el)return[selector,null];const r=el.getBoundingClientRect(),s=getComputedStyle(el);return[selector,{x:r.x,y:r.y,width:r.width,height:r.height,font:s.fontFamily,fontSize:s.fontSize,lineHeight:s.lineHeight,padding:s.padding,gap:s.gap,background:s.backgroundColor,border:s.border,radius:s.borderRadius}];})),selectors);const surface=await page.evaluate(()=>({innerWidth,clientWidth:document.documentElement.clientWidth,scrollbarWidth:innerWidth-document.documentElement.clientWidth,scrollY}));metrics.push({name,viewport:page.viewport(),surface,values:result});await fs.writeFile(path.join(OUT,'visual-metrics.partial.json'),JSON.stringify(metrics,null,2));return result;}
async function shot(name){await page.screenshot({path:path.join(OUT,name+'.png'),fullPage:false});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+1),'Overflow '+name);checks.push(name);}
try{for(const width of [1024,1280,1440,1920]){await page.setViewport({width,height:1000});await go('home');await page.waitForSelector('.hero-poster');await shot('home-'+width);await measure('home-'+width,['.consumer-header','.consumer-header nav','.feature-hero','.feature-copy h1','.hero-poster','.home-content .drama-cover','.home-channels']);await go('search');await page.waitForSelector('#drama-search-input');await shot('search-'+width);await measure('search-'+width,['.search-page-heading','#drama-search-input','.consumer-footer']);await go('price');await page.waitForSelector('.coin-pack-card');await shot('price-'+width);const price=await measure('price-'+width,['.price-page','.price-heading h1','.price-tabs','.membership-card','.coin-pack-card']);assert.equal(price['.price-page'].width,Math.min(1120,(await page.evaluate(()=>document.documentElement.clientWidth))-96));assert.equal(await page.$eval('.header-language',el=>getComputedStyle(el).display==='none'),false);const cards=await page.$$eval('.membership-card',list=>list.map(el=>({top:el.getBoundingClientRect().y,h:el.getBoundingClientRect().height,heading:el.querySelector('.plan-card-heading').getBoundingClientRect().y,price:el.querySelector('.plan-price-row').getBoundingClientRect().y,button:el.querySelector('button').getBoundingClientRect().bottom,gold:el.querySelector('button').classList.contains('plan-gold-button')})));assert.equal(new Set(cards.map(c=>c.top)).size,1);assert(cards.every(c=>c.h>=320&&c.price>c.heading));assert(Math.max(...cards.map(c=>c.button))-Math.min(...cards.map(c=>c.button))<1);assert(cards[0].gold&&!cards[2].gold);await go('account');await page.waitForSelector('.member-guest-state');await shot('me-'+width);await measure('me-'+width,['.member-page','.member-sidebar','.library-tabs','.member-guest-state']);await go('rewards');await page.waitForSelector('.rewards-modal .task-row');await shot('rewards-'+width);await measure('rewards-'+width,['.rewards-modal','.rewards-modal .modal-head','.task-checkin','.task-row:not(.task-checkin)']);await go('account');await page.waitForFunction(()=>!document.querySelector('.consumer-header .sign-in')?.disabled);await page.click('.consumer-header .sign-in');await page.waitForSelector('.auth-sheet input');await shot('login-'+width);await measure('login-'+width,['.auth-sheet','.auth-sheet input','.auth-sheet .button.primary']);await page.click('.auth-sheet [aria-label="Close dialog"]');await page.waitForSelector('.auth-sheet',{hidden:true});await go('foryou');await page.waitForFunction(()=>{const video=document.querySelector('.feed-item[data-index="0"] video');return video?.dataset.visualFrozen==='true'&&video.paused&&video.readyState>=2;});if(width===1440)await page.evaluate(()=>new Promise(resolve=>setTimeout(resolve,3500)));assert.equal(await page.evaluate(()=>window.scrollY),0);assert.equal(await page.$eval('.feed-scroll',el=>el.scrollTop),0);await shot('for-you-'+width);const feed=await measure('for-you-'+width,['.feed-item','.feed-item video','.feed-settings-toggle']);assert.equal(feed['.feed-item'].width,460);assert.equal(feed['.feed-item'].height,936);assert(Math.abs(feed['.feed-item'].y-64)<1);assert.equal(await page.$eval('.feed-item .video-options',el=>getComputedStyle(el).display),'none');await page.click('.feed-item .feed-settings-toggle');await page.waitForFunction(()=>getComputedStyle(document.querySelector('.feed-item .video-options')).display==='flex');await page.click('.feed-item [aria-label="Unmute"]');assert.equal(await page.$eval('.feed-item video',video=>video.muted),false);await shot('for-you-settings-'+width);}
assert.deepEqual(errors,[]);await fs.writeFile(path.join(OUT,'visual-metrics.json'),JSON.stringify({apiMode:'explicit local fixtures',checks,metrics},null,2));await fs.rm(path.join(OUT,'visual-failure.png'),{force:true});await fs.rm(path.join(OUT,'visual-metrics.partial.json'),{force:true});console.log(JSON.stringify({passed:checks.length,screenshots:OUT}));}
catch(error){await page.screenshot({path:path.join(OUT,'visual-failure.png'),fullPage:true}).catch(()=>{});throw error;}
finally{await browser.close();await server.close();}

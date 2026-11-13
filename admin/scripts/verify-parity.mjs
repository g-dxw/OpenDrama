/** Local browser contract acceptance. All API responses below are explicit fixtures. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer-core';
import { createServer } from 'vite';
const ROOT=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const OUT=process.env.SHOTS_DIR||path.join(os.tmpdir(),'jjtv-admin-parity-acceptance');
const port=Number(process.env.ADMIN_ACCEPT_PORT||5186);const origin='http://127.0.0.1:'+port;
await fs.mkdir(OUT,{recursive:true});
const server=await createServer({root:ROOT,server:{host:'127.0.0.1',port,strictPort:true},logLevel:'error'});await server.listen();
const executablePath=process.env.CHROME_PATH||(process.platform==='win32'?'C:/Program Files/Google/Chrome/Application/chrome.exe':process.platform==='darwin'?'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome':'/usr/bin/google-chrome');
const browser=await puppeteer.launch({executablePath,headless:true,args:['--no-first-run','--no-default-browser-check']});
const page=await browser.newPage();const errors=[];const requests=[];let readOnly=false;const checks=[];
page.on('pageerror',error=>errors.push(error.message));
const menus=['/login-settings','/stripe-accounts','/coin-packs','/coin-ledger','/attribution','/support/workbench','/support/queue','/support/reports','/support/settings','/plans','/finance'];
const permissions=['login_settings.view','login_settings.manage','stripe.view','stripe.manage','coin_packs.view','coin_packs.manage','coins.view','coins.export','attribution.view','attribution.manage','support.view','support.queue','support.reports','support.settings','support.manage','plans.view','plans.manage','finance.view','finance.refund'];
const identity=()=>({account_scope:'tenant',session_kind:'normal',is_platform_admin:false,is_tenant_admin:!readOnly,user:{id:1,username:'fixture-admin',display_name:readOnly?'只读测试员工':'验收测试员工',role:'admin',must_change_password:false},tenant:{id:'jjtv',code:'jjtv',name:'JJTV 自营',kind:'native',status:'active'},role_name:readOnly?'只读员工':'租户管理员',permissions:readOnly?permissions.filter(p=>p.endsWith('.view')||p==='support.queue'||p==='support.reports'):permissions,menu_keys:readOnly?menus.filter(p=>p!=='/support/settings'):menus,enabled_modules:['support','attribution','stripe','coin_packs','coins','login_settings','plans','finance']});
const config={calendar:{timezone:'Asia/Shanghai',weekdays:[0,1,2,3,4],start:'10:00',end:'19:00',holidays:['2026-12-25']},sla:Object.fromEntries(['low','normal','high','urgent'].map(p=>[p,{first_response_minutes:60,resolution_minutes:480}])),teams:[{code:'default',name:'客户服务组',members:[1],max_concurrent:20}],default_team:'default',assignment:'least_busy',categories:[{code:'general',name_en:'General',name_zh:'一般问题',fields:[]}],rules:[]};
const ticket={id:1,number:'TK-FIXTURE',subject:'Fixture support ticket',category:'general',status:'open',priority:'normal',email:'viewer@example.com',assignee_id:null,assignee_name:null,version:1,order_no:null,fields:{},created_at:'2026-11-14T00:00:00Z',updated_at:'2026-11-14T00:00:00Z',sla:{overdue:false,resolution_minutes:15,resolution_target:480},csat_score:null};
const messages=[{id:1,author_type:'user',body:'Please help with my membership.',internal:false,created_at:'2026-11-14T00:00:00Z'}];
const detail=()=>({ticket,messages,customer:{membership:{active:false},coins:120,orders:[]},events:[]});
let supportVersion=0;
const pixels=[{id:1,name:'Fixture Meta pixel',platform:'meta',pixel_id:'123456789',enabled:false,delivery_mode:'test',test_event_code:'TEST-FIXTURE',token_configured:true,token_masked:'••••••••',expired:false}];
const sources=[{id:1,name:'Fixture source',pixel_id:1,account_id:'ad-fixture',timezone:'Asia/Shanghai',currency:'USD',enabled:true}];
const plans=[{id:1,pid:'fixture-pid',name:'Fixture plan',source_id:1,pixel_id:1,landing_path:'/',region:'US',enabled:true,tracking_url:'https://jjtv.8jj.net/?pid=fixture-pid',visits:1,registrations:1,paying_users:0,paid_orders:0,revenue_cents:{}}];
const events=[{id:1,pixel_id:1,plan_id:1,event_id:'registration:42',kind:'registration',status:'pending',attempts:0,test:false,last_error:'',response:{},created_at:'2026-11-14T00:00:00Z'}];
await page.setRequestInterception(true);
page.on('request',async req=>{const url=new URL(req.url());const p=url.pathname;const isApi=p.startsWith('/admin/v1/')||p.startsWith('/tenant/v1/');if(!isApi){if(url.origin===origin||url.protocol==='data:'||url.protocol==='about:')await req.continue();else await req.abort();return;}
 let body={};try{body=JSON.parse(req.postData()||'{}');}catch{}requests.push({path:p,method:req.method(),headers:req.headers(),body});let result;let status=200;
 if(p==='/tenant/v1/context')result=identity();
 else if(p==='/admin/v1/login/settings')result={email_enabled:true,email_from:'JJTV <test@example.com>',google_enabled:false,google_client_ids:[],facebook_enabled:false,facebook_app_id:'',email_api_key_configured:true,facebook_app_secret_configured:false,source:'database',updated_at:null};
 else if(p==='/admin/v1/stripe/accounts')result={items:[{code:'fixture',name:'Stripe fixture account',status:'draft',is_default:true,secret_key_configured:true,webhook_secret_configured:true,livemode:false}]};
 else if(p==='/admin/v1/stripe/prices')result={items:[]};
 else if(p==='/admin/v1/coin-packs')result={items:[{code:'starter',name_en:'Fixture coin pack',name_zh:'测试金币包',status:'active',price_cents:499,base_coins:500,bonus_coins:50,version:1,sort_order:0,badge:''}]};
 else if(p==='/admin/v1/coins/ladder')result={base_coins_per_usd:100,items:[{code:'starter',coins_per_usd:100.2,bonus_ratio_pct:10}]};
 else if(p==='/admin/v1/coins/summary')result={balance:120,items:[{biz_type:'unlock',amount:-60},{biz_type:'purchase',amount:180}]};
 else if(p==='/admin/v1/coins/records')result={items:[{id:1,user_id:42,biz_type:'unlock',amount:-60,balance_after:120,note:'Fixture unlock',created_at:'2026-11-14T00:00:00Z'}],has_more:false};
 else if(p==='/admin/v1/coins/stats')result={items:[{day:'2026-11-14',amount:120,count:2}]};
 else if(p==='/admin/v1/commerce/plans')result={billing_mode:'one_time',items:[{code:'weekly',name_en:'Weekly fixture',status:'active',price_cents:499,base_coins:100,bonus_coins:10,version:1,benefits:{items:['Fixture membership']}}]};
 else if(p==='/admin/v1/commerce/orders')result={items:[{order_no:'FIXTURE-ORDER',product_name:'Fixture coins',product_type:'coin_pack',account_code:'fixture',amount_cents:499,currency:'USD',status:'paid',paid_at:'2026-11-14T00:00:00Z'}],has_more:false};
 else if(p==='/admin/v1/support/agents')result={items:[{id:1,name:'Fixture agent'}]};
 else if(p==='/admin/v1/support/settings'){if(req.method()==='POST'){Object.assign(config,body.config);supportVersion++;}result={config,version:supportVersion};}
 else if(p==='/admin/v1/support/reports')result={days:7,new_tickets:1,resolved_tickets:0,backlog:1,csat_average:null,csat_responses:0,first_response_sla_rate:null,resolution_sla_rate:null,first_response_p50:null,first_response_p90:null,resolution_p50:null,resolution_p90:null,reopen_7d_rate:0,by_status:{open:1},by_category:{general:1},by_assignee:{unassigned:1}};
 else if(p==='/admin/v1/support/tickets')result={items:[ticket],total:1,page:1,page_size:25};
 else if(p==='/admin/v1/support/tickets/TK-FIXTURE/messages'){messages.push({id:messages.length+1,author_type:'admin',body:body.body,internal:body.internal,created_at:'2026-11-14T01:00:00Z'});ticket.version++;result=detail();}
 else if(p==='/admin/v1/support/tickets/TK-FIXTURE'){if(req.method()==='PATCH')Object.assign(ticket,body,{version:ticket.version+1});result=detail();}
 else if(p==='/admin/v1/attr/pixels')result={items:pixels};
 else if(p==='/admin/v1/attr/sources')result={items:sources};
 else if(p==='/admin/v1/attr/plans')result={items:plans};
 else if(p==='/admin/v1/attr/events')result={items:events,total:events.length,page:1,page_size:50};
 else if(p==='/admin/v1/attr/pixels/1/test'){result={id:2,event_id:'test:fixture',status:'pending',test:true};events.unshift({...result,pixel_id:1,kind:'test',attempts:0,last_error:'',response:{},created_at:'2026-11-14T01:00:00Z'});}
 else {status=404;result={detail:'Unmocked API '+req.method()+' '+p};}
 await req.respond({status,contentType:'application/json',body:JSON.stringify(result)});
});
await page.evaluateOnNewDocument(()=>localStorage.setItem('jjtv_tenant_token:jjtv','fixture-admin-token'));
async function text(value){await page.waitForFunction(v=>document.body.innerText.includes(v),{},value);}
async function click(value){await page.evaluate(v=>{const target=[...document.querySelectorAll('button,a,[role="tab"]')].find(el=>el.textContent.trim()===v);if(!target)throw new Error('Missing control '+v);target.click();},value);}
async function go(route){await page.goto(origin+'/admin/t/jjtv'+route,{waitUntil:'networkidle0'});}
async function noOverflow(label){assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'horizontal overflow: '+label);}
const pass=label=>{checks.push(label);console.log('PASS '+label);};
try{
 const pages=[['/login-settings','登录配置'],['/stripe-accounts','支付通道'],['/coin-packs','金币商城'],['/coin-ledger','金币消耗'],['/attribution','投放归因'],['/support/workbench','工单工作台'],['/support/queue','工单队列'],['/support/reports','服务质量'],['/support/settings','工单设置'],['/plans','会员套餐'],['/finance','财务订单']];
 for(const [route,title] of pages){await go(route);await text(title);for(const width of [1280,1440,1920]){await page.setViewport({width,height:1000});await noOverflow(route+' '+width);await page.screenshot({path:path.join(OUT,route.slice(1).replaceAll('/','-')+'-'+width+'.png'),fullPage:true});}pass(title+' renders at 1280/1440/1920');}
 await page.setViewport({width:1440,height:1000});await go('/support/workbench');await text('Fixture support ticket');await click('Fixture support ticket');await text('Please help with my membership.');await page.type('textarea[aria-label="回复内容"]','This is a private fixture note.');await page.evaluate(()=>{const label=[...document.querySelectorAll('label')].find(el=>el.textContent.includes('仅员工可见'));label.click();});await click('保存备注');await text('This is a private fixture note.');const reply=requests.find(r=>r.path.endsWith('/TK-FIXTURE/messages'));assert.equal(reply.body.internal,true);assert.equal(reply.headers.authorization,'Bearer fixture-admin-token');assert.equal(reply.headers['x-jjtv-tenant'],'jjtv');pass('staff internal note uses authenticated tenant-scoped action');await page.screenshot({path:path.join(OUT,'support-internal-note-1440.png'),fullPage:true});
 await go('/support/settings');await text('客服团队');await click('保存配置');await page.waitForFunction(()=>document.body.innerText.includes('工单设置已保存'));const saved=requests.filter(r=>r.path.endsWith('/support/settings')&&r.method==='POST').at(-1);assert.equal(saved.body.config.calendar.start,'10:00');assert.deepEqual(saved.body.config.calendar.holidays,['2026-12-25']);assert.equal(saved.body.config.sla.normal.resolution_minutes,480);pass('saving one settings tab retains calendar and SLA snapshots');
 await go('/attribution');await text('Fixture Meta pixel');await click('发送测试');await text('回传日志');await text('测试事件已进入回传队列');assert(requests.some(r=>r.path==='/admin/v1/attr/pixels/1/test'&&r.method==='POST'));pass('pixel test queues an explicit test event without claiming live success');
 readOnly=true;await go('/attribution');await text('Fixture Meta pixel');assert.equal(await page.evaluate(()=>[...document.querySelectorAll('button')].some(el=>el.textContent.trim()==='添加像素')),false);assert.equal(await page.evaluate(()=>[...document.querySelectorAll('[role="switch"]')].every(el=>el.disabled||el.getAttribute('aria-disabled')==='true')),true);pass('read-only attribution hides writes and disables toggles');
 await go('/support/workbench');await text('Fixture support ticket');await click('Fixture support ticket');await text('Please help with my membership.');assert.equal(await page.$('textarea[aria-label="回复内容"]'),null);pass('read-only support has no reply or assignment actions');
 const before=requests.filter(r=>r.path==='/admin/v1/support/settings').length;await go('/support/settings');assert(!new URL(page.url()).pathname.endsWith('/support/settings'));assert.equal(requests.filter(r=>r.path==='/admin/v1/support/settings').length,before);pass('direct ungranted menu URL cannot fetch support settings');
 assert.deepEqual(errors,[]);pass('no uncaught browser errors');await fs.writeFile(path.join(OUT,'results.json'),JSON.stringify({checks,screenshots:OUT,apiMode:'explicit local fixtures'},null,2));console.log(JSON.stringify({passed:checks.length,screenshots:OUT}));
}finally{await browser.close();await server.close();}

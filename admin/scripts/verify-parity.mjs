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
const menus=['/dramas','/login-settings','/stripe-accounts','/coin-packs','/coin-ledger','/attribution','/support/workbench','/support/queue','/support/reports','/support/settings','/plans','/finance'];
const permissions=['dramas.view','dramas.edit','dramas.upload','dramas.pricing','login_settings.view','login_settings.manage','stripe.view','stripe.manage','coin_packs.view','coin_packs.manage','coins.view','coins.export','attribution.view','attribution.manage','support.view','support.queue','support.reports','support.settings','support.manage','plans.view','plans.manage','finance.view','finance.refund'];
const identity=()=>({account_scope:'tenant',session_kind:'normal',is_platform_admin:false,is_tenant_admin:!readOnly,user:{id:1,username:'fixture-admin',display_name:readOnly?'只读测试员工':'验收测试员工',role:'admin',must_change_password:false},tenant:{id:'jjtv',code:'jjtv',name:'JJTV 自营',kind:'native',status:'active'},role_name:readOnly?'只读员工':'租户管理员',permissions:readOnly?permissions.filter(p=>p.endsWith('.view')||p==='support.queue'||p==='support.reports'):permissions,menu_keys:readOnly?menus.filter(p=>p!=='/support/settings'):menus,enabled_modules:['content','dramas','support','attribution','stripe','coin_packs','coins','login_settings','plans','finance']});
const config={calendar:{timezone:'Asia/Shanghai',weekdays:[0,1,2,3,4],start:'10:00',end:'19:00',holidays:['2026-12-25']},sla:Object.fromEntries(['low','normal','high','urgent'].map(p=>[p,{first_response_minutes:60,resolution_minutes:480}])),teams:[{code:'default',name:'客户服务组',members:[1],max_concurrent:20}],default_team:'default',assignment:'least_busy',categories:[{code:'general',name_en:'General',name_zh:'一般问题',fields:[]}],rules:[]};
const ticket={id:1,number:'TK-FIXTURE',subject:'Fixture support ticket',category:'general',status:'open',priority:'normal',email:'viewer@example.com',assignee_id:1,assignee_name:'Fixture agent',version:1,order_no:null,fields:{},created_at:'2026-11-14T00:00:00Z',updated_at:'2026-11-14T00:00:00Z',sla:{overdue:false,resolution_minutes:15,resolution_target:480},csat_score:null};
const unassignedTicket={...ticket,id:2,number:'TK-UNASSIGNED',subject:'Unassigned playback question',email:'other@example.com',assignee_id:null,assignee_name:null,priority:'urgent',sla:{overdue:true,resolution_minutes:900,resolution_target:480}};
const supportTickets=[ticket,unassignedTicket];
const episode={id:901,episode_number:7,title:'Fixture episode',status:'not_uploaded',duration_seconds:0,bunny_video_id:'',coin_price:null};let subtitleTracks=[];

const messages=[{id:1,author_type:'user',body:'Please help with my membership.',internal:false,created_at:'2026-11-14T00:00:00Z'}];
const detail=()=>({ticket,messages,customer:{membership:{active:false},coins:120,orders:[]},events:[]});
let supportVersion=0;let rejectSourceCreate=true;
const pixels=[{id:1,name:'Fixture Meta pixel',platform:'meta',pixel_id:'123456789',enabled:false,delivery_mode:'test',test_event_code:'TEST-FIXTURE',token_configured:true,token_masked:'••••••••',expired:false}];
const sources=[{id:1,name:'Fixture source',pixel_id:1,account_id:'ad-fixture',timezone:'Asia/Shanghai',currency:'USD',enabled:true}];
const plans=[{id:1,pid:'fixture-pid',name:'Fixture plan',source_id:1,pixel_id:1,landing_path:'/',region:'US',enabled:true,tracking_url:'https://jjtv.8jj.net/?pid=fixture-pid',visits:1,registrations:1,paying_users:0,paid_orders:0,revenue_cents:{}}];
const events=[{id:1,pixel_id:1,plan_id:1,event_id:'registration:42',kind:'registration',status:'pending',attempts:0,test:false,last_error:'',response:{},created_at:'2026-11-14T00:00:00Z'}];
await page.setRequestInterception(true);
page.on('request',async req=>{const url=new URL(req.url());const p=url.pathname;const isApi=p.startsWith('/admin/v1/')||p.startsWith('/tenant/v1/');if(!isApi){if(url.origin===origin||url.protocol==='data:'||url.protocol==='about:')await req.continue();else await req.abort();return;}
 let body={};try{body=JSON.parse(req.postData()||'{}');}catch{}requests.push({path:p,method:req.method(),headers:req.headers(),body});let result;let status=200;
 if(p==='/tenant/v1/context')result=identity();
 else if(p==='/admin/v1/login/test/email')result={ok:true};
 else if(p==='/admin/v1/login/settings')result={email_enabled:true,email_from:'JJTV <test@example.com>',google_enabled:false,google_client_ids:[],facebook_enabled:false,facebook_app_id:'',email_api_key_configured:true,facebook_app_secret_configured:false,source:'database',updated_at:null};
 else if(p==='/admin/v1/stripe/accounts')result={items:[{code:'fixture',name:'Stripe fixture account',status:'draft',is_default:true,secret_key_configured:true,webhook_secret_configured:true,livemode:false}]};
 else if(p==='/admin/v1/stripe/prices')result={items:[]};
 else if(p==='/admin/v1/coin-packs')result={items:[{code:'starter',name_en:'Fixture coin pack',name_zh:'测试金币包',status:'active',price_cents:499,base_coins:500,bonus_coins:50,version:1,sort_order:0,badge:''}]};
 else if(p==='/admin/v1/coins/ladder')result={base_coins_per_usd:100,items:[{code:'starter',coins_per_usd:100.2,bonus_ratio_pct:10}]};
 else if(p==='/admin/v1/coins/summary')result={balance:120,items:[{biz_type:'unlock',amount:-60},{biz_type:'purchase',amount:180}]};
 else if(p==='/admin/v1/coins/records')result={items:[{id:1,user_id:42,biz_type:'unlock',amount:-60,balance_after:120,note:'Fixture unlock',created_at:'2026-11-14T00:00:00Z'}],has_more:false};
 else if(p==='/admin/v1/coins/stats')result={items:[{day:'2026-11-14',amount:120,count:2}]};
 else if(p==='/admin/v1/commerce/plans')result={billing_mode:'one_time',items:[{code:'weekly',name_en:'Weekly fixture',status:'active',price_cents:499,base_coins:100,bonus_coins:10,version:1,benefits:{items:['Fixture membership']}}]};
 else if(p==='/admin/v1/commerce/orders')result={items:[{order_no:'FIXTURE-ORDER',product_name:'Fixture coins',product_code:'starter',product_type:'coin_pack',billing_mode:'one_time',account_code:'fixture',amount_cents:499,currency:'USD',status:'paid',paid_at:'2026-11-14T00:00:00Z',created_at:'2026-11-14T00:00:00Z',coins:550,user_id:42,user:{id:42,email:'viewer@example.com',display_name:'Fixture viewer',public_id:'viewer42'},external_txn_id:'cs_test_fixture'}],has_more:false,total:1,summary:{orders:1,paid_orders:1,gross_cents:499,paid_cents:499,refunded_orders:0,refunded_cents:0,net_cents:499,pending_orders:0,pending_cents:0,pending_refund_orders:0,review_orders:0,currency:'USD',currencies:[{currency:'USD',orders:1,gross_cents:499,refunded_cents:0,net_cents:499}]}};
 else if(p==='/admin/v1/commerce/orders/FIXTURE-ORDER')result={order:{order_no:'FIXTURE-ORDER',product_name:'Fixture coins',product_code:'starter',product_type:'coin_pack',billing_mode:'one_time',account_code:'fixture',amount_cents:499,currency:'USD',status:'paid',paid_at:'2026-11-14T00:00:00Z',created_at:'2026-11-14T00:00:00Z',coins:550,user_id:42,user:{id:42,email:'viewer@example.com',display_name:'Fixture viewer',public_id:'viewer42'},external_txn_id:'cs_test_fixture',snapshot:{code:'starter',name:'Fixture coins',base_coins:500,bonus_coins:50},payment_intent_id:'pi_test_fixture',invoice_id:null,subscription_ref:null},customer:{id:42,email:'viewer@example.com',display_name:'Fixture viewer',public_id:'viewer42'},refund:null,entitlement_sources:{coins:{original:550,remaining:550,reserved:0},membership:null},refund_eligibility:{eligible:true,reason:'No consumption recorded'}};
 else if(p==='/admin/v1/dramas/101')result={id:101,title:'Fixture drama',synopsis:'Fixture drama description',cover_url:'',status:'draft',free_episode_count:2,coin_price_per_episode:60,access_mode:'members',member_pays_coins:false,non_member_free_episodes:null,language:'en',tags:[],episode_count:1};
 else if(p==='/admin/v1/dramas/101/episodes')result={items:[episode],total:1};
 else if(p==='/admin/v1/episodes/901/pricing'){episode.coin_price=body.coin_price;result={id:901,coin_price:episode.coin_price};}
 else if(p==='/admin/v1/episodes/901/subtitles'){if(req.method()==='POST'){subtitleTracks=[{language:'en',label:'Fixture English',url:'subtitles/fixture-en.vtt',is_default:true}];result={ok:true};}else result={items:subtitleTracks,legacy_default_url:subtitleTracks.length?'subtitles/fixture-en.vtt':'subtitles/legacy-fixture.vtt'};}
 else if(p==='/admin/v1/support/agents')result={items:[{id:1,name:'Fixture agent'}],teams:[{code:'default',name:'客户服务组'}]};
 else if(p==='/admin/v1/support/settings'){if(req.method()==='POST'){Object.assign(config,body.config);supportVersion++;}result={config,version:supportVersion};}
 else if(p==='/admin/v1/support/reports')result={days:7,new_tickets:1,resolved_tickets:0,backlog:1,csat_average:null,csat_responses:0,first_response_sla_rate:null,resolution_sla_rate:null,first_response_p50:null,first_response_p90:null,resolution_p50:null,resolution_p90:null,reopen_7d_rate:0,by_status:{open:1},by_category:{general:1},by_assignee:{unassigned:1}};
 else if(p==='/admin/v1/support/tickets'){const qp=url.searchParams;let list=[...supportTickets];const queue=qp.get('queue');if(queue==='mine')list=list.filter(x=>x.assignee_id===1&&!['closed','resolved','merged'].includes(x.status));else if(queue==='unassigned')list=list.filter(x=>!x.assignee_id&&!['closed','resolved','merged'].includes(x.status));else if(queue==='overdue')list=list.filter(x=>x.sla.overdue);else if(queue==='waiting_customer')list=list.filter(x=>x.status==='waiting_customer');else if(queue==='active')list=list.filter(x=>!['closed','resolved','merged'].includes(x.status));if(qp.get('status'))list=list.filter(x=>x.status===qp.get('status'));if(qp.get('priority'))list=list.filter(x=>x.priority===qp.get('priority'));if(qp.get('assignee_id'))list=list.filter(x=>x.assignee_id===Number(qp.get('assignee_id')));if(qp.get('q'))list=list.filter(x=>(x.subject+' '+x.number+' '+x.email).toLowerCase().includes(qp.get('q').toLowerCase()));const current=Number(qp.get('page')||1),size=Number(qp.get('page_size')||25);result={items:list.slice((current-1)*size,current*size),total:list.length,page:current,page_size:size,counts:{active:2,mine:1,unassigned:1,overdue:1,waiting_customer:0}};}
 else if(p==='/admin/v1/support/tickets/TK-FIXTURE/messages'){messages.push({id:messages.length+1,author_type:'admin',body:body.body,internal:body.internal,created_at:'2026-11-14T01:00:00Z'});ticket.version++;result=detail();}
 else if(p==='/admin/v1/support/tickets/TK-FIXTURE'){if(req.method()==='PATCH')Object.assign(ticket,body,{version:ticket.version+1});result=detail();}
 else if(p==='/admin/v1/attr/pixels'){if(req.method()==='POST'){const item={id:pixels.length+1,...body,token_configured:!!body.token,token_masked:body.token?'••••••••':''};pixels.push(item);result=item;}else result={items:pixels};}
 else if(p==='/admin/v1/attr/sources'){if(req.method()==='POST'){if(rejectSourceCreate){rejectSourceCreate=false;status=400;result={detail:'Fixture validation: source was not saved'};}else{const item={id:sources.length+1,...body};sources.push(item);result=item;}}else result={items:sources};}
 else if(p==='/admin/v1/attr/plans'){if(req.method()==='POST'){const item={id:plans.length+1,pid:'new-fixture-pid',tracking_url:'https://jjtv.8jj.net/?pid=new-fixture-pid',visits:0,registrations:0,paying_users:0,paid_orders:0,revenue_cents:{},...body};plans.push(item);result=item;}else result={items:plans};}
 else if(p==='/admin/v1/attr/events')result={items:events,total:events.length,page:1,page_size:50};
 else if(p==='/admin/v1/attr/pixels/1/test'){result={id:2,event_id:'test:fixture',status:'pending',test:true};events.unshift({...result,pixel_id:1,kind:'test',attempts:0,last_error:'',response:{},created_at:'2026-11-14T01:00:00Z'});}
 else {status=404;result={detail:'Unmocked API '+req.method()+' '+p};}
 await req.respond({status,contentType:'application/json',body:JSON.stringify(result)});
});
await page.evaluateOnNewDocument(()=>localStorage.setItem('jjtv_tenant_token:jjtv','fixture-admin-token'));
async function text(value){await page.waitForFunction(v=>document.body.innerText.includes(v),{},value);}
async function click(value){await page.waitForFunction(v=>[...document.querySelectorAll('button,a,[role="tab"]')].some(el=>(el.textContent.trim()===v||el.getAttribute('aria-label')===v)&&el.getClientRects().length&&!el.disabled&&el.getAttribute('aria-disabled')!=='true'),{},value);await page.evaluate(v=>{const target=[...document.querySelectorAll('button,a,[role="tab"]')].find(el=>(el.textContent.trim()===v||el.getAttribute('aria-label')===v)&&el.getClientRects().length&&!el.disabled&&el.getAttribute('aria-disabled')!=='true');if(!target)throw new Error('Missing enabled control '+v);target.click();},value);}
async function go(route){await page.goto(origin+'/admin/t/jjtv'+route,{waitUntil:'networkidle0'});}
async function noOverflow(label){assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'horizontal overflow: '+label);}
const pass=label=>{checks.push(label);console.log('PASS '+label);};
const dialog='.ant-modal-wrap:not([style*="display: none"])';
async function fillDialog(id,value){const selector=dialog+' #'+id;await page.waitForSelector(selector,{visible:true});await page.click(selector,{clickCount:3});await page.keyboard.press('Backspace');await page.type(selector,value);}
async function chooseDialog(id,label){await page.click(dialog+' #'+id);await page.waitForSelector('.ant-select-dropdown:not(.ant-select-dropdown-hidden)',{visible:true});await page.evaluate(v=>{const option=[...document.querySelectorAll('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option')].find(x=>x.textContent.trim()===v);if(!option)throw new Error('Missing option '+v);option.click();},label);}
async function confirmDialog(){await page.evaluate(q=>{const modal=document.querySelector(q);const button=[...modal.querySelectorAll('.ant-modal-footer button')].find(x=>x.classList.contains('ant-btn-primary'));button.click();},dialog);}
async function dialogClosed(){await page.waitForFunction(q=>!document.querySelector(q),{},dialog);}

// Separate browser contexts keep these tenant fixtures out of the JJTV request ledger.
async function storefrontFixture({ code, web_url = '', h5_url = '', platform = false }, inspect) {
 const context = await browser.createBrowserContext();
 const fixturePage = await context.newPage(); const seen = []; const fixtureErrors = []; const blocked = [];
 const token = 'storefront-fixture-token:' + code;
 const tenant = { id: 'tenant-' + code, code, name: 'Fixture international operating platform ' + code, kind: 'native', status: 'active', web_url, h5_url };
 const fixtureIdentity = { account_scope: platform ? 'platform' : 'tenant', session_kind: 'normal', is_platform_admin: platform, is_tenant_admin: false,
  user: { id: 7, username: 'fixture-staff', display_name: 'Fixture employee', role: platform ? 'admin' : 'editor', account_scope: platform ? 'platform' : 'tenant', tenant_id: platform ? null : tenant.id, must_change_password: false },
  tenant: platform ? null : tenant, role_name: '普通员工', permissions: [], menu_keys: [], enabled_modules: [] };
 fixturePage.on('pageerror', error => fixtureErrors.push(error.message));
 await fixturePage.setRequestInterception(true);
 fixturePage.on('request', async request => {
  const url = new URL(request.url()); const isApi = /^\/(?:admin|tenant|platform)\/v1\//.test(url.pathname);
  if (!isApi) { if (url.origin === origin || ['data:', 'about:'].includes(url.protocol)) await request.continue(); else { blocked.push(url.href); await request.abort(); } return; }
  seen.push({ path: url.pathname, method: request.method(), authorization: request.headers().authorization });
  const contextPath = platform ? '/platform/v1/context' : '/tenant/v1/context';
  const known = request.method() === 'GET' && (url.pathname === contextPath || platform && url.pathname === '/platform/v1/tenants');
  await request.respond({ status: known ? 200 : 500, contentType: 'application/json', body: JSON.stringify(url.pathname === contextPath ? fixtureIdentity : known ? [] : { detail: 'Unexpected storefront fixture API' }) });
 });
 await fixturePage.evaluateOnNewDocument(({ key, token }) => localStorage.setItem(key, token), { key: platform ? 'jjtv_platform_token' : 'jjtv_tenant_token:' + code, token });
 try {
  await fixturePage.setViewport({ width: 1440, height: 1000 });
  await fixturePage.goto(origin + (platform ? '/admin/platform/tenants' : '/admin/t/' + code + '/'), { waitUntil: 'networkidle0' });
  await fixturePage.waitForSelector('.backoffice-header', { visible: true });
  await inspect(fixturePage, token);
  assert.deepEqual(fixtureErrors, [], 'No storefront fixture browser errors');
  assert.deepEqual(blocked, [], 'Rendering a storefront link must not navigate or contact its destination');
  assert(seen.length > 0 && seen.every(call => call.method === 'GET' && call.authorization === 'Bearer ' + token && (call.path === (platform ? '/platform/v1/context' : '/tenant/v1/context') || platform && call.path === '/platform/v1/tenants')), 'Header uses only its authenticated context, without requesting privileged site settings');
 } catch (error) {
  await fixturePage.screenshot({ path: path.join(OUT, 'storefront-failure-' + code + '.png'), fullPage: true }).catch(() => {});
  await fs.writeFile(path.join(OUT, 'storefront-failure-' + code + '.html'), await fixturePage.content()).catch(() => {});
  throw error;
 } finally { await context.close(); }
}
async function verifyStorefrontNavigation() {
 for (const fixture of [
  { code: 'store-us', web_url: 'https://us.example.com/watch', h5_url: 'https://m-us.example.com/', expected: 'https://us.example.com/watch' },
  { code: 'store-jp', web_url: 'https://operator:fixture-password@wrong.example.com/', h5_url: 'https://jp.example.com/mobile', expected: 'https://jp.example.com/mobile' },
 ]) await storefrontFixture(fixture, async (fixturePage, token) => {
  for (const width of [1440, 375]) {
   await fixturePage.setViewport({ width, height: width === 375 ? 844 : 1000 });
   const link = await fixturePage.$eval('.backoffice-header [aria-label="访问前台"]', element => {
    const rect = element.getBoundingClientRect(), header = element.closest('.backoffice-header').getBoundingClientRect();
    const location = document.querySelector('.app-header-location').getBoundingClientRect();
    return { tag: element.tagName, href: element.getAttribute('href'), target: element.getAttribute('target'), rel: element.getAttribute('rel') || '', visible: !!element.getClientRects().length, left: rect.left, right: rect.right, headerRight: header.right, locationRight: location.right, headerOverflow: element.closest('.backoffice-header').scrollWidth > element.closest('.backoffice-header').clientWidth + 1, pageOverflow: document.documentElement.scrollWidth > innerWidth + 1 };
   });
   assert.equal(link.tag, 'A'); assert(link.visible); assert.equal(new URL(link.href).href, new URL(fixture.expected).href);
   assert.equal(link.target, '_blank'); assert(link.rel.split(/\s+/).includes('noopener')); assert(link.rel.split(/\s+/).includes('noreferrer')); assert(!link.href.includes(token));
   assert(!link.headerOverflow && !link.pageOverflow && link.locationRight <= link.left + 1 && link.right <= link.headerRight + 1, 'Storefront action stays in the header without overflow or overlapping navigation at ' + width);
   await fixturePage.screenshot({ path: path.join(OUT, 'storefront-' + fixture.code + '-' + width + '.png'), fullPage: true });
  }
 });
 pass('ordinary staff storefront links stay tenant-specific, prefer valid Web and fall back to H5, with safe new tabs at 1440/375');
 for (const fixture of [
  { code: 'store-missing' },
  { code: 'store-unsafe', web_url: 'javascript:alert(1)', h5_url: 'data:text/html,unsafe' },
  { code: 'store-userinfo', web_url: 'https://operator:fixture-password@wrong.example.com/' },
  { code: 'store-relative', web_url: '/watch', h5_url: '//other.example.com/' },
 ]) await storefrontFixture(fixture, async fixturePage => {
  const action = await fixturePage.$eval('.backoffice-header [aria-label="访问前台"]', element => ({ tag: element.tagName, disabled: element.disabled, href: element.getAttribute('href') }));
  assert.equal(action.tag, 'BUTTON'); assert.equal(action.disabled, true); assert.equal(action.href, null);
 });
 pass('missing, unsafe, credential-bearing and relative storefront URLs cannot create a fallback navigation target');
 await storefrontFixture({ code: 'company', platform: true }, async fixturePage => { assert.equal(await fixturePage.$('.backoffice-header [aria-label="访问前台"]'), null); });
 pass('company administration does not invent a tenant storefront link');
}

try{
 await verifyStorefrontNavigation();
 const pages=[['/login-settings','登录配置'],['/stripe-accounts','支付通道'],['/coin-packs','金币商城'],['/coin-ledger','金币消耗'],['/attribution','投放归因'],['/support/workbench','工单工作台'],['/support/queue','工单队列'],['/support/reports','服务质量'],['/support/settings','工单设置'],['/plans','会员套餐'],['/finance','财务订单']];
 for(const [route,title] of pages){await go(route);await text(title);for(const width of [1280,1440,1920]){await page.setViewport({width,height:1000});await noOverflow(route+' '+width);await page.screenshot({path:path.join(OUT,route.slice(1).replaceAll('/','-')+'-'+width+'.png'),fullPage:true});}pass(title+' renders at 1280/1440/1920');}
 await page.setViewport({width:1440,height:1000});await go('/support/workbench');await text('Fixture support ticket');assert(await page.$('.support-workbench-layout'));assert(await page.$('.support-ticket-card'));await click('Fixture support ticket');await text('Please help with my membership.');await page.type('textarea[aria-label="回复内容"]','This is a private fixture note.');await page.evaluate(()=>{const label=[...document.querySelectorAll('label')].find(el=>el.textContent.includes('仅员工可见'));label.click();});await click('保存备注');await text('This is a private fixture note.');const reply=requests.find(r=>r.path.endsWith('/TK-FIXTURE/messages'));assert.equal(reply.body.internal,true);assert.equal(reply.headers.authorization,'Bearer fixture-admin-token');assert.equal(reply.headers['x-jjtv-tenant'],'jjtv');pass('staff internal note uses authenticated tenant-scoped action');await page.screenshot({path:path.join(OUT,'support-internal-note-1440.png'),fullPage:true});
 await go('/support/settings');await text('团队与成员');await click('保存配置');await page.waitForFunction(()=>document.body.innerText.includes('工单设置已保存'));const saved=requests.filter(r=>r.path.endsWith('/support/settings')&&r.method==='POST').at(-1);assert.equal(saved.body.config.calendar.start,'10:00');assert.deepEqual(saved.body.config.calendar.holidays,['2026-12-25']);assert.equal(saved.body.config.sla.normal.resolution_minutes,480);pass('saving one settings tab retains calendar and SLA snapshots');

 await click('SLA 与工作日历');await click('设为 7 × 24 小时');await click('保存配置');await page.waitForFunction(()=>document.body.innerText.includes('工单设置已保存'));const calendarSave=requests.filter(r=>r.path.endsWith('/support/settings')&&r.method==='POST').at(-1);assert.equal(calendarSave.body.config.calendar.start,'00:00');assert.equal(calendarSave.body.config.calendar.end,'24:00');assert.deepEqual(calendarSave.body.config.calendar.weekdays,[0,1,2,3,4,5,6]);assert.deepEqual(calendarSave.body.config.calendar.holidays,['2026-12-25']);pass('visual service calendar supports real 24:00 without dropping saved holidays');
 await click('团队与成员');await click('新增团队');await page.waitForFunction(q=>document.querySelector(q+' #max_concurrent')?.value==='20',{},dialog);await fillDialog('code','billing');await fillDialog('name','Fixture billing team');await page.evaluate(q=>[...document.querySelector(q).querySelectorAll('button')].find(x=>x.textContent.trim()==='保存团队').click(),dialog);await dialogClosed();const teamSave=requests.filter(r=>r.path.endsWith('/support/settings')&&r.method==='POST').at(-1);assert.equal(teamSave.body.config.teams[1].code,'billing');assert.equal(teamSave.body.config.teams[1].max_concurrent,20);assert.equal(teamSave.body.config.calendar.end,'24:00');pass('team editor submits visible default limits and preserves other settings');
 await go('/login-settings');await text('登录配置');await page.type('input[aria-label="测试邮件收件地址"]','invalid-address');await click('发送测试邮件');await text('请输入有效的测试收件邮箱');assert(!requests.some(r=>r.path==='/admin/v1/login/test/email'));await page.click('input[aria-label="测试邮件收件地址"]',{clickCount:3});await page.keyboard.press('Backspace');await page.type('input[aria-label="测试邮件收件地址"]','fixture@example.com');await click('发送测试邮件');await text('测试邮件已发送');assert.equal(requests.filter(r=>r.path==='/admin/v1/login/test/email').at(-1).body.email,'fixture@example.com');await page.type('#email_from',' edited');assert(await page.evaluate(()=>[...document.querySelectorAll('button')].find(x=>x.textContent.trim()==='发送测试邮件').disabled));pass('email test validates recipient, sends saved config and blocks unsaved edits');
 await go('/attribution');await text('Fixture Meta pixel');await click('发送测试');await text('回传日志');await text('测试事件已进入回传队列');assert(requests.some(r=>r.path==='/admin/v1/attr/pixels/1/test'&&r.method==='POST'));pass('pixel test queues an explicit test event without claiming live success');

 await click('回传凭证');await click('添加像素');await page.waitForSelector(dialog+' #platform',{visible:true});await page.waitForFunction(q=>[...document.querySelectorAll(q+' .ant-select-selection-item')].some(x=>x.textContent==='Meta'),{},dialog);assert.equal(await page.$eval(dialog+' .ant-select-selection-item[title="Meta"]',el=>el.textContent),'Meta');assert.equal(await page.$eval(dialog+' .ant-select-selection-item[title="测试事件"]',el=>el.textContent),'测试事件');await fillDialog('name','Created fixture pixel');await fillDialog('pixel_id','99887766');await fillDialog('token','fixture-only-token');await confirmDialog();await dialogClosed();const pixelCreate=requests.filter(r=>r.path==='/admin/v1/attr/pixels'&&r.method==='POST').at(-1);assert.equal(pixelCreate.body.platform,'meta');assert.equal(pixelCreate.body.delivery_mode,'test');assert.equal(pixelCreate.body.enabled,false);pass('new attribution pixel preserves mounted Meta/test defaults in actual payload');
 await click('添加投放来源');await page.waitForSelector(dialog+' #timezone',{visible:true});await page.waitForFunction(q=>document.querySelector(q+' #timezone')?.value==='America/Los_Angeles',{},dialog);assert.equal(await page.$eval(dialog+' #timezone',el=>el.value),'America/Los_Angeles');assert.equal(await page.$eval(dialog+' #currency',el=>el.value),'USD');await fillDialog('name','Created fixture source');await chooseDialog('pixel_id','Created fixture pixel · meta');await fillDialog('account_id','fixture-account');await confirmDialog();await text('Fixture validation: source was not saved');assert.equal(await page.$eval(dialog+' #name',el=>el.value),'Created fixture source');assert(await page.$(dialog+' .ant-alert-error'));await confirmDialog();await dialogClosed();const sourceCreate=requests.filter(r=>r.path==='/admin/v1/attr/sources'&&r.method==='POST').at(-1);assert.equal(sourceCreate.body.timezone,'America/Los_Angeles');assert.equal(sourceCreate.body.currency,'USD');assert.equal(sourceCreate.body.enabled,true);assert.equal(sourceCreate.body.pixel_id,2);pass('source default timezone/currency/enabled persist; inline failure preserves inputs for retry');
 await click('投放计划');await click('创建追踪计划');await page.waitForSelector(dialog+' #landing_path',{visible:true});await page.waitForFunction(q=>document.querySelector(q+' #landing_path')?.value==='/',{},dialog);assert.equal(await page.$eval(dialog+' #landing_path',el=>el.value),'/');await fillDialog('name','Created fixture plan');await chooseDialog('source_id','Created fixture source');await confirmDialog();await dialogClosed();const planCreate=requests.filter(r=>r.path==='/admin/v1/attr/plans'&&r.method==='POST').at(-1);assert.equal(planCreate.body.source_id,2);assert.equal(planCreate.body.landing_path,'/');assert.equal(planCreate.body.enabled,true);pass('tracking plan creation submits actual source identity and safe landing defaults');

 await go('/dramas/101');await text('Fixture drama');await text('观看与付费规则');await click('分集');await text('Fixture episode');await click('字幕与定价');await text('第 7 集 · 字幕与定价');await text('当前分集已有历史默认字幕');await page.waitForFunction(q=>document.querySelector(q+' input[aria-label="字幕语言"]')?.value==='en',{},dialog);const priceInput=dialog+' input[aria-label="单集金币价格覆盖"]';await page.type(priceInput,'0');await text('当前分集将对所有用户免费开放');await click('保存价格');await text('当前覆盖：0 金币');assert.equal(requests.filter(r=>r.path.endsWith('/901/pricing')).at(-1).body.coin_price,0);await click('恢复沿用');await click('保存价格');await text('当前：沿用剧集价格');assert.equal(requests.filter(r=>r.path.endsWith('/901/pricing')).at(-1).body.coin_price,null);pass('episode pricing shows real episode number and preserves zero versus inherited price');
 const invalidSub=path.join(OUT,'invalid-fixture.vtt');await fs.writeFile(invalidSub,'not a WebVTT file');let fileInput=await page.$(dialog+' input[type=file]');await fileInput.uploadFile(invalidSub);await text('VTT 文件必须以 WEBVTT 开头');assert(!requests.some(r=>r.path==='/admin/v1/episodes/901/subtitles'&&r.method==='POST'));const validSub=path.join(OUT,'valid-fixture.vtt');await fs.writeFile(validSub,'WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nFixture subtitle.\n');fileInput=await page.$(dialog+' input[type=file]');await fileInput.uploadFile(validSub);await text('Fixture English');await text('已上传：valid-fixture.vtt');assert(requests.some(r=>r.path==='/admin/v1/episodes/901/subtitles'&&r.method==='POST'));await page.screenshot({path:path.join(OUT,'episode-assets-modal-1440.png'),fullPage:true});pass('subtitle modal preserves legacy default, rejects malformed files and refreshes successful upload');
 readOnly=true;await go('/attribution');await text('Fixture Meta pixel');assert.equal(await page.evaluate(()=>[...document.querySelectorAll('button')].some(el=>el.textContent.trim()==='添加像素')),false);assert.equal(await page.evaluate(()=>[...document.querySelectorAll('[role="switch"]')].every(el=>el.disabled||el.getAttribute('aria-disabled')==='true')),true);pass('read-only attribution hides writes and disables toggles');
 await go('/support/workbench');await text('Fixture support ticket');await click('Fixture support ticket');await text('Please help with my membership.');assert.equal(await page.$('textarea[aria-label="回复内容"]'),null);pass('read-only support has no reply or assignment actions');
 const before=requests.filter(r=>r.path==='/admin/v1/support/settings').length;await go('/support/settings');assert(!new URL(page.url()).pathname.endsWith('/support/settings'));assert.equal(requests.filter(r=>r.path==='/admin/v1/support/settings').length,before);pass('direct ungranted menu URL cannot fetch support settings');
 assert.deepEqual(errors,[]);pass('no uncaught browser errors');await fs.writeFile(path.join(OUT,'results.json'),JSON.stringify({checks,screenshots:OUT,apiMode:'explicit local fixtures'},null,2));console.log(JSON.stringify({passed:checks.length,screenshots:OUT}));
}catch(error){await page.screenshot({path:path.join(OUT,'failure.png'),fullPage:true}).catch(()=>{});await fs.writeFile(path.join(OUT,'failure-dom.txt'),await page.content()).catch(()=>{});throw error;}finally{await browser.close();await server.close();}

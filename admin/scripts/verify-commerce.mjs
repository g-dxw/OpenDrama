/** Commerce acceptance uses explicit local fixtures. No real provider/API writes. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import puppeteer from 'puppeteer-core';
import {createServer} from 'vite';
const ROOT=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const OUT=process.env.SHOTS_DIR||path.join(os.tmpdir(),'jjtv-commerce-acceptance');
const port=Number(process.env.COMMERCE_ACCEPT_PORT||5188),origin='http://127.0.0.1:'+port;
await fs.mkdir(OUT,{recursive:true});
const server=await createServer({root:ROOT,server:{host:'127.0.0.1',port,strictPort:true},logLevel:'error'});await server.listen();
const executablePath=process.env.CHROME_PATH||(process.platform==='win32'?'C:/Program Files/Google/Chrome/Application/chrome.exe':'/usr/bin/google-chrome');
const browser=await puppeteer.launch({executablePath,headless:true,args:['--no-first-run','--no-default-browser-check']});
const page=await browser.newPage();await page.setViewport({width:1440,height:1000});
const requests=[],errors=[],checks=[];let readOnly=false,refundAttempts=0,packSaveFailures=1,stripeTestFailures=1;
let siteCurrency="USD",siteTimezone="UTC";
let refundState=null,orderDetailFailures=0;
let membershipResolution=null,resolutionAttempts=0,resolutionMode="unknown",releaseResolution=null;
const refundUnknownError='Fixture refund outcome unknown; sources remain frozen';
const pendingRefundReason='Original provider refund is not identifiable; keep frozen for manual reconciliation';
const manualRefundReason='Refund identity or full amount does not match the frozen request; manual reconciliation required';
const menus=['/dashboard','/coin-packs','/stripe-accounts','/plans','/finance','/coin-ledger'];
const permissions=['dashboard.view','coin_packs.view','coin_packs.manage','stripe.view','stripe.manage','plans.view','plans.manage','finance.view','finance.refund','coins.view','coins.export'];
const identity=()=>({account_scope:'tenant',session_kind:'normal',is_platform_admin:false,is_tenant_admin:!readOnly,user:{id:1,username:'fixture-admin',display_name:'Commerce QA',role:'admin',must_change_password:false},tenant:{id:'jjtv',code:'jjtv',name:'JJTV 自营',kind:'native',status:'active',currency:siteCurrency,timezone:siteTimezone},role_name:'验收',permissions:readOnly?permissions.filter(p=>p.endsWith('.view')):permissions,menu_keys:menus,enabled_modules:['coin_packs','stripe','plans','finance','coins']});
const pack={code:'starter',name_en:'Starter coins',name_zh:'测试金币包',currency:'USD',status:'active',price_cents:499,base_coins:500,bonus_coins:50,version:2,sort_order:0,badge:'popular'};
const plan={code:'monthly',name_en:'Monthly',name_zh:'月度会员',currency:'USD',status:'active',price_cents:999,base_coins:300,bonus_coins:30,version:3,benefits:{items:['Member access','Monthly coins'],best:true,custom:'keep-this'}};
const account={code:'fixture',name:'Stripe fixture',status:'active',is_default:true,secret_key_configured:true,webhook_secret_configured:true,remote_id:'acct_fixture',livemode:false};
const planVariants=[{...plan,code:'yearly',name_en:'Yearly',name_zh:'年度会员',status:'retired',duration_days:365,benefits:{...plan.benefits,best:false}},{...plan,code:'weekly',name_en:'Weekly',name_zh:'周度会员',duration_days:7},{...plan,duration_days:30},{...plan,code:'quarterly_fixture',name_en:'Quarterly fixture',name_zh:'季度示例',duration_days:90,benefits:{...plan.benefits,best:false}}];
let billingMode='one_time';
const order={order_no:'TEST-ORDER',product_type:'coin_pack',product_code:'starter',product_name:'Starter coins',plan_name_zh:'测试金币包',amount_cents:499,currency:'USD',status:'paid',billing_mode:'one_time',account_code:'fixture',created_at:'2026-11-14T00:00:00Z',paid_at:'2026-11-14T00:01:00Z',coins:550,user_id:42,user:{id:42,display_name:'Viewer fixture',email:'viewer@example.com',public_id:'viewer-42'},external_txn_id:'cs_fixture'};
const orderDetail=()=>({membership_refund_resolution:membershipResolution,order:{...order,snapshot:{name:'Starter coins',name_zh:'测试金币包',price_cents:499,currency:'USD',base_coins:500,bonus_coins:50},version_id:2,source_drama_id:null,payment_intent_id:'pi_fixture',invoice_id:null,subscription_ref:null},customer:order.user,refund:refundState,entitlement_sources:{coins:{original:550,remaining:refundState?0:550,reserved:refundState?550:0},membership:null},refund_eligibility:{eligible:!refundState,reason:refundState?(refundState.error||'退款处理中，可重试现有申请'):'可申请未消费权益的全额退款'}});
await page.setRequestInterception(true);page.on('pageerror',error=>errors.push(error.message));
page.on('request',async request=>{const url=new URL(request.url()),p=url.pathname;if(!p.startsWith('/admin/v1/')&&!p.startsWith('/tenant/v1/')){if(url.origin===origin||url.protocol==='data:'||url.protocol==='about:')await request.continue();else await request.abort();return;}
 let body={};try{body=JSON.parse(request.postData()||'{}');}catch{}requests.push({path:p,method:request.method(),query:Object.fromEntries(url.searchParams),body,headers:request.headers()});let result,status=200;
 if(p==='/tenant/v1/context')result=identity();
 else if(p==='/tenant/v1/site'){status=403;result={detail:'Tenant administrator required'};}
 else if(p==='/admin/v1/dashboard')result={day:'2026-11-14',revenue_cents:1999,overview:{lifetime_revenue_cents:1999},trend:[],previous:{}};
 else if(p==='/admin/v1/coin-packs'){if(request.method()==='POST')result={...pack,...body};else result={items:[pack]};}
 else if(p==='/admin/v1/coin-packs/starter'){if(packSaveFailures-- >0){status=409;result={detail:'Fixture version conflict; review then retry'};}else{Object.assign(pack,body);result=pack;}}
 else if(p==='/admin/v1/coins/ladder')result={base_coins_per_usd:100,items:[]};
 else if(p==='/admin/v1/coins/settings')result=body;
 else if(p==='/admin/v1/stripe/accounts'){result=request.method()==='POST'?body:{items:[account]};}
 else if(p==='/admin/v1/stripe/accounts/fixture'){Object.assign(account,body);result=account;}
 else if(p==='/admin/v1/stripe/accounts/fixture/test'){if(stripeTestFailures-- >0){status=503;result={detail:'Fixture Stripe temporarily unavailable'};}else result={ok:true,account_id:'acct_fixture',country:'US',livemode:false};}
 else if(p==='/admin/v1/stripe/prices')result={items:[{account_code:'fixture',product_type:'coin_pack',version_id:2,billing_mode:'one_time',price_id:'price_fixture'}]};
 else if(p==='/admin/v1/stripe/prices/provision')result={items:[{account:'fixture',code:'starter',mode:'one_time',ok:true,price_id:'price_fixture'},{account:'fixture',code:'monthly',mode:'subscription',ok:false,error:'Fixture provider error'}]};
 else if(p==='/admin/v1/commerce/plans')result={items:planVariants,billing_mode:billingMode};
 else if(p==='/admin/v1/commerce/plans/monthly/price'){Object.assign(plan,body);result=plan;}
 else if(p==='/admin/v1/billing/settings'){billingMode=body.billing_mode;result=body;}
 else if(p==='/admin/v1/commerce/orders')result={items:membershipResolution?[order,{...order,order_no:'OTHER-ORDER',product_name:'Other fixture order'}]:[order],has_more:true,total:125,summary:{orders:125,paid_orders:100,gross_cents:50000,refunded_orders:2,refunded_cents:1000,net_cents:49000,pending_orders:25,pending_cents:12500,pending_refund_orders:refundAttempts,review_orders:0,currency:'USD',currencies:[{currency:'USD',gross_cents:50000,refunded_cents:1000,net_cents:49000}]}};
 else if(p==='/admin/v1/commerce/orders/OTHER-ORDER')result={...orderDetail(),order:{...orderDetail().order,order_no:'OTHER-ORDER',product_name:'Other fixture order'},membership_refund_resolution:null};
 else if(p==='/admin/v1/commerce/orders/TEST-ORDER'){if(orderDetailFailures>0){orderDetailFailures--;status=503;result={detail:'Fixture order detail temporarily unavailable'};}else result=orderDetail();}
 else if(p==='/admin/v1/finance/orders/TEST-ORDER/refund'){
  refundAttempts++;order.status='refund_pending';
  if(refundAttempts===1){refundState={id:1,status:'pending',refund_id:null,error:refundUnknownError};status=503;result={detail:refundUnknownError};}
  else if(refundAttempts===4){order.status='refund_review';refundState={id:1,status:'manual_review',refund_id:'re_fixture',error:manualRefundReason};orderDetailFailures=1;result=refundState;}
  else{refundState={id:1,status:'pending',refund_id:'re_fixture',error:refundAttempts===2?pendingRefundReason:''};result=refundState;}
 }
 else if(p==='/admin/v1/finance/orders/TEST-ORDER/membership-refund-resolution'){
   resolutionAttempts++;
   if(resolutionMode==='held'){await new Promise(resolve=>releaseResolution=resolve);}
   if(resolutionMode==='success'){membershipResolution={...membershipResolution,required:false,resolved:true,can_resolve:false};result={order_no:'TEST-ORDER',resolved:true,restored:membershipResolution.disposition==='restore_remaining',released_coins:550,starts_at:'2026-11-14T12:00:00Z',ends_at:'2026-11-14T13:30:00Z',replayed:false};membershipResolution.result=result;}
   else if(resolutionMode==='changed'){membershipResolution={...membershipResolution,revision:'2'.repeat(64),remaining_seconds:5400};status=409;result={detail:{code:'membership_resolution_changed',action:'review_again',committed:false,message:'Fixture resolution facts changed; review again'}};}
   else{status=resolutionMode==='unknown409'?409:503;result={detail:'Fixture resolution result unknown; keep original request'};}
 }
 else if(p==='/admin/v1/coins/records')result={items:[{id:1,user_id:42,biz_type:'unlock',amount:-20,balance_after:530,note:'Episode unlock',created_at:'2026-11-14T00:00:00Z'}],has_more:false};
 else if(p==='/admin/v1/coins/summary')result={balance:530,items:[{biz_type:'purchase',amount:550},{biz_type:'unlock',amount:-20}]};
 else if(p==='/admin/v1/coins/stats')result={items:[{day:'2026-11-13',amount:530,count:2}]};
 else if(p==='/admin/v1/coins/export')result={filename:'fixture.csv',content:'id,user_id,amount\n1,42,-20'};
 else{status=404;result={detail:'Unmocked commerce request '+request.method()+' '+p};}
 await request.respond({status,contentType:'application/json',body:JSON.stringify(result)});
});
await page.evaluateOnNewDocument(()=>localStorage.setItem('jjtv_tenant_token:jjtv','fixture-admin-token'));
async function text(value){await page.waitForFunction(v=>document.body.innerText.includes(v),{},value);}
async function go(route){await page.goto(origin+'/admin/t/jjtv'+route,{waitUntil:'networkidle0'});}
async function click(value,scope){await page.evaluate((v,s)=>{const visible=e=>e.getBoundingClientRect().width>0&&e.getBoundingClientRect().height>0;const root=s==='dialog'?[...document.querySelectorAll('.ant-modal[role="dialog"]')].find(visible):s?document.querySelector(s):document;const e=[...root.querySelectorAll('button,a,[role="tab"]')].find(e=>visible(e)&&e.textContent.replace(/\s+/g,'')===v.replace(/\s+/g,''));if(!e)throw new Error('Missing visible control '+v);e.click();},value,scope);}
async function rowClick(text,control){await page.evaluate((t,c)=>{const row=[...document.querySelectorAll('tbody tr')].find(e=>e.innerText.includes(t));const button=[...row.querySelectorAll('button')].find(e=>e.textContent.trim()===c);if(!button)throw new Error('Missing row control '+c);button.click();},text,control);}
async function closed(){await page.waitForFunction(()=>![...document.querySelectorAll('.ant-modal[role="dialog"]')].some(e=>e.getBoundingClientRect().width>0&&e.getBoundingClientRect().height>0));}
async function modalValue(id){return page.evaluate(name=>{const root=[...document.querySelectorAll('.ant-modal[role="dialog"]')].find(e=>e.getBoundingClientRect().width>0);return root?.querySelector('[id="'+name+'"]')?.value;},id);}
async function fill(id,value){const selector='[role="dialog"] input[id="'+id+'"], [role="dialog"] textarea[id="'+id+'"]';const handles=await page.$$(selector);for(const el of handles){if(await el.boundingBox()){await el.click({clickCount:3});await el.press('Backspace');await el.type(value);return;}}throw new Error('Missing visible form field '+id);}
async function choose(label,value){await page.click('[aria-label="'+label+'"]');await page.waitForSelector('.ant-select-dropdown:not(.ant-select-dropdown-hidden)');await page.evaluate(v=>{const options=[...document.querySelectorAll('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option')];const o=options.find(e=>e.textContent.trim()===v);if(!o)throw new Error('Missing option '+v);o.click();},value);}
async function detailValue(value){return page.evaluate(v=>{const label=[...document.querySelectorAll('.ant-drawer-body .ant-descriptions-item-label')].find(element=>element.textContent.trim()===v);return label?.parentElement?.querySelector('.ant-descriptions-item-content')?.textContent.trim();},value);}
async function until(predicate){const deadline=Date.now()+5000;while(!predicate()){if(Date.now()>deadline)throw new Error('Expected request did not arrive');await new Promise(r=>setTimeout(r,40));}}
const pass=label=>{checks.push(label);console.log('PASS '+label);};
try {
 await go('/coin-packs');await text('测试金币包');await click('新增金币包');assert.equal(await modalValue('price_cents'),'99');assert.equal(await modalValue('bonus_coins'),'0');await fill('code','new_pack');await fill('name_en','New fixture pack');await click('保存档位','dialog');await until(()=>requests.some(r=>r.path==='/admin/v1/coin-packs'&&r.method==='POST'));const created=requests.find(r=>r.path==='/admin/v1/coin-packs'&&r.method==='POST');assert.equal(created.body.price_cents,99);assert.equal(created.body.bonus_coins,0);assert.equal(created.body.badge,'');await closed();pass('coin create defaults survive initial modal mount and save');
 await rowClick('测试金币包','编辑');assert.equal(await modalValue('price_cents'),'499');assert.equal(await modalValue('bonus_coins'),'50');await page.evaluate(()=>{const root=[...document.querySelectorAll('.ant-modal[role="dialog"]')].find(e=>e.getBoundingClientRect().width>0);root.querySelector('.ant-select-clear').dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));});await fill('note','Clear obsolete badge');await click('保存档位','dialog');await text('Fixture version conflict; review then retry');assert.equal(await modalValue('note'),'Clear obsolete badge');await click('保存档位','dialog');await until(()=>requests.filter(r=>r.path==='/admin/v1/coin-packs/starter'&&r.method==='PATCH').length===2);assert.equal(requests.filter(r=>r.path.endsWith('/coin-packs/starter')&&r.method==='PATCH').at(-1).body.badge,'');await closed();await click('新增金币包');assert.equal(await modalValue('code'),'');assert.equal(await modalValue('price_cents'),'99');await page.waitForFunction(()=>![...document.querySelectorAll('.ant-modal')].some(e=>/ant-zoom-(enter|appear)/.test(e.className)));await page.screenshot({path:path.join(OUT,'commerce-coin-form-1440.png'),fullPage:true});await click('取消','dialog');await closed();pass('coin failed save retains input; retry clears badge; reopening create resets fields');
 await page.screenshot({path:path.join(OUT,'commerce-coins-1440.png'),fullPage:true});
 await go('/stripe-accounts');await text('Stripe fixture');await click('新增账号');await fill('code','stripe_new');await fill('name','New Stripe fixture');await click('保存账号','dialog');await until(()=>requests.some(r=>r.path==='/admin/v1/stripe/accounts'&&r.method==='POST'));assert.equal(requests.find(r=>r.path==='/admin/v1/stripe/accounts'&&r.method==='POST').body.status,'draft');await closed();pass('Stripe create saves draft default instead of empty status');
 await click('测试连接');await text('Fixture Stripe temporarily unavailable');await click('测试连接');await text('连接通过 · US');assert(requests.some(r=>r.path.endsWith('/fixture/test')));await click('价格矩阵');await text('price_fixture');await click('补齐当前产品价格');await text('补齐当前产品的 Stripe 价格？');assert(!requests.some(r=>r.path.endsWith('/prices/provision')));await click('补齐缺失价格','dialog');await text('价格同步完成：1 成功 / 1 失败');await text('Fixture provider error');pass('Stripe retry and confirmed price provisioning preserve partial failure evidence');
 await go('/plans');await text('月度会员');await page.click('[data-testid="plan-card-monthly"] .ant-btn-block');assert.equal(await modalValue('items'),'Member access\nMonthly coins');await fill('note','Clarify member benefit');await click('保存新版本','dialog');await until(()=>requests.some(r=>r.path.endsWith('/monthly/price')));const version=requests.find(r=>r.path.endsWith('/monthly/price')).body;assert.deepEqual(version.benefits.items,['Member access','Monthly coins']);assert.equal(version.benefits.best,true);assert.equal(version.benefits.custom,'keep-this');await closed();pass('plan mounted defaults preserve multiline benefits, recommendation and unknown benefits');
 await click('自动续费');await text('确认切换新购买计费模式');assert(!requests.some(r=>r.path==='/admin/v1/billing/settings'));await click('取消','dialog');await click('自动续费');await click('确认切换','dialog');await until(()=>requests.some(r=>r.path==='/admin/v1/billing/settings'));assert.equal(requests.find(r=>r.path==='/admin/v1/billing/settings').body.billing_mode,'subscription');await closed();await page.waitForFunction(()=>![...document.querySelectorAll('.ant-modal-mask')].some(el=>getComputedStyle(el).display!=='none'&&getComputedStyle(el).visibility!=='hidden'));pass('billing mode write requires explicit confirmation');
 await page.waitForSelector('.admin-plan-card');const planGeometry=await page.evaluate(()=>[...document.querySelectorAll('.admin-plan-card')].map(el=>({x:el.getBoundingClientRect().x,y:el.getBoundingClientRect().y,width:el.getBoundingClientRect().width,retired:el.classList.contains('is-retired'),best:el.classList.contains('is-best'),version:el.querySelector('.admin-plan-price-row .ant-tag')?.textContent,benefits:el.querySelectorAll('.admin-plan-benefits li').length})));assert.equal(planGeometry.length,4);assert.equal(new Set(planGeometry.map(p=>p.y)).size,1);assert(planGeometry[0].retired);assert(planGeometry[1].best);assert(planGeometry.every(p=>p.version==='v3'&&p.benefits===2));await page.screenshot({path:path.join(OUT,'membership-plan-cards-1440.png'),fullPage:true});pass('membership cards preserve four-column geometry, retired styling, recommendation and versioned benefits');

 await go('/finance');await text('Viewer fixture');await text('共 125 笔');await choose('订单状态','已支付');await choose('商品类型','金币包');await choose('支付账号筛选','Stripe fixture');await choose('订单计费模式','单次付费');await page.type('[aria-label="搜索财务订单"]','viewer@example.com');await until(()=>requests.some(r=>r.path==='/admin/v1/commerce/orders'&&r.query.q==='viewer@example.com'&&r.query.status==='paid'&&r.query.product_type==='coin_pack'&&r.query.account_code==='fixture'&&r.query.billing_mode==='one_time'));pass('finance filters are sent together to the global server query');
 await click("详情 / 退款");
  await text("本订单权益来源");
  await text("pi_fixture");
  await click("申请全额退款");
  await page.type(
    'textarea[aria-label="退款原因"]',
    "Fixture unused purchase refund",
  );
  await click("提交全额退款", "dialog");
  await text(refundUnknownError);
  await page.waitForFunction(() => {
    const label = [
      ...document.querySelectorAll(
        ".ant-drawer-body .ant-descriptions-item-label",
      ),
    ].find((element) => element.textContent.trim() === "冻结金币");
    return (
      label?.parentElement
        ?.querySelector(".ant-descriptions-item-content")
        ?.textContent.trim() === "550"
    );
  });
  assert.equal(await detailValue("剩余金币"), "0");
  assert.equal(await detailValue("退款状态"), "处理中");
  assert.equal(
    await page.$eval('textarea[aria-label="退款原因"]', (input) => input.value),
    "Fixture unused purchase refund",
  );
  const errorModal = await page.$eval(
    '.ant-modal[role="dialog"]',
    (modal) => modal.innerText,
  );
  assert(errorModal.includes("请求出错或结果未知时仍保持冻结"));
  assert(!errorModal.includes("处理失败会释放"));
  assert(
    !(
      await page.$eval(".ant-drawer-body", (drawer) => drawer.innerText)
    ).includes("本次退款状态：已确认完成"),
  );
  pass(
    "refund HTTP uncertainty keeps sources frozen, retains the form and never promises a release",
  );
  await click("查询 / 重试退款", "dialog");
  await text("本次退款状态：处理中");
  await text(pendingRefundReason);
  await text("查询 / 重试本笔退款");
  await closed();
  assert.equal(await detailValue("冻结金币"), "550");
  assert.equal(
    requests.find((r) => r.path.endsWith("/TEST-ORDER/refund")).body
      .idempotency_key,
    "refund-TEST-ORDER",
  );
  pass(
    "finance provenance and pending reconciliation reason remain visible without claiming a refund",
  );
  await click("查询 / 重试本笔退款");
  await page.type(
    'textarea[aria-label="退款原因"]',
    "Retry same fixture refund",
  );
  await click("查询 / 重试退款", "dialog");
  await until(() => refundAttempts === 3);
  await closed();
  assert(
    requests
      .filter((r) => r.path.endsWith("/TEST-ORDER/refund"))
      .every((r) => r.body.idempotency_key === "refund-TEST-ORDER"),
  );
  pass("uncertain refund queries reuse the same stable key");
  await page.screenshot({
    path: path.join(OUT, "commerce-refund-pending-1440.png"),
    fullPage: true,
  });
  await click("查询 / 重试本笔退款");
  await page.type(
    'textarea[aria-label="退款原因"]',
    "Verify original provider facts",
  );
  await click("查询 / 重试退款", "dialog");
  await text("Fixture order detail temporarily unavailable");
  await closed();
  await text("本次退款状态：待人工核对");
  await text(manualRefundReason);
  assert.equal(await detailValue("冻结金币"), "550");
  assert.equal(
    await page.evaluate(() =>
      [...document.querySelectorAll(".ant-drawer-body button")].some(
        (button) => button.textContent.trim() === "查询 / 重试本笔退款",
      ),
    ),
    false,
  );
  assert(
    !(
      await page.$eval(".ant-drawer-body", (drawer) => drawer.innerText)
    ).includes("本次退款状态：已确认完成"),
  );
  await click("刷新", ".ant-drawer");
  await page.waitForFunction(() => {
    const label = [
      ...document.querySelectorAll(
        ".ant-drawer-body .ant-descriptions-item-label",
      ),
    ].find((element) => element.textContent.trim() === "退款状态");
    return (
      label?.parentElement
        ?.querySelector(".ant-descriptions-item-content")
        ?.textContent.trim() === "待人工核对"
    );
  });
  assert.equal(await detailValue("最近结果"), manualRefundReason);
  assert.equal(await detailValue("冻结金币"), "550");
  pass(
    "manual-review outcome and its reason survive a failed detail refresh and block automatic resubmission",
  );
  await page.screenshot({
    path: path.join(OUT, "commerce-refund-review-1440.png"),
    fullPage: true,
  });
  order.status = "refund_pending";
  refundState = {
    id: 1,
    status: "pending",
    refund_id: "re_fixture",
    error: "",
  };
 membershipResolution={required:true,kind:'one_time_refund_window',provider_status:null,remaining_seconds:7200,can_resolve:false,blocked_reason:'Fixture provider terminal state is not confirmed',revision:'1'.repeat(64),disposition:'restore_remaining'};
 Object.assign(order,{product_type:'membership',product_code:'monthly',product_name:'Monthly fixture',plan_name_zh:'月度会员'});
 await go('/finance');await click('详情 / 退款');
 await page.waitForSelector('[data-testid="membership-refund-resolution-card"]');
 await text('Fixture provider terminal state is not confirmed');
 assert.equal(await page.$eval('[data-testid="membership-refund-resolution-open"]',b=>b.disabled),true);
 assert.equal(resolutionAttempts,0);
 pass('membership refund resolution exposes provider evidence and blocks action until its source can be resolved');
 const resolutionPosts=()=>requests.filter(r=>r.path.endsWith('/membership-refund-resolution'));
 const resolutionModal='.membership-refund-resolution-modal';
 const resolutionConfirm=()=>page.locator(resolutionModal+' .ant-modal-footer .ant-btn-primary');
 const resolutionOpen=async()=>{await page.locator('[data-testid="membership-refund-resolution-open"]').click();await page.waitForSelector('[data-testid="membership-refund-resolution-reason"]',{visible:true});};
 const resolutionClose=async()=>{await page.locator(resolutionModal+' .ant-modal-close').click();await page.waitForSelector(resolutionModal+' .ant-modal',{hidden:true});};
 const closeDrawer=async()=>{await page.locator('.ant-drawer-open .ant-drawer-close').click();await page.waitForFunction(()=>!document.querySelector('.ant-drawer-open'));};
 const resolutionReason=async value=>{await page.locator('[data-testid="membership-refund-resolution-reason"]').click();await page.type('[data-testid="membership-refund-resolution-reason"]',value);};
 const settleResolution=async()=>{const response=page.waitForResponse(r=>r.url().endsWith('/membership-refund-resolution'));releaseResolution();await (await response).buffer();await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));};
 readOnly=true;await go('/finance');await click('详情 / 退款');await page.waitForSelector('[data-testid="membership-refund-resolution-card"]');
 assert.equal(await page.$('[data-testid="membership-refund-resolution-open"]'),null);assert.equal(resolutionPosts().length,0);
 pass('read-only finance roles can inspect resolution evidence without any resolution write control');
 readOnly=false;membershipResolution={...membershipResolution,provider_status:'failed',can_resolve:true,blocked_reason:null};
 await go('/finance');await click('详情 / 退款');await resolutionOpen();
 await resolutionConfirm().click();await text('请填写处理原因');assert.equal(resolutionPosts().length,0);
 await resolutionReason('Fixture preserve frozen membership window');
 resolutionMode='held';await resolutionConfirm().click();await until(()=>resolutionAttempts===1);
 const firstResolution=resolutionPosts()[0].body;assert.equal(firstResolution.expected_revision,'1'.repeat(64));assert.equal(firstResolution.reason,'Fixture preserve frozen membership window');assert(firstResolution.idempotency_key.length>=8);
 const busyPoint=await page.$eval(resolutionModal+' .ant-modal-footer .ant-btn-primary',b=>{const r=b.getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2};});
 await page.mouse.click(busyPoint.x,busyPoint.y);await page.mouse.click(busyPoint.x,busyPoint.y);
 assert.equal(resolutionPosts().length,1);
 resolutionMode='unknown';await settleResolution();await text('Fixture resolution result unknown; keep original request');
 assert((await page.$eval(resolutionModal,e=>e.innerText)).includes('从本次确认时起恢复 7,200 秒'));
 assert.equal(await page.$eval('[data-testid="membership-refund-resolution-reason"]',e=>e.value),firstResolution.reason);
 pass('resolution requires an audit reason, confirms the exact frozen seconds and blocks duplicate submits while preserving an unknown request');
 await resolutionClose();await closeDrawer();await click('详情 / 退款');await resolutionOpen();
 assert.equal(await page.$eval('[data-testid="membership-refund-resolution-reason"]',e=>e.readOnly),true);
 assert.equal(await page.$eval('[data-testid="membership-refund-resolution-reason"]',e=>e.value),firstResolution.reason);
 resolutionMode='unknown409';await resolutionConfirm().click();await until(()=>resolutionAttempts===2);await text('Fixture resolution result unknown; keep original request');await page.waitForFunction(()=>!document.querySelector('.membership-refund-resolution-modal .ant-btn-loading'));
 assert.deepEqual(resolutionPosts()[1].body,firstResolution);assert.equal(await page.$('[data-testid="membership-refund-resolution-review"]'),null);
 pass('closing and reopening an uncertain resolution retains its exact reason, revision and key; an unclassified 409 cannot restart it');
 resolutionMode='changed';await resolutionConfirm().click();await until(()=>resolutionAttempts===3);await text('Fixture resolution facts changed; review again');
 await page.waitForFunction(()=>document.querySelector('[data-testid="membership-refund-resolution-review"]')&&!document.querySelector('[data-testid="membership-refund-resolution-review"]').disabled);
 assert.deepEqual(resolutionPosts()[2].body,firstResolution);
 assert.equal(await page.$eval(resolutionModal+' .ant-modal-footer .ant-btn-primary',e=>e.disabled),true);
 const beforeReview=resolutionPosts().length;await page.locator('[data-testid="membership-refund-resolution-review"]').click();
 assert.equal(resolutionPosts().length,beforeReview);assert.equal(await page.$eval('[data-testid="membership-refund-resolution-reason"]',e=>e.readOnly),false);
 const beforeRefreshes=requests.filter(r=>r.path==='/admin/v1/commerce/orders'||r.path==='/admin/v1/commerce/orders/TEST-ORDER').length;
 resolutionMode='success';orderDetailFailures=1;await resolutionConfirm().click();await until(()=>resolutionAttempts===4);await closed();await text('权益结案已完成');await text('Fixture order detail temporarily unavailable');
 assert.equal(await page.$('[data-testid="membership-refund-resolution-card"]'),null);
 await page.waitForSelector('[data-testid="membership-refund-resolution-card"]',{hidden:true});
 const restarted=resolutionPosts()[3].body;assert.notEqual(restarted.idempotency_key,firstResolution.idempotency_key);assert.equal(restarted.expected_revision,'2'.repeat(64));
 assert(requests.filter(r=>r.path==='/admin/v1/commerce/orders'||r.path==='/admin/v1/commerce/orders/TEST-ORDER').length>=beforeRefreshes+2);
 pass('only an explicit review after a guaranteed uncommitted 409 creates a new resolution intent; success refreshes detail and list while a detail outage cannot reopen an already confirmed action');
 membershipResolution={required:true,kind:'one_time_refund_window',provider_status:'canceled',remaining_seconds:0,can_resolve:true,blocked_reason:null,revision:'3'.repeat(64),disposition:'release_revoked'};
 await go('/finance');await click('详情 / 退款');await resolutionOpen();
 const revokedText=await page.$eval(resolutionModal,e=>e.innerText);assert(revokedText.includes('仅释放本订单冻结金币，会员权益保持收回'));assert(!revokedText.includes('恢复 0 秒'));
 await resolutionReason('Fixture release coins without reviving revoked access');resolutionMode='held';await resolutionConfirm().click();await until(()=>resolutionAttempts===5);
 const revokedIntent=resolutionPosts()[4].body;
 await resolutionClose();await closeDrawer();await rowClick('OTHER-ORDER','详情 / 退款');await text('OTHER-ORDER');
 const detailReadsBeforeLate=requests.filter(r=>r.path.startsWith('/admin/v1/commerce/orders/')).length;
 await page.evaluate(()=>localStorage.setItem('jjtv_tenant_token:jjtv','other-admin-token'));
 resolutionMode='success';await settleResolution();
 assert.equal(requests.filter(r=>r.path.startsWith('/admin/v1/commerce/orders/')).length,detailReadsBeforeLate);
 assert.equal(await page.$('[data-testid="membership-refund-resolution-card"]'),null);assert((await page.$eval('.ant-drawer-body',e=>e.innerText)).includes('OTHER-ORDER'));
 assert.equal(await page.$(resolutionModal),null);
 pass('release-only confirmation preserves revoked membership and a late old-owner response cannot close or refresh another order');
 await page.evaluate(()=>localStorage.setItem('jjtv_tenant_token:jjtv','fixture-admin-token'));
 membershipResolution={...membershipResolution,required:false,resolved:false,result:null};
 await closeDrawer();await rowClick('TEST-ORDER','详情 / 退款');await text('TEST-ORDER');
 const cachedBeforeProof=await page.evaluate(()=>Object.keys(sessionStorage).filter(k=>k.startsWith('jjtv_membership_refund_resolution:')).map(k=>({key:k,value:JSON.parse(sessionStorage.getItem(k))})));
 assert(cachedBeforeProof.some(v=>v.value.key===revokedIntent.idempotency_key));
 membershipResolution={...membershipResolution,resolved:true,result:{order_no:'TEST-ORDER',resolved:true,restored:false,released_coins:550,starts_at:null,ends_at:null,replayed:true}};
 await click('刷新','.ant-drawer');
 await page.waitForFunction(key=>!Object.keys(sessionStorage).filter(k=>k.startsWith('jjtv_membership_refund_resolution:')).some(k=>JSON.parse(sessionStorage.getItem(k)).key===key),{},revokedIntent.idempotency_key);
 assert.equal(resolutionPosts().length,5);
 pass('required=false alone preserves an unknown intent; explicit same-order resolved evidence retires it without a new write');
 readOnly=true;await go('/plans');await text('月度会员');assert.equal(await page.$$('.admin-plan-card .ant-switch').then(x=>x.length),0);assert.equal(await page.$$('.admin-plan-card .ant-btn-block').then(x=>x.length),0);pass('read-only membership cards have no status or edit controls');await go('/coin-packs');await text('测试金币包');assert.equal(await page.evaluate(()=>[...document.querySelectorAll('button')].some(e=>e.textContent.trim()==='新增金币包')),false);await go('/finance');await click('详情 / 退款');await text('本订单权益来源');assert.equal(await page.evaluate(()=>[...document.querySelectorAll('button')].some(e=>e.textContent.trim()==='查询 / 重试本笔退款')),false);pass('read-only role sees evidence without create or refund controls');
 siteCurrency='JPY';siteTimezone='Asia/Tokyo';await go('/dashboard');await text('Asia/Tokyo');
 const expectedMoney=currency=>new Intl.NumberFormat('zh-CN',{style:'currency',currency}).format(1999/10**new Intl.NumberFormat('zh-CN',{style:'currency',currency}).resolvedOptions().maximumFractionDigits);
 await text(expectedMoney('JPY'));
 const staffFormat=()=>page.evaluate(async()=>{const f=await import('/src/site-format.ts');return {currency:f.operatorCurrency(),timezone:f.operatorTimezone(),date:f.operatorDate('2026-11-14T00:00:00Z')};});
 assert.deepEqual(await staffFormat(),{currency:'JPY',timezone:'Asia/Tokyo',date:new Intl.DateTimeFormat('zh-CN',{dateStyle:'medium',timeStyle:'short',timeZone:'Asia/Tokyo'}).format(new Date('2026-11-14T00:00:00Z'))});
 siteCurrency='EUR';siteTimezone='Europe/Paris';await page.evaluate(()=>window.dispatchEvent(new Event('focus')));await text('Europe/Paris');await text(expectedMoney('EUR'));
 assert.deepEqual(await staffFormat(),{currency:'EUR',timezone:'Europe/Paris',date:new Intl.DateTimeFormat('zh-CN',{dateStyle:'medium',timeStyle:'short',timeZone:'Europe/Paris'}).format(new Date('2026-11-14T00:00:00Z'))});
 assert.equal(requests.filter(r=>r.path==='/tenant/v1/site').length,0);
 pass('non-admin staff use authenticated identity for currency and timezone, including zero-decimal JPY and refreshed site settings without privileged reads');
 assert.deepEqual(errors,[]);assert(requests.filter(r=>r.method==='POST'||r.method==='PATCH').every(r=>r.headers.authorization==='Bearer fixture-admin-token'&&r.headers['x-jjtv-tenant']==='jjtv'));pass('all actions are authenticated and tenant scoped; no browser errors');
 await fs.writeFile(path.join(OUT,'commerce-results.json'),JSON.stringify({checks,apiMode:'explicit fixtures; no real provider writes',screenshots:OUT},null,2));console.log(JSON.stringify({passed:checks.length,screenshots:OUT}));
} catch(error) {await fs.writeFile(path.join(OUT,'commerce-failure.json'),JSON.stringify({error:String(error),body:await page.evaluate(()=>document.body.innerText),requests:requests.slice(-8),errors},null,2));throw error;} finally {await browser.close();await server.close();}

/** Anonymous login acceptance. All API calls are intercepted local fixtures. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer-core';
import { createServer } from 'vite';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const OUT = process.env.SHOTS_DIR || path.join(os.tmpdir(), 'minitv-admin-login-acceptance');
const port = Number(process.env.ADMIN_LOGIN_ACCEPT_PORT || 5188);
const origin = 'http://127.0.0.1:' + port;
const executablePath = process.env.CHROME_PATH || (process.platform === 'win32'
  ? 'C:/Program Files/Google/Chrome/Application/chrome.exe'
  : process.platform === 'darwin' ? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' : '/usr/bin/google-chrome');
const layouts = [
  { name: 'entry-chooser', route: '/admin/login', selector: '[data-testid="auth-entry-chooser"]' },
  { name: 'platform-login', route: '/admin/platform/login', selector: '[data-testid="auth-login-form"]' },
  { name: 'tenant-login', route: '/admin/t/jjtv/login', selector: '[data-testid="auth-login-form"]' },
];
const viewports = [{ width: 1440, height: 1000 }, { width: 1920, height: 1080 }, { width: 390, height: 844 }];
const directory = [{ code: 'jjtv', name: 'JJTV 演示站' }, { code: 'japan', name: '日本运营站' }];
const checks = [];
const screenshots = [];
const browserErrors = [];
const unexpectedAPIs = [];
const blockedExternalRequests = [];
const contexts = [];
let browser;
let activePage;

await fs.mkdir(OUT, { recursive: true });
const server = await createServer({ root: ROOT, server: { host: '127.0.0.1', port, strictPort: true }, logLevel: 'error' });
await server.listen();

function pass(label) { checks.push(label); console.log('PASS ' + label); }
async function text(page, value) {
  await page.waitForFunction(v => document.body.innerText.includes(v), {}, value);
}
async function button(page, label) {
  await page.waitForFunction(value => [...document.querySelectorAll('button')].some(node =>
    (node.textContent.trim() === value || node.getAttribute('aria-label') === value) && node.getClientRects().length && !node.disabled), {}, label);
  await page.evaluate(value => [...document.querySelectorAll('button')].find(node =>
    (node.textContent.trim() === value || node.getAttribute('aria-label') === value) && node.getClientRects().length && !node.disabled).click(), label);
}
async function until(predicate, message) {
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  throw new Error(message);
}

async function fixturePage(mode = 'success') {
  const page = await browser.newPage();
  activePage = page;
  page.setDefaultTimeout(10_000);
  const fixture = { page, mode, requests: [], holdLogin: false, pending: [] };
  fixture.releaseLogins = () => { for (const release of fixture.pending.splice(0)) release(); };
  contexts.push(fixture);
  page.on('pageerror', error => browserErrors.push(error.message));
  await page.evaluateOnNewDocument(() => { localStorage.clear(); sessionStorage.clear(); });
  await page.setRequestInterception(true);
  page.on('request', async request => {
    try {
      if (request.isInterceptResolutionHandled()) return;
      const url = new URL(request.url());
      if (url.origin !== origin) {
        if (['data:', 'about:', 'blob:'].includes(url.protocol)) { await request.continue(); return; }
        blockedExternalRequests.push(url.origin + url.pathname);
        await request.abort();
        return;
      }
      if (!/^\/(?:admin|tenant|platform)\/v1\//.test(url.pathname)) { await request.continue(); return; }
      let body = {};
      try { body = JSON.parse(request.postData() || '{}'); } catch { /* Assert invalid data in the requesting test. */ }
      fixture.requests.push({ path: url.pathname, method: request.method(), body, headers: request.headers() });
      let status = 200;
      let result;
      if (url.pathname === '/tenant/v1/login-options' && request.method() === 'GET') {
        if (fixture.mode === 'failure') { status = 503; result = { detail: 'Fixture tenant directory unavailable' }; }
        else result = { items: fixture.mode === 'empty' ? [] : directory };
      } else if (['/platform/v1/auth/login', '/tenant/v1/auth/login'].includes(url.pathname) && request.method() === 'POST') {
        // Deliberately never issue a token or enter the authenticated application.
        if (fixture.holdLogin) await new Promise(resolve => fixture.pending.push(resolve));
        status = 401;
        result = { detail: 'Invalid fixture credential' };
      } else {
        unexpectedAPIs.push(request.method() + ' ' + url.pathname);
        status = 404;
        result = { detail: 'Unmocked anonymous API' };
      }
      if (!request.isInterceptResolutionHandled()) await request.respond({ status, contentType: 'application/json', body: JSON.stringify(result) });
    } catch (error) {
      if (!page.isClosed() && !/Target closed|Session closed|Invalid InterceptionId|Request is already handled/i.test(error.message)) {
        browserErrors.push('fixture interception: ' + error.message);
      }
    }
  });
  return fixture;
}

async function visit(fixture, route, viewport = viewports[0]) {
  await fixture.page.setViewport(viewport);
  await fixture.page.goto(origin + route, { waitUntil: 'networkidle0' });
  await fixture.page.waitForSelector('[data-testid="auth-layout"]', { visible: true });
  await fixture.page.evaluate(() => document.fonts.ready);
}

async function assertLayout(page, selector, viewport) {
  await page.waitForSelector(selector, { visible: true });
  const geometry = await page.evaluate(formSelector => {
    const rect = node => { const value = node.getBoundingClientRect(); return { x: value.x, y: value.y, width: value.width, height: value.height, right: value.right }; };
    const hero = document.querySelector('[data-testid="auth-hero"]');
    const panel = document.querySelector('[data-testid="auth-panel"]');
    const layout = document.querySelector('[data-testid="auth-layout"]');
    const form = document.querySelector(formSelector);
    if (!hero || !panel || !layout || !form) throw new Error('Missing auth layout contract elements');
    const heroStyle = getComputedStyle(hero);
    const heroVisible = hero.getClientRects().length > 0 && heroStyle.display !== 'none' && heroStyle.visibility !== 'hidden';
    const media = [hero, ...hero.querySelectorAll('*')].some(node => node instanceof HTMLImageElement
      ? node.complete && node.naturalWidth > 0
      : /url\(/.test(getComputedStyle(node).backgroundImage));
    return { hero: rect(hero), panel: rect(panel), layout: rect(layout), form: rect(form), heroVisible, media,
      viewport: innerWidth, scrollWidth: document.documentElement.scrollWidth,
      fields: [...form.querySelectorAll('input:not([type="hidden"]),button[type="submit"]')].filter(node => node.getClientRects().length)
        .map(node => ({ type: node.getAttribute('autocomplete') || node.type,
          height: (node.closest('.ant-input-affix-wrapper') || node).getBoundingClientRect().height,
          label: node.getAttribute('aria-label') || '', autocomplete: node.getAttribute('autocomplete') })) };
  }, selector);
  assert(geometry.scrollWidth <= viewport.width + 1, 'Login has horizontal overflow');
  assert(geometry.form.width > 0 && geometry.form.width <= 440 + 1, 'Auth form must be at most 440px wide');
  assert(geometry.layout.width >= viewport.width - 2, 'Auth layout should fill the viewport width');
  if (viewport.width >= 1024) {
    assert(geometry.heroVisible && geometry.media, 'Desktop must show the team photo');
    assert(await page.evaluate(async () => {
      const hero = document.querySelector('[data-testid="auth-hero"]');
      const nodes = [hero, ...hero.querySelectorAll('*')];
      if (nodes.some(node => node instanceof HTMLImageElement && node.complete && node.naturalWidth > 0)) return true;
      const urls = nodes.flatMap(node => [...getComputedStyle(node).backgroundImage.matchAll(/url\(["']?([^"')]+)["']?\)/g)].map(match => match[1]));
      const loaded = await Promise.all(urls.map(url => new Promise(resolve => {
        const image = new Image();
        const timer = setTimeout(() => resolve(false), 5000);
        image.onload = () => { clearTimeout(timer); resolve(image.naturalWidth > 0); };
        image.onerror = () => { clearTimeout(timer); resolve(false); };
        image.src = url;
      })));
      return loaded.some(Boolean);
    }), 'Desktop hero photo must load successfully');
    assert(Math.abs(geometry.hero.width - viewport.width / 2) <= 2, 'Hero should occupy the left half');
    assert(Math.abs(geometry.panel.width - viewport.width / 2) <= 2, 'Form panel should occupy the right half');
    assert(Math.abs(geometry.hero.x) <= 1 && Math.abs(geometry.panel.x - geometry.hero.right) <= 2, 'Desktop halves must meet without outer card margins');
    assert(geometry.hero.height >= viewport.height * 0.85, 'Desktop hero should fill the page height');
  } else {
    assert.equal(geometry.heroVisible, false, 'Mobile must hide the decorative hero');
    assert(geometry.panel.width >= viewport.width - 2, 'Mobile form panel should fill the viewport');
    assert(geometry.form.x >= 0 && geometry.form.x + geometry.form.width <= viewport.width + 1, 'Mobile form must stay inside the viewport');
    for (const control of geometry.fields) {
      // rc-select's searchable input is internally shorter than its 44px control.
      if (control.autocomplete === 'username' || control.autocomplete === 'current-password' || control.type === 'submit') {
        assert(control.height >= 44 - 1, 'Mobile login controls must have 44px touch targets');
      }
    }
  }
}

async function clearField(page, selector) {
  await page.focus(selector);
  const modifier = process.platform === 'darwin' ? 'Meta' : 'Control';
  await page.keyboard.down(modifier);
  await page.keyboard.press('A');
  await page.keyboard.up(modifier);
  await page.keyboard.press('Backspace');
}
async function fillLogin(page) {
  const username = 'input[autocomplete="username"]';
  const password = 'input[autocomplete="current-password"]';
  await clearField(page, username);
  await clearField(page, password);
  await page.type(username, 'fixture-login-user');
  await page.type(password, 'fixture-invalid-password');
}

async function testLoginRequest(platform) {
  const fixture = await fixturePage();
  const { page } = fixture;
  const route = platform ? '/admin/platform/login' : '/admin/t/jjtv/login';
  const label = platform ? '登录管理系统' : '登录所选平台';
  await visit(fixture, route, viewports[2]);
  await clearField(page, 'input[autocomplete="username"]');
  await clearField(page, 'input[autocomplete="current-password"]');
  await button(page, label);
  await text(page, '请输入账号');
  await text(page, '请输入密码');
  assert(!fixture.requests.some(request => request.path.endsWith('/auth/login')), 'Required validation must stop submission');
  await fillLogin(page);
  fixture.holdLogin = true;
  await button(page, label);
  await until(() => fixture.requests.some(request => request.path.endsWith('/auth/login')), 'Missing login fixture request');
  await page.waitForSelector('button[aria-label="' + label + '"].ant-btn-loading', { visible: true });
  const sent = fixture.requests.filter(request => request.path.endsWith('/auth/login'));
  assert.equal(sent.length, 1, 'Login should submit once while pending');
  assert.equal(sent[0].path, platform ? '/platform/v1/auth/login' : '/tenant/v1/auth/login');
  assert.deepEqual(sent[0].body, { username: 'fixture-login-user', password: 'fixture-invalid-password', ...(platform ? {} : { tenant_code: 'jjtv' }) });
  assert.equal(sent[0].headers.authorization, undefined, 'Anonymous login must not send an old token');
  fixture.releaseLogins();
  await text(page, '账号或密码不正确');
  await page.waitForFunction(value => !document.querySelector('button[aria-label="' + value + '"]').classList.contains('ant-btn-loading'), {}, label);
  assert.equal(await page.$eval('input[autocomplete="username"]', input => input.value), 'fixture-login-user');
  if (platform) assert(!fixture.requests.some(request => request.path === '/tenant/v1/login-options'), 'Platform login must not request the tenant directory');
  else assert(fixture.requests.some(request => request.path === '/tenant/v1/login-options'));
  await assertLayout(page, '[data-testid="auth-login-form"]', viewports[2]);
  await page.close();
  pass((platform ? 'platform' : 'tenant') + ' required fields, pending feedback, generic 401 and entry-bound request');
}

async function selectJapan(page) {
  await page.click('[aria-label="选择平台"]');
  await page.waitForSelector('.ant-select-dropdown:not(.ant-select-dropdown-hidden)', { visible: true });
  await page.evaluate(() => {
    const option = [...document.querySelectorAll('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option')]
      .find(node => node.textContent.includes('日本运营站（japan）'));
    if (!option) throw new Error('Missing fixture tenant option');
    option.click();
  });
}

try {
  browser = await puppeteer.launch({ executablePath, headless: true, args: ['--no-first-run', '--no-default-browser-check'] });
  for (const layout of layouts) {
    const fixture = await fixturePage();
    for (const viewport of viewports) {
      await visit(fixture, layout.route, viewport);
      await assertLayout(fixture.page, layout.selector, viewport);
      const filename = layout.name + '-' + viewport.width + '.png';
      await fixture.page.screenshot({ path: path.join(OUT, filename), fullPage: true });
      screenshots.push(filename);
    }
    if (layout.name === 'platform-login') assert(!fixture.requests.some(request => request.path === '/tenant/v1/login-options'));
    await fixture.page.close();
    pass(layout.name + ' layout at 1440/1920/390');
  }
  await testLoginRequest(true);
  await testLoginRequest(false);

  const failure = await fixturePage('failure');
  await visit(failure, '/admin/login');
  await text(failure.page, 'Fixture tenant directory unavailable');
  assert(await failure.page.$eval('button[type="submit"]', button => button.disabled));
  failure.mode = 'success';
  await button(failure.page, '重新加载');
  await failure.page.waitForFunction(() => document.querySelector('.ant-select') &&
    !document.querySelector('.ant-select').classList.contains('ant-select-disabled') &&
    !document.body.innerText.includes('Fixture tenant directory unavailable'));
  for (const request of failure.requests.filter(request => request.path === '/tenant/v1/login-options')) assert.equal(request.headers.authorization, undefined);
  await failure.page.close();
  pass('directory failure disables chooser and anonymous retry restores available sites');

  const empty = await fixturePage('empty');
  await visit(empty, '/admin/login', viewports[2]);
  await text(empty.page, '当前没有开放登录的平台，请联系管理员。');
  assert(await empty.page.$eval('button[type="submit"]', button => button.disabled));
  await assertLayout(empty.page, '[data-testid="auth-entry-chooser"]', viewports[2]);
  await empty.page.close();
  pass('empty tenant directory stays disabled without inventing a default site');

  for (const fromChooser of [true, false]) {
    const fixture = await fixturePage();
    await visit(fixture, fromChooser ? '/admin/login' : '/admin/t/jjtv/login');
    await fixture.page.evaluate(() => { window.__loginAcceptanceDocumentMarker = true; });
    if (fromChooser) {
      await selectJapan(fixture.page);
      await Promise.all([fixture.page.waitForNavigation({ waitUntil: 'networkidle0' }), button(fixture.page, '进入平台登录')]);
    } else {
      await Promise.all([fixture.page.waitForNavigation({ waitUntil: 'networkidle0' }), selectJapan(fixture.page)]);
    }
    assert.equal(new URL(fixture.page.url()).pathname, '/admin/t/japan/login');
    assert.equal(await fixture.page.evaluate(() => window.__loginAcceptanceDocumentMarker), undefined, 'Tenant change must load a new document');
    await fixture.page.waitForSelector('[data-testid="auth-login-form"]', { visible: true });
    await fillLogin(fixture.page);
    await button(fixture.page, '登录所选平台');
    await text(fixture.page, '账号或密码不正确');
    const login = fixture.requests.filter(request => request.path === '/tenant/v1/auth/login').at(-1);
    assert.equal(login.body.tenant_code, 'japan');
    await fixture.page.close();
    pass((fromChooser ? 'entry chooser' : 'tenant selector') + ' navigates to a new tenant document and binds its login body');
  }

  const support = await fixturePage();
  await support.page.goto(origin + '/admin/s/jjtv/login', { waitUntil: 'domcontentloaded' });
  await support.page.waitForFunction(() => location.pathname === '/admin/platform/tenants' && new URLSearchParams(location.search).get('notice') === 'support-ended');
  assert(!support.requests.some(request => request.path === '/tenant/v1/auth/login'));
  await support.page.close();
  pass('missing support session returns to the platform entry without tenant password login');

  assert.deepEqual(unexpectedAPIs, [], 'Anonymous pages must not call protected or unmocked APIs');
  assert.deepEqual(browserErrors, [], 'No uncaught browser or fixture errors');
  assert.deepEqual(blockedExternalRequests, [], 'Login assets must load locally without external requests');
  assert.equal(screenshots.length, 9);
  await fs.writeFile(path.join(OUT, 'results.json'), JSON.stringify({ checks, screenshots, apiMode: 'explicit local anonymous fixtures',
    blockedExternalRequests: [...new Set(blockedExternalRequests)] }, null, 2));
  console.log(JSON.stringify({ passed: checks.length, screenshots: OUT, screenshotCount: screenshots.length }));
} catch (error) {
  if (activePage && !activePage.isClosed()) {
    await activePage.screenshot({ path: path.join(OUT, 'failure.png'), fullPage: true }).catch(() => {});
    await fs.writeFile(path.join(OUT, 'failure-dom.txt'), await activePage.content()).catch(() => {});
  }
  throw error;
} finally {
  for (const fixture of contexts) fixture.releaseLogins();
  await browser?.close();
  await server.close();
}

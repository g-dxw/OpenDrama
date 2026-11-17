// UI-only role/connection regression checks. API responses below are synthetic;
// provider authorization and cross-service acceptance require separate tests.
// Usage: node admin/tests/india-role-auth.browser.mjs [path/to/@playwright/test/index.mjs] [browser-channel]
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { createServer as createNetServer } from 'node:net';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createServer } from '../node_modules/vite/dist/node/index.js';

const playwrightModule = process.argv[2];
const { chromium, expect } = await import(playwrightModule ? pathToFileURL(playwrightModule).href : '@playwright/test');
const adminRoot = fileURLToPath(new URL('../', import.meta.url));
const screenshots = fileURLToPath(new URL('../../outputs/india-role-ui/', import.meta.url));
const portProbe = createNetServer();
await new Promise(resolve => portProbe.listen(0, '127.0.0.1', resolve));
const port = portProbe.address().port;
await new Promise(resolve => portProbe.close(resolve));
const server = await createServer({ root: adminRoot, server: { port, host: '127.0.0.1', strictPort: true }, logLevel: 'error' });
await server.listen();
const base = server.resolvedUrls.local[0].replace(/\/$/, '');
let browser;
try { browser = await chromium.launch({ headless: true, ...(process.argv[3] ? { channel: process.argv[3] } : {}) }); }
catch (error) { await server.close(); throw error; }
let passed = 0;
const failures = [];
const series = {
  id: 'ui-series', title: 'India role fixture', synopsis: 'Synthetic UI fixture', genre: 'drama', language: 'hi',
  content_origin: 'live_action', content_format: 'micro_series', content_collections: ['india_launch'], age_rating: 'U',
  free_episode_count: 1, episode_count: 1, version: 1, status: 'draft', rights: null,
  episodes: [{ id: 'ui-episode', episode_no: 1, title: 'UI episode', duration_ms: 3000, media_status: 'ready' }],
};

async function fixture(permissions, options = {}) {
  const context = await browser.newContext({ viewport: options.viewport || { width: 1440, height: 1000 } });
  await context.addInitScript(() => localStorage.setItem('jjtv_tenant_token:india', 'synthetic-ui-token'));
  const state = { permissions, contentCalls: 0, sessionCalls: 0, writes: [], series: { ...series, ...options.series, status: options.status || series.status } };
  const tenant = { id: 'india-short-drama', code: 'india', name: 'India UI fixture', kind: 'india_legacy', status: 'active', version: 1 };
  await context.route('**/tenant/v1/context', route => route.fulfill({ json: {
    user: { id: 93001, username: 'ui.operator', display_name: 'UI operator', role: 'ops', must_change_password: false },
    account_scope: 'tenant', session_kind: 'normal', is_platform_admin: false, is_tenant_admin: false,
    tenant, role_name: '测试岗位', permissions: state.permissions, menu_keys: ['/india-content'], enabled_modules: ['dramas', 'reviews'],
  } }));
  await context.route('**/admin/v1/tenants/india-short-drama/india/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname.split('/india/')[1];
    if (path === 'session') {
      state.sessionCalls++;
      if ((!options.session || options.session.auth_mode === 'jjtv_delegated') && request.method() !== 'GET') failures.push('Delegated UI attempted legacy session write: ' + request.method());
      return route.fulfill({ json: options.session || { configured: true, connected: true, auth_mode: 'jjtv_delegated', permissions: state.permissions } });
    }
    state.contentCalls++;
    if (request.method() === 'GET' && path === 'admin/series') return route.fulfill({ json: { data: { items: [state.series], next_cursor: null } } });
    if (request.method() === 'GET' && path === 'admin/series/ui-series') return route.fulfill({ json: { data: state.series } });
    if ((request.method() === 'POST' && path === 'admin/series') || (request.method() === 'PATCH' && path === 'admin/series/ui-series')) {
      const payload = request.postDataJSON();
      state.writes.push({ method: request.method(), payload });
      state.series = { ...state.series, ...payload, version: request.method() === 'POST' ? 1 : state.series.version + 1 };
      return route.fulfill({ status: request.method() === 'POST' ? 201 : 200, json: { data: state.series } });
    }
    if (request.method() === 'POST' && path === 'admin/episodes/ui-episode/uploads') return route.fulfill({ json: { data: { upload_id: 'ui-upload', upload_url: '/v1/admin/uploads/ui-upload/content', max_bytes: 100000 } } });
    if (request.method() === 'PUT' && path === 'admin/uploads/ui-upload/content') return route.fulfill({ json: { data: { sha256: '0'.repeat(64) } } });
    if (request.method() === 'POST' && path === 'admin/uploads/ui-upload/complete') return route.fulfill({ json: { data: { job_id: 'ui-job', status: 'failed' } } });
    if (request.method() === 'GET' && path === 'admin/jobs/ui-job') return route.fulfill({ json: { data: { id: 'ui-job', status: 'failed', attempts: 1 } } });
    return route.fulfill({ status: 500, json: { detail: { message: 'Unhandled UI fixture request: ' + request.method() + ' ' + path } } });
  });
  const page = await context.newPage();
  page.on('pageerror', error => failures.push(error.message));
  await page.goto(base + '/admin/t/india/india-content');
  return { context, page, state };
}

async function delegatedReady(page) {
  await expect(page.getByText('租户角色授权', { exact: true })).toBeVisible();
  await expect(page.getByText('India role fixture', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: /连接旧站运营账号|更换旧站账号|续期连接|^断开$/ })).toHaveCount(0);
  await expect(page.getByText(/^到期 /)).toHaveCount(0);
}

async function fillNewDraft(page) {
  await page.getByRole('button', { name: '新增剧集', exact: true }).click();
  const modal = page.getByRole('dialog', { name: '新增 India 剧集', exact: true });
  await modal.getByLabel('剧集名称', { exact: true }).fill('Draft without rights');
  await modal.getByLabel('剧情简介', { exact: true }).fill('An upload draft with licensing details to follow.');
  await modal.getByLabel('题材', { exact: true }).fill('drama');
  await modal.getByLabel('原声语言代码', { exact: true }).fill('hi');
  await modal.getByLabel('内容来源', { exact: true }).click();
  await page.getByText('真人实拍', { exact: true }).last().click();
  await modal.getByLabel('年龄分级', { exact: true }).click();
  await page.getByText('U', { exact: true }).last().click();
  return modal;
}

async function check(name, run) {
  try { await run(); passed++; console.log('PASS ' + name); }
  catch (error) { failures.push(name + ': ' + error.message); console.error('FAIL ' + name + ': ' + error.message); }
}

try {
  await check('viewer opens content without old account and cannot create', async () => {
    const { page, context, state } = await fixture(['dramas.view']);
    try { await delegatedReady(page); await expect(page.getByRole('button', { name: '新增剧集', exact: true })).toBeDisabled(); assert.ok(state.contentCalls > 0); }
    finally { await context.close(); }
  });
  await check('editor uses only local edit permission and updated permissions remove edit', async () => {
    const { page, context, state } = await fixture(['dramas.view', 'dramas.edit']);
    try {
      await delegatedReady(page);
      await expect(page.getByRole('button', { name: '新增剧集', exact: true })).toBeEnabled();
      await page.getByRole('button', { name: '管理内容', exact: true }).click();
      await expect(page.getByLabel('剧集名称', { exact: true })).toBeEnabled();
      await expect(page.getByRole('button', { name: '提交审核', exact: true })).toBeVisible();
      await expect(page.getByRole('button', { name: '审核通过', exact: true })).toHaveCount(0);
      await expect(page.getByRole('button', { name: /^上\s*架$/ })).toHaveCount(0);
      await page.keyboard.press('Escape');
      state.permissions = ['dramas.view'];
      await page.getByRole('button', { name: '刷新当前租户权限', exact: true }).click();
      await expect(page.getByRole('button', { name: '新增剧集', exact: true })).toBeDisabled();
    } finally { await context.close(); }
  });
  await check('editor creates an upload draft with blank optional rights', async () => {
    const { page, context, state } = await fixture(['dramas.view', 'dramas.edit', 'dramas.pricing']);
    try {
      await delegatedReady(page);
      const modal = await fillNewDraft(page);
      await expect(modal.getByText('版权授权（选填）', { exact: true })).toBeVisible();
      await modal.getByRole('button', { name: '创建草稿', exact: true }).click();
      await expect.poll(() => state.writes.length).toBe(1);
      assert.equal(state.writes[0].method, 'POST');
      assert.equal(state.writes[0].payload.rights, null);
      assert.equal(state.writes[0].payload.title, 'Draft without rights');
      assert.equal('version' in state.writes[0].payload, false);
      await expect(modal).toBeHidden();
    } finally { await context.close(); }
  });
  await check('editor saves an existing draft without licensing details', async () => {
    const { page, context, state } = await fixture(['dramas.view', 'dramas.edit']);
    try {
      await delegatedReady(page);
      await page.getByRole('button', { name: '管理内容', exact: true }).click();
      await page.getByLabel('剧集名称', { exact: true }).fill('Edited draft without rights');
      await page.getByRole('button', { name: '保存资料', exact: true }).click();
      await expect.poll(() => state.writes.length).toBe(1);
      assert.equal(state.writes[0].method, 'PATCH');
      assert.equal(state.writes[0].payload.rights, null);
      assert.equal(state.writes[0].payload.version, 1);
      assert.equal(state.writes[0].payload.title, 'Edited draft without rights');
    } finally { await context.close(); }
  });
  await check('partially entered licensing details cannot be saved', async () => {
    const { page, context, state } = await fixture(['dramas.view', 'dramas.edit']);
    try {
      await delegatedReady(page);
      await page.getByRole('button', { name: '管理内容', exact: true }).click();
      await page.getByLabel('权利持有人', { exact: true }).fill('Example Rights Holder');
      await page.getByRole('button', { name: '保存资料', exact: true }).click();
      await expect(page.getByText('请填写此项', { exact: true }).first()).toBeVisible();
      assert.equal(state.writes.length, 0);
    } finally { await context.close(); }
  });
  await check('complete existing rights retain evidence and reject an invalid date range', async () => {
    const rights = {
      holder: 'Example Rights Holder', license_version: 'license-v3', territories: ['IN'], languages: ['hi'],
      platforms: ['web'], monetization_modes: ['free'], allowed_uses: ['streaming'], exclusive: false,
      starts_at: '2026-01-01T00:00:00.000Z', ends_at: '2028-01-01T00:00:00.000Z',
      evidence_ref: 'rights-evidence-v3', withdrawal_terms: 'Remove content when the license ends.',
    };
    const { page, context, state } = await fixture(['dramas.view', 'dramas.edit'], { series: { rights, version: 7 } });
    try {
      await delegatedReady(page);
      await page.getByRole('button', { name: '管理内容', exact: true }).click();
      const starts = page.getByLabel('授权开始（本地时间）', { exact: true });
      const ends = page.getByLabel('授权结束（本地时间）', { exact: true });
      await ends.fill(await starts.inputValue());
      await page.getByRole('button', { name: '保存资料', exact: true }).click();
      await expect(page.getByText('授权结束时间必须晚于开始时间', { exact: true })).toBeVisible();
      assert.equal(state.writes.length, 0);
      // One calendar year later is valid in every browser timezone.
      await ends.fill((await starts.inputValue()).replace(/^2026/, '2027'));
      await page.getByRole('button', { name: '保存资料', exact: true }).click();
      await expect.poll(() => state.writes.length).toBe(1);
      assert.equal(state.writes[0].method, 'PATCH');
      assert.equal(state.writes[0].payload.version, 7);
      assert.deepEqual(state.writes[0].payload.rights, { ...rights, ends_at: '2027-01-01T00:00:00.000Z' });
    } finally { await context.close(); }
  });
  await check('rights evidence requires saved licensing details while cover upload stays available', async () => {
    const withoutRights = await fixture(['dramas.view', 'dramas.upload']);
    try {
      await delegatedReady(withoutRights.page);
      await withoutRights.page.getByRole('button', { name: '管理内容', exact: true }).click();
      await withoutRights.page.getByRole('tab', { name: '封面与版权证明', exact: true }).click();
      const files = withoutRights.page.getByRole('tabpanel', { name: '封面与版权证明', exact: true });
      await expect(files.getByText('请先在资料页补全并保存版权授权，再上传版权证明。', { exact: true })).toBeVisible();
      await expect(files.getByLabel('授权文件版本', { exact: true })).toBeDisabled();
      await expect(files.getByRole('button', { name: '上传版权证明', exact: true })).toBeDisabled();
      await expect(files.getByRole('button', { name: '上传 / 替换封面', exact: true })).toBeEnabled();
    } finally { await withoutRights.context.close(); }
    const withRights = await fixture(['dramas.view', 'dramas.upload'], { series: { rights: {
      holder: 'Example Rights Holder', license_version: 'license-v3', territories: ['IN'], languages: ['hi'],
      platforms: ['web'], monetization_modes: ['free'], allowed_uses: ['streaming'], exclusive: false,
      starts_at: '2026-01-01T00:00:00.000Z', ends_at: '2028-01-01T00:00:00.000Z',
      evidence_ref: 'rights-evidence-v3', withdrawal_terms: 'Remove content when the license ends.',
    } } });
    try {
      await delegatedReady(withRights.page);
      await withRights.page.getByRole('button', { name: '管理内容', exact: true }).click();
      await withRights.page.getByRole('tab', { name: '封面与版权证明', exact: true }).click();
      const files = withRights.page.getByRole('tabpanel', { name: '封面与版权证明', exact: true });
      await expect(files.getByText('请先在资料页补全并保存版权授权，再上传版权证明。', { exact: true })).toHaveCount(0);
      await expect(files.getByLabel('授权文件版本', { exact: true })).toBeEnabled();
      await expect(files.getByLabel('授权文件版本', { exact: true })).toHaveValue('license-v3');
      await expect(files.getByRole('button', { name: '上传版权证明', exact: true })).toBeEnabled();
      await expect(files.getByRole('button', { name: '上传 / 替换封面', exact: true })).toBeEnabled();
    } finally { await withRights.context.close(); }
  });
  await check('uploader can upload and retry without old admin role', async () => {
    const { page, context } = await fixture(['dramas.view', 'dramas.upload']);
    try {
      await delegatedReady(page);
      await expect(page.getByRole('button', { name: '新增剧集', exact: true })).toBeDisabled();
      await page.getByRole('button', { name: '管理内容', exact: true }).click();
      await expect(page.getByLabel('剧集名称', { exact: true })).toBeDisabled();
      await page.getByRole('tab', { name: '分集与视频', exact: true }).click();
      await page.getByRole('button', { name: '视频、转码与字幕', exact: true }).click();
      await expect(page.getByRole('button', { name: '上传 / 替换原视频', exact: true })).toBeEnabled();
      await page.locator('input[type=file]').first().setInputFiles({ name: 'ui-fixture.mp4', mimeType: 'video/mp4', buffer: Buffer.alloc(32) });
      await expect(page.getByRole('button', { name: '重试转码', exact: true })).toBeEnabled();
    } finally { await context.close(); }
  });
  await check('review and publish remain separate local permissions', async () => {
    const reviewer = await fixture(['reviews.view', 'reviews.decide'], { status: 'rights_review' });
    try {
      await delegatedReady(reviewer.page);
      await reviewer.page.getByRole('button', { name: '管理内容', exact: true }).click();
      await expect(reviewer.page.getByRole('button', { name: '审核通过', exact: true })).toBeEnabled();
      await expect(reviewer.page.getByRole('button', { name: /^上\s*架$/ })).toHaveCount(0);
    } finally { await reviewer.context.close(); }
    const publisher = await fixture(['dramas.view', 'dramas.publish'], { status: 'ready' });
    try {
      await delegatedReady(publisher.page);
      await publisher.page.getByRole('button', { name: '管理内容', exact: true }).click();
      await expect(publisher.page.getByRole('button', { name: /^上\s*架$/ })).toBeEnabled();
      await expect(publisher.page.getByRole('button', { name: '审核通过', exact: true })).toHaveCount(0);
    } finally { await publisher.context.close(); }
  });
  await check('missing delegated configuration never prompts for old credentials', async () => {
    const { page, context, state } = await fixture(['dramas.view'], { session: { configured: false, connected: false, auth_mode: 'jjtv_delegated', state: 'unavailable' } });
    try {
      await expect(page.getByText('India 运营授权暂不可用，请联系平台管理员检查配置', { exact: true })).toBeVisible();
      await expect(page.getByRole('button', { name: /连接旧站运营账号|更换旧站账号|续期连接|^断开$/ })).toHaveCount(0);
      assert.equal(state.contentCalls, 0);
    } finally { await context.close(); }
  });
  await check('password compatibility mode retains its explicit connection flow', async () => {
    const { page, context } = await fixture(['dramas.view'], { session: { configured: true, connected: false, auth_mode: 'jjtv_client_password' } });
    try {
      await page.getByRole('button', { name: '连接旧站运营账号', exact: true }).click();
      await expect(page.getByLabel('旧站运营账号', { exact: true })).toBeVisible();
      await expect(page.getByLabel('旧站密码', { exact: true })).toBeVisible();
    } finally { await context.close(); }
  });
  await check('375px delegated page stays within viewport', async () => {
    const { page, context } = await fixture(['dramas.view', 'dramas.edit'], { viewport: { width: 375, height: 900 } });
    try {
      await delegatedReady(page);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
      assert.equal(overflow, false);
      await mkdir(screenshots, { recursive: true });
      await page.screenshot({ path: screenshots + 'delegated-375.png', fullPage: true });
    } finally { await context.close(); }
  });
} finally {
  await browser.close();
  await server.close();
}
console.log(JSON.stringify({ passed, failures }, null, 2));
if (failures.length) process.exitCode = 1;

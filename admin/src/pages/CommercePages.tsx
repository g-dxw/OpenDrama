import { useState } from 'react';
import { Alert, Button, Card, Col, Form, Input, InputNumber, Modal, Popconfirm, Row, Select, Space, Statistic, Switch, Table, Tabs, Tag, Typography, message } from 'antd';
import { api, errorText } from '../api';
import { useLoad, formatTime } from '../hooks';
import { usePermission } from '../me';

type Pack = { code: string; name_en: string; name_zh: string; status: string; price_cents: number; base_coins: number; bonus_coins: number; version: number; sort_order: number; badge: string };
type Account = { code: string; name: string; status: string; is_default: boolean; secret_key_configured: boolean; webhook_secret_configured: boolean; remote_id?: string; livemode?: boolean };
const money = (cents: number) => '$' + (cents / 100).toFixed(2);
const STATUS_NAMES: Record<string,string> = { active: '已启用', retired: '已下架', draft: '草稿', disabled: '已停用', created: '待支付', paid: '已支付', failed: '支付失败', refund_pending: '退款处理中', refunded: '已退款', refund_review: '退款待人工核对' };
const status = (value: string) => <Tag color={value === 'active' || value === 'paid' ? 'green' : ['refund_pending','refund_review'].includes(value) ? 'gold' : 'default'}>{STATUS_NAMES[value] || value}</Tag>;
function ErrorBanner({ error, retry }: { error: string | null; retry: () => void }) { return error && error !== 'unauthorized' ? <Alert type="error" showIcon message={error} action={<Button onClick={retry}>重试</Button>} /> : null; }

export function CoinPacksPage() {
  const state = useLoad<{ items: Pack[] }>('/admin/v1/coin-packs', { items: [] });
  const ladder = useLoad<{ base_coins_per_usd: number; items: { code: string; coins_per_usd: number; bonus_ratio_pct: number }[] }>('/admin/v1/coins/ladder', { base_coins_per_usd: 100, items: [] });
  const canManage = usePermission('coin_packs.manage');
  const [editing, setEditing] = useState<Pack | null | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [form] = Form.useForm();
  const [baseRate, setBaseRate] = useState<number | null>(null);
  const open = (pack: Pack | null) => { setEditing(pack); form.resetFields(); form.setFieldsValue(pack ?? { price_cents: 499, base_coins: 500, bonus_coins: 0, sort_order: 0 }); };
  const save = async () => { try { const values = await form.validateFields(); setBusy(true); await api('/admin/v1/coin-packs' + (editing ? '/' + editing.code : ''), { method: editing ? 'PATCH' : 'POST', body: JSON.stringify(values) }); setEditing(undefined); message.success('已保存，后续购买使用新版本'); await Promise.all([state.reload(), ladder.reload()]); } catch (e) { if (!(e as any)?.errorFields) message.error(errorText(e)); } finally { setBusy(false); } };
  return <Space direction="vertical" size={20} style={{ width: '100%' }}>
    <div><Typography.Title level={3}>金币商城</Typography.Title><Typography.Text type="secondary">编辑价格与赠币会生成新版本，已购买订单保留原快照。</Typography.Text></div>
    <ErrorBanner error={state.error} retry={state.reload} />
    <Card title="金币包" extra={canManage && <Button type="primary" onClick={() => open(null)}>新增金币包</Button>}>
      <Table rowKey="code" loading={state.loading} dataSource={state.data.items} scroll={{ x: 900 }} columns={[
        { title: '金币包', render: (_, p: Pack) => <><strong>{p.name_en}</strong><br /><Typography.Text type="secondary">{p.code} · v{p.version}</Typography.Text></> },
        { title: '价格', render: (_, p: Pack) => money(p.price_cents) },
        { title: '基础 / 加赠', render: (_, p: Pack) => p.base_coins + ' + ' + p.bonus_coins },
        { title: '状态', dataIndex: 'status', render: status },
        { title: '操作', render: (_, p: Pack) => canManage && <Space><Button onClick={() => open(p)}>编辑</Button><Popconfirm title={p.status === 'active' ? '下架后新用户不能购买，历史权益保留。' : '确认上架？'} onConfirm={async () => { try { await api('/admin/v1/coin-packs/' + p.code + '/status', { method: 'POST', body: JSON.stringify({ status: p.status === 'active' ? 'retired' : 'active' }) }); await state.reload(); } catch (e) { message.error(errorText(e)); } }}><Button>{p.status === 'active' ? '下架' : '上架'}</Button></Popconfirm></Space> },
      ]} />
    </Card>
    <Card title="金币阶梯自检"><Space wrap><Typography.Text>基础金币 / USD：{ladder.data.base_coins_per_usd}</Typography.Text>{canManage && <><InputNumber aria-label="每美元基础金币" min={1} value={baseRate} placeholder={String(ladder.data.base_coins_per_usd)} onChange={setBaseRate} /><Button disabled={!baseRate} onClick={async () => { try { await api('/admin/v1/coins/settings', { method: 'POST', body: JSON.stringify({ base_coins_per_usd: baseRate }) }); await ladder.reload(); message.success('已保存参考比例'); } catch (e) { message.error(errorText(e)); } }}>保存参考比例</Button></>}</Space><Table rowKey="code" pagination={false} dataSource={ladder.data.items} columns={[{ title: '金币包', dataIndex: 'code' }, { title: '基础金币 / USD', dataIndex: 'coins_per_usd' }, { title: '加赠比例 %', dataIndex: 'bonus_ratio_pct' }]} /></Card>
    <Modal title={editing ? '编辑金币包' : '新增金币包'} open={editing !== undefined} onCancel={() => setEditing(undefined)} onOk={save} confirmLoading={busy} destroyOnClose>
      <Form form={form} layout="vertical">{!editing && <Form.Item name="code" label="唯一编码" rules={[{ required: true, pattern: /^[a-z0-9_]{2,32}$/, message: '2–32位小写英文、数字或下划线' }]}><Input /></Form.Item>}
        <Form.Item name="name_en" label="英文名称" rules={[{ required: true }]}><Input maxLength={64} /></Form.Item><Form.Item name="name_zh" label="中文名称"><Input maxLength={64} /></Form.Item>
        <Form.Item name="price_cents" label="价格（美分）" rules={[{ required: true }]}><InputNumber min={50} precision={0} /></Form.Item><Form.Item name="base_coins" label="基础金币" rules={[{ required: true }]}><InputNumber min={1} precision={0} /></Form.Item><Form.Item name="bonus_coins" label="加赠金币"><InputNumber min={0} precision={0} /></Form.Item><Form.Item name="sort_order" label="排序"><InputNumber min={0} precision={0} /></Form.Item><Form.Item name="badge" label="角标"><Input maxLength={64} /></Form.Item>
      </Form>
    </Modal>
  </Space>;
}

export function StripeAccountsPage() {
  const state = useLoad<{ items: Account[] }>('/admin/v1/stripe/accounts', { items: [] });
  const prices = useLoad<{ items: any[] }>('/admin/v1/stripe/prices', { items: [] });
  const canManage = usePermission('stripe.manage');
  const [editing, setEditing] = useState<Account | null | undefined>();
  const [busy, setBusy] = useState(false); const [result, setResult] = useState<any[] | null>(null); const [form] = Form.useForm();
  const open = (account: Account | null) => { setEditing(account); form.resetFields(); form.setFieldsValue(account ?? { status: 'draft', is_default: false }); };
  const save = async () => { try { const values = await form.validateFields(); setBusy(true); await api('/admin/v1/stripe/accounts' + (editing ? '/' + editing.code : ''), { method: editing ? 'PATCH' : 'POST', body: JSON.stringify(values) }); setEditing(undefined); await state.reload(); message.success('支付账号已保存'); } catch (e) { if (!(e as any)?.errorFields) message.error(errorText(e)); } finally { setBusy(false); } };
  return <Space direction="vertical" size={20} style={{ width: '100%' }}><div><Typography.Title level={3}>支付通道</Typography.Title><Typography.Text type="secondary">每笔订单固定 Stripe 账号与价格版本。停用只停止新购买，已有回调、续订与退款仍使用原账号。</Typography.Text></div>
    <ErrorBanner error={state.error} retry={state.reload} />
    <Alert type="info" showIcon message="仅接入 Stripe" description="先保存草稿并测试连接，配置本账号独立的 webhook 签名密钥后启用。凭据加密保存，留空不修改。" />
    <Card title="Stripe 账号" extra={canManage && <Button type="primary" onClick={() => open(null)}>新增账号</Button>}><Table rowKey="code" loading={state.loading} dataSource={state.data.items} scroll={{ x: 900 }} columns={[
      { title: '账号', render: (_, a: Account) => <><strong>{a.name}</strong> {a.is_default && <Tag color="blue">默认</Tag>}<br />{a.code} · {a.remote_id || '尚未测试'}</> }, { title: '状态', dataIndex: 'status', render: status }, { title: '凭据', render: (_, a: Account) => <>{a.secret_key_configured ? '已配置 Key' : '缺少 Key'} / {a.webhook_secret_configured ? '已配 webhook' : '缺少 webhook'}</> },
      { title: '操作', render: (_, a: Account) => canManage && <Space><Button onClick={() => open(a)}>编辑</Button><Button onClick={async () => { setBusy(true); try { const r = await api<any>('/admin/v1/stripe/accounts/' + a.code + '/test', { method: 'POST' }); message.success('连接成功：' + r.account_id + (r.livemode ? ' · LIVE' : ' · TEST')); await state.reload(); } catch (e) { message.error(errorText(e)); } finally { setBusy(false); } }} loading={busy}>测试连接</Button></Space> },
    ]} /></Card>
    <Card title="账号价格矩阵" extra={canManage && <Button loading={busy} onClick={async () => { setBusy(true); try { const r = await api<{ items: any[] }>('/admin/v1/stripe/prices/provision', { method: 'POST' }); setResult(r.items); await prices.reload(); } catch (e) { message.error(errorText(e)); } finally { setBusy(false); } }}>铺齐当前产品价格</Button>}><Table rowKey={r => [r.account_code, r.product_type, r.version_id, r.billing_mode].join(':')} dataSource={prices.data.items} columns={[{ title: '账号', dataIndex: 'account_code' }, { title: '商品', dataIndex: 'product_type' }, { title: '版本', dataIndex: 'version_id' }, { title: '模式', dataIndex: 'billing_mode' }, { title: 'Stripe Price', dataIndex: 'price_id' }]} />{result && <Alert type={result.some(x => !x.ok) ? 'warning' : 'success'} message="价格接入结果" description={result.map((x, i) => <div key={i}>{x.account} · {x.code} · {x.mode}：{x.ok ? '成功' : x.error}</div>)} />}</Card>
    <Modal title={editing ? '编辑 Stripe 账号' : '新增 Stripe 账号'} open={editing !== undefined} onCancel={() => setEditing(undefined)} onOk={save} confirmLoading={busy} destroyOnClose><Form form={form} layout="vertical">
      {!editing && <Form.Item name="code" label="账号编码" rules={[{ required: true, pattern: /^[a-z0-9_]{2,32}$/ }]}><Input /></Form.Item>}<Form.Item name="name" label="名称" rules={[{ required: true }]}><Input maxLength={100} /></Form.Item>
      <Form.Item name="secret_key" label="Secret Key（留空不修改）"><Input.Password autoComplete="new-password" placeholder={editing?.secret_key_configured ? '已保存，密钥不会回显' : 'sk_test_…'} /></Form.Item><Form.Item name="webhook_secret" label="Webhook signing secret（留空不修改）"><Input.Password autoComplete="new-password" placeholder="whsec_…" /></Form.Item>
      <Alert type="info" message="Webhook 地址" description={'/v1/billing/stripe/accounts/' + (editing?.code || '账号编码') + '/webhook'} />
      <Form.Item name="status" label="状态"><Select options={[{ value: 'draft', label: '草稿' }, { value: 'active', label: '启用' }, { value: 'disabled', label: '停用新购买' }]} /></Form.Item><Form.Item name="is_default" label="设为新客户默认账号" valuePropName="checked"><Switch /></Form.Item>
    </Form></Modal>
  </Space>;
}

export function CommercePlansPage() {
  const state = useLoad<{ items: any[]; billing_mode: string }>('/admin/v1/commerce/plans', { items: [], billing_mode: 'one_time' });
  const canManage = usePermission('plans.manage');const [editing, setEditing] = useState<any>(null);const [busy, setBusy] = useState(false);const [form] = Form.useForm();
  return <Space direction="vertical" size={20} style={{ width: '100%' }}><Typography.Title level={3}>会员套餐</Typography.Title><ErrorBanner error={state.error} retry={state.reload} /><Alert type="info" showIcon message="每次改价或调整赠币均建立新版本" description="已支付订单与既有订阅固定原权益快照。赠币按实际成功支付的每期发放，重复回调不会重复到账。" />
    <Card title="计费模式"><Select aria-label="新购买计费模式" value={state.data.billing_mode} disabled={!canManage || busy} options={[{ value: 'one_time', label: '买断制（单次付费）' }, { value: 'subscription', label: '订阅制（自动续期）' }]} onChange={async billing_mode => { setBusy(true); try { await api('/admin/v1/billing/settings', { method: 'POST', body: JSON.stringify({ billing_mode }) }); await state.reload(); } catch (e) { message.error(errorText(e)); } finally { setBusy(false); } }} /></Card>
    <Card><Table rowKey="code" loading={state.loading} dataSource={state.data.items} scroll={{ x: 800 }} columns={[{ title: '套餐', render: (_, p: any) => <>{p.name_en}<br /><Typography.Text type="secondary">{p.code} · v{p.version}</Typography.Text></> }, { title: '金额', render: (_, p: any) => money(p.price_cents) }, { title: '每期金币', render: (_, p: any) => p.base_coins + ' + ' + p.bonus_coins }, { title: '状态', dataIndex: 'status', render: status }, { title: '操作', render: (_, p: any) => canManage && <Space><Button onClick={() => { setEditing(p); form.setFieldsValue({ ...p, items: (p.benefits?.items || []).join('\n') }); }}>改价 / 权益</Button><Popconfirm title="只影响后续购买，既有权益保持原快照。" onConfirm={async () => { try { await api('/admin/v1/plans/' + p.code + '/status', { method: 'POST', body: JSON.stringify({ status: p.status === 'active' ? 'retired' : 'active' }) }); await state.reload(); } catch (e) { message.error(errorText(e)); } }}><Button>{p.status === 'active' ? '下架' : '上架'}</Button></Popconfirm></Space> }]} /></Card>
    <Modal title="创建套餐新版本" open={!!editing} onCancel={() => setEditing(null)} confirmLoading={busy} onOk={async () => { try { const v = await form.validateFields(); setBusy(true); await api('/admin/v1/commerce/plans/' + editing.code + '/price', { method: 'POST', body: JSON.stringify({ price_cents: v.price_cents, base_coins: v.base_coins, bonus_coins: v.bonus_coins, benefits: { ...editing.benefits, items: v.items.split('\n').filter(Boolean) }, note: v.note }) }); setEditing(null); await state.reload(); } catch (e) { if (!(e as any)?.errorFields) message.error(errorText(e)); } finally { setBusy(false); } }}><Form form={form} layout="vertical"><Form.Item name="price_cents" label="价格（美分）" rules={[{ required: true }]}><InputNumber min={50} precision={0} /></Form.Item><Form.Item name="base_coins" label="每期基础金币"><InputNumber min={0} precision={0} /></Form.Item><Form.Item name="bonus_coins" label="每期加赠金币"><InputNumber min={0} precision={0} /></Form.Item><Form.Item name="items" label="权益文案（每行一条）"><Input.TextArea rows={4} /></Form.Item><Form.Item name="note" label="调整原因" rules={[{ required: true }]}><Input maxLength={500} /></Form.Item></Form></Modal>
  </Space>;
}

export function CoinLedgerPage() {
  const [userId, setUserId] = useState<number | null>(null); const [kind, setKind] = useState(''); const [offset, setOffset] = useState(0);
  const query = new URLSearchParams({ limit: '50', offset: String(offset) }); if (userId) query.set('user_id', String(userId)); if (kind) query.set('biz_type', kind);
  const state = useLoad<{ items: any[]; has_more: boolean }>('/admin/v1/coins/records?' + query, { items: [], has_more: false });
  const summary = useLoad<{ balance: number; items: any[] }>('/admin/v1/coins/summary', { balance: 0, items: [] }); const stats = useLoad<{ items: any[] }>('/admin/v1/coins/stats', { items: [] }); const canExport = usePermission('coins.export');
  return <Space direction="vertical" size={20} style={{ width: '100%' }}><Typography.Title level={3}>金币消耗</Typography.Title><ErrorBanner error={state.error} retry={state.reload} /><Card><Row gutter={16}><Col span={8}><Statistic title="用户可用金币总额" value={summary.data.balance} /></Col><Col span={8}><Statistic title="解锁消耗" value={Math.abs(summary.data.items.find(x => x.biz_type === 'unlock')?.amount || 0)} /></Col><Col span={8}><Statistic title="成功购币 / 套餐赠币" value={summary.data.items.find(x => x.biz_type === 'purchase')?.amount || 0} /></Col></Row></Card>
    <Card><Space wrap><InputNumber aria-label="按用户 ID 筛选" placeholder="用户 ID" min={1} value={userId} onChange={v => { setOffset(0); setUserId(v); }} /><Select aria-label="账目类型" value={kind} style={{ width: 160 }} onChange={v => { setOffset(0); setKind(v); }} options={[{ value: '', label: '全部类型' }, ...['unlock', 'purchase', 'checkin', 'ad_reward', 'adjustment', 'refund_reserve', 'refund_release'].map(value => ({ value, label: value }))]} />{canExport && <Button onClick={async () => { try { const r = await api<{ filename: string; content: string }>('/admin/v1/coins/export?' + query); const url = URL.createObjectURL(new Blob(['﻿' + r.content], { type: 'text/csv;charset=utf-8' })); const a = document.createElement('a'); a.href = url; a.download = r.filename; a.click(); URL.revokeObjectURL(url); } catch (e) { message.error(errorText(e)); } }}>导出筛选账目</Button>}</Space>
      <Table rowKey="id" pagination={false} loading={state.loading} dataSource={state.data.items} scroll={{ x: 700 }} columns={[{ title: '时间', dataIndex: 'created_at', render: formatTime }, { title: '用户', dataIndex: 'user_id' }, { title: '类型', dataIndex: 'biz_type' }, { title: '数量', dataIndex: 'amount' }, { title: '余额', dataIndex: 'balance_after' }, { title: '说明', dataIndex: 'note' }]} /><Space><Button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>上一页</Button><Button disabled={!state.data.has_more} onClick={() => setOffset(offset + 50)}>下一页</Button></Space>
    </Card><Card title="最近 30 个有账目业务日"><Table rowKey="day" dataSource={stats.data.items} columns={[{ title: '日期', dataIndex: 'day' }, { title: '净变动', dataIndex: 'amount' }, { title: '笔数', dataIndex: 'count' }]} /></Card></Space>;
}

export function CommerceFinancePage() {
  const [offset, setOffset] = useState(0); const state = useLoad<{ items: any[]; has_more: boolean }>('/admin/v1/commerce/orders?limit=50&offset=' + offset, { items: [], has_more: false }); const canRefund = usePermission('finance.refund'); const [order, setOrder] = useState<any>(null); const [reason, setReason] = useState(''); const [busy, setBusy] = useState(false);
  return <Space direction="vertical" size={20} style={{ width: '100%' }}><Typography.Title level={3}>财务订单</Typography.Title><Alert type="info" showIcon message="全额退款仅支持可以证明未消费的订单" description="金币已按来源消耗、会员已用于观看、历史来源不完整的订单转人工处理。退款处理中冻结对应权益；失败自动释放，成功只收回本订单权益。" /><ErrorBanner error={state.error} retry={state.reload} />
    <Card><Table rowKey="order_no" loading={state.loading} dataSource={state.data.items} pagination={false} scroll={{ x: 1000 }} columns={[{ title: '订单', dataIndex: 'order_no' }, { title: '商品', render: (_, o: any) => <>{o.product_name}<br /><Typography.Text type="secondary">{o.product_type} · {o.account_code}</Typography.Text></> }, { title: '金额', render: (_, o: any) => money(o.amount_cents) + ' ' + o.currency }, { title: '状态', dataIndex: 'status', render: status }, { title: '支付时间', dataIndex: 'paid_at', render: formatTime }, { title: '操作', render: (_, o: any) => canRefund && ['paid', 'refund_pending'].includes(o.status) && <Button onClick={() => { setOrder(o); setReason(''); }}>{o.status === 'refund_pending' ? '查询 / 重试退款' : '全额退款'}</Button> }]} /><Space><Button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>上一页</Button><Button disabled={!state.data.has_more} onClick={() => setOffset(offset + 50)}>下一页</Button></Space></Card>
    <Modal title="全额退款" open={!!order} onCancel={() => setOrder(null)} confirmLoading={busy} okText="提交全额退款" onOk={async () => { if (!reason.trim()) { message.error('请填写退款原因'); return; } setBusy(true); try { const r = await api<any>('/admin/v1/finance/orders/' + order.order_no + '/refund', { method: 'POST', body: JSON.stringify({ reason, idempotency_key: 'refund-' + order.order_no }) }); message.info('退款状态：' + r.status); setOrder(null); await state.reload(); } catch (e) { message.error(errorText(e)); await state.reload(); } finally { setBusy(false); } }}><Typography.Paragraph>{order?.order_no} · {money(order?.amount_cents || 0)}</Typography.Paragraph><Input.TextArea aria-label="退款原因" value={reason} onChange={e => setReason(e.target.value)} maxLength={500} rows={3} /></Modal>
  </Space>;
}

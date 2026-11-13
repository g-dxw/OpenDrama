import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Select, Space, Table, Tag, Typography } from 'antd';
import { api, errorText } from '../api';
import type { BusinessTenant } from '../identity';

type AuditPayload = Record<string, unknown> & { tenant_id?: string; outcome?: string; method?: string; path?: string };
type AuditRow = {
  id?: number; created_at?: string; actor_id?: number; actor_username?: string; actor_scope?: string | null;
  tenant_id?: string; tenant_name?: string; action?: string; outcome?: string;
  target_type?: string | null; target_id?: number | string | null; payload?: AuditPayload | null;
};
const ACTION_LABELS: Record<string, string> = {
  'platform.tenant.create': '开通租户', 'platform.tenant.update': '更新租户', 'platform.modules.update': '调整业务模块',
  'platform.tenant.enter': '平台进入租户', 'tenant.support.exit': '平台退出租户',
  'platform.domain.create': '添加入口域名', 'platform.domain.delete': '删除入口域名',
  'platform.administrator.create': '新增租户管理员', 'platform.administrator.password.reset': '重置管理员密码',
  'tenant.role.create': '新增租户角色', 'tenant.role.update': '更新租户角色', 'tenant.role.delete': '删除租户角色',
  'tenant.staff.create': '新增租户员工', 'tenant.staff.update': '更新租户员工',
  'account.password.change': '修改账号密码', 'platform.logout': '退出平台', 'tenant.logout': '退出租户',
};
function requestSummary(row: AuditRow) {
  const method = typeof row.payload?.method === 'string' ? row.payload.method : '';
  const path = typeof row.payload?.path === 'string' ? row.payload.path : '';
  return method && path ? method + ' ' + path : '';
}
function actorScope(scope?: string | null) {
  return scope === 'platform' ? '平台' : scope === 'tenant' ? '租户' : '历史';
}
export default function PlatformAudit() {
  const [tenants, setTenants] = useState<BusinessTenant[]>([]); const [tenantId, setTenantId] = useState<string>();
  const [rows, setRows] = useState<AuditRow[]>([]); const [loading, setLoading] = useState(false); const [error, setError] = useState('');
  const [expanded, setExpanded] = useState<string[]>([]); const generation = useRef(0);
  useEffect(() => { void api<BusinessTenant[]>('/platform/v1/tenants').then(setTenants).catch(() => {}); }, []);
  const load = useCallback(async () => {
    const current = ++generation.current; setLoading(true); setError(''); setExpanded([]); setRows([]);
    try {
      const result = await api<{ items: AuditRow[] }>('/platform/v1/audit' + (tenantId ? '?tenant_id=' + encodeURIComponent(tenantId) : ''));
      if (current === generation.current) setRows(result.items);
    } catch (e) { if (current === generation.current) setError(errorText(e)); }
    finally { if (current === generation.current) setLoading(false); }
  }, [tenantId]);
  useEffect(() => { void load(); return () => { generation.current++; }; }, [load]);
  return <Space direction="vertical" size="middle" style={{ width: '100%' }}>
    <Card><Space direction="vertical" style={{ width: '100%' }}>
      <Typography.Title level={4} style={{ margin: 0 }}>平台审计</Typography.Title>
      <Typography.Text type="secondary">查看租户管理及平台支持访问记录。展开记录可核对请求路径、操作对象与资源明细。</Typography.Text>
      <Space wrap><Select aria-label="按租户筛选审计" allowClear placeholder="全部租户" value={tenantId} onChange={setTenantId} style={{ minWidth: 220 }} options={tenants.map(tenant => ({ value: tenant.id, label: tenant.name }))} /><Button onClick={() => void load()}>刷新</Button></Space>
    </Space></Card>
    {error && <Alert type="error" showIcon message={error} />}
    <Table<AuditRow> rowKey={(row, index) => String(row.id ?? index)} dataSource={rows} loading={loading} scroll={{ x: 1180 }}
      expandable={{
        columnTitle: '详情', columnWidth: 90, expandedRowKeys: expanded,
        onExpandedRowsChange: keys => setExpanded(keys.map(String)),
        expandIcon: ({ expanded: isExpanded, onExpand, record }) => <Button type="link" size="small" aria-label={(isExpanded ? '收起审计详情 ' : '查看审计详情 ') + record.id} onClick={event => onExpand(record, event)}>{isExpanded ? '收起' : '查看详情'}</Button>,
        expandedRowRender: row => <Card size="small" title={'审计记录 #' + (row.id ?? '—')}>
          {requestSummary(row) && <Typography.Paragraph code style={{ marginBottom: 12, overflowWrap: 'anywhere' }}>{requestSummary(row)}</Typography.Paragraph>}
          <pre aria-label="审计详情 JSON" style={{ margin: 0, maxHeight: 360, overflow: 'auto', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', fontSize: 12, lineHeight: 1.6 }}>{JSON.stringify({
            actor_scope: row.actor_scope ?? 'legacy', actor_id: row.actor_id ?? null,
            target_type: row.target_type ?? null, target_id: row.target_id ?? null, payload: row.payload ?? {},
          }, null, 2)}</pre>
        </Card>,
      }}
      columns={[
        { title: '时间', width: 170, render: (_, row) => row.created_at ? new Date(row.created_at).toLocaleString() : '—' },
        { title: '操作', width: 290, render: (_, row) => <Space direction="vertical" size={2} style={{ maxWidth: 290 }}>
          <Typography.Text style={{ overflowWrap: 'anywhere' }}>{requestSummary(row) || ACTION_LABELS[row.action || ''] || row.action || '—'}</Typography.Text>
          {row.action && <Typography.Text type="secondary" style={{ fontSize: 12, overflowWrap: 'anywhere' }}>{row.action}</Typography.Text>}
        </Space> },
        { title: '操作人', width: 150, render: (_, row) => <Space direction="vertical" size={2}>
          <span>{row.actor_username || (row.actor_id != null ? '账号 #' + row.actor_id : '—')}</span>
          <Tag color={row.actor_scope === 'platform' ? 'blue' : row.actor_scope === 'tenant' ? 'green' : undefined}>{actorScope(row.actor_scope)}</Tag>
        </Space> },
        { title: '操作对象', width: 160, render: (_, row) => <Space direction="vertical" size={2}>
          <Typography.Text code>{row.target_type || '—'}</Typography.Text>
          <Typography.Text type="secondary">{row.target_id != null ? '#' + row.target_id : '—'}</Typography.Text>
        </Space> },
        { title: '租户', width: 180, render: (_, row) => row.tenant_name || tenants.find(tenant => tenant.id === (row.tenant_id || row.payload?.tenant_id))?.name || row.tenant_id || row.payload?.tenant_id || '—' },
        { title: '结果', width: 90, render: (_, row) => <Tag>{row.outcome || row.payload?.outcome || '已记录'}</Tag> },
      ]} />
  </Space>;
}

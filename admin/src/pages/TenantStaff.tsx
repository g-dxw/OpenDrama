import { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Card, Form, Input, Modal, Popconfirm, Select, Space, Table, Tag, Tooltip, Typography, message } from 'antd';
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { api, errorText } from '../api';
import { useIdentity } from '../identity';
import { useTenant } from '../tenant';
import TemporaryCredential, { type Credential } from '../components/TemporaryCredential';
import type { TenantRole } from '../tenant-permissions';
type TenantStaffRow = { id: number; username: string; display_name: string; status: string; role_id: string | null; role_name: string; is_partner?: boolean; is_tenant_admin: boolean; must_change_password: boolean };
export default function TenantStaff() {
  const identity = useIdentity(); const { refresh } = useTenant();
  const [rows, setRows] = useState<TenantStaffRow[]>([]); const [roles, setRoles] = useState<TenantRole[]>([]); const [loading, setLoading] = useState(false); const [error, setError] = useState('');
  const [editing, setEditing] = useState<TenantStaffRow | 'new' | null>(null); const [form] = Form.useForm(); const [saving, setSaving] = useState(false); const [credential, setCredential] = useState<Credential | null>(null);
  const load = useCallback(async () => { setLoading(true); setError(''); try { const result = await api<{ items: TenantStaffRow[]; roles: TenantRole[] }>('/tenant/v1/staff'); setRows(result.items); setRoles(result.roles); } catch (e) { setError(errorText(e)); } finally { setLoading(false); } }, []);
  useEffect(() => { void load(); }, [load]);
  const open = (row: TenantStaffRow | 'new') => { form.resetFields(); form.setFieldsValue(row === 'new' ? { role_id: roles.find(role => role.builtin_key === 'ops' && role.kind !== 'tenant_admin')?.id || roles.find(role => role.kind !== 'tenant_admin')?.id } : { display_name: row.display_name, role_id: row.role_id }); setEditing(row); };
  const save = async () => {
    if (!editing || error) return; const values = await form.validateFields().catch(() => null); if (!values) return; setSaving(true);
    try {
      if (editing === 'new') { const result = await api<TenantStaffRow & { temporary_password: string }>('/tenant/v1/staff', { method: 'POST', body: JSON.stringify(values) }); setCredential({ username: result.username, temporary_password: result.temporary_password, tenant_code: identity?.tenant?.code }); }
      else { if (editing.is_partner) delete values.role_id; await api('/tenant/v1/staff/' + editing.id, { method: 'PATCH', body: JSON.stringify(values) }); }
      setEditing(null); message.success('租户员工已保存'); await refresh(); await load();
    } catch (e) { message.error(errorText(e)); } finally { setSaving(false); }
  };
  const status = async (row: TenantStaffRow) => { try { await api('/tenant/v1/staff/' + row.id, { method: 'PATCH', body: JSON.stringify({ status: row.status === 'active' ? 'disabled' : 'active' }) }); await refresh(); await load(); } catch (e) { message.error(errorText(e)); } };
  const reset = async (row: TenantStaffRow) => { try { const result = await api<{ temporary_password: string; username?: string }>('/tenant/v1/staff/' + row.id + '/reset-password', { method: 'POST', body: '{}' }); setCredential({ username: result.username || row.username, temporary_password: result.temporary_password, tenant_code: identity?.tenant?.code }); await load(); } catch (e) { message.error(errorText(e)); } };
  return <Space direction="vertical" size="middle" style={{ width: '100%' }}>
    <Card><Space wrap style={{ width: '100%', justifyContent: 'space-between' }}><div><Typography.Title level={4} style={{ margin: '0 0 6px' }}>租户员工</Typography.Title><Typography.Text type="secondary">账号只属于当前租户。为本租户创建员工并分配角色，不从其他租户或平台选择账号。</Typography.Text></div><Space><Button icon={<ReloadOutlined aria-hidden />} onClick={() => void load()}>刷新</Button><Button type="primary" icon={<PlusOutlined aria-hidden />} disabled={loading || !!error} onClick={() => open('new')}>新增员工</Button></Space></Space></Card>
    {error && <Alert showIcon type="error" message={error} />}
    <Table<TenantStaffRow> rowKey="id" loading={loading} dataSource={rows} pagination={false} scroll={{ x: 800 }} columns={[
      { title: '账号', render: (_, row) => <Space><Typography.Text strong>{row.username}</Typography.Text>{row.id === identity?.user.id && <Tag>当前账号</Tag>}</Space> },
      { title: '姓名', dataIndex: 'display_name' }, { title: '角色', render: (_, row) => <Space>{row.is_partner ? '上剧方账号' : row.role_name}{row.is_tenant_admin && <Tag color="blue">租户管理员</Tag>}</Space> },
      { title: '状态', render: (_, row) => <Space><Tag color={row.status === 'active' ? 'green' : 'default'}>{row.status === 'active' ? '启用' : '已禁用'}</Tag>{row.must_change_password && <Tag>待首次改密</Tag>}</Space> },
      { title: '操作', render: (_, row) => <Space wrap size={4}><Button type="link" disabled={!!error} onClick={() => open(row)}>编辑</Button>
        <Tooltip title={row.id === identity?.user.id ? '修改自己的密码请使用账号菜单' : undefined}><span><Popconfirm title="重置此员工的密码？" description="重置后原会话失效，首次使用临时密码登录需要改密。" onConfirm={() => reset(row)} disabled={row.id === identity?.user.id}><Button type="link" disabled={row.id === identity?.user.id || !!error}>重置密码</Button></Popconfirm></span></Tooltip>
        <Popconfirm title={row.status === 'active' ? '禁用此员工？' : '启用此员工？'} onConfirm={() => status(row)}><Button type="link" danger={row.status === 'active'} disabled={!!error}>{row.status === 'active' ? '禁用' : '启用'}</Button></Popconfirm>
      </Space> },
    ]} />
    <Modal open={!!editing} title={editing === 'new' ? '新增租户员工' : '编辑租户员工'} onCancel={() => { if (!saving) setEditing(null); }} closable={!saving} maskClosable={!saving} keyboard={!saving} onOk={() => void save()} confirmLoading={saving} okText="保存" cancelText="取消" forceRender>
      <Form form={form} layout="vertical">
        {editing === 'new' && <Form.Item name="username" label="账号" rules={[{ required: true }]}><Input maxLength={64} autoComplete="off" /></Form.Item>}
        <Form.Item name="display_name" label="姓名"><Input maxLength={100} /></Form.Item>
        <Form.Item name="role_id" label="租户角色" rules={editing !== 'new' && editing?.is_partner ? [] : [{ required: true, message: '请选择角色' }]}><Select disabled={editing !== 'new' && !!editing?.is_partner} placeholder={editing !== 'new' && editing?.is_partner ? '上剧方账号角色不可更改' : undefined} options={roles.map(role => ({ value: role.id, label: role.name + (role.kind === 'tenant_admin' ? ' · 受保护' : '') }))} /></Form.Item>
        {editing === 'new' && <Typography.Text type="secondary">系统会生成仅展示一次的临时密码，员工首次登录后需自行修改。</Typography.Text>}
      </Form>
    </Modal>
    <TemporaryCredential value={credential} onClose={() => setCredential(null)} />
  </Space>;
}

import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Checkbox, Drawer, Empty, Form, Input, Modal, Popconfirm, Select, Space, Table, Tabs, Tag, Typography, message } from 'antd';
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { api, errorText } from '../api';
import { storeSupport, tenantEntryUrl } from '../entry';
import type { BusinessTenant, LoginResult } from '../identity';
import TemporaryCredential, { type Credential } from '../components/TemporaryCredential';
type ModuleItem = { key: string; label: string; menu_key?: string; enabled: boolean; supported: boolean; backend: string; reason?: string };
type ModulePayload = { version: number; items: ModuleItem[] };
type Domain = { id: string; hostname: string; kind: 'admin' | 'site'; status: string; verified_at: string | null; verification_record?: string; verification_token?: string; cname_target?: string | null };
type Administrator = { id: number; username: string; display_name?: string; status?: string; must_change_password?: boolean };
const base = (id: string) => '/platform/v1/tenants/' + encodeURIComponent(id);
export default function Tenants() {
  const [rows, setRows] = useState<BusinessTenant[]>([]); const [loading, setLoading] = useState(false); const [error, setError] = useState('');
  const [editing, setEditing] = useState<BusinessTenant | 'new' | null>(null); const [form] = Form.useForm(); const [saving, setSaving] = useState(false);
  const [active, setActive] = useState<BusinessTenant | null>(null); const [tab, setTab] = useState('modules');
  const [modules, setModules] = useState<ModulePayload | null>(null); const [picked, setPicked] = useState<string[]>([]); const [domains, setDomains] = useState<Domain[]>([]); const [admins, setAdmins] = useState<Administrator[]>([]);
  const detailGeneration = useRef(0); const activeId = useRef<string | null>(null); activeId.current = active?.id || null;
  const [detailLoading, setDetailLoading] = useState(false); const [detailError, setDetailError] = useState(''); const [detailSaving, setDetailSaving] = useState(false);
  const [domainForm] = Form.useForm(); const [adminForm] = Form.useForm(); const [credential, setCredential] = useState<Credential | null>(null); const [entering, setEntering] = useState<string | null>(null);
  const load = useCallback(async () => { setLoading(true); setError(''); try { setRows(await api<BusinessTenant[]>('/platform/v1/tenants')); } catch (e) { setError(errorText(e)); } finally { setLoading(false); } }, []);
  useEffect(() => { void load(); }, [load]);
  const openEdit = (row: BusinessTenant | 'new') => { form.resetFields(); form.setFieldsValue(row === 'new' ? { admin_username: 'tenant-admin', target_markets: ['US'], default_locale: 'en', locales: ['en','zh'], currency: 'USD', timezone: 'UTC', web_url: '', h5_url: '' } : { name: row.name, target_markets:row.target_markets||[], default_locale:row.default_locale||'en', locales:row.locales||['en'], currency:row.currency||'USD', timezone:row.timezone||'UTC', web_url:row.web_url||'', h5_url:row.h5_url||'' }); setEditing(row); };
  const save = async () => {
    const values = await form.validateFields().catch(() => null); if (!values || !editing) return;
    setSaving(true);
    try {
      if (editing === 'new') {
        const result = await api<{ tenant: BusinessTenant; administrator: { username: string; temporary_password: string } }>('/platform/v1/tenants', { method: 'POST', body: JSON.stringify({ ...values, code: values.code.trim().toLowerCase(), name: values.name.trim(), admin_username: values.admin_username.trim() }) });
        setCredential({ ...result.administrator, tenant_code: result.tenant.code });
      } else await api(base(editing.id), { method: 'PATCH', body: JSON.stringify({ ...values, version: editing.version, name: values.name.trim() }) });
      setEditing(null); await load(); message.success('租户已保存');
    } catch (e) { message.error(errorText(e)); } finally { setSaving(false); }
  };
  const status = async (row: BusinessTenant) => { try { await api(base(row.id), { method: 'PATCH', body: JSON.stringify({ version: row.version, status: row.status === 'active' ? 'paused' : 'active' }) }); await load(); } catch (e) { message.error(errorText(e)); } };
  const enter = async (row: BusinessTenant) => {
    setEntering(row.id);
    try {
      const result = await api<LoginResult & { entry_url: string; expires_at: string }>(base(row.id) + '/enter', { method: 'POST', body: '{}' });
      storeSupport(row.code, { token: result.token, expires_at: result.expires_at, tenant_id: row.id, operator: result.context.user.display_name || result.context.user.username });
      window.location.assign(tenantEntryUrl(row.code, true) + '/');
    } catch (e) { message.error(errorText(e)); setEntering(null); }
  };
  const loadDetail = useCallback(async () => {
    if (!active || active.id !== activeId.current) return; const generation = ++detailGeneration.current; setDetailLoading(true); setDetailError('');
    try {
      const [m, d, a] = await Promise.all([api<ModulePayload>(base(active.id) + '/modules'), api<{ items: Domain[] }>(base(active.id) + '/domains'), api<{ items: Administrator[] }>(base(active.id) + '/administrators')]);
      if (generation !== detailGeneration.current || active.id !== activeId.current) return;
      setModules(m); setPicked(m.items.filter(item => item.enabled).map(item => item.key)); setDomains(d.items); setAdmins(a.items);
    } catch (e) { if (generation === detailGeneration.current) setDetailError(errorText(e)); } finally { if (generation === detailGeneration.current) setDetailLoading(false); }
  }, [active?.id]);
  useEffect(() => { setModules(null); setDomains([]); setAdmins([]); domainForm.resetFields(); adminForm.resetFields(); if (active) void loadDetail(); return () => { detailGeneration.current++; }; }, [active?.id, loadDetail]);
  const saveModules = async () => {
    if (!active || !modules || detailError) return; setDetailSaving(true);
    try { const result = await api<ModulePayload>(base(active.id) + '/modules', { method: 'PUT', body: JSON.stringify({ version: modules.version, module_keys: picked }) }); if (active.id === activeId.current) { setModules(result); setPicked(result.items.filter(item => item.enabled).map(item => item.key)); } await load(); message.success('业务模块已保存'); }
    catch (e) { setDetailError(errorText(e)); } finally { setDetailSaving(false); }
  };
  const createDomain = async () => { if (!active) return; const values = await domainForm.validateFields().catch(() => null); if (!values) return; setDetailSaving(true); try { await api(base(active.id) + '/domains', { method: 'POST', body: JSON.stringify(values) }); domainForm.resetFields(); await loadDetail(); message.success('域名已保存，等待验证'); } catch (e) { message.error(errorText(e)); } finally { setDetailSaving(false); } };
  const createAdmin = async () => { if (!active) return; const values = await adminForm.validateFields().catch(() => null); if (!values) return; setDetailSaving(true); try { const result = await api<Administrator & { temporary_password: string }>(base(active.id) + '/administrators', { method: 'POST', body: JSON.stringify(values) }); setCredential({ username: result.username, temporary_password: result.temporary_password, tenant_code: active.code }); adminForm.resetFields(); await loadDetail(); } catch (e) { message.error(errorText(e)); } finally { setDetailSaving(false); } };
  return <Space direction="vertical" size="middle" style={{ width: '100%' }}>
    <Card><Space wrap style={{ width: '100%', justifyContent: 'space-between' }}><div><Typography.Title level={4} style={{ margin: '0 0 6px' }}>站点管理</Typography.Title><Typography.Text type="secondary">管理租户开通、业务模块、入口域名及租户管理员。进入业务后台会创建有时限的支持会话。</Typography.Text></div><Space><Button icon={<ReloadOutlined aria-hidden />} onClick={() => void load()}>刷新</Button><Button type="primary" icon={<PlusOutlined aria-hidden />} onClick={() => openEdit('new')}>新增租户</Button></Space></Space></Card>
    {error && <Alert showIcon type="error" message={error} />}
    <Table<BusinessTenant> rowKey="id" loading={loading} dataSource={rows} pagination={false} scroll={{ x: 900 }} locale={{ emptyText: <Empty description="暂无租户" /> }} columns={[
      { title: '租户', render: (_, row) => <Space direction="vertical" size={0}><Typography.Text strong>{row.name}</Typography.Text><Typography.Text type="secondary">{row.code}</Typography.Text></Space> },
      { title: '类型', render: () => '独立站点管理' },
      { title: '状态', render: (_, row) => <Tag color={row.status === 'active' ? 'green' : 'default'}>{row.status === 'active' ? '启用' : '已暂停'}</Tag> },
      { title: '操作', render: (_, row) => <Space wrap size={4}>
        <Button type="link" disabled={row.status !== 'active'} loading={entering === row.id} onClick={() => void enter(row)}>进入租户 · 30 分钟</Button>
        <Button type="link" onClick={() => openEdit(row)}>站点配置</Button><Button type="link" onClick={() => { setTab('modules'); setActive(row); }}>开通与入口</Button>
        <Popconfirm title={row.status === 'active' ? '暂停此租户？' : '启用此租户？'} onConfirm={() => status(row)}><Button type="link" danger={row.status === 'active'}>{row.status === 'active' ? '暂停' : '启用'}</Button></Popconfirm>
      </Space> },
    ]} />
    <Modal open={!!editing} title={editing === 'new' ? '开通站点管理' : '修改站点配置'} onCancel={() => { if (!saving) setEditing(null); }} closable={!saving} maskClosable={!saving} keyboard={!saving} onOk={() => void save()} confirmLoading={saving} okText="保存" cancelText="取消" forceRender>
      <Form form={form} layout="vertical">
        {editing === 'new' && <Form.Item name="code" label="租户标识" rules={[{ required: true }, { pattern: /^[a-z0-9][a-z0-9-]{1,63}$/i, message: '使用 2–64 位字母、数字或短横线' }]}><Input placeholder="将用于独立登录地址" maxLength={64} /></Form.Item>}
        <Form.Item name="name" label="租户名称" rules={[{ required: true, whitespace: true }]}><Input maxLength={100} /></Form.Item>
<Form.Item name="target_markets" label="运营国家代码 / Market countries" rules={[{required:true,type:'array'}]}><Select mode="tags" tokenSeparators={[',',' ']} options={['US','IN','GB','SG','MY','ID','PH','AU','CA','BR'].map(value=>({value}))}/></Form.Item><Form.Item name="locales" label="可用语言 / Languages" rules={[{required:true,type:'array'}]}><Select mode="multiple" options={[{value:'en',label:'English'},{value:'zh',label:'中文'}]}/></Form.Item><Form.Item name="default_locale" label="默认语言 / Default language" rules={[{required:true}]}><Select options={[{value:'en',label:'English'},{value:'zh',label:'中文'}]}/></Form.Item><Form.Item name="currency" label="币种 / Currency" rules={[{required:true}]}><Select options={['USD','INR','EUR','GBP','SGD','MYR','IDR','PHP','AUD','CAD','BRL'].map(value=>({value}))}/></Form.Item><Form.Item name="timezone" label="时区 / Timezone" rules={[{required:true}]}><Select showSearch options={['UTC','Asia/Kolkata','Asia/Shanghai','Asia/Singapore','Asia/Jakarta','Asia/Manila','Europe/London','America/New_York','America/Los_Angeles','America/Sao_Paulo','Australia/Sydney'].map(value=>({value}))}/></Form.Item><Form.Item name="web_url" label="桌面站地址 / Desktop URL" rules={[{type:'url'}]}><Input placeholder="https://watch.example.com"/></Form.Item><Form.Item name="h5_url" label="移动站地址 / Mobile URL" rules={[{type:'url'}]}><Input placeholder="https://watch.example.com"/></Form.Item>
        {editing === 'new' && <Form.Item name="admin_username" label="首位租户管理员账号" rules={[{ required: true }]}><Input maxLength={64} /></Form.Item>}
      </Form>
    </Modal>
    <Drawer open={!!active} title={active?.name + ' · 开通与入口'} width="min(900px, 100vw)" onClose={() => { if (!detailSaving) setActive(null); }} closable={!detailSaving} maskClosable={!detailSaving} keyboard={!detailSaving} destroyOnHidden>
      <Space direction="vertical" style={{ width: '100%' }} size="middle">
        {detailError && <Alert showIcon type="error" message={detailError} action={<Button onClick={() => void loadDetail()}>重新加载</Button>} />}
        <Typography.Link href={active ? tenantEntryUrl(active.code) + '/login' : '#'} target="_blank" rel="noreferrer">租户登录入口：{active ? tenantEntryUrl(active.code) + '/login' : ''}</Typography.Link>
        <Tabs activeKey={tab} onChange={setTab} items={[
          { key: 'modules', label: '业务模块', children: <Space direction="vertical" style={{ width: '100%' }} size="middle"><Typography.Text type="secondary">开通模块决定租户可以使用哪些业务。租户管理员只能在已开通范围内分配员工权限。</Typography.Text>
            {modules?.items.map(item => <Card size="small" key={item.key}><Checkbox checked={picked.includes(item.key)} disabled={!item.supported || detailLoading || detailSaving} onChange={event => setPicked(values => event.target.checked ? [...values, item.key] : values.filter(key => key !== item.key))}>{item.label}</Checkbox>{!item.supported && <Typography.Text type="secondary"> · {item.reason || '当前后端不支持'}</Typography.Text>}</Card>)}
            <Button type="primary" loading={detailSaving} disabled={detailLoading || !!detailError || !modules} onClick={() => void saveModules()}>保存模块开通</Button></Space> },
          { key: 'domains', label: '入口域名', children: <Space direction="vertical" style={{ width: '100%' }} size="middle"><Alert showIcon type="info" message="域名保存后为待验证状态" description="添加所示 TXT 验证记录与 CNAME 后点击验证。DNS 验证与 HTTPS 证书状态分别确认；部署入口须允许此域名。" />
            <Table<Domain> rowKey="id" size="small" loading={detailLoading} dataSource={domains} pagination={false} scroll={{ x: 500 }} columns={[{ title: '域名', dataIndex: 'hostname' },{ title: '用途', render: (_, row) => row.kind === 'admin' ? '租户后台' : '客户端站点' },{ title: '状态', render: (_, row) => <Tag>{row.status === 'pending' ? '待验证' : row.status}</Tag> },{ title: 'DNS 验证', render: (_, row) => row.status === 'active' ? <Tag color="green">已验证</Tag> : <Space direction="vertical" size={4}><Typography.Text code>TXT {row.verification_record}</Typography.Text><Typography.Text copyable>{row.verification_token}</Typography.Text>{row.cname_target&&<Typography.Text code>CNAME → {row.cname_target}</Typography.Text>}<Button loading={detailSaving} onClick={async()=>{if(!active)return;setDetailSaving(true);try{await api(base(active.id)+'/domains/'+encodeURIComponent(row.id)+'/verify',{method:'POST',body:'{}'});await loadDetail();message.success('域名验证通过 / Domain verified');}catch(e){message.error(errorText(e));}finally{setDetailSaving(false);}}}>验证 DNS / Verify DNS</Button></Space> },{ title: '操作', render: (_, row) => <Popconfirm title="删除此域名记录？" onConfirm={async () => { if (!active) return; try { await api(base(active.id) + '/domains/' + encodeURIComponent(row.id), { method: 'DELETE' }); await loadDetail(); } catch (e) { message.error(errorText(e)); } }}><Button type="link" danger>删除</Button></Popconfirm> }]} />
            <Form form={domainForm} layout="vertical" initialValues={{ kind: 'admin' }}><Form.Item name="hostname" label="域名" rules={[{ required: true }]}><Input placeholder="例如 admin.example.com" /></Form.Item><Form.Item name="kind" label="用途" rules={[{ required: true }]}><Select options={[{ value: 'admin', label: '租户后台' },{ value: 'site', label: '客户端站点' }]} /></Form.Item><Button loading={detailSaving} disabled={detailLoading || !!detailError} onClick={() => void createDomain()}>保存待验证域名</Button></Form></Space> },
          { key: 'administrators', label: '租户管理员', children: <Space direction="vertical" style={{ width: '100%' }} size="middle"><Typography.Text type="secondary">租户管理员负责该租户的员工与角色，不拥有平台管理权限。</Typography.Text>
            <Table<Administrator> rowKey="id" size="small" loading={detailLoading} dataSource={admins} pagination={false} scroll={{ x: 480 }} columns={[{ title: '账号', dataIndex: 'username' },{ title: '姓名', dataIndex: 'display_name' },{ title: '状态', render: (_, row) => row.must_change_password ? <Tag>待首次改密</Tag> : <Tag>{row.status === 'disabled' ? '已禁用' : '可登录'}</Tag> },{ title: '操作', render: (_, row) => <Popconfirm title="重置此管理员的密码？" onConfirm={async () => { if (!active) return; try { const result = await api<{ temporary_password: string; username: string }>(base(active.id) + '/administrators/' + row.id + '/reset-password', { method: 'POST', body: '{}' }); setCredential({ ...result, tenant_code: active.code }); await loadDetail(); } catch (e) { message.error(errorText(e)); } }}><Button type="link">重置密码</Button></Popconfirm> }]} />
            <Form form={adminForm} layout="vertical"><Form.Item name="username" label="新管理员账号" rules={[{ required: true }]}><Input maxLength={64} /></Form.Item><Form.Item name="display_name" label="姓名"><Input maxLength={100} /></Form.Item><Button loading={detailSaving} disabled={detailLoading || !!detailError} onClick={() => void createAdmin()}>创建管理员并生成临时密码</Button></Form></Space> },
        ]} />
      </Space>
    </Drawer>
    <TemporaryCredential value={credential} onClose={() => setCredential(null)} />
  </Space>;
}

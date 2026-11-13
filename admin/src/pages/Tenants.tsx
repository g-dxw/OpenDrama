import { useRef, useState } from "react";
import { Alert, Button, Card, Empty, Form, Input, Modal, Popconfirm, Select, Space, Table, Tag, Typography, message } from "antd";
import { PlusOutlined, ReloadOutlined, TeamOutlined } from "@ant-design/icons";
import { api, errorText } from "../api";
import { useMe } from "../me";
import TenantRoles from "./TenantRoles";
import type { TenantRole } from "../tenant-permissions";
import { useTenant, type Tenant } from "../tenant";

type Member = { user_id: number; username?: string; display_name?: string; role?: string; role_id?: string | null; role_name?: string };
type Staff = { id: number; username: string; display_name: string; role: string };
type Memberships = { members: Member[]; staff: Staff[]; roles: TenantRole[] };


export default function Tenants() {
  const me = useMe();
  const { items, current, refresh, select } = useTenant();
  const [editing, setEditing] = useState<Tenant | "new" | null>(null);
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);
  const [memberTenant, setMemberTenant] = useState<Tenant | null>(null);
  const [members, setMembers] = useState<Member[]>([]);
  const [memberRoles, setMemberRoles] = useState<TenantRole[]>([]);
  const [roleTenant, setRoleTenant] = useState<Tenant | null>(null);
  const [staff, setStaff] = useState<Staff[]>([]);
  const [memberLoading, setMemberLoading] = useState(false);
  const [memberError, setMemberError] = useState("");
  const memberGeneration = useRef(0);
  const [memberSaving, setMemberSaving] = useState(false);
  if ((me?.platform_role ?? me?.role) !== "admin") return <Alert type="warning" showIcon message="仅平台管理员可管理租户" />;

  const open = (tenant: Tenant | "new") => {
    form.resetFields();
    form.setFieldsValue({ name: tenant === "new" ? "" : tenant.name });
    setEditing(tenant);
  };
  const save = async () => {
    const values = await form.validateFields().catch(() => null);
    if (!values) return;
    setSaving(true);
    try {
      if (editing === "new") await api("/admin/v1/tenants", { method: "POST", body: JSON.stringify({ name: values.name.trim() }) });
      else if (editing) await api("/admin/v1/tenants/" + encodeURIComponent(editing.id), { method: "PATCH", body: JSON.stringify({ name: values.name.trim() }) });
      message.success(editing === "new" ? "租户已创建" : "名称已保存");
      setEditing(null);
      await refresh();
    } catch (e) { message.error(errorText(e)); }
    finally { setSaving(false); }
  };
  const changeStatus = async (tenant: Tenant) => {
    try {
      await api("/admin/v1/tenants/" + encodeURIComponent(tenant.id), { method: "PATCH", body: JSON.stringify({ status: tenant.status === "active" ? "paused" : "active" }) });
      await refresh();
      message.success(tenant.status === "active" ? "租户已暂停" : "租户已启用");
    } catch (e) { message.error(errorText(e)); }
  };
  const openMembers = async (tenant: Tenant) => {
    const generation = ++memberGeneration.current;
    setMemberTenant(tenant); setMembers([]); setStaff([]); setMemberRoles([]); setMemberError(""); setMemberLoading(true);
    try {
      const result = await api<Memberships>("/admin/v1/tenants/" + encodeURIComponent(tenant.id) + "/members");
      if (generation === memberGeneration.current) { setMembers(result.members.map(member => ({ ...member, role_id: member.role_id || result.roles.find(role => role.builtin_key === member.role)?.id || null }))); setStaff(result.staff); setMemberRoles(result.roles); }
    } catch (e) { if (generation === memberGeneration.current) setMemberError(errorText(e)); }
    finally { if (generation === memberGeneration.current) setMemberLoading(false); }
  };
  const saveMembers = async () => {
    if (!memberTenant || memberError || memberLoading || members.some(member => !member.role_id)) return;
    setMemberSaving(true);
    try {
      await api("/admin/v1/tenants/" + encodeURIComponent(memberTenant.id) + "/members", {
        method: "PUT", body: JSON.stringify({ members: members.map(({ user_id, role_id }) => ({ user_id, role_id })) }),
      });
      message.success("成员权限已保存"); setMemberTenant(null); await refresh();
    } catch (e) { message.error(errorText(e)); }
    finally { setMemberSaving(false); }
  };
  return <Space direction="vertical" size="middle" style={{ width: "100%" }}>
    <Card>
      <Space wrap style={{ justifyContent: "space-between", width: "100%" }}>
        <div><Typography.Title level={4} style={{ margin: "0 0 6px" }}>租户管理</Typography.Title>
          <Typography.Text type="secondary">管理各租户的名称、启用状态和运营成员。</Typography.Text></div>
        <Space><Button icon={<ReloadOutlined aria-hidden />} onClick={() => void refresh().catch(e => message.error(errorText(e)))}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined aria-hidden />} onClick={() => open("new")}>新增租户</Button></Space>
      </Space>
    </Card>
    <Table<Tenant> rowKey="id" dataSource={items} pagination={false} scroll={{ x: 740 }}
      locale={{ emptyText: <Empty description="暂无租户" /> }}
      columns={[
        { title: "租户名称", dataIndex: "name", render: (name: string, row) => <Space><Typography.Text strong>{name}</Typography.Text>{current?.id === row.id && <Tag color="red">当前租户</Tag>}</Space> },
        { title: "类型", dataIndex: "kind", render: value => value === "india_legacy" ? <Tag color="blue">India 内容运营</Tag> : "标准租户" },
        { title: "状态", dataIndex: "status", render: value => <Tag color={value === "active" ? "green" : "default"}>{value === "active" ? "启用" : "已暂停"}</Tag> },
        { title: "操作", width: 430, render: (_, row) => <Space wrap size={4}>
          <Button type="link" size="small" disabled={row.status !== "active"} onClick={() => select(row.id)}>进入租户</Button>
          <Button type="link" size="small" onClick={() => open(row)}>改名</Button>
          <Button type="link" size="small" onClick={() => setRoleTenant(row)}>角色与权限</Button>
          <Button type="link" size="small" icon={<TeamOutlined aria-hidden />} onClick={() => void openMembers(row)}>成员</Button>
          <Popconfirm title={row.status === "active" ? "暂停此租户？" : "重新启用此租户？"}
            description={row.status === "active" ? "暂停后成员将无法进入该租户开展业务。" : undefined}
            onConfirm={() => changeStatus(row)} okText="确认" cancelText="取消">
            <Button type="link" size="small" danger={row.status === "active"}>{row.status === "active" ? "暂停" : "启用"}</Button>
          </Popconfirm>
        </Space> },
      ]} />
    <Modal title={editing === "new" ? "新增租户" : "修改租户名称"} open={editing !== null} onCancel={() => { setEditing(null); form.resetFields(); }}
      onOk={() => void save()} confirmLoading={saving} okText="保存" cancelText="取消" destroyOnHidden>
      <Form form={form} layout="vertical" preserve={false}>
        <Form.Item label="租户名称" name="name" rules={[{ required: true, whitespace: true, message: "请输入租户名称" }, { max: 100, message: "最多 100 个字符" }]}>
          <Input autoFocus maxLength={100} placeholder="输入品牌或运营团队名称" />
        </Form.Item>
      </Form>
    </Modal>
    <Modal title={memberTenant ? memberTenant.name + " · 租户成员" : "租户成员"} open={memberTenant !== null}
      onCancel={() => { memberGeneration.current++; setMemberTenant(null); }} onOk={() => void saveMembers()} confirmLoading={memberSaving}
      okButtonProps={{ disabled: memberLoading || !!memberError || members.some(member => !member.role_id) }} okText="保存成员" cancelText="取消" width={720} destroyOnHidden>
      <Space direction="vertical" style={{ width: "100%" }} size="middle">
        {memberError && <Alert showIcon type="error" message={memberError} action={<Button onClick={() => memberTenant && void openMembers(memberTenant)}>重试</Button>} />}
        <Typography.Text type="secondary">每位成员在此租户绑定一个角色。平台管理员始终拥有该租户已开放的全部权限；其他成员按所选角色操作，平台管理功能仍受平台账号权限限制。</Typography.Text>
        <Select mode="multiple" aria-label="租户成员" placeholder="选择已有员工账号" loading={memberLoading}
          style={{ width: "100%" }} value={members.map(m => m.user_id)} optionFilterProp="label"
          options={staff.map(s => ({ value: s.id, label: (s.display_name || s.username) + " · " + s.username }))}
          onChange={(ids: number[]) => setMembers(ids.map(user_id => members.find(m => m.user_id === user_id) ?? { user_id, role_id: memberRoles.find(role => role.builtin_key === "ops")?.id || null }))} />
        <Table<Member> rowKey="user_id" loading={memberLoading} dataSource={members} pagination={false} size="small"
          columns={[
            { title: "成员", render: (_, row) => { const s = staff.find(s => s.id === row.user_id); return s?.display_name || s?.username || row.username || String(row.user_id); } },
            { title: "租户角色", render: (_, row) => <Select aria-label={"成员 " + row.user_id + " 的租户角色"} value={row.role_id || undefined} placeholder="选择角色" options={memberRoles.map(role => ({ value: role.id, label: role.name }))}
              style={{ width: 130 }} onChange={role_id => setMembers(items => items.map(m => m.user_id === row.user_id ? { ...m, role_id } : m))} /> },
          ]} />
      </Space>
    </Modal>
    <TenantRoles tenant={roleTenant} onClose={() => setRoleTenant(null)} />
  </Space>;
}

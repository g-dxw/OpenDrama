import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Checkbox, Drawer, Empty, Input, Modal, Popconfirm, Space, Table, Tag, Typography, message } from "antd";
import { PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import { api, errorText } from "../api";
import { useTenant, type Tenant } from "../tenant";
import { changePermission, permissionSections, type TenantRole, type PermissionEntry, type TenantRolesPayload } from "../tenant-permissions";

export default function TenantRoles({ tenant, onClose, inline = false }: { tenant: Tenant | null; onClose: () => void; inline?: boolean }) {
  const { refresh } = useTenant();
  const [roles, setRoles] = useState<TenantRole[]>([]);
  const [catalog, setCatalog] = useState<PermissionEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  const [editing, setEditing] = useState<TenantRole | "new" | null>(null);
  const [name, setName] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const load = useCallback(async () => {
    if (!tenant) return;
    const current = ++generation.current; setLoading(true); setError("");
    try {
      const value = await api<TenantRolesPayload>("/tenant/v1/roles");
      if (!Array.isArray(value.roles) || !Array.isArray(value.catalog)) throw new Error("角色目录返回不完整，请重试");
      if (current === generation.current) { setRoles(value.roles); setCatalog(value.catalog); }
    } catch (e) { if (current === generation.current) setError(errorText(e)); }
    finally { if (current === generation.current) setLoading(false); }
  }, [tenant?.id]);
  useEffect(() => { setEditing(null); setRoles([]); setCatalog([]); if (tenant) void load(); return () => { generation.current++; }; }, [tenant?.id, load]);
  const edit = (role: TenantRole | "new") => {
    if (role !== "new" && role.kind === "tenant_admin") return;
    setEditing(role); setName(role === "new" ? "" : role.name); setPicked(role === "new" ? [] : [...role.permissions]); setSaveError("");
  };
  const save = async () => {
    if (!tenant || !editing || !name.trim() || error || loading) return;
    setSaving(true); setSaveError("");
    try {
      const base = "/tenant/v1/roles";
      await api(base + (editing === "new" ? "" : "/" + encodeURIComponent(editing.id)), {
        method: editing === "new" ? "POST" : "PATCH",
        body: JSON.stringify({ name: name.trim(), permissions: [...new Set(picked)].sort(), ...(editing === "new" ? {} : { version: editing.version }) }),
      });
      setEditing(null); message.success("角色与权限已保存"); await load(); await refresh();
    } catch (e) { setSaveError(errorText(e)); }
    finally { setSaving(false); }
  };
  const remove = async (role: TenantRole) => {
    if (!tenant || role.kind === "tenant_admin") return;
    try {
      await api("/tenant/v1/roles/" + encodeURIComponent(role.id) + "?version=" + role.version, { method: "DELETE" });
      message.success("角色已删除"); await load(); await refresh();
    } catch (e) { message.error(errorText(e)); }
  };
  const sections = permissionSections(catalog, tenant?.kind === "india_legacy");
  const invalid = picked.some(key => !catalog.some(entry => entry.key === key));
  const body = <>
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        <Typography.Text type="secondary">菜单分类与后台导航保持一致；“查看”权限决定可见子菜单，操作权限分别控制编辑、上传、审核等动作。仅显示此租户支持的功能。</Typography.Text>
        {tenant?.kind === "india_legacy" && <Alert type="info" showIcon message="India 内容运营权限" description="按查看、编辑、上传、定价、审核和上下架分别授权。统一运营授权启用后，员工使用本租户账号即可操作；旧站账号连接仅用于兼容模式。" />}
        {error && <Alert type="error" showIcon message={error} action={<Button onClick={() => void load()}>重试</Button>} />}
        <Space><Button type="primary" icon={<PlusOutlined aria-hidden />} disabled={loading || !!error} onClick={() => edit("new")}>新增角色</Button>
          <Button icon={<ReloadOutlined aria-hidden />} loading={loading} onClick={() => void load()}>刷新角色</Button></Space>
        <Table<TenantRole> rowKey="id" dataSource={roles} loading={loading} pagination={false} scroll={{ x: 560 }} locale={{ emptyText: <Empty description="暂无角色" /> }}
          columns={[
            { title: "角色", dataIndex: "name", render: (value, row) => <Space><Typography.Text strong>{value}</Typography.Text>{row.kind === "tenant_admin" ? <Tag color="blue">租户管理员 · 受保护</Tag> : row.builtin_key && <Tag>预设</Tag>}</Space> },
            { title: "已选权限", render: (_, row) => row.kind === "tenant_admin" ? "全部已开通业务与系统管理" : row.permissions.length + " 项" },
            { title: "操作", render: (_, row) => <Space><Button type="link" disabled={!!error || row.kind === "tenant_admin"} onClick={() => edit(row)}>配置权限</Button>
              <Popconfirm title="删除此角色？" description="已分配给成员的角色不能删除。" okText="删除" cancelText="取消" onConfirm={() => remove(row)} disabled={row.kind === "tenant_admin" || !!row.builtin_key || !!error}>
                <Button type="link" danger disabled={row.kind === "tenant_admin" || !!row.builtin_key || !!error}>删除</Button>
              </Popconfirm></Space> },
          ]} />
      </Space>
    </>;
  return <>
    {inline ? body : <Drawer title={tenant ? tenant.name + " · 角色与菜单权限" : "角色与菜单权限"} open={!!tenant} onClose={onClose} width="min(960px, 100vw)" destroyOnHidden>{body}</Drawer>}
    <Modal title={editing === "new" ? "新增租户角色" : "配置角色与菜单权限"} open={editing !== null}
      onCancel={() => { if (!saving) setEditing(null); }} onOk={() => void save()} width={800} okText="保存权限" cancelText="取消"
      confirmLoading={saving} okButtonProps={{ disabled: !name.trim() || invalid || loading || !!error, "aria-label": "保存权限" }} destroyOnHidden>
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        {saveError && <Alert type="error" showIcon message={saveError} description="当前选择已保留。如角色已被其他人修改，请关闭编辑后刷新角色，再重新核对。" />}
        {invalid && <Alert type="warning" showIcon message="角色包含当前目录中不可用的权限，请重新加载角色目录后再配置。" />}
        <div><label htmlFor="tenant-role-name">角色名称</label><Input id="tenant-role-name" value={name} onChange={event => setName(event.target.value)} maxLength={64} disabled={saving} style={{ marginTop: 6 }} /></div>
        <Typography.Text type="secondary">选择操作会同时选中必要的查看权限；取消查看权限会移除依赖它的操作。</Typography.Text>
        <div style={{ maxHeight: "55vh", overflow: "auto", width: "100%" }}>
          <Space direction="vertical" style={{ width: "100%" }} size="small">
            {sections.map(section => <Card key={section.key} size="small" title={section.title}>
              <Space direction="vertical" style={{ width: "100%" }} size="middle">
                {section.modules.map(module => <div className="tenant-role-module" key={module.key}>
                  <Typography.Text strong>{module.title}</Typography.Text>
                  <Space direction="vertical" style={{ width: "100%", marginTop: 10 }}>
                    {module.entries.map(entry => <Checkbox className="tenant-permission-choice" key={entry.key} checked={picked.includes(entry.key)} disabled={saving || entry.enabled === false}
                      onChange={event => setPicked(current => changePermission(current, entry.key, event.target.checked, catalog))}>
                      {entry.label}{entry.enabled === false && <Tag style={{ marginInlineStart: 6 }}>模块未开通{picked.includes(entry.key) ? " · 已有授权保留" : ""}</Tag>}{entry.key.endsWith(".view") && !entry.label.includes("菜单") && <Typography.Text type="secondary"> · 菜单访问</Typography.Text>}
                    </Checkbox>)}
                  </Space>
                </div>)}
              </Space>
            </Card>)}
          </Space>
        </div>
      </Space>
    </Modal>
  </>;
}

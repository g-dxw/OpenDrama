import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Drawer, Empty, Form, Input, Modal, Popconfirm, Select, Space, Spin, Table, Tabs, Tag, Typography, message } from "antd";
import { LinkOutlined, PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import { api } from "../api";
import { usePermission } from "../me";
import { useTenant } from "../tenant";
import { INDIA_BASE, indiaApi, indiaErrorText, STATUS, type IndiaSeries, type IndiaSession } from "./india-api";
import IndiaSeriesForm from "./IndiaSeriesForm";
import IndiaEpisodes from "./IndiaEpisodes";
import IndiaAssets from "./IndiaAssets";

const id = encodeURIComponent;
export default function IndiaContent() {
  const { current } = useTenant();
  const [session, setSession] = useState<IndiaSession | null>(null);
  const [sessionLoading, setSessionLoading] = useState(true);
  const [sessionError, setSessionError] = useState("");
  const [connectOpen, setConnectOpen] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [connectError, setConnectError] = useState("");
  const [loginForm] = Form.useForm();
  const [rows, setRows] = useState<IndiaSeries[]>([]);
  const [loading, setLoading] = useState(false);
  const [listError, setListError] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [cursors, setCursors] = useState<string[]>([""]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<IndiaSeries | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [creating, setCreating] = useState(false);
  const [action, setAction] = useState<string | null>(null);
  const [actionReason, setActionReason] = useState("");
  const [acting, setActing] = useState(false);
  const mounted = useRef(true);
  const detailGeneration = useRef(0);
  const listGeneration = useRef(0);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; detailGeneration.current++; listGeneration.current++; }; }, []);
  const oldEditor = ["admin", "editor"].includes(session?.role || "");
  const oldReviewer = ["admin", "reviewer"].includes(session?.role || "");
  const writable = usePermission("dramas.edit") && oldEditor;
  const canUpload = usePermission("dramas.upload") && oldEditor;
  const canPricing = usePermission("dramas.pricing") && oldEditor;
  const reviewer = usePermission("reviews.decide") && oldReviewer;
  const canPublish = usePermission("dramas.publish") && oldReviewer;
  const oldAdmin = canUpload && session?.role === "admin";
  const connectionReady = session?.configured !== false && session?.auth_mode === "jjtv_client_password";
  const readSession = useCallback(async () => {
    setSessionLoading(true); setSessionError("");
    try { const value = await api<IndiaSession>(INDIA_BASE + "/session"); if (mounted.current) setSession(value); }
    catch (e) { if (mounted.current) setSessionError(indiaErrorText(e)); }
    finally { if (mounted.current) setSessionLoading(false); }
  }, []);
  useEffect(() => { void readSession(); }, [readSession]);
  const load = useCallback(async () => {
    if (!session?.connected) return;
    const generation = ++listGeneration.current;
    setLoading(true); setListError("");
    try {
      const params = new URLSearchParams({ limit: "30" });
      if (query) params.set("q", query);
      if (status) params.set("status", status);
      if (cursors[cursors.length - 1]) params.set("cursor", cursors[cursors.length - 1]!);
      const value = await indiaApi<{ items: IndiaSeries[]; next_cursor: string | null }>("/admin/series?" + params);
      if (mounted.current && generation === listGeneration.current) { setRows(value.items); setNextCursor(value.next_cursor); }
    } catch (e) {
      if (mounted.current && generation === listGeneration.current) {
        setRows([]); setListError(indiaErrorText(e));
        if ((e as any)?.detail?.code === "india_auth_required") { setSession(s => s ? { ...s, connected: false } : s); setSelected(null); }
      }
    } finally { if (mounted.current && generation === listGeneration.current) setLoading(false); }
  }, [session?.connected, session?.username, query, status, cursors]);
  useEffect(() => { void load(); }, [load]);
  const loadDetail = useCallback(async (seriesId: string) => {
    const generation = ++detailGeneration.current;
    setDetailLoading(true); setDetailError("");
    try { const value = await indiaApi<IndiaSeries>("/admin/series/" + id(seriesId)); if (mounted.current && generation === detailGeneration.current) setDetail(value); }
    catch (e) { if (mounted.current && generation === detailGeneration.current) setDetailError(indiaErrorText(e)); }
    finally { if (mounted.current && generation === detailGeneration.current) setDetailLoading(false); }
  }, []);
  const openDetail = (seriesId: string) => { setSelected(seriesId); setDetail(null); void loadDetail(seriesId); };
  const changed = () => { if (selected) void loadDetail(selected); void load(); };
  const closeConnect = () => {
    if (connecting) return;
    loginForm.resetFields(); setConnectError(""); setConnectOpen(false);
  };
  const connected = async () => {
    loginForm.resetFields(); setConnectOpen(false); setConnectError("");
    setRows([]); setCursors([""]); setSelected(null); setDetail(null); await readSession();
  };
  const login = async (values: { username: string; password: string }) => {
    if (!connectionReady) return;
    const credentials = { username: values.username, password: values.password };
    setConnecting(true); setConnectError(""); loginForm.setFieldValue("password", undefined);
    try {
      const result = await api<IndiaSession>(INDIA_BASE + "/session", { method: "POST", body: JSON.stringify(credentials) });
      if (!mounted.current) return;
      if (!result.connected) throw new Error("专用连接未返回有效授权，请联系平台管理员检查连接配置");
      await connected(); message.success("旧站运营账号已连接");
    } catch (e) { if (mounted.current) setConnectError(indiaErrorText(e)); }
    finally { if (mounted.current) setConnecting(false); }
  };
  const renewConnection = async () => {
    setConnecting(true);
    try { const result = await api<IndiaSession>(INDIA_BASE + "/session/refresh", { method: "POST" }); setSession(result); message.success("连接已续期"); }
    catch (e) { message.error(indiaErrorText(e)); }
    finally { setConnecting(false); }
  };
  const disconnect = async () => {
    try { await api(INDIA_BASE + "/session", { method: "DELETE" }); setSession(s => s ? { ...s, connected: false } : s); setRows([]); setSelected(null); setDetail(null); setCreating(false); message.success("当前 JJTV 会话已断开旧站连接"); }
    catch (e) { message.error(indiaErrorText(e)); }
  };
  const execute = async () => {
    if (!detail || !action || !actionReason.trim()) return;
    if (action === "submit" ? !writable : action.startsWith("review") ? !reviewer : !canPublish) return;
    setActing(true);
    try {
      const operation = action.startsWith("review") ? "review" : action;
      await indiaApi("/admin/series/" + id(detail.id) + "/" + operation, { method: "POST", body: JSON.stringify({
        version: detail.version, reason: actionReason.trim(), ...(operation === "review" ? { approved: action === "review-approve" } : {}),
      }) });
      message.success("旧站已接受操作"); setAction(null); setActionReason(""); changed();
    } catch (e) { message.error(indiaErrorText(e)); }
    finally { setActing(false); }
  };
  if (current?.kind !== "india_legacy") return <Alert type="warning" message="请先选择 India 租户" />;
  return <Space direction="vertical" size="middle" style={{ width: "100%" }}>
    <Card>
      <Space wrap style={{ justifyContent: "space-between", width: "100%" }}>
        <div><Typography.Title level={4} style={{ margin: "0 0 8px" }}>India 内容运营</Typography.Title>
          {sessionLoading ? <Space><Spin size="small" /><span>正在检查账号连接…</span></Space> : <Space wrap>
            <Tag color={session?.connected ? "green" : "default"}>{session?.connected ? "已连接旧站" : "未连接旧站"}</Tag>
            {session?.connected && <Typography.Text>{session.username} · {({ admin: "管理员", editor: "内容编辑", reviewer: "审核员" } as Record<string, string>)[session.role || ""] || session.role}</Typography.Text>}
            {session?.expires_at && session.connected && <Typography.Text type="secondary">到期 {new Date(session.expires_at).toLocaleString()}</Typography.Text>}
          </Space>}
        </div>
        <Space wrap>
          <Button icon={<ReloadOutlined aria-hidden />} onClick={() => void readSession()} loading={sessionLoading}>检查连接</Button>
          <Button icon={<LinkOutlined aria-hidden />} type={session?.connected ? "default" : "primary"} disabled={sessionLoading || !connectionReady} onClick={() => { loginForm.resetFields(); setConnectError(""); setConnectOpen(true); }}>{session?.connected ? "更换旧站账号" : "连接旧站运营账号"}</Button>
          {session?.connected && <Button loading={connecting} onClick={() => void renewConnection()}>续期连接</Button>}
          {session?.connected && <Popconfirm title="断开当前会话的旧站连接？" okText="断开" cancelText="取消" onConfirm={disconnect}><Button>断开</Button></Popconfirm>}
        </Space>
      </Space>
      {sessionError && <Alert type="error" showIcon message={sessionError} style={{ marginTop: 16 }} />}
      {!sessionLoading && !sessionError && !connectionReady && <Alert type="info" showIcon message="旧站专用连接暂未配置完成，请联系平台管理员" style={{ marginTop: 16 }} />}
      {connectionReady && !session?.connected && session?.state === "mfa_required" && <Alert type="info" showIcon message="旧连接需要更新，请重新输入旧站账号和密码连接" style={{ marginTop: 16 }} />}
    </Card>
    {session?.connected ? <>
      <Space wrap>
        <Button type="primary" icon={<PlusOutlined aria-hidden />} disabled={!writable} onClick={() => setCreating(true)}>新增剧集</Button>
        <Input.Search allowClear placeholder="搜索剧集" aria-label="搜索 India 剧集" onSearch={value => { setQuery(value.trim()); setCursors([""]); }} style={{ width: 240 }} />
        <Select aria-label="India 剧集状态" value={status} onChange={value => { setStatus(value); setCursors([""]); }} style={{ width: 150 }}
          options={[{ value: "", label: "全部状态" }, ...["draft", "rights_review", "ready", "scheduled", "published", "suspended", "expired"].map(value => ({ value, label: STATUS[value] }))]} />
        <Button icon={<ReloadOutlined aria-hidden />} onClick={() => void load()}>刷新列表</Button>
      </Space>
      {listError && <Alert type="error" showIcon message={listError} />}
      <Table<IndiaSeries> rowKey="id" loading={loading} dataSource={rows} pagination={false} scroll={{ x: 720 }}
        columns={[
          { title: "剧集", dataIndex: "title", render: (title, row) => <Button type="link" style={{ padding: 0, textAlign: "left", height: "auto", whiteSpace: "normal" }} onClick={() => openDetail(row.id)}>{title}</Button> },
          { title: "语言", dataIndex: "language", width: 85 },
          { title: "分集", dataIndex: "episode_count", width: 75 },
          { title: "状态", dataIndex: "status", render: value => <Tag color={value === "published" ? "green" : value === "rights_review" ? "orange" : "default"}>{STATUS[value] || value}</Tag> },
          { title: "更新时间", dataIndex: "updated_at", render: value => value ? new Date(value).toLocaleString() : "—" },
          { title: "操作", render: (_, row) => <Button type="link" onClick={() => openDetail(row.id)}>管理内容</Button> },
        ]} />
      <Space style={{ justifyContent: "flex-end", width: "100%" }}>
        <Button disabled={loading || cursors.length < 2} onClick={() => setCursors(values => values.slice(0, -1))}>上一页</Button>
        <Typography.Text type="secondary">第 {cursors.length} 页</Typography.Text>
        <Button disabled={loading || !nextCursor} onClick={() => nextCursor && setCursors(values => [...values, nextCursor])}>下一页</Button>
      </Space>
    </> : !sessionLoading && <Card><Empty description="连接旧站运营账号后，即可管理 India 的剧集和视频" /></Card>}
    <Modal title="新增 India 剧集" open={creating} onCancel={() => setCreating(false)} width={900} footer={null} destroyOnHidden>
      {creating && <IndiaSeriesForm writable={writable} canPricing={canPricing} onSaved={row => { setCreating(false); openDetail(row.id); void load(); }} />}
    </Modal>
    <Drawer title={detail ? detail.title : "剧集详情"} open={!!selected && !!session?.connected} width="min(1120px, 100vw)"
      onClose={() => { setSelected(null); setDetail(null); detailGeneration.current++; }} destroyOnHidden
      extra={<Button icon={<ReloadOutlined aria-hidden />} onClick={changed} disabled={!selected}>刷新详情</Button>}>
      {detailLoading && !detail ? <Spin /> : detailError ? <Alert type="error" showIcon message={detailError} action={<Button onClick={changed}>重试</Button>} /> : detail && <>
        <Space wrap style={{ marginBottom: 16 }}><Tag color={detail.status === "published" ? "green" : "default"}>{STATUS[detail.status] || detail.status}</Tag>
          <Typography.Text type="secondary">版本 {detail.version}</Typography.Text>
          {writable && <Button disabled={!["draft", "suspended", "expired"].includes(detail.status)} onClick={() => { setAction("submit"); setActionReason(""); }}>提交审核</Button>}
          {reviewer && <><Button disabled={detail.status !== "rights_review"} onClick={() => { setAction("review-approve"); setActionReason(""); }}>审核通过</Button>
            <Button disabled={detail.status !== "rights_review"} onClick={() => { setAction("review-reject"); setActionReason(""); }}>审核退回</Button></>}
           {canPublish && <><Button type="primary" disabled={!["ready", "suspended"].includes(detail.status)} onClick={() => { setAction("publish"); setActionReason(""); }}>上架</Button>
            <Button danger disabled={!["published", "scheduled"].includes(detail.status)} onClick={() => { setAction("unpublish"); setActionReason(""); }}>下架</Button></>}
        </Space>
        <Tabs items={[
          { key: "metadata", label: "剧集资料与版权", children: <IndiaSeriesForm key={detail.id} record={detail} writable={writable} canPricing={canPricing} onSaved={() => changed()} /> },
          { key: "episodes", label: "分集与视频", children: <IndiaEpisodes series={detail} writable={writable} canUpload={canUpload} oldAdmin={oldAdmin} onChanged={changed} /> },
          { key: "files", label: "封面与版权证明", children: <IndiaAssets series={detail} writable={canUpload && !["published", "scheduled"].includes(detail.status)} onChanged={changed} /> },
        ]} />
      </>}
    </Drawer>
    <Modal title={action ? ({ submit: "提交审核", "review-approve": "审核通过", "review-reject": "审核退回", publish: "上架剧集", unpublish: "下架剧集" } as Record<string, string>)[action] : "内容操作"}
      open={!!action} onCancel={() => { if (!acting) setAction(null); }} onOk={() => void execute()} confirmLoading={acting} okText="提交旧站校验" cancelText="取消" okButtonProps={{ disabled: !actionReason.trim() }}>
      <Space direction="vertical" style={{ width: "100%" }}>
        <Typography.Text>当前剧集：{detail?.title}。状态以旧站校验结果为准。</Typography.Text>
        <label htmlFor="india-action-reason">操作说明</label>
        <Input.TextArea id="india-action-reason" rows={3} maxLength={500} value={actionReason} onChange={event => setActionReason(event.target.value)} />
      </Space>
    </Modal>
    <Modal title="连接旧站运营账号" open={connectOpen} onCancel={closeConnect} footer={null} destroyOnHidden maskClosable={!connecting}>
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        <Typography.Text type="secondary">输入旧站账号和密码即可连接，无需验证码。此连接仅用于当前 JJTV 登录会话，密码不会保存。</Typography.Text>
        <Typography.Text type="secondary">直接登录旧站时，仍需使用验证器。</Typography.Text>
        {connectError && <Alert role="alert" showIcon type="error" message={connectError} />}
        <Form form={loginForm} layout="vertical" onFinish={login} preserve={false}>
          <Form.Item label="旧站运营账号" name="username" rules={[{ required: true, whitespace: true, message: "请输入账号" }]}><Input autoComplete="off" disabled={connecting} /></Form.Item>
          <Form.Item label="旧站密码" name="password" rules={[{ required: true, message: "请输入密码" }]}><Input.Password autoComplete="off" disabled={connecting} /></Form.Item>
          <Button block type="primary" htmlType="submit" loading={connecting} disabled={!connectionReady} aria-label="连接账号" aria-busy={connecting}>连接账号</Button>
        </Form>
      </Space>
    </Modal>
  </Space>;
}

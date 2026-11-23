import { useState } from "react";
import { Alert, Button, Card, Col, Form, Input, Modal, Row, Select, Space, Table, Tag, Typography, message } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import { api, errorText } from "../api";
import { ManagementFormSection } from "../components/ManagementPage";
import { useLoad } from "../hooks";

const path = "/platform/v1/creem/accounts";
const statuses = { draft: "草稿", active: "已启用", disabled: "已停用" };

export type CreemMode = "test" | "production";
type CreemAccount = {
  code: string;
  name: string;
  status: keyof typeof statuses;
  mode: CreemMode;
  api_key_configured: boolean;
  webhook_secret_configured: boolean;
  notify_url: string;
};
type AccountForm = Pick<CreemAccount, "code" | "name" | "status" | "mode"> & {
  api_key?: string;
  webhook_secret?: string;
};

export function CreemAccountsPanel() {
  const accounts = useLoad<{ items: CreemAccount[] }>(path, { items: [] });
  const [form] = Form.useForm<AccountForm>();
  const [editing, setEditing] = useState<CreemAccount | null | undefined>();
  const [saving, setSaving] = useState(false);
  const [verifying, setVerifying] = useState("");
  const [saveError, setSaveError] = useState("");
  const [verificationError, setVerificationError] = useState("");
  const active = Form.useWatch("status", form) === "active";

  function open(account: CreemAccount | null) {
    form.resetFields();
    form.setFieldsValue(account ? {
      name: account.name,
      status: account.status,
      mode: account.mode,
    } : { status: "draft", mode: "test" });
    setSaveError("");
    setEditing(account);
  }

  function close() {
    form.setFieldsValue({ api_key: "", webhook_secret: "" });
    setEditing(undefined);
  }

  async function save(values: AccountForm) {
    setSaving(true);
    setSaveError("");
    const { code, name, status, mode, api_key, webhook_secret } = values;
    try {
      await api(path + (editing ? "/" + editing.code : ""), {
        method: editing ? "PATCH" : "POST",
        body: JSON.stringify({
          name: name.trim(),
          status,
          ...(!editing && { code, mode }),
          ...(api_key && { api_key }),
          ...(webhook_secret && { webhook_secret }),
        }),
      });
      close();
      await accounts.reload();
      message.success("Creem 账号已保存");
    } catch (cause) {
      setSaveError(errorText(cause));
    } finally {
      setSaving(false);
    }
  }

  async function verify(account: CreemAccount) {
    setVerifying(account.code);
    setVerificationError("");
    try {
      await api(path + "/" + account.code + "/test", { method: "POST" });
      await accounts.reload();
      message.success(account.name + " 的 Creem API 连接已通过验证");
    } catch (cause) {
      setVerificationError(account.name + "：" + errorText(cause));
    } finally {
      setVerifying("");
    }
  }

  return <Card title="Creem 收款账号" size="small" extra={<Space wrap>
    <Button icon={<ReloadOutlined />} loading={accounts.loading} onClick={() => void accounts.reload()}>刷新</Button>
    <Button type="primary" onClick={() => open(null)}>新增 Creem 账号</Button>
  </Space>}>
    {(accounts.error || verificationError) && <Alert type="error" showIcon message={accounts.error || verificationError} style={{ marginBottom: 16 }} />}
    <Table<CreemAccount>
      rowKey="code"
      size="small"
      loading={accounts.loading}
      dataSource={accounts.data.items}
      scroll={{ x: true }}
      locale={{ emptyText: "暂无 Creem 收款账号" }}
      columns={[
        { title: "账号", dataIndex: "name", render: (name, account) => <>{name} <Typography.Text type="secondary">({account.code})</Typography.Text></> },
        { title: "环境", dataIndex: "mode", render: (mode: CreemMode) => <Tag color={mode === "test" ? "blue" : "red"}>{mode === "test" ? "测试" : "生产"}</Tag> },
        { title: "状态", dataIndex: "status", render: (status: CreemAccount["status"]) => <Tag color={status === "active" ? "green" : undefined}>{statuses[status]}</Tag> },
        { title: "凭据", key: "credentials", render: (_, account) => <Space wrap>
          <Tag color={account.api_key_configured ? "green" : undefined}>{account.api_key_configured ? "API 密钥已保存" : "待填 API 密钥"}</Tag>
          <Tag color={account.webhook_secret_configured ? "green" : undefined}>{account.webhook_secret_configured ? "Webhook 密钥已保存" : "待填 Webhook 密钥"}</Tag>
        </Space> },
        { title: "操作", key: "actions", render: (_, account) => <Space wrap>
          <Button size="small" aria-label={`编辑 ${account.name}`} onClick={() => open(account)}>编辑</Button>
          <Button size="small" aria-label={`验证 ${account.name}`} loading={verifying === account.code}
            disabled={!account.api_key_configured || (!!verifying && verifying !== account.code)}
            onClick={() => void verify(account)}>测试连接</Button>
        </Space> },
      ]}
    />
    <Modal
      title={editing ? "编辑 Creem 账号" : "新增 Creem 账号"}
      open={editing !== undefined}
      width={680}
      forceRender
      confirmLoading={saving}
      okText="保存账号"
      cancelText="取消"
      onOk={() => form.submit()}
      onCancel={() => { if (!saving) close(); }}
    >
      <Form name="creem-account" form={form} layout="vertical" onFinish={save} scrollToFirstError validateTrigger="onBlur">
        {saveError && <Alert type="error" showIcon message={saveError} style={{ marginBottom: 16 }} />}
        <ManagementFormSection title="账号信息">
          {editing
            ? <Typography.Paragraph type="secondary">账号编码 <Typography.Text code>{editing.code}</Typography.Text></Typography.Paragraph>
            : <Form.Item label="账号编码" name="code" rules={[{ required: true, pattern: /^[a-z0-9_]{2,32}$/, message: "使用 2–32 位小写字母、数字或下划线" }]}>
                <Input placeholder="creem_test" maxLength={32} />
              </Form.Item>}
          <Form.Item label="账号名称" name="name" rules={[{ required: true, whitespace: true, message: "输入账号名称" }]}>
            <Input maxLength={100} />
          </Form.Item>
          <Row gutter={16}>
            <Col xs={24} sm={12}>
              <Form.Item label="接入状态" name="status">
                <Select options={Object.entries(statuses).map(([value, label]) => ({ value, label }))} />
              </Form.Item>
            </Col>
            <Col xs={24} sm={12}>
              <Form.Item label="环境" name="mode">
                <Select disabled={Boolean(editing)} options={[{ value: "test", label: "测试" }, { value: "production", label: "生产" }]} />
              </Form.Item>
            </Col>
          </Row>
        </ManagementFormSection>
        <ManagementFormSection title="Creem 凭据" description="使用所选环境的 API 密钥和 Webhook 签名密钥。测试与生产请分别新建账号。">
          <Form.Item label="API 密钥" name="api_key" extra={editing?.api_key_configured ? "留空保留已保存的密钥。" : undefined}
            rules={[{ required: active && !editing?.api_key_configured, whitespace: true, message: "输入 Creem API 密钥" }]}>
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Form.Item label="Webhook 密钥" name="webhook_secret" extra={editing?.webhook_secret_configured ? "留空保留已保存的密钥。" : undefined}
            rules={[{ required: active && !editing?.webhook_secret_configured, whitespace: true, message: "输入 Creem Webhook 签名密钥" }]}>
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          {editing?.notify_url && <Alert type="info" showIcon message="支付通知地址"
            description={<Typography.Text copyable>{editing.notify_url}</Typography.Text>} />}
        </ManagementFormSection>
      </Form>
    </Modal>
  </Card>;
}

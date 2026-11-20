import { useState } from "react";
import { Alert, Button, Card, Checkbox, Col, Form, Input, Modal, Row, Select, Space, Table, Tag, Typography, message } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import { api, errorText } from "../api";
import { ManagementFormSection } from "../components/ManagementPage";
import { useLoad } from "../hooks";

const path = "/platform/v1/waffo/accounts";
const statuses = { draft: "草稿", active: "已启用", disabled: "已停用" };

type WaffoAccount = {
  code: string;
  name: string;
  status: keyof typeof statuses;
  mode: "test" | "production";
  merchant_id: string;
  currency: string;
  subscriptions_enabled: boolean;
  api_key_configured: boolean;
  private_key_configured: boolean;
  public_key_configured: boolean;
  notify_url: string;
};

type AccountForm = Pick<WaffoAccount, "code" | "name" | "status" | "mode" | "merchant_id" | "currency" | "subscriptions_enabled"> & {
  api_key?: string;
  private_key?: string;
  public_key?: string;
};

export function WaffoAccountsPanel() {
  const accounts = useLoad<{ items: WaffoAccount[] }>(path, { items: [] });
  const [form] = Form.useForm<AccountForm>();
  const [editing, setEditing] = useState<WaffoAccount | null | undefined>();
  const [saving, setSaving] = useState(false);
  const [verifying, setVerifying] = useState("");
  const [saveError, setSaveError] = useState("");
  const [verifyError, setVerifyError] = useState("");
  const active = Form.useWatch("status", form) === "active";

  function open(account: WaffoAccount | null) {
    form.resetFields();
    form.setFieldsValue(account ? {
      name: account.name, status: account.status, mode: account.mode, merchant_id: account.merchant_id,
      currency: account.currency, subscriptions_enabled: account.subscriptions_enabled,
    } : { status: "draft", mode: "test", currency: "USD", subscriptions_enabled: false });
    setSaveError("");
    setEditing(account);
  }

  function close() {
    form.setFieldsValue({ api_key: "", private_key: "", public_key: "" });
    setEditing(undefined);
  }

  async function save(values: AccountForm) {
    setSaving(true);
    setSaveError("");
    const { code, name, mode, status, merchant_id, currency, subscriptions_enabled, api_key, private_key, public_key } = values;
    try {
      await api(path + (editing ? "/" + editing.code : ""), {
        method: editing ? "PATCH" : "POST",
        body: JSON.stringify({
          name: name.trim(), status, merchant_id: merchant_id.trim(), currency, subscriptions_enabled,
          ...(!editing && { code, mode }),
          ...(api_key?.trim() && { api_key: api_key.trim() }),
          ...(private_key?.trim() && { private_key: private_key.trim() }),
          ...(public_key?.trim() && { public_key: public_key.trim() }),
        }),
      });
      close();
      await accounts.reload();
      message.success("Waffo 账号已保存");
    } catch (cause) {
      setSaveError(errorText(cause));
    } finally {
      setSaving(false);
    }
  }

  async function verify(account: WaffoAccount) {
    setVerifying(account.code);
    setVerifyError("");
    try {
      await api(path + "/" + account.code + "/test", { method: "POST" });
      await accounts.reload();
      message.success(account.name + " 的 Waffo 连接已通过验证");
    } catch (cause) {
      setVerifyError(account.name + "：" + errorText(cause));
    } finally {
      setVerifying("");
    }
  }

  return <Card title="Waffo 收款账号" size="small" extra={<Space wrap>
    <Button icon={<ReloadOutlined />} loading={accounts.loading} onClick={() => void accounts.reload()}>刷新</Button>
    <Button type="primary" onClick={() => open(null)}>新增 Waffo 账号</Button>
  </Space>}>
    {(accounts.error || verifyError) && <Alert type="error" showIcon message={accounts.error || verifyError} style={{ marginBottom: 16 }} />}
    <Table<WaffoAccount>
      rowKey="code"
      size="small"
      loading={accounts.loading}
      dataSource={accounts.data.items}
      scroll={{ x: true }}
      locale={{ emptyText: "暂无 Waffo 收款账号" }}
      columns={[
        { title: "账号", dataIndex: "name", render: (name, account) => <>{name} <Typography.Text type="secondary">({account.code})</Typography.Text></> },
        { title: "环境", dataIndex: "mode", render: (mode: WaffoAccount["mode"]) => <Tag color={mode === "test" ? "blue" : "red"}>{mode === "test" ? "沙盒" : "生产"}</Tag> },
        { title: "币种", dataIndex: "currency" },
        { title: "计费", dataIndex: "subscriptions_enabled", render: (value: boolean) => value ? "一次性付款与自动续费" : "一次性付款" },
        { title: "状态", dataIndex: "status", render: (status: WaffoAccount["status"]) => <Tag color={status === "active" ? "green" : undefined}>{statuses[status]}</Tag> },
        { title: "操作", key: "actions", render: (_, account) => <Space wrap>
          <Button size="small" aria-label={`编辑 ${account.name}`} onClick={() => open(account)}>编辑</Button>
          <Button size="small" aria-label={`验证 ${account.name}`} loading={verifying === account.code}
            disabled={!account.api_key_configured || !account.private_key_configured || !account.public_key_configured || (!!verifying && verifying !== account.code)}
            onClick={() => void verify(account)}>测试连接</Button>
        </Space> },
      ]}
    />
    <Modal
      title={editing ? "编辑 Waffo 账号" : "新增 Waffo 账号"}
      open={editing !== undefined}
      width={700}
      forceRender
      confirmLoading={saving}
      okText="保存账号"
      cancelText="取消"
      onOk={() => form.submit()}
      onCancel={() => { if (!saving) close(); }}
    >
      <Form name="waffo-account" form={form} layout="vertical" onFinish={save} scrollToFirstError validateTrigger="onBlur">
        {saveError && <Alert type="error" showIcon message={saveError} style={{ marginBottom: 16 }} />}
        <ManagementFormSection title="账号信息">
          {editing
            ? <Typography.Paragraph type="secondary">账号编码 <Typography.Text code>{editing.code}</Typography.Text></Typography.Paragraph>
            : <Form.Item label="账号编码" name="code" rules={[{ required: true, pattern: /^[a-z0-9_]{2,32}$/, message: "使用 2–32 位小写字母、数字或下划线" }]}>
                <Input placeholder="waffo_sandbox" maxLength={32} />
              </Form.Item>}
          <Form.Item label="账号名称" name="name" rules={[{ required: true, whitespace: true, message: "输入账号名称" }]}>
            <Input maxLength={100} />
          </Form.Item>
          <Row gutter={16}>
            <Col xs={24} sm={8}>
              <Form.Item label="环境" name="mode">
                <Select disabled={Boolean(editing)} options={[{ value: "test", label: "沙盒" }, { value: "production", label: "生产" }]} />
              </Form.Item>
            </Col>
            <Col xs={24} sm={8}>
              <Form.Item label="接入状态" name="status">
                <Select options={Object.entries(statuses).map(([value, label]) => ({ value, label }))} />
              </Form.Item>
            </Col>
            <Col xs={24} sm={8}>
              <Form.Item label="收款币种" name="currency" normalize={(value: string) => value.trim().toUpperCase()}
                rules={[{ required: true, pattern: /^[A-Z]{3}$/, message: "输入三位币种代码，例如 USD" }]}>
                <Input maxLength={3} placeholder="USD" />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="subscriptions_enabled" valuePropName="checked" extra="Waffo 商户开通自动续费后启用。">
            <Checkbox>允许自动续费</Checkbox>
          </Form.Item>
        </ManagementFormSection>
        <ManagementFormSection title="商户凭据" description="沙盒和生产分别建立收款账号，并使用 Waffo 对应环境的凭据。">
          <Form.Item label="商户 ID" name="merchant_id" normalize={(value: string) => value.trim()}
            rules={[{ required: true, pattern: /^[a-zA-Z0-9_-]{1,100}$/, message: "使用 Waffo 提供的商户 ID" }]}>
            <Input maxLength={100} spellCheck={false} />
          </Form.Item>
          <Form.Item label="API 密钥" name="api_key" extra={editing?.api_key_configured ? "留空保留已保存的密钥。" : undefined}
            rules={[{ required: active && !editing?.api_key_configured, whitespace: true, message: "输入 Waffo API 密钥" }]}>
            <Input.Password autoComplete="new-password" maxLength={16384} />
          </Form.Item>
          <Form.Item label="商户 RSA 私钥" name="private_key"
            extra={editing?.private_key_configured ? "留空保留已保存的私钥。" : "粘贴用于签名的商户 RSA 私钥。"}
            rules={[{ required: active && !editing?.private_key_configured, whitespace: true, message: "输入商户 RSA 私钥" }]}>
            <Input.TextArea rows={4} autoComplete="off" spellCheck={false} maxLength={16384} />
          </Form.Item>
          <Form.Item label="Waffo 公钥" name="public_key"
            extra={editing?.public_key_configured ? "留空保留已保存的公钥。" : "粘贴 Waffo 提供的公钥，用于验证支付通知。"}
            rules={[{ required: active && !editing?.public_key_configured, whitespace: true, message: "输入 Waffo 公钥" }]}>
            <Input.TextArea rows={4} autoComplete="off" spellCheck={false} maxLength={16384} />
          </Form.Item>
          {editing?.notify_url && <Alert type="info" showIcon message={editing.mode === "test" ? "沙盒支付通知地址" : "生产支付通知地址"}
            description={<Typography.Text copyable>{editing.notify_url}</Typography.Text>} />}
        </ManagementFormSection>
      </Form>
    </Modal>
  </Card>;
}

import { useState } from "react";
import { Alert, Button, Card, Col, Form, Input, Modal, Row, Select, Space, Table, Tag, Typography, message } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import { API_BASE, api, errorText } from "../api";
import { ManagementFormSection } from "../components/ManagementPage";
import { useLoad } from "../hooks";

const accountPath = "/platform/v1/epay/accounts";
const methodPattern = /^[a-z0-9_]+$/;
const statusLabels = { draft: "草稿", active: "已启用", disabled: "已停用" };

type EpayAccount = {
  code: string;
  name: string;
  status: keyof typeof statusLabels;
  endpoint: string;
  merchant_id: string;
  payment_methods: string[];
  secret_configured: boolean;
  notify_url: string;
  currency: "CNY";
};

type AccountForm = Pick<EpayAccount, "code" | "name" | "status" | "endpoint" | "merchant_id" | "payment_methods"> & {
  secret_key?: string;
};

export function EpayAccountsPanel() {
  const accounts = useLoad<{ items: EpayAccount[] }>(accountPath, { items: [] });
  const [form] = Form.useForm<AccountForm>();
  const [editing, setEditing] = useState<EpayAccount | null | undefined>();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [verifying, setVerifying] = useState("");
  const [verificationError, setVerificationError] = useState("");

  function open(account: EpayAccount | null) {
    form.resetFields();
    form.setFieldsValue(account ? { ...account, secret_key: "" } : { status: "draft", payment_methods: [] });
    setError("");
    setEditing(account);
  }

  async function save(values: AccountForm) {
    setSaving(true);
    setError("");
    const { code, secret_key, ...fields } = values;
    try {
      await api(accountPath + (editing ? "/" + editing.code : ""), {
        method: editing ? "PATCH" : "POST",
        body: JSON.stringify({
          ...fields,
          name: fields.name.trim(),
          endpoint: fields.endpoint.trim(),
          merchant_id: fields.merchant_id.trim(),
          ...(!editing && { code }),
          ...(secret_key && { secret_key }),
        }),
      });
      close();
      await accounts.reload();
      message.success("Epay 账号已保存");
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setSaving(false);
    }
  }

  async function verify(account: EpayAccount) {
    setVerifying(account.code);
    setVerificationError("");
    try {
      await api<EpayAccount>(accountPath + "/" + account.code + "/test", { method: "POST" });
      await accounts.reload();
      message.success(account.name + " 的商户凭据已通过验证");
    } catch (cause) {
      setVerificationError(account.name + "：" + errorText(cause));
    } finally {
      setVerifying("");
    }
  }

  function close() {
    form.setFieldValue("secret_key", "");
    setEditing(undefined);
  }

  return (
    <Card
      title="Epay 收款账号"
      size="small"
      extra={<Space wrap>
        <Button icon={<ReloadOutlined />} loading={accounts.loading} onClick={() => void accounts.reload()}>刷新</Button>
        <Button type="primary" onClick={() => open(null)}>新增 Epay 账号</Button>
      </Space>}
    >
      {accounts.error && <Alert type="error" showIcon message={accounts.error} style={{ marginBottom: 16 }} />}
      {verificationError && <Alert type="error" showIcon message={verificationError} style={{ marginBottom: 16 }} />}
      <Table<EpayAccount>
        rowKey="code"
        size="small"
        loading={accounts.loading}
        dataSource={accounts.data.items}
        scroll={{ x: true }}
        locale={{ emptyText: "暂无 Epay 收款账号" }}
        columns={[
          { title: "账号", dataIndex: "name", render: (name, account) => <>{name} <Typography.Text type="secondary">({account.code})</Typography.Text></> },
          { title: "商户 ID", dataIndex: "merchant_id" },
          { title: "币种", dataIndex: "currency" },
          { title: "付款方式", dataIndex: "payment_methods", render: (methods: string[]) => methods.map(method => <Tag key={method}>{method}</Tag>) },
          { title: "状态", dataIndex: "status", render: (status: EpayAccount["status"]) => <Tag color={status === "active" ? "green" : undefined}>{statusLabels[status]}</Tag> },
          { title: "操作", key: "actions", render: (_, account) => <Space wrap>
            <Button size="small" aria-label={`编辑 ${account.name}`} onClick={() => open(account)}>编辑</Button>
            <Button
              size="small"
              aria-label={`验证 ${account.name}`}
              disabled={!account.secret_configured || (!!verifying && verifying !== account.code)}
              loading={verifying === account.code}
              onClick={() => void verify(account)}
            >
              验证凭据
            </Button>
          </Space> },
        ]}
      />
      <Modal
        title={editing ? "编辑 Epay 账号" : "新增 Epay 账号"}
        open={editing !== undefined}
        width={700}
        forceRender
        confirmLoading={saving}
        okText="保存账号"
        cancelText="取消"
        onOk={() => form.submit()}
        onCancel={() => { if (!saving) close(); }}
      >
        <Form name="epay-account" form={form} layout="vertical" onFinish={save} scrollToFirstError validateTrigger="onBlur">
          {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />}
          <ManagementFormSection title="账号信息">
            {editing ? (
              <Typography.Paragraph type="secondary">账号编码 <Typography.Text code>{editing.code}</Typography.Text></Typography.Paragraph>
            ) : (
              <Form.Item label="账号编码" name="code" rules={[{ required: true, pattern: /^[a-z0-9_]{2,32}$/, message: "使用 2–32 位小写字母、数字或下划线" }]}>
                <Input placeholder="epay_cn" maxLength={32} />
              </Form.Item>
            )}
            <Row gutter={16}>
              <Col xs={24} sm={14}>
                <Form.Item label="账号名称" name="name" rules={[{ required: true, whitespace: true, message: "输入账号名称" }]}>
                  <Input maxLength={100} />
                </Form.Item>
              </Col>
              <Col xs={24} sm={10}>
                <Form.Item label="接入状态" name="status">
                  <Select options={Object.entries(statusLabels).map(([value, label]) => ({ value, label }))} />
                </Form.Item>
              </Col>
            </Row>
          </ManagementFormSection>
          <ManagementFormSection title="易支付 v1 凭据" description="使用服务商提供的 HTTPS 端点和商户凭据。结算币种为 CNY。">
            <Form.Item
              label="Epay 端点"
              name="endpoint"
              extra="如服务商提供独立沙盒，请用沙盒端点和商户凭据新建测试账号。"
              rules={[
                { required: true, type: "url", message: "输入服务商的 HTTPS 基础地址" },
                { pattern: /^https:\/\//i, message: "端点必须使用 HTTPS" },
              ]}
            >
              <Input placeholder="https://pay.example.com" />
            </Form.Item>
            <Form.Item label="商户 ID" name="merchant_id" rules={[{ required: true, whitespace: true, message: "输入易支付商户 ID" }]}>
              <Input />
            </Form.Item>
            <Form.Item
              label="签名密钥"
              name="secret_key"
              extra={editing?.secret_configured ? "留空保留已保存的密钥。" : undefined}
              rules={[{ required: !editing?.secret_configured, whitespace: true, message: "输入易支付签名密钥" }]}
            >
              <Input.Password autoComplete="new-password" />
            </Form.Item>
            <Form.Item
              label="已开通的付款方式"
              name="payment_methods"
              extra="输入商户已开通的方式编码，按 Enter 添加。"
              validateTrigger="onChange"
              rules={[
                { required: true, type: "array", min: 1, message: "至少添加一种付款方式" },
                { validator: (_, methods: string[] | undefined) => methods?.some(method => !methodPattern.test(method)) ? Promise.reject(new Error("方式编码仅支持小写字母、数字和下划线")) : Promise.resolve() },
              ]}
            >
              <Select mode="tags" placeholder="例如 alipay 或 wxpay" options={[{ value: "alipay" }, { value: "wxpay" }]} />
            </Form.Item>
            {editing?.notify_url && <Alert
              type="info"
              showIcon
              message="支付通知地址"
              description={<Typography.Text copyable>{new URL(editing.notify_url, API_BASE || window.location.origin).href}</Typography.Text>}
            />}
          </ManagementFormSection>
        </Form>
      </Modal>
    </Card>
  );
}

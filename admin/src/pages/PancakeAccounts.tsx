import { useState } from "react";
import { Alert, Button, Card, Col, Form, Input, Modal, Row, Select, Space, Table, Tag, Typography, message } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import { api, errorText } from "../api";
import { ManagementFormSection } from "../components/ManagementPage";
import { useLoad } from "../hooks";

const path = "/platform/v1/waffo-pancake/accounts";
const statuses = { draft: "草稿", active: "已启用", disabled: "已停用" };

type PancakeAccount = {
  code: string;
  name: string;
  status: keyof typeof statuses;
  mode: "test" | "production";
  merchant_id: string;
  store_id: string;
  private_key_configured: boolean;
  notify_url: string;
};
type AccountForm = Pick<PancakeAccount, "code" | "name" | "status" | "mode" | "merchant_id" | "store_id"> & {
  private_key?: string;
};
type Store = { id: string; name: string; status: string; prod_enabled: boolean };
type Product = {
  id: string;
  name: string;
  status: string;
  billing_mode: "one_time" | "subscription";
  currency: string | null;
  price_minor: number | null;
};

function PancakeCatalog({ account, chooseStore }: { account: PancakeAccount; chooseStore: (store: Store) => void }) {
  const catalog = useLoad<{ stores: Store[]; products: Product[] }>(path + "/" + account.code + "/catalog", { stores: [], products: [] });

  return <Space direction="vertical" size={16} style={{ width: "100%" }}>
    <Space wrap style={{ justifyContent: "space-between", width: "100%" }}>
      <Typography.Text>{account.name} · {account.mode === "test" ? "测试" : "生产"}</Typography.Text>
      <Button icon={<ReloadOutlined />} loading={catalog.loading} onClick={() => void catalog.reload()}>刷新目录</Button>
    </Space>
    {catalog.error && <Alert showIcon type="error" message={catalog.error} />}
    <Table<Store>
      caption={<Typography.Text strong>商户店铺</Typography.Text>}
      rowKey="id"
      size="small"
      scroll={{ x: true }}
      loading={catalog.loading}
      dataSource={catalog.data.stores}
      locale={{ emptyText: "暂无店铺" }}
      columns={[
        { title: "店铺", dataIndex: "name" },
        { title: "店铺 ID", dataIndex: "id", render: (id, store) => <>
          <Typography.Text copyable>{id}</Typography.Text> {store.id === account.store_id && <Tag>已绑定</Tag>}
        </> },
        { title: "状态", dataIndex: "status", render: (status: string) => status === "active" ? "有效" : status },
        { title: "生产收款", dataIndex: "prod_enabled", render: (enabled: boolean) => <Tag color={enabled ? "green" : undefined}>{enabled ? "已获批" : "待审核"}</Tag> },
        { title: "操作", key: "actions", render: (_, store) => <Button size="small" disabled={store.id === account.store_id} onClick={() => chooseStore(store)}>选择店铺</Button> },
      ]}
    />
    <Table<Product>
      caption={<Typography.Text strong>已绑定店铺的商品</Typography.Text>}
      rowKey={item => item.id + ":" + (item.currency ?? "")}
      size="small"
      scroll={{ x: true }}
      loading={catalog.loading}
      dataSource={catalog.data.products}
      locale={{ emptyText: "暂无商品" }}
      columns={[
        { title: "商品", dataIndex: "name" },
        { title: "商品 ID", dataIndex: "id", render: (value: string) => <Typography.Text copyable>{value}</Typography.Text> },
        { title: "计费", dataIndex: "billing_mode", render: (value: Product["billing_mode"]) => value === "subscription" ? "自动续费" : "一次性付款" },
        { title: "价格", key: "price", render: (_, item) => {
          if (!item.currency || item.price_minor === null) return "—";
          const formatter = new Intl.NumberFormat("zh-CN", { style: "currency", currency: item.currency });
          return formatter.format(item.price_minor / (10 ** (formatter.resolvedOptions().maximumFractionDigits ?? 2))) + " " + item.currency;
        } },
        { title: "状态", dataIndex: "status", render: (status: string) => status === "active" ? "有效" : status },
      ]}
    />
  </Space>;
}

export function PancakeAccountsPanel() {
  const accounts = useLoad<{ items: PancakeAccount[] }>(path, { items: [] });
  const [form] = Form.useForm<AccountForm>();
  const [editing, setEditing] = useState<PancakeAccount | null | undefined>();
  const [catalog, setCatalog] = useState<PancakeAccount | null>(null);
  const [saving, setSaving] = useState(false);
  const [verifying, setVerifying] = useState("");
  const [saveError, setSaveError] = useState("");
  const [verificationError, setVerificationError] = useState("");
  const active = Form.useWatch("status", form) === "active";

  function open(account: PancakeAccount | null, store?: Store) {
    form.resetFields();
    form.setFieldsValue(account ? {
      name: account.name,
      status: account.status,
      mode: account.mode,
      merchant_id: account.merchant_id,
      store_id: store?.id ?? account.store_id,
    } : { status: "draft", mode: "test" });
    setSaveError("");
    setEditing(account);
  }

  function close() {
    form.setFieldValue("private_key", "");
    setEditing(undefined);
  }

  async function save(values: AccountForm) {
    setSaving(true);
    setSaveError("");
    const { code, name, status, mode, merchant_id, store_id, private_key } = values;
    try {
      await api(path + (editing ? "/" + editing.code : ""), {
        method: editing ? "PATCH" : "POST",
        body: JSON.stringify({
          name: name.trim(),
          status,
          merchant_id: merchant_id.trim(),
          store_id: store_id.trim(),
          ...(!editing && { code, mode }),
          ...(private_key?.trim() && { private_key: private_key.trim() }),
        }),
      });
      close();
      await accounts.reload();
      message.success("Waffo Pancake 账号已保存");
    } catch (cause) {
      setSaveError(errorText(cause));
    } finally {
      setSaving(false);
    }
  }

  async function verify(account: PancakeAccount) {
    setVerifying(account.code);
    setVerificationError("");
    try {
      await api(path + "/" + account.code + "/test", { method: "POST" });
      await accounts.reload();
      message.success(account.name + " 的 Waffo Pancake API 连接已通过验证");
    } catch (cause) {
      setVerificationError(account.name + "：" + errorText(cause));
    } finally {
      setVerifying("");
    }
  }

  return <Card title="Waffo Pancake 收款账号" size="small" extra={<Space wrap>
    <Button icon={<ReloadOutlined />} loading={accounts.loading} onClick={() => void accounts.reload()}>刷新</Button>
    <Button type="primary" onClick={() => open(null)}>新增 Waffo Pancake 账号</Button>
  </Space>}>
    {(accounts.error || verificationError) && <Alert type="error" showIcon message={accounts.error || verificationError} style={{ marginBottom: 16 }} />}
    <Table<PancakeAccount>
      rowKey="code"
      size="small"
      loading={accounts.loading}
      dataSource={accounts.data.items}
      scroll={{ x: true }}
      locale={{ emptyText: "暂无 Waffo Pancake 收款账号" }}
      columns={[
        { title: "账号", dataIndex: "name", render: (name, account) => <>{name} <Typography.Text type="secondary">({account.code})</Typography.Text></> },
        { title: "环境", dataIndex: "mode", render: (mode: PancakeAccount["mode"]) => <Tag color={mode === "test" ? "blue" : "red"}>{mode === "test" ? "测试" : "生产"}</Tag> },
        { title: "店铺 ID", dataIndex: "store_id", render: (id: string) => <Typography.Text copyable>{id}</Typography.Text> },
        { title: "状态", dataIndex: "status", render: (status: PancakeAccount["status"]) => <Tag color={status === "active" ? "green" : undefined}>{statuses[status]}</Tag> },
        { title: "操作", key: "actions", render: (_, account) => <Space wrap>
          <Button size="small" aria-label={`编辑 ${account.name}`} onClick={() => open(account)}>编辑</Button>
          <Button size="small" aria-label={`验证 ${account.name}`} loading={verifying === account.code}
            disabled={!account.private_key_configured || (!!verifying && verifying !== account.code)}
            onClick={() => void verify(account)}>测试连接</Button>
          <Button size="small" disabled={!account.private_key_configured} onClick={() => setCatalog(account)}>查看目录</Button>
        </Space> },
      ]}
    />
    <Modal title="Waffo Pancake 店铺和商品" open={Boolean(catalog)} onCancel={() => setCatalog(null)} footer={null} width={860}>
      {catalog && <PancakeCatalog key={catalog.code} account={catalog} chooseStore={store => { setCatalog(null); open(catalog, store); }} />}
    </Modal>
    <Modal
      title={editing ? "编辑 Waffo Pancake 账号" : "新增 Waffo Pancake 账号"}
      open={editing !== undefined}
      width={680}
      forceRender
      confirmLoading={saving}
      okText="保存账号"
      cancelText="取消"
      onOk={() => form.submit()}
      onCancel={() => { if (!saving) close(); }}
    >
      <Form name="waffo-pancake-account" form={form} layout="vertical" onFinish={save} scrollToFirstError validateTrigger="onBlur">
        {saveError && <Alert type="error" showIcon message={saveError} style={{ marginBottom: 16 }} />}
        <ManagementFormSection title="账号信息">
          {editing
            ? <Typography.Paragraph type="secondary">账号编码 <Typography.Text code>{editing.code}</Typography.Text></Typography.Paragraph>
            : <Form.Item label="账号编码" name="code" rules={[{ required: true, pattern: /^[a-z0-9_]{2,32}$/, message: "使用 2–32 位小写字母、数字或下划线" }]}>
                <Input placeholder="pancake_test" maxLength={32} />
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
        <ManagementFormSection title="商户和店铺" description="从 Waffo Pancake 控制台获取商户 ID、店铺 ID 和所选环境的 API 私钥。">
          <Row gutter={16}>
            <Col xs={24} sm={12}>
              <Form.Item label="商户 ID" name="merchant_id" rules={[{ required: true, pattern: /^MER_[a-zA-Z0-9]+$/, message: "输入以 MER_ 开头的商户 ID" }]}>
                <Input placeholder="MER_..." maxLength={128} spellCheck={false} />
              </Form.Item>
            </Col>
            <Col xs={24} sm={12}>
              <Form.Item label="店铺 ID" name="store_id" rules={[{ required: true, pattern: /^STO_[a-zA-Z0-9]+$/, message: "输入以 STO_ 开头的店铺 ID" }]}>
                <Input placeholder="STO_..." maxLength={128} spellCheck={false} />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item label="API 私钥" name="private_key" extra={editing?.private_key_configured ? "留空保留已保存的私钥。" : "粘贴控制台下载的 RSA 私钥。"}
            rules={[{ required: active && !editing?.private_key_configured, whitespace: true, message: "输入 API 私钥" }]}>
            <Input.TextArea rows={4} autoComplete="off" spellCheck={false} />
          </Form.Item>
          {editing?.notify_url && <Alert type="info" showIcon message={editing.mode === "test" ? "测试 Webhook 地址" : "生产 Webhook 地址"}
            description={<Typography.Text copyable>{editing.notify_url}</Typography.Text>} />}
        </ManagementFormSection>
      </Form>
    </Modal>
  </Card>;
}

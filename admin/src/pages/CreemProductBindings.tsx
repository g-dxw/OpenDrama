import { useState } from "react";
import { Alert, Button, Form, Input, Modal, Popconfirm, Space, Table, Tag, Typography, message } from "antd";
import { api, errorText } from "../api";
import { useLoad } from "../hooks";

const path = "/tenant/v1/creem/product-bindings";
const productIdPattern = /^prod_[a-zA-Z0-9_-]+$/;
type Copy = (zh: string, en: string) => string;
type BillingMode = "one_time" | "subscription";
type ProductBinding = {
  product_type: "coin_pack" | "membership";
  product_code: string;
  product_name: string;
  version_id: number;
  version: number;
  billing_mode: BillingMode;
  price_cents: number;
  currency: string;
  recurring_interval?: string | null;
  external_product_id: string | null;
};
type ProductBindings = {
  account_code: string | null;
  currency: string;
  billing_mode: BillingMode;
  items: ProductBinding[];
};

function rowKey(item: ProductBinding) {
  return [item.product_type, item.version_id, item.billing_mode].join(":");
}

export function CreemProductBindings({ accountCode, l }: { accountCode: string; l: Copy }) {
  const state = useLoad<ProductBindings | null>(path, null);
  const [updated, setUpdated] = useState<ProductBindings | null>(null);
  const data = updated ?? state.data;
  const [form] = Form.useForm<{ external_product_id: string }>();
  const [editing, setEditing] = useState<ProductBinding | null>(null);
  const [saving, setSaving] = useState("");
  const [error, setError] = useState("");
  const rows = data?.account_code === accountCode ? data.items : [];
  const intervals: Record<string, string> = {
    week: l("每周", "Weekly"),
    month: l("每月", "Monthly"),
    year: l("每年", "Yearly"),
  };

  function open(item: ProductBinding) {
    setError("");
    form.setFieldsValue({ external_product_id: item.external_product_id ?? "" });
    setEditing(item);
  }

  async function save(item: ProductBinding, externalProductId: string | null) {
    setSaving(rowKey(item));
    setError("");
    try {
      const value = await api<ProductBindings>(path, {
        method: "PUT",
        body: JSON.stringify({
          account_code: accountCode,
          product_type: item.product_type,
          version_id: item.version_id,
          billing_mode: item.billing_mode,
          external_product_id: externalProductId,
        }),
      });
      setUpdated(value);
      setEditing(null);
      message.success(externalProductId
        ? l("Creem 商品映射已保存", "Creem product mapping saved")
        : l("Creem 商品映射已移除", "Creem product mapping removed"));
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setSaving("");
    }
  }

  function price(item: ProductBinding) {
    const formatter = new Intl.NumberFormat(undefined, { style: "currency", currency: item.currency });
    const digits = formatter.resolvedOptions().maximumFractionDigits ?? 2;
    return formatter.format(item.price_cents / (10 ** digits));
  }

  function billingLabel(item: ProductBinding) {
    return item.billing_mode === "one_time"
      ? l("一次性付款", "One-time")
      : l("自动续费", "Subscription") + (item.recurring_interval ? " · " + (intervals[item.recurring_interval] || item.recurring_interval) : "");
  }

  const errorTextToDisplay = state.error || (!editing && error);
  return <section>
    <Space wrap style={{ justifyContent: "space-between", width: "100%", marginBottom: 8 }}>
      <Typography.Title level={5} style={{ margin: 0 }}>{l("Creem 商品映射", "Creem product mappings")}</Typography.Title>
      <Button loading={state.loading} disabled={Boolean(saving)} onClick={() => { setUpdated(null); setError(""); void state.reload(); }}>
        {l("刷新商品", "Refresh products")}
      </Button>
    </Space>
    <Typography.Paragraph type="secondary">
      {l("按本站商品版本和计费方式填写 Creem 商品 ID。修改售价或权益生成新版本后，请为新版本配置映射。", "Map each site product version and billing mode to a Creem Product ID. Set a new mapping after a price or benefit change creates a new version.")}
    </Typography.Paragraph>
    {errorTextToDisplay && <Alert type="error" showIcon message={errorTextToDisplay} style={{ marginBottom: 16 }} />}
    {data && data.account_code !== accountCode && <Alert type="warning" showIcon
      message={l("收款账号已变更，请刷新本站收款账号页面。", "The merchant account has changed. Refresh this page.")} style={{ marginBottom: 16 }} />}
    <Table<ProductBinding>
      rowKey={rowKey}
      size="small"
      loading={state.loading}
      dataSource={rows}
      scroll={{ x: true }}
      pagination={false}
      locale={{ emptyText: l("暂无可映射商品", "No products to map") }}
      columns={[
        { title: l("本站商品", "Site product"), key: "product", render: (_, item) => <>
          {item.product_name} <Tag>v{item.version}</Tag>
          <span className="management-row-secondary">{item.product_code} · {item.product_type === "coin_pack" ? l("金币包", "Coin pack") : l("会员", "Membership")}</span>
        </> },
        { title: l("价格", "Price"), key: "price", render: (_, item) => price(item) },
        { title: l("计费方式", "Billing"), key: "billing", render: (_, item) => <>
          {billingLabel(item)}
          {item.product_type === "membership" && item.billing_mode === data?.billing_mode &&
            <span className="management-row-secondary">{l("本站当前模式", "Current site mode")}</span>}
        </> },
        { title: "Creem Product ID", dataIndex: "external_product_id", render: (value: string | null) => value
          ? <Typography.Text copyable>{value}</Typography.Text>
          : <Typography.Text type="secondary">{l("待映射", "Unmapped")}</Typography.Text> },
        { title: l("操作", "Actions"), key: "actions", render: (_, item) => <Space wrap>
          <Button size="small" disabled={Boolean(saving)} onClick={() => open(item)}>{l("编辑映射", "Edit mapping")}</Button>
          {item.external_product_id && <Popconfirm
            title={l("移除此商品映射？", "Remove this product mapping?")}
            okText={l("移除", "Remove")}
            cancelText={l("取消", "Cancel")}
            onConfirm={() => save(item, null)}
          ><Button size="small" loading={saving === rowKey(item) && !editing} disabled={Boolean(saving) && saving !== rowKey(item)}>{l("移除映射", "Remove mapping")}</Button></Popconfirm>}
        </Space> },
      ]}
    />
    <Modal
      title={l("编辑 Creem 商品映射", "Edit Creem product mapping")}
      open={Boolean(editing)}
      confirmLoading={Boolean(saving)}
      okText={l("保存映射", "Save mapping")}
      cancelText={l("取消", "Cancel")}
      onOk={() => form.submit()}
      onCancel={() => { if (!saving) { setEditing(null); setError(""); } }}
      forceRender
    >
      {editing && <Typography.Paragraph>{editing.product_name} · v{editing.version} · {billingLabel(editing)} · {price(editing)}</Typography.Paragraph>}
      {editing && error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />}
      <Form name="creem-product-mapping" form={form} layout="vertical" onFinish={values => { if (editing) void save(editing, values.external_product_id.trim()); }}>
        <Form.Item
          label="Creem Product ID"
          name="external_product_id"
          extra={l("选择匹配的 Creem 商品，保存时将核对售价、币种和计费周期。", "Use the matching Creem product. Price, currency, and billing cycle are checked on save.")}
          rules={[{ required: true, pattern: productIdPattern, message: l("输入以 prod_ 开头的 Creem 商品 ID", "Enter a Creem product ID starting with prod_") }]}
        ><Input placeholder="prod_..." maxLength={128} /></Form.Item>
      </Form>
    </Modal>
  </section>;
}

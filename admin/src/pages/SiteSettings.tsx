import { operatorDate } from "../site-format";
import { contrastText } from "../../../shared/site";
import { useIdentity } from "../identity";
import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Divider,
  Checkbox,
  Col,
  Form,
  Input,
  InputNumber,
  Modal,
  Row,
  Select,
  Space,
  Spin,
  Switch,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from "antd";
import { api, errorText } from "../api";
import { ManagementPageHeader } from "../components/ManagementPage";
import { useLoad } from "../hooks";
import type { CreemMode } from "./CreemAccounts";
import { CreemProductBindings } from "./CreemProductBindings";
import type { SiteBrand, SiteConfig } from "../../../shared/site";
function useCopy() {
  const [lang, setLang] = useState(() =>
    localStorage.getItem("operator_locale") === "en" ? "en" : "zh",
  );
  return {
    lang,
    l: (zh: string, en: string) => (lang === "zh" ? zh : en),
    select: (
      <Select
        aria-label="Operator language"
        value={lang}
        style={{ width: 110 }}
        options={[
          { value: "zh", label: "中文" },
          { value: "en", label: "English" },
        ]}
        onChange={(v) => {
          localStorage.setItem("operator_locale", v);
          setLang(v);
        }}
      />
    ),
  };
}
const featureLabels: Record<string, [string, string]> = {
  coins: ["金币与购买", "Coins and purchases"],
  membership: ["会员", "Membership"],
  rewards: ["任务奖励", "Rewards"],
  comments: ["评论", "Comments"],
  support: ["客服", "Support"],
  attribution: ["投放归因", "Attribution"],
};
export function SiteSettingsPage() {
  const identity = useIdentity();
  const enabled = new Set(identity?.enabled_modules || []);
  const supported: Record<string, boolean> = {
    coins: enabled.has("coins") || enabled.has("users"),
    membership: enabled.has("plans"),
    rewards: enabled.has("rewards"),
    comments: enabled.has("comments"),
    support: enabled.has("support"),
    attribution: enabled.has("attribution"),
  };
  const { l, select } = useCopy();
  const [form] = Form.useForm();
  const [data, setData] = useState<SiteConfig | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const load = useCallback(async () => {
    setError("");
    try {
      const value = await api<SiteConfig>("/tenant/v1/site");
      setData(value);
      form.setFieldsValue({
        ...value,
        features: { ...supported, ...value.features },
      });
    } catch (e) {
      setError(errorText(e));
    }
  }, [form]);
  useEffect(() => {
    void load();
  }, [load]);
  const save = async () => {
    const values = await form.validateFields().catch(() => null);
    if (!values || !data) return;
    setBusy(true);
    setError("");
    try {
      const result = await api<SiteConfig>("/tenant/v1/site", {
        method: "PATCH",
        body: JSON.stringify({
          ...Object.fromEntries(
            Object.entries(values).filter(([key]) => key !== "name"),
          ),
          version: data.version,
        }),
      });
      setData(result);
      form.setFieldsValue(result);
      message.success(l("站点设置已保存", "Site settings saved"));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <ManagementPageHeader
        title={l("站点设置", "Site settings")}
        description={l(
          "配置本站市场、语言、时区、币种与已开通功能。",
          "Configure the markets, languages, timezone, currency and enabled features for this site.",
        )}
        extra={select}
      />
      {error && (
        <Alert
          type="error"
          showIcon
          message={error}
          action={
            <Button onClick={() => void load()}>
              {l("重新加载", "Reload")}
            </Button>
          }
        />
      )}
      <Card loading={!data && !error}>
        <Form form={form} layout="vertical" onFinish={() => void save()}>
          <Row gutter={24}>
            <Col xs={24} md={12}>
              <Form.Item
                name="name"
                label={l("站点名称", "Site name")}
                rules={[{ required: true, whitespace: true }]}
              >
                <Input maxLength={100} disabled />
              </Form.Item>
              <Form.Item
                name="target_markets"
                label={l("运营国家 / 地区代码", "Market country codes")}
                extra={l(
                  "使用 ISO 两位代码，例如 IN、US。",
                  "Use two-letter ISO codes, such as IN and US.",
                )}
                rules={[
                  { required: true, type: "array" },
                  {
                    validator: (_, v: string[]) =>
                      !v?.some((x) => !/^[A-Z]{2}$/.test(x))
                        ? Promise.resolve()
                        : Promise.reject(
                            new Error(
                              l(
                                "请输入大写的两位国家代码",
                                "Enter uppercase two-letter country codes",
                              ),
                            ),
                          ),
                  },
                ]}
              >
                <Select
                  mode="tags"
                  tokenSeparators={[",", " "]}
                  options={[
                    "IN",
                    "US",
                    "GB",
                    "CA",
                    "AU",
                    "SG",
                    "MY",
                    "ID",
                    "PH",
                    "TH",
                    "VN",
                    "BR",
                    "MX",
                  ].map((value) => ({ value }))}
                />
              </Form.Item>
              <Form.Item
                name="locales"
                label={l("可用语言", "Available languages")}
                rules={[{ required: true, type: "array" }]}
              >
                <Select
                  mode="multiple"
                  options={[
                    { value: "en", label: "English" },
                    { value: "zh", label: "简体中文" },
                  ]}
                />
              </Form.Item>
              <Form.Item
                name="default_locale"
                label={l("默认语言", "Default language")}
                rules={[{ required: true }]}
              >
                <Select
                  options={[
                    { value: "en", label: "English" },
                    { value: "zh", label: "简体中文" },
                  ]}
                />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item
                name="currency"
                label={l("结算展示币种", "Display currency")}
                rules={[{ required: true }]}
              >
                <Select
                  options={[
                    "USD",
                    "INR",
                    "GBP",
                    "EUR",
                    "SGD",
                    "MYR",
                    "IDR",
                    "PHP",
                    "THB",
                    "AUD",
                    "CAD",
                    "BRL",
                    "MXN",
                  ].map((value) => ({ value }))}
                />
              </Form.Item>
              <Form.Item
                name="timezone"
                label={l("站点时区", "Site timezone")}
                rules={[{ required: true }]}
              >
                <Select
                  showSearch
                  options={[
                    "UTC",
                    "Asia/Kolkata",
                    "Asia/Shanghai",
                    "Asia/Singapore",
                    "Asia/Bangkok",
                    "Asia/Jakarta",
                    "Asia/Manila",
                    "Europe/London",
                    "Europe/Berlin",
                    "America/New_York",
                    "America/Los_Angeles",
                    "America/Sao_Paulo",
                    "Australia/Sydney",
                  ].map((value) => ({ value }))}
                />
              </Form.Item>
              <Form.Item
                name="web_url"
                label={l("桌面站地址", "Desktop site URL")}
                rules={[{ type: "url" }]}
              >
                <Input placeholder="https://watch.example.com" />
              </Form.Item>
              <Form.Item
                name="h5_url"
                label={l("移动站地址", "Mobile site URL")}
                rules={[{ type: "url" }]}
              >
                <Input placeholder="https://watch.example.com" />
              </Form.Item>
            </Col>
          </Row>
          <Typography.Title level={5}>
            {l("观众端功能", "Viewer features")}
          </Typography.Title>
          <Alert
            showIcon
            type="info"
            style={{ marginBottom: 16 }}
            message={l(
              "功能仍受后台已开通模块限制，关闭后本站不再展示入口。",
              "Features remain limited to enabled business modules. Disabled features are hidden on this site.",
            )}
          />
          <Row gutter={16}>
            {Object.entries(featureLabels).map(([key, label]) => (
              <Col xs={12} md={8} key={key}>
                <Form.Item
                  name={["features", key]}
                  label={l(...label)}
                  valuePropName="checked"
                >
                  <Switch disabled={!supported[key]} />
                </Form.Item>
              </Col>
            ))}
          </Row>
          <Button
            type="primary"
            htmlType="submit"
            loading={busy}
            disabled={!data}
          >
            {l("保存站点设置", "Save site settings")}
          </Button>
        </Form>
      </Card>
    </Space>
  );
}
type BrandState = {
  draft: SiteBrand;
  published: SiteBrand;
  version: number;
  revisions: { version: number; created_at: string }[];
};
export function BrandPage() {
  const { l, select } = useCopy();
  const [form] = Form.useForm();
  const [data, setData] = useState<BrandState | null>(null),
    [site, setSite] = useState<SiteConfig | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [preview, setPreview] = useState<{
      token: string;
      expires_at: string;
    } | null>(null);
  const values = Form.useWatch([], form) as Partial<SiteBrand> | undefined;
  const load = useCallback(async () => {
    setError("");
    try {
      const [value, config] = await Promise.all([
        api<BrandState>("/tenant/v1/brand"),
        api<SiteConfig>("/tenant/v1/site"),
      ]);
      setData(value);
      setSite(config);
      form.setFieldsValue(value.draft);
    } catch (e) {
      setError(errorText(e));
    }
  }, [form]);
  useEffect(() => {
    void load();
  }, [load]);
  const save = async () => {
    const draft = await form.validateFields();
    if (!data) throw new Error("Brand is not loaded");
    const value = await api<BrandState>("/tenant/v1/brand", {
      method: "PUT",
      body: JSON.stringify({ draft, version: data.version }),
    });
    setData(value);
    setPreview(null);
    return value;
  };
  const action = async (kind: "save" | "preview" | "publish") => {
    setBusy(true);
    setError("");
    try {
      const saved = await save();
      if (kind === "preview") {
        setPreview(
          await api("/tenant/v1/brand/preview", {
            method: "POST",
            body: JSON.stringify({ version: saved.version }),
          }),
        );
      } else if (kind === "publish") {
        const next = await api<BrandState>("/tenant/v1/brand/publish", {
          method: "POST",
          body: JSON.stringify({ version: saved.version }),
        });
        setData(next);
      }
      message.success(
        kind === "publish"
          ? l("品牌已发布", "Brand published")
          : l("草稿已保存", "Draft saved"),
      );
    } catch (e) {
      if (!(e as any)?.errorFields) setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const rollback = async (version: number) => {
    setBusy(true);
    try {
      const next = await api<BrandState>("/tenant/v1/brand/rollback", {
        method: "POST",
        body: JSON.stringify({ version }),
      });
      setData(next);
      form.setFieldsValue(next.draft);
      setPreview(null);
      message.success(l("已恢复品牌版本", "Brand version restored"));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const link = (base: string) => {
    const url = new URL(base || window.location.origin);
    url.searchParams.set("preview_token", preview!.token);
    return url.href;
  };
  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <ManagementPageHeader
        title={l("品牌与样式", "Brand and appearance")}
        description={l(
          "草稿保存后可预览；发布后才会更新观众站点。",
          "Save and preview your draft. Publishing updates the viewer site.",
        )}
        extra={select}
      />
      {error && (
        <Alert
          type="error"
          showIcon
          message={error}
          action={
            <Button onClick={() => void load()}>
              {l("重新加载", "Reload")}
            </Button>
          }
        />
      )}
      <Tabs
        items={[
          {
            key: "edit",
            label: l("品牌编辑", "Brand editor"),
            children: (
              <Row gutter={[24, 24]}>
                <Col xs={24} xl={15}>
                  <Card loading={!data && !error}>
                    <Form form={form} layout="vertical">
                      <Form.Item
                        name="name"
                        label={l("品牌名称", "Brand name")}
                        rules={[{ required: true, whitespace: true }]}
                      >
                        <Input maxLength={100} />
                      </Form.Item>
                      <Row gutter={16}>
                        <Col span={12}>
                          <Form.Item
                            name="logo_url"
                            label={l("Logo 地址", "Logo URL")}
                            rules={[{ type: "url" }]}
                          >
                            <Input />
                          </Form.Item>
                        </Col>
                        <Col span={12}>
                          <Form.Item
                            name="favicon_url"
                            label={l("网站图标地址", "Favicon URL")}
                            rules={[{ type: "url" }]}
                          >
                            <Input />
                          </Form.Item>
                        </Col>
                      </Row>
                      <Row gutter={16}>
                        <Col span={12}>
                          <Form.Item
                            name="primary_color"
                            label={l("主题色", "Accent color")}
                            rules={[
                              { required: true, pattern: /^#[0-9a-fA-F]{6}$/ },
                            ]}
                          >
                            <Input
                              placeholder="#0ea5e9"
                              maxLength={7}
                              style={{ height: 44 }}
                            />
                          </Form.Item>
                        </Col>
                        <Col span={12}>
                          <Form.Item
                            name="background_color"
                            label={l("深色背景", "Dark background")}
                            rules={[
                              { required: true, pattern: /^#[0-9a-fA-F]{6}$/ },
                              {
                                validator: (_, value) =>
                                  !value || contrastText(value) === "#ffffff"
                                    ? Promise.resolve()
                                    : Promise.reject(
                                        new Error(
                                          l(
                                            "请选择保持白色文字可读的深色背景",
                                            "Choose a dark background that keeps white text readable",
                                          ),
                                        ),
                                      ),
                              },
                            ]}
                          >
                            <Input
                              placeholder="#0ea5e9"
                              maxLength={7}
                              style={{ height: 44 }}
                            />
                          </Form.Item>
                        </Col>
                      </Row>
                      <Row gutter={16}>
                        <Col span={16}>
                          <Form.Item
                            name="font_family"
                            label={l("字体", "Font family")}
                          >
                            <Select
                              options={[
                                "Inter",
                                "Manrope",
                                "Poppins",
                                "DM Sans",
                                "Space Grotesk",
                                "System",
                              ].map((value) => ({ value }))}
                            />
                          </Form.Item>
                        </Col>
                        <Col span={8}>
                          <Form.Item
                            name="button_radius"
                            label={l("按钮圆角", "Button radius")}
                          >
                            <InputNumber min={0} max={32} addonAfter="px" />
                          </Form.Item>
                        </Col>
                      </Row>
                      <Form.Item
                        name="customer_email"
                        label={l("客服邮箱", "Support email")}
                        rules={[{ type: "email" }]}
                      >
                        <Input />
                      </Form.Item>
                      <Form.Item
                        name="seo_title"
                        label={l("SEO 标题", "SEO title")}
                      >
                        <Input maxLength={150} />
                      </Form.Item>
                      <Form.Item
                        name="seo_description"
                        label={l("SEO 描述", "SEO description")}
                      >
                        <Input.TextArea rows={3} maxLength={500} />
                      </Form.Item>
                      {["facebook", "instagram", "youtube", "tiktok"].map(
                        (key) => (
                          <Form.Item
                            key={key}
                            name={["social_links", key]}
                            label={key}
                            rules={[{ type: "url" }]}
                          >
                            <Input />
                          </Form.Item>
                        ),
                      )}
                      <Space wrap>
                        <Button
                          loading={busy}
                          disabled={!data}
                          onClick={() => void action("save")}
                        >
                          {l("保存草稿", "Save draft")}
                        </Button>
                        <Button
                          loading={busy}
                          disabled={!data}
                          onClick={() => void action("preview")}
                        >
                          {l("生成安全预览", "Generate preview")}
                        </Button>
                        <Button
                          type="primary"
                          loading={busy}
                          disabled={!data}
                          onClick={() =>
                            Modal.confirm({
                              title: l("发布当前品牌？", "Publish this brand?"),
                              content: l(
                                "此预览中的品牌将更新到本站所有观众页面。",
                                "This brand will appear across the viewer pages for this site.",
                              ),
                              okText: l("发布", "Publish"),
                              cancelText: l("取消", "Cancel"),
                              onOk: () => action("publish"),
                            })
                          }
                        >
                          {l("发布品牌", "Publish brand")}
                        </Button>
                      </Space>
                    </Form>
                  </Card>
                </Col>
                <Col xs={24} xl={9}>
                  <Card title={l("即时外观预览", "Appearance preview")}>
                    <div
                      style={{
                        background: values?.background_color || "#0c0a0b",
                        color: "#fff",
                        padding: 24,
                        minHeight: 300,
                        borderRadius: 16,
                        fontFamily: values?.font_family || "system-ui",
                      }}
                    >
                      {values?.logo_url && (
                        <img
                          src={values.logo_url}
                          alt=""
                          style={{
                            width: 52,
                            height: 52,
                            objectFit: "contain",
                          }}
                        />
                      )}
                      <h2>{values?.name || site?.name}</h2>
                      <p>
                        {values?.seo_description ||
                          l(
                            "你的故事，你的品牌。",
                            "Your stories. Your brand.",
                          )}
                      </p>
                      <button
                        type="button"
                        style={{
                          background: values?.primary_color || "#f53666",
                          color: "#fff",
                          border: 0,
                          padding: "12px 22px",
                          borderRadius: values?.button_radius ?? 12,
                        }}
                      >
                        {l("立即观看", "Watch now")}
                      </button>
                    </div>
                    <Typography.Paragraph
                      type="secondary"
                      style={{ marginTop: 16 }}
                    >
                      {l(
                        "预览展示当前表单；安全预览在真实站点中查看已保存的草稿。",
                        "This card reflects the form. The secure preview opens the saved draft on the real site.",
                      )}
                    </Typography.Paragraph>
                    {preview && (
                      <Alert
                        type="success"
                        showIcon
                        message={l("预览链接已生成", "Preview ready")}
                        description={
                          <Space direction="vertical">
                            <Typography.Link
                              href={link(site?.web_url || "")}
                              target="_blank"
                              rel="noreferrer"
                            >
                              {l("打开桌面预览", "Open desktop preview")}
                            </Typography.Link>
                            <Typography.Link
                              href={link(site?.h5_url || site?.web_url || "")}
                              target="_blank"
                              rel="noreferrer"
                            >
                              {l("打开移动预览", "Open mobile preview")}
                            </Typography.Link>
                            <Typography.Text type="secondary">
                              {l("失效时间：", "Expires: ")}
                              {operatorDate(preview.expires_at)}
                            </Typography.Text>
                          </Space>
                        }
                      />
                    )}
                  </Card>
                </Col>
              </Row>
            ),
          },
          {
            key: "history",
            label: l("发布记录", "Published versions"),
            children: (
              <Card>
                <Table
                  rowKey="version"
                  dataSource={data?.revisions || []}
                  pagination={false}
                  columns={[
                    { title: l("版本", "Version"), dataIndex: "version" },
                    {
                      title: l("创建时间", "Created"),
                      dataIndex: "created_at",
                      render: (v) => operatorDate(v),
                    },
                    {
                      title: l("操作", "Action"),
                      render: (_, row) => (
                        <Button
                          loading={busy}
                          onClick={() =>
                            Modal.confirm({
                              title: l(
                                "恢复此品牌版本？",
                                "Restore this brand version?",
                              ),
                              content: l(
                                "观众站点将恢复此版本，业务数据不变。",
                                "The viewer site will use this version. Business data stays unchanged.",
                              ),
                              onOk: () => rollback(row.version),
                            })
                          }
                        >
                          {l("恢复版本", "Restore version")}
                        </Button>
                      ),
                    },
                  ]}
                />
              </Card>
            ),
          },
        ]}
      />
    </Space>
  );
}
export function MerchantBindingPage() {
  const { l, select } = useCopy();
  const identity = useIdentity();
  const native = identity?.tenant?.kind === "native";
  const [form] = Form.useForm();
  const [state, setState] = useState<any>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const load = useCallback(async () => {
    setError("");
    try {
      const result = await api<any>("/tenant/v1/merchant-binding");
      setState(result);
      form.setFieldsValue({
        account_code: result.account_code,
        allow_reuse: result.explicit_reuse || false,
      });
    } catch (e) {
      setError(errorText(e));
    }
  }, [form]);
  useEffect(() => {
    void load();
  }, [load]);
  const save = async () => {
    const values = await form.validateFields().catch(() => null);
    if (!values) return;
    setBusy(true);
    try {
      await api("/tenant/v1/merchant-binding", {
        method: "PUT",
        body: JSON.stringify(values),
      });
      await load();
      message.success(l("收款账号已绑定", "Merchant account bound"));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Space direction="vertical" style={{ width: "100%" }} size="large">
      <ManagementPageHeader
        title={l("本站收款账号", "Site merchant account")}
        description={l("为本站选择可用的收款账号。", "Choose merchant accounts for this site.")}
        extra={select}
      />
      {error && (
        <Alert
          showIcon
          type="error"
          message={error}
          action={
            <Button onClick={() => void load()}>{l("重试", "Retry")}</Button>
          }
        />
      )}
      <Card title="Stripe" loading={!state && !error}>
        <Alert
          showIcon
          type={state?.configured ? "success" : "warning"}
          message={
            state?.configured
              ? l("本站已配置 Stripe", "Stripe account configured")
              : l(
                  "绑定账号后即可使用 Stripe 收款",
                  "Bind an account to accept payments with Stripe",
                )
          }
          style={{ marginBottom: 24 }}
        />
        <Form form={form} layout="vertical" onFinish={() => void save()}>
          <Form.Item
            name="account_code"
            label={l("Stripe 账号", "Stripe account")}
            rules={[{ required: true }]}
          >
            <Select
              showSearch
              optionFilterProp="label"
              options={(state?.available_accounts || []).map((a: any) => ({
                value: a.code,
                label: a.name + " · " + a.code,
                disabled: a.status !== "active",
              }))}
            />
          </Form.Item>
          <Form.Item name="allow_reuse" valuePropName="checked">
            <Checkbox>
              {l(
                "明确允许本公司多个站点共用此收款账号",
                "Explicitly allow company sites to share this merchant account",
              )}
            </Checkbox>
          </Form.Item>
          <Button
            type="primary"
            htmlType="submit"
            loading={busy}
            disabled={!state}
          >
            {l("保存绑定", "Save binding")}
          </Button>
        </Form>
      </Card>
      {native && <>
        <GatewayBindingCard provider="epay" l={l} />
        <GatewayBindingCard provider="creem" l={l} />
      </>}
    </Space>
  );
}

type GatewayBinding = {
  account_code: string | null;
  configured: boolean;
  currency: string;
  available_accounts: { code: string; name: string; status: string; mode?: CreemMode }[];
};

function GatewayBindingCard({ provider, l }: { provider: "epay" | "creem"; l: ReturnType<typeof useCopy>["l"] }) {
  const isEpay = provider === "epay";
  const name = isEpay ? "Epay" : "Creem";
  const path = "/tenant/v1/" + provider + "/merchant-binding";
  const [form] = Form.useForm<{ account_code?: string }>();
  const state = useLoad<GatewayBinding | null>(path, null);
  const binding = state.data;
  const eligible = !isEpay || binding?.currency === "CNY";
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");

  useEffect(() => {
    form.setFieldValue("account_code", binding?.account_code ?? undefined);
  }, [binding, form]);

  async function save({ account_code }: { account_code?: string }) {
    if (!binding || (account_code && !eligible)) return;
    setSaving(true);
    setSaveError("");
    try {
      await api(path, { method: "PUT", body: JSON.stringify({ account_code: account_code || null }) });
      await state.reload();
      message.success(account_code ? l(name + " 账号已绑定", name + " account bound") : l(name + " 账号已解除绑定", name + " account unbound"));
    } catch (cause) {
      setSaveError(errorText(cause));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card title={name} loading={state.loading && !binding}>
      {(state.error || saveError) && <Alert
        type="error"
        showIcon
        message={state.error || saveError}
        action={state.error ? <Button onClick={() => void state.reload()}>{l("重试", "Retry")}</Button> : undefined}
        style={{ marginBottom: 16 }}
      />}
      {binding && <Alert
        type={eligible && binding.configured ? "success" : "info"}
        showIcon
        message={eligible && binding.configured
          ? l("本站已配置 " + name, name + " account configured")
          : isEpay
            ? l("Epay 使用人民币（CNY）收款", "Epay accepts payments in CNY")
            : binding.account_code
              ? l("Creem 账号已绑定，渠道暂未启用", "Creem account bound, payments are currently disabled")
              : l("选择本站的 Creem 账号", "Choose the site's Creem account")}
        description={!isEpay
          ? l("测试账号可用于联调，只有启用测试收款的环境才会向观众展示。", "Use test accounts for integration. Viewers see them only where test payments are enabled.")
          : !eligible
          ? l(`本站币种为 ${binding.currency}。将本站币种设为 CNY 后可绑定 Epay。`, `This site uses ${binding.currency}. Set the site currency to CNY before binding Epay.`)
          : l("适用于金币包和一次性会员。", "Available for coin packs and one-time membership.")}
        style={{ marginBottom: 16 }}
      />}
      <Form name={provider + "-binding"} form={form} layout="vertical" onFinish={save}>
        <Form.Item
          name="account_code"
          label={l(name + " 账号", name + " account")}
          extra={l("清空选择并保存即可解除本站绑定。", "Clear the selection and save to remove the binding.")}
        >
          <Select
            allowClear
            showSearch
            optionFilterProp="label"
            disabled={!binding || (!eligible && !binding.account_code) || saving}
            placeholder={l("选择平台配置的 " + name + " 账号", "Choose a company " + name + " account")}
            options={(binding?.available_accounts ?? []).map(account => ({
              value: account.code,
              label: account.name + " · " + account.code + (account.mode ? " · " +
                (account.mode === "test" ? l("测试", "Test") : l("生产", "Production")) : ""),
              disabled: !eligible || account.status !== "active",
            }))}
          />
        </Form.Item>
        <Button type="primary" htmlType="submit" loading={saving} disabled={!binding || state.loading || (!eligible && !binding.account_code)}>
          {l("保存 " + name + " 绑定", "Save " + name + " binding")}
        </Button>
      </Form>
      {provider === "creem" && binding?.account_code && <>
        <Divider/>
        <CreemProductBindings key={binding.account_code} accountCode={binding.account_code} l={l} />
      </>}
    </Card>
  );
}

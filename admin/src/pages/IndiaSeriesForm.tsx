import { useEffect, useState } from "react";
import { Alert, Button, Checkbox, Divider, Form, Input, InputNumber, Select, Space, Switch, message } from "antd";
import { indiaApi, indiaErrorText, type IndiaSeries } from "./india-api";

const required = [{ required: true, message: "请填写此项" }];
const options = (values: string[]) => values.map(value => ({ value, label: value }));
function localDate(value?: string) {
  if (!value || !Number.isFinite(Date.parse(value))) return "";
  const d = new Date(value);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}
function fields(record?: IndiaSeries | null) {
  return { ...record, edit_rights: false, member_mode: record?.member_window?.mode || "standard", public_at: localDate(record?.member_window?.public_at),
    rights: { ...record?.rights, starts_at: localDate(record?.rights?.starts_at), ends_at: localDate(record?.rights?.ends_at) },
    content_format: record?.content_format || "unknown", content_collections: record?.content_collections || [],
    tags: record?.tags || [], actors: record?.actors || [], search_aliases: record?.search_aliases || [], descriptors: record?.descriptors || [],
    free_episode_count: record?.free_episode_count ?? 1 };
}
export default function IndiaSeriesForm({ record, writable, canPricing, onSaved }: { record?: IndiaSeries | null; writable: boolean; canPricing: boolean; onSaved: (row: IndiaSeries) => void }) {
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);
  const [loadedVersion, setLoadedVersion] = useState<number | undefined>(record?.version);
  const [stale, setStale] = useState(false);
  const [dirty, setDirty] = useState(false);
  const memberMode = Form.useWatch("member_mode", form);
  const editRights = Form.useWatch("edit_rights", form);
  useEffect(() => {
    if (dirty && record?.version !== loadedVersion) { setStale(true); return; }
    form.resetFields(); form.setFieldsValue(fields(record)); setLoadedVersion(record?.version); setStale(false); setDirty(false);
  }, [record?.id, record?.version]);
  const reload = () => { form.resetFields(); form.setFieldsValue(fields(record)); setLoadedVersion(record?.version); setStale(false); setDirty(false); };
  const submit = async (values: any) => {
    if ((!writable && !(record && canPricing)) || stale) return;
    const payload: Record<string, unknown> = {};
    if (writable) {
      if (values.edit_rights) {
        const starts = new Date(values.rights?.starts_at), ends = new Date(values.rights?.ends_at);
        if (!Number.isFinite(starts.getTime()) || !Number.isFinite(ends.getTime()) || ends <= starts) { message.error("授权结束时间必须晚于开始时间"); return; }
        payload.rights = { ...values.rights, starts_at: starts.toISOString(), ends_at: ends.toISOString(), exclusive: !!values.rights.exclusive, evidence_ref: record?.rights?.evidence_ref || "" };
      } else if (!record) payload.rights = null;
      Object.assign(payload, { title: values.title.trim(), title_hi: values.title_hi || "", synopsis: values.synopsis, genre: values.genre, language: values.language,
        content_origin: values.content_origin, age_rating: values.age_rating, content_format: values.content_format,
        content_collections: values.content_collections || [], tags: values.tags || [], actors: values.actors || [], search_aliases: values.search_aliases || [], descriptors: values.descriptors || [] });
    }
    if (canPricing) {
      if (values.member_mode === "early_access" && !Number.isFinite(Date.parse(values.public_at))) { message.error("请填写有效的公开时间"); return; }
      payload.free_episode_count = values.free_episode_count;
      payload.member_window = values.member_mode === "early_access" ? { mode: "early_access", public_at: new Date(values.public_at).toISOString() } : { mode: values.member_mode };
    }
    setSaving(true);
    try {
      const result = await indiaApi<IndiaSeries>("/admin/series" + (record ? "/" + encodeURIComponent(record.id) : ""), {
        method: record ? "PATCH" : "POST", body: JSON.stringify({ ...payload, ...(record ? { version: loadedVersion } : {}) }),
      });
      form.resetFields(); setStale(false); setDirty(false); message.success(record ? "资料已保存，当前状态以旧站返回为准" : "草稿已创建");
      onSaved(result);
    } catch (e) { message.error(indiaErrorText(e)); }
    finally { setSaving(false); }
  };
  return <Form form={form} layout="vertical" onFinish={submit} onValuesChange={() => setDirty(true)} disabled={!writable || saving} initialValues={fields(record)}>
    {stale && <Alert showIcon type="warning" style={{ marginBottom: 16 }} message="旧站已有新版本，请重新载入后再编辑"
      action={<Button onClick={reload}>放弃未保存修改并载入</Button>} />}
    <div className="india-form-grid">
      <Form.Item label="剧集名称" name="title" rules={required}><Input maxLength={160} /></Form.Item>
      <Form.Item label="印地语名称" name="title_hi"><Input maxLength={200} /></Form.Item>
      <Form.Item className="india-form-full" label="剧情简介" name="synopsis" rules={required}><Input.TextArea rows={3} maxLength={3000} showCount /></Form.Item>
      <Form.Item label="题材" name="genre" rules={required}><Input maxLength={40} placeholder="如 drama" /></Form.Item>
      <Form.Item label="原声语言代码" name="language" rules={[...required, { min: 2, max: 12, message: "填写 2–12 位语言代码" }]}><Input placeholder="如 hi、en" /></Form.Item>
      <Form.Item label="内容来源" name="content_origin" rules={required}><Select options={[
        { value: "live_action", label: "真人实拍" }, { value: "animation", label: "动画" }, { value: "ai_generated", label: "AI 生成" }, { value: "ai_assisted", label: "AI 辅助" },
      ]} /></Form.Item>
      <Form.Item label="年龄分级" name="age_rating" rules={required}><Select options={options(["U", "U/A 7+", "U/A 13+", "U/A 16+", "A"])} /></Form.Item>
      <Form.Item label="内容形式" name="content_format" rules={required}><Select options={[{ value: "unknown", label: "尚未分类" }, { value: "micro_series", label: "分集短剧" }, { value: "complete_short_film", label: "完整短片" }]} /></Form.Item>
      <Form.Item label="内容集合" name="content_collections"><Select mode="multiple" options={[{ value: "india_launch", label: "India Launch（仅分集短剧）" }]} /></Form.Item>
      <Form.Item label="免费集数" name="free_episode_count" rules={required}><InputNumber disabled={!canPricing || saving} min={0} max={1000} precision={0} style={{ width: "100%" }} /></Form.Item>
      <Form.Item label="会员观看窗口" name="member_mode" rules={required}><Select disabled={!canPricing || saving} options={[{ value: "standard", label: "普通" }, { value: "exclusive", label: "会员专享" }, { value: "early_access", label: "会员抢先看" }]} /></Form.Item>
      {memberMode === "early_access" && <Form.Item label="公开时间（本地时间）" name="public_at" rules={required}><Input disabled={!canPricing || saving} type="datetime-local" /></Form.Item>}
      <Form.Item label="标签" name="tags"><Select mode="tags" tokenSeparators={[",", "，"]} /></Form.Item>
      <Form.Item label="演员" name="actors"><Select mode="tags" tokenSeparators={[",", "，"]} /></Form.Item>
      <Form.Item label="搜索别名" name="search_aliases"><Select mode="tags" tokenSeparators={[",", "，"]} /></Form.Item>
      <Form.Item label="内容提示" name="descriptors"><Select mode="tags" tokenSeparators={[",", "，"]} /></Form.Item>
    </div>
    <Divider orientation="left">版权授权（选填）</Divider>
    <Alert showIcon type="info" style={{ marginBottom: 16 }} message="版权选填，未勾选时可直接保存草稿和上传视频，已有版权保持不变。需要补录或修改时再勾选；审核及上架仍按现有版权规则校验。" />
    <Form.Item name="edit_rights" valuePropName="checked"><Checkbox>本次填写/更新版权授权</Checkbox></Form.Item>
    {editRights && <div className="india-form-grid">
      <Form.Item label="权利持有人" name={["rights", "holder"]} rules={required}><Input maxLength={300} /></Form.Item>
      <Form.Item label="授权文件版本" name={["rights", "license_version"]} rules={required}><Input maxLength={100} placeholder="与上传的版权证明版本一致" /></Form.Item>
      <Form.Item label="授权地区" name={["rights", "territories"]} rules={[...required, { validator: (_, value) => !value || value.every((v: string) => /^[A-Z]{2}$/.test(v)) ? Promise.resolve() : Promise.reject(new Error("使用大写两位国家代码，如 IN")) }]}><Select mode="tags" tokenSeparators={[",", "，"]} placeholder="如 IN" /></Form.Item>
      <Form.Item label="授权语言" name={["rights", "languages"]} rules={required}><Select mode="tags" tokenSeparators={[",", "，"]} placeholder="如 hi、en" /></Form.Item>
      <Form.Item label="授权平台" name={["rights", "platforms"]} rules={required}><Checkbox.Group options={[{ value: "web", label: "Web" }, { value: "android", label: "Android" }, { value: "ios", label: "iOS" }]} /></Form.Item>
      <Form.Item label="变现授权" name={["rights", "monetization_modes"]} rules={required}><Checkbox.Group options={[{ value: "free", label: "免费" }, { value: "subscription", label: "订阅" }, { value: "advertising", label: "广告" }]} /></Form.Item>
      <Form.Item className="india-form-full" label="允许的用途" name={["rights", "allowed_uses"]} rules={required}><Checkbox.Group options={[
        { value: "streaming", label: "在线播放" }, { value: "promotion", label: "推广" }, { value: "clips", label: "剪辑" }, { value: "adaptation", label: "改编" }, { value: "dubbing", label: "配音" },
        { value: "ai_inputs", label: "AI 输入" }, { value: "ai_voice", label: "AI 声音" }, { value: "ai_likeness", label: "AI 形象" }, { value: "offline_download", label: "离线下载" },
      ]} /></Form.Item>
      <Form.Item label="授权开始（本地时间）" name={["rights", "starts_at"]} rules={required}><Input type="datetime-local" /></Form.Item>
      <Form.Item label="授权结束（本地时间）" name={["rights", "ends_at"]} rules={required}><Input type="datetime-local" /></Form.Item>
      <Form.Item label="独家授权" name={["rights", "exclusive"]} valuePropName="checked"><Switch checkedChildren="是" unCheckedChildren="否" /></Form.Item>
      <Form.Item label="已登记版权证明"><Input readOnly value={record?.rights?.evidence_ref || "保存草稿后，在文件页上传版权证明"} /></Form.Item>
      <Form.Item className="india-form-full" label="撤回与下架条款" name={["rights", "withdrawal_terms"]} rules={required}><Input.TextArea rows={2} maxLength={3000} /></Form.Item>
    </div>}
    <Space><Button type="primary" htmlType={writable ? "submit" : "button"} onClick={!writable && canPricing && record ? async () => { const values = await form.validateFields(["free_episode_count", "member_mode", ...(memberMode === "early_access" ? ["public_at"] : [])]).catch(() => null); if (values) void submit(values); } : undefined} loading={saving} disabled={(!writable && !(record && canPricing)) || stale || saving}>{record ? "保存资料" : "创建草稿"}</Button>
      {record && <span style={{ color: "#8c8c8c" }}>保存资料会重新进入草稿及审核流程</span>}</Space>
  </Form>;
}

import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  Button,
  Card,
  Col,
  Descriptions,
  Divider,
  Form,
  Input,
  InputNumber,
  Popconfirm,
  Row,
  Select,
  Space,
  Spin,
  Switch,
  Steps,
  Tabs,
  Tag,
  Typography,
  Upload,
  message,
} from "antd";
import {
  ArrowLeftOutlined,
  DeleteOutlined,
  LinkOutlined,
  PictureOutlined,
  ReloadOutlined,
  SaveOutlined,
  UploadOutlined,
} from "@ant-design/icons";
import type { UploadProps } from "antd";
import { api, errorText } from "../api";
import { usePermission, useModuleEnabled } from "../me";
import EpisodesPanel from "./EpisodesPanel";
import { PublishPanel } from "./StagePages";
import { DramaGroupsPanel } from "./Groups";

type Drama = {
  id: number;
  title: string;
  synopsis: string;
  cover_url: string;
  status: string;
  free_episode_count: number;
  coin_price_per_episode: number;
  access_mode: string;
  member_pays_coins: boolean;
  non_member_free_episodes: number | null;
  language: string;
  tags: { id: number; name_en: string; name_zh: string }[];
  episode_count: number;
};

type TagOption = { id: number; name_en: string; name_zh: string; status: string; drama_count: number };

const STATUS_LABELS: Record<string, { text: string; color: string }> = {
  draft: { text: "草稿", color: "default" },
  in_review: { text: "审核中", color: "orange" },
  published: { text: "已上架", color: "green" },
  unpublished: { text: "已下架", color: "red" },
};

const COVER_MAX_BYTES = 5 * 1024 * 1024;
const COVER_TYPES = ["image/jpeg", "image/png", "image/webp"];
const COVER_W = 200;
const COVER_H = 266;

export default function DramaEdit() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const canWrite = usePermission("dramas.edit");
  const canUpload = usePermission("dramas.upload");
  const canPricing = usePermission("dramas.pricing");
  const canPublish = usePermission("dramas.publish");
  const canDelete = usePermission("dramas.delete");
  const groupsEnabled = useModuleEnabled("groups");
  const tagsEnabled = useModuleEnabled("tags");
  const canViewGroups = usePermission("groups.view") && groupsEnabled;
  const canViewTags = usePermission("tags.view") && tagsEnabled;
  const [form] = Form.useForm();
  const [drama, setDrama] = useState<Drama | null>(null);
  const [tagOptions, setTagOptions] = useState<TagOption[]>([]);
  const [saving, setSaving] = useState(false);
  const [coverBusy, setCoverBusy] = useState<null | "upload" | "delete">(null);
  const [dirty, setDirty] = useState(false);
  const [urlEditing, setUrlEditing] = useState(false);
  const [urlDraft, setUrlDraft] = useState("");
  const [activeTab, setActiveTab] = useState("info");
  const [coverUrl, setCoverUrl] = useState("");
  // 上架流程清单需要知道有多少集已经真正上传了视频
  const [episodeStats, setEpisodeStats] = useState<{ total: number; uploaded: number } | null>(null);

  const load = useCallback(async () => {
    const [d, t] = await Promise.all([
      api<Drama>(`/admin/v1/dramas/${id}`),
      canViewTags ? api<{ items: TagOption[] }>("/admin/v1/tags") : Promise.resolve({ items: [] as TagOption[] }),
    ]);
    setDrama(d);
    setCoverUrl(d.cover_url ?? "");
    setTagOptions(t.items);
    if (canWrite || canPricing || canUpload) form.setFieldsValue({
      title: d.title,
      synopsis: d.synopsis,
      cover_url: d.cover_url,
      free_episode_count: d.free_episode_count,
      coin_price_per_episode: d.coin_price_per_episode,
      access_mode:d.access_mode,
      member_pays_coins:d.member_pays_coins,
      non_member_free_episodes:d.non_member_free_episodes,
      language:d.language,
      tag_ids: d.tags.map((x) => x.id),
    });
    setDirty(false);
  }, [id, form, canWrite, canPricing, canUpload, canViewTags]);

  useEffect(() => {
    load().catch((e) => message.error(`加载失败：${errorText(e)}`));
  }, [load]);

  const loadEpisodeStats = useCallback(async () => {
    try {
      const res = await api<{ items: { bunny_video_id: string | null }[] }>(
        `/admin/v1/dramas/${id}/episodes?page_size=200`
      );
      setEpisodeStats({
        total: res.items.length,
        uploaded: res.items.filter((e) => e.bunny_video_id).length,
      });
    } catch {
      setEpisodeStats(null);
    }
  }, [id]);

  // 切换 Tab 时重新盘点（分集页可能刚登记/上传过）
  useEffect(() => {
    loadEpisodeStats();
  }, [loadEpisodeStats, activeTab]);

  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (!dirty) return;
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);

  const onSave = async (values: any) => {
    if (!(canWrite || canPricing || canUpload)) return;
    const payload: Record<string, unknown> = {};
    if (canWrite) for (const key of ["title", "synopsis", "language"]) payload[key] = values[key];
    if (canWrite && canViewTags) payload.tag_ids = values.tag_ids;
    if (canPricing) for (const key of ["free_episode_count", "coin_price_per_episode", "access_mode", "member_pays_coins", "non_member_free_episodes"]) payload[key] = values[key];
    if (canUpload) payload.cover_url = values.cover_url;
    setSaving(true);
    try {
      await api(`/admin/v1/dramas/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
      message.success("已保存");
      setDirty(false);
      await load();
    } catch (e: any) {
      message.error(`保存失败：${errorText(e)}`);
    } finally {
      setSaving(false);
    }
  };

  const uploadCover = async (file: File) => {
    if (!canUpload) return;
    setCoverBusy("upload");
    try {
      const fd = new FormData();
      fd.append("file", file, file.name);
      const res = await api<{ cover_url: string }>(`/admin/v1/dramas/${id}/cover`, {
        method: "POST",
        body: fd,
      });
      form.setFieldValue("cover_url", res.cover_url);
      setCoverUrl(res.cover_url);
      setDrama((d) => (d ? { ...d, cover_url: res.cover_url } : d));
      setUrlEditing(false);
      message.success("封面已更新");
    } catch (e: any) {
      message.error(`封面上传失败：${errorText(e)}`);
    } finally {
      setCoverBusy(null);
    }
  };

  const beforeUpload: UploadProps["beforeUpload"] = (file) => {
    const okType =
      COVER_TYPES.includes(file.type || "") || /\.(jpe?g|png|webp)$/i.test(file.name || "");
    if (!okType) {
      message.error("封面只支持 JPG / PNG / WebP 格式");
      return Upload.LIST_IGNORE;
    }
    if (file.size > COVER_MAX_BYTES) {
      message.error("封面大小需在 5MB 以内");
      return Upload.LIST_IGNORE;
    }
    void uploadCover(file as unknown as File);
    return Upload.LIST_IGNORE;
  };

  const removeCover = async () => {
    if (!canUpload) return;
    setCoverBusy("delete");
    try {
      await api(`/admin/v1/dramas/${id}/cover`, { method: "DELETE" });
      form.setFieldValue("cover_url", "");
      setCoverUrl("");
      setDrama((d) => (d ? { ...d, cover_url: "" } : d));
      message.success("封面已删除");
    } catch (e: any) {
      message.error(`删除封面失败：${errorText(e)}`);
    } finally {
      setCoverBusy(null);
    }
  };

  if (!drama) {
    return (
      <Card loading>
        <div style={{ height: 320 }} />
      </Card>
    );
  }

  const statusMeta = STATUS_LABELS[drama.status] ?? { text: drama.status, color: "default" };

  // ---- 上架流程：基本信息 → 登记分集 → 上传视频 → 提交上架 ----
  const uploadedCount = episodeStats?.uploaded ?? 0;
  const basicsReady = Boolean(drama.cover_url && drama.synopsis?.trim());
  const videosReady = drama.episode_count > 0 && uploadedCount >= drama.episode_count;
  const issues = [
    !drama.cover_url ? "还没有上传封面" : null,
    !drama.synopsis?.trim() ? "还没有填写简介" : null,
    drama.episode_count === 0
      ? "还没有登记分集"
      : !videosReady
        ? `还有 ${drama.episode_count - uploadedCount} 集没有上传视频`
        : null,
  ].filter((value): value is string => value !== null);

  const flowSteps = [
    {
      key: "info",
      title: "基本信息",
      desc: basicsReady ? "封面 / 简介已填" : "补封面、简介、标签",
      done: basicsReady,
    },
    {
      key: "episodes",
      title: "登记分集",
      desc: drama.episode_count > 0 ? `${drama.episode_count} 集` : "还没有分集",
      done: drama.episode_count > 0,
    },
    {
      key: "episodes",
      title: "上传视频",
      desc:
        drama.episode_count > 0
          ? `${uploadedCount}/${drama.episode_count} 集已上传`
          : "先登记分集",
      done: videosReady,
    },
    {
      key: "publish",
      title: "提交上架",
      desc: "提交审核 → 上架",
      done: drama.status === "published",
    },
  ];
  const nextStepIndex = flowSteps.findIndex((step) => !step.done);

  const uploadProps: UploadProps = {
    accept: "image/jpeg,image/png,image/webp",
    showUploadList: false,
    beforeUpload,
    disabled: !canUpload || coverBusy !== null,
  };

  const infoTab = (canWrite || canPricing || canUpload) ? (
    <Card styles={{ body: { padding: 24 } }}>
      <Form
        form={form}
        disabled={!canWrite}
        layout="vertical"
        onFinish={onSave}
        onValuesChange={() => setDirty(true)}
        requiredMark="optional"
      >
        {/* cover_url is edited through the cover panel; keep it in the form so saves carry it */}
        <Form.Item name="cover_url" hidden>
          <Input />
        </Form.Item>

        <Row gutter={[40, 24]}>
          <Col xs={24} md={8} lg={7}>
            <Space direction="vertical" size={12} style={{ width: "100%" }}>
              <Typography.Text strong>封面</Typography.Text>
              <div style={{ width: COVER_W, maxWidth: "100%" }}>
                <Upload {...uploadProps}>
                  <div
                    style={{
                      position: "relative",
                      width: COVER_W,
                      maxWidth: "100%",
                      height: COVER_H,
                      borderRadius: 12,
                      overflow: "hidden",
                      border: coverUrl ? "1px solid #f0f0f0" : "1px dashed #d9d9d9",
                      background: "#fafafa",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      cursor: coverBusy ? "wait" : "pointer",
                    }}
                  >
                    {coverUrl ? (
                      <img
                        src={coverUrl}
                        alt="封面预览"
                        style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
                      />
                    ) : (
                      <Space direction="vertical" align="center" size={6} style={{ opacity: 0.55 }}>
                        <PictureOutlined style={{ fontSize: 30 }} />
                        <Typography.Text type="secondary">点击上传封面</Typography.Text>
                      </Space>
                    )}
                    {coverBusy === "upload" && (
                      <div
                        style={{
                          position: "absolute",
                          inset: 0,
                          background: "rgba(255,255,255,0.7)",
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                        }}
                      >
                        <Spin />
                      </div>
                    )}
                  </div>
                </Upload>

                <Space style={{ marginTop: 12 }} wrap>
                  <Upload {...uploadProps}>
                    <Button
                      size="small"
                      icon={<UploadOutlined />}
                      loading={coverBusy === "upload"}
                      disabled={!canUpload || coverBusy !== null}
                    >
                      {coverUrl ? "替换" : "上传"}
                    </Button>
                  </Upload>
                  <Popconfirm
                    title="删除封面"
                    description="将从 CDN 移除该封面，确定删除？"
                    okText="删除"
                    cancelText="取消"
                    okButtonProps={{ danger: true }}
                    onConfirm={removeCover}
                    disabled={!canUpload || !coverUrl}
                  >
                    <Button
                      size="small"
                      danger
                      icon={<DeleteOutlined />}
                      loading={coverBusy === "delete"}
                      disabled={!canUpload || !coverUrl || coverBusy !== null}
                    >
                      删除
                    </Button>
                  </Popconfirm>
                </Space>

                <div style={{ marginTop: 10 }}>
                  <Button
                    type="link"
                    size="small"
                    icon={<LinkOutlined />}
                    style={{ paddingLeft: 0 }}
                    disabled={!canUpload || coverBusy !== null}
                    onClick={() => {
                      if (!urlEditing) setUrlDraft(coverUrl);
                      setUrlEditing(!urlEditing);
                    }}
                  >
                    {urlEditing ? "收起链接登记" : "使用图片链接登记"}
                  </Button>
                  {urlEditing && (
                    <Input.Search
                      disabled={!canUpload}
                      style={{ width: COVER_W, maxWidth: "100%" }}
                      placeholder="https://cdn.example/cover.jpg"
                      value={urlDraft}
                      onChange={(e) => setUrlDraft(e.target.value)}
                      enterButton="应用"
                      onSearch={(v) => {
                        form.setFieldValue("cover_url", v.trim());
                        setCoverUrl(v.trim());
                        setDirty(true);
                        setUrlEditing(false);
                      }}
                    />
                  )}
                </div>

                <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 12, marginBottom: 0 }}>
                  支持 JPG / PNG / WebP，不超过 5MB，建议 3:4 竖版，≥ 720×960。
                </Typography.Paragraph>
              </div>
            </Space>
          </Col>

          <Col xs={24} md={16} lg={17}>
            <Form.Item
              label="剧名"
              name="title"
              rules={[{ required: true, message: "剧名必填" }]}
              style={{ maxWidth: 480 }}
            >
              <Input maxLength={200} showCount placeholder="请输入剧名" />
            </Form.Item>

            <Form.Item label="内容语言" name="language" extra="用于 PC 片库语言筛选，如 en、hi、zh。"><Input disabled={!canWrite} maxLength={16}/></Form.Item>
            <Form.Item label="简介" name="synopsis">
              <Input.TextArea rows={6} maxLength={20000} showCount placeholder="请输入剧情简介" />
            </Form.Item>

            {canViewTags && <Form.Item label="标签" name="tag_ids" style={{ maxWidth: 640 }}>
              <Select
                mode="multiple"
                placeholder="选择标签"
                allowClear
                optionFilterProp="label"
                options={tagOptions.map((t) => ({
                  value: t.id,
                  label: `${t.name_zh || t.name_en}（${t.name_en}）`,
                }))}
              />
            </Form.Item>}

            <Card size="small" title="观看与付费规则" style={{marginBottom:24}}>
              <Typography.Paragraph type="secondary">先设置观看范围和试看集数，再决定会员是否需要金币解锁。单集页面可以覆盖下方统一金币价。</Typography.Paragraph>
              <Row gutter={20}>
                <Col xs={24} md={12}><Form.Item label="观看范围" name="access_mode" extra="会员专享剧在试看范围外要求有效会员。"><Select disabled={!canPricing} options={[{value:'public',label:'所有用户可试看与解锁'},{value:'members',label:'会员专享，游客仅可试看'}]}/></Form.Item></Col>
                <Col xs={24} md={12}><Form.Item label="会员在付费集仍需金币" name="member_pays_coins" valuePropName="checked" extra="关闭后，有效会员可以直接观看付费集。"><Switch disabled={!canPricing}/></Form.Item></Col>
                <Col xs={24} md={12}><Form.Item label="默认免费集数" name="free_episode_count" extra="未单独覆盖非会员试看时，沿用此集数。"><InputNumber disabled={!canPricing} min={0} precision={0} style={{width:'100%'}} addonAfter="集"/></Form.Item></Col>
                <Col xs={24} md={12}><Form.Item label="非会员试看集数覆盖" name="non_member_free_episodes" extra="留空沿用默认免费集数；0 表示不提供试看。"><InputNumber disabled={!canPricing} min={0} precision={0} placeholder="沿用默认免费集数" style={{width:'100%'}} addonAfter="集"/></Form.Item></Col>
                <Col xs={24} md={12}><Form.Item label="统一单集金币价" name="coin_price_per_episode" extra="适用于未单独定价的分集。0 表示不提供金币购买，仍按会员及试看规则判断观看资格。"><InputNumber disabled={!canPricing} min={0} precision={0} style={{width:'100%'}} addonAfter="金币"/></Form.Item></Col>
              </Row>
            </Card>

            <Divider style={{ margin: "8px 0 16px" }} />

            <Space>
              <Button disabled={false} type="primary" htmlType="submit" icon={<SaveOutlined />} loading={saving}>
                保存
              </Button>
              <Button
                icon={<ReloadOutlined />}
                disabled={!dirty || saving}
                onClick={() => load().catch((e) => message.error(`重置失败：${errorText(e)}`))}
              >
                重置
              </Button>
              {dirty && <Typography.Text type="warning">有未保存的修改</Typography.Text>}
            </Space>
          </Col>
        </Row>
      </Form>
    </Card>
  ) : <Card><Descriptions layout="vertical" column={{ xs: 1, md: 2 }} items={[
    { key: "title", label: "剧集名称", children: drama.title },
    { key: "status", label: "状态", children: statusMeta.text },
    { key: "synopsis", label: "简介", children: drama.synopsis || "—", span: 2 },
    { key: "free", label: "免费集数", children: drama.free_episode_count },
    { key: "coins", label: "单集金币", children: drama.coin_price_per_episode },
    { key: "access", label: "观看范围", children: drama.access_mode==='members'?'会员专享，游客仅可试看':'所有用户可试看与解锁' },
    { key: "memberCoins", label: "会员付费集仍需金币", children: drama.member_pays_coins?'是':'否' },
    { key: "preview", label: "非会员试看集数", children: drama.non_member_free_episodes==null?'沿用默认免费集数':drama.non_member_free_episodes },
    { key: "tags", label: "标签", children: drama.tags.map(tag => tag.name_zh || tag.name_en).join("、") || "—" },
  ]} /><Typography.Text type="secondary">当前租户角色仅可查看内容。</Typography.Text></Card>;

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Card styles={{ body: { padding: "16px 24px" } }}>
        <Space wrap size="middle">
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate("/dramas")}>
            返回列表
          </Button>
          <Divider type="vertical" style={{ height: 24 }} />
          <Typography.Title level={4} style={{ margin: 0 }}>
            {drama.title}
          </Typography.Title>
          <Tag color={statusMeta.color}>{statusMeta.text}</Tag>
          <Typography.Text type="secondary">
            ID #{drama.id} · {drama.episode_count} 集
          </Typography.Text>
        </Space>
      </Card>

      {(canWrite || canUpload || canPublish) && drama.status !== "published" && (
        <Card
          size="small"
          title="上架流程"
          extra={
            nextStepIndex >= 0 ? (
              <Button
                type="link"
                style={{ paddingInline: 0 }}
                onClick={() => setActiveTab(flowSteps[nextStepIndex].key)}
              >
                去完成：{flowSteps[nextStepIndex].title} →
              </Button>
            ) : null
          }
        >
          <Steps
            size="small"
            current={nextStepIndex === -1 ? flowSteps.length : nextStepIndex}
            items={flowSteps.map((step, index) => ({
              title: step.title,
              description: step.desc,
              status: step.done ? "finish" : index === nextStepIndex ? "process" : "wait",
            }))}
          />
        </Card>
      )}

      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          { key: "info", label: "基本信息", children: infoTab },
          { key: "episodes", label: "分集", children: <EpisodesPanel dramaId={id!} /> },
          { key: "groups", label: "首页分组", children: <DramaGroupsPanel dramaId={id!} /> },
          {
            key: "publish",
            label: "发布与下架",
            children: (
              <PublishPanel
                dramaId={id!}
                dramaTitle={drama.title}
                episodeCount={drama.episode_count}
                status={drama.status}
                issues={issues}
              />
            ),
          },
        ].filter(item => ["info", "episodes"].includes(item.key) || (item.key === "groups" ? canViewGroups : canPublish || canDelete))}
      />
    </Space>
  );
}

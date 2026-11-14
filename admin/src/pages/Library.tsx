import { useIdentity } from "../identity";
import { operatorDate } from "../site-format";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Drawer,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Progress,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from "antd";
import { useNavigate } from "react-router-dom";
import { api, errorText } from "../api";
import { ManagementPageHeader } from "../components/ManagementPage";
import { directUpload, probeVideo, type UploadSession } from "./episodeUpload";
type Asset = {
  id: string;
  status: string;
  language: string;
  duration_seconds?: number;
  width?: number;
  height?: number;
  size_bytes?: number;
};
type Subtitle = { language: string; label?: string; url: string };
type Episode = {
  id: string;
  episode_number: number;
  title: string;
  subtitles?: Subtitle[];
  media_asset_id?: string;
  asset?: Asset;
};
type Grant = {
  id: string;
  tenant_id: string;
  version: number;
  status: string;
  countries: string[];
  languages: string[];
  sales_until: string | null;
  play_until: string | null;
};
type Title = {
  id: string;
  title: string;
  synopsis: string;
  cover_url: string;
  original_language: string;
  version: number;
  approved_version: number | null;
  status: string;
  episode_count: number;
  rights_note?: string;
  episodes?: Episode[];
  grants?: Grant[];
  grant_id?: string;
  grant?: Grant;
  imported_drama_id?: number;
  imported_version?: number;
  update_available?: boolean;
};
const P = "/platform/v1/library",
  T = "/tenant/v1/library";
export default function LibraryPage({
  platform = false,
}: {
  platform?: boolean;
}) {
  const navigate = useNavigate();
  const identity = useIdentity();
  const canImport = !!(
    identity?.is_tenant_admin ||
    identity?.session_kind === "support" ||
    identity?.permissions.includes("dramas.edit")
  );
  const [page, setPage] = useState(1),
    [pageSize, setPageSize] = useState(20),
    [total, setTotal] = useState(0),
    [search, setSearch] = useState("");
  const [lang, setLang] = useState(() =>
    localStorage.getItem("operator_locale") === "en" ? "en" : "zh",
  );
  const l = (zh: string, en: string) => (lang === "zh" ? zh : en);
  const base = platform ? P : T;
  const [adopting, setAdopting] = useState(false);
  const [subtitleEditing, setSubtitleEditing] = useState<{
    episode: Episode;
    version: number;
  } | null>(null);
  const [subtitleError, setSubtitleError] = useState("");
  const [subtitleForm] = Form.useForm<{ subtitles: Subtitle[] }>();
  const [rows, setRows] = useState<Title[]>([]),
    [loading, setLoading] = useState(false),
    [error, setError] = useState(""),
    [query, setQuery] = useState(""),
    [selected, setSelected] = useState<Title | null>(null),
    [detail, setDetail] = useState<Title | null>(null),
    [detailError, setDetailError] = useState(""),
    [busy, setBusy] = useState(false),
    [editing, setEditing] = useState<Title | "new" | null>(null),
    [importing, setImporting] = useState<Title | null>(null),
    [tenants, setTenants] = useState<
      { id: string; name: string; target_markets?: string[] }[]
    >([]);
  const [titleForm] = Form.useForm(),
    [grantForm] = Form.useForm(),
    [episodeForm] = Form.useForm(),
    [importForm] = Form.useForm();
  const [file, setFile] = useState<File | null>(null),
    [progress, setProgress] = useState<number | null>(null);
  const xhr = useRef<XMLHttpRequest | null>(null);
  const generation = useRef(0);
  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await api<{ items: Title[]; total?: number }>(
        base +
          (platform
            ? "?page=" +
              page +
              "&page_size=" +
              pageSize +
              "&q=" +
              encodeURIComponent(query)
            : ""),
      );
      setRows(result.items);
      setTotal(result.total ?? result.items.length);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setLoading(false);
    }
  }, [base, platform, page, pageSize, query]);
  useEffect(() => {
    void load();
    if (platform)
      void api<any[]>("/platform/v1/tenants")
        .then(setTenants)
        .catch((e) => setError(errorText(e)));
    return () => {
      generation.current++;
      xhr.current?.abort();
    };
  }, [load, platform]);
  const loadDetail = useCallback(
    async (id: string) => {
      const request = ++generation.current;
      setDetailError("");
      try {
        const value = await api<Title>(base + "/" + encodeURIComponent(id));
        if (request === generation.current)
          setDetail({
            ...value,
            grants: value.grants || (value.grant ? [value.grant] : []),
          });
      } catch (e) {
        if (request === generation.current) setDetailError(errorText(e));
      }
    },
    [base],
  );
  const open = (row: Title) => {
    setSelected(row);
    setDetail(null);
    setProgress(null);
    grantForm.resetFields();
    episodeForm.resetFields();
    setFile(null);
    void loadDetail(row.id);
  };
  const edit = (row: Title | "new") => {
    titleForm.resetFields();
    titleForm.setFieldsValue(row === "new" ? { original_language: "en" } : row);
    setEditing(row);
  };
  const saveTitle = async () => {
    const values = await titleForm.validateFields().catch(() => null);
    if (!values || !editing) return;
    setBusy(true);
    try {
      const created = await api<Title>(
        P + (editing === "new" ? "" : "/" + editing.id),
        {
          method: editing === "new" ? "POST" : "PATCH",
          body: JSON.stringify({
            ...values,
            ...(editing === "new" ? {} : { version: editing.version }),
          }),
        },
      );
      setEditing(null);
      await load();
      if (selected?.id === created.id) await loadDetail(created.id);
      message.success(l("内容已保存", "Title saved"));
    } catch (e) {
      message.error(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const addEpisode = async () => {
    if (!detail) return;
    const values = await episodeForm.validateFields().catch(() => null);
    if (!values) return;
    setBusy(true);
    setDetailError("");
    try {
      const episode = await api<Episode>(P + "/" + detail.id + "/episodes", {
        method: "POST",
        body: JSON.stringify(values),
      });
      if (file) {
        const meta = await probeVideo(file);
        const session = await api<UploadSession & { asset_id: string }>(
          P + "/" + detail.id + "/episodes/" + episode.id + "/upload-session",
          {
            method: "POST",
            body: JSON.stringify({
              filename: file.name,
              mime: file.type || "video/mp4",
              size_bytes: file.size,
              width: meta.width,
              height: meta.height,
              duration_seconds: meta.durationSeconds,
              language: values.language,
            }),
          },
        );
        setProgress(0);
        await directUpload(
          { ...session, episode_id: Number(episode.id) },
          file,
          setProgress,
          (value) => {
            xhr.current = value;
          },
        );
        await api(P + "/assets/" + session.asset_id + "/sync-status", {
          method: "POST",
          body: "{}",
        });
      }
      episodeForm.resetFields();
      setFile(null);
      await loadDetail(detail.id);
      await load();
      message.success(
        l(
          "分集已添加，编码状态以服务器为准",
          "Episode added. Encoding status is confirmed by the server.",
        ),
      );
    } catch (e) {
      setDetailError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const uploadEpisode = async (episode: Episode, nextFile: File) => {
    if (!detail) return;
    setBusy(true);
    setDetailError("");
    try {
      const meta = await probeVideo(nextFile);
      const session = await api<UploadSession & { asset_id: string }>(
        P + "/" + detail.id + "/episodes/" + episode.id + "/upload-session",
        {
          method: "POST",
          body: JSON.stringify({
            filename: nextFile.name,
            mime: nextFile.type || "video/mp4",
            size_bytes: nextFile.size,
            width: meta.width,
            height: meta.height,
            duration_seconds: meta.durationSeconds,
            language: episode.asset?.language || detail.original_language,
          }),
        },
      );
      setProgress(0);
      await directUpload(
        { ...session, episode_id: Number(episode.id) },
        nextFile,
        setProgress,
        (value) => {
          xhr.current = value;
        },
      );
      await api(P + "/assets/" + session.asset_id + "/sync-status", {
        method: "POST",
        body: "{}",
      });
      await loadDetail(detail.id);
      await load();
      message.success(l("视频已上传", "Video uploaded"));
    } catch (e) {
      setDetailError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const editSubtitles = (episode: Episode) => {
    if (!detail || !platform) return;
    setSubtitleError("");
    subtitleForm.setFieldsValue({
      subtitles: (episode.subtitles || []).map((subtitle) => ({ ...subtitle })),
    });
    setSubtitleEditing({ episode, version: detail.version });
  };
  const saveSubtitles = async () => {
    if (!detail || !subtitleEditing || !platform) return;
    const values = await subtitleForm.validateFields().catch(() => null);
    if (!values) return;
    setBusy(true);
    setSubtitleError("");
    try {
      await api(
        P + "/" + detail.id + "/episodes/" + subtitleEditing.episode.id,
        {
          method: "PATCH",
          body: JSON.stringify({
            version: subtitleEditing.version,
            subtitles: (values.subtitles || []).map((subtitle) => ({
              language: subtitle.language.trim().toLowerCase(),
              label: subtitle.label?.trim() || "",
              url: subtitle.url.trim(),
            })),
          }),
        },
      );
      setSubtitleEditing(null);
      await loadDetail(detail.id);
      await load();
      message.success(
        l(
          "字幕已保存，请审核新版后授权分发",
          "Subtitles saved. Approve the new version before licensing it.",
        ),
      );
    } catch (e) {
      setSubtitleError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const approve = async () => {
    if (!detail) return;
    setBusy(true);
    try {
      await api(P + "/" + detail.id + "/approve", {
        method: "POST",
        body: JSON.stringify({ version: detail.version }),
      });
      await loadDetail(detail.id);
      await load();
      message.success(l("此版本已审核通过", "Version approved"));
    } catch (e) {
      setDetailError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const addGrant = async () => {
    if (!detail) return;
    const values = await grantForm.validateFields().catch(() => null);
    if (!values) return;
    setBusy(true);
    try {
      await api(P + "/" + detail.id + "/grants", {
        method: "POST",
        body: JSON.stringify({
          ...values,
          sales_until: values.sales_until || null,
          play_until: values.play_until || null,
        }),
      });
      grantForm.resetFields();
      await loadDetail(detail.id);
      message.success(l("站点授权已创建", "Site license created"));
    } catch (e) {
      setDetailError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const changeGrant = async (grant: Grant, status: string) => {
    if (!detail) return;
    setBusy(true);
    try {
      await api(P + "/" + detail.id + "/grants/" + grant.id, {
        method: "PATCH",
        body: JSON.stringify({ version: grant.version, status }),
      });
      await loadDetail(detail.id);
    } catch (e) {
      setDetailError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const importTitle = async () => {
    if (!importing || !canImport) return;
    const values = await importForm.validateFields().catch(() => null);
    if (!values) return;
    setBusy(true);
    try {
      const result = await api<{
        drama_id: number;
        created: boolean;
        episode_count: number;
      }>(T + "/" + importing.id + "/import", {
        method: "POST",
        body: JSON.stringify({
          ...values,
          ...(adopting ? { adopt_new_version: true } : {}),
          grant_id:
            importing.grant_id || detail?.grant_id || selected?.grant_id,
        }),
      });
      setImporting(null);
      await load();
      message.success(
        result.created
          ? l(
              "已引入为本站草稿，请完成定价与审核后发布",
              "Imported as a site draft. Set pricing and complete review before publishing.",
            )
          : l(
              "此内容已经引入，已打开草稿",
              "This title was already imported. Opening the draft.",
            ),
      );
      navigate("/dramas/" + result.drama_id);
    } catch (e) {
      message.error(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const beginImport = (row: Title, adopt = false) => {
    if (row.imported_drama_id && !adopt) {
      navigate("/dramas/" + row.imported_drama_id);
      return;
    }
    setAdopting(adopt);
    setImporting(row);
    importForm.setFieldsValue({
      title: adopt ? row.title + " · v" + row.version : row.title,
      synopsis: row.synopsis,
      cover_url: row.cover_url,
    });
  };
  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <ManagementPageHeader
        title={
          platform
            ? l("公司内容库", "Company content library")
            : l("选剧与授权", "Licensed content library")
        }
        description={
          platform
            ? l(
                "上传母版、审核内容并向国家站授权；各站独立编辑、定价与发布。",
                "Upload masters, approve versions and license titles to country sites. Each site controls its own publishing and pricing.",
              )
            : l(
                "选择已授权内容，引入本站草稿，再完成本地化与上架。",
                "Select licensed content, import a site draft, then localize and publish.",
              )
        }
        extra={
          <>
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
            <Button loading={loading} onClick={() => void load()}>
              {l("刷新", "Refresh")}
            </Button>
            {platform && (
              <Button type="primary" onClick={() => edit("new")}>
                {l("新增内容", "Add title")}
              </Button>
            )}
          </>
        }
      />
      {error && <Alert showIcon type="error" message={error} />}
      <Card>
        <Input.Search
          allowClear
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            if (!e.target.value) {
              setQuery("");
              setPage(1);
            }
          }}
          onSearch={(value) => {
            setQuery(value);
            setPage(1);
          }}
          placeholder={l("搜索剧名", "Search titles")}
          style={{ maxWidth: 360, marginBottom: 20 }}
        />
        <Table<Title>
          rowKey="id"
          loading={loading}
          pagination={
            platform
              ? {
                  current: page,
                  pageSize,
                  total,
                  showSizeChanger: true,
                  onChange: (p, size) => {
                    setPage(p);
                    setPageSize(size);
                  },
                }
              : { defaultPageSize: 20, showSizeChanger: true }
          }
          dataSource={rows.filter((row) =>
            row.title.toLowerCase().includes(query.toLowerCase()),
          )}
          scroll={{ x: 850 }}
          locale={{
            emptyText: (
              <Empty description={l("暂无可用内容", "No available titles")} />
            ),
          }}
          columns={[
            {
              title: l("内容", "Title"),
              render: (_, row) => (
                <Space>
                  {row.cover_url && (
                    <img
                      src={row.cover_url}
                      alt=""
                      style={{
                        width: 42,
                        height: 60,
                        objectFit: "cover",
                        borderRadius: 5,
                      }}
                    />
                  )}
                  <div>
                    <Typography.Text strong>{row.title}</Typography.Text>
                    <div>
                      <Typography.Text type="secondary">
                        {row.original_language} · {row.episode_count}{" "}
                        {l("集", "episodes")}
                      </Typography.Text>
                    </div>
                  </div>
                </Space>
              ),
            },
            {
              title: l("版本", "Version"),
              render: (_, row) => (
                <Space direction="vertical" size={0}>
                  <span>v{row.version}</span>
                  <Tag
                    color={
                      row.approved_version === row.version ||
                      (!platform && row.status === "approved")
                        ? "green"
                        : "orange"
                    }
                  >
                    {row.approved_version === row.version ||
                    (!platform && row.status === "approved")
                      ? l("已审核", "Approved")
                      : l("待审核", "Needs review")}
                  </Tag>
                </Space>
              ),
            },
            {
              title: l("状态", "Status"),
              render: (_, row) =>
                row.imported_drama_id ? (
                  <Tag color="green">{l("已引入", "Imported")}</Tag>
                ) : (
                  <Tag>{row.status}</Tag>
                ),
            },
            {
              title: l("操作", "Actions"),
              render: (_, row) => (
                <Space>
                  <Button onClick={() => open(row)}>
                    {l("详情", "Details")}
                  </Button>
                  {platform ? (
                    <Button onClick={() => edit(row)}>
                      {l("编辑", "Edit")}
                    </Button>
                  ) : row.imported_drama_id ? (
                    <Button
                      type="primary"
                      onClick={() =>
                        navigate("/dramas/" + row.imported_drama_id)
                      }
                    >
                      {l("打开本站剧集", "Open site title")}
                    </Button>
                  ) : (
                    <Button
                      type="primary"
                      disabled={!canImport}
                      onClick={() => beginImport(row)}
                    >
                      {l("引入本站", "Import to site")}
                    </Button>
                  )}
                  {!platform && row.update_available && (
                    <Button
                      type="primary"
                      onClick={() => beginImport(row, true)}
                    >
                      {l("采用新版为新草稿", "Adopt new version as draft")}
                    </Button>
                  )}
                </Space>
              ),
            },
          ]}
        />
      </Card>
      <Modal
        open={!!editing}
        title={
          editing === "new"
            ? l("新增内容", "Add title")
            : l("编辑内容", "Edit title")
        }
        onCancel={() => {
          if (!busy) setEditing(null);
        }}
        onOk={() => void saveTitle()}
        confirmLoading={busy}
        okText={l("保存", "Save")}
        cancelText={l("取消", "Cancel")}
      >
        <Form form={titleForm} layout="vertical">
          <Form.Item
            name="title"
            label={l("剧名", "Title")}
            rules={[{ required: true, whitespace: true }]}
          >
            <Input maxLength={200} />
          </Form.Item>
          <Form.Item name="synopsis" label={l("简介", "Synopsis")}>
            <Input.TextArea rows={4} />
          </Form.Item>
          <Form.Item
            name="cover_url"
            label={l("封面地址", "Cover URL")}
            rules={[{ type: "url" }]}
          >
            <Input />
          </Form.Item>
          <Form.Item
            name="original_language"
            label={l("原始语言", "Original language")}
            rules={[{ required: true }]}
          >
            <Select
              options={[
                { value: "en", label: "English" },
                { value: "zh", label: "中文" },
                { value: "hi", label: "Hindi" },
                { value: "id", label: "Indonesia" },
                { value: "es", label: "Español" },
              ]}
            />
          </Form.Item>
          <Form.Item
            name="rights_note"
            label={l("权利与素材说明", "Rights and materials note")}
          >
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>
      <Drawer
        open={!!selected}
        title={detail?.title || selected?.title}
        width="min(1080px,100vw)"
        onClose={() => {
          if (!busy) {
            generation.current++;
            setSelected(null);
            setDetail(null);
          }
        }}
        closable={!busy}
        maskClosable={!busy}
      >
        {detailError && (
          <Alert
            showIcon
            type="error"
            message={detailError}
            style={{ marginBottom: 16 }}
          />
        )}
        {!detail && !detailError ? (
          <Card loading />
        ) : (
          detail && (
            <>
              <Typography.Paragraph>{detail.synopsis}</Typography.Paragraph>
              <Space wrap style={{ marginBottom: 20 }}>
                <Tag>v{detail.version}</Tag>
                <Tag
                  color={
                    detail.approved_version === detail.version ||
                    (!platform && detail.status === "approved")
                      ? "green"
                      : "orange"
                  }
                >
                  {detail.approved_version === detail.version ||
                  (!platform && detail.status === "approved")
                    ? l("当前版本已审核", "Current version approved")
                    : l("当前版本待审核", "Current version needs review")}
                </Tag>
                {platform ? (
                  <Button
                    type="primary"
                    disabled={
                      detail.approved_version === detail.version ||
                      (!platform && detail.status === "approved") ||
                      busy
                    }
                    loading={busy}
                    onClick={() => void approve()}
                  >
                    {l("审核通过此版本", "Approve this version")}
                  </Button>
                ) : (
                  <Button
                    type="primary"
                    disabled={!canImport && !detail.imported_drama_id}
                    onClick={() => beginImport(detail)}
                  >
                    {detail.imported_drama_id
                      ? l("打开本站草稿", "Open site draft")
                      : l("引入本站", "Import to site")}
                  </Button>
                )}
              </Space>
              <Tabs
                items={[
                  {
                    key: "episodes",
                    label: l("分集与媒体", "Episodes and media"),
                    children: (
                      <Space
                        direction="vertical"
                        style={{ width: "100%" }}
                        size="large"
                      >
                        <Table<Episode>
                          rowKey="id"
                          dataSource={detail.episodes || []}
                          pagination={false}
                          scroll={{ x: 650 }}
                          columns={[
                            {
                              title: l("集号", "Episode"),
                              dataIndex: "episode_number",
                            },
                            { title: l("标题", "Title"), dataIndex: "title" },
                            {
                              title: l("媒体状态", "Media state"),
                              render: (_, ep) => (
                                <Tag
                                  color={
                                    ep.asset?.status === "ready"
                                      ? "green"
                                      : "orange"
                                  }
                                >
                                  {ep.asset?.status || l("待上传", "No upload")}
                                </Tag>
                              ),
                            },
                            {
                              title: l("规格", "Metadata"),
                              render: (_, ep) =>
                                ep.asset ? (
                                  <span>
                                    {ep.asset.language} · {ep.asset.width || 0}×
                                    {ep.asset.height || 0} ·{" "}
                                    {ep.asset.duration_seconds || 0}s
                                  </span>
                                ) : (
                                  "—"
                                ),
                            },
                            {
                              title: l("字幕资源", "Subtitles"),
                              render: (_: unknown, ep: Episode) => (
                                <Space wrap>
                                  {(ep.subtitles || []).length ? (
                                    ep.subtitles!.map((subtitle, index) => (
                                      <Tag key={index}>
                                        {subtitle.label || subtitle.language}{" "}
                                        {index === 0
                                          ? l("· 默认", "· Default")
                                          : ""}
                                      </Tag>
                                    ))
                                  ) : (
                                    <Typography.Text type="secondary">
                                      {l("无字幕", "No subtitles")}
                                    </Typography.Text>
                                  )}
                                  {platform && (
                                    <Button
                                      disabled={busy}
                                      onClick={() => editSubtitles(ep)}
                                    >
                                      {l("编辑字幕", "Edit subtitles")}
                                    </Button>
                                  )}
                                </Space>
                              ),
                            },
                            ...(platform
                              ? [
                                  {
                                    title: l("操作", "Actions"),
                                    render: (_: unknown, ep: Episode) => (
                                      <Space>
                                        <label>
                                          <input
                                            type="file"
                                            accept="video/*"
                                            style={{ maxWidth: 190 }}
                                            aria-label={l(
                                              "上传分集视频",
                                              "Upload episode video",
                                            )}
                                            disabled={busy}
                                            onChange={(e) => {
                                              const f = e.target.files?.[0];
                                              if (f) void uploadEpisode(ep, f);
                                              e.target.value = "";
                                            }}
                                          />
                                        </label>
                                        {ep.media_asset_id && (
                                          <Button
                                            disabled={busy}
                                            onClick={async () => {
                                              setBusy(true);
                                              try {
                                                await api(
                                                  P +
                                                    "/assets/" +
                                                    ep.media_asset_id +
                                                    "/sync-status",
                                                  {
                                                    method: "POST",
                                                    body: "{}",
                                                  },
                                                );
                                                await loadDetail(detail.id);
                                              } catch (e) {
                                                setDetailError(errorText(e));
                                              } finally {
                                                setBusy(false);
                                              }
                                            }}
                                          >
                                            {l("同步状态", "Sync status")}
                                          </Button>
                                        )}
                                      </Space>
                                    ),
                                  },
                                ]
                              : []),
                          ]}
                        />
                        {progress !== null && (
                          <>
                            <Progress
                              percent={progress}
                              status={detailError ? "exception" : undefined}
                            />
                            {xhr.current && (
                              <Button onClick={() => xhr.current?.abort()}>
                                {l("取消上传", "Cancel upload")}
                              </Button>
                            )}
                          </>
                        )}
                        {platform && (
                          <Card title={l("添加分集", "Add episode")}>
                            <Form
                              form={episodeForm}
                              layout="inline"
                              initialValues={{
                                language: detail.original_language,
                              }}
                            >
                              <Form.Item
                                name="episode_number"
                                label={l("集号", "Episode")}
                                rules={[{ required: true }]}
                              >
                                <InputNumber min={1} />
                              </Form.Item>
                              <Form.Item
                                name="title"
                                label={l("标题", "Title")}
                              >
                                <Input />
                              </Form.Item>
                              <Form.Item
                                name="language"
                                label={l("语言", "Language")}
                                rules={[{ required: true }]}
                              >
                                <Input style={{ width: 90 }} />
                              </Form.Item>
                            </Form>
                            <Space wrap style={{ marginTop: 16 }}>
                              <input
                                type="file"
                                accept="video/*"
                                aria-label={l("分集文件", "Episode file")}
                                onChange={(e) =>
                                  setFile(e.target.files?.[0] || null)
                                }
                                disabled={busy}
                              />
                              <Button
                                type="primary"
                                loading={busy}
                                onClick={() => void addEpisode()}
                              >
                                {l("添加并上传", "Add and upload")}
                              </Button>
                            </Space>
                          </Card>
                        )}
                      </Space>
                    ),
                  },
                  {
                    key: "grants",
                    label: l("授权范围", "Licensing"),
                    children: (
                      <Space
                        direction="vertical"
                        style={{ width: "100%" }}
                        size="large"
                      >
                        <Alert
                          showIcon
                          type="info"
                          message={l(
                            "授权在销售、发布及播放时由服务器校验。撤销或到期后不再允许新的销售和播放。",
                            "The server validates rights during sales, publication and playback. Revoked or expired licenses prevent further sales and playback.",
                          )}
                        />
                        <Table<Grant>
                          rowKey="id"
                          dataSource={detail.grants || []}
                          pagination={false}
                          scroll={{ x: 800 }}
                          columns={[
                            {
                              title: l("站点", "Site"),
                              render: (_, g) =>
                                tenants.find((x) => x.id === g.tenant_id)
                                  ?.name || g.tenant_id,
                            },
                            {
                              title: l("国家", "Countries"),
                              render: (_, g) => g.countries.join(", ") || "—",
                            },
                            {
                              title: l("语言", "Languages"),
                              render: (_, g) => g.languages.join(", ") || "—",
                            },
                            {
                              title: l("销售截止", "Sales until"),
                              dataIndex: "sales_until",
                              render: (v) =>
                                v ? operatorDate(v) : l("长期", "No expiry"),
                            },
                            {
                              title: l("播放截止", "Playback until"),
                              dataIndex: "play_until",
                              render: (v) =>
                                v ? operatorDate(v) : l("长期", "No expiry"),
                            },
                            { title: l("状态", "Status"), dataIndex: "status" },
                            ...(platform
                              ? [
                                  {
                                    title: l("操作", "Action"),
                                    render: (_: unknown, g: Grant) => (
                                      <Popconfirm
                                        title={
                                          g.status === "active"
                                            ? l(
                                                "撤销此授权？",
                                                "Revoke this license?",
                                              )
                                            : l(
                                                "重新启用此授权？",
                                                "Reactivate this license?",
                                              )
                                        }
                                        onConfirm={() =>
                                          changeGrant(
                                            g,
                                            g.status === "active"
                                              ? "revoked"
                                              : "active",
                                          )
                                        }
                                      >
                                        <Button
                                          danger={g.status === "active"}
                                          disabled={busy}
                                        >
                                          {g.status === "active"
                                            ? l("撤销", "Revoke")
                                            : l("启用", "Activate")}
                                        </Button>
                                      </Popconfirm>
                                    ),
                                  },
                                ]
                              : []),
                          ]}
                        />
                        {platform && (
                          <Card title={l("授权给国家站", "License to a site")}>
                            <Form form={grantForm} layout="vertical">
                              <Form.Item
                                name="tenant_id"
                                label={l("目标站点", "Target site")}
                                rules={[{ required: true }]}
                              >
                                <Select
                                  options={tenants.map((x) => ({
                                    value: x.id,
                                    label: x.name,
                                  }))}
                                />
                              </Form.Item>
                              <Form.Item
                                name="countries"
                                label={l("国家代码", "Country codes")}
                                rules={[{ required: true, type: "array" }]}
                              >
                                <Select
                                  mode="tags"
                                  tokenSeparators={[",", " "]}
                                  options={["US", "IN", "GB", "SG"].map(
                                    (value) => ({ value }),
                                  )}
                                />
                              </Form.Item>
                              <Form.Item
                                name="languages"
                                label={l("授权语言", "Licensed languages")}
                                rules={[{ required: true, type: "array" }]}
                              >
                                <Select
                                  mode="tags"
                                  tokenSeparators={[",", " "]}
                                  options={["en", "zh", "hi", "id", "es"].map(
                                    (value) => ({ value }),
                                  )}
                                />
                              </Form.Item>
                              <Form.Item
                                name="sales_until"
                                label={l(
                                  "销售截止时间（ISO 8601；留空长期）",
                                  "Sales deadline (ISO 8601; empty for no expiry)",
                                )}
                                rules={[
                                  {
                                    validator: (_, v) =>
                                      !v || !Number.isNaN(Date.parse(v))
                                        ? Promise.resolve()
                                        : Promise.reject(
                                            new Error(
                                              l("时间格式无效", "Invalid date"),
                                            ),
                                          ),
                                  },
                                ]}
                              >
                                <Input placeholder="2027-12-31T23:59:59Z" />
                              </Form.Item>
                              <Form.Item
                                name="play_until"
                                label={l(
                                  "播放截止时间（ISO 8601；留空长期）",
                                  "Playback deadline (ISO 8601; empty for no expiry)",
                                )}
                                rules={[
                                  {
                                    validator: (_, v) =>
                                      !v || !Number.isNaN(Date.parse(v))
                                        ? Promise.resolve()
                                        : Promise.reject(
                                            new Error(
                                              l("时间格式无效", "Invalid date"),
                                            ),
                                          ),
                                  },
                                ]}
                              >
                                <Input placeholder="2027-12-31T23:59:59Z" />
                              </Form.Item>
                              <Button
                                type="primary"
                                loading={busy}
                                onClick={() => void addGrant()}
                              >
                                {l("创建授权", "Create license")}
                              </Button>
                            </Form>
                          </Card>
                        )}
                      </Space>
                    ),
                  },
                ]}
              />
            </>
          )
        )}
      </Drawer>
      <Modal
        open={!!subtitleEditing}
        title={
          l("编辑分集字幕", "Edit episode subtitles") +
          (subtitleEditing
            ? " · " + subtitleEditing.episode.episode_number
            : "")
        }
        width={760}
        zIndex={1100}
        onCancel={() => {
          if (!busy) setSubtitleEditing(null);
        }}
        onOk={() => void saveSubtitles()}
        confirmLoading={busy}
        okText={l("保存字幕", "Save subtitles")}
        cancelText={l("取消", "Cancel")}
        maskClosable={!busy}
        closable={!busy}
      >
        <Alert
          showIcon
          type="info"
          style={{ marginBottom: 20 }}
          message={l(
            "字幕随审核版本分发",
            "Subtitles are distributed with the approved version",
          )}
          description={l(
            "首条字幕为默认字幕。保存后需重新审核并授权；已引入站点与旧版本的字幕保留。请使用可公开访问的字幕地址。",
            "The first track is the default. Save, approve and license the new version; subtitles on existing imports and older versions stay intact. Use publicly accessible subtitle URLs.",
          )}
        />
        {subtitleError && (
          <Alert
            showIcon
            type="error"
            message={subtitleError}
            style={{ marginBottom: 16 }}
          />
        )}
        <Form form={subtitleForm} layout="vertical" disabled={busy}>
          <Form.List
            name="subtitles"
            rules={[
              {
                validator: (_, values: Subtitle[] | undefined) => {
                  const languages = (values || [])
                    .map((subtitle) =>
                      (subtitle?.language || "").trim().toLowerCase(),
                    )
                    .filter(Boolean);
                  if ((values || []).length > 32)
                    return Promise.reject(
                      new Error(
                        l(
                          "最多添加 32 条字幕轨道",
                          "Add up to 32 subtitle tracks",
                        ),
                      ),
                    );
                  return new Set(languages).size === languages.length
                    ? Promise.resolve()
                    : Promise.reject(
                        new Error(
                          l(
                            "同一语言只能添加一条字幕轨道",
                            "Add only one subtitle track per language",
                          ),
                        ),
                      );
                },
              },
            ]}
          >
            {(fields, { add, remove }, { errors }) => (
              <Space
                direction="vertical"
                size="middle"
                style={{ width: "100%" }}
              >
                {fields.length === 0 && (
                  <Empty
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    description={l("暂无字幕轨道", "No subtitle tracks")}
                  />
                )}
                {fields.map((field, index) => (
                  <Card
                    key={field.key}
                    size="small"
                    title={l("字幕轨道", "Subtitle track") + " " + (index + 1)}
                    extra={
                      <Button
                        danger
                        type="text"
                        disabled={busy}
                        onClick={() => remove(field.name)}
                        aria-label={
                          l("删除字幕轨道", "Remove subtitle track") +
                          " " +
                          (index + 1)
                        }
                      >
                        {l("删除", "Remove")}
                      </Button>
                    }
                  >
                    <Space wrap align="start" style={{ width: "100%" }}>
                      <Form.Item
                        name={[field.name, "language"]}
                        label={l("语言代码", "Language code")}
                        rules={[
                          { required: true, whitespace: true },
                          {
                            pattern: /^[a-z]{2,3}(?:-[a-z0-9]{2,8}){0,2}$/i,
                            message: l(
                              "请输入有效语言代码，如 en、zh-Hans",
                              "Enter a language code such as en or zh-Hans",
                            ),
                          },
                        ]}
                      >
                        <Input
                          placeholder="en / zh-Hans"
                          maxLength={16}
                          style={{ width: 170 }}
                        />
                      </Form.Item>
                      <Form.Item
                        name={[field.name, "label"]}
                        label={l("显示名（可选）", "Display name (optional)")}
                      >
                        <Input
                          placeholder="English / 中文"
                          maxLength={80}
                          style={{ width: 260 }}
                        />
                      </Form.Item>
                    </Space>
                    <Form.Item
                      name={[field.name, "url"]}
                      label={l("字幕 URL", "Subtitle URL")}
                      rules={[
                        { required: true, whitespace: true },
                        {
                          validator: (_, value) => {
                            if (!value) return Promise.resolve();
                            const url = String(value).trim();
                            let valid =
                              !/[\s\x00-\x1f\x7f\\]/.test(url) &&
                              url.length <= 500 &&
                              url.startsWith("/") &&
                              !url.startsWith("//");
                            if (
                              !valid &&
                              !/[\s\x00-\x1f\x7f\\]/.test(url) &&
                              url.length <= 500
                            ) {
                              try {
                                const parsed = new URL(url);
                                valid =
                                  parsed.protocol === "https:" &&
                                  !!parsed.hostname &&
                                  !parsed.username &&
                                  !parsed.password;
                              } catch {
                                valid = false;
                              }
                            }
                            return valid
                              ? Promise.resolve()
                              : Promise.reject(
                                  new Error(
                                    l(
                                      "请输入 HTTPS 地址或以单个 / 开头的同源路径；不能包含空格或账号信息",
                                      "Use an HTTPS URL or a same-origin path starting with one /. Spaces and embedded credentials are not allowed.",
                                    ),
                                  ),
                                );
                          },
                        },
                      ]}
                    >
                      <Input
                        placeholder="https://cdn.example.com/subtitles/episode-1.en.vtt"
                        maxLength={500}
                      />
                    </Form.Item>
                  </Card>
                ))}
                <Form.ErrorList errors={errors} />
                <Button
                  block
                  type="dashed"
                  disabled={fields.length >= 32 || busy}
                  onClick={() => add({ language: "", label: "", url: "" })}
                >
                  {l("添加字幕轨道", "Add subtitle track")}
                </Button>
              </Space>
            )}
          </Form.List>
        </Form>
      </Modal>
      <Modal
        open={!!importing}
        title={
          adopting
            ? l("采用新版为新草稿", "Adopt new version as draft")
            : l("引入本站草稿", "Import a site draft")
        }
        onCancel={() => {
          if (!busy) setImporting(null);
        }}
        onOk={() => void importTitle()}
        confirmLoading={busy}
        okText={l("引入并编辑", "Import and edit")}
        cancelText={l("取消", "Cancel")}
      >
        {adopting && (
          <Alert
            showIcon
            type="warning"
            style={{ marginBottom: 16 }}
            message={l(
              "将创建独立新版草稿；旧剧、发布记录与已购权益保留。",
              "A separate draft will be created. Existing titles, releases and purchased access remain intact.",
            )}
          />
        )}
        <Alert
          showIcon
          type="info"
          style={{ marginBottom: 20 }}
          message={l(
            "母版复用，本站独立运营",
            "Shared master, independent site operations",
          )}
          description={l(
            "引入后为草稿。本站可修改文案、定价、分类与上架安排；版权范围由授权约束。",
            "The imported title starts as a draft. Customize its copy, pricing, categories and release schedule within the license scope.",
          )}
        />
        <Form form={importForm} layout="vertical">
          <Form.Item
            name="title"
            label={l("本站剧名", "Site title")}
            rules={[{ required: true }]}
          >
            <Input />
          </Form.Item>
          <Form.Item name="synopsis" label={l("本站简介", "Site synopsis")}>
            <Input.TextArea rows={3} />
          </Form.Item>
          <Form.Item
            name="cover_url"
            label={l("本站封面地址", "Site cover URL")}
            rules={[{ type: "url" }]}
          >
            <Input />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}

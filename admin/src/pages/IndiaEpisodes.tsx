import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Form, Input, InputNumber, Modal, Progress, Select, Space, Table, Tag, Typography, Upload, message } from "antd";
import { PlusOutlined, ReloadOutlined, UploadOutlined } from "@ant-design/icons";
import { indiaApi, indiaUpload, indiaErrorText, STATUS, videoContentType, type IndiaEpisode, type IndiaJob, type IndiaSeries } from "./india-api";

function EpisodeMedia({ episode, writable, admin, onChanged }: { episode: IndiaEpisode; writable: boolean; admin: boolean; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [percent, setPercent] = useState(0);
  const [phase, setPhase] = useState("");
  const [error, setError] = useState("");
  const [jobId, setJobId] = useState("");
  const [job, setJob] = useState<IndiaJob | null>(null);
  const [automatic, setAutomatic] = useState(true);
  const [pendingUpload, setPendingUpload] = useState("");
  const [language, setLanguage] = useState("en");
  const [subtitle, setSubtitle] = useState("");
  const [reason, setReason] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  const changedRef = useRef(onChanged); changedRef.current = onChanged;
  useEffect(() => () => abortRef.current?.abort(), []);
  useEffect(() => {
    if (!jobId || !automatic) return;
    let alive = true, count = 0, timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    const poll = async () => {
      try {
        const row = await indiaApi<IndiaJob>("/admin/jobs/" + encodeURIComponent(jobId), { signal: controller.signal });
        if (!alive) return;
        setJob(row);
        if (["pending", "processing", "queued"].includes(row.status) && ++count < 120) timer = setTimeout(() => void poll(), 15000);
        else { setAutomatic(false); changedRef.current(); }
      } catch (e) { if (alive) { setError(indiaErrorText(e)); setAutomatic(false); } }
    };
    void poll();
    return () => { alive = false; controller.abort(); if (timer) clearTimeout(timer); };
  }, [jobId, automatic]);
  const complete = async (id: string, sha256?: string) => {
    const row = await indiaApi<{ job_id: string; status: string }>("/admin/uploads/" + encodeURIComponent(id) + "/complete", {
      method: "POST", body: JSON.stringify(sha256 ? { sha256 } : {}),
    });
    setJobId(row.job_id); setJob({ id: row.job_id, status: row.status }); setAutomatic(true); setPendingUpload("");
    setPhase("文件已接收，转码任务已提交"); changedRef.current();
  };
  const video = async (file: File) => {
    if (!writable) return false;
    const content_type = videoContentType(file);
    if (!content_type || file.size < 16 || file.size > 100 * 1024 * 1024) { message.error("请选择 MP4 / MOV / WebM / MKV，大小不超过 100 MiB"); return false; }
    const controller = new AbortController(); abortRef.current = controller;
    setBusy(true); setError(""); setPercent(0); setPhase("正在申请上传");
    try {
      const upload = await indiaApi<{ upload_id: string; upload_url: string; max_bytes: number }>("/admin/episodes/" + encodeURIComponent(episode.id) + "/uploads", {
        method: "POST", body: JSON.stringify({ filename: file.name, size_bytes: file.size, content_type }), signal: controller.signal,
      });
      if (file.size > upload.max_bytes) throw new Error("文件超过旧站本次允许的上传大小");
      setPhase("正在上传原视频");
      const receipt = await indiaUpload<{ sha256?: string }>(upload.upload_url, file, content_type, setPercent, controller.signal);
      setPendingUpload(upload.upload_id); setPhase("文件已接收，正在提交转码");
      await complete(upload.upload_id, receipt?.sha256);
      message.success("转码任务已提交");
    } catch (e) { setError(indiaErrorText(e)); }
    finally { setBusy(false); abortRef.current = null; }
    return false;
  };
  const resume = async () => {
    const id = pendingUpload || episode.current_upload_id;
    if (!id) return;
    setBusy(true); setError("");
    try { await complete(id); }
    catch (e) { setError(indiaErrorText(e)); }
    finally { setBusy(false); }
  };
  const refreshJob = async () => {
    if (!jobId) { await resume(); return; }
    try { const row = await indiaApi<IndiaJob>("/admin/jobs/" + encodeURIComponent(jobId)); setJob(row); setError(""); changedRef.current(); }
    catch (e) { setError(indiaErrorText(e)); }
  };
  const retry = async () => {
    if (!jobId) return;
    setBusy(true);
    try {
      await indiaApi("/admin/jobs/" + encodeURIComponent(jobId) + "/retry", { method: "POST", body: JSON.stringify({ reason: "JJTV 内容运营手动重试转码" }) });
      setError(""); setAutomatic(true); await refreshJob();
    } catch (e) { setError(indiaErrorText(e)); }
    finally { setBusy(false); }
  };
  const saveSubtitle = async (missing = false) => {
    setBusy(true);
    try {
      await indiaApi("/admin/episodes/" + encodeURIComponent(episode.id) + "/subtitles", {
        method: "POST", body: JSON.stringify(missing ? { availability: "not_provided", reason } : { language, content: subtitle }),
      });
      message.success(missing ? "已记录字幕缺失声明" : "字幕已保存"); onChanged();
    } catch (e) { message.error(indiaErrorText(e)); }
    finally { setBusy(false); }
  };
  return <Space direction="vertical" size="middle" style={{ width: "100%" }}>
    {error && <Alert type="error" showIcon message={error} closable onClose={() => setError("")} />}
    <Typography.Text type="secondary">最长 20 分钟、最大 100 MiB。上传完成后由旧站转码，实际时长和就绪状态以旧站返回为准。</Typography.Text>
    <Space wrap>
      <Upload accept=".mp4,.mov,.webm,.mkv" showUploadList={false} disabled={!writable || busy} beforeUpload={file => { void video(file); return false; }}>
        <Button type="primary" icon={<UploadOutlined aria-hidden />} disabled={!writable || busy}>上传 / 替换原视频</Button>
      </Upload>
      {(pendingUpload || episode.current_upload_id) && <Button disabled={!writable || busy} onClick={() => void resume()}>查询 / 继续转码</Button>}
      {busy && <Button danger onClick={() => abortRef.current?.abort()} disabled={!abortRef.current}>取消上传</Button>}
    </Space>
    {phase && <div><Typography.Text>{phase}</Typography.Text><Progress percent={percent} status={error ? "exception" : busy ? "active" : "normal"} /></div>}
    {job && <Card size="small" title={<Space><span>转码任务</span><Tag color={job.status === "completed" ? "green" : ["failed", "dead_letter"].includes(job.status) ? "error" : "blue"}>{STATUS[job.status] || job.status}</Tag></Space>}
      extra={<Button icon={<ReloadOutlined aria-hidden />} size="small" onClick={() => void refreshJob()}>刷新状态</Button>}>
      <Space direction="vertical" style={{ width: "100%" }}>
        <Typography.Text type="secondary" copyable>{job.id}</Typography.Text>
        <Typography.Text type="secondary">{automatic ? "正在自动刷新，每 15 秒检查一次。" : "自动刷新已停止，可手动刷新。"}{job.attempts !== undefined ? " 尝试次数：" + job.attempts : ""}</Typography.Text>
        {job.error && <Alert type="error" showIcon message={"转码未完成：" + job.error} />}
        {["failed", "dead_letter"].includes(job.status) && <Button disabled={!admin || busy} loading={busy} onClick={() => void retry()}>重试转码（旧站管理员）</Button>}
      </Space>
    </Card>}
    <Card size="small" title="字幕">
      <Space direction="vertical" style={{ width: "100%" }}>
        <Typography.Text type="secondary">已登记：{episode.subtitle_languages?.join("、") || (episode.subtitle_availability === "not_provided" ? "已声明未提供" : "尚无字幕")}</Typography.Text>
        <Space wrap><Select aria-label="字幕语言" value={language} onChange={setLanguage} options={[{ value: "en", label: "English" }, { value: "hi", label: "हिन्दी" }]} style={{ width: 150 }} />
          <Upload accept=".vtt,text/vtt" showUploadList={false} disabled={!writable || busy} beforeUpload={file => {
            if (file.size > 100000) { message.error("字幕文件不超过 100 KB"); return false; }
            void file.text().then(setSubtitle).catch(() => message.error("无法读取字幕文件")); return false;
          }}><Button icon={<UploadOutlined aria-hidden />} disabled={!writable || busy}>读取 VTT 文件</Button></Upload></Space>
        <Input.TextArea aria-label="WebVTT 字幕内容" rows={5} value={subtitle} onChange={event => setSubtitle(event.target.value)} disabled={!writable || busy} maxLength={100000} placeholder={"WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n字幕内容"} />
        <Button onClick={() => void saveSubtitle()} disabled={!writable || busy || !subtitle.trim()}>保存字幕</Button>
        <Input.TextArea aria-label="无法提供字幕的原因" rows={2} value={reason} onChange={event => setReason(event.target.value)} disabled={!writable || busy} maxLength={1000} placeholder="如果确实无法提供字幕，请填写原因；此声明仍由旧站校验" />
        <Button onClick={() => void saveSubtitle(true)} disabled={!writable || busy || !reason.trim()}>记录无法提供字幕</Button>
      </Space>
    </Card>
  </Space>;
}

export default function IndiaEpisodes({ series, writable, canUpload, oldAdmin, onChanged }: { series: IndiaSeries; writable: boolean; canUpload: boolean; oldAdmin: boolean; onChanged: () => void }) {
  const [adding, setAdding] = useState(false);
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const unlocked = !["published", "scheduled"].includes(series.status);
  const editable = writable && unlocked;
  const uploadable = canUpload && unlocked;
  const episode = series.episodes?.find(ep => ep.id === selected);
  const create = async () => {
    if (!editable) return;
    const value = await form.validateFields().catch(() => null);
    if (!value) return;
    setSaving(true);
    try {
      await indiaApi("/admin/series/" + encodeURIComponent(series.id) + "/episodes", { method: "POST", body: JSON.stringify({ ...value, duration_ms: 12000 }) });
      message.success("分集已创建，请上传原视频"); setAdding(false); form.resetFields(); onChanged();
    } catch (e) { message.error(indiaErrorText(e)); }
    finally { setSaving(false); }
  };
  return <Space direction="vertical" size="middle" style={{ width: "100%" }}>
    {!editable && writable && <Alert showIcon type="info" message="此剧已上架或已排期，请先下架再修改媒体" />}
    <Space><Button type="primary" icon={<PlusOutlined aria-hidden />} disabled={!editable} onClick={() => {
      form.resetFields(); form.setFieldsValue({ episode_no: Math.max(0, ...(series.episodes || []).map(ep => ep.episode_no)) + 1 }); setAdding(true);
    }}>新增分集</Button><Button icon={<ReloadOutlined aria-hidden />} onClick={onChanged}>刷新分集</Button></Space>
    <Table<IndiaEpisode> rowKey="id" dataSource={series.episodes || []} pagination={{ pageSize: 20 }} scroll={{ x: 640 }}
      columns={[
        { title: "集号", dataIndex: "episode_no", width: 70 },
        { title: "分集名称", dataIndex: "title" },
        { title: "时长", dataIndex: "duration_ms", render: (value, row) => row.media_status === "synthetic_ready" ? "上传后读取" : Math.round((value || 0) / 1000) + " 秒" },
        { title: "媒体状态", dataIndex: "media_status", render: value => <Tag color={value === "ready" ? "green" : value === "failed" ? "error" : "default"}>{value === "ready" ? "视频就绪" : STATUS[value] || value}</Tag> },
        { title: "操作", render: (_, row) => <Button type="link" onClick={() => setSelected(row.id)}>视频、转码与字幕</Button> },
      ]} />
    <Modal title="新增分集" open={adding} onCancel={() => setAdding(false)} onOk={() => void create()} confirmLoading={saving} okText="创建" cancelText="取消" destroyOnHidden>
      <Form form={form} layout="vertical" preserve={false}>
        <Form.Item label="集号" name="episode_no" rules={[{ required: true }]}><InputNumber min={1} max={1000} precision={0} style={{ width: "100%" }} /></Form.Item>
        <Form.Item label="分集名称" name="title" rules={[{ required: true, whitespace: true, message: "请输入分集名称" }]}><Input maxLength={160} /></Form.Item>
        <Typography.Text type="secondary">分集创建后待上传原视频，转码完成会回写实际时长。</Typography.Text>
      </Form>
    </Modal>
    <Modal title={episode ? "第 " + episode.episode_no + " 集 · " + episode.title : "分集媒体"} open={!!episode} onCancel={() => setSelected(null)} footer={null} width={820} destroyOnHidden>
      {episode && <EpisodeMedia key={episode.id} episode={episode} writable={uploadable} admin={oldAdmin && uploadable} onChanged={onChanged} />}
    </Modal>
  </Space>;
}

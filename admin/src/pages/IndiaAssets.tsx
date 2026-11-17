import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Input, Progress, Space, Typography, Upload, message } from "antd";
import { UploadOutlined } from "@ant-design/icons";
import { indiaApi, indiaUpload, indiaErrorText, type IndiaSeries } from "./india-api";

export default function IndiaAssets({ series, writable, onChanged }: { series: IndiaSeries; writable: boolean; onChanged: () => void }) {
  const [documentVersion, setDocumentVersion] = useState(series.rights?.license_version || "");
  const [busy, setBusy] = useState("");
  const [percent, setPercent] = useState(0);
  const [error, setError] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  useEffect(() => () => abortRef.current?.abort(), []);
  useEffect(() => { if (!busy) setDocumentVersion(series.rights?.license_version || ""); }, [series.rights?.license_version]);
  const upload = async (file: File, cover: boolean) => {
    if (!writable || busy) return;
    if (!cover && !series.rights) { message.error("请先在资料页补全并保存版权授权，再上传版权证明。"); return; }
    const ext = file.name.toLowerCase().split(".").pop();
    const type = ({ png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", pdf: "application/pdf", txt: "text/plain" } as Record<string, string>)[ext || ""];
    if (!type || cover && !["image/png", "image/jpeg"].includes(type) || file.size > (cover ? 5 : 10) * 1024 * 1024 || !file.size) {
      message.error(cover ? "请选择不超过 5 MiB 的 PNG / JPEG 封面" : "请选择不超过 10 MiB 的 PDF / PNG / JPEG / TXT 版权证明"); return;
    }
    if (!cover && !documentVersion.trim()) { message.error("请填写授权文件版本"); return; }
    const controller = new AbortController(); abortRef.current = controller;
    setBusy(cover ? "封面" : "版权证明"); setPercent(0); setError("");
    try {
      const row = await indiaApi<{ id: string; upload_url: string }>("/admin/series/" + encodeURIComponent(series.id) + (cover ? "/cover/uploads" : "/rights/evidence"), {
        method: "POST", body: JSON.stringify({ filename: file.name, size_bytes: file.size, content_type: type, ...(!cover ? { document_version: documentVersion.trim() } : {}) }), signal: controller.signal,
      });
      await indiaUpload(row.upload_url, file, type, setPercent, controller.signal);
      message.success(cover ? "封面已保存" : "版权证明已登记"); onChanged();
    } catch (e) { setError(indiaErrorText(e)); }
    finally { setBusy(""); abortRef.current = null; }
  };
  return <Space direction="vertical" size="middle" style={{ width: "100%" }}>
    {error && <Alert showIcon type="error" message={error} />}
    {busy && <div><Space><Typography.Text>正在上传{busy}</Typography.Text><Button danger size="small" onClick={() => abortRef.current?.abort()}>取消上传</Button></Space><Progress percent={percent} status="active" /></div>}
    <Card size="small" title="剧集封面">
      <Space direction="vertical">
        <Typography.Text type="secondary">{series.cover_id ? "已登记封面" : "尚未上传封面"} · PNG / JPEG，最大 5 MiB、4096 像素</Typography.Text>
        <Upload accept=".png,.jpg,.jpeg" showUploadList={false} disabled={!writable || !!busy} beforeUpload={file => { void upload(file, true); return false; }}>
          <Button icon={<UploadOutlined aria-hidden />} disabled={!writable || !!busy}>上传 / 替换封面</Button>
        </Upload>
      </Space>
    </Card>
    <Card size="small" title="版权证明">
      <Space direction="vertical" style={{ width: "100%" }}>
        <Typography.Text type="secondary">当前证明：{series.rights?.evidence_ref || "未登记"}。授权版本必须与证明文件版本一致。</Typography.Text>
        {!series.rights && <Alert showIcon type="info" message="请先在资料页补全并保存版权授权，再上传版权证明。" />}
        <label htmlFor="india-document-version">授权文件版本</label>
        <Input id="india-document-version" value={documentVersion} onChange={event => setDocumentVersion(event.target.value)} maxLength={100} disabled={!writable || !!busy || !series.rights} />
        <Upload accept=".pdf,.png,.jpg,.jpeg,.txt" showUploadList={false} disabled={!writable || !!busy || !series.rights || !documentVersion.trim()} beforeUpload={file => { void upload(file, false); return false; }}>
          <Button icon={<UploadOutlined aria-hidden />} disabled={!writable || !!busy || !series.rights || !documentVersion.trim()}>上传版权证明</Button>
        </Upload>
        <Typography.Text type="secondary">PDF / PNG / JPEG / TXT，最大 10 MiB。提交审核前请检查剧集授权声明与此证明一致。</Typography.Text>
      </Space>
    </Card>
  </Space>;
}

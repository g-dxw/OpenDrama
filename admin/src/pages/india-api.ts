import { api, ApiError, API_BASE, getToken, forceReauth, isDeadCredential } from "../api";
import { getCurrentTenantId, INDIA_TENANT_ID } from "../tenant-scope";

export const INDIA_BASE = "/admin/v1/tenants/" + INDIA_TENANT_ID + "/india";
export type IndiaSession = { connected: boolean; configured?: boolean; username?: string; role?: string; expires_at?: string; state?: string };
export type IndiaEpisode = { id: string; episode_no: number; title: string; duration_ms: number; media_status: string; current_upload_id?: string; subtitle_languages?: string[]; subtitle_availability?: string };
export type IndiaSeries = {
  id: string; title: string; title_hi?: string; synopsis: string; genre: string; language: string;
  content_origin: string; content_format?: string; content_collections?: string[]; age_rating: string | null;
  free_episode_count: number; episode_count: number; status: string; version: number;
  rights: Record<string, any> | null; episodes?: IndiaEpisode[]; [key: string]: any;
};
export type IndiaJob = { id: string; status: string; attempts?: number; error?: string; error_details?: Record<string, unknown>; [key: string]: any };
export const STATUS: Record<string, string> = { draft: "草稿", rights_review: "待审核", ready: "待上架", scheduled: "已排期", published: "已上架", suspended: "已下架", expired: "已到期", pending: "排队中", queued: "排队中", processing: "处理中", completed: "已完成", failed: "失败", dead_letter: "重试次数耗尽", superseded: "已被替换", uploading: "上传中", synthetic_ready: "待上传原视频" };
const ERRORS: Record<string, string> = {
  india_connection_busy: "旧站连接正在更新，请稍候重试。",
  india_auth_required: "旧站账号未连接或连接已过期，请重新连接。",
  india_not_configured: "India 内容服务暂未配置完成，请联系平台管理员。",
  india_unavailable: "India 内容服务暂时不可用，请稍后重试。",
  LOCAL_ADAPTER_DISABLED: "旧站当前未开放此媒体操作，请联系管理员核实媒体模式。",
  PRODUCTION_MEDIA_UPLOAD_DISABLED: "旧站当前媒体模式仅允许读取，暂不能上传。",
  VERSION_CONFLICT: "内容已被更新，请刷新后重新检查再保存。",
  RIGHTS_EVIDENCE_REQUIRED: "请先上传与授权版本一致的版权证明。",
  RIGHTS_UNAVAILABLE: "版权授权尚未满足要求，请检查地区、期限、用途和证明。",
  DUPLICATE_MEDIA_UPLOAD: "该视频已登记，请查询或继续原转码任务，避免重复上传。",
  MEDIA_NOT_READY: "视频尚未就绪，请查看转码任务并完成字幕设置。",
  SUBTITLES_REQUIRED: "请上传字幕，或明确记录无法提供字幕的原因。",
  INVALID_CREDENTIALS: "旧站账号或密码不正确。",
  FRESH_AUTH_REQUIRED: "旧站要求重新验证，请重新连接并完成 MFA。",
  SELF_REVIEW_FORBIDDEN: "旧站不允许提交人审核自己的内容，请由其他审核员处理。",
};
export function indiaErrorText(error: any): string {
  const detail = error?.detail;
  if (typeof detail === "string") return detail;
  const code = typeof detail?.code === "string" ? detail.code : "";
  const upstream = typeof detail?.upstream_code === "string" ? detail.upstream_code : "";
  const text = ERRORS[upstream] || ERRORS[code] || detail?.message || error?.message || "请求失败，请重试";
  return text + (upstream && !ERRORS[upstream] ? "（" + upstream + "）" : "");
}

function contentPath(path: string): string {
  const relative = path.replace(/^\/v1(?=\/)/, "");
  if (!relative.startsWith("/admin/") || relative.includes("..") || relative.includes("\\") || relative.includes("://")) throw new Error("上传地址不在 India 内容范围内");
  return INDIA_BASE + relative;
}
export async function indiaApi<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  if (getCurrentTenantId() !== INDIA_TENANT_ID) throw new Error("请先切换到 India 租户");
  const headers = new Headers(options.headers);
  if (!["GET", "HEAD"].includes((options.method || "GET").toUpperCase()) && !headers.has("Idempotency-Key")) headers.set("Idempotency-Key", crypto.randomUUID());
  const result = await api<any>(contentPath(path), { ...options, headers: Object.fromEntries(headers.entries()) });
  return (result && Object.prototype.hasOwnProperty.call(result, "data") ? result.data : result) as T;
}
export function indiaUpload<T = any>(path: string, file: File, contentType: string, progress: (percent: number) => void, signal?: AbortSignal): Promise<T> {
  if (getCurrentTenantId() !== INDIA_TENANT_ID) return Promise.reject(new Error("请先切换到 India 租户"));
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const abort = () => xhr.abort();
    const cleanup = () => signal?.removeEventListener("abort", abort);
    xhr.open("PUT", API_BASE + contentPath(path));
    xhr.timeout = 30 * 60 * 1000;
    xhr.setRequestHeader("Content-Type", contentType);
    xhr.setRequestHeader("X-JJTV-Tenant", INDIA_TENANT_ID);
    xhr.setRequestHeader("Idempotency-Key", crypto.randomUUID());
    const token = getToken();
    if (token) xhr.setRequestHeader("Authorization", "Bearer " + token);
    xhr.upload.onprogress = event => { if (event.lengthComputable && event.total) progress(Math.round(event.loaded / event.total * 100)); };
    xhr.onload = () => {
      cleanup();
      let payload: any = null;
      try { payload = JSON.parse(xhr.responseText); } catch { /* proxy error */ }
      if (xhr.status >= 200 && xhr.status < 300) {
        progress(100); resolve((payload?.data ?? payload) as T);
      } else {
        const detail = payload?.detail ?? payload ?? { message: "上传失败（HTTP " + xhr.status + "）" };
        if (token && isDeadCredential(xhr.status, detail)) forceReauth(detail?.code || "expired");
        reject(new ApiError(xhr.status, detail));
      }
    };
    xhr.onerror = () => { cleanup(); reject(new Error("上传连接中断，请检查网络后重试")); };
    xhr.ontimeout = () => { cleanup(); reject(new Error("上传超时，请检查文件大小和网络")); };
    xhr.onabort = () => { cleanup(); reject(new Error("上传已取消")); };
    signal?.addEventListener("abort", abort, { once: true });
    if (signal?.aborted) { cleanup(); reject(new Error("上传已取消")); return; }
    xhr.send(file);
  });
}
export const videoContentType = (file: File) => {
  const ext = file.name.toLowerCase().split(".").pop();
  return ({ mp4: "video/mp4", mov: "video/quicktime", webm: "video/webm", mkv: "video/x-matroska" } as Record<string, string>)[ext || ""];
};

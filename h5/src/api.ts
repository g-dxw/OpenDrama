import type {
  CommentCreateResult,
  CommentsPayload,
  FavoritesPayload,
  HistoryPayload,
  LedgerPayload,
  LikesPayload,
  MessagesPayload,
} from "./types";

import { createConsumerClient, ApiError } from "../../shared/consumer/client";
export { ApiError };
const DEVICE_KEY = "minitv_h5_device";
export function deviceId() {
  let value = localStorage.getItem(DEVICE_KEY);
  if (!value) {
    value = "h5-" + crypto.randomUUID();
    localStorage.setItem(DEVICE_KEY, value);
  }
  return value;
}
const client = createConsumerClient({
  apiBase:
    import.meta.env.VITE_API_BASE ??
    (import.meta.env.PROD ? "https://jjtv-api.8jj.net" : ""),
  attributionPid: () => localStorage.getItem("jjtv_attribution_pid"),
  deviceId,
  tokenStore: {
    get: () => localStorage.getItem("minitv_h5_token"),
    set: (value) => {
      if (value) localStorage.setItem("minitv_h5_token", value);
      else localStorage.removeItem("minitv_h5_token");
    },
  },
});
export const authToken = client.authToken,
  setAuthToken = client.setAuthToken,
  onUnauthorized = client.onUnauthorized;
const send = client.send;
export const getJson = client.get,
  postJson = client.post;
export interface AuthProviders {
  email: { enabled: boolean };
  google: { enabled: boolean; client_id: string | null };
  /** Absent on deployments built before Facebook sign-in existed. */
  facebook?: { enabled: boolean; app_id: string | null };
}

export interface MeProfile {
  id: number;
  public_id: string;
  email: string | null;
  display_name: string;
  avatar_url: string;
  bio: string;
  coins: number;
  following_count: number;
  follower_count: number;
  received_likes: number;
  reminder_opt_in: boolean;
  membership?: {
    active: boolean;
    plan_code: string | null;
    until: string | null;
    canceling: boolean;
    billing_mode?: "subscription" | "one_time" | null;
    subscription_until?: string | null;
    pass_until?: string | null;
  };
}

export interface Session {
  token: string;
  user_id: number;
  public_id: string;
  created?: boolean;
}

/** Which sign-in methods this deployment actually supports. */
export function getAuthProviders(): Promise<AuthProviders> {
  return getJson<AuthProviders>("/v1/auth/providers");
}

export function sendEmailCode(email: string): Promise<{ sent: boolean }> {
  return postJson<{ sent: boolean }>("/v1/auth/email/code", {
    email,
    purpose: "login",
  });
}

export function authenticateEmail(
  email: string,
  code: string,
): Promise<Session> {
  return postJson<Session>("/v1/auth/email/authenticate", { email, code });
}

export function loginWithGoogle(idToken: string): Promise<Session> {
  return postJson<Session>("/v1/auth/google", { id_token: idToken });
}

/** The H5 SDK only hands over an opaque token; the backend asks Graph who it is. */
export function loginWithFacebook(accessToken: string): Promise<Session> {
  return postJson<Session>("/v1/auth/facebook", { access_token: accessToken });
}

export function getMe(): Promise<MeProfile> {
  return getJson<MeProfile>("/v1/me");
}

export function getFavorites(
  limit = 50,
  offset = 0,
): Promise<FavoritesPayload> {
  return getJson<FavoritesPayload>(
    `/v1/me/favorites?limit=${limit}&offset=${offset}`,
  );
}

export function getHistory(): Promise<HistoryPayload> {
  return getJson<HistoryPayload>("/v1/me/history");
}

export function getLikes(): Promise<LikesPayload> {
  return getJson<LikesPayload>("/v1/me/lists/like");
}

export function getLedger(): Promise<LedgerPayload> {
  return getJson<LedgerPayload>("/v1/me/ledger");
}

export function getMessages(): Promise<MessagesPayload> {
  return getJson<MessagesPayload>("/v1/me/messages");
}

export function readMessages(): Promise<{ unread: number }> {
  return postJson<{ unread: number }>("/v1/me/messages/read", {});
}

export interface CheckinResult {
  streak: number;
  amount: number;
  replayed: boolean;
  balance: number;
}

export function checkin(): Promise<CheckinResult> {
  return postJson<CheckinResult>("/v1/me/checkin", {});
}

export interface DailyTaskItem {
  code: string;
  name: string;
  description: string;
  metric: "like" | "favorite" | "complete";
  target: number;
  progress: number;
  amount: number;
  claimed: boolean;
}

export interface DailyTasksPayload {
  today: string;
  balance: number;
  checkin: { checked_today: boolean; streak: number; amount: number };
  items: DailyTaskItem[];
}

export interface TaskClaimResult {
  code: string;
  amount: number;
  balance: number;
  replayed: boolean;
  progress: number;
}

/** Daily task centre: progress, amounts and today's check-in state. */
export function getTasks(): Promise<DailyTasksPayload> {
  return getJson<DailyTasksPayload>("/v1/me/tasks");
}

/** Same catalogue without a session: amounts and targets, progress zeroed. */
export function getPublicTasks(): Promise<DailyTasksPayload> {
  return getJson<DailyTasksPayload>("/v1/tasks");
}

export function claimTask(code: string): Promise<TaskClaimResult> {
  return postJson<TaskClaimResult>(`/v1/me/tasks/${code}/claim`, {});
}

export function updateMe(body: {
  display_name?: string;
  bio?: string;
}): Promise<{ ok: boolean }> {
  return send("PATCH", "/v1/me", body) as Promise<{ ok: boolean }>;
}

export interface UnlockResult {
  status: "unlocked" | "already_unlocked" | "subscription";
  source: "coin" | "subscription";
  episode_id: number;
  balance_after: number | null;
  replayed?: boolean;
}

/**
 * Spend coins on one episode. The key makes retries safe: reuse the same value
 * for a retry, and mint a new one for a different episode or a fresh intent.
 */
export function unlockEpisode(
  episodeId: number,
  idempotencyKey: string,
): Promise<UnlockResult> {
  return send(
    "POST",
    "/v1/unlock",
    { episode_id: episodeId },
    {
      "Idempotency-Key": idempotencyKey,
    },
  ) as Promise<UnlockResult>;
}

export interface Plan {
  code: string;
  name_en: string;
  name_zh: string;
  price_cents: number;
  currency: string;
  version: number;
  version_id: number;
  benefits: { items?: string[]; best?: boolean };
  billing_mode?: "subscription" | "one_time";
}

export interface PlansPayload {
  items: Plan[];
  billing_mode?: "subscription" | "one_time";
}

export interface CheckoutResult {
  url: string;
  session_id: string;
  order_no: string;
}

/** Weekly / monthly / yearly plans, priced by the backend. */
export function getPlans(): Promise<PlansPayload> {
  return getJson<PlansPayload>("/v1/plans");
}

const membershipIntentKey = (planCode: string) =>
  "jjtv_checkout_membership_" + planCode;
export function membershipCheckoutIntent(planCode: string): string {
  const key = membershipIntentKey(planCode);
  let intent = sessionStorage.getItem(key);
  if (!intent) {
    intent = crypto.randomUUID();
    sessionStorage.setItem(key, intent);
  }
  return intent;
}
/** Rotate only an explicitly expired intent still owned by this account and cache entry. */
export function replaceExpiredMembershipIntent(
  planCode: string,
  expectedIntent: string,
  ownerToken: string,
): boolean {
  const key = membershipIntentKey(planCode);
  if (
    authToken() !== ownerToken ||
    sessionStorage.getItem(key) !== expectedIntent
  )
    return false;
  sessionStorage.setItem(key, crypto.randomUUID());
  return true;
}
/** Uncertain retries keep the exact same payment intent. */
export function startCheckout(
  planCode: string,
  intent = membershipCheckoutIntent(planCode),
): Promise<CheckoutResult> {
  return postJson<CheckoutResult>(
    "/v1/commerce/checkout",
    {
      product_type: "membership",
      code: planCode,
      idempotency_key: intent,
      attribution_pid:
        localStorage.getItem("jjtv_attribution_pid") || undefined,
      success_url:
        window.location.origin +
        "/?checkout=success&surface=h5" +
        window.location.hash,
      cancel_url:
        window.location.origin +
        "/?checkout=cancel&surface=h5" +
        window.location.hash,
    },
    { "Idempotency-Key": intent },
  );
}

/** Stop renewing; access stays until the end of the paid period. */
export function cancelSubscription(): Promise<{ ok: boolean }> {
  return postJson<{ ok: boolean }>("/v1/billing/cancel", {});
}

export interface OrderItem {
  order_no: string;
  plan_code: string;
  plan_name_en: string;
  plan_name_zh: string;
  amount_cents: number;
  currency: string;
  billing_mode: "subscription" | "one_time";
  status: "created" | "paid" | "failed" | "refunded";
  paid_at: string | null;
  created_at: string;
}

export interface OrdersPayload {
  items: OrderItem[];
  has_more: boolean;
}

/** Purchase history shown in the Me page. */
export function getOrders(limit = 20, offset = 0): Promise<OrdersPayload> {
  return getJson<OrdersPayload>(
    `/v1/me/orders?limit=${limit}&offset=${offset}`,
  );
}

export function listComments(
  episodeId: number,
  cursor: number | null = null,
  limit = 20,
) {
  const query = new URLSearchParams({ limit: String(limit) });
  if (cursor) query.set("cursor", String(cursor));
  return getJson<CommentsPayload>(
    `/v1/episodes/${episodeId}/comments?${query}`,
  );
}

export function createComment(episodeId: number, body: string) {
  return postJson<CommentCreateResult>(`/v1/episodes/${episodeId}/comments`, {
    body,
  });
}

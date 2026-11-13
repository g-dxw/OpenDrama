import { createConsumerClient } from "../../shared/consumer/client";
export { ApiError } from "../../shared/consumer/client";
export type * from "../../shared/consumer/types";
export const API_BASE = (
  import.meta.env.VITE_API_BASE ??
  (import.meta.env.PROD ? "https://jjtv-api.8jj.net" : "")
).replace(/\/$/, "");
const client = createConsumerClient({
  apiBase: API_BASE,
  attributionPid: () => localStorage.getItem("jjtv_attribution_pid"),
  deviceId: () => {
    let id = localStorage.getItem("jjtv_pc_device");
    if (!id) {
      id = "pc-" + crypto.randomUUID();
      localStorage.setItem("jjtv_pc_device", id);
    }
    return id;
  },
  tokenStore: {
    get: () => localStorage.getItem("jjtv_pc_token"),
    set: (value) => {
      if (value) localStorage.setItem("jjtv_pc_token", value);
      else localStorage.removeItem("jjtv_pc_token");
    },
  },
});
export const get = client.get,
  post = client.post,
  send = client.send,
  authToken = client.authToken,
  setAuthToken = client.setAuthToken,
  onUnauthorized = client.onUnauthorized;
export interface Profile {
  id: number;
  public_id: string;
  email: string | null;
  display_name: string;
  avatar_url: string;
  bio: string;
  coins: number;
  membership?: {
    active: boolean;
    plan_code: string | null;
    until: string | null;
    canceling: boolean;
    billing_mode?: string;
  };
}
export interface CatalogDrama {
  id: number;
  title: string;
  cover_url: string | null;
  synopsis?: string;
  tags?: string[];
  episode_count?: number;
  published_episode_count?: number;
  play_count?: number;
  free_episode_count?: number;
  language?: string;
}
export interface Catalog {
  items: CatalogDrama[];
  total: number;
  limit: number;
  offset: number;
}
export interface Pack {
  code: string;
  name_en: string;
  name_zh: string;
  price_cents: number;
  currency: string;
  base_coins: number;
  bonus_coins: number;
  coins: number;
  version_id: number;
}
export interface Plan {
  code: string;
  name_en: string;
  name_zh: string;
  price_cents: number;
  currency: string;
  benefits?: { items?: string[]; best?: boolean };
  billing_mode?: string;
}
export interface Order {
  order_no: string;
  product_type: string;
  product_code: string;
  product_name: string;
  amount_cents: number;
  currency: string;
  status: string;
  paid_at: string | null;
  created_at: string;
  coins: number;
  billing_mode: string;
}
export const money = (cents: number, currency = "USD") =>
  new Intl.NumberFormat("en-US", { style: "currency", currency }).format(
    cents / 100,
  );
export const errorMessage = (error: unknown) =>
  error instanceof Error
    ? error.message
    : "Something went wrong. Please try again.";

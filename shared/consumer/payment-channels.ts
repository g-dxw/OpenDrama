type ProductType = "membership" | "coin_pack";
type BillingMode = "one_time" | "subscription";

export interface PaymentChannel {
  code: string;
  name: string;
  provider?: string;
  enabled: boolean;
  method?: string;
  billing_modes?: string[];
  products?: { product_type: ProductType; product_code: string; billing_mode: BillingMode }[];
}

export interface CheckoutOrderStatus {
  order: {
    order_no: string;
    product_type: string;
    product_code: string;
    status: string;
  };
  settled: boolean;
}

export function paymentChannelOptions(
  channels: PaymentChannel[],
  mode: string,
  productType: ProductType,
  products: {code: string}[],
) {
  const enabled = channels.filter((channel) =>
    channel.enabled && (!channel.billing_modes || channel.billing_modes.includes(mode)),
  );
  const byProduct = new Map(products.map(({code}) => [
    code,
    enabled.filter((channel) => {
      if (channel.provider === "epay") return Boolean(channel.method) && mode === "one_time";
      if (channel.provider === "waffo") return channel.billing_modes?.includes(mode) === true;
      if (isProductMappedProvider(channel.provider)) {
        return channel.products?.some((item) =>
          item.product_type === productType && item.product_code === code && item.billing_mode === mode,
        );
      }
      return !channel.provider || channel.provider === "stripe";
    }),
  ]));
  const options = [...byProduct.values()];
  const first = options[0] ?? [];
  const uniform = options.every((items) =>
    items.length === first.length && items.every((item, index) => item === first[index]),
  );
  return {byProduct, common: uniform ? first : null, available: options.some((items) => items.length > 0)};
}

export const paymentChannelKey = (channel: PaymentChannel) =>
  JSON.stringify([channel.provider || "stripe", channel.code, channel.method || ""]);

export function selectedPaymentChannel(channels: PaymentChannel[], key?: string): PaymentChannel | undefined {
  return channels.find((channel) => paymentChannelKey(channel) === key) ?? channels[0];
}

export function isProductMappedProvider(provider?: string | null) {
  return provider === "creem" || provider === "waffo_pancake";
}

const returnFields = {
  epay: ["pid", "trade_no", "out_trade_no", "type", "name", "money", "trade_status", "sign", "sign_type", "param"],
  creem: ["request_id", "checkout_id", "order_id", "customer_id", "subscription_id", "product_id", "signature"],
  waffo_pancake: ["sessionId"],
  waffo: [],
};

export function isReconciledProvider(provider?: string | null): provider is keyof typeof returnFields {
  return typeof provider === "string" && Object.prototype.hasOwnProperty.call(returnFields, provider);
}

export const paymentChannelFields = (channel?: PaymentChannel) => ({
  account_code: channel?.code,
  provider: isReconciledProvider(channel?.provider) ? channel.provider : undefined,
  payment_method: channel?.provider === "epay" ? channel.method : undefined,
});

export const checkoutReturnMarker = (provider?: string) =>
  isReconciledProvider(provider) ? "&payment_provider=" + provider : "";

export function recordCheckoutReturn(storageKey: string, orderNo: string, provider?: string) {
  sessionStorage.setItem(storageKey, orderNo);
  if (isReconciledProvider(provider)) sessionStorage.setItem(storageKey + ":reconcile", orderNo);
  else sessionStorage.removeItem(storageKey + ":reconcile");
  sessionStorage.removeItem(storageKey + ":epay");
}

export interface CheckoutReturnReference {
  result: string;
  orderNo: string | null;
  reconcile: boolean;
}

export function readCheckoutReturn(storageKey: string): CheckoutReturnReference | null {
  const params = new URLSearchParams(window.location.search);
  const result = params.get("checkout");
  if (!result) return null;
  const explicitNumber = params.get("order_no");
  const storedNumber = sessionStorage.getItem(storageKey);
  const provider = params.get("payment_provider");
  const reconcile = isReconciledProvider(provider);
  const orderNo = reconcile && explicitNumber !== null
    ? (/^[A-Za-z0-9_-]{1,64}$/.test(explicitNumber) ? explicitNumber : null)
    : storedNumber;
  return {
    result,
    orderNo,
    reconcile: Boolean(orderNo && (
      reconcile || sessionStorage.getItem(storageKey + ":reconcile") === orderNo ||
      sessionStorage.getItem(storageKey + ":epay") === orderNo
    )),
  };
}

export function cleanupCheckoutReturn(params: URLSearchParams) {
  const provider = params.get("payment_provider");
  if (isReconciledProvider(provider)) {
    params.delete("payment_provider");
    params.delete("order_no");
    for (const key of returnFields[provider]) params.delete(key);
  }
  params.delete("checkout");
}

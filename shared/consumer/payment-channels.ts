export interface PaymentChannel {
  code: string;
  name: string;
  provider?: string;
  enabled: boolean;
  method?: string;
  billing_modes?: string[];
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

export function availablePaymentChannels(channels: PaymentChannel[], mode: string) {
  return channels.filter((channel) => {
    if (!channel.enabled || (channel.billing_modes && !channel.billing_modes.includes(mode))) return false;
    if (channel.provider === "epay") return Boolean(channel.method) && mode === "one_time";
    return !channel.provider || channel.provider === "stripe";
  });
}

export const paymentChannelKey = (channel: PaymentChannel) =>
  JSON.stringify([channel.provider || "stripe", channel.code, channel.method || ""]);

export const paymentChannelFields = (channel?: PaymentChannel) =>
  channel?.provider === "epay"
    ? { account_code: channel.code, provider: channel.provider, payment_method: channel.method }
    : { account_code: channel?.code };

export function recordCheckoutReturn(storageKey: string, orderNo: string, provider?: string) {
  sessionStorage.setItem(storageKey, orderNo);
  if (provider === "epay") sessionStorage.setItem(storageKey + ":epay", orderNo);
  else sessionStorage.removeItem(storageKey + ":epay");
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
  const isEpay = params.get("payment_provider") === "epay";
  const orderNo = isEpay && explicitNumber !== null
    ? (/^[A-Za-z0-9_-]{1,64}$/.test(explicitNumber) ? explicitNumber : null)
    : storedNumber;
  return {
    result,
    orderNo,
    reconcile: Boolean(orderNo && (isEpay || sessionStorage.getItem(storageKey + ":epay") === orderNo)),
  };
}

export function cleanupCheckoutReturn(params: URLSearchParams) {
  if (params.get("payment_provider") === "epay") {
    for (const key of ["payment_provider", "order_no", "pid", "trade_no", "out_trade_no", "type", "name", "money", "trade_status", "sign", "sign_type", "param"]) {
      params.delete(key);
    }
  }
  params.delete("checkout");
}

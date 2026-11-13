import { useEffect, useRef, useState } from "react";
import {
  get,
  post,
  errorMessage,
  money,
  type Pack,
  type Plan,
} from "./consumer-api";
import { useAuth } from "./auth";
import { Failure, Icon, Loading, Modal } from "./components";
export function Store({
  kind = "membership",
  sourceDramaId,
  sourceEpisodeId,
}: {
  kind?: "membership" | "coin_pack";
  sourceDramaId?: number;
  sourceEpisodeId?: number;
}) {
  const { user, requireAuth, refresh } = useAuth();
  const [product, setProduct] = useState(kind),
    [plans, setPlans] = useState<Plan[]>([]),
    [packs, setPacks] = useState<Pack[]>([]),
    [mode, setMode] = useState("one_time"),
    [channels, setChannels] = useState<
      Array<{ code: string; name: string; enabled: boolean }>
    >([]),
    [account, setAccount] = useState(""),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(""),
    [cancelOpen, setCancelOpen] = useState(false);
  const keys = useRef(new Map<string, string>());
  const load = () => {
    setLoading(true);
    setError("");
    Promise.all([
      get<{ items: Plan[]; billing_mode?: string }>("/v1/plans"),
      get<{ items: Pack[] }>("/v1/coin-packs"),
      get<{ items: Array<{ code: string; name: string; enabled: boolean }> }>(
        "/v1/billing/channels",
      ),
    ])
      .then(([p, c, ch]) => {
        setPlans(p.items);
        setMode(p.billing_mode ?? "one_time");
        setPacks(c.items);
        setChannels(ch.items.filter((i) => i.enabled));
        setAccount(ch.items.find((i) => i.enabled)?.code || "");
      })
      .catch((e) => setError(errorMessage(e)))
      .finally(() => setLoading(false));
  };
  useEffect(load, []);
  useEffect(() => setProduct(kind), [kind]);
  async function purchase(code: string) {
    setBusy(code);
    setError("");
    const intent = product + ":" + code + ":" + account;
    let key = keys.current.get(intent);
    if (!key) {
      key = crypto.randomUUID();
      keys.current.set(intent, key);
    }
    const returnHash = sourceDramaId
      ? "#watch/" +
        sourceDramaId +
        (sourceEpisodeId ? "?episode=" + sourceEpisodeId : "")
      : window.location.hash;
    try {
      const result = await post<{ url: string; order_no: string }>(
        "/v1/commerce/checkout",
        {
          product_type: product,
          code,
          account_code: account || undefined,
          idempotency_key: key,
          success_url:
            window.location.origin + "/?checkout=success" + returnHash,
          cancel_url: window.location.origin + "/?checkout=cancel" + returnHash,
          attribution_pid:
            localStorage.getItem("jjtv_attribution_pid") || undefined,
          source_drama_id: sourceDramaId,
        },
      );
      sessionStorage.setItem("jjtv_checkout_order", result.order_no);
      window.location.assign(result.url);
    } catch (e) {
      setError(errorMessage(e));
      setBusy("");
    }
  }
  return (
    <div className="page-content store-page">
      <div className="page-title">
        <span className="eyebrow">More story. Less waiting.</span>
        <h1>
          {product === "membership"
            ? "Your front-row pass"
            : "Keep the story going"}
        </h1>
        <p className="muted">
          {product === "membership"
            ? "Choose the membership that fits you. Prices and benefits are set by JJTV."
            : "Top up your wallet and unlock episodes at your own pace."}
        </p>
      </div>
      <div className="segmented" aria-label="Store category">
        <button
          aria-pressed={product === "membership"}
          onClick={() => setProduct("membership")}
        >
          Membership
        </button>
        <button
          aria-pressed={product === "coin_pack"}
          onClick={() => setProduct("coin_pack")}
        >
          Coin packs
        </button>
      </div>
      {user?.membership?.active && (
        <div className="membership-banner">
          <Icon name="crown" />
          <div>
            <strong>Your membership is active</strong>
            <p>
              {user.membership.until
                ? "Access until " +
                  new Date(user.membership.until).toLocaleDateString()
                : ""}
              {user.membership.canceling ? " · Renewal cancelled" : ""}
            </p>
          </div>
          {!user.membership.canceling &&
            user.membership.billing_mode === "subscription" && (
              <button
                className="button secondary"
                onClick={() => setCancelOpen(true)}
              >
                Cancel renewal
              </button>
            )}
        </div>
      )}
      {error && <Failure message={error} retry={load} />}{" "}
      {loading ? (
        <Loading />
      ) : (
        <>
          <div className="purchase-grid">
            {product === "membership"
              ? plans.map((plan) => (
                  <article
                    className={
                      "purchase-card " +
                      (plan.benefits?.best ? "recommended" : "")
                    }
                    key={plan.code}
                  >
                    {plan.benefits?.best && (
                      <span className="purchase-badge">Recommended</span>
                    )}
                    <Icon name="crown" size={30} />
                    <h2>{plan.name_en || plan.name_zh}</h2>
                    <div className="purchase-price">
                      {money(plan.price_cents, plan.currency)}
                    </div>
                    <p className="muted">
                      {(plan.billing_mode || mode) === "subscription"
                        ? "Auto-renewing subscription"
                        : "One-time payment"}
                    </p>
                    <ul>
                      {(plan.benefits?.items ?? []).map((text) => (
                        <li key={text}>
                          <Icon name="check" size={16} />
                          {text}
                        </li>
                      ))}
                    </ul>
                    <button
                      className="button primary"
                      disabled={!!busy || !channels.length}
                      onClick={() =>
                        requireAuth(() => {
                          void purchase(plan.code);
                        })
                      }
                    >
                      {busy === plan.code
                        ? "Opening checkout…"
                        : "Choose " + (plan.name_en || plan.name_zh)}
                    </button>
                  </article>
                ))
              : packs.map((pack) => (
                  <article className="purchase-card" key={pack.code}>
                    <Icon name="coin" size={30} />
                    <h2>{pack.name_en || pack.name_zh}</h2>
                    <div className="coin-total">
                      {pack.coins.toLocaleString()}
                      <span> coins</span>
                    </div>
                    {pack.bonus_coins > 0 && (
                      <p className="bonus">
                        Includes {pack.bonus_coins.toLocaleString()} bonus coins
                      </p>
                    )}
                    <div className="purchase-price">
                      {money(pack.price_cents, pack.currency)}
                    </div>
                    <button
                      className="button primary"
                      disabled={!!busy || !channels.length}
                      onClick={() =>
                        requireAuth(() => {
                          void purchase(pack.code);
                        })
                      }
                    >
                      {busy === pack.code ? "Opening checkout…" : "Buy coins"}
                    </button>
                  </article>
                ))}
          </div>
          {!(product === "membership" ? plans : packs).length && (
            <p className="empty">No products are available right now.</p>
          )}
          {channels.length > 1 && (
            <label className="payment-channel">
              Payment option
              <select
                value={account}
                onChange={(e) => setAccount(e.target.value)}
              >
                {channels.map((channel) => (
                  <option value={channel.code} key={channel.code}>
                    {channel.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          {!channels.length && (
            <p className="error-text">
              Payments are currently unavailable. Please try again later.
            </p>
          )}
          <p className="fine-print">
            Payments are securely processed by Stripe. You will review your
            purchase before paying.{" "}
            {product === "membership" && mode === "subscription"
              ? "Cancel renewal from your account at any time."
              : ""}
          </p>
        </>
      )}
      {cancelOpen && (
        <Modal
          title="Cancel membership renewal?"
          onClose={() => setCancelOpen(false)}
        >
          <p>
            Your membership benefits remain available until the end of your paid
            period.
          </p>
          <button
            className="button primary"
            disabled={!!busy}
            onClick={() => {
              setBusy("cancel");
              post("/v1/billing/cancel", {})
                .then(() => refresh())
                .then(() => setCancelOpen(false))
                .catch((e) => setError(errorMessage(e)))
                .finally(() => setBusy(""));
            }}
          >
            Confirm cancellation
          </button>
        </Modal>
      )}
    </div>
  );
}
export function CheckoutReturn({ result }: { result: string }) {
  const { refresh, user } = useAuth();
  const [state, setState] = useState("Checking your order…");
  useEffect(() => {
    if (result !== "success") {
      setState(
        "Checkout was cancelled. You have not been charged by this page.",
      );
      return;
    }
    let active = true;
    let tries = 0;
    const check = async () => {
      try {
        await refresh();
        const orders = await get<{
          items: Array<{ order_no: string; status: string }>;
        }>("/v1/commerce/orders?limit=100");
        if (!active) return;
        if (
          orders.items.find(
            (order) =>
              order.order_no === sessionStorage.getItem("jjtv_checkout_order"),
          )?.status === "paid"
        ) {
          setState("Payment confirmed. Your account has been updated.");
          return;
        }
        if (tries++ < 5) timer = window.setTimeout(() => void check(), 2000);
        else
          setState(
            "Your payment is still being confirmed. Check your orders for the latest status.",
          );
      } catch {
        if (active)
          setState(
            "Please sign in and check your orders for payment confirmation.",
          );
      }
    };
    let timer = 0;
    void check();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [result]);
  return (
    <div className="checkout-notice" role="status">
      <Icon name="coin" />
      <span>
        {state}
        {user ? " Wallet: " + user.coins.toLocaleString() + " coins." : ""}
      </span>
      <a href="#account/orders">View orders</a>
    </div>
  );
}

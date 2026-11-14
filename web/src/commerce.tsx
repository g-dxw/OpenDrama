import { useEffect, useRef, useState } from "react";
import {
  get,
  post,
  errorMessage,
  money,
  type Pack,
  type Plan,
  type Order,
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
  const [plans, setPlans] = useState<Plan[]>([]),
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
  const keys = useRef(new Map<string, string>()),
    submitting = useRef(false);
  const load = () => {
    setLoading(true);
    setError("");
    Promise.allSettled([
      get<{ items: Plan[]; billing_mode?: string }>("/v1/plans"),
      get<{ items: Pack[] }>("/v1/coin-packs"),
      get<{ items: Array<{ code: string; name: string; enabled: boolean }> }>(
        "/v1/billing/channels",
      ),
    ])
      .then(([p, c, ch]) => {
        const errors: string[] = [];
        if (p.status === "fulfilled") {
          setPlans(p.value.items);
          setMode(p.value.billing_mode ?? "one_time");
        } else errors.push("Membership plans: " + errorMessage(p.reason));
        if (c.status === "fulfilled") setPacks(c.value.items);
        else errors.push("Coin packs: " + errorMessage(c.reason));
        if (ch.status === "fulfilled") {
          const available = ch.value.items.filter((i) => i.enabled);
          setChannels(available);
          setAccount((previous) =>
            available.some((i) => i.code === previous)
              ? previous
              : available[0]?.code || "",
          );
        } else {
          setChannels([]);
          errors.push("Payment options: " + errorMessage(ch.reason));
        }
        setError(errors.join(" "));
      })
      .finally(() => setLoading(false));
  };
  useEffect(load, []);
  useEffect(() => {
    if (!loading && kind === "coin_pack")
      document.getElementById("coin-packs")?.scrollIntoView({ block: "start" });
  }, [kind, loading]);
  function selectTab(next: "membership" | "coin_pack") {
    const params = new URLSearchParams(
      window.location.hash.split("?")[1] || "",
    );
    params.set("tab", next === "coin_pack" ? "coins" : "premium");
    window.location.hash = "price?" + params.toString();
    if (next === "membership")
      document
        .getElementById("membership-plans")
        ?.scrollIntoView({ block: "start" });
  }
  async function purchase(product: "membership" | "coin_pack", code: string) {
    if (submitting.current) return;
    submitting.current = true;
    const selection = product + ":" + code;
    setBusy(selection);
    setError("");
    const intent = selection + ":" + account;
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
    } finally {
      submitting.current = false;
    }
  }
  const firstCurrency = plans[0]?.currency;
  const minimum = plans
    .filter((p) => p.currency === firstCurrency)
    .reduce((lowest, p) => Math.min(lowest, p.price_cents), Infinity);
  return (
    <div className="page-content price-page">
      <div className="price-heading">
        <div>
          <h1>Price</h1>
          <p className="muted">
            Membership plans and coin packs, all in one place.
          </p>
        </div>
        {Number.isFinite(minimum) && (
          <span className="price-from">
            Premium from {money(minimum, firstCurrency)}
          </span>
        )}
      </div>
      <div className="segmented price-tabs" aria-label="Store category">
        <button
          aria-pressed={kind === "membership"}
          onClick={() => selectTab("membership")}
        >
          Premium
        </button>
        <button
          aria-pressed={kind === "coin_pack"}
          onClick={() => selectTab("coin_pack")}
        >
          Coins
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
      {error && <Failure message={error} retry={load} />}
      {loading ? (
        <Loading />
      ) : (
        <>
          <section className="price-section" id="membership-plans">
            <div className="price-section-heading">
              <h2>Membership plans</h2>
              <p className="muted">
                {mode === "subscription"
                  ? "Choose your plan. Cancel renewal at any time."
                  : "One payment for your selected access period."}
              </p>
            </div>
            <div className="membership-grid">
              {plans.map((plan) => (
                <article
                  className={
                    "membership-card " +
                    (plan.benefits?.best ? "recommended" : "")
                  }
                  key={plan.code}
                >
                  {plan.benefits?.best && (
                    <span className="purchase-badge">Most popular</span>
                  )}
                  <div className="plan-card-heading">
                    <div>
                      <h3>{plan.name_en || plan.name_zh}</h3>
                      <span>
                        {(plan.billing_mode || mode) === "subscription"
                          ? "Auto-renewing"
                          : "One-time pass"}
                      </span>
                      {(plan.billing_mode || mode) === "one_time" &&
                        Number(plan.duration_days) > 0 && (
                          <p className="plan-duration">
                            Access for {plan.duration_days} days
                          </p>
                        )}
                    </div>
                    <strong>
                      {money(plan.price_cents, plan.currency)}
                      {(plan.billing_mode || mode) === "subscription" &&
                        plan.recurring_interval && (
                          <small className="plan-cadence">
                            per {plan.recurring_interval}
                          </small>
                        )}
                    </strong>
                  </div>
                  {Number(plan.base_coins) + Number(plan.bonus_coins) > 0 && (
                    <div className="plan-coin-strip">
                      <strong>
                        +
                        {(
                          Number(plan.base_coins || 0) +
                          Number(plan.bonus_coins || 0)
                        ).toLocaleString()}{" "}
                        coins included
                      </strong>
                      <span>
                        {plan.base_coins || 0} base + {plan.bonus_coins || 0}{" "}
                        bonus
                      </span>
                    </div>
                  )}
                  <ul className="plan-benefit-list">
                    {(plan.benefits?.items ?? []).map((text) => (
                      <li key={text}>
                        <Icon name="check" size={15} />
                        {text}
                      </li>
                    ))}
                  </ul>
                  <button
                    className="button secondary full-width"
                    disabled={!!busy || !channels.length}
                    onClick={() =>
                      requireAuth(() => {
                        void purchase("membership", plan.code);
                      })
                    }
                  >
                    {busy === "membership:" + plan.code
                      ? "Opening checkout…"
                      : (plan.billing_mode || mode) === "subscription"
                        ? "Subscribe"
                        : "Buy pass"}
                  </button>
                </article>
              ))}
            </div>
            {!plans.length && (
              <p className="empty compact">
                No membership plans are available right now.
              </p>
            )}
            <p className="section-footnote">
              Episode access and any coin charges follow the rules shown on each
              drama.
            </p>
          </section>
          <section className="price-section" id="coin-packs">
            <div className="price-section-heading">
              <h2>Coin packs</h2>
              <p className="muted">
                Choose your top-up. Episode prices are shown before you unlock.
              </p>
            </div>
            <div className="coin-pack-grid">
              {packs.map((pack) => (
                <article
                  className={"coin-pack-card " + (pack.badge ? "featured" : "")}
                  key={pack.code}
                >
                  {pack.badge && (
                    <span className="purchase-badge">{pack.badge}</span>
                  )}
                  <h3 className="sr-only">{pack.name_en || pack.name_zh}</h3>
                  <div className="coin-pack-total">
                    <strong>{pack.coins.toLocaleString()}</strong>
                    <span>coins</span>
                    {pack.bonus_coins > 0 && (
                      <span className="coin-bonus">
                        +{pack.bonus_coins.toLocaleString()} bonus
                      </span>
                    )}
                  </div>
                  <p className="coin-breakdown">
                    {pack.base_coins.toLocaleString()} base +{" "}
                    {pack.bonus_coins.toLocaleString()} bonus coins
                  </p>
                  <p className="coin-unit-price">
                    {pack.coins > 0
                      ? money(
                          (pack.price_cents / pack.coins) * 100,
                          pack.currency,
                        ) + " per 100 coins"
                      : ""}
                  </p>
                  <div className="coin-pack-footer">
                    <strong>{money(pack.price_cents, pack.currency)}</strong>
                    <button
                      className="button secondary"
                      disabled={!!busy || !channels.length}
                      onClick={() =>
                        requireAuth(() => {
                          void purchase("coin_pack", pack.code);
                        })
                      }
                    >
                      {busy === "coin_pack:" + pack.code
                        ? "Opening…"
                        : "Buy coins"}
                    </button>
                  </div>
                </article>
              ))}
            </div>
            {!packs.length && (
              <p className="empty compact">
                No coin packs are available right now.
              </p>
            )}
            <a className="reward-callout" href="#rewards?return=price">
              <span>
                <strong>No budget today?</strong> Check in and finish daily
                tasks to earn coins.
              </span>
              <span>Go to Rewards →</span>
            </a>
          </section>
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
            <div className="payment-unavailable" role="status">
              <Icon name="lock" size={18} />
              <span>
                Payments are currently unavailable. You can still earn coins
                through daily tasks.
              </span>
            </div>
          )}
          <footer className="price-footer">
            <div>
              <a href="#account/orders">Order history</a>
              <a href="#account/ledger">Coin ledger</a>
              <a href="#support">Payment help</a>
            </div>
            <span>
              <Icon name="lock" size={14} /> Secure checkout by Stripe
            </span>
          </footer>
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
export function CheckoutReturn({
  result,
  onDismiss,
}: {
  result: string;
  onDismiss?: () => void;
}) {
  const { refresh, user, status, openLogin } = useAuth();
  const [state, setState] = useState<
      "pending" | "confirmed" | "cancelled" | "failed" | "signin" | "unknown"
    >("pending"),
    [message, setMessage] = useState("Checking your order…"),
    [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (result !== "success") {
      setState("cancelled");
      setMessage(
        "Checkout was cancelled. Check your orders if you completed a payment.",
      );
      return;
    }
    if (status === "loading") {
      setState("pending");
      setMessage("Restoring your account before checking payment…");
      return;
    }
    if (status !== "user") {
      setState("signin");
      setMessage("Sign in to check your payment and updated balance.");
      return;
    }
    const orderNumber = sessionStorage.getItem("jjtv_checkout_order");
    if (!orderNumber) {
      setState("unknown");
      setMessage("Open your order history to check payment confirmation.");
      return;
    }
    let active = true,
      timer = 0,
      tries = 0;
    setState("pending");
    setMessage("Waiting for payment confirmation…");
    const check = async () => {
      try {
        const orders = await get<{ items: Order[] }>(
          "/v1/commerce/orders?limit=100",
        );
        if (!active) return;
        const order = orders.items.find((o) => o.order_no === orderNumber);
        if (order?.status === "paid") {
          await refresh();
          if (!active) return;
          setState("confirmed");
          setMessage("Payment confirmed. Your account has been updated.");
          return;
        }
        if (order && ["failed", "refunded"].includes(order.status)) {
          setState("failed");
          setMessage(
            order.status === "refunded"
              ? "This order has been refunded. See your order history for details."
              : "Payment was not completed. You can try again from Price.",
          );
          return;
        }
        if (tries++ < 5) timer = window.setTimeout(() => void check(), 2000);
        else {
          setState("unknown");
          setMessage(
            "Your payment is still being confirmed. Check your orders or retry shortly.",
          );
        }
      } catch (e) {
        if (active) {
          setState("unknown");
          setMessage(errorMessage(e));
        }
      }
    };
    void check();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [result, status, user?.id, attempt, refresh]);
  return (
    <div className={"checkout-notice checkout-" + state} role="status">
      <Icon name={state === "confirmed" ? "check" : "coin"} />
      <span>
        {message}
        {state === "confirmed" && user
          ? " Wallet: " + user.coins.toLocaleString() + " coins."
          : ""}
      </span>
      <div className="checkout-actions">
        {state === "signin" ? (
          <button className="text-button" onClick={openLogin}>
            Sign in
          </button>
        ) : state === "unknown" ? (
          <button
            className="text-button"
            onClick={() => setAttempt((a) => a + 1)}
          >
            Check again
          </button>
        ) : null}
        <a href="#account/orders">View orders</a>
        {onDismiss && (
          <button
            className="icon-button"
            aria-label="Dismiss payment notice"
            onClick={onDismiss}
          >
            <Icon name="close" size={16} />
          </button>
        )}
      </div>
    </div>
  );
}

import { planPriceDisplay } from "./plan-display";
import { useLocale } from "./locale";
import { setDesktopCheckoutActive } from "./surface";
import { useEffect, useRef, useState } from "react";
import {
  get,
  post,
  errorMessage,
  ApiError,
  authToken,
  type Pack,
  type Plan,
  type Order,
} from "./consumer-api";
import { useAuth } from "./auth";
import { Failure, Icon, Loading, Modal } from "./components";
type CheckoutConflictCode =
  | "pending_subscription_checkout"
  | "checkout_payment_pending"
  | "checkout_in_progress"
  | "checkout_expired"
  | "checkout_recovery_required"
  | "checkout_already_paid";
interface CheckoutConflict {
  code: CheckoutConflictCode;
  message: string;
  order_no: string;
  checkout_url?: string;
  action: string;
}
interface PurchaseIntent {
  product: "membership" | "coin_pack";
  code: string;
  account: string;
  key: string;
  mapKey: string;
  returnHash: string;
  sourceDramaId?: number;
  ownerToken: string | null;
}
interface RecoveryState {
  conflict: CheckoutConflict;
  intent: PurchaseIntent;
}
const conflictTitles: Record<CheckoutConflictCode, string> = {
  pending_subscription_checkout: "A membership checkout is already open",
  checkout_payment_pending: "Payment is being confirmed",
  checkout_in_progress: "Checkout is being prepared",
  checkout_expired: "This checkout has expired",
  checkout_recovery_required: "This checkout needs support",
  checkout_already_paid: "This order has already been paid",
};
function checkoutConflict(error: unknown): CheckoutConflict | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  const detail = (error.data as { detail?: unknown } | null)?.detail;
  if (!detail || typeof detail !== "object") return null;
  const value = detail as Partial<CheckoutConflict>;
  if (
    !value.code ||
    !Object.prototype.hasOwnProperty.call(conflictTitles, value.code) ||
    typeof value.message !== "string"
  )
    return null;
  return {
    code: value.code,
    message: value.message,
    order_no: typeof value.order_no === "string" ? value.order_no : "",
    checkout_url:
      typeof value.checkout_url === "string" ? value.checkout_url : undefined,
    action: typeof value.action === "string" ? value.action : "",
  };
}
export function Store({
  kind = "membership",
  sourceDramaId,
  sourceEpisodeId,
}: {
  kind?: "membership" | "coin_pack";
  sourceDramaId?: number;
  sourceEpisodeId?: number;
}) {
  const { locale, t, number, date, money } = useLocale();
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
    [cancelOpen, setCancelOpen] = useState(false),
    [recovery, setRecovery] = useState<RecoveryState | null>(null),
    [purchaseNotice, setPurchaseNotice] = useState("");
  const keys = useRef(new Map<string, string>()),
    submitting = useRef(false),
    mounted = useRef(true);
  const viewerToken = authToken();
  const activeRecovery =
    recovery?.intent.ownerToken === viewerToken ? recovery : null;
  useEffect(() => {
    setDesktopCheckoutActive(!!busy || !!activeRecovery || cancelOpen);
    return () => setDesktopCheckoutActive(false);
  }, [busy, activeRecovery, cancelOpen]);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    keys.current.clear();
    submitting.current = false;
    setBusy("");
    setRecovery(null);
    setPurchaseNotice("");
  }, [viewerToken]);
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
        setError(errors.join("\n"));
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
  function newIntent(
    product: "membership" | "coin_pack",
    code: string,
  ): PurchaseIntent {
    const mapKey = product + ":" + code + ":" + account;
    let key = keys.current.get(mapKey);
    if (!key) {
      key = crypto.randomUUID();
      keys.current.set(mapKey, key);
    }
    return {
      product,
      code,
      account,
      key,
      mapKey,
      ownerToken: authToken(),
      returnHash: sourceDramaId
        ? "#watch/" +
          sourceDramaId +
          (sourceEpisodeId ? "?episode=" + sourceEpisodeId : "")
        : window.location.hash,
      sourceDramaId,
    };
  }
  const isCurrentIntent = (intent: PurchaseIntent) =>
    mounted.current && intent.ownerToken === authToken();
  function showConflict(error: unknown, intent: PurchaseIntent) {
    const conflict = checkoutConflict(error);
    if (conflict) {
      setRecovery({ conflict, intent });
      setError("");
    } else setError(errorMessage(error));
  }
  async function purchase(
    product: "membership" | "coin_pack",
    code: string,
    previous?: PurchaseIntent,
  ) {
    if (submitting.current) return;
    const intent = previous ?? newIntent(product, code);
    if (!isCurrentIntent(intent)) return;
    submitting.current = true;
    setBusy(product + ":" + code);
    setError("");
    setPurchaseNotice("");
    try {
      const result = await post<{ url: string; order_no: string }>(
        "/v1/commerce/checkout",
        {
          product_type: intent.product,
          code: intent.code,
          account_code: intent.account || undefined,
          idempotency_key: intent.key,
          success_url:
            window.location.origin + "/?checkout=success" + intent.returnHash,
          cancel_url:
            window.location.origin + "/?checkout=cancel" + intent.returnHash,
          attribution_pid:
            localStorage.getItem("jjtv_attribution_pid") || undefined,
          source_drama_id: intent.sourceDramaId,
        },
      );
      if (!isCurrentIntent(intent)) return;
      sessionStorage.setItem("jjtv_checkout_order", result.order_no);
      window.location.assign(result.url);
    } catch (error) {
      if (isCurrentIntent(intent)) showConflict(error, intent);
    } finally {
      if (isCurrentIntent(intent)) {
        submitting.current = false;
        setBusy("");
      }
    }
  }
  function retryCheckout(fresh = false) {
    if (!activeRecovery || submitting.current) return;
    let intent = activeRecovery.intent;
    if (fresh) {
      intent = { ...intent, key: crypto.randomUUID() };
      keys.current.set(intent.mapKey, intent.key);
      // A new attempt that loses its response must retry this new key, not mint another.
      setRecovery({
        intent,
        conflict: {
          code: "checkout_in_progress",
          action: "retry",
          order_no: "",
          message:
            "Opening your new checkout. If the connection fails, retry this same payment attempt.",
        },
      });
    }
    void purchase(intent.product, intent.code, intent);
  }
  function resumeCheckout() {
    if (
      !activeRecovery?.conflict.checkout_url ||
      !isCurrentIntent(activeRecovery.intent)
    )
      return;
    if (activeRecovery.conflict.order_no)
      sessionStorage.setItem(
        "jjtv_checkout_order",
        activeRecovery.conflict.order_no,
      );
    window.location.assign(activeRecovery.conflict.checkout_url);
  }
  async function cancelPendingCheckout() {
    if (!activeRecovery || submitting.current) return;
    const { intent, conflict } = activeRecovery;
    if (!conflict.order_no) return;
    submitting.current = true;
    setBusy("cancel-checkout");
    setError("");
    setPurchaseNotice("");
    try {
      const result = await post<{
        order_no: string;
        status: string;
        canceled: boolean;
      }>(
        "/v1/commerce/orders/" +
          encodeURIComponent(conflict.order_no) +
          "/cancel",
        undefined,
      );
      if (!isCurrentIntent(intent)) return;
      if (!result.canceled)
        throw new Error(
          "Cancellation is not confirmed. Check the order before retrying.",
        );
      keys.current.delete(intent.mapKey);
      setRecovery(null);
      setPurchaseNotice(
        "Pending checkout cancelled. You can choose your plan again.",
      );
    } catch (error) {
      if (isCurrentIntent(intent)) showConflict(error, intent);
    } finally {
      if (isCurrentIntent(intent)) {
        submitting.current = false;
        setBusy("");
      }
    }
  }
  async function checkPendingPayment() {
    if (!activeRecovery || submitting.current) return;
    const { intent, conflict } = activeRecovery;
    submitting.current = true;
    setBusy("check-payment");
    setError("");
    setPurchaseNotice("");
    try {
      const result = await get<{ items: Order[] }>(
        "/v1/commerce/orders?limit=100",
      );
      if (!isCurrentIntent(intent)) return;
      const order = result.items.find(
        (item) => item.order_no === conflict.order_no,
      );
      if (order?.status === "paid") {
        await refresh();
        if (!isCurrentIntent(intent)) return;
        keys.current.delete(intent.mapKey);
        setRecovery(null);
        setPurchaseNotice("Payment confirmed. This order has been paid.");
      } else
        setPurchaseNotice(
          "Payment is still being confirmed. Check your order history or try again shortly.",
        );
    } catch (error) {
      if (isCurrentIntent(intent)) setError(errorMessage(error));
    } finally {
      if (isCurrentIntent(intent)) {
        submitting.current = false;
        setBusy("");
      }
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
          <h1>{t("Price")}</h1>
          <p className="muted">
            {t("Membership plans and coin packs, all in one place.")}</p>
        </div>
        {Number.isFinite(minimum) && (
          <span className="price-from">
            {t("Premium from {price}",{price:money(minimum,firstCurrency)})}
          </span>
        )}
      </div>
      <div className="segmented price-tabs" aria-label={t("Store category")}>
        <button
          aria-pressed={kind === "membership"}
          onClick={() => selectTab("membership")}
        >
          {t("Premium")}</button>
        <button
          aria-pressed={kind === "coin_pack"}
          onClick={() => selectTab("coin_pack")}
        >
          {t("Coins")}</button>
      </div>
      {user?.membership?.active && (
        <div className="membership-banner">
          <Icon name="crown" />
          <div>
            <strong>{t("Your membership is active")}</strong>
            <p>
              {user.membership.until
                ? t("Access until {date}",{date:date(user.membership.until,{dateStyle:"medium"})})
                : ""}
              {user.membership.canceling ? " · " + t("Renewal cancelled") : ""}
            </p>
          </div>
          {!user.membership.canceling &&
            user.membership.billing_mode === "subscription" && (
              <button
                className="button secondary"
                onClick={() => setCancelOpen(true)}
              >
                {t("Cancel renewal")}</button>
            )}
        </div>
      )}
      {error && <Failure message={error.split("\n").map((line) => { const colon = line.indexOf(": "); return colon > 0 && ["Membership plans", "Coin packs", "Payment options"].includes(line.slice(0,colon)) ? t(line.slice(0,colon)) + ": " + t(line.slice(colon+2)) : t(line); }).join("\n")} retry={load} />}
      {activeRecovery && (
        <section className="checkout-recovery" role="alert">
          <h2>{t(conflictTitles[activeRecovery.conflict.code])}</h2>
          <p>{t(activeRecovery.conflict.message)}</p>
          {activeRecovery.conflict.order_no && (
            <p>{t("Order {number}",{number:activeRecovery.conflict.order_no})}</p>
          )}
          <div className="button-row">
            {activeRecovery.conflict.code ===
              "pending_subscription_checkout" && (
              <>
                {activeRecovery.conflict.checkout_url && (
                  <button
                    className="button primary"
                    disabled={!!busy}
                    onClick={resumeCheckout}
                  >
                    {t("Continue checkout")}</button>
                )}
                <button
                  className="button secondary"
                  disabled={!!busy || !activeRecovery.conflict.order_no}
                  onClick={() => void cancelPendingCheckout()}
                >
                  {busy === "cancel-checkout"
                    ? t("Cancelling…")
                    : t("Cancel pending checkout")}
                </button>
              </>
            )}
            {activeRecovery.conflict.code === "checkout_in_progress" && (
              <button
                className="button primary"
                disabled={!!busy}
                onClick={() => retryCheckout()}
              >
                {busy ? t("Retrying…") : t("Retry checkout")}
              </button>
            )}
            {activeRecovery.conflict.code === "checkout_expired" && (
              <button
                className="button primary"
                disabled={!!busy}
                onClick={() => retryCheckout(true)}
              >
                {t("Start new checkout")}</button>
            )}
            {activeRecovery.conflict.code === "checkout_payment_pending" && (
              <button
                className="button secondary"
                disabled={!!busy}
                onClick={() => void checkPendingPayment()}
              >
                {busy === "check-payment"
                  ? t("Checking…")
                  : t("Check payment status")}
              </button>
            )}
            <a className="button secondary" href="#account/orders">
              {t("View orders")}</a>
            <a
              className="text-button"
              href={
                "#support?new=1" +
                (activeRecovery.conflict.order_no
                  ? "&order=" +
                    encodeURIComponent(activeRecovery.conflict.order_no)
                  : "")
              }
            >
              {t("Contact support")}</a>
          </div>
        </section>
      )}
      {purchaseNotice && (
        <p className="purchase-notice" role="status">
          {t(purchaseNotice)}
        </p>
      )}
      {loading ? (
        <Loading />
      ) : (
        <>
          <section className="price-section" id="membership-plans">
            <div className="price-section-heading">
              <h2>{t("Membership plans")}</h2>
              <p className="muted">
                {mode === "subscription"
                  ? t("Choose your plan. Cancel renewal at any time.")
                  : t("One payment for your selected access period.")}
              </p>
            </div>
            <div className="membership-grid">
              {plans.map((plan) => {
                const display = planPriceDisplay(plan,plans,mode);
                return (
                <article
                  className={
                    "membership-card " +
                    (plan.benefits?.best ? "recommended" : "")
                  }
                  key={plan.code}
                >
                  {plan.benefits?.best && (
                    <span className="purchase-badge">{t("Most popular")}</span>
                  )}
                  <div className="plan-card-heading"><div><h3>{locale === "zh" ? plan.name_zh || plan.name_en : plan.name_en || plan.name_zh}</h3><span>{Number(plan.duration_days)>0?t("{days} days",{days:number(Number(plan.duration_days))}):(plan.billing_mode||mode)==='subscription'?t("Auto-renewing"):t("One-time pass")}</span></div>{display.discountPercent != null && <span className="plan-discount-badge">{t("Save {percent}% vs monthly",{percent:number(display.discountPercent)})}</span>}</div>
                  <div className="plan-price-row"><strong>{money(display.priceCents, plan.currency)}</strong><span>{display.subscription ? "/ "+t(display.interval || "billing period") : t("/ pass")}</span>{display.monthlyComparisonCents != null && <s className="plan-original-price">{money(display.monthlyComparisonCents,plan.currency)} / {t("month")}</s>}</div>
                  <p className="plan-billing-note">{display.annualChargeCents != null ? t("Billed {price} / year",{price:money(display.annualChargeCents,plan.currency)}) : (plan.billing_mode||mode)==='subscription'?t(plan.recurring_interval === "week" ? "Billed every week" : plan.recurring_interval === "month" ? "Billed every month" : plan.recurring_interval === "year" ? "Billed every year" : "Billed every period"):t("One payment · no automatic renewal")}</p>
                  {Number(plan.base_coins) + Number(plan.bonus_coins) > 0 && (
                    <div className="plan-coin-strip">
                      <strong>
                        {t("+{count} coins included",{count:number(Number(plan.base_coins||0)+Number(plan.bonus_coins||0))})}
                      </strong>
                      <span>
                        {t("{base} base + {bonus} bonus",{base:number(plan.base_coins||0),bonus:number(plan.bonus_coins||0)})}
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
                    className={"button secondary full-width" + (plan.benefits?.best ? " plan-gold-button" : "")}
                    disabled={!!busy || !!activeRecovery || !channels.length}
                    onClick={() =>
                      requireAuth(() => {
                        void purchase("membership", plan.code);
                      })
                    }
                  >
                    {busy === "membership:" + plan.code
                      ? t("Opening checkout…")
                      : (plan.billing_mode || mode) === "subscription"
                        ? t("Subscribe")
                        : t("Buy pass")}
                  </button>
                </article>
              );})}
            </div>
            {!plans.length && (
              <p className="empty compact">
                {t("No membership plans are available right now.")}</p>
            )}
            <p className="section-footnote">
              {t("Episode access and any coin charges follow the rules shown on each drama.")}</p>
          </section>
          <section className="price-section" id="coin-packs">
            <div className="price-section-heading">
              <h2>{t("Coin packs")}</h2>
              <p className="muted">
                {t("Choose your top-up. Episode prices are shown before you unlock.")}</p>
            </div>
            <div className="coin-pack-grid">
              {packs.map((pack) => (
                <article
                  className={"coin-pack-card " + (["best", "best_value", "best value"].includes((pack.badge || "").trim().toLowerCase()) ? "featured" : (pack.badge || "").trim().toLowerCase() === "popular" ? "popular" : "")}
                  key={pack.code}
                >
                  {pack.badge && (
                    <span className="purchase-badge">{pack.badge}</span>
                  )}
                  <h3 className="sr-only">{locale === "zh" ? pack.name_zh || pack.name_en : pack.name_en || pack.name_zh}</h3>
                  <div className="coin-pack-total">
                    <strong>{number(pack.coins)}</strong>
                    <span>{t("coins")}</span>
                    {pack.bonus_coins > 0 && (
                      <span className="coin-bonus">
                        {t("+{count} bonus",{count:number(pack.bonus_coins)})}
                      </span>
                    )}
                  </div>
                  <p className="coin-breakdown">
                    {t("{base} base + {bonus} bonus coins",{base:number(pack.base_coins),bonus:number(pack.bonus_coins)})}
                  </p>
                  <p className="coin-unit-price">
                    {pack.coins > 0
                      ? t("{price} per 100 coins",{price:money((pack.price_cents/pack.coins)*100,pack.currency)})
                      : ""}
                  </p>
                  <div className="coin-pack-footer">
                    <strong>{money(pack.price_cents, pack.currency)}</strong>
                    <button
                      className="button secondary"
                      disabled={!!busy || !!activeRecovery || !channels.length}
                      onClick={() =>
                        requireAuth(() => {
                          void purchase("coin_pack", pack.code);
                        })
                      }
                    >
                      {busy === "coin_pack:" + pack.code
                        ? t("Opening…")
                        : t("Buy coins")}
                    </button>
                  </div>
                </article>
              ))}
            </div>
            {!packs.length && (
              <p className="empty compact">
                {t("No coin packs are available right now.")}</p>
            )}
            <a className="reward-callout" href="#rewards?return=price">
              <span>
                <strong>{t("No budget today?")}</strong> {t("Check in and finish daily tasks to earn coins.")}
              </span>
              <span>{t("Go to Rewards →")}</span>
            </a>
          </section>
          {channels.length > 1 && (
            <label className="payment-channel">
              {t("Payment option")}<select
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
                {t("Payments are currently unavailable. You can still earn coins through daily tasks.")}</span>
            </div>
          )}
          <footer className="price-footer">
            <div>
              <a href="#account/orders">{t("Order history")}</a>
              <a href="#account/ledger">{t("Coin ledger")}</a>
              <a href="#support">{t("Payment help")}</a>
            </div>
            <span>
              <Icon name="lock" size={14} /> {t("Secure checkout by Stripe")}</span>
          </footer>
        </>
      )}
      {cancelOpen && (
        <Modal
          title={t("Cancel membership renewal?")}
          onClose={() => setCancelOpen(false)}
        >
          <p>
            {t("Your membership benefits remain available until the end of your paid period.")}</p>
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
            {t("Confirm cancellation")}</button>
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
  const { t, number } = useLocale();
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
        {t(message)}
        {state === "confirmed" && user
          ? " " + t("Wallet: {count} coins.",{count:number(user.coins)})
          : ""}
      </span>
      <div className="checkout-actions">
        {state === "signin" ? (
          <button className="text-button" onClick={openLogin}>
            {t("Sign in")}</button>
        ) : state === "unknown" ? (
          <button
            className="text-button"
            onClick={() => setAttempt((a) => a + 1)}
          >
            {t("Check again")}</button>
        ) : null}
        <a href="#account/orders">{t("View orders")}</a>
        {onDismiss && (
          <button
            className="icon-button"
            aria-label={t("Dismiss payment notice")}
            onClick={onDismiss}
          >
            <Icon name="close" size={16} />
          </button>
        )}
      </div>
    </div>
  );
}

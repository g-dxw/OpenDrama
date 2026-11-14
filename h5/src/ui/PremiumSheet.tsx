import { RightsNotice } from "./RightsNotice";
import type {ContentRights} from "../../../shared/consumer/types";
import { getSite, siteMoney } from "../../../shared/site";
import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  authToken,
  cancelSubscription,
  getPlans,
  startCheckout,
  membershipCheckoutIntent,
  replaceExpiredMembershipIntent,
  replacePaidMembershipIntent,
  matchesMembershipCheckoutIntent,
  getCheckoutOrder,
  type MeProfile,
  type Plan,
} from "../api";
import type { Locale } from "../copy";
import { t } from "../copy";
import { Sheet } from "./Sheet";
import { toast } from "./toast";
import { useAuth } from "../auth/AuthProvider";
import { setMobileCheckoutActive } from "../../../shared/consumer/surface";

type Membership = NonNullable<MeProfile["membership"]>;
type BillingMode = "subscription" | "one_time";
type TerminalCheckout = {
  ownerToken: string;
  code: string;
  intent: string;
  message: string;
  paidOrderNo?: string;
};

function formatDay(iso: string | null, locale: Locale): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(locale === "zh" ? "zh-CN" : "en-US", { timeZone:getSite().tenant.timezone,
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

/**
 * Subscription / pass upsell and account controls.
 *
 * The backend decides whether new purchases are auto-renewing or one-time
 * (global switch); this sheet only mirrors that. Prices come from /v1/plans.
 */
export function PremiumSheet({
  rights,
  locale,
  membership,
  onChanged,
  onClose,
}: {
  rights?:ContentRights;
  locale: Locale;
  membership?: Membership;
  onChanged: () => void;
  onClose: () => void;
}) {
  const { requireAuth, loginOpen, status } = useAuth();
  const [plans, setPlans] = useState<Plan[]>([]);
  const [mode, setMode] = useState<BillingMode>("one_time");
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [showPlans, setShowPlans] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [expired, setExpired] = useState<TerminalCheckout | null>(null);
  const expiredRef = useRef<TerminalCheckout | null>(null);
  const updateExpired = (value: TerminalCheckout | null) => {
    expiredRef.current = value;
    setExpired(value);
  };
  const mounted = useRef(false),
    generation = useRef(0),
    ownerToken = useRef(authToken()),
    pinOwner = useRef("");
  if (!pinOwner.current) pinOwner.current = "h5-premium-" + crypto.randomUUID();
  const awaitingAuth = useRef(false);
  const currentToken = authToken();
  const ownedExpired = expired?.ownerToken === currentToken ? expired : null;
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      generation.current++;
      expiredRef.current = null;
      awaitingAuth.current = false;
      setMobileCheckoutActive(pinOwner.current, false);
    };
  }, []);
  useEffect(() => {
    if (ownerToken.current !== currentToken) {
      ownerToken.current = currentToken;
      generation.current++;
      updateExpired(null);
      setError("");
      setBusy(null);
      if (!awaitingAuth.current)
        setMobileCheckoutActive(pinOwner.current, false);
    }
  }, [currentToken]);
  useEffect(() => {
    if (awaitingAuth.current && !loginOpen && status === "guest") {
      awaitingAuth.current = false;
      setMobileCheckoutActive(pinOwner.current, false);
    }
  }, [loginOpen, status]);
  const beginRequest = (kind: string) => {
    const token = authToken();
    if (!mounted.current || !token) {
      setMobileCheckoutActive(pinOwner.current, false);
      return null;
    }
    ownerToken.current = token;
    const id = ++generation.current;
    setBusy(kind);
    setError("");
    setMobileCheckoutActive(pinOwner.current, true);
    return { id, token };
  };
  const isCurrent = (request: { id: number; token: string }) =>
    mounted.current &&
    generation.current === request.id &&
    authToken() === request.token;
  const finishRequest = (
    request: { id: number; token: string },
    keepPinned = false,
  ) => {
    if (generation.current !== request.id) return;
    generation.current++;
    if (!keepPinned) setMobileCheckoutActive(pinOwner.current, false);
    if (mounted.current) setBusy(null);
  };

  const active = Boolean(membership?.active);
  const isPass = membership?.billing_mode === "one_time";
  const showActive = active && !showPlans;

  useEffect(() => {
    let alive = true;
    getPlans()
      .then((payload) => {
        if (!alive) return;
        setPlans(payload.items);
        setMode(payload.billing_mode ?? "subscription");
        setState("ready");
      })
      .catch(() => {
        if (alive) setState("error");
      });
    return () => {
      alive = false;
    };
  }, []);

  const subscribe = (code: string) => {
    if (!mounted.current || ownedExpired) return;
    awaitingAuth.current = status !== "user";
    setMobileCheckoutActive(pinOwner.current, true);
    requireAuth(() => {
      awaitingAuth.current = false;
      if (mounted.current) void startSubscription(code);
      else setMobileCheckoutActive(pinOwner.current, false);
    });
  };
  async function reconcileCheckout(
    request: { id: number; token: string },
    code: string,
    intent: string,
    orderNo: string,
  ): Promise<"paid" | "created" | "stale"> {
    const result = await getCheckoutOrder(orderNo);
    if (!isCurrent(request)) return "stale";
    if (
      !matchesMembershipCheckoutIntent(code, intent, request.token) ||
      result.order.order_no !== orderNo ||
      result.order.product_type !== "membership" ||
      result.order.product_code !== code
    )
      throw new Error("Checkout identity changed");
    if (
      (result.order.status === "paid" || result.order.status === "refunded") &&
      result.settled === true
    ) {
      updateExpired({
        ownerToken: request.token,
        code,
        intent,
        paidOrderNo: orderNo,
        message:
          result.order.status === "refunded"
            ? locale === "zh"
              ? "这笔订单已全额退款。请先查看订单，再决定是否开始新的购买。"
              : "This order has been fully refunded. Review your orders before starting another purchase."
            : locale === "zh"
              ? "这笔订单已付款。请先查看订单，再决定是否开始新的购买。"
              : "This order has already been paid. Review your orders before starting another purchase.",
      });
      finishRequest(request, true);
      return "paid";
    }
    if (result.order.status !== "created")
      throw new Error("Payment needs confirmation");
    return "created";
  }
  async function startSubscription(code: string) {
    const request = beginRequest(code);
    if (!request) return;
    let intent = "";
    try {
      intent = membershipCheckoutIntent(code);
      const result = await startCheckout(code, intent);
      if (!isCurrent(request)) {
        finishRequest(request);
        return;
      }
      const reconciled = await reconcileCheckout(
        request,
        code,
        intent,
        result.order_no,
      );
      if (reconciled === "paid") return;
      if (reconciled === "stale") {
        finishRequest(request);
        return;
      }
      // Recheck at the side-effect boundary after the nested async reconciliation.
      if (!isCurrent(request)) {
        finishRequest(request);
        return;
      }
      if (!matchesMembershipCheckoutIntent(code, intent, request.token))
        throw new Error("Checkout identity changed");
      sessionStorage.setItem("jjtv_h5_checkout_order", result.order_no);
      // Keep the owning mobile tree pinned until the checkout navigation unmounts it.
      window.location.assign(result.url);
    } catch (err) {
      if (!isCurrent(request)) {
        finishRequest(request);
        return;
      }
      const detail =
        err instanceof ApiError &&
        err.data &&
        typeof err.data === "object" &&
        "detail" in err.data
          ? err.data.detail
          : null;
      if (
        err instanceof ApiError &&
        err.status === 409 &&
        detail &&
        typeof detail === "object" &&
        "code" in detail &&
        detail.code === "checkout_expired" &&
        "action" in detail &&
        detail.action === "new_purchase"
      ) {
        updateExpired({
          ownerToken: request.token,
          code,
          intent,
          message: err.message,
        });
        finishRequest(request, true);
        return;
      }
      if (
        err instanceof ApiError &&
        err.status === 409 &&
        detail &&
        typeof detail === "object" &&
        "code" in detail &&
        detail.code === "checkout_already_paid" &&
        "action" in detail &&
        detail.action === "view_orders" &&
        "order_no" in detail &&
        typeof detail.order_no === "string" &&
        detail.order_no
      ) {
        try {
          const reconciled = await reconcileCheckout(
            request,
            code,
            intent,
            detail.order_no,
          );
          if (reconciled === "paid") return;
        } catch {
          /* Unknown or unconfirmed orders retain the same intent. */
        }
        if (!isCurrent(request)) {
          finishRequest(request);
          return;
        }
      }
      finishRequest(request);
      if (err instanceof ApiError && err.status === 401)
        setError(t(locale, "premium_need_sign_in"));
      else if (err instanceof ApiError && err.message.includes("0.50"))
        setError(t(locale, "premium_min_price"));
      else setError(t(locale, "premium_failed"));
    }
  }
  const startNewCheckout = () => {
    const recovery = expiredRef.current;
    if (!mounted.current || !recovery || busy !== null) return;
    try {
      if (authToken() !== recovery.ownerToken) {
        updateExpired(null);
        setMobileCheckoutActive(pinOwner.current, false);
        return;
      }
      if (
        !(
          recovery.paidOrderNo
            ? replacePaidMembershipIntent
            : replaceExpiredMembershipIntent
        )(recovery.code, recovery.intent, recovery.ownerToken)
      ) {
        updateExpired(null);
        setMobileCheckoutActive(pinOwner.current, false);
        setError(
          locale === "zh"
            ? "支付意图已改变，请重新打开会员方案。"
            : "This payment attempt changed. Reopen your membership plans.",
        );
        return;
      }
      updateExpired(null);
      void startSubscription(recovery.code);
    } catch {
      setError(t(locale, "premium_failed"));
    }
  };
  async function stopRenewing() {
    const request = beginRequest("cancel");
    if (!request) return;
    try {
      await cancelSubscription();
      if (!isCurrent(request)) {
        finishRequest(request);
        return;
      }
      onChanged();
      toast(t(locale, "premium_cancel_done"));
      onClose();
    } catch {
      if (!isCurrent(request)) {
        finishRequest(request);
        return;
      }
      finishRequest(request);
      setError(t(locale, "premium_cancel_failed"));
    }
  }

  const unitOf = (plan: Plan) =>
    mode === "one_time"
      ? t(locale, `premium_span_${plan.code}`)
      : t(locale, `premium_unit_${plan.code}`);

  return (
    <Sheet
      title={
        showActive
          ? isPass
            ? t(locale, "premium_title_pass")
            : t(locale, "premium_title_active")
          : t(locale, "premium_title")
      }
      onClose={onClose}
      testId="premium-sheet"
    ><RightsNotice rights={rights} locale={locale}/>
      {showActive && membership ? (
        <>
          <div
            className={
              membership.canceling
                ? "premium-status canceling"
                : "premium-status"
            }
          >
            <span className="premium-status-crown">★</span>
            <div className="premium-status-main">
              <b>
                {t(locale, `premium_plan_${membership.plan_code ?? "monthly"}`)}
              </b>
              <i>
                {membership.canceling
                  ? t(locale, "premium_ends_on", {
                      date: formatDay(membership.until, locale),
                    })
                  : t(locale, "premium_active_until", {
                      date: formatDay(membership.until, locale),
                    })}
              </i>
            </div>
            <span className="premium-status-tag">
              {isPass
                ? t(locale, "premium_pass_badge")
                : t(locale, "premium_badge_active")}
            </span>
          </div>
          <ul className="premium-benefits status">
            <li>{t(locale, "premium_benefit_all")}</li>
            <li>{t(locale, "premium_benefit_noads")}</li>
            <li>{t(locale, "premium_benefit_hd")}</li>
          </ul>
          {mode === "one_time" && !isPass && (
            <button
              type="button"
              className="premium-secondary"
              onClick={() => setShowPlans(true)}
              data-testid="premium-buy-pass"
            >
              {t(locale, "premium_buy_pass_cta")}
            </button>
          )}
          {isPass ? (
            <>
              <button
                type="button"
                className="premium-cta"
                onClick={() => setShowPlans(true)}
                data-testid="premium-extend"
              >
                {t(locale, "premium_extend_cta")}
              </button>
              <p className="premium-note">{t(locale, "premium_extend_note")}</p>
            </>
          ) : membership.canceling ? (
            <p className="premium-note">{t(locale, "premium_canceled_note")}</p>
          ) : (
            <>
              <button
                type="button"
                className="premium-cancel"
                onClick={stopRenewing}
                disabled={busy !== null}
                data-testid="premium-cancel"
              >
                {busy === "cancel"
                  ? t(locale, "premium_canceling")
                  : t(locale, "premium_cancel_cta")}
              </button>
              <p className="premium-note">{t(locale, "premium_cancel_hint")}</p>
            </>
          )}
        </>
      ) : (
        <>
          <p className="premium-lede">
            {mode === "one_time"
              ? t(locale, "premium_lede_onetime")
              : t(locale, "premium_lede")}
          </p>
          {state === "loading" && <div className="me-sheet-loading">…</div>}
          {state === "error" && (
            <div className="me-empty small">
              <p>{t(locale, "err_generic")}</p>
            </div>
          )}
          <div className="premium-plans">
            {plans.map((plan) => (
              <div
                key={plan.code}
                className={
                  plan.benefits?.best ? "premium-plan best" : "premium-plan"
                }
              >
                {plan.benefits?.best && (
                  <span className="premium-badge">
                    {t(locale, "premium_popular")}
                  </span>
                )}
                <div className="premium-plan-head">
                  <b>
                    {locale === "zh"
                      ? plan.name_zh || plan.name_en
                      : plan.name_en}
                  </b>
                  <span className="premium-price">
                    {siteMoney(plan.price_cents,locale,(plan as {currency?:string}).currency||getSite().tenant.currency)}
                    <i>/{unitOf(plan)}</i>
                  </span>
                </div>
                <ul className="premium-benefits">
                  {(plan.benefits?.items ?? []).map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
                <button
                  type="button"
                  className="premium-cta"
                  disabled={busy !== null || !!ownedExpired}
                  onClick={() => subscribe(plan.code)}
                  data-testid={`premium-buy-${plan.code}`}
                >
                  {busy === plan.code
                    ? t(locale, "premium_redirecting")
                    : mode === "one_time"
                      ? t(locale, "premium_buy_cta")
                      : t(locale, "premium_cta")}
                </button>
              </div>
            ))}
          </div>
          {ownedExpired && (
            <div
              className="premium-error"
              role="alert"
              data-testid={
                ownedExpired.paidOrderNo
                  ? "premium-paid-checkout"
                  : "premium-expired-checkout"
              }
            >
              <p>{ownedExpired.message}</p>
              {ownedExpired.paidOrderNo && (
                <button
                  className="premium-secondary"
                  data-testid="premium-paid-view-orders"
                  onClick={() => {
                    onClose();
                    window.location.hash = "account/orders";
                  }}
                >
                  {locale === "zh" ? "查看订单" : "View orders"}
                </button>
              )}
              <button
                className="premium-cta"
                data-testid={
                  ownedExpired.paidOrderNo
                    ? "premium-paid-start-new-checkout"
                    : "premium-start-new-checkout"
                }
                onClick={startNewCheckout}
                disabled={busy !== null}
              >
                {ownedExpired.paidOrderNo
                  ? locale === "zh"
                    ? "开始新的购买"
                    : "Start a new purchase"
                  : locale === "zh"
                    ? "重新发起支付"
                    : "Start new checkout"}
              </button>
            </div>
          )}
          {error && <p className="premium-error">{error}</p>}
          <p className="premium-note">
            {mode === "one_time"
              ? t(locale, "premium_note_onetime")
              : t(locale, "premium_note")}
          </p>
        </>
      )}
    </Sheet>
  );
}

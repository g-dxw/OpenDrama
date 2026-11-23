"""Plan catalog, Stripe Checkout, and signed payment gateway webhooks.

Stripe owns the money: the client opens a Checkout Session, and everything
that changes entitlement comes back through a signed webhook. The local row
is the source of truth for "is this user a subscriber right now", so the
webhook has to be idempotent — every handler re-applies a full snapshot
instead of incrementing anything.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.modules.billing.models import BillingSettings, Order, Plan, PlanVersion, Subscription
from app.modules.common.models import AuditLog
from app.modules.content.models import Drama
from app.modules.tenancy.consumer_scope import current_site, require_owned, site_context

_stripe_credentials = ContextVar('opendrama_stripe_credentials', default=None)

@contextmanager
def managed_stripe_runtime(session, code='env'):
    from app.modules.billing.commerce import account_config
    token=_stripe_credentials.set(account_config(session,code))
    try:
        yield
    finally:
        _stripe_credentials.reset(token)

DEFAULT_PLANS = [
    ("weekly", "Weekly", "周卡", 999, False, ["Unlock all", "No ads", "1080p"]),
    ("monthly", "Monthly", "月卡", 1999, True, ["Weekly benefits", "Download"]),
    ("yearly", "Yearly", "年卡", 7999, False, ["Monthly benefits", "Priority support", "Save 58%"]),
]


class BillingError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def pay_secret() -> bytes:
    return os.environ.get("PAY_WEBHOOK_SECRET", "local-pay-webhook").encode()


def sign_webhook(body: bytes) -> str:
    return hmac.new(pay_secret(), body, hashlib.sha256).hexdigest()


def ensure_default_plans(session: Session) -> None:
    if session.scalar(select(Plan).limit(1)):
        return
    for code, en, zh, cents, best, benefits in DEFAULT_PLANS:
        plan = Plan(code=code, name_en=en, name_zh=zh)
        session.add(plan)
        session.flush()
        session.add(
            PlanVersion(
                plan_id=plan.id,
                version=1,
                price_cents=cents,
                benefits={"items": benefits, "best": best},
            )
        )
    session.commit()


def list_current_plans(session: Session) -> list[dict]:
    plans = session.scalars(select(Plan).where(Plan.status == "active").order_by(Plan.id)).all()
    mode = billing_mode(session)
    out = []
    for plan in plans:
        version = session.scalar(
            select(PlanVersion).where(PlanVersion.plan_id == plan.id).order_by(PlanVersion.version.desc())
        )
        if version is None:
            continue
        out.append(
            {
                "code": plan.code,
                "name_en": plan.name_en,
                "name_zh": plan.name_zh,
                "price_cents": version.price_cents,
                "currency": version.currency,
                "version": version.version,
                "version_id": version.id,
                "benefits": version.benefits,
                "base_coins": version.base_coins,
                "bonus_coins": version.bonus_coins,
                "duration_days": PLAN_DAYS.get(plan.code, 30) if mode == "one_time" else None,
                "recurring_interval": INTERVAL_OF.get(plan.code, "month") if mode == "subscription" else None,
                "billing_mode": mode,
            }
        )
    return out


BILLING_MODES = ("subscription", "one_time")


def new_billing_settings(session,tenant_id=None):
    from sqlalchemy import func,text
    site=tenant_id or current_site(session)
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext('site-billing-settings'))"))
    # Another first reader/provisioner may have created it while we waited.
    # Keep the legacy explicit-ID allocator and the caller's transaction intact.
    existing=session.scalar(select(BillingSettings).where(BillingSettings.tenant_id==site).execution_options(include_all_tenants=True,populate_existing=True))
    if existing is not None:return existing
    identifier=(session.scalar(select(func.max(BillingSettings.id)).execution_options(include_all_tenants=True)) or 0)+1
    return BillingSettings(id=identifier,tenant_id=site,billing_mode='one_time')

def get_billing_settings(session: Session) -> BillingSettings:
    row = session.scalar(select(BillingSettings).where(BillingSettings.tenant_id == current_site(session)))
    if row is None:
        row = new_billing_settings(session)
        session.add(row)
        session.flush()
    return row


def billing_mode(session: Session) -> str:
    """How *new* purchases are billed. Global, one switch for every plan."""
    return get_billing_settings(session).billing_mode


def set_billing_mode(session: Session, mode: str, actor_id: int) -> BillingSettings:
    if mode not in BILLING_MODES:
        raise BillingError(400, "unknown billing mode")
    row = get_billing_settings(session)
    row.billing_mode = mode
    row.updated_by = actor_id
    session.commit()
    return row


def provision_prices_for_mode(session: Session, mode: str) -> list[dict]:
    """Best effort: make sure every plan has a Stripe price of that type."""
    out = []
    plans = session.scalars(select(Plan).where(Plan.status == "active")).all()
    for plan in plans:
        version = _latest_version(session, plan)
        try:
            price_id = ensure_stripe_price(session, plan, version, mode)
            out.append({"code": plan.code, "price_id": price_id, "ok": True})
        except BillingError as exc:
            out.append({"code": plan.code, "ok": False, "error": exc.detail})
    return out


def _as_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def access_until(session: Session, user_id: int) -> datetime | None:
    """Latest moment this user is entitled: subscription period or pass end."""
    row = session.execute(
        select(Subscription).where(
            Subscription.user_id == user_id,
            Subscription.status == "active",
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    now = datetime.now(timezone.utc)
    candidates = [
        end
        for end in (_as_aware(row.current_period_end), _as_aware(row.pass_end))
        if end is not None and end > now
    ]
    return max(candidates) if candidates else None


def list_user_orders(session: Session, user_id: int, limit: int = 20, offset: int = 0) -> dict:
    """Purchase history for the H5 "Orders" sheet, newest first."""
    rows = session.execute(
        select(Order, Plan)
        .join(PlanVersion, PlanVersion.id == Order.plan_version_id)
        .join(Plan, Plan.id == PlanVersion.plan_id)
        # Abandoned checkouts are noise in a purchase history: only money that
        # actually moved (paid, or paid then refunded) belongs here.
        .where(Order.user_id == user_id, Order.status.in_(("paid", "refunded")))
        .order_by(Order.created_at.desc(), Order.id.desc())
        .offset(offset)
        .limit(limit + 1)
    ).all()
    has_more = len(rows) > limit
    items = []
    for order, plan in rows[:limit]:
        items.append(
            {
                "order_no": order.order_no,
                "plan_code": plan.code,
                "plan_name_en": plan.name_en,
                "plan_name_zh": plan.name_zh,
                "amount_cents": order.amount_cents,
                "currency": order.currency,
                "billing_mode": order.billing_mode,
                "status": order.status,
                "paid_at": order.paid_at.isoformat() if order.paid_at else None,
                "created_at": order.created_at.isoformat(),
            }
        )
    return {"items": items, "has_more": has_more}


def active_membership(session: Session, user_id: int) -> dict:
    """What /v1/me shows: whichever access window ends last."""
    row = session.execute(
        select(Subscription, Plan)
        .join(Plan, Plan.id == Subscription.plan_id)
        .where(
            Subscription.user_id == user_id,
            Subscription.status == "active",
        )
    ).first()
    if row is None:
        return {
            "active": False,
            "plan_code": None,
            "until": None,
            "canceling": False,
            "billing_mode": None,
            "subscription_until": None,
            "pass_until": None,
        }
    subscription, plan = row
    now = datetime.now(timezone.utc)
    sub_end = _as_aware(subscription.current_period_end)
    pass_end = _as_aware(subscription.pass_end)
    live = [end for end in (sub_end, pass_end) if end is not None and end > now]
    if not live:
        return {
            "active": False,
            "plan_code": None,
            "until": None,
            "canceling": False,
            "billing_mode": None,
            "subscription_until": sub_end.isoformat() if sub_end else None,
            "pass_until": pass_end.isoformat() if pass_end else None,
        }
    until = max(live)
    mode = "one_time" if pass_end == until and (sub_end is None or pass_end > sub_end) else "subscription"
    return {
        "active": True,
        "plan_code": plan.code,
        "until": until.isoformat(),
        # "Won't renew" only makes sense while a subscription is the live window.
        "canceling": mode == "subscription" and subscription.canceled_at is not None and sub_end is not None and sub_end > now,
        "billing_mode": mode,
        "subscription_until": sub_end.isoformat() if sub_end else None,
        "pass_until": pass_end.isoformat() if pass_end else None,
    }


def change_price(session: Session, *, code: str, price_cents: int, benefits: dict, actor_id: int) -> PlanVersion:
    plan = session.scalar(select(Plan).where(Plan.code == code))
    from app.modules.billing.site_merchants import site_currency,assert_site_currency
    currency=site_currency(session)
    ensure_chargeable(billing_mode(session), price_cents, currency)
    if plan is None:
        raise BillingError(404, "plan not found")
    latest = session.scalar(
        select(PlanVersion).where(PlanVersion.plan_id == plan.id).order_by(PlanVersion.version.desc())
    )
    version = PlanVersion(
        plan_id=plan.id,
        version=(latest.version if latest else 0) + 1,
        price_cents=price_cents,
        currency=currency,
        benefits=benefits,
    )
    session.add(version)
    session.flush()
    session.add(
        AuditLog(
            actor_type="admin",
            actor_id=actor_id,
            action="plan_price_change",
            target_type="plan",
            target_id=plan.id,
            payload={"version": version.version, "price_cents": price_cents},
        )
    )
    session.commit()
    # Best effort: materialize the Stripe price now so a bad key/amount shows up
    # here instead of at the first purchase. A failure is retried on checkout.
    # Provision the *current* billing mode (a one-time shop needs one-time prices).
    try:
        ensure_stripe_price(session, plan, version, billing_mode(session))
    except BillingError:
        session.rollback()
    return version


def cancel_subscription(session: Session, user_id: int) -> None:
    """Stop renewing. With Stripe the access runs until the paid period ends."""
    current = session.scalar(
        select(Subscription).where(Subscription.user_id == user_id, Subscription.status == "active")
    )
    if current is None:
        return
    if not current.stripe_subscription_id and current.billing_mode == "one_time":
        raise BillingError(400, "one-time passes cannot be canceled")
    if current.stripe_subscription_id:
        _stripe_request(
            f"/subscriptions/{current.stripe_subscription_id}",
            {"cancel_at_period_end": "true"},
            method="POST",
        )
        # Access continues to current_period_end; canceled_at only marks "will not renew".
        current.canceled_at = datetime.now(timezone.utc)
        session.commit()
        return
    current.status = "canceled"
    current.canceled_at = datetime.now(timezone.utc)
    session.commit()


# ------------------------------------------------- admin membership ops
#
# Support needs to hand out and take back membership without going through
# Stripe. Entitlement is read from exactly one place — an `active` row whose
# `max(current_period_end, pass_end)` is still in the future (see
# `access_until`) — so these helpers only move those two windows and never
# touch entitlements, orders or Stripe prices.

MAX_GRANT_DAYS = 3650
GRANT_MODES = ("extend", "restart")
REVOKE_MODES = ("now", "period_end")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def active_subscription(session: Session, user_id: int, *, for_update: bool = False) -> Subscription | None:
    stmt = select(Subscription).where(
        Subscription.user_id == user_id,
        Subscription.status == "active",
    )
    if for_update:
        stmt = stmt.with_for_update()
    return session.scalar(stmt)


def _window_base(row: Subscription) -> datetime:
    """Where a granted period starts when it is *stacked* on what is left."""
    candidates = [end for end in (_as_aware(row.pass_end), _as_aware(row.current_period_end)) if end]
    return max([_now()] + candidates)


def _is_recurring(row: Subscription) -> bool:
    return bool(row.stripe_subscription_id or row.creem_subscription_id or row.pancake_subscription_id or row.waffo_subscription_id)


def _describe(sub: Subscription, plan: Plan) -> dict:
    return {
        "id": sub.id,
        "plan_code": plan.code,
        "plan_name": plan.name_zh or plan.name_en,
        "status": sub.status,
        "start": sub.current_period_start.isoformat(),
        "end": sub.current_period_end.isoformat(),
        "pass_end": sub.pass_end.isoformat() if sub.pass_end else None,
        "canceled_at": sub.canceled_at.isoformat() if sub.canceled_at else None,
        "billing_mode": sub.billing_mode,
        "source": "subscription" if _is_recurring(sub) else "one_time",
    }


def membership_detail(session: Session, user_id: int) -> dict:
    """What the admin modal shows: identical numbers to `/v1/me.membership`."""
    membership = active_membership(session, user_id)
    row = active_subscription(session, user_id)
    plan = session.get(Plan, row.plan_id) if row is not None else session.scalar(select(Plan).where(Plan.code==membership.get("plan_code")))
    recurring = row is not None and _is_recurring(row)
    membership["source"] = ("one_time" if membership.get("active") else None) if row is None else ("subscription" if recurring else "one_time")
    membership["plan_name"] = (plan.name_zh or plan.name_en) if plan is not None else None
    if membership.get("plan_code") is None and plan is not None:
        # Expired window: still useful for support to see the last plan bought.
        membership["plan_code"] = plan.code
    membership["has_stripe_subscription"] = bool(row is not None and row.stripe_subscription_id)
    membership["has_renewing_subscription"] = recurring

    rows = session.execute(
        select(Subscription, Plan)
        .join(Plan, Plan.id == Subscription.plan_id)
        .where(Subscription.user_id == user_id)
        .order_by(Subscription.id.desc())
        .limit(6)
    ).all()
    membership["history"] = [_describe(sub, plan_row) for sub, plan_row in rows]

    last = session.scalar(
        select(AuditLog)
        .where(
            AuditLog.target_type == "user",
            AuditLog.target_id == user_id,
            AuditLog.action.in_(("membership_grant", "membership_revoke")),
        )
        .order_by(AuditLog.id.desc())
    )
    membership["last_adjust"] = (
        None
        if last is None
        else {
            "action": last.action,
            "actor_id": last.actor_id,
            "at": last.created_at.isoformat(),
            "payload": last.payload,
        }
    )
    return membership


def grant_membership(
    session: Session,
    *,
    user_id: int,
    plan_code: str,
    days: int | None,
    mode: str,
    reason: str,
    actor_id: int,
) -> dict:
    """Give a user time, the same way a purchased pass would be stacked."""
    if mode not in GRANT_MODES:
        raise BillingError(422, "unknown grant mode")
    plan = session.scalar(select(Plan).where(Plan.code == plan_code, Plan.status == "active"))
    if plan is None:
        raise BillingError(404, "plan not found")
    version = _latest_version(session, plan)
    length = PLAN_DAYS.get(plan_code, 30) if days is None else int(days)
    if length < 1 or length > MAX_GRANT_DAYS:
        raise BillingError(422, f"天数需在 1–{MAX_GRANT_DAYS} 之间")
    reason = (reason or "").strip()
    if not reason:
        raise BillingError(422, "请填写调整原因")

    # Checkout settlement and refunds use the wallet as the per-user entitlement
    # mutex. Read source facts only after acquiring the same lock.
    from app.modules.billing.commerce import wallet_lock
    from app.modules.billing.commerce_models import MembershipGrant

    wallet_lock(session, user_id)
    before_until = access_until(session, user_id)
    row = active_subscription(session, user_id, for_update=True)
    if mode == "restart" and row is not None and _is_recurring(row):
        # A local grant cannot rewrite a provider-managed billing period.
        raise BillingError(409, "该用户有自动续费订阅，请改用「顺延」")
    if mode == "restart":
        protected_source = session.scalar(
            select(MembershipGrant.id)
            .where(
                MembershipGrant.user_id == user_id,
                or_(
                    MembershipGrant.status.in_(("refund_pending", "pending_refund")),
                    (MembershipGrant.status == "active") & (MembershipGrant.ends_at > _now()),
                ),
            )
            .limit(1)
            .with_for_update()
        )
        if protected_source is not None:
            # Rewriting a legacy pass does not overwrite paid/frozen order grants.
            # Preserve those source facts instead of auditing a fictitious end date.
            raise BillingError(
                409,
                "该用户存在未到期的订单会员权益或退款冻结权益，不能「从今天重算」，请改用「顺延」",
            )

    if row is None:
        row = Subscription(
            user_id=user_id,
            plan_id=plan.id,
            plan_version_id=version.id,
            status="active",
            current_period_start=_now(),
            current_period_end=_now(),
            billing_mode="one_time",
        )
        session.add(row)
        session.flush()

    base = max(_window_base(row), before_until or _now()) if mode == "extend" else _now()
    row.pass_end = base + timedelta(days=length)
    if not _is_recurring(row):
        # Provider-owned rows keep the plan associated with their paid order.
        row.plan_id = plan.id
        row.plan_version_id = version.id

    session.add(
        AuditLog(
            actor_type="admin",
            actor_id=actor_id,
            action="membership_grant",
            created_at=_now(),
            target_type="user",
            target_id=user_id,
            payload={
                "plan_code": plan_code,
                "days": length,
                "mode": mode,
                "reason": reason,
                "before_until": before_until.isoformat() if before_until else None,
                "after_until": row.pass_end.isoformat(),
            },
        )
    )
    session.commit()
    return {
        "before_until": before_until.isoformat() if before_until else None,
        "after_until": row.pass_end.isoformat(),
        "added_days": length,
        "plan_code": plan_code,
        "mode": mode,
    }


def revoke_membership(
    session: Session,
    *,
    user_id: int,
    mode: str,
    reason: str,
    actor_id: int,
) -> dict:
    """Take membership back. Stripe is always told *before* the local row moves."""
    if mode not in REVOKE_MODES:
        raise BillingError(422, "unknown revoke mode")
    reason = (reason or "").strip()
    if not reason:
        raise BillingError(422, "请填写取消原因")

    row = active_subscription(session, user_id, for_update=True)
    now = _now()
    before_until = access_until(session, user_id)
    if row is None or before_until is None:
        raise BillingError(409, "该用户当前没有有效会员")

    stripe_id = row.stripe_subscription_id
    if mode == "period_end":
        if not stripe_id:
            raise BillingError(409, "时长包没有续费概念，请选择「立即收回」")
        # Access continues to current_period_end; canceled_at only marks
        # "will not renew". A failure here raises 502 and leaves the row alone.
        _stripe_request(f"/subscriptions/{stripe_id}", {"cancel_at_period_end": "true"}, method="POST")
        row.canceled_at = now
        session.add(
            AuditLog(
                actor_type="admin",
                actor_id=actor_id,
                action="membership_revoke",
                created_at=_now(),
                target_type="user",
                target_id=user_id,
                payload={
                    "mode": mode,
                    "reason": reason,
                    "before_until": before_until.isoformat(),
                    "after_until": before_until.isoformat(),
                    "stripe_canceled": True,
                },
            )
        )
        session.commit()
        return {
            "mode": mode,
            "before_until": before_until.isoformat(),
            "after_until": before_until.isoformat(),
            "stripe_canceled": True,
        }

    if stripe_id:
        # Immediate stop: no proration refund, the money side stays manual.
        _stripe_request(f"/subscriptions/{stripe_id}", method="DELETE")
    row.pass_end = now
    row.current_period_end = now
    row.canceled_at = now
    row.status = "canceled"
    session.add(
        AuditLog(
            actor_type="admin",
            actor_id=actor_id,
            action="membership_revoke",
            created_at=_now(),
            target_type="user",
            target_id=user_id,
            payload={
                "mode": mode,
                "reason": reason,
                "before_until": before_until.isoformat(),
                "after_until": now.isoformat(),
                "stripe_canceled": bool(stripe_id),
            },
        )
    )
    session.commit()
    return {
        "mode": mode,
        "before_until": before_until.isoformat(),
        "after_until": now.isoformat(),
        "stripe_canceled": bool(stripe_id),
    }


# --------------------------------------------------------------- stripe


STRIPE_API = "https://api.stripe.com/v1"
STRIPE_WEBHOOK_TOLERANCE = 300
PRICE_ENV = {
    "weekly": "STRIPE_PRICE_WEEKLY",
    "monthly": "STRIPE_PRICE_MONTHLY",
    "yearly": "STRIPE_PRICE_YEARLY",
}
PRODUCT_ENV = {
    "weekly": "STRIPE_PRODUCT_WEEKLY",
    "monthly": "STRIPE_PRODUCT_MONTHLY",
    "yearly": "STRIPE_PRODUCT_YEARLY",
}
INTERVAL_OF = {"weekly": "week", "monthly": "month", "yearly": "year"}
# Stripe refuses one-time Checkout sessions below 50 cents (USD). Subscriptions
# are exempt, which is why the $0.10 test price only worked in subscription mode.
MIN_ONE_TIME_CENTS = 50
PLAN_BY_PRICE: dict[str, str] = {}


def stripe_secret() -> str:
    credentials=_stripe_credentials.get()
    key = (credentials.get("secret_key", "") if credentials is not None else os.environ.get("STRIPE_SECRET_KEY", "")).strip()
    if not key:
        raise BillingError(503, "stripe is not configured")
    return key


def stripe_mode() -> str:
    return "live" if stripe_secret().startswith("sk_live_") else "test"


def price_id_for(code: str) -> str:
    env_key = PRICE_ENV.get(code)
    if env_key is None:
        raise BillingError(404, "plan not found")
    price = os.environ.get(env_key, "").strip()
    if not price:
        raise BillingError(503, f"stripe price for {code} is not configured")
    return price


def plan_code_for_price(price_id: str | None, fallback: str | None = None) -> str | None:
    if fallback:
        return fallback
    if not price_id:
        return None
    for code, env_key in PRICE_ENV.items():
        if os.environ.get(env_key, "").strip() == price_id:
            return code
    return None


def _stripe_request(path: str, data: dict | None = None, method: str = "POST") -> dict:
    """Minimal Stripe REST call. Only the handful of fields we need."""
    payload = urllib.parse.urlencode(data, doseq=True).encode() if data else None
    # A body-less POST is a GET; an explicit DELETE must stay a DELETE.
    if payload is None and method == "POST":
        method = "GET"
    request = urllib.request.Request(f"{STRIPE_API}{path}", data=payload, method=method)
    request.add_header("Authorization", f"Bearer {stripe_secret()}")
    request.add_header("User-Agent", "minitv-billing/1.0")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode())
            message = detail.get("error", {}).get("message", "")
        except Exception:  # noqa: BLE001 - never mask the HTTP status
            message = ""
        raise BillingError(502, f"stripe rejected the request: {message or exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise BillingError(502, "stripe is unreachable") from exc


def _latest_version(session: Session, plan: Plan) -> PlanVersion:
    version = session.scalar(
        select(PlanVersion).where(PlanVersion.plan_id == plan.id).order_by(PlanVersion.version.desc())
    )
    if version is None:
        raise BillingError(500, "plan has no price version")
    return version


def ensure_stripe_price(
    session: Session, plan: Plan, version: PlanVersion, mode: str = "subscription"
) -> str:
    """The Stripe price of the right *type* for this plan version.

    A plan version can carry two prices: recurring (subscriptions) and one-time
    (passes). Stripe prices are immutable and typed, so switching the global
    billing mode means provisioning the other variant, not editing a price.
    """
    if mode not in BILLING_MODES:
        raise BillingError(400, "unknown billing mode")
    if mode == "one_time":
        if version.stripe_one_time_price_id:
            return version.stripe_one_time_price_id
    elif version.stripe_price_id:
        return version.stripe_price_id

    env_price = os.environ.get(PRICE_ENV.get(plan.code, ""), "").strip()
    if mode == "subscription" and version.version == 1 and env_price:
        # Bootstrap: version 1 was created alongside a dashboard price.
        version.stripe_price_id = env_price
        session.commit()
        return env_price

    product = os.environ.get(PRODUCT_ENV.get(plan.code, ""), "").strip()
    if not product:
        raise BillingError(503, f"stripe product for {plan.code} is not configured")
    payload = {
        "product": product,
        "unit_amount": version.price_cents,
        "currency": (version.currency or "USD").lower(),
        "nickname": (
            f"{plan.name_en} v{version.version} ${version.price_cents / 100:.2f}"
            + (" one-time" if mode == "one_time" else "")
        ),
    }
    if mode == "subscription":
        payload["recurring[interval]"] = INTERVAL_OF.get(plan.code, "month")
    price = _stripe_request("/prices", payload)
    if mode == "one_time":
        version.stripe_one_time_price_id = price["id"]
    else:
        version.stripe_price_id = price["id"]
    session.commit()
    return price["id"]


def ensure_chargeable(mode: str, price_cents: int, currency: str | None) -> None:
    if mode != "one_time":
        return
    if (currency or "USD").upper() != "USD":
        return
    if price_cents < MIN_ONE_TIME_CENTS:
        raise BillingError(
            400,
            f"one-time price must be at least ${MIN_ONE_TIME_CENTS / 100:.2f} (Stripe minimum)",
        )


def create_checkout_session(
    session: Session,
    *,
    user_id: int,
    email: str | None,
    plan_code: str,
    success_url: str | None = None,
    cancel_url: str | None = None,
    source_drama_id: int | None = None,
) -> dict:
    plan = session.scalar(select(Plan).where(Plan.code == plan_code))
    if plan is None or plan.status != "active":
        raise BillingError(404, "plan not found")
    version = _latest_version(session, plan)

    if source_drama_id is not None:
        drama = session.get(Drama, source_drama_id)
        if drama is None or drama.status != "published":
            raise BillingError(404, "source drama not found")

    mode = billing_mode(session)
    ensure_chargeable(mode, version.price_cents, version.currency)
    base = os.environ.get("H5_PUBLIC_BASE_URL", "https://m.example.com").rstrip("/")
    from app.modules.billing.commerce import return_url
    success_url = return_url(success_url, "success")
    cancel_url = return_url(cancel_url, "cancel")
    data = {
        "mode": "payment" if mode == "one_time" else "subscription",
        "line_items[0][price]": ensure_stripe_price(session, plan, version, mode),
        "line_items[0][quantity]": "1",
        "success_url": success_url or f"{base}/?checkout=success",
        "cancel_url": cancel_url or f"{base}/?checkout=cancel",
        "client_reference_id": str(user_id),
        "allow_promotion_codes": "true",
        "metadata[user_id]": str(user_id),
        "metadata[plan_code]": plan_code,
        "metadata[billing_mode]": mode,
    }
    if source_drama_id is not None:
        # Kept on the session and mirrored onto the payment/subscription so a
        # late webhook can still recover it.
        data["metadata[source_drama_id]"] = str(source_drama_id)
    if mode == "one_time":
        data["payment_intent_data[metadata][user_id]"] = str(user_id)
        data["payment_intent_data[metadata][plan_code]"] = plan_code
        if source_drama_id is not None:
            data["payment_intent_data[metadata][source_drama_id]"] = str(source_drama_id)
    else:
        data["subscription_data[metadata][user_id]"] = str(user_id)
        data["subscription_data[metadata][plan_code]"] = plan_code
        if source_drama_id is not None:
            data["subscription_data[metadata][source_drama_id]"] = str(source_drama_id)

    # Reuse the Stripe customer we already have (from a subscription) so passes
    # and subscriptions live under one customer; otherwise let Checkout create one.
    customer_id = session.scalar(
        select(Subscription.stripe_customer_id)
        .where(Subscription.user_id == user_id, Subscription.stripe_customer_id.is_not(None))
        .order_by(Subscription.id.desc())
    )
    if mode == "one_time":
        if customer_id:
            data["customer"] = customer_id
        else:
            data["customer_creation"] = "always"
            if email:
                data["customer_email"] = email
    elif email:
        data["customer_email"] = email
    checkout = _stripe_request("/checkout/sessions", data)

    order = Order(
        order_no=secrets.token_hex(8),
        user_id=user_id,
        plan_version_id=version.id,
        amount_cents=version.price_cents,
        currency=version.currency,
        billing_mode=mode,
        status="created",
        external_txn_id=checkout["id"],
        source_drama_id=source_drama_id,
    )
    session.add(order)
    session.commit()
    return {"url": checkout["url"], "session_id": checkout["id"], "order_no": order.order_no}


def verify_stripe_signature(body: bytes, header: str) -> None:
    """Stripe-Signature: t=...,v1=...,v1=... signed over "<t>.<body>"."""
    credentials=_stripe_credentials.get()
    secret = (credentials.get("webhook_secret", "") if credentials is not None else os.environ.get("STRIPE_WEBHOOK_SECRET", "")).strip()
    if not secret:
        raise BillingError(503, "stripe webhook is not configured")
    stamps: list[int] = []
    signatures: list[str] = []
    for part in (header or "").split(","):
        key, _, value = part.partition("=")
        if key == "t":
            try:
                stamps.append(int(value))
            except ValueError:
                continue
        elif key == "v1":
            signatures.append(value)
    if not stamps or not signatures:
        raise BillingError(400, "missing stripe signature")
    if abs(time.time() - max(stamps)) > STRIPE_WEBHOOK_TOLERANCE:
        raise BillingError(400, "stripe signature expired")
    expected = hmac.new(secret.encode(), f"{max(stamps)}.".encode() + body, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, candidate) for candidate in signatures):
        raise BillingError(401, "bad stripe signature")


def _upsert_subscription(
    session: Session,
    *,
    user_id: int,
    plan_code: str | None,
    stripe_subscription_id: str | None,
    stripe_customer_id: str | None,
    stripe_price_id: str | None,
    status: str,
    period_start: datetime,
    period_end: datetime,
    source_drama_id: int | None = None,
) -> Subscription | None:
    plan = session.scalar(select(Plan).where(Plan.code == plan_code)) if plan_code else None
    if plan is None:
        return None
    version = _latest_version(session, plan)
    current = None
    if stripe_subscription_id:
        current = session.scalar(
            select(Subscription).where(Subscription.stripe_subscription_id == stripe_subscription_id)
        )
    if current is None:
        # One active subscription per user (partial unique index): retire the old row first.
        current = session.scalar(
            select(Subscription).where(Subscription.user_id == user_id, Subscription.status == "active")
        )
    if current is None:
        current = Subscription(user_id=user_id, plan_id=plan.id, plan_version_id=version.id)
        session.add(current)
    elif (
        current.status == "canceled"
        and current.canceled_at is not None
        and status == "active"
        and period_start <= _as_aware(current.canceled_at)
    ):
        # A late or retried event generated *before* the cancellation must not
        # resurrect a membership an operator took back (or Stripe already
        # ended). A renewal that starts after the cancellation still counts.
        return current
    elif current.plan_id != plan.id:
        current.plan_id = plan.id
        current.plan_version_id = version.id
    current.status = status
    current.current_period_start = period_start
    current.current_period_end = period_end
    current.stripe_subscription_id = stripe_subscription_id or current.stripe_subscription_id
    current.stripe_customer_id = stripe_customer_id or current.stripe_customer_id
    current.stripe_price_id = stripe_price_id or current.stripe_price_id
    if status == "active":
        current.canceled_at = None
    # First purchase wins; renewals never rewrite the origin drama.
    if source_drama_id is not None and current.source_drama_id is None:
        current.source_drama_id = source_drama_id
    return current


def _as_datetime(epoch: int | None, fallback: datetime | None = None) -> datetime:
    if epoch:
        return datetime.fromtimestamp(int(epoch), tz=timezone.utc)
    return fallback or datetime.now(timezone.utc)


def _source_drama_id(metadata: dict) -> int | None:
    """Attribution can only come from our own metadata; ignore junk."""
    raw = metadata.get("source_drama_id")
    try:
        return int(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _period_bounds(remote: dict, plan_code: str | None = None) -> tuple[datetime, datetime]:
    """Billing period of a Stripe subscription.

    Stripe moved ``current_period_start/end`` from the subscription onto its
    items (Basil API, 2025+), so the top-level fields are usually null now.
    Read both shapes before falling back to the plan length.
    """
    items = (remote.get("items") or {}).get("data") or [{}]
    item = items[0] if items else {}
    start_epoch = remote.get("current_period_start") or item.get("current_period_start")
    end_epoch = remote.get("current_period_end") or item.get("current_period_end")
    start = _as_datetime(start_epoch)
    end = _as_datetime(end_epoch, start + timedelta(days={"weekly": 7, "monthly": 30, "yearly": 365}.get(plan_code or "", 30)))
    if end <= start:  # never hand the entitlement a zero-length window
        end = start + timedelta(days={"weekly": 7, "monthly": 30, "yearly": 365}.get(plan_code or "", 30))
    return start, end


def _activate_from_checkout(session: Session, checkout: dict) -> dict:
    metadata = checkout.get("metadata") or {}
    user_id = int(metadata.get("user_id") or checkout.get("client_reference_id") or 0)
    plan_code = metadata.get("plan_code")
    source_drama_id = _source_drama_id(metadata)
    if not user_id or not plan_code:
        raise BillingError(400, "checkout session is missing our metadata")

    order = session.scalar(select(Order).where(Order.external_txn_id == checkout.get("id")))
    if order is None:
        plan = session.scalar(select(Plan).where(Plan.code == plan_code))
        if plan is None:
            raise BillingError(404, "plan not found")
        order = Order(
            order_no=secrets.token_hex(8),
            user_id=user_id,
            plan_version_id=_latest_version(session, plan).id,
            amount_cents=int(checkout.get("amount_total") or 0),
            currency=(checkout.get("currency") or "usd").upper(),
            external_txn_id=checkout.get("id"),
            source_drama_id=source_drama_id,
        )
        session.add(order)
    elif order.source_drama_id is None:
        order.source_drama_id = source_drama_id
    order.status = "paid"
    order.paid_at = datetime.now(timezone.utc)

    subscription_id = checkout.get("subscription")
    customer_id = checkout.get("customer")
    price_id = None
    period_start = datetime.now(timezone.utc)
    period_end = period_start + timedelta(days={"weekly": 7, "monthly": 30, "yearly": 365}.get(plan_code, 30))
    if subscription_id:
        remote = _stripe_request(f"/subscriptions/{subscription_id}")
        price_id = ((remote.get("items") or {}).get("data") or [{}])[0].get("price", {}).get("id")
        customer_id = remote.get("customer") or customer_id
        plan_code = (remote.get("metadata") or {}).get("plan_code") or plan_code
        period_start, period_end = _period_bounds(remote, plan_code)
    _upsert_subscription(
        session,
        user_id=user_id,
        plan_code=plan_code,
        stripe_subscription_id=subscription_id,
        stripe_customer_id=customer_id,
        stripe_price_id=price_id,
        status="active",
        period_start=period_start,
        period_end=period_end,
        source_drama_id=source_drama_id,
    )
    session.commit()
    return {"order_no": order.order_no, "status": order.status, "user_id": user_id}


PLAN_DAYS = {"weekly": 7, "monthly": 30, "yearly": 365}


def _activate_pass(session: Session, checkout: dict) -> dict:
    """One-time purchase: stack the plan's days onto whatever is left."""
    metadata = checkout.get("metadata") or {}
    user_id = int(metadata.get("user_id") or checkout.get("client_reference_id") or 0)
    plan_code = metadata.get("plan_code")
    source_drama_id = _source_drama_id(metadata)
    if not user_id or not plan_code:
        raise BillingError(400, "checkout session is missing our metadata")
    if checkout.get("payment_status") not in (None, "paid"):
        return {"pending": True, "session_id": checkout.get("id")}

    order = session.scalar(select(Order).where(Order.external_txn_id == checkout.get("id")))
    if order is not None and order.status == "paid":
        # Stripe retries the same event; never add days twice.
        return {"replayed": True, "order_no": order.order_no}

    plan = session.scalar(select(Plan).where(Plan.code == plan_code))
    if plan is None or plan.status != "active":
        raise BillingError(404, "plan not found")
    version = session.get(PlanVersion, order.plan_version_id) if order is not None else _latest_version(session, plan)
    if order is None:
        order = Order(
            order_no=secrets.token_hex(8),
            user_id=user_id,
            plan_version_id=version.id,
            amount_cents=int(checkout.get("amount_total") or version.price_cents),
            currency=(checkout.get("currency") or version.currency or "usd").upper(),
            billing_mode="one_time",
            external_txn_id=checkout.get("id"),
            source_drama_id=source_drama_id,
        )
        session.add(order)
    elif order.source_drama_id is None:
        order.source_drama_id = source_drama_id
    order.status = "paid"
    order.paid_at = datetime.now(timezone.utc)
    order.billing_mode = "one_time"

    now = datetime.now(timezone.utc)
    days = PLAN_DAYS.get(plan_code, 30)
    row = session.scalar(
        select(Subscription).where(Subscription.user_id == user_id, Subscription.status == "active")
    )
    if row is None:
        row = Subscription(
            user_id=user_id,
            plan_id=plan.id,
            plan_version_id=version.id,
            status="active",
            current_period_start=now,
            current_period_end=now,
            billing_mode="one_time",
        )
        session.add(row)
    # Stack on whatever is already paid for — a live subscription counts too,
    # otherwise buying a pass mid-subscription would burn most of its days.
    base = max(
        [now]
        + [end for end in (_as_aware(row.pass_end), _as_aware(row.current_period_end)) if end]
    )
    row.pass_end = base + timedelta(days=days)
    row.billing_mode = "one_time"
    row.plan_id = plan.id
    row.plan_version_id = version.id
    if source_drama_id is not None and row.source_drama_id is None:
        row.source_drama_id = source_drama_id
    session.commit()
    return {
        "order_no": order.order_no,
        "status": "paid",
        "user_id": user_id,
        "days": days,
        "pass_until": row.pass_end.isoformat(),
    }


def _sync_from_subscription(session: Session, remote: dict) -> dict:
    metadata = remote.get("metadata") or {}
    user_id = int(metadata.get("user_id") or 0)
    source_drama_id = _source_drama_id(metadata)
    price_id = ((remote.get("items") or {}).get("data") or [{}])[0].get("price", {}).get("id")
    plan_code = metadata.get("plan_code") or plan_code_for_price(price_id)
    existing = session.scalar(
        select(Subscription).where(Subscription.stripe_subscription_id == remote.get("id"))
    )
    if existing is not None:
        user_id = existing.user_id
    if not user_id:
        raise BillingError(400, "subscription is missing our user id")

    if plan_code is None and price_id:
        # Prices created by ensure_stripe_price are only known to the DB.
        owner = session.scalar(
            select(PlanVersion).where(PlanVersion.stripe_price_id == price_id)
        )
        if owner is not None:
            plan_code = session.scalar(select(Plan.code).where(Plan.id == owner.plan_id))

    status = remote.get("status")
    local_status = "active" if status in {"active", "trialing", "past_due"} else "canceled"
    period_start, period_end = _period_bounds(remote, plan_code)
    row = _upsert_subscription(
        session,
        user_id=user_id,
        plan_code=plan_code,
        stripe_subscription_id=remote.get("id"),
        stripe_customer_id=remote.get("customer"),
        stripe_price_id=price_id,
        status=local_status,
        period_start=period_start,
        period_end=period_end,
        source_drama_id=source_drama_id,
    )
    if row is not None:
        if local_status == "canceled":
            row.canceled_at = row.canceled_at or datetime.now(timezone.utc)
            pass_end = _as_aware(row.pass_end)
            if pass_end is not None and pass_end > datetime.now(timezone.utc):
                row.status = "active"  # the pass is still running
        elif remote.get("cancel_at_period_end"):
            # Scheduled to stop renewing: access lasts until current_period_end.
            row.canceled_at = row.canceled_at or datetime.now(timezone.utc)
        else:
            row.canceled_at = None
    session.commit()
    return {"user_id": user_id, "status": local_status}


def apply_stripe_event(session: Session, *, body: bytes, signature: str, payload: dict, signature_verified: bool = False) -> dict:
    if not signature_verified:
        verify_stripe_signature(body, signature)
    event_type = payload.get("type") or ""
    obj = (payload.get("data") or {}).get("object") or {}
    if event_type in {"checkout.session.completed", "checkout.session.async_payment_succeeded"}:
        if (obj.get("mode") or "") == "payment":
            result = _activate_pass(session, obj)
        else:
            result = _activate_from_checkout(session, obj)
    elif event_type in {"customer.subscription.created", "customer.subscription.updated"}:
        result = _sync_from_subscription(session, obj)
    elif event_type == "customer.subscription.deleted":
        row = session.scalar(
            select(Subscription).where(Subscription.stripe_subscription_id == obj.get("id"))
        )
        if row is not None:
            row.canceled_at = datetime.now(timezone.utc)
            pass_end = _as_aware(row.pass_end)
            still_paid = pass_end is not None and pass_end > datetime.now(timezone.utc)
            row.status = "active" if still_paid else "canceled"
            session.commit()
        result = {"canceled": bool(row and row.status == "canceled")}
    else:
        result = {"ignored": event_type}
    return {"received": True, "type": event_type, **result}



# Commercial grants coexist with immutable historical subscription windows.
_legacy_active_membership = active_membership
_legacy_apply_stripe_event = apply_stripe_event
_legacy_cancel_subscription = cancel_subscription

def access_until(session: Session, user_id: int) -> datetime | None:
    from app.modules.billing.commerce import grant_access_until
    return grant_access_until(session,user_id)

def active_membership(session: Session, user_id: int) -> dict:
    from app.modules.billing.commerce import grant_access_until,aware
    from app.modules.billing.commerce_models import MembershipGrant
    result=_legacy_active_membership(session,user_id)
    end=grant_access_until(session,user_id)
    result['active']=end is not None
    result['until']=end.isoformat() if end else None
    grant=session.scalar(select(MembershipGrant).where(MembershipGrant.user_id==user_id,MembershipGrant.status=='active',MembershipGrant.starts_at<=datetime.now(timezone.utc),MembershipGrant.ends_at>datetime.now(timezone.utc)).order_by(MembershipGrant.ends_at.desc()).limit(1))
    if grant is not None:
        order=session.get(Order,grant.order_id);snapshot=order.product_snapshot
        result.update(plan_code=snapshot.get('code'),billing_mode=order.billing_mode,pass_until=grant.ends_at.isoformat() if order.billing_mode=='one_time' else result.get('pass_until'))
    return result

def list_user_orders(session: Session,user_id:int,limit:int=20,offset:int=0)->dict:
    from app.modules.billing.commerce import list_orders
    return list_orders(session,user_id,limit,offset)

def apply_stripe_event(session:Session,*,body:bytes,signature:str,payload:dict)->dict:
    from app.modules.billing.site_merchants import receive
    def legacy():
        with managed_stripe_runtime(session):
            return _legacy_apply_stripe_event(session,body=body,signature=signature,payload=payload,signature_verified=True)
    return receive(session,'env',body,signature,payload,legacy_handler=legacy)


def _prepare_managed_cancellation(session:Session,row:Subscription)->Subscription:
    """Commit the intent before network I/O, then restore wallet/subscription locks."""
    from app.modules.billing.commerce import wallet_lock
    from app.modules.billing.commerce_models import StripeSubscriptionLifecycle
    identity=(row.id,row.user_id,row.account_code,row.stripe_subscription_id)
    while not (row.cancellation_context or {}).get('key'):
        fact=session.get(StripeSubscriptionLifecycle,(row.account_code,row.stripe_subscription_id),populate_existing=True)
        row.cancellation_context=dict(key='cancel:'+secrets.token_hex(24),requested_at=int(time.time()),lifecycle_event_created=fact.last_event_created if fact else 0)
        session.commit()
        wallet_lock(session,identity[1])
        row=session.get(Subscription,identity[0],with_for_update=True,populate_existing=True)
        if row is None or not row.managed_commerce or row.status!='active' or (row.id,row.user_id,row.account_code,row.stripe_subscription_id)!=identity:
            raise BillingError(409,'Subscription changed while preparing cancellation; retry')
        # An accepted resume may have retired this intent during the commit gap.
    return row


def _cancel_managed_renewal(session:Session,row:Subscription)->None:
    from app.modules.billing.commerce import stripe_request
    context=row.cancellation_context
    path='/subscriptions/'+urllib.parse.quote(row.stripe_subscription_id,safe='')
    response=stripe_request(session,row.account_code,path,
        {'cancel_at_period_end':'true'},idempotency_key=context['key'])
    # A successful POST may be an old idempotency-cache response. Confirm current
    # merchant state before telling a user/admin that future renewal is stopped.
    remote=stripe_request(session,row.account_code,path,method='GET')
    if not isinstance(remote,dict) or remote.get('id')!=row.stripe_subscription_id:
        raise BillingError(502,'Could not verify the original subscription cancellation')
    live={'active','trialing','past_due','unpaid','incomplete','paused'}
    terminal=remote.get('status') in {'canceled','incomplete_expired'}
    if not terminal:
        if remote.get('status') not in live or type(remote.get('cancel_at_period_end')) is not bool:
            raise BillingError(409,'Original subscription cancellation is not confirmed; retry or contact support')
        if not remote['cancel_at_period_end']:
            if not isinstance(response,dict) or response.get('id')!=row.stripe_subscription_id or response.get('status') not in live or response.get('cancel_at_period_end') is not True:
                raise BillingError(409,'Cancellation response could not be reconciled with current provider state; contact support')
            # The original operation did succeed, but a later resume superseded
            # it. Retire its key durably; a caller retry is a new logical intent.
            row.cancellation_context={};row.canceled_at=None;session.commit()
            raise BillingError(409,dict(code='subscription_resumed_retry',message='This subscription resumed after the previous cancellation. Retry to stop renewal.',action='retry'))
    from app.modules.billing.commerce_models import StripeSubscriptionLifecycle
    fact=session.get(StripeSubscriptionLifecycle,(row.account_code,row.stripe_subscription_id),populate_existing=True)
    stamp=int(time.time())
    row.cancellation_context={**context,'confirmed_at':context.get('confirmed_at') or stamp,'verified_at':stamp,'lifecycle_event_created':max(context.get('lifecycle_event_created',0),fact.last_event_created if fact else 0)}


def cancel_subscription(session:Session,user_id:int)->None:
    from app.modules.billing.commerce import wallet_lock
    wallet_lock(session,user_id)
    row=active_subscription(session,user_id,for_update=True)
    if row is not None and row.waffo_subscription_id:
        from app.modules.billing.waffo_subscriptions import cancel_renewal

        cancel_renewal(session,row)
        session.commit()
        return
    if row is not None and row.pancake_subscription_id:
        from app.modules.billing.pancake_subscriptions import cancel_renewal

        cancel_renewal(session,row)
        session.commit()
        return
    if row is not None and row.creem_subscription_id:
        from app.modules.billing.creem_subscriptions import cancel_renewal

        cancel_renewal(session,row)
        session.commit()
        return
    if row is not None and row.managed_commerce and row.stripe_subscription_id:
        row=_prepare_managed_cancellation(session,row)
        _cancel_managed_renewal(session,row)
        row.canceled_at=datetime.now(timezone.utc);session.commit();return
    with managed_stripe_runtime(session,row.account_code if row else "env"):
        return _legacy_cancel_subscription(session,user_id)


_legacy_revoke_membership = revoke_membership

def _existing_managed_subscription_bindings(session:Session,user_id:int)->list[dict]:
    bindings=set(session.execute(select(Subscription.account_code,Subscription.stripe_subscription_id).where(Subscription.user_id==user_id,Subscription.managed_commerce.is_(True),Subscription.stripe_subscription_id.is_not(None))).all())
    bindings.update(session.execute(select(Order.account_code,Order.subscription_ref).where(Order.provider=='stripe',Order.user_id==user_id,Order.product_type=='membership',Order.billing_mode=='subscription',Order.checkout_key.is_not(None),Order.subscription_ref.is_not(None))).all())
    return [dict(account_code=account,stripe_subscription_id=subscription) for account,subscription in sorted(bindings)]


def _existing_membership_sources(session:Session,user_id:int)->list[dict]:
    from app.modules.billing.commerce_models import MembershipGrant
    stmt=select(MembershipGrant.order_id,MembershipGrant.id).join(Order,Order.id==MembershipGrant.order_id).where(MembershipGrant.user_id==user_id,Order.user_id==user_id,Order.product_type=='membership',MembershipGrant.status.in_(('active','refund_pending','pending_refund'))).order_by(MembershipGrant.id).with_for_update()
    return [dict(order_id=order_id,grant_id=grant_id) for order_id,grant_id in session.execute(stmt)]


def revoke_membership(session:Session,*,user_id:int,mode:str,reason:str,actor_id:int)->dict:
    from app.modules.billing import commerce, creem_subscriptions, pancake_subscriptions, waffo_subscriptions
    from app.modules.billing.commerce_models import MembershipGrant
    if mode not in REVOKE_MODES:raise BillingError(422,'unknown revoke mode')
    if not reason.strip():raise BillingError(422,'请填写取消原因')
    commerce.wallet_lock(session,user_id)
    grants=list(session.scalars(select(MembershipGrant).where(MembershipGrant.user_id==user_id,MembershipGrant.status=='active',MembershipGrant.ends_at>_now()).with_for_update()))
    row=active_subscription(session,user_id,for_update=True)
    revoked_bindings=_existing_managed_subscription_bindings(session,user_id) if mode=='now' else []
    revoked_creem=creem_subscriptions.existing_bindings(session,user_id) if mode=='now' else []
    revoked_pancake=pancake_subscriptions.revocable_bindings(session,user_id) if mode=='now' else []
    revoked_waffo=waffo_subscriptions.revocable_bindings(session,user_id) if mode=='now' else []
    revoked_sources=_existing_membership_sources(session,user_id) if mode=='now' else []
    if not grants and (row is None or not row.managed_commerce) and not revoked_bindings and not revoked_creem and not revoked_pancake and not revoked_waffo and not revoked_sources:
        with managed_stripe_runtime(session,row.account_code if row else "env"):
            return _legacy_revoke_membership(session,user_id=user_id,mode=mode,reason=reason,actor_id=actor_id)
    before=access_until(session,user_id)
    if before is None:raise BillingError(409,'该用户当前没有有效会员')
    stripe_id=row.stripe_subscription_id if row else None
    creem_id=row.creem_subscription_id if row else None
    pancake_id=row.pancake_subscription_id if row else None
    waffo_id=row.waffo_subscription_id if row else None
    if mode=='period_end' and not (stripe_id or creem_id or pancake_id or waffo_id):raise BillingError(409,'时长包没有续费概念，请选择「立即收回」')
    if stripe_id:
        if row.managed_commerce:
            if mode=='period_end':
                row=_prepare_managed_cancellation(session,row)
                # The durable intent commit releases locks. Refresh paid access
                # before applying an admin action in the reacquired transaction.
                grants=list(session.scalars(select(MembershipGrant).where(MembershipGrant.user_id==user_id,MembershipGrant.status=='active',MembershipGrant.ends_at>_now()).with_for_update().execution_options(populate_existing=True)))
                before=access_until(session,user_id)
                if before is None:raise BillingError(409,'该用户当前没有有效会员')
                _cancel_managed_renewal(session,row)
            else:
                commerce.stripe_request(session,row.account_code,'/subscriptions/'+stripe_id,None,method='DELETE',idempotency_key='admin-revoke:'+mode+':'+stripe_id)
        else:
            with managed_stripe_runtime(session,row.account_code):
                _stripe_request('/subscriptions/'+stripe_id,{'cancel_at_period_end':'true'} if mode=='period_end' else None,method='POST' if mode=='period_end' else 'DELETE')
    if mode=='period_end' and creem_id:
        creem_subscriptions.cancel_renewal(session,row)
    if mode=='period_end' and pancake_id:
        pancake_subscriptions.cancel_renewal(session,row)
    if mode=='period_end' and waffo_id:
        waffo_subscriptions.cancel_renewal(session,row)
    if mode=='now':
        if creem_id:
            binding = dict(account_code=row.account_code,subscription_id=creem_id)
            if binding not in revoked_creem:
                revoked_creem.append(binding)
        creem_subscriptions.cancel_known_bindings(session,revoked_creem,row)
        if pancake_id:
            binding = dict(account_code=row.account_code,subscription_id=pancake_id)
            if binding not in revoked_pancake:
                revoked_pancake.append(binding)
        pancake_subscriptions.cancel_known_bindings(session,revoked_pancake,row)
        if waffo_id:
            binding = dict(account_code=row.account_code,subscription_id=waffo_id)
            if binding not in revoked_waffo:
                revoked_waffo.append(binding)
        waffo_subscriptions.cancel_known_bindings(session,revoked_waffo,row)
    moment=_now()
    if row:row.canceled_at=moment
    if mode=='now':
        for grant in grants:grant.status='revoked'
        if row:row.pass_end=moment;row.current_period_end=moment;row.status='canceled'
    payload=dict(mode=mode,reason=reason,before_until=before.isoformat(),after_until=before.isoformat() if mode=='period_end' else moment.isoformat(),stripe_canceled=bool(stripe_id))
    if mode=='now':
        # Freeze known bindings and sources without disturbing refund reservations.
        # Original managed orders retain historical identities if a row was replaced.
        payload.update(cutoff_at=moment.isoformat(),revoked_subscription_bindings=revoked_bindings,revoked_membership_sources=revoked_sources)
        if revoked_creem:
            payload['revoked_creem_subscription_bindings']=revoked_creem
        if revoked_pancake:
            payload['revoked_pancake_subscription_bindings']=revoked_pancake
        if revoked_waffo:
            payload['revoked_waffo_subscription_bindings']=revoked_waffo
    session.add(AuditLog(actor_type='admin',actor_id=actor_id,action='membership_revoke',target_type='user',target_id=user_id,payload=payload,created_at=_now()))
    session.commit()
    result=dict(mode=mode,before_until=before.isoformat(),after_until=before.isoformat() if mode=='period_end' else moment.isoformat(),stripe_canceled=bool(stripe_id))
    if creem_id or revoked_creem:
        result['creem_canceled']=True
    if pancake_id or revoked_pancake:
        result['pancake_canceled']=True
    if waffo_id or revoked_waffo:
        result['waffo_canceled']=True
    return result


# checkout. Only the original site's original merchant may use global price IDs.

def ensure_stripe_price(session:Session,plan:Plan,version:PlanVersion,mode:str="subscription")->str:
    from app.modules.billing.commerce import ensure_price
    from app.modules.billing.site_merchants import purchase_account

    plan = require_owned(session, Plan, plan.id)
    version = require_owned(session, PlanVersion, version.id)
    if version.plan_id != plan.id:
        raise BillingError(404, "plan price version not found")
    if mode not in BILLING_MODES:
        raise BillingError(400, "unknown billing mode")
    account = purchase_account(session)
    return ensure_price(session, account['code'], dict(
        product_type='membership', code=plan.code, name=plan.name_en,
        version_id=version.id, price_cents=version.price_cents,
        currency=version.currency, billing_mode=mode,
    ))


def provision_site_billing(db,tenant_id):
    """Explicit provisioning hook. Default packages are local drafts until funded."""
    with site_context(db,tenant_id):
        from app.modules.sites.models import SiteSettings
        settings=db.get(SiteSettings,tenant_id)
        currency=settings.currency if settings else 'USD'
        if db.scalar(select(Plan).limit(1)) is None:
            for code,en,zh,cents,best,items in DEFAULT_PLANS:
                plan=Plan(tenant_id=tenant_id,code=code,name_en=en,name_zh=zh)
                db.add(plan);db.flush()
                db.add(PlanVersion(tenant_id=tenant_id,plan_id=plan.id,version=1,price_cents=cents,currency=currency,benefits={'items':items,'best':best}))
        if db.scalar(select(BillingSettings).limit(1)) is None:db.add(new_billing_settings(db,tenant_id))
        db.flush()

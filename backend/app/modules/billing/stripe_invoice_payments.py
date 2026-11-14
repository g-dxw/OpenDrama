"""Resolve invoice payment sources without treating an allocation as a whole PI.

The caller supplies GET access bound to the order's frozen Stripe account. No
API version is changed here. Legacy signed invoice-paid events keep their old
pointer semantics; Basil invoices require complete allocations and remote proof.
See Stripe's 2025-03-31 Basil invoice-payment changelog and object/list APIs.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from app.modules.billing.service import BillingError

Fetch = Callable[[str], dict[str, Any]]


@dataclass(frozen=True)
class InvoicePaymentResolution:
    payment_intent_id: str | None
    payment_intent_ids: tuple[str, ...]
    requires_manual_review: bool
    reason: str
    evidence: dict[str, Any]


def _id(value: Any, prefix: str) -> str | None:
    if isinstance(value, Mapping):
        value = value.get("id")
    if isinstance(value, str) and "_secret_" not in value and len(value) <= 100 and re.fullmatch(re.escape(prefix) + r"[A-Za-z0-9_]+", value):
        return value
    return None


def _integer(value: Any) -> int | None:
    return value if type(value) is int else None


def _currency(value: Any) -> str | None:
    return value.upper() if isinstance(value, str) and re.fullmatch(r"[a-zA-Z]{3}", value) else None


def _list_all(fetch: Fetch, parameters: dict[str, str], initial: Any = None) -> list[dict]:
    """Do not ACK a truncated or malformed provider response as complete proof."""
    if isinstance(initial, Mapping) and isinstance(initial.get("data"), list) and type(initial.get("has_more")) is bool:
        page = initial
    else:
        page = fetch("/invoice_payments?" + urlencode(parameters))
    records: list[dict] = []
    seen: set[str] = set()
    for _ in range(50):
        if not isinstance(page, Mapping) or not isinstance(page.get("data"), list) or type(page.get("has_more")) is not bool:
            raise BillingError(502, "Stripe invoice-payment list is incomplete; retry the invoice webhook")
        for record in page["data"]:
            record_id = _id(record, "inpay_")
            if not isinstance(record, Mapping) or record_id is None or record_id in seen:
                raise BillingError(502, "Stripe invoice-payment pagination is inconsistent; retry the invoice webhook")
            seen.add(record_id)
            records.append(dict(record))
        if not page["has_more"]:
            return records
        if not page["data"]:
            raise BillingError(502, "Stripe invoice-payment pagination has no cursor; retry the invoice webhook")
        parameters = {**parameters, "starting_after": page["data"][-1]["id"]}
        page = fetch("/invoice_payments?" + urlencode(parameters))
    raise BillingError(502, "Stripe invoice-payment pagination exceeded its safety bound; manual investigation required")


def _payment_evidence(row: Mapping) -> dict:
    payment = row.get("payment") if isinstance(row.get("payment"), Mapping) else {}
    kind = payment.get("type")
    reference = payment.get(kind) if isinstance(kind, str) else None
    if isinstance(reference, Mapping):
        reference = reference.get("id")
    if not isinstance(reference, str) or "_secret_" in reference or not re.fullmatch(r"(?:pi|ch|pyr|pmr)_[A-Za-z0-9_]{1,96}", reference):
        reference = None
    return {
        "id": row.get("id"), "invoice": _id(row.get("invoice"), "in_"),
        "status": row.get("status") if isinstance(row.get("status"), str) else None, "amount_paid": _integer(row.get("amount_paid")),
        "amount_requested": _integer(row.get("amount_requested")),
        "currency": _currency(row.get("currency")),
        "livemode": row.get("livemode") if type(row.get("livemode")) is bool else None,
        "type": kind if isinstance(kind, str) else None,
        "payment_intent_id": _id(payment.get("payment_intent"), "pi_"),
        "payment_reference": reference,
    }


def resolve_invoice_payment(
    invoice: dict,
    *,
    fetch: Fetch,
    expected_amount: int,
    expected_currency: str,
    livemode: bool | None = None,
) -> InvoicePaymentResolution:
    """Return one refundable PI only when its whole receipt pays this invoice.

    Call only from an authenticated invoice-paid event, with the order's frozen
    amount/currency and a fetch closure using that same account's credentials.
    Network and provider-shape failures propagate, so webhook retries retain a
    chance to record payment associations before acknowledging the event.
    """
    reasons: list[str] = []
    invoice_id = _id(invoice, "in_")
    legacy = _id(invoice.get("payment_intent"), "pi_")
    modern = "payments" in invoice or legacy is None
    currency = _currency(invoice.get("currency"))
    wanted_currency = _currency(expected_currency)
    invoice_mode = invoice.get("livemode") if type(invoice.get("livemode")) is bool else None
    mode = livemode if type(livemode) is bool else invoice_mode
    evidence = {
        "version": 1, "source": "invoice_payments" if modern else "legacy_invoice_pointer",
        "invoice_id": invoice_id, "invoice_status": invoice.get("status") if isinstance(invoice.get("status"), str) else None,
        "amount_paid": _integer(invoice.get("amount_paid")), "currency": currency,
        "livemode": invoice_mode, "payments": [], "payment_intents": [],
    }
    references: set[str] = set()

    def result(primary: str | None = None) -> InvoicePaymentResolution:
        reason = "; ".join(dict.fromkeys(reasons))
        review = bool(reason) or primary is None
        if review and not reason:
            reason = "Invoice has no provable single whole PaymentIntent; manual refund review required"
        ids = tuple(sorted(references))
        evidence.update(payment_intent_ids=list(ids), requires_manual_review=review, reason=reason)
        return InvoicePaymentResolution(None if review else primary, ids, review, reason, evidence)

    if invoice_id is None:
        reasons.append("Invoice identity is missing or invalid")
    if type(expected_amount) is not int or expected_amount <= 0 or evidence["amount_paid"] != expected_amount:
        reasons.append("Invoice paid amount does not match the frozen order")
    if wanted_currency is None or currency != wanted_currency:
        reasons.append("Invoice currency does not match the frozen order")
    if invoice_mode is not None and mode is not None and invoice_mode != mode:
        reasons.append("Invoice mode does not match the bound Stripe account")
    if ("status" in invoice and not isinstance(invoice["status"], str)) or evidence["invoice_status"] not in ({None, "paid"} if not modern else {"paid"}) or invoice.get("paid") is False:
        reasons.append("Invoice is not confirmed paid")
    for field in ("amount_remaining", "amount_overpaid", "amount_paid_off_stripe"):
        if field in invoice and _integer(invoice[field]) != 0:
            reasons.append("Invoice has remaining, overpaid or off-Stripe amounts")
    if invoice.get("paid_out_of_band"):
        reasons.append("Invoice was paid out of band")
    if "amount_due" in invoice and _integer(invoice["amount_due"]) != expected_amount:
        reasons.append("Invoice amount due does not prove a whole order payment")

    # Old invoice versions guaranteed one PI. Preserve signed historical events
    # and fixtures without introducing new provider calls for those versions.
    if not modern:
        references.add(legacy)
        evidence["legacy_payment_intent_id"] = legacy
        expanded = invoice.get("payment_intent")
        if isinstance(expanded, Mapping):
            for field in ("amount", "amount_received"):
                if field in expanded and _integer(expanded[field]) != expected_amount:
                    reasons.append("Expanded legacy PaymentIntent is not a whole order payment")
            if "status" in expanded and expanded["status"] != "succeeded":
                reasons.append("Expanded legacy PaymentIntent is not succeeded")
            if "currency" in expanded and _currency(expanded["currency"]) != currency:
                reasons.append("Expanded legacy PaymentIntent currency does not match the invoice")
            if "livemode" in expanded and (type(expanded["livemode"]) is not bool or mode is not None and expanded["livemode"] != mode):
                reasons.append("Expanded legacy PaymentIntent mode does not match the account")
        return result(legacy)
    if "payments" not in invoice and invoice.get("paid_out_of_band") is True:
        evidence["source"] = "legacy_out_of_band"
        return result()
    if invoice_id is None:
        return result()
    if mode is None:
        reasons.append("Invoice payment mode is unknown")

    allocations = _list_all(fetch, {"invoice": invoice_id, "limit": "100"}, invoice.get("payments"))
    evidence["payments"] = [_payment_evidence(row) for row in allocations]
    paid: list[dict] = []
    candidates: set[str] = set()
    for row in evidence["payments"]:
        if row["invoice"] != invoice_id:
            reasons.append("Invoice payment belongs to another invoice")
            continue
        if row["status"] not in {"open", "paid", "canceled"}:
            reasons.append("Invoice payment status is unknown")
        if row["currency"] != currency:
            reasons.append("Invoice payment currency does not match the invoice")
        if row["livemode"] is None or mode is None or row["livemode"] != mode:
            reasons.append("Invoice payment mode does not match the bound account")
        if row["type"] != "payment_intent" or row["payment_intent_id"] is None:
            reasons.append("Invoice contains an unsupported or unknown payment source")
            continue
        candidates.add(row["payment_intent_id"])
        if row["status"] == "paid":
            if row["amount_paid"] is None or row["amount_paid"] <= 0:
                reasons.append("Paid invoice allocation has no positive amount")
            paid.append(row)
        elif row["amount_paid"] not in {None, 0}:
            reasons.append("Unpaid invoice allocation unexpectedly contains paid funds")
        elif row["status"] == "open":
            reasons.append("Invoice still has an open payment allocation")

    for identifier in sorted(candidates):
        # Always retrieve the PI with the original account, even if the event
        # expanded it, to avoid trusting a pointer belonging to another merchant.
        intent = fetch("/payment_intents/" + identifier)
        if not isinstance(intent, Mapping) or _id(intent, "pi_") != identifier or intent.get("object") != "payment_intent":
            raise BillingError(502, "Stripe did not return the requested PaymentIntent; retry the invoice webhook")
        reverse = _list_all(fetch, {"payment[type]": "payment_intent", "payment[payment_intent]": identifier, "limit": "100"})
        links: set[str] = set()
        reverse_facts: list[dict] = []
        for raw in reverse:
            link = _payment_evidence(raw)
            if link["type"] != "payment_intent" or link["payment_intent_id"] != identifier or link["invoice"] is None:
                raise BillingError(502, "Stripe PaymentIntent invoice associations are inconsistent; retry the invoice webhook")
            links.add(link["invoice"])
            reverse_facts.append(link)
        current_links = [row for row in reverse_facts if row["invoice"] == invoice_id]
        forward_links = [row for row in evidence["payments"] if row["payment_intent_id"] == identifier and row["invoice"] == invoice_id]
        comparable = ("invoice", "status", "amount_paid", "amount_requested", "currency", "livemode", "type", "payment_intent_id")
        if {row["id"] for row in current_links} != {row["id"] for row in forward_links} or any(
            not any(row["id"] == remote["id"] and all(row[key] == remote[key] for key in comparable) for remote in current_links)
            for row in forward_links
        ):
            reasons.append("Invoice Payment allocations do not match current provider associations")
        facts = {"id": identifier, "status": intent.get("status") if isinstance(intent.get("status"), str) else None, "amount": _integer(intent.get("amount")),
                 "amount_received": _integer(intent.get("amount_received")), "currency": _currency(intent.get("currency")),
                 "livemode": intent.get("livemode") if type(intent.get("livemode")) is bool else None,
                 "customer": _id(intent.get("customer"), "cus_"), "invoice_ids": sorted(links), "invoice_payments": reverse_facts}
        evidence["payment_intents"].append(facts)
        if invoice_id in links and facts["livemode"] is not None and facts["livemode"] == mode:
            references.add(identifier)
        else:
            reasons.append("PaymentIntent cannot be confirmed under this invoice and account")
        if links != {invoice_id}:
            reasons.append("PaymentIntent is shared with another invoice or lacks this invoice association")
        if facts["status"] != "succeeded" or facts["amount"] != expected_amount or facts["amount_received"] != expected_amount:
            reasons.append("PaymentIntent is not a succeeded whole order payment")
        if facts["currency"] != currency:
            reasons.append("PaymentIntent currency does not match the invoice")
        customer = _id(invoice.get("customer"), "cus_")
        if customer is not None and facts["customer"] != customer:
            reasons.append("PaymentIntent customer does not match the invoice")
        if facts["livemode"] is None or mode is None or facts["livemode"] != mode:
            reasons.append("PaymentIntent mode does not match the bound account")

    if len(paid) != 1 or len(candidates) != 1 or len(references) != 1:
        reasons.append("Invoice does not have exactly one proven paid PaymentIntent allocation")
    if any(row["amount_paid"] != expected_amount or row["amount_requested"] != expected_amount for row in paid):
        reasons.append("Invoice allocation is partial or differs from the whole order amount")
    if sum(row["amount_paid"] or 0 for row in paid) != expected_amount:
        reasons.append("Invoice payment allocations do not cover the frozen order amount")
    if legacy is not None and legacy not in references:
        reasons.append("Legacy invoice pointer conflicts with the verified payment allocations")
    primary = paid[0]["payment_intent_id"] if len(paid) == 1 else None
    return result(primary)

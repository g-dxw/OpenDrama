"""Basil invoice sources preserve safe refunds and account-scoped audit links."""
from __future__ import annotations
import copy
import time
from urllib.parse import parse_qs, urlparse
import pytest
from app.modules.billing import commerce as commerce
from app.modules.billing import service as billing
from tests.test_commerce import commercial, membership_order


def payment_intent(identifier="pi_basil", amount=1999, **changes):
    value={"id":identifier,"object":"payment_intent","status":"succeeded","amount":amount,"amount_received":amount,"currency":"usd","customer":"cus_basil","livemode":False}
    value.update(changes)
    return value


def allocation(identifier="inpay_basil", pi="pi_basil", amount=1999, invoice="in_basil", **changes):
    value={"id":identifier,"object":"invoice_payment","invoice":invoice,"status":"paid","amount_paid":amount,"amount_requested":amount,"currency":"usd","livemode":False,"is_default":True,"payment":{"type":"payment_intent","payment_intent":pi}}
    value.update(changes)
    return value


def invoice_object(payments=None, **changes):
    stamp=int(time.time())
    value={"id":"in_basil","object":"invoice","status":"paid","amount_due":1999,"amount_paid":1999,"amount_remaining":0,"amount_overpaid":0,"currency":"usd","customer":"cus_basil","livemode":False,"subscription":"sub_basil","lines":{"data":[{"period":{"start":stamp,"end":stamp+30*86400}}]}}
    if payments is not None:value["payments"]={"object":"list","data":payments,"has_more":False}
    value.update(changes)
    return value


def test_basil_invoice_retains_single_paid_intent_for_refund(session, commercial, monkeypatch):
    user, _, _=commercial
    billing.get_billing_settings(session).billing_mode="subscription"
    session.commit()
    order=membership_order(session,user)
    order.subscription_ref="sub_basil"
    session.commit()
    pi=payment_intent()
    row=allocation(pi=pi)
    invoice=invoice_object([row])
    old_request=commerce.stripe_request
    def remote(db,code,path,data=None,method="POST",idempotency_key=None):
        assert code==order.account_code
        if path.startswith("/invoice_payments"):
            return {"object":"list","data":[allocation()],"has_more":False}
        if path=="/payment_intents/pi_basil":return copy.deepcopy(pi)
        return old_request(db,code,path,data,method,idempotency_key)
    monkeypatch.setattr(commerce,"stripe_request",remote)
    commerce.process_event(session,order.account_code,{"type":"invoice.paid","data":{"object":invoice}})
    session.refresh(order)
    assert order.status=="paid"
    assert order.payment_intent_id=="pi_basil"
    assert commerce.refund_eligibility(session,order)["eligible"] is True


class Provider:
    def __init__(self, rows=None, intents=None, reverse=None):
        self.rows=copy.deepcopy([allocation()] if rows is None else rows)
        self.intents=copy.deepcopy({"pi_basil":payment_intent()} if intents is None else intents)
        self.reverse=copy.deepcopy(reverse or {})
        self.calls=[]

    def __call__(self,path):
        self.calls.append(path)
        parsed=urlparse(path)
        if parsed.path.startswith("/payment_intents/"):
            return copy.deepcopy(self.intents[parsed.path.rsplit("/",1)[1]])
        assert parsed.path=="/invoice_payments", path
        query=parse_qs(parsed.query)
        if "payment[payment_intent]" in query:
            identifier=query["payment[payment_intent]"][0]
            rows=self.reverse.get(identifier,[row for row in self.rows if (row.get("payment") or {}).get("payment_intent")==identifier])
        else:
            assert query["invoice"]==["in_basil"]
            rows=self.rows
        return {"object":"list","data":copy.deepcopy(rows),"has_more":False}


def resolve(invoice=None,provider=None,**kwargs):
    from app.modules.billing.stripe_invoice_payments import resolve_invoice_payment
    provider=provider or Provider()
    return resolve_invoice_payment(invoice_object([allocation()]) if invoice is None else invoice,fetch=provider,expected_amount=1999,expected_currency="USD",livemode=False,**kwargs)


def test_expanded_basil_payment_is_verified_and_audit_never_keeps_secrets():
    pi=payment_intent(client_secret="must-not-persist",metadata={"secret":"must-not-persist"})
    provider=Provider(intents={"pi_basil":pi})
    result=resolve(invoice_object([allocation(pi=pi)]),provider)
    assert result.payment_intent_id=="pi_basil"
    assert result.payment_intent_ids==("pi_basil",)
    assert not result.requires_manual_review
    assert result.reason==""
    assert "/payment_intents/pi_basil" in provider.calls
    assert any("payment%5Bpayment_intent%5D=pi_basil" in path for path in provider.calls)
    assert "must-not-persist" not in __import__("json").dumps(result.evidence)
    assert result.evidence["payment_intents"][0]["invoice_ids"]==["in_basil"]


def test_unexpanded_invoice_queries_all_its_payments():
    provider=Provider()
    result=resolve(invoice_object(),provider)
    assert result.payment_intent_id=="pi_basil"
    assert parse_qs(urlparse(provider.calls[0]).query)["invoice"]==["in_basil"]


def test_inline_partial_list_is_completed_and_every_paid_source_is_retained():
    first=allocation("inpay_first","pi_first",1000)
    second=allocation("inpay_second","pi_second",999)
    provider=Provider([first,second],{"pi_first":payment_intent("pi_first",1000),"pi_second":payment_intent("pi_second",999)})
    original=provider.__call__
    def fetch(path):
        query=parse_qs(urlparse(path).query)
        if query.get("invoice"):
            assert query["starting_after"]==["inpay_first"]
            provider.calls.append(path)
            return {"data":[second],"has_more":False}
        return original(path)
    from app.modules.billing.stripe_invoice_payments import resolve_invoice_payment
    invoice=invoice_object([first]);invoice["payments"]["has_more"]=True
    result=resolve_invoice_payment(invoice,fetch=fetch,expected_amount=1999,expected_currency="USD",livemode=False)
    assert result.payment_intent_id is None and result.requires_manual_review
    assert result.payment_intent_ids==("pi_first","pi_second")
    assert len(result.evidence["payments"])==2
    assert any("starting_after=inpay_first" in path for path in provider.calls)


def test_reverse_payment_associations_are_paginated_and_shared_pi_is_manual():
    other=allocation("inpay_other","pi_basil",500,invoice="in_other")
    provider=Provider()
    original=provider.__call__
    def fetch(path):
        query=parse_qs(urlparse(path).query)
        if query.get("payment[payment_intent]"):
            provider.calls.append(path)
            return {"data":[other],"has_more":False} if query.get("starting_after") else {"data":[allocation()],"has_more":True}
        return original(path)
    from app.modules.billing.stripe_invoice_payments import resolve_invoice_payment
    result=resolve_invoice_payment(invoice_object([allocation()]),fetch=fetch,expected_amount=1999,expected_currency="USD",livemode=False)
    assert result.payment_intent_id is None and result.requires_manual_review
    assert result.payment_intent_ids==("pi_basil",)
    assert "shared" in result.reason
    assert result.evidence["payment_intents"][0]["invoice_ids"]==["in_basil","in_other"]


@pytest.mark.parametrize("changes",[
    {"status":"processing"},{"amount":2999},{"amount_received":1000},
    {"currency":"eur"},{"customer":"cus_other"},{"livemode":True},
])
def test_inconsistent_or_partial_payment_intent_is_never_automatic(changes):
    result=resolve(provider=Provider(intents={"pi_basil":payment_intent(**changes)}))
    assert result.payment_intent_id is None
    assert result.requires_manual_review and result.reason


@pytest.mark.parametrize("changes",[
    {"status":"open","amount_paid":None},{"status":"canceled","amount_paid":None},
    {"status":"unknown"},{"amount_paid":1000},{"amount_requested":2999},
    {"currency":"eur"},{"livemode":True},{"invoice":"in_other"},
])
def test_inconsistent_allocation_is_not_selected_as_full_refund_source(changes):
    row=allocation(**changes)
    result=resolve(invoice_object([row]),Provider(rows=[row]))
    assert result.payment_intent_id is None
    assert result.requires_manual_review and result.reason


def test_reverse_current_allocation_must_match_the_event():
    provider=Provider(reverse={"pi_basil":[allocation(amount=1000)]})
    result=resolve(provider=provider)
    assert result.payment_intent_id is None and result.requires_manual_review
    assert result.payment_intent_ids==("pi_basil",)
    assert "current provider associations" in result.reason


def test_unknown_payment_type_is_retained_for_manual_audit():
    row=allocation(payment={"type":"payment_record","payment_record":"pyr_offstripe"})
    result=resolve(invoice_object([row]),Provider(rows=[row]))
    assert result.payment_intent_id is None and result.requires_manual_review
    assert result.evidence["payments"][0]["payment_reference"]=="pyr_offstripe"
    assert result.payment_intent_ids==()


@pytest.mark.parametrize("changes",[
    {"status":"open"},{"status":{"client_secret":"not-an-enum"}},
    {"amount_paid":1000},{"currency":"eur"},{"livemode":True},
    {"amount_remaining":1},{"amount_overpaid":1},{"amount_paid_off_stripe":1},
    {"paid_out_of_band":True},{"amount_due":1000},
])
def test_invoice_money_and_mode_contradictions_require_manual_review(changes):
    result=resolve(invoice_object([allocation()],**changes))
    assert result.payment_intent_id is None and result.requires_manual_review
    assert "not-an-enum" not in __import__("json").dumps(result.evidence)


def test_legacy_pointer_preserves_old_signed_event_without_new_requests():
    invoice=invoice_object(payment_intent="pi_legacy")
    invoice.pop("status");invoice.pop("livemode")
    def no_fetch(path):raise AssertionError("Legacy invoice unexpectedly fetched "+path)
    result=resolve(invoice,no_fetch)
    assert result.payment_intent_id=="pi_legacy"
    assert result.payment_intent_ids==("pi_legacy",)
    assert not result.requires_manual_review
    assert result.evidence["source"]=="legacy_invoice_pointer"


def test_expanded_legacy_contradiction_is_manual_without_extra_requests():
    def no_fetch(path):raise AssertionError(path)
    result=resolve(invoice_object(payment_intent=payment_intent(amount_received=1)),no_fetch)
    assert result.payment_intent_id is None and result.requires_manual_review
    assert result.payment_intent_ids==("pi_basil",)


def test_modern_api_failure_propagates_instead_of_acknowledging_no_source():
    from app.modules.billing.service import BillingError
    def unavailable(path):raise BillingError(503,"provider unavailable")
    with pytest.raises(BillingError,match="unavailable"):
        resolve(invoice_object(),unavailable)


@pytest.mark.parametrize("reply",[{},{"data":[]},{"data":[],"has_more":True}])
def test_incomplete_provider_list_is_retryable(reply):
    from app.modules.billing.service import BillingError
    with pytest.raises(BillingError) as caught:
        resolve(invoice_object(),lambda path:reply)
    assert caught.value.status_code==502


def test_repeated_pagination_cursor_is_retryable():
    from app.modules.billing.service import BillingError
    with pytest.raises(BillingError,match="pagination"):
        resolve(invoice_object(),lambda path:{"data":[allocation()],"has_more":True})


def test_failure_during_pagination_does_not_return_a_partial_source():
    from app.modules.billing.service import BillingError
    first=allocation();invoice=invoice_object([first]);invoice["payments"]["has_more"]=True
    def unavailable(path):raise BillingError(503,"next page unavailable")
    with pytest.raises(BillingError,match="next page"):
        resolve(invoice,unavailable)


def test_legacy_out_of_band_without_a_pointer_stays_manual_without_new_api():
    def no_fetch(path):raise AssertionError(path)
    result=resolve(invoice_object(paid_out_of_band=True),no_fetch)
    assert result.payment_intent_id is None and result.requires_manual_review
    assert result.payment_intent_ids==()
    assert result.evidence["source"]=="legacy_out_of_band"


def test_client_secret_is_not_accepted_or_retained_as_a_payment_identifier():
    invoice=invoice_object(payment_intent="pi_basil_secret_do_not_persist")
    result=resolve(invoice,Provider())
    assert result.payment_intent_id=="pi_basil"
    assert "do_not_persist" not in __import__("json").dumps(result.evidence)

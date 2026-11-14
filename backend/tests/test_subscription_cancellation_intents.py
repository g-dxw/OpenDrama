"""Each cancel/resume cycle has a durable, shared Stripe cancellation intent."""
from copy import deepcopy
import importlib.util
from pathlib import Path
from io import StringIO
import time

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from app.modules.billing import commerce as c
from app.modules.billing import service as billing
from app.modules.billing.models import Subscription
from app.modules.billing.commerce_models import StripeSubscriptionLifecycle
from app.modules.billing.service import BillingError
from app.modules.wallet.models import WalletAccount
from tests.test_commerce import commercial
from tests.test_commerce_lifecycle import invoice, lifecycle, subscription_order


@pytest.fixture
def cancellation(session, commercial, monkeypatch):
    user, actor, _ = commercial
    order = subscription_order(session, user)
    c.process_event(session, "env", invoice(order))
    row = session.scalar(select(Subscription).where(Subscription.user_id == user.id))
    original = c.stripe_request
    state = dict(cancel=False, lose_response=False)
    calls, cache = [], {}
    def remote(db, code, path, data=None, method="POST", idempotency_key=None):
        if path == "/subscriptions/"+row.stripe_subscription_id:
            if method == "GET":
                return dict(id=row.stripe_subscription_id, status="active", cancel_at_period_end=state["cancel"])
            if method == "POST":
                calls.append((code, idempotency_key))
                if idempotency_key not in cache:
                    state["cancel"] = True
                    cache[idempotency_key] = dict(id=row.stripe_subscription_id, status="active", cancel_at_period_end=True)
                if state["lose_response"]:
                    state["lose_response"] = False
                    raise BillingError(503, "unknown cancellation outcome")
                return deepcopy(cache[idempotency_key])
        return original(db, code, path, data, method, idempotency_key)
    monkeypatch.setattr(c, "stripe_request", remote)
    return user, actor, order, row, state, calls


def cancel(db, user, actor, path):
    if path == "user":
        billing.cancel_subscription(db, user.id)
    else:
        billing.revoke_membership(db, user_id=user.id, mode="period_end",
            reason="Stop future renewal", actor_id=actor.id)


@pytest.mark.parametrize("first,second", [("user", "user"), ("admin", "admin"), ("user", "admin"), ("admin", "user")])
def test_resume_starts_a_new_cancellation_epoch(session, cancellation, first, second):
    user, actor, order, row, state, calls = cancellation
    stamp = int(time.time())
    cancel(session, user, actor, first)
    original_key = calls[-1][1]
    c.process_event(session, "env", lifecycle(order, kind="customer.subscription.updated", created=stamp+1, cancel=True))
    state["cancel"] = False  # Billing Portal/Dashboard resumes this subscription.
    c.process_event(session, "env", lifecycle(order, kind="customer.subscription.updated", created=stamp+2, cancel=False))
    assert row.canceled_at is None
    cancel(session, user, actor, second)
    assert state["cancel"], "Stripe must receive a new logical cancellation, not replay a cached success"
    assert calls[-1][1] != original_key
    assert row.canceled_at is not None


@pytest.mark.parametrize("first,second", [("user", "user"), ("admin", "admin"), ("user", "admin"), ("admin", "user")])
def test_unknown_result_survives_rollback_and_cross_path_retry(session, cancellation, engine, first, second):
    user, actor, _, row, state, calls = cancellation
    state["lose_response"] = True
    with pytest.raises(BillingError, match="unknown cancellation outcome"):
        cancel(session, user, actor, first)
    first_key = calls[-1][1]
    session.rollback()
    factory = sessionmaker(bind=engine)
    with factory() as check:
        persisted = check.get(Subscription, row.id)
        assert persisted.cancellation_context["key"] == first_key
        assert not persisted.cancellation_context.get("confirmed_at")
        assert persisted.canceled_at is None
    cancel(session, user, actor, second)
    assert calls[-1][1] == first_key
    assert row.canceled_at is not None and state["cancel"]


@pytest.mark.parametrize("fact", ["initial", "older", "same_second", "duplicate"])
def test_non_resume_lifecycle_snapshots_do_not_rotate_the_pending_intent(session, cancellation, fact):
    user, actor, order, row, state, calls = cancellation
    stamp = int(time.time())
    if fact in {"older", "same_second", "duplicate"}:
        c.process_event(session, "env", lifecycle(order, kind="customer.subscription.updated", created=stamp-10, cancel=True))
    state["lose_response"] = True
    with pytest.raises(BillingError):
        cancel(session, user, actor, "user")
    session.rollback()
    original_key = calls[-1][1]
    created = {"initial":stamp+1, "older":stamp-20, "same_second":stamp-10, "duplicate":stamp-10}[fact]
    event = lifecycle(order, kind="customer.subscription.updated", created=created, cancel=False)
    c.process_event(session, "env", event)
    if fact == "duplicate":
        c.process_event(session, "env", event)
    cancel(session, user, actor, "user")
    assert calls[-1][1] == original_key
    assert state["cancel"]


def test_accepted_but_pre_request_snapshot_does_not_clear_a_confirmed_intent(session, cancellation):
    user, actor, order, _, _, calls = cancellation
    stamp = int(time.time())
    c.process_event(session, "env", lifecycle(order, kind="customer.subscription.updated", created=stamp-10, cancel=True))
    cancel(session, user, actor, "user")
    original_key = calls[-1][1]
    c.process_event(session, "env", lifecycle(order, kind="customer.subscription.updated", created=stamp-1, cancel=False))
    cancel(session, user, actor, "user")
    assert calls[-1][1] == original_key


def test_same_request_second_is_not_proof_of_a_new_resume_cycle(session, cancellation):
    user, actor, order, row, _, calls = cancellation
    cancel(session, user, actor, "user")
    original_key = calls[-1][1]
    stamp = row.cancellation_context["confirmed_at"]
    c.process_event(session, "env", lifecycle(order, kind="customer.subscription.updated", created=stamp, cancel=False))
    cancel(session, user, actor, "user")
    assert calls[-1][1] == original_key
    # A later accepted resume can still retire the old confirmed intent.
    c.process_event(session, "env", lifecycle(
        order, kind="customer.subscription.updated",
        created=row.cancellation_context["verified_at"] + 1, cancel=False,
    ))
    cancel(session, user, actor, "user")
    assert calls[-1][1] != original_key


def test_cancellation_stays_on_original_disabled_account(session, cancellation):
    user, actor, _, row, _, calls = cancellation
    c.save_account(session, "env", dict(status="disabled"), actor.id)
    c.save_account(session, "replacement", dict(name="Replacement", status="active", is_default=True,
        secret_key="sk_test_replacement", webhook_secret="whsec_replacement"), actor.id)
    cancel(session, user, actor, "admin")
    assert calls[-1][0] == row.account_code == "env"


@pytest.mark.parametrize("path", ["user", "admin"])
def test_cancellation_provider_call_holds_wallet_and_subscription_locks(
    session, cancellation, engine, monkeypatch, path,
):
    user, actor, _, row, _, _ = cancellation
    original = c.stripe_request
    factory = sessionmaker(bind=engine)
    checks = []
    def remote(db, code, target, data=None, method="POST", idempotency_key=None):
        if target == "/subscriptions/"+row.stripe_subscription_id and method == "POST":
            for model, key in ((WalletAccount, user.id), (Subscription, row.id)):
                with factory() as contender:
                    with pytest.raises(DBAPIError) as error:
                        contender.get(model, key, with_for_update={"nowait":True})
                    assert (getattr(error.value.orig,"sqlstate",None) or getattr(error.value.orig,"pgcode",None)) == "55P03"
                checks.append(model.__tablename__)
        return original(db, code, target, data, method, idempotency_key)
    monkeypatch.setattr(c, "stripe_request", remote)
    cancel(session, user, actor, path)
    cancel(session, user, actor, path)
    assert checks == ["wallet_accounts", "subscriptions"] * 2


def test_cancellation_migration_preserves_existing_subscription_values(engine):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    filename = Path(__file__).resolve().parents[1] / "alembic/versions/a1114cancel_intent_subscription_cancellation.py"
    spec = importlib.util.spec_from_file_location("cancellation_intent_migration", filename)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as connection:
        # A temporary legacy table shadows public.subscriptions on this connection.
        connection.execute(text("CREATE TEMP TABLE subscriptions (id bigint PRIMARY KEY, status varchar(16), canceled_at timestamptz) ON COMMIT DROP"))
        connection.execute(text("INSERT INTO subscriptions VALUES (1,'active',NULL),(2,'active','2025-01-02T00:00:00Z'),(3,'canceled','2025-02-03T00:00:00Z')"))
        before = connection.execute(text("SELECT * FROM subscriptions ORDER BY id")).all()
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        assert connection.execute(text("SELECT cancellation_context FROM subscriptions ORDER BY id")).scalars().all() == [{}, {}, {}]
        assert connection.execute(text("SELECT id,status,canceled_at FROM subscriptions ORDER BY id")).all() == before
        connection.execute(text("INSERT INTO subscriptions(id,status) VALUES (4,'active')"))
        assert connection.execute(text("SELECT cancellation_context FROM subscriptions WHERE id=4")).scalar_one() == {}
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        assert connection.execute(text("SELECT id,status,canceled_at FROM subscriptions WHERE id<4 ORDER BY id")).all() == before


def test_cancellation_downgrade_preserves_durable_pending_intent(session, cancellation, engine):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    user, actor, _, row, state, _ = cancellation
    state["lose_response"] = True
    with pytest.raises(BillingError):
        cancel(session, user, actor, "user")
    session.rollback()
    stored = deepcopy(row.cancellation_context)
    session.rollback()  # Release the reader before the migration takes its table lock.
    filename = Path(__file__).resolve().parents[1] / "alembic/versions/a1114cancel_intent_subscription_cancellation.py"
    spec = importlib.util.spec_from_file_location("cancellation_intent_downgrade", filename)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(RuntimeError, match="Cannot discard durable subscription cancellation intents"):
                migration.downgrade()
    session.expire_all()
    assert session.get(Subscription, row.id).cancellation_context == stored


@pytest.mark.parametrize("path", ["user", "admin"])
def test_unknown_cancel_then_resume_before_late_cancel_proof_retires_old_key(
    session, cancellation, path,
):
    user, actor, order, row, state, calls = cancellation
    state["lose_response"] = True
    with pytest.raises(BillingError):
        cancel(session, user, actor, path)
    session.rollback()
    key = calls[-1][1]
    stamp = row.cancellation_context["requested_at"]
    state["cancel"] = False
    c.process_event(session, "env", lifecycle(order, kind="customer.subscription.updated", created=stamp+2, cancel=False))
    assert row.cancellation_context["key"] == key
    c.process_event(session, "env", lifecycle(order, kind="customer.subscription.updated", created=stamp+1, cancel=True))
    fact = session.get(StripeSubscriptionLifecycle, ("env", row.stripe_subscription_id))
    assert not fact.cancel_at_period_end and fact.last_event_created == stamp+2
    assert row.canceled_at is None
    assert row.cancellation_context == {}, "late cancel proof plus newer current resume must retire the old intent"
    cancel(session, user, actor, path)
    assert state["cancel"] and calls[-1][1] != key


def test_cancellation_downgrade_holds_table_lock_before_preflight(
    session, cancellation, engine, monkeypatch,
):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    _, _, _, row, _, _ = cancellation
    subscription_id = row.id
    filename = Path(__file__).resolve().parents[1] / "alembic/versions/a1114cancel_intent_subscription_cancellation.py"
    spec = importlib.util.spec_from_file_location("cancellation_intent_lock", filename)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    # This connection rolls transactional DDL back even when the test succeeds.
    session.rollback()
    with engine.connect() as connection:
        transaction = connection.begin()
        original_scalar = connection.scalar
        probes = []
        def preflight(statement, *args, **kwargs):
            result = original_scalar(statement, *args, **kwargs)
            if "SELECT EXISTS" in str(statement):
                with engine.connect() as contender:
                    with pytest.raises(DBAPIError) as error:
                        contender.execute(text("SET LOCAL lock_timeout='250ms'"))
                        contender.execute(text("UPDATE subscriptions SET cancellation_context='{\"key\":\"racing-intent\"}'::jsonb WHERE id=:id"), {"id":subscription_id})
                        contender.commit()
                    assert (getattr(error.value.orig,"sqlstate",None) or getattr(error.value.orig,"pgcode",None)) == "55P03"
                probes.append(True)
            return result
        monkeypatch.setattr(connection, "scalar", preflight)
        try:
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
            assert probes == [True]
        finally:
            transaction.rollback()
    session.expire_all()
    assert session.get(Subscription, subscription_id).cancellation_context == {}


def test_cancellation_downgrade_rejects_offline_preflight():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    filename = Path(__file__).resolve().parents[1] / "alembic/versions/a1114cancel_intent_subscription_cancellation.py"
    spec = importlib.util.spec_from_file_location("cancellation_intent_offline", filename)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    context = MigrationContext.configure(url="postgresql://", opts={"as_sql":True,"output_buffer":StringIO()})
    with Operations.context(context):
        with pytest.raises(RuntimeError, match="Cannot downgrade.*offline"):
            migration.downgrade()


@pytest.mark.parametrize("path", ["user", "admin"])
def test_unknown_cancel_resume_then_retry_before_cancel_proof_cannot_report_success(
    session, cancellation, path,
):
    user, actor, order, row, state, calls = cancellation
    state["lose_response"] = True
    with pytest.raises(BillingError):
        cancel(session, user, actor, path)
    session.rollback()
    key = calls[-1][1]
    stamp = row.cancellation_context["requested_at"]
    state["cancel"] = False
    c.process_event(session, "env", lifecycle(order, kind="customer.subscription.updated", created=stamp+2, cancel=False))
    with pytest.raises(BillingError) as error:
        cancel(session, user, actor, path)
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "subscription_resumed_retry"
    assert calls[-1][1] == key, "the uncertain attempt must first reuse its original provider key"
    session.rollback(); session.refresh(row)
    assert row.cancellation_context == {} and row.canceled_at is None
    assert not state["cancel"]
    cancel(session, user, actor, path)
    assert calls[-1][1] != key and state["cancel"]


@pytest.mark.parametrize("failure", ["network", "wrong_id", "missing_cancel", "not_object", "unknown_status"])
def test_unconfirmed_current_provider_state_preserves_original_cancellation_key(
    session, cancellation, monkeypatch, failure,
):
    user, actor, _, row, _, calls = cancellation
    original = c.stripe_request
    def unavailable(db, code, target, data=None, method="POST", idempotency_key=None):
        if method == "GET" and target == "/subscriptions/"+row.stripe_subscription_id:
            if failure == "network":raise BillingError(503,"provider unavailable")
            if failure == "not_object":return []
            response = dict(id=row.stripe_subscription_id, status="active", cancel_at_period_end=True)
            if failure == "wrong_id":response["id"] = "sub_someone_else"
            if failure == "missing_cancel":response.pop("cancel_at_period_end")
            if failure == "unknown_status":response["status"] = "unrecognized_state"
            return response
        return original(db, code, target, data, method, idempotency_key)
    monkeypatch.setattr(c,"stripe_request",unavailable)
    with pytest.raises(BillingError):
        cancel(session,user,actor,"user")
    session.rollback();session.refresh(row)
    key = calls[-1][1]
    assert row.cancellation_context["key"] == key
    assert not row.cancellation_context.get("confirmed_at")
    assert row.canceled_at is None
    monkeypatch.setattr(c,"stripe_request",original)
    cancel(session,user,actor,"user")
    assert calls[-1][1] == key and row.canceled_at is not None


def test_prior_epoch_lifecycle_replays_cannot_retire_new_cancellation(session, cancellation):
    user, actor, order, row, state, calls = cancellation
    cancel(session,user,actor,"user")
    stamp = row.cancellation_context["requested_at"]
    cancel_event = lifecycle(order,kind="customer.subscription.updated",created=stamp+1,cancel=True)
    resume_event = lifecycle(order,kind="customer.subscription.updated",created=stamp+2,cancel=False)
    c.process_event(session,"env",cancel_event)
    state["cancel"] = False
    c.process_event(session,"env",resume_event)
    cancel(session,user,actor,"user")
    new_key = calls[-1][1]
    c.process_event(session,"env",cancel_event)
    c.process_event(session,"env",resume_event)
    assert row.cancellation_context["key"] == new_key
    cancel(session,user,actor,"user")
    assert calls[-1][1] == new_key and state["cancel"]

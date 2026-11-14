"""Real commercial-schema rollback refuses to erase paid entitlement evidence."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa


@pytest.fixture
def commercial_migration(engine, monkeypatch):
    source = engine.url.database
    assert source and re.fullmatch(r"[a-z][a-z0-9_]{0,40}", source)
    name = source + "_down_" + uuid4().hex[:8]
    assert len(name) <= 63 and name.startswith(source + "_down_")
    admin = sa.create_engine(engine.url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    target_url = engine.url.set(database=name)
    target = None
    created = False
    try:
        with admin.connect() as db:
            db.execute(sa.text('CREATE DATABASE "' + name + '"'))
            created = True
        backend = Path(__file__).resolve().parents[1]
        config = Config(str(backend / "alembic.ini"))
        config.set_main_option("script_location", str(backend / "alembic"))
        config.set_main_option("sqlalchemy.url", target_url.render_as_string(hide_password=False).replace("%", "%%"))
        with monkeypatch.context() as local:
            local.setenv("ALEMBIC_DATABASE_URL", target_url.render_as_string(hide_password=False))
            # Pin this migration's parent: later revisions may be developed in parallel.
            command.upgrade(config, "a1114content")
            target = sa.create_engine(target_url)
            with target.begin() as db:
                user_id = db.scalar(sa.text("INSERT INTO users(email,display_name,avatar_url,gender,bio,region,locale,following_count,follower_count,received_likes,reminder_opt_in) VALUES ('downgrade@example.com','Historical viewer','','unspecified','','','en',0,0,0,false) RETURNING id"))
                db.execute(sa.text("INSERT INTO wallet_accounts(user_id,balance) VALUES (:user,321)"), {"user": user_id})
                plan_id = db.scalar(sa.text("INSERT INTO plans(code,name_en,name_zh,status) VALUES ('monthly','Historical Monthly','原月卡','active') RETURNING id"))
                version_id = db.scalar(sa.text("INSERT INTO plan_versions(plan_id,version,price_cents,currency,benefits,note,stripe_price_id) VALUES (:plan,1,1999,'USD','{}','Original price','price_original') RETURNING id"), {"plan": plan_id})
                order_id = db.scalar(sa.text("INSERT INTO orders(order_no,user_id,plan_version_id,amount_cents,currency,billing_mode,status,external_txn_id,paid_at) VALUES ('ORD-LEGACY-DOWN',:user,:version,1999,'USD','subscription','paid','cs_original',now()) RETURNING id"), {"user": user_id, "version": version_id})
            command.upgrade(config, "a1114commerce")
            yield target, config, {"user": user_id, "version": version_id, "legacy_order": order_id}
    finally:
        if target is not None:
            target.dispose()
        if created:
            with admin.connect() as db:
                owner = db.execute(sa.text("SELECT pg_get_userbyid(datdba),current_user FROM pg_database WHERE datname=:name"), {"name": name}).first()
                assert owner is not None and owner[0] == owner[1] and name.startswith(source + "_down_")
                db.execute(sa.text('DROP DATABASE "' + name + '" WITH (FORCE)'))
        admin.dispose()


def _paid_one_time_order(db, ids):
    # No settlement, coins or snapshot markers: the grant itself must protect
    # a paid one-time pass that the old subscriptions table cannot represent.
    return db.scalar(sa.text("INSERT INTO orders(order_no,user_id,plan_version_id,amount_cents,currency,billing_mode,status,external_txn_id,paid_at) VALUES ('ORD-ONE-TIME-DOWN',:user,:version,1999,'USD','one_time','paid','cs_one_time',now()) RETURNING id"), ids)


def _assert_refused_without_changes(target, config):
    tables = ("orders", "wallet_accounts", "subscriptions", "plan_versions", "coin_sources", "coin_allocations", "membership_grants", "commerce_settlements", "commerce_refunds")
    with target.connect() as db:
        before = {table: db.execute(sa.text("SELECT * FROM " + table + " ORDER BY 1")).all() for table in tables}
    with pytest.raises(RuntimeError, match="Cannot downgrade a1114commerce.*commercial"):
        command.downgrade(config, "a1114content")
    with target.connect() as db:
        assert db.scalar(sa.text("SELECT version_num FROM alembic_version")) == "a1114commerce"
        assert "membership_grants" in sa.inspect(db).get_table_names()
        for table, rows in before.items():
            assert db.execute(sa.text("SELECT * FROM " + table + " ORDER BY 1")).all() == rows


@pytest.mark.parametrize(("status", "consumed", "days_ago"), [
    ("active", False, 0),
    ("active", True, 0),
    ("refund_pending", False, 0),
    ("refunded", False, 0),
    ("active", False, 60),
])
def test_paid_one_time_grants_block_irreversible_downgrade(commercial_migration, status, consumed, days_ago):
    target, config, ids = commercial_migration
    starts = datetime.now(timezone.utc) - timedelta(days=days_ago)
    with target.begin() as db:
        order_id = _paid_one_time_order(db, ids)
        db.execute(sa.text("INSERT INTO membership_grants(order_id,user_id,starts_at,ends_at,status,access_consumed) VALUES (:order,:user,:starts,:ends,:status,:consumed)"), {"order": order_id, "user": ids["user"], "starts": starts, "ends": starts + timedelta(days=30), "status": status, "consumed": consumed})
    _assert_refused_without_changes(target, config)


@pytest.mark.parametrize("evidence", ["coin_source", "settlement", "refund", "managed_subscription", "paid_coins", "paid_snapshot"])
def test_other_paid_commerce_evidence_blocks_downgrade(commercial_migration, evidence):
    target, config, ids = commercial_migration
    with target.begin() as db:
        order_id = _paid_one_time_order(db, ids)
        values = {**ids, "order": order_id}
        if evidence == "coin_source":
            db.execute(sa.text("INSERT INTO coin_sources(user_id,order_id,original,remaining,reserved) VALUES (:user,:order,100,100,0)"), values)
        elif evidence == "settlement":
            db.execute(sa.text("INSERT INTO commerce_settlements(key,order_id) VALUES ('settled-one-time',:order)"), values)
        elif evidence == "refund":
            db.execute(sa.text("INSERT INTO commerce_refunds(order_id,idempotency_key,status,reason,error) VALUES (:order,'refund-one-time','pending','Requested','')"), values)
        elif evidence == "paid_coins":
            db.execute(sa.text("UPDATE orders SET product_type='coins',plan_version_id=NULL WHERE id=:order"), values)
        elif evidence == "paid_snapshot":
            db.execute(sa.text("UPDATE orders SET product_snapshot=CAST(:snapshot AS jsonb) WHERE id=:order"), {**values, "snapshot": '{"days":30}'})
        else:
            db.execute(sa.text("INSERT INTO subscriptions(user_id,plan_id,plan_version_id,status,current_period_start,current_period_end,billing_mode,managed_commerce) SELECT :user,plan_id,id,'active',now(),now()+interval '30 days','subscription',true FROM plan_versions WHERE id=:version"), values)
    _assert_refused_without_changes(target, config)


def test_legacy_only_commerce_schema_can_downgrade_and_upgrade(commercial_migration):
    target, config, ids = commercial_migration
    with target.connect() as db:
        assert db.execute(sa.text("SELECT order_id,original,remaining,reserved FROM coin_sources")).one() == (None, 321, 321, 0)
    with target.begin() as db:
        # The existing rollback path for abandoned, unpaid coin orders remains.
        for status in ("created", "failed"):
            db.execute(sa.text("INSERT INTO orders(order_no,user_id,plan_version_id,amount_cents,currency,billing_mode,status,product_type,product_snapshot) VALUES (:no,:user,NULL,499,'USD','one_time',:status,'coins',CAST(:snapshot AS jsonb))"), {"no": "UNPAID-" + status, "user": ids["user"], "status": status, "snapshot": '{"coins":50}'})
    command.downgrade(config, "a1114content")
    with target.connect() as db:
        assert db.scalar(sa.text("SELECT version_num FROM alembic_version")) == "a1114content"
        assert "membership_grants" not in sa.inspect(db).get_table_names()
        assert db.execute(sa.text("SELECT id,status,plan_version_id,external_txn_id FROM orders")).one() == (ids["legacy_order"], "paid", ids["version"], "cs_original")
        assert db.scalar(sa.text("SELECT balance FROM wallet_accounts WHERE user_id=:user"), ids) == 321
        assert not next(column for column in sa.inspect(db).get_columns("orders") if column["name"] == "plan_version_id")["nullable"]
    command.upgrade(config, "a1114commerce")
    with target.connect() as db:
        assert db.scalar(sa.text("SELECT version_num FROM alembic_version")) == "a1114commerce"
        assert db.execute(sa.text("SELECT order_id,original,remaining,reserved FROM coin_sources")).one() == (None, 321, 321, 0)


def test_offline_downgrade_cannot_skip_commercial_data_preflight(commercial_migration):
    target, config, _ids = commercial_migration
    with pytest.raises(RuntimeError, match="Cannot downgrade a1114commerce offline.*commercial"):
        command.downgrade(config, "a1114commerce:a1114content", sql=True)
    with target.connect() as db:
        assert db.scalar(sa.text("SELECT version_num FROM alembic_version")) == "a1114commerce"

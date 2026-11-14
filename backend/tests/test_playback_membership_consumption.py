"""Playback must account for every membership needed to authorize a signed stream.

Stripe is a controlled fixture; wallet, unlock, grant and refund state use PostgreSQL.
"""
import pytest
from sqlalchemy import select

from app.modules.billing import commerce as commerce
from app.modules.billing import service as billing
from app.modules.billing.commerce_models import CoinAllocation, CoinSource, CommerceRefund, MembershipGrant
from app.modules.billing.service import BillingError
from app.modules.playback import service as playback
from app.modules.wallet import service as wallet
from app.modules.wallet.models import WalletAccount
from tests.test_commerce import commercial, membership_order, pay, purchase


def paid_membership_with_older_coins(db, commercial, *, legacy=None):
    user, actor, calls = commercial
    billing.get_billing_settings(db).billing_mode = "one_time"
    db.commit()
    coins = purchase(db, user, "coins-before-membership")
    pay(db, coins)
    if legacy is not None:
        legacy(user)
    membership = membership_order(db, user)
    pay(db, membership)
    source = db.scalar(select(CoinSource).where(CoinSource.order_id == membership.id))
    grant = db.scalar(select(MembershipGrant).where(MembershipGrant.order_id == membership.id))
    assert source.original == source.remaining == 25 and not grant.access_consumed
    assert db.get(WalletAccount, user.id).balance == 145
    return user, actor, calls, coins, membership, source, grant


def published_episode(db, make, *, access="members", pays=True, free=0, number=1, override=None):
    drama = make.drama(status="published", free=free, price=60)
    drama.access_mode = access
    drama.member_pays_coins = pays
    episode = make.episode(drama, number=number, status="published")
    episode.coin_price = override
    db.commit()
    return drama, episode


def authorize(db, user, drama, episode):
    return playback.authorize(db, user_id=user.id, drama_id=drama.id, episode_id=episode.id)


def test_member_only_coin_unlock_consumes_membership_before_signing(session, commercial, make, monkeypatch):
    user, _, _, coins, _, source, grant = paid_membership_with_older_coins(session, commercial)
    drama, episode = published_episode(session, make)
    result = wallet.unlock_episode(session, user_id=user.id, episode_id=episode.id, idempotency_key="old-coins-unlock")
    assert result.source == "coin"
    allocation = session.scalar(select(CoinAllocation))
    old_source = session.scalar(select(CoinSource).where(CoinSource.order_id == coins.id))
    assert allocation.source_id == old_source.id and allocation.amount == 60
    assert source.original == source.remaining == 25
    sign = playback.sign_path
    def checked_sign(*args, **kwargs):
        session.refresh(grant)
        assert grant.access_consumed, "membership must be consumed before a stream token is minted"
        return sign(*args, **kwargs)
    monkeypatch.setattr(playback, "sign_path", checked_sign)
    authorized = authorize(session, user, drama, episode)
    assert authorized["allowed"] and "token=" in authorized["url"]
    assert source.original == source.remaining == 25
    assert grant.access_consumed


def test_member_only_coin_playback_blocks_refund_of_used_membership(session, commercial, make):
    user, actor, calls, _, membership, source, grant = paid_membership_with_older_coins(session, commercial)
    drama, episode = published_episode(session, make)
    wallet.unlock_episode(session, user_id=user.id, episode_id=episode.id, idempotency_key="coins-and-membership")
    assert authorize(session, user, drama, episode)["allowed"]
    assert source.remaining == source.original
    with pytest.raises(BillingError, match="consumed"):
        commerce.request_refund(session, membership.order_no, actor.id, "used access", "refund-used-membership")
    session.rollback()
    assert not session.scalar(select(CommerceRefund))
    assert not [call for call in calls if call[1] == "/refunds"]
    session.refresh(grant)
    assert grant.access_consumed and grant.status == "active"


def test_public_coin_unlock_does_not_consume_unused_membership(session, commercial, make):
    user, actor, _, _, membership, source, grant = paid_membership_with_older_coins(session, commercial)
    drama, episode = published_episode(session, make, access="public")
    wallet.unlock_episode(session, user_id=user.id, episode_id=episode.id, idempotency_key="public-unlock")
    assert authorize(session, user, drama, episode)["allowed"]
    session.refresh(grant)
    assert not grant.access_consumed and source.remaining == source.original
    assert commerce.request_refund(session, membership.order_no, actor.id, "unused membership", "refund-unused-membership")["status"] == "succeeded"
    assert session.get(WalletAccount, user.id).balance == 60


@pytest.mark.parametrize("preview,number,override", [(1, 1, None), (0, 2, 0)])
def test_free_member_drama_preview_does_not_consume_membership(session, commercial, make, preview, number, override):
    user, actor, _, _, membership, _, grant = paid_membership_with_older_coins(session, commercial)
    drama, episode = published_episode(session, make, free=preview, number=number, override=override)
    assert authorize(session, user, drama, episode)["allowed"]
    session.refresh(grant)
    assert not grant.access_consumed
    assert commerce.request_refund(session, membership.order_no, actor.id, "free preview only", "refund-preview-only")["status"] == "succeeded"


@pytest.mark.parametrize("access", ["public", "members"])
def test_member_free_playback_still_consumes_membership(session, commercial, make, access):
    user, actor, _, _, membership, _, grant = paid_membership_with_older_coins(session, commercial)
    drama, episode = published_episode(session, make, access=access, pays=False)
    assert authorize(session, user, drama, episode)["allowed"]
    session.refresh(grant)
    assert grant.access_consumed
    with pytest.raises(BillingError, match="consumed"):
        commerce.request_refund(session, membership.order_no, actor.id, "membership was used", "refund-member-free")


def test_frozen_membership_cannot_authorize_previously_coin_unlocked_episode(session, commercial, make, monkeypatch):
    user, actor, _, _, membership, _, grant = paid_membership_with_older_coins(session, commercial)
    drama, episode = published_episode(session, make)
    wallet.unlock_episode(session, user_id=user.id, episode_id=episode.id, idempotency_key="unlock-before-freeze")
    monkeypatch.setattr(commerce, "stripe_request", lambda *a, **k: {"id": "re_pending", "status": "pending"})
    commerce.request_refund(session, membership.order_no, actor.id, "unused before playback", "refund-before-playback")
    assert grant.status == "refund_pending"
    authorized = authorize(session, user, drama, episode)
    assert not authorized["allowed"] and authorized["reason"] == "membership_required"
    assert "url" not in authorized and "subtitle" not in authorized and "subtitles" not in authorized
    assert not grant.access_consumed


def test_refund_winning_between_access_check_and_consumption_prevents_signing(session, commercial, make, monkeypatch):
    user, actor, _, _, membership, _, grant = paid_membership_with_older_coins(session, commercial)
    drama, episode = published_episode(session, make)
    wallet.unlock_episode(session, user_id=user.id, episode_id=episode.id, idempotency_key="unlock-before-race")
    monkeypatch.setattr(commerce, "stripe_request", lambda *a, **k: {"id": "re_race", "status": "pending"})
    active = playback._active_subscription
    def stale_membership(db, viewer):
        was_active = active(db, viewer)
        assert was_active
        # Interleave a committed refund after the initial read but before the
        # existing wallet/grant lock revalidation in mark_membership_consumed.
        commerce.request_refund(db, membership.order_no, actor.id, "refund won race", "refund-race")
        return was_active
    monkeypatch.setattr(playback, "_active_subscription", stale_membership)
    monkeypatch.setattr(playback, "sign_path", lambda *a, **k: pytest.fail("must not sign a stream after its required membership froze"))
    authorized = authorize(session, user, drama, episode)
    assert not authorized["allowed"] and "url" not in authorized
    session.refresh(grant)
    assert grant.status == "refund_pending" and not grant.access_consumed


def test_existing_legacy_membership_does_not_consume_a_future_purchased_grant(session, commercial, make):
    user, actor, _, _, membership, source, grant = paid_membership_with_older_coins(
        session, commercial, legacy=make.subscription
    )
    assert grant.starts_at > commerce.now()
    drama, episode = published_episode(session, make)
    wallet.unlock_episode(session, user_id=user.id, episode_id=episode.id, idempotency_key="legacy-authorized-unlock")
    assert authorize(session, user, drama, episode)["allowed"]
    session.refresh(grant)
    assert not grant.access_consumed and source.remaining == source.original
    assert commerce.request_refund(session, membership.order_no, actor.id, "legacy access covered playback", "refund-future-grant")["status"] == "succeeded"
    assert billing.access_until(session, user.id) is not None

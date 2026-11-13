"""Password mutations serialize on real PostgreSQL account locks.

Each worker owns a separate Session; no TestClient or Session crosses threads.
The shared fixtures provision and truncate only the dedicated test database.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic, sleep

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from app.modules.ops import service as auth
from app.modules.ops.models import AdminUser
from app.modules.ops.router import lock_password_account
from app.modules.tenancy.identity import SessionPrincipal
from app.modules.tenancy.models import Tenant
from app.modules.tenancy.tenant_staff import reset_tenant_password

WAIT_SECONDS = 10


def _principal(user):
    return SessionPrincipal(user, 'tenant', user.tenant_id, 'normal', user.auth_version)


def _pid(db):
    # Bound waits also release connections if a failed assertion leaves a worker
    # waiting for a lock; the holder is always rolled back in a finally block.
    db.execute(text("SET LOCAL lock_timeout = '10s'"))
    return db.scalar(text('SELECT pg_backend_pid()'))


def _assert_blocked_by(db, waiter_pid, holder_pid):
    deadline = monotonic() + 5
    while monotonic() < deadline:
        blocked = db.scalar(
            text('SELECT :holder = ANY(pg_blocking_pids(:waiter))'),
            {'holder': holder_pid, 'waiter': waiter_pid},
        )
        if blocked:
            return
        sleep(0.01)
    pytest.fail('The competing password mutation did not wait for the account transaction')


def test_admin_reset_waits_for_self_change_and_refreshes_auth_version(session, make, monkeypatch):
    person = make.admin(username='password-race', password='original-pass-1234')
    account_id = person.id
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    assert session.get_bind().dialect.name == 'postgresql'
    # Isolate the row-lock/version contract from Redis availability. No session
    # credential is created by this helper-level regression.
    monkeypatch.setattr(auth, 'revoke_user_sessions', lambda _user_id: 0)
    ready, reset_now = Event(), Event()
    state = {}

    def administrator_reset():
        with factory() as db:
            state['pid'] = _pid(db)
            target = db.get(AdminUser, account_id)
            state['initial_version'] = target.auth_version
            ready.set()
            assert reset_now.wait(WAIT_SECONDS), 'Self-change never released the reset worker'
            temporary = reset_tenant_password(db, target)
            version = target.auth_version
            db.commit()
            return temporary, version

    with ThreadPoolExecutor(max_workers=1) as pool, factory() as db:
        future = pool.submit(administrator_reset)
        try:
            assert ready.wait(WAIT_SECONDS), 'Reset worker did not load the original account'
            holder_pid = _pid(db)
            user = db.get(AdminUser, account_id)
            user = lock_password_account(db, user, _principal(user))
            assert auth.verify_password('original-pass-1234', user.password_hash)
            user.password_hash = auth.hash_password('self-change-pass-1234')
            user.auth_version += 1
            user.must_change_password = False
            db.flush()
            reset_now.set()
            _assert_blocked_by(db, state['pid'], holder_pid)
            db.commit()
            temporary, version = future.result(timeout=WAIT_SECONDS)
        finally:
            reset_now.set()
            db.rollback()

    assert state['initial_version'] == 1  # Deliberately stale identity-map state.
    assert version == 3  # Self-change v2 followed by reset v3, never two v2 writes.
    with factory() as db:
        current = db.get(AdminUser, account_id)
        assert current.auth_version == 3
        assert current.must_change_password is True
        assert auth.verify_password(temporary, current.password_hash)
        assert not auth.verify_password('self-change-pass-1234', current.password_hash)


def test_self_change_waiting_on_admin_reset_rejects_stale_principal(session, make, monkeypatch):
    person = make.admin(username='stale-password-request', password='original-pass-1234')
    account_id = person.id
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    monkeypatch.setattr(auth, 'revoke_user_sessions', lambda _user_id: 0)
    ready, change_now = Event(), Event()
    state = {}

    def stale_self_change():
        with factory() as db:
            state['pid'] = _pid(db)
            user = db.get(AdminUser, account_id)
            principal = _principal(user)
            state['principal_version'] = principal.auth_version
            ready.set()
            assert change_now.wait(WAIT_SECONDS), 'Reset never released the stale request'
            try:
                lock_password_account(db, user, principal)
            except HTTPException as exc:
                db.rollback()
                return exc.status_code, exc.detail
            pytest.fail('A password request authenticated before reset was accepted after reset')

    with ThreadPoolExecutor(max_workers=1) as pool, factory() as db:
        future = pool.submit(stale_self_change)
        try:
            assert ready.wait(WAIT_SECONDS), 'Self-change worker did not authenticate at v1'
            holder_pid = _pid(db)
            target = db.get(AdminUser, account_id)
            # Match the platform/staff reset route's Tenant -> User lock order.
            db.scalar(select(Tenant).where(Tenant.id == target.tenant_id).with_for_update())
            temporary = reset_tenant_password(db, target)
            db.flush()
            change_now.set()
            _assert_blocked_by(db, state['pid'], holder_pid)
            db.commit()
            status, detail = future.result(timeout=WAIT_SECONDS)
        finally:
            change_now.set()
            db.rollback()

    assert state['principal_version'] == 1
    assert status == 401
    assert detail['code'] == 'invalid_session'
    with factory() as db:
        current = db.get(AdminUser, account_id)
        assert current.auth_version == 2
        assert current.must_change_password is True
        assert auth.verify_password(temporary, current.password_hash)
        assert not auth.verify_password('original-pass-1234', current.password_hash)

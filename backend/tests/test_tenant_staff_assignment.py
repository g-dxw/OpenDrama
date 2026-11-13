"""A migrated account gains tenant access only through explicit role assignment."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.ops import service as auth
from app.modules.ops.models import AdminUser, Supplier
from app.modules.tenancy.models import TenantMember
from app.modules.tenancy.permissions import preset_role_id


@pytest.fixture()
def administrator(session, make):
    owner = make.admin(role='admin', username='assignment-owner')
    client = TestClient(app)
    response = client.post('/tenant/v1/auth/login', json={
        'tenant_code': 'jjtv', 'username': owner.username, 'password': 'pass-1234',
    })
    assert response.status_code == 200, response.text
    return client, {'Authorization': 'Bearer ' + response.json()['token']}


def _unassigned(session, username='migrated-unassigned', tenant_id='jjtv', *, supplier_id=None):
    user = AdminUser(
        username=username, password_hash=auth.hash_password('pass-1234'),
        display_name='Existing account', role='partner' if supplier_id else 'ops',
        status='active', account_scope='tenant', tenant_id=tenant_id,
        auth_version=1, must_change_password=False, supplier_id=supplier_id,
    )
    session.add(user)
    session.commit()
    return user


def _login(client, user):
    return client.post('/tenant/v1/auth/login', json={
        'tenant_code': user.tenant_id, 'username': user.username, 'password': 'pass-1234',
    })


def test_unassigned_employee_requires_explicit_role_before_login(session, administrator):
    client, headers = administrator
    user = _unassigned(session)
    assert _login(client, user).status_code == 401

    renamed = client.patch('/tenant/v1/staff/' + str(user.id),
                           json={'display_name': 'Renamed without authorization'}, headers=headers)
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()['role_id'] is None
    assert renamed.json()['role_name'] == '未分配角色'
    assert session.get(TenantMember, ('jjtv', user.id)) is None
    assert _login(client, user).status_code == 401

    role_id = preset_role_id('jjtv', 'finance')
    assigned = client.patch('/tenant/v1/staff/' + str(user.id),
                            json={'role_id': role_id}, headers=headers)
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()['role_id'] == role_id
    assert assigned.json()['is_tenant_admin'] is False
    membership = session.get(TenantMember, ('jjtv', user.id), populate_existing=True)
    assert membership is not None
    assert membership.role_id == role_id
    assert membership.role == 'finance'
    logged_in = _login(client, user)
    assert logged_in.status_code == 200, logged_in.text
    assert logged_in.json()['context']['role_id'] == role_id


def test_unassigned_employee_cannot_receive_another_tenants_role(session, administrator):
    client, headers = administrator
    user = _unassigned(session)
    response = client.patch('/tenant/v1/staff/' + str(user.id),
                            json={'role_id': preset_role_id('india-short-drama', 'ops')}, headers=headers)
    assert response.status_code == 404, response.text
    assert session.get(TenantMember, ('jjtv', user.id)) is None
    assert _login(client, user).status_code == 401

    other = _unassigned(session, username='other-tenant-unassigned', tenant_id='india-short-drama')
    response = client.patch('/tenant/v1/staff/' + str(other.id),
                            json={'role_id': preset_role_id('jjtv', 'ops')}, headers=headers)
    assert response.status_code == 404, response.text
    assert session.get(TenantMember, ('india-short-drama', other.id)) is None


def test_partner_cannot_be_converted_to_tenant_member_by_role_assignment(session, administrator):
    client, headers = administrator
    supplier = Supplier(name='Existing external supplier', status='active')
    session.add(supplier)
    session.commit()
    partner = _unassigned(session, username='external-partner', supplier_id=supplier.id)
    response = client.patch('/tenant/v1/staff/' + str(partner.id),
                            json={'role_id': preset_role_id('jjtv', 'ops')}, headers=headers)
    assert response.status_code == 400, response.text
    assert session.get(TenantMember, ('jjtv', partner.id)) is None
    session.refresh(partner)
    assert partner.role == 'partner'
    assert partner.supplier_id == supplier.id

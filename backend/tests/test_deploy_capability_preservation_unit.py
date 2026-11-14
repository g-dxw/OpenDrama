"""Known capability migration additions do not permit changing old authorizations."""
from copy import deepcopy

import pytest

from app.deploy_tenant_upgrade import verify_preserved

KEYS = ('login_settings', 'stripe', 'coin_packs', 'coins', 'attribution', 'support')
STAMP = '2026-11-13T00:00:00+00:00'


def evidence(*, head=False):
    tenants = [{'id': 'jjtv', 'code': 'jjtv', 'kind': 'native'},
               {'id': 'india-short-drama', 'code': 'india-short-drama', 'kind': 'india_legacy'}]
    bindings = [{'module_key': 'dramas', 'tenant_id': 'jjtv', 'created_at': STAMP}]
    modules = [dict(tenant_id=t['id'], module_key='dramas', enabled=t['id'] == 'jjtv') for t in tenants]
    if head:
        bindings += [dict(module_key=k, tenant_id='jjtv', created_at=STAMP) for k in KEYS]
        modules += [dict(tenant_id=t['id'], module_key=k, enabled=t['id'] == 'jjtv') for t in tenants for k in KEYS]
    snapshot = dict(revisions=['a1114cancel_intent' if head else 'e6a1c3d9b742'],
                    tenants=tenants, legacy_bindings=bindings, modules=modules,
                    table_counts={'admin_users': 1, 'users': 3,
                                  'legacy_business_bindings': len(bindings),
                                  'tenant_modules': len(modules)})
    return snapshot, {1: b'original-account'}, {7: b'original-audit'}


def recount(value):
    snapshot = value[0]
    snapshot['table_counts']['legacy_business_bindings'] = len(snapshot['legacy_bindings'])
    snapshot['table_counts']['tenant_modules'] = len(snapshot['modules'])


def test_allows_only_the_exact_missing_capability_additions():
    before = evidence()
    after = evidence(head=True)
    verify_preserved(before, after)


def test_keeps_preexisting_disabled_capability_pair_unchanged():
    before = evidence()
    before[0]['modules'].append(dict(tenant_id='jjtv', module_key='support', enabled=False))
    recount(before)
    after = evidence(head=True)
    next(row for row in after[0]['modules'] if row['tenant_id'] == 'jjtv' and row['module_key'] == 'support')['enabled'] = False
    verify_preserved(before, after)


@pytest.mark.parametrize('mutation', [
    'binding_owner', 'binding_created_at', 'module_enabled',
    'binding_unexpected_key', 'module_unexpected_key',
    'binding_deleted', 'module_deleted',
])
def test_rejects_changed_old_authorizations_even_at_the_same_schema_head(mutation):
    before = evidence(head=True)
    after = deepcopy(before)
    if mutation == 'binding_owner':
        after[0]['legacy_bindings'][0]['tenant_id'] = 'india-short-drama'
    elif mutation == 'binding_created_at':
        after[0]['legacy_bindings'][0]['created_at'] = '2026-11-14T00:00:00+00:00'
    elif mutation == 'module_enabled':
        next(row for row in after[0]['modules'] if row['tenant_id'] == 'india-short-drama')['enabled'] = True
    elif mutation == 'binding_unexpected_key':
        after[0]['legacy_bindings'][0]['module_key'] = 'unregistered-capability'
    elif mutation == 'module_unexpected_key':
        after[0]['modules'][0]['module_key'] = 'unregistered-capability'
    elif mutation == 'binding_deleted':
        after[0]['legacy_bindings'].pop()
    else:
        after[0]['modules'].pop()
    recount(after)
    with pytest.raises(RuntimeError, match='remains stopped'):
        verify_preserved(before, after)


@pytest.mark.parametrize('missing_table', ['both', 'legacy_bindings', 'modules'])
def test_rejects_missing_required_additions_when_crossing_the_capability_migration(missing_table):
    before = evidence()
    after = evidence(head=True)
    for table in ('legacy_bindings', 'modules') if missing_table == 'both' else (missing_table,):
        after[0][table] = deepcopy(before[0][table])
    recount(after)
    with pytest.raises(RuntimeError, match='remains stopped'):
        verify_preserved(before, after)


@pytest.mark.parametrize('invalid', [
    'binding_owner', 'module_owner', 'module_enabled',
    'binding_unexpected_addition', 'module_unexpected_addition',
])
def test_rejects_wrong_owner_or_enabled_state_for_new_capability_rows(invalid):
    before = evidence()
    after = evidence(head=True)
    if invalid == 'binding_owner':
        after[0]['legacy_bindings'][-1]['tenant_id'] = 'india-short-drama'
    elif invalid == 'module_owner':
        after[0]['modules'][-1]['tenant_id'] = 'unrelated-tenant'
    elif invalid == 'module_enabled':
        next(row for row in after[0]['modules'] if row['tenant_id'] == 'india-short-drama' and row['module_key'] == 'support')['enabled'] = True
    elif invalid == 'binding_unexpected_addition':
        after[0]['legacy_bindings'].append(dict(module_key='unregistered-capability', tenant_id='jjtv', created_at=STAMP))
    else:
        after[0]['modules'].append(dict(tenant_id='jjtv', module_key='unregistered-capability', enabled=True))
    recount(after)
    with pytest.raises(RuntimeError, match='remains stopped'):
        verify_preserved(before, after)


def test_same_head_cannot_refill_intentionally_removed_known_capabilities():
    # Test each table independently: either missing row may be an intentional
    # post-migration operator choice, not permission to re-run old defaults.
    for table in ('legacy_bindings', 'modules'):
        after = evidence(head=True)
        before = deepcopy(after)
        before[0][table] = [row for row in before[0][table]
                            if not (row['module_key'] == 'support'
                                    and (table == 'legacy_bindings' or row['tenant_id'] == 'jjtv'))]
        recount(before)
        with pytest.raises(RuntimeError, match='remains stopped'):
            verify_preserved(before, after)

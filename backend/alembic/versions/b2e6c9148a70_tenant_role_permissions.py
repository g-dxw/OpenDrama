"""Tenant role definitions and same-tenant membership bindings.

The preset permissions below are a migration snapshot. Do not import runtime
permission helpers: later application changes must not rewrite this revision.
"""
from __future__ import annotations

import hashlib

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision = "b2e6c9148a70"
down_revision = "f9c2a71d8b30"
branch_labels = None
depends_on = None

_ROLE_NAMES = {"admin": "内容管理员", "ops": "内容运营", "finance": "财务 / 只读"}
_NATIVE_CONTENT = frozenset({
    "dramas.view", "dramas.edit", "dramas.upload", "dramas.publish", "dramas.delete",
    "dramas.pricing", "episodes.delete", "reviews.view", "reviews.decide",
    "tags.view", "tags.manage", "groups.view", "groups.manage",
})
_INDIA_CONTENT = frozenset({
    "dramas.view", "dramas.edit", "dramas.upload", "dramas.publish", "dramas.pricing",
    "reviews.view", "reviews.decide",
})
_GLOBAL = frozenset({
    "dashboard.view", "users.view", "users.manage", "home_ops.view", "home_ops.manage",
    "rewards.view", "rewards.manage", "comments.view", "comments.manage",
    "moderation.view", "moderation.manage", "finance.view", "settlements.view",
    "settlements.manage", "plans.view", "plans.manage", "staff.view", "staff.manage",
    "api_keys.view", "api_keys.manage", "suppliers.view", "suppliers.manage",
})
_JJTV_GLOBAL_DEFAULTS = {
    "admin": _GLOBAL,
    "ops": frozenset({
        "dashboard.view", "home_ops.view", "home_ops.manage", "rewards.view",
        "rewards.manage", "comments.view", "comments.manage", "moderation.view",
    }),
    "finance": frozenset({
        "dashboard.view", "users.view", "finance.view", "settlements.view",
        "settlements.manage", "plans.view",
    }),
}


def _preset_id(tenant_id: str, builtin_key: str) -> str:
    return "role-" + hashlib.sha256((tenant_id + ":" + builtin_key).encode()).hexdigest()[:40]


def _preset_permissions(tenant_id: str, kind: str, builtin_key: str) -> list[str]:
    if kind not in {"native", "india_legacy"} or builtin_key not in _ROLE_NAMES:
        raise RuntimeError("Cannot represent an unknown tenant kind or built-in role")
    content = _INDIA_CONTENT if kind == "india_legacy" else _NATIVE_CONTENT
    if builtin_key == "admin":
        permissions = set(content)
    elif builtin_key == "ops":
        permissions = set(content) - {"dramas.delete"}
    else:
        permissions = {"dramas.view"}
    if tenant_id == "jjtv":
        permissions.update(_JJTV_GLOBAL_DEFAULTS[builtin_key])
    return sorted(permissions)


def _effective_builtin(user_role: str, member_role: str) -> str:
    if user_role == "admin":
        return "admin"
    if user_role not in {"ops", "finance"}:
        raise RuntimeError("Cannot migrate a tenant membership belonging to a non-staff account")
    return "finance" if user_role == "finance" or member_role == "finance" else "ops"


def _tenants(bind) -> list[dict]:
    return list(bind.execute(sa.text("SELECT id, kind FROM tenants ORDER BY id")).mappings())


def _members(bind, *, with_role_id: bool = False) -> list[dict]:
    extra = ", m.role_id" if with_role_id else ""
    return list(bind.execute(sa.text(
        "SELECT m.tenant_id, m.user_id, m.role AS member_role, u.role AS user_role" + extra +
        " FROM tenant_members AS m JOIN admin_users AS u ON u.id = m.user_id"
    )).mappings())


def upgrade():
    op.create_table(
        "tenant_roles",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(64), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("builtin_key", sa.String(16), nullable=True),
        sa.Column("permissions", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "id", name="uq_tenant_roles_tenant_role_id"),
        sa.UniqueConstraint("tenant_id", "builtin_key", name="uq_tenant_roles_tenant_builtin"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_tenant_roles_tenant_name"),
        sa.CheckConstraint("builtin_key IS NULL OR builtin_key IN ('admin','ops','finance')", name="ck_tenant_roles_builtin_key"),
        sa.CheckConstraint("jsonb_typeof(permissions) = 'array'", name="ck_tenant_roles_permissions_array"),
        sa.CheckConstraint("version >= 1", name="ck_tenant_roles_version_positive"),
    )
    bind = op.get_bind()
    role_table = sa.table(
        "tenant_roles", sa.column("id", sa.String(64)), sa.column("tenant_id", sa.String(64)),
        sa.column("name", sa.String(80)), sa.column("builtin_key", sa.String(16)),
        sa.column("permissions", JSONB()), sa.column("version", sa.Integer()),
    )
    rows = [
        {"id": _preset_id(tenant["id"], key), "tenant_id": tenant["id"],
         "name": name, "builtin_key": key,
         "permissions": _preset_permissions(tenant["id"], tenant["kind"], key), "version": 1}
        for tenant in _tenants(bind) for key, name in _ROLE_NAMES.items()
    ]
    if rows:
        op.bulk_insert(role_table, rows)
    op.add_column("tenant_members", sa.Column("role_id", sa.String(64), nullable=True))
    updates = []
    for member in _members(bind):
        effective = _effective_builtin(member["user_role"], member["member_role"])
        updates.append({"tenant_id": member["tenant_id"], "user_id": member["user_id"],
                        "role_id": _preset_id(member["tenant_id"], effective), "role": effective})
    if updates:
        bind.execute(sa.text(
            "UPDATE tenant_members SET role_id = :role_id, role = :role"
            " WHERE tenant_id = :tenant_id AND user_id = :user_id"
        ), updates)
    op.alter_column("tenant_members", "role_id", existing_type=sa.String(64), nullable=False)
    op.create_foreign_key(
        "fk_tenant_members_tenant_role", "tenant_members", "tenant_roles",
        ["tenant_id", "role_id"], ["tenant_id", "id"], ondelete="RESTRICT",
    )
    op.create_index("ix_tenant_members_tenant_role", "tenant_members", ["tenant_id", "role_id"])


def downgrade():
    bind = op.get_bind()
    tenants = {tenant["id"]: tenant["kind"] for tenant in _tenants(bind)}
    roles = list(bind.execute(sa.text(
        "SELECT id, tenant_id, builtin_key, permissions FROM tenant_roles"
    )).mappings())
    expected_ids = {_preset_id(tenant_id, key) for tenant_id in tenants for key in _ROLE_NAMES}
    actual_ids = set()
    role_keys = {}
    for role in roles:
        key = role["builtin_key"]
        if key not in _ROLE_NAMES:
            raise RuntimeError("Refusing to discard custom tenant roles; migrate their authorization first")
        tenant_id = role["tenant_id"]
        if tenant_id not in tenants or role["id"] != _preset_id(tenant_id, key):
            raise RuntimeError("Refusing to discard non-preset tenant role identities")
        permissions = role["permissions"]
        expected = _preset_permissions(tenant_id, tenants[tenant_id], key)
        if not isinstance(permissions, list) or not all(isinstance(item, str) for item in permissions) or sorted(permissions) != expected:
            raise RuntimeError("Refusing to discard modified tenant role permissions; restore presets before downgrade")
        actual_ids.add(role["id"])
        role_keys[(tenant_id, role["id"])] = key
    if actual_ids != expected_ids:
        raise RuntimeError("Refusing to downgrade an incomplete set of built-in tenant roles")
    for member in _members(bind, with_role_id=True):
        expected = _effective_builtin(member["user_role"], member["member_role"])
        if role_keys.get((member["tenant_id"], member["role_id"])) != expected:
            raise RuntimeError("Refusing to discard a tenant membership that differs from legacy effective permissions")
    op.drop_constraint("fk_tenant_members_tenant_role", "tenant_members", type_="foreignkey")
    op.drop_index("ix_tenant_members_tenant_role", table_name="tenant_members")
    op.drop_column("tenant_members", "role_id")
    op.drop_table("tenant_roles")

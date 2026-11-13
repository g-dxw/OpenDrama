"""Custom tenant roles must authorize HTTP actions, not only hide UI menus.

Uses the dedicated database/Redis fixtures. India is mocked and is never mutated.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.modules.content.models import Drama, Episode, MediaUpload
from app.modules.tenancy.india import IndiaError, IndiaReply
from app.modules.tenancy.models import DEFAULT_TENANT, INDIA_TENANT

ROOT = "/admin/v1/tenants"
INDIA = ROOT + "/india-short-drama/india"
CONTENT_KEYS = {"dramas.view", "dramas.edit", "dramas.upload", "dramas.publish", "dramas.delete", "dramas.pricing",
                "episodes.delete", "reviews.view", "reviews.decide", "tags.view", "tags.manage", "groups.view", "groups.manage"}
ACCESS, REFRESH = "a" * 43, "r" * 43
OLD_ACTOR = {"id": "user_old_editor", "name": "Named old editor", "role": "editor", "age_verified": False}


def logged_in(client, staff):
    response = client.post("/admin/v1/auth/login", json={"username": staff.username, "password": "pass-1234"})
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["token"]}


def in_tenant(headers, tenant):
    return {**headers, "X-JJTV-Tenant": tenant}


@pytest.fixture()
def ctx(session, make, monkeypatch):
    client = TestClient(app)
    owner, operator, finance = make.admin(role="admin"), make.admin(role="ops"), make.admin(role="finance")
    monkeypatch.setenv("INDIA_API_BASE_URL", "https://india.example/v1")
    monkeypatch.setenv("TENANT_CONNECTION_KEY", Fernet.generate_key().decode())
    return {"db": session, "make": make, "client": client, "owner": owner, "operator": operator, "finance": finance,
            "owner_headers": logged_in(client, owner), "operator_headers": logged_in(client, operator), "finance_headers": logged_in(client, finance)}


def new_tenant(ctx, name="Permission test studio"):
    response = ctx["client"].post(ROOT, json={"name": name}, headers=ctx["owner_headers"])
    assert response.status_code == 201, response.text
    return response.json()["id"]


def role_catalog(ctx, tenant):
    response = ctx["client"].get(ROOT + f"/{tenant}/roles", headers=ctx["owner_headers"])
    assert response.status_code == 200, response.text
    assert set(response.json()) >= {"roles", "catalog"}
    return response.json()


def create_role(ctx, tenant, permissions, name="Custom role"):
    response = ctx["client"].post(ROOT + f"/{tenant}/roles", json={"name": name, "permissions": permissions}, headers=ctx["owner_headers"])
    assert response.status_code == 201, response.text
    role = response.json()
    assert role["name"] == name and role["builtin_key"] is None
    assert set(role["permissions"]) == set(permissions)
    assert isinstance(role["version"], int) and role["version"] > 0
    return role


def assign(ctx, tenant, staff, role):
    response = ctx["client"].put(ROOT + f"/{tenant}/members", json={"members": [{"user_id": staff.id, "role_id": role["id"]}]}, headers=ctx["owner_headers"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) >= {"members", "staff", "roles"}
    member = next(item for item in body["members"] if item["user_id"] == staff.id)
    assert member["role_id"] == role["id"] and member["role_name"] == role["name"]
    return member


def seed_content(ctx, tenant, title="Scoped draft", *, review_state="none", ready=False):
    row = Drama(tenant_id=tenant, title=title, status="draft", review_state=review_state, coin_price_per_episode=20)
    ctx["db"].add(row); ctx["db"].flush()
    episode = Episode(drama_id=row.id, episode_number=1, title="First episode", status="in_review" if ready else "not_uploaded",
                      bunny_video_id="fake-existing-video" if ready else "")
    ctx["db"].add(episode); ctx["db"].commit()
    return row, episode


def test_role_catalog_and_CRUD_require_platform_admin_and_version_match(ctx):
    tenant = new_tenant(ctx)
    catalog = role_catalog(ctx, tenant)
    assert CONTENT_KEYS <= {entry["key"] for entry in catalog["catalog"]}
    for entry in catalog["catalog"]:
        assert set(entry) >= {"key", "label", "group", "requires"}
    role = create_role(ctx, tenant, ["dramas.view", "dramas.edit"], "Metadata editor")
    assigned = assign(ctx, tenant, ctx["operator"], role)
    assert assigned["role_id"] == role["id"]
    for method in ("GET", "POST"):
        kwargs = {"headers": ctx["operator_headers"]}
        if method == "POST": kwargs["json"] = {"name": "Cannot self-grant", "permissions": sorted(CONTENT_KEYS)}
        assert ctx["client"].request(method, ROOT + f"/{tenant}/roles", **kwargs).status_code == 403
    stale = ctx["client"].patch(ROOT + f"/{tenant}/roles/{role['id']}", json={"version": role["version"] + 1, "permissions": ["dramas.view"]}, headers=ctx["owner_headers"])
    assert stale.status_code == 409
    unchanged = next(item for item in role_catalog(ctx, tenant)["roles"] if item["id"] == role["id"])
    assert unchanged["version"] == role["version"] and set(unchanged["permissions"]) == {"dramas.view", "dramas.edit"}
    assert ctx["client"].delete(ROOT + f"/{tenant}/roles/{role['id']}?version={role['version']}", headers=ctx["owner_headers"]).status_code == 409
    builtin = next(item for item in catalog["roles"] if item["builtin_key"] == "finance")
    assert ctx["client"].delete(ROOT + f"/{tenant}/roles/{builtin['id']}?version={builtin['version']}", headers=ctx["owner_headers"]).status_code == 409
    # An unassigned custom role may be removed using its admitted version.
    unused = create_role(ctx, tenant, ["tags.view"], "Unused reader")
    assert ctx["client"].delete(ROOT + f"/{tenant}/roles/{unused['id']}?version={unused['version']}", headers=ctx["owner_headers"]).status_code in (200, 204)
    assert unused["id"] not in {item["id"] for item in role_catalog(ctx, tenant)["roles"]}


@pytest.mark.parametrize("permissions", [["dramas.*"], ["platform.superadmin"], ["dramas.edit"], ["reviews.decide"], ["tags.manage"], ["groups.manage"]])
def test_unknown_permissions_and_missing_view_dependencies_are_rejected(ctx, permissions):
    tenant = new_tenant(ctx)
    response = ctx["client"].post(ROOT + f"/{tenant}/roles", json={"name": "Invalid grant", "permissions": permissions}, headers=ctx["owner_headers"])
    assert response.status_code == 422, response.text
    assert not any(role["name"] == "Invalid grant" for role in role_catalog(ctx, tenant)["roles"])


def test_role_ids_cannot_be_used_or_mutated_across_tenants(ctx):
    first, second = new_tenant(ctx, "First tenant"), new_tenant(ctx, "Second tenant")
    role = create_role(ctx, first, ["dramas.view"], "First tenant reader")
    headers = ctx["owner_headers"]
    assert ctx["client"].patch(ROOT + f"/{second}/roles/{role['id']}", json={"version": role["version"], "name": "Takeover"}, headers=headers).status_code == 404
    assert ctx["client"].delete(ROOT + f"/{second}/roles/{role['id']}?version={role['version']}", headers=headers).status_code == 404
    rejected = ctx["client"].put(ROOT + f"/{second}/members", json={"members": [{"user_id": ctx["operator"].id, "role_id": role["id"]}]}, headers=headers)
    assert rejected.status_code in (404, 422)
    assert ctx["client"].get(ROOT + f"/{second}/members", headers=headers).json()["members"] == []
    builtin_ops = next(item for item in role_catalog(ctx, second)["roles"] if item["builtin_key"] == "ops")
    conflict = ctx["client"].put(ROOT + f"/{second}/members", json={"members": [{"user_id": ctx["operator"].id, "role_id": builtin_ops["id"], "role": "finance"}]}, headers=headers)
    assert conflict.status_code == 422


def test_role_changes_revoke_actions_and_menu_entries_without_relogin(ctx):
    tenant = new_tenant(ctx)
    role = create_role(ctx, tenant, ["dramas.view", "dramas.edit"], "Initially an editor")
    assign(ctx, tenant, ctx["operator"], role)
    headers = in_tenant(ctx["operator_headers"], tenant)
    catalog = {entry["key"]: entry for entry in role_catalog(ctx, tenant)["catalog"]}
    row = next(item for item in ctx["client"].get(ROOT, headers=ctx["operator_headers"]).json() if item["id"] == tenant)
    assert set(row["permissions"]) == {"dramas.view", "dramas.edit"}
    assert row["role_id"] == role["id"] and row["role_name"] == role["name"]
    assert catalog["dramas.view"]["menu_key"] in row["menu_keys"]
    assert ctx["client"].post("/admin/v1/dramas", json={"title": "Created before revoke"}, headers=headers).status_code == 200
    updated = ctx["client"].patch(ROOT + f"/{tenant}/roles/{role['id']}", json={"version": role["version"], "name": "Now tags only", "permissions": ["tags.view"]}, headers=ctx["owner_headers"])
    assert updated.status_code == 200, updated.text
    assert ctx["client"].get("/admin/v1/dramas", headers=headers).status_code == 403
    assert ctx["client"].post("/admin/v1/dramas", json={"title": "Must not be created"}, headers=headers).status_code == 403
    assert ctx["client"].get("/admin/v1/tags", headers=headers).status_code == 200
    row = next(item for item in ctx["client"].get(ROOT, headers=ctx["operator_headers"]).json() if item["id"] == tenant)
    assert set(row["permissions"]) == {"tags.view"} and row["role_name"] == "Now tags only"
    assert catalog["tags.view"]["menu_key"] in row["menu_keys"]
    assert catalog["dramas.view"]["menu_key"] not in row["menu_keys"]


def test_edit_permission_does_not_allow_pricing_or_partial_mixed_patch(ctx):
    tenant = new_tenant(ctx)
    role = create_role(ctx, tenant, ["dramas.view", "dramas.edit"])
    assign(ctx, tenant, ctx["operator"], role)
    drama, episode = seed_content(ctx, tenant)
    headers = in_tenant(ctx["operator_headers"], tenant)
    accepted = ctx["client"].patch(f"/admin/v1/dramas/{drama.id}", json={"title": "Allowed metadata edit"}, headers=headers)
    assert accepted.status_code == 200
    for restricted in ({"coin_price_per_episode": 99}, {"free_episode_count": 8}, {"cover_url": "https://images.example/unauthorized.png"}):
        response = ctx["client"].patch(f"/admin/v1/dramas/{drama.id}", json={"title": "Must not partially apply", **restricted}, headers=headers)
        assert response.status_code == 403, response.text
    ctx["db"].expire_all()
    stored = ctx["db"].get(Drama, drama.id)
    assert stored.title == "Allowed metadata edit" and stored.coin_price_per_episode == 20 and stored.free_episode_count == 0 and stored.cover_url == ""


def test_edit_permission_cannot_bypass_publish_and_review_action_routes(ctx):
    tenant = new_tenant(ctx)
    role = create_role(ctx, tenant, ["dramas.view", "dramas.edit"])
    assign(ctx, tenant, ctx["operator"], role)
    drama, episode = seed_content(ctx, tenant, ready=True)
    headers = in_tenant(ctx["operator_headers"], tenant)
    requests = [
        (f"/admin/v1/episodes/{episode.id}/publish", {}),
        (f"/admin/v1/episodes/{episode.id}/unpublish", {}),
        ("/admin/v1/episodes/bulk-status", {"episode_ids": [episode.id], "action": "publish"}),
        (f"/admin/v1/dramas/{drama.id}/status", {"to_status": "published"}),
        (f"/admin/v1/dramas/{drama.id}/schedule", {"at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}),
        ("/admin/v1/schedules/apply", {}),
        (f"/admin/v1/reviews/{drama.id}/decision", {"decision": "approve"}),
        ("/admin/v1/reviews/batch", {"drama_ids": [drama.id], "decision": "approve"}),
        (f"/admin/v1/reviews/{drama.id}/publish", {}),
        (f"/admin/v1/dramas/{drama.id}/delete", {"confirm": True}),
    ]
    for path, body in requests:
        response = ctx["client"].post(path, json=body, headers=headers)
        assert response.status_code == 403, (path, response.status_code, response.text)
    assert ctx["client"].delete(f"/admin/v1/episodes/{episode.id}", headers=headers).status_code == 403
    ctx["db"].expire_all()
    assert ctx["db"].get(Drama, drama.id).status == "draft"
    assert ctx["db"].get(Episode, episode.id).status == "in_review"


def test_upload_and_metadata_are_separate_actions_including_TUS_HEAD(ctx):
    tenant = new_tenant(ctx)
    role = create_role(ctx, tenant, ["dramas.view", "dramas.edit"])
    assign(ctx, tenant, ctx["operator"], role)
    drama, episode = seed_content(ctx, tenant, ready=True)
    upload = MediaUpload(episode_id=episode.id, video_id="permission-owned-video", filename="clip.mp4", mime="video/mp4", size_bytes=16, status="created")
    ctx["db"].add(upload); ctx["db"].commit()
    headers = in_tenant(ctx["operator_headers"], tenant)
    for path, body in [
        (f"/admin/v1/episodes/{episode.id}/upload-session", {"filename": "clip.mp4", "mime": "video/mp4", "size_bytes": 16, "width": 720, "height": 1280}),
        (f"/admin/v1/episodes/{episode.id}/upload-headers", {}),
        (f"/admin/v1/episodes/{episode.id}/sync-status", {}),
        (f"/admin/v1/episodes/{episode.id}/upload-failed", {"reason": "no upload permission"}),
        ("/admin/v1/dev-tus/permission-owned-video", {}),
    ]:
        response = ctx["client"].post(path, json=body, headers=headers)
        assert response.status_code == 403, (path, response.status_code, response.text)
    assert ctx["client"].head("/admin/v1/dev-tus/permission-owned-video", headers=headers).status_code == 403
    assert ctx["client"].patch("/admin/v1/dev-tus/permission-owned-video", content=b"forbidden", headers=headers).status_code == 403
    assert ctx["client"].post(f"/admin/v1/dramas/{drama.id}/cover", files={"file": ("cover.png", b"\x89PNG\r\n\x1a\nfixture", "image/png")}, headers=headers).status_code == 403
    assert ctx["client"].delete(f"/admin/v1/dramas/{drama.id}/cover", headers=headers).status_code == 403
    assert ctx["client"].post(f"/admin/v1/episodes/{episode.id}/subtitle", files={"file": ("captions.vtt", b"WEBVTT\n", "text/vtt")}, headers=headers).status_code == 403
    ctx["db"].expire_all()
    assert ctx["db"].get(MediaUpload, upload.id).status == "created"


def test_custom_content_role_can_grant_finance_upload_without_global_admin(ctx):
    tenant = new_tenant(ctx)
    role = create_role(ctx, tenant, ["dramas.view", "dramas.upload"])
    assign(ctx, tenant, ctx["finance"], role)
    drama, episode = seed_content(ctx, tenant)
    headers = in_tenant(ctx["finance_headers"], tenant)
    uploaded = ctx["client"].post(f"/admin/v1/episodes/{episode.id}/upload-session", json={"filename": "clip.mp4", "mime": "video/mp4", "size_bytes": 16, "width": 720, "height": 1280}, headers=headers)
    assert uploaded.status_code == 200, uploaded.text
    assert ctx["client"].head("/admin/v1/dev-tus/" + uploaded.json()["video_id"], headers=headers).status_code == 200
    assert ctx["client"].patch(f"/admin/v1/episodes/{episode.id}", json={"title": "Missing edit permission"}, headers=headers).status_code == 403
    assert ctx["client"].get("/admin/v1/staff", headers=headers).status_code == 403
    assert ctx["client"].get("/admin/v1/staff", headers=ctx["finance_headers"]).status_code == 403
    ctx["db"].expire_all()
    assert ctx["db"].get(type(ctx["finance"]), ctx["finance"].id).role == "finance"


def test_episode_delete_and_entire_drama_delete_require_different_permissions(ctx):
    tenant = new_tenant(ctx)
    role = create_role(ctx, tenant, ["dramas.view", "episodes.delete"])
    assign(ctx, tenant, ctx["operator"], role)
    drama, episode = seed_content(ctx, tenant)
    headers = in_tenant(ctx["operator_headers"], tenant)
    drama_id, episode_id = drama.id, episode.id
    assert ctx["client"].delete(f"/admin/v1/episodes/{episode_id}", headers=headers).status_code == 204
    assert ctx["client"].post(f"/admin/v1/dramas/{drama_id}/delete", json={"confirm": True}, headers=headers).status_code == 403
    ctx["db"].expire_all()
    assert ctx["db"].get(Drama, drama_id) is not None
    assert ctx["db"].get(Episode, episode_id) is None


def test_custom_delete_and_pricing_permissions_do_not_change_platform_role(ctx):
    tenant = new_tenant(ctx)
    role = create_role(ctx, tenant, ["dramas.view", "dramas.edit", "dramas.pricing", "dramas.delete"])
    assign(ctx, tenant, ctx["finance"], role)
    headers = in_tenant(ctx["finance_headers"], tenant)
    created = ctx["client"].post("/admin/v1/dramas", json={"title": "Finance explicitly granted content"}, headers=headers)
    assert created.status_code == 200, created.text
    identifier = created.json()["id"]
    priced = ctx["client"].patch(f"/admin/v1/dramas/{identifier}", json={"coin_price_per_episode": 45, "free_episode_count": 3}, headers=headers)
    assert priced.status_code == 200, priced.text
    deleted = ctx["client"].post(f"/admin/v1/dramas/{identifier}/delete", json={"confirm": True}, headers=headers)
    assert deleted.status_code == 200 and deleted.json()["deleted"] is True
    assert ctx["client"].post("/admin/v1/staff", json={"username": "cannot-create-staff", "password": "pass-1234", "role": "admin"}, headers=ctx["finance_headers"]).status_code == 403
    assert ctx["client"].post("/admin/v1/billing/settings", json={"mode": "subscription"}, headers=ctx["finance_headers"]).status_code == 403
    ctx["db"].expire_all()
    assert ctx["db"].get(type(ctx["finance"]), ctx["finance"].id).role == "finance"


def test_review_decision_does_not_imply_publish_permission(ctx):
    tenant = new_tenant(ctx)
    role = create_role(ctx, tenant, ["dramas.view", "reviews.view", "reviews.decide"])
    assign(ctx, tenant, ctx["finance"], role)
    drama, episode = seed_content(ctx, tenant, review_state="pending", ready=True)
    headers = in_tenant(ctx["finance_headers"], tenant)
    assert ctx["client"].get("/admin/v1/reviews", headers=headers).status_code == 200
    approved = ctx["client"].post(f"/admin/v1/reviews/{drama.id}/decision", json={"decision": "approve"}, headers=headers)
    assert approved.status_code == 200, approved.text
    assert approved.json()["review_state"] == "approved"
    assert ctx["client"].post(f"/admin/v1/reviews/{drama.id}/publish", json={}, headers=headers).status_code == 403
    ctx["db"].expire_all()
    assert ctx["db"].get(Drama, drama.id).status == "draft"


def test_tags_and_groups_manage_permissions_do_not_unlock_other_menus(ctx):
    tenant = new_tenant(ctx)
    role = create_role(ctx, tenant, ["tags.view", "tags.manage"])
    assign(ctx, tenant, ctx["operator"], role)
    headers = in_tenant(ctx["operator_headers"], tenant)
    assert ctx["client"].post("/admin/v1/tags", json={"name_en": "Allowed tag"}, headers=headers).status_code in (200, 201)
    assert ctx["client"].get("/admin/v1/tags", headers=headers).status_code == 200
    for method, path, payload in [("GET", "/admin/v1/groups", None), ("POST", "/admin/v1/groups", {"name": "Cannot create group"}), ("GET", "/admin/v1/dramas", None), ("GET", "/admin/v1/reviews", None)]:
        options = {"headers": headers}
        if payload is not None: options["json"] = payload
        assert ctx["client"].request(method, path, **options).status_code == 403


@pytest.fixture()
def india(ctx, monkeypatch):
    from app.modules.tenancy import router as tenant_router
    observed = []
    denied = {"upstream": False}

    class FakeIndia:
        async def call(self, action, *, access_token=None, payload=None, resource_id=None, params=None, idempotency_key=None):
            observed.append(action)
            if action == "login": return IndiaReply({"access_token": ACCESS, "refresh_token": REFRESH, "user": OLD_ACTOR, "environment": "production"}, "old-login")
            if action == "me": return IndiaReply(OLD_ACTOR, "old-me")
            if action == "series_list": return IndiaReply({"items": [], "next_cursor": None}, "old-list")
            if action == "series_detail": return IndiaReply({"id": resource_id, "version": 3, "free_episode_count": 0}, "old-detail")
            if denied["upstream"]: raise IndiaError("india_forbidden", 403, upstream_code="FORBIDDEN", request_id="old-denied")
            return IndiaReply({"id": resource_id or "series_new", "version": 4}, "old-content")

    monkeypatch.setattr(tenant_router, "IndiaClient", FakeIndia)
    return observed, denied


def test_India_roles_are_limited_and_effective_permissions_intersect_upstream(ctx, india):
    keys = {row["key"] for row in role_catalog(ctx, INDIA_TENANT)["catalog"]}
    assert keys == {"dramas.view", "dramas.edit", "dramas.upload", "dramas.publish", "dramas.pricing", "reviews.view", "reviews.decide"}
    invalid = ctx["client"].post(ROOT + f"/{INDIA_TENANT}/roles", json={"name": "No local delete", "permissions": ["dramas.view", "dramas.delete"]}, headers=ctx["owner_headers"])
    assert invalid.status_code == 422
    role = create_role(ctx, INDIA_TENANT, ["dramas.view", "dramas.edit"], "India metadata only")
    assign(ctx, INDIA_TENANT, ctx["operator"], role)
    headers = ctx["operator_headers"]
    connected = ctx["client"].post(INDIA + "/session", json={"username": "old-editor", "password": "used-once"}, headers=headers)
    assert connected.status_code == 200 and connected.json()["connected"] is True
    assert ctx["client"].get(INDIA + "/admin/series", headers=headers).status_code == 200
    for path, payload in [
        ("/admin/series/series_1/publish", {"version": 3, "reason": "Not locally permitted"}),
        ("/admin/series/series_1/review", {"version": 3, "approved": True, "reason": "Not locally permitted"}),
        ("/admin/episodes/episode_1/uploads", {"filename": "clip.mp4", "size_bytes": 16, "content_type": "video/mp4"}),
    ]:
        before = len(india[0])
        result = ctx["client"].post(INDIA + path, json=payload, headers={**headers, "Idempotency-Key": str(uuid4())})
        assert result.status_code == 403, result.text
        assert india[0][before:] == []
    before = len(india[0])
    price = ctx["client"].patch(INDIA + "/admin/series/series_1", json={"version": 3, "title": "Mixed update", "free_episode_count": 2}, headers={**headers, "Idempotency-Key": str(uuid4())})
    assert price.status_code == 403
    assert india[0][before:] == []
    # Field authority is strict even when a full-form client resubmits the old value.
    for restricted in ({"free_episode_count": 0}, {"member_window": {"mode": "exclusive"}}, {"member_window": {"mode": "standard"}}):
        before = len(india[0])
        forbidden = ctx["client"].patch(INDIA + "/admin/series/series_1", json={"version": 3, **restricted}, headers={**headers, "Idempotency-Key": str(uuid4())})
        assert forbidden.status_code == 403, forbidden.text
        assert india[0][before:] == []  # No snapshot GET or mutation leaves the BFF.
    india[1]["upstream"] = True
    result = ctx["client"].patch(INDIA + "/admin/series/series_1", json={"version": 3, "title": "Locally permitted metadata"}, headers={**headers, "Idempotency-Key": str(uuid4())})
    assert result.status_code == 403
    assert result.json()["detail"]["upstream_code"] == "FORBIDDEN"
    assert india[0][-1] == "series_edit"


def test_publish_permission_works_without_metadata_edit_permission(ctx):
    tenant = new_tenant(ctx)
    role = create_role(ctx, tenant, ["dramas.view", "dramas.publish"])
    assign(ctx, tenant, ctx["operator"], role)
    drama, episode = seed_content(ctx, tenant, ready=True)
    drama.status = "in_review"
    ctx["db"].commit()
    headers = in_tenant(ctx["operator_headers"], tenant)
    assert ctx["client"].post(f"/admin/v1/episodes/{episode.id}/publish", json={}, headers=headers).status_code == 200
    published = ctx["client"].post(f"/admin/v1/dramas/{drama.id}/status", json={"to_status": "published"}, headers=headers)
    assert published.status_code == 200, published.text
    assert ctx["client"].patch(f"/admin/v1/dramas/{drama.id}", json={"title": "No metadata authority"}, headers=headers).status_code == 403
    ctx["db"].expire_all()
    assert ctx["db"].get(Drama, drama.id).status == "published"


def test_custom_role_does_not_bypass_native_tenant_object_ownership(ctx):
    first, second = new_tenant(ctx, "Authorized studio"), new_tenant(ctx, "Another studio")
    role = create_role(ctx, first, ["dramas.view", "dramas.edit"])
    assign(ctx, first, ctx["finance"], role)
    own, own_episode = seed_content(ctx, first, "Own draft")
    foreign, foreign_episode = seed_content(ctx, second, "Foreign draft")
    headers = in_tenant(ctx["finance_headers"], first)
    listed = ctx["client"].get("/admin/v1/dramas", headers=headers)
    assert listed.status_code == 200 and {row["id"] for row in listed.json()["items"]} == {own.id}
    assert ctx["client"].get(f"/admin/v1/dramas/{foreign.id}", headers=headers).status_code == 404
    assert ctx["client"].patch(f"/admin/v1/dramas/{foreign.id}", json={"title": "Forbidden"}, headers=headers).status_code == 404
    assert ctx["client"].patch(f"/admin/v1/episodes/{foreign_episode.id}", json={"title": "Forbidden"}, headers=headers).status_code == 404
    assert ctx["client"].get("/admin/v1/dramas", headers=in_tenant(ctx["finance_headers"], second)).status_code == 403
    ctx["db"].expire_all()
    assert ctx["db"].get(Drama, foreign.id).title == "Foreign draft"


def test_India_pricing_only_role_can_change_pricing_but_not_metadata(ctx, india):
    role = create_role(ctx, INDIA_TENANT, ["dramas.view", "dramas.pricing"], "India price operator")
    assign(ctx, INDIA_TENANT, ctx["operator"], role)
    headers = ctx["operator_headers"]
    connected = ctx["client"].post(INDIA + "/session", json={"username": "old-editor", "password": "used-once"}, headers=headers)
    assert connected.status_code == 200 and connected.json()["connected"] is True
    before = len(india[0])
    priced = ctx["client"].patch(INDIA + "/admin/series/series_1", json={"version": 3, "free_episode_count": 2, "member_window": {"mode": "standard"}}, headers={**headers, "Idempotency-Key": str(uuid4())})
    assert priced.status_code == 200, priced.text
    assert india[0][before:] == ["series_edit"]
    before = len(india[0])
    mixed = ctx["client"].patch(INDIA + "/admin/series/series_1", json={"version": 3, "free_episode_count": 2, "title": "No metadata grant"}, headers={**headers, "Idempotency-Key": str(uuid4())})
    assert mixed.status_code == 403, mixed.text
    assert india[0][before:] == []

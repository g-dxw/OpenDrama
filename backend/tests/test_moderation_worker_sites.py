"""Background moderation binds the persisted comment owner before using policy."""
from __future__ import annotations

import json
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import sessionmaker

from app.modules.account.models import User
from app.modules.content.models import Drama, Episode
from app.modules.ops.config_models import ModerationSettings
from app.modules.sites.models import SiteSettings
from app.modules.social import moderation
from app.modules.social.models import Comment
from app.modules.tenancy.consumer_scope import site_context
from app.modules.tenancy.models import LegacyBusinessBinding, Tenant, TenantModule
from app.modules.tenancy.permissions import seed_tenant_defaults

OTHER = "moderation-b"


@pytest.fixture
def workers(session,engine,monkeypatch):
    tenant = Tenant(id=OTHER,code=OTHER,name="Other moderation site",kind="native",status="active")
    session.add(tenant)
    session.flush()
    seed_tenant_defaults(session,tenant)
    session.commit()
    monkeypatch.setenv("MODERATION_KEY_SECRET","moderation-worker-unit-secret")
    monkeypatch.setenv("OPENAI_API_KEY","sk-environment-must-not-win")
    # Even a factory carrying a previous site's scope must discover the job's
    # actual owner rather than inheriting this unrelated context.
    factory = sessionmaker(bind=engine,expire_on_commit=False,info={"content_tenant_id":OTHER})
    monkeypatch.setattr(moderation,"_session_factory",factory)
    return factory


def policy(session,owner,*,enabled,threshold=0.9):
    with site_context(session,owner):
        settings = moderation.get_settings(session)
        settings.enabled = enabled
        settings.base_url = "https://"+owner+".moderation.example.test/v1"
        settings.model = owner+"-policy"
        settings.approve_threshold = threshold
        settings.block_threshold = threshold
        moderation.set_api_key(settings,"sk-"+owner+"-only-test-credential")
        session.commit()
        return settings


def queued_comment(session,owner):
    with site_context(session,owner):
        user = User(tenant_id=owner,email=uuid4().hex+"@example.test",display_name="Worker fixture")
        drama = Drama(tenant_id=owner,title="Worker fixture",status="published")
        session.add_all([user,drama])
        session.flush()
        episode = Episode(tenant_id=owner,drama_id=drama.id,episode_number=1,status="published")
        session.add(episode)
        session.flush()
        row = Comment(tenant_id=owner,user_id=user.id,drama_id=drama.id,episode_id=episode.id,
                      body="Comment owned by "+owner,status="pending",moderation_state="queued")
        session.add(row)
        session.commit()
        return row


def stored(session,identifier):
    session.expire_all()
    return session.get(Comment,identifier)


def provider(monkeypatch,*,verdict="allow",score=0.8):
    calls = []
    class Response:
        status_code = 200
        text = ""
        def json(self):
            return {"choices":[{"message":{"content":json.dumps({"verdict":verdict,"score":score,"reason":"fixture"})},"finish_reason":"stop"}]}
    class Client:
        def __init__(self,*args,**kwargs): pass
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def post(self,url,*,json,headers):
            calls.append(dict(url=url,authorization=headers["Authorization"],model=json["model"],messages=json["messages"]))
            return Response()
    monkeypatch.setattr(moderation.httpx,"Client",Client)
    return calls


def forbidden_provider(*args,**kwargs):
    raise AssertionError("A disabled or missing site's worker must not call a provider")


def test_unbound_legacy_settings_lookup_does_not_read_another_sites_first_row(session,workers):
    other = policy(session,OTHER,enabled=False,threshold=0.1)
    jjtv = policy(session,"jjtv",enabled=True,threshold=0.9)
    assert other.id < jjtv.id
    assert moderation.get_settings(session).tenant_id=="jjtv"
    with site_context(session,OTHER):
        assert moderation.get_settings(session).tenant_id==OTHER


def test_jjtv_job_ignores_foreign_disabled_policy_and_uses_own_endpoint_key_and_threshold(session,workers,monkeypatch):
    other = policy(session,OTHER,enabled=False,threshold=0.1)
    jjtv = policy(session,"jjtv",enabled=True,threshold=0.9)
    assert other.id < jjtv.id
    row = queued_comment(session,"jjtv")
    unrelated = queued_comment(session,OTHER)
    calls = provider(monkeypatch,score=0.8)

    moderation.process_comment(row.id)

    result = stored(session,row.id)
    assert result.tenant_id=="jjtv" and result.moderation_state=="done"
    assert result.status=="pending"  # 0.8 does not pass JJTV's 0.9 threshold.
    assert result.moderation_model=="jjtv-policy"
    assert calls==[dict(url="https://jjtv.moderation.example.test/v1/chat/completions",
        authorization="Bearer sk-jjtv-only-test-credential",model="jjtv-policy",
        messages=moderation.build_messages(row.body,jjtv))]
    assert stored(session,unrelated.id).moderation_state=="queued"
    assert stored(session,unrelated.id).moderation_attempts==0


def test_own_disabled_policy_cannot_borrow_foreign_enabled_configuration(session,workers,monkeypatch):
    policy(session,OTHER,enabled=True,threshold=0.1)
    policy(session,"jjtv",enabled=False,threshold=0.9)
    row = queued_comment(session,"jjtv")
    monkeypatch.setattr(moderation,"classify",forbidden_provider)
    moderation.process_comment(row.id)
    result = stored(session,row.id)
    assert result.moderation_state=="canceled" and result.status=="pending"
    assert result.moderation_claimed_at is None


def test_worker_cancels_unsupported_other_site_without_reading_jjtv_credentials(session,workers,monkeypatch):
    policy(session,"jjtv",enabled=True,threshold=0.1)
    policy(session,OTHER,enabled=True,threshold=0.9)
    row = queued_comment(session,OTHER)
    monkeypatch.setattr(moderation,"classify",forbidden_provider)
    moderation.process_comment(row.id)
    result = stored(session,row.id)
    assert result.tenant_id==OTHER and result.moderation_state=="canceled"
    assert result.status=="pending" and result.moderation_verdict is None


def test_worker_uses_non_jjtv_policy_when_module_is_explicitly_owned_by_that_site(session,workers,monkeypatch):
    policy(session,"jjtv",enabled=False,threshold=0.99)
    policy(session,OTHER,enabled=True,threshold=0.7)
    session.get(LegacyBusinessBinding,"moderation").tenant_id = OTHER
    session.get(TenantModule,(OTHER,"moderation")).enabled = True
    session.commit()
    row = queued_comment(session,OTHER)
    calls = provider(monkeypatch,score=0.8)
    moderation.process_comment(row.id)
    result = stored(session,row.id)
    assert result.tenant_id==OTHER and result.moderation_state=="done" and result.status=="approved"
    assert calls[0]["url"]=="https://"+OTHER+".moderation.example.test/v1/chat/completions"
    assert calls[0]["authorization"]=="Bearer sk-"+OTHER+"-only-test-credential"
    assert calls[0]["model"]==OTHER+"-policy"


def test_global_sweeper_rebinds_each_job_instead_of_inheriting_factory_scope(session,workers,monkeypatch):
    policy(session,OTHER,enabled=False,threshold=0.1)
    policy(session,"jjtv",enabled=True,threshold=0.7)
    jjtv = queued_comment(session,"jjtv")
    other = queued_comment(session,OTHER)
    calls = provider(monkeypatch)
    assert moderation.process_pending_batch(limit=5)==2
    assert stored(session,jjtv.id).moderation_state=="done"
    assert stored(session,jjtv.id).status=="approved"
    assert stored(session,other.id).moderation_state=="canceled"
    assert len(calls)==1 and calls[0]["model"]=="jjtv-policy"


def test_missing_comment_never_initializes_or_falls_back_to_jjtv_policy(session,workers,monkeypatch):
    monkeypatch.setattr(moderation,"get_settings",forbidden_provider)
    monkeypatch.setattr(moderation,"classify",forbidden_provider)
    moderation.process_comment(999999)
    assert session.scalar(select(func.count()).select_from(ModerationSettings))==0


@pytest.mark.parametrize("disabled",["moderation","comments","site","comments_feature"])
def test_disabled_own_module_or_site_cancels_without_provider(session,workers,monkeypatch,disabled):
    policy(session,"jjtv",enabled=True)
    row = queued_comment(session,"jjtv")
    if disabled=="site":
        session.get(Tenant,"jjtv").status="paused"
    elif disabled=="comments_feature":
        session.add(SiteSettings(tenant_id="jjtv",features={"comments":False}))
    else:
        session.get(TenantModule,("jjtv",disabled)).enabled=False
    session.commit()
    monkeypatch.setattr(moderation,"classify",forbidden_provider)
    moderation.process_comment(row.id)
    assert stored(session,row.id).moderation_state=="canceled"
    assert stored(session,row.id).status=="pending"


@pytest.mark.parametrize("failure",[False,True])
def test_disabling_policy_during_model_call_cancels_instead_of_approving_or_retrying(session,workers,monkeypatch,failure):
    policy(session,"jjtv",enabled=True,threshold=0.1)
    row = queued_comment(session,"jjtv")
    def classify(body,settings):
        assert settings.tenant_id=="jjtv"
        with workers() as db:
            db.info["content_tenant_id"]="jjtv"
            db.execute(update(ModerationSettings).where(ModerationSettings.tenant_id=="jjtv").values(enabled=False))
            db.commit()
        if failure:
            raise moderation.ModerationError("local provider timeout")
        return moderation.ModerationResult("allow",0.99,"clean","jjtv-policy",1)
    monkeypatch.setattr(moderation,"classify",classify)
    moderation.process_comment(row.id)
    result = stored(session,row.id)
    assert result.moderation_state=="canceled" and result.status=="pending"


def test_human_decision_during_model_call_still_wins_with_owned_writeback(session,workers,monkeypatch):
    policy(session,"jjtv",enabled=True)
    row = queued_comment(session,"jjtv")
    def classify(body,settings):
        with workers() as db:
            db.info["content_tenant_id"]="jjtv"
            db.execute(update(Comment).where(Comment.id==row.id,Comment.tenant_id=="jjtv")
                       .values(status="approved",moderation_state="canceled"))
            db.commit()
        return moderation.ModerationResult("block",0.99,"late","jjtv-policy",1)
    monkeypatch.setattr(moderation,"classify",classify)
    moderation.process_comment(row.id)
    result = stored(session,row.id)
    assert result.status=="approved" and result.moderation_state=="canceled"
    assert result.moderation_verdict is None

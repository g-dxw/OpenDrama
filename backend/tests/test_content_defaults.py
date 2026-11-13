"""Native starter configuration preserves operator choices and content ownership."""
from __future__ import annotations

import json
import pytest
from sqlalchemy import func, select

from app.modules.content.models import Drama, DramaHomeGroup, Episode, HomeGroup, Tag
from app.modules.ops.config_models import Banner, ChannelTab
from app.modules.tenancy.models import Tenant
from app.modules.tenancy import scope as _tenant_scope  # noqa: F401, real ownership rules
from app.modules.tenancy import content_defaults as defaults


def _native(db, code):
    tenant = Tenant(id='default-test-'+code,code=code,name='Starter '+code,kind='native',status='active')
    db.add(tenant)
    db.commit()
    return tenant


def _rows(db, model, tenant_id):
    # Verification queries are explicitly owned and read all fixture scopes.
    return list(db.scalars(select(model).where(model.tenant_id==tenant_id)
                           .execution_options(include_all_tenants=True).order_by(model.id)))


def _count(db, model):
    return db.scalar(select(func.count()).select_from(model).execution_options(include_all_tenants=True))


def _drama(db, tenant_id, title, *, status='published', ready=True, plays=0):
    drama = Drama(tenant_id=tenant_id,title=title,status=status,play_count=plays)
    db.add(drama)
    db.flush()
    episode = Episode(drama_id=drama.id,episode_number=1,status='published',duration_seconds=60,
                      bunny_video_id='owned-video-'+str(drama.id) if ready else '')
    db.add(episode)
    db.commit()
    return drama


def test_empty_native_has_useful_configuration_and_no_fabricated_content(session):
    tenant = _native(session,'empty-defaults')
    session.info['content_tenant_id'] = 'jjtv'
    report = defaults.seed_content_defaults(session,tenant)
    assert session.info['content_tenant_id'] == 'jjtv'
    assert report['created'] == {'tags':50,'groups':12,'tabs':6,'group_memberships':0,'banners':0}
    json.dumps(report)
    session.commit()
    tags = _rows(session,Tag,tenant.id)
    groups = _rows(session,HomeGroup,tenant.id)
    tabs = _rows(session,ChannelTab,tenant.id)
    assert len(tags)==50 and all(row.name_en and row.name_zh and row.category in {'main','sub'} for row in tags)
    assert len(groups)==12 and all(row.style in {'poster3','rank','grid2'} and row.badge in {'rank','label'} for row in groups)
    assert len(tabs)==6
    discover = next(row for row in tabs if row.code=='discover')
    assert discover.locked is True and discover.position==0 and discover.group_id is None
    assert all(row.group_id in {group.id for group in groups} for row in tabs if row.code!='discover')
    assert _count(session,Drama)==_count(session,Episode)==_count(session,DramaHomeGroup)==_count(session,Banner)==0
    assert _rows(session,Tag,'jjtv')==[]


def test_rerun_respects_renames_disabled_records_layout_order_and_tab_binding(session):
    tenant = _native(session,'edited-defaults')
    defaults.seed_content_defaults(session,tenant)
    session.commit()
    tag = next(row for row in _rows(session,Tag,tenant.id) if row.default_key=='content.tag.romance')
    group = next(row for row in _rows(session,HomeGroup,tenant.id) if row.default_key=='content.group.romance')
    tab = next(row for row in _rows(session,ChannelTab,tenant.id) if row.code=='romance')
    manual = HomeGroup(tenant_id=tenant.id,name='Operator-only group',position=91,status='inactive')
    session.add(manual)
    session.flush()
    tag.name_en='Operator genre'; tag.name_zh='人工译名'; tag.category='custom'; tag.status='disabled'
    group.name='Operator renamed'; group.position=87; group.status='inactive'; group.style='grid2'; group.badge='rank'
    tab.title_en='Operator tab'; tab.title_zh='人工频道'; tab.position=43; tab.enabled=False; tab.group_id=manual.id
    session.commit()
    before = {model:tuple(row.id for row in _rows(session,model,tenant.id)) for model in (Tag,HomeGroup,ChannelTab)}
    result = defaults.seed_content_defaults(session,tenant)
    assert all(value==0 for value in result['created'].values())
    assert result['reused']['tags']==50 and result['reused']['groups']==12 and result['reused']['tabs']==6
    session.commit()
    assert before=={model:tuple(row.id for row in _rows(session,model,tenant.id)) for model in (Tag,HomeGroup,ChannelTab)}
    session.refresh(tag); session.refresh(group); session.refresh(tab)
    assert (tag.name_en,tag.name_zh,tag.category,tag.status)==('Operator genre','人工译名','custom','disabled')
    assert (group.name,group.position,group.status,group.style,group.badge)==('Operator renamed',87,'inactive','grid2','rank')
    assert (tab.title_en,tab.title_zh,tab.position,tab.enabled,tab.group_id)==('Operator tab','人工频道',43,False,manual.id)
    assert 'content_tenant_id' not in session.info


def test_existing_logical_names_are_claimed_without_overwriting_manual_fields(session):
    tenant = _native(session,'manual-defaults')
    tag = Tag(tenant_id=tenant.id,name_en='rOmAnCe',name_zh='自定义翻译',category='operator',status='disabled')
    group = HomeGroup(tenant_id=tenant.id,name='Most Popular',position=15,status='inactive',style='grid2',badge='label')
    other = HomeGroup(tenant_id=tenant.id,name='Manual channel content',position=18)
    session.add_all([tag,group,other]); session.flush()
    tab = ChannelTab(tenant_id=tenant.id,code='popular',title_en='Custom Popular',title_zh='人工频道',
                     position=20,enabled=False,locked=False,group_id=other.id)
    session.add(tab); session.commit()
    result = defaults.seed_content_defaults(session,tenant)
    session.commit()
    assert result['created']['tags']==49 and result['created']['groups']==11 and result['created']['tabs']==5
    session.refresh(tag); session.refresh(group); session.refresh(tab)
    assert tag.default_key=='content.tag.romance'
    assert (tag.name_en,tag.name_zh,tag.category,tag.status)==('rOmAnCe','自定义翻译','operator','disabled')
    assert group.default_key=='content.group.popular'
    assert (group.name,group.position,group.status,group.style,group.badge)==('Most Popular',15,'inactive','grid2','label')
    assert (tab.title_en,tab.title_zh,tab.position,tab.enabled,tab.group_id)==('Custom Popular','人工频道',20,False,other.id)
    assert all(row.position>18 for row in _rows(session,HomeGroup,tenant.id) if row.id not in {group.id,other.id})
    assert all(row.position>20 for row in _rows(session,ChannelTab,tenant.id) if row.code not in {'popular','discover'})


def test_each_tenant_gets_its_own_rows_and_never_shares_default_ids(session):
    a = _native(session,'first-defaults'); b = _native(session,'second-defaults')
    defaults.seed_content_defaults(session,a); session.commit()
    first = {model:tuple(row.id for row in _rows(session,model,a.id)) for model in (Tag,HomeGroup,ChannelTab)}
    session.info['content_tenant_id'] = a.id
    defaults.seed_content_defaults(session,b); session.commit()
    assert session.info['content_tenant_id']==a.id
    assert first=={model:tuple(row.id for row in _rows(session,model,a.id)) for model in (Tag,HomeGroup,ChannelTab)}
    for model in (Tag,HomeGroup,ChannelTab):
        assert not set(first[model])&{row.id for row in _rows(session,model,b.id)}
    b_groups = {row.id for row in _rows(session,HomeGroup,b.id)}
    assert all(tab.group_id is None or tab.group_id in b_groups for tab in _rows(session,ChannelTab,b.id))


def test_india_defaults_are_rejected_without_touching_content(session):
    india = session.get(Tenant,'india-short-drama')
    before = tuple(_count(session,model) for model in (Tag,HomeGroup,ChannelTab,Drama,Banner))
    with pytest.raises(ValueError,match='native tenants'):
        defaults.seed_content_defaults(session,india,populate_content=True)
    assert before==tuple(_count(session,model) for model in (Tag,HomeGroup,ChannelTab,Drama,Banner))


def test_failed_initialization_restores_scope_and_caller_can_rollback_all_work(session,monkeypatch):
    tenant = _native(session,'rollback-defaults')
    session.info['content_tenant_id'] = 'jjtv'
    def fail(*_args,**_kwargs):
        raise RuntimeError('Controlled transaction failure')
    monkeypatch.setattr(defaults,'_seed_tabs',fail)
    with pytest.raises(RuntimeError,match='Controlled transaction failure'):
        defaults.seed_content_defaults(session,tenant)
    assert session.info['content_tenant_id']=='jjtv'
    session.rollback()
    assert _rows(session,Tag,tenant.id)==_rows(session,HomeGroup,tenant.id)==_rows(session,ChannelTab,tenant.id)==[]


def test_authenticated_context_cannot_be_changed_by_default_initialization(session):
    tenant = _native(session,'blocked-defaults')
    session.info['content_tenant_id'] = 'jjtv'
    session.info['tenant_audit_context'] = {'tenant_id':'jjtv'}
    with pytest.raises(ValueError,match='authenticated tenant context'):
        defaults.seed_content_defaults(session,tenant)
    assert session.info['content_tenant_id']=='jjtv'
    assert _rows(session,Tag,tenant.id)==[]
    session.info.pop('tenant_audit_context')


def test_optional_population_uses_only_owned_published_playable_content(session):
    a = _native(session,'populate-defaults'); b = _native(session,'foreign-defaults')
    own = _drama(session,a.id,'Owned ready',plays=5)
    not_uploaded = _drama(session,a.id,'No uploaded video',ready=False,plays=200)
    draft = _drama(session,a.id,'Never auto-publish',status='draft',plays=300)
    foreign = _drama(session,b.id,'Foreign ready',plays=9999)
    before = (_count(session,Drama),_count(session,Episode))
    first = defaults.seed_content_defaults(session,a,populate_content=True)
    session.commit()
    assert first['created']['banners']==1
    groups = _rows(session,HomeGroup,a.id)
    memberships = list(session.scalars(select(DramaHomeGroup).where(DramaHomeGroup.group_id.in_([row.id for row in groups]))
                                       .execution_options(include_all_tenants=True)))
    assert memberships and {row.drama_id for row in memberships}=={own.id}
    assert {row.drama_id for row in _rows(session,Banner,a.id)}=={own.id}
    assert _rows(session,Banner,b.id)==[]
    assert before==(_count(session,Drama),_count(session,Episode))
    session.refresh(draft); session.refresh(not_uploaded); session.refresh(foreign)
    assert draft.status=='draft' and not_uploaded.status=='published' and foreign.play_count==9999
    second = defaults.seed_content_defaults(session,a,populate_content=True)
    assert second['created']['group_memberships']==second['created']['banners']==0
    session.commit()


def test_manual_empty_or_populated_groups_and_offline_banners_are_not_filled(session):
    tenant = _native(session,'keep-operations')
    playable = _drama(session,tenant.id,'Playable only')
    manual = HomeGroup(tenant_id=tenant.id,name='热门推荐',position=18,status='inactive',style='grid2',badge='label')
    empty = HomeGroup(tenant_id=tenant.id,name='新剧上架',position=19,status='inactive')
    session.add_all([manual,empty]); session.flush()
    session.add(DramaHomeGroup(group_id=manual.id,drama_id=playable.id,position_in_group=9))
    banner = Banner(tenant_id=tenant.id,slot='home',drama_id=playable.id,status='offline',position=8,headline='Operator copy')
    session.add(banner); session.commit()
    result = defaults.seed_content_defaults(session,tenant,populate_content=True)
    session.commit()
    assert result['created']['banners']==0
    session.refresh(manual); session.refresh(empty); session.refresh(banner)
    assert manual.status==empty.status=='inactive'
    links = list(session.scalars(select(DramaHomeGroup).where(DramaHomeGroup.group_id==manual.id)))
    assert len(links)==1 and links[0].position_in_group==9
    assert list(session.scalars(select(DramaHomeGroup).where(DramaHomeGroup.group_id==empty.id)))==[]
    assert len(_rows(session,Banner,tenant.id))==1
    assert (banner.status,banner.position,banner.headline)==('offline',8,'Operator copy')


def test_optional_population_of_empty_native_never_invents_video_or_banner(session):
    tenant = _native(session,'no-videos')
    result = defaults.seed_content_defaults(session,tenant,populate_content=True)
    session.commit()
    assert result['created']['group_memberships']==result['created']['banners']==0
    assert _count(session,Drama)==_count(session,Episode)==_count(session,Banner)==0
    assert any('No published playable content' in note for note in result['notes'])


def test_new_tabs_do_not_reenable_a_manually_disabled_group(session):
    tenant = _native(session,'inactive-template-group')
    group = HomeGroup(tenant_id=tenant.id,name='Most Popular',position=35,status='inactive',style='grid2',badge='label')
    session.add(group); session.commit()
    defaults.seed_content_defaults(session,tenant); session.commit()
    tab = next(row for row in _rows(session,ChannelTab,tenant.id) if row.code=='popular')
    session.refresh(group)
    assert tab.group_id==group.id and tab.enabled is False
    assert (group.position,group.status,group.style,group.badge)==(35,'inactive','grid2','label')
    again = defaults.seed_content_defaults(session,tenant)
    assert not any(again['created'].values())

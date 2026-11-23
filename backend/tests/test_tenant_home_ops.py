"""Tenant-owned homepage config never opens unrelated global operations."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.main import app
from app.modules.content.models import Drama, HomeGroup
from app.modules.ops.config_models import Banner, ChannelTab, CopyEntry
from app.modules.tenancy.models import LegacyBusinessBinding, Tenant, TenantModule
from app.modules.tenancy.permissions import seed_tenant_defaults


@pytest.fixture()
def home_context(session,make):
    tenants=[]
    for key in ('home-native-a','home-native-b'):
        tenant=Tenant(id=key,code=key,name=key,kind='native',status='active')
        session.add(tenant);session.flush();seed_tenant_defaults(session,tenant)
        tenants.append(tenant)
    session.commit()
    client=TestClient(app)
    def headers(tenant):
        user=make.admin(role='admin',tenant_id=tenant.id)
        response=client.post('/tenant/v1/auth/login',json={'tenant_code':tenant.code,'username':user.username,'password':'pass-1234'})
        assert response.status_code==200,response.text
        return user,{'Authorization':'Bearer '+response.json()['token']}
    aa,ah=headers(tenants[0]);ba,bh=headers(tenants[1])
    jjtv=session.get(Tenant,'jjtv');ja,jh=headers(jjtv)
    def content(tenant,label):
        drama=Drama(tenant_id=tenant.id,title=label+' drama',status='published')
        group=HomeGroup(tenant_id=tenant.id,name=label+' group',status='active')
        session.add_all([drama,group]);session.commit()
        return drama,group
    ad,ag=content(tenants[0],'A');bd,bg=content(tenants[1],'B');jd,jg=content(jjtv,'JJTV')
    return dict(db=session,client=client,a=tenants[0],b=tenants[1],auser=aa,ah=ah,bh=bh,jh=jh,ad=ad,ag=ag,bd=bd,bg=bg,jd=jd,jg=jg)


def post_tab(ctx,headers,code='discover',group=None):
    data={'code':code,'title_en':code.title(),'title_zh':'频道','position':0}
    if group is not None:data['group_id']=group
    result=ctx['client'].post('/admin/v1/tabs',json=data,headers=headers)
    assert result.status_code==200,result.text
    return result.json()


def post_banner(ctx,headers,drama_id):
    result=ctx['client'].post('/admin/v1/banners',json={'drama_id':drama_id,'headline':'Owned banner'},headers=headers)
    assert result.status_code==200,result.text
    return result.json()


def test_home_channels_and_banners_are_independent_per_native_tenant(home_context):
    ctx=home_context;c=ctx['client']
    a=post_tab(ctx,ctx['ah']);b=post_tab(ctx,ctx['bh'])
    assert a['id']!=b['id'] and a['code']==b['code']=='discover'
    ab=post_banner(ctx,ctx['ah'],ctx['ad'].id);bb=post_banner(ctx,ctx['bh'],ctx['bd'].id)
    assert ab['id']!=bb['id']
    for headers,tab,banner in ((ctx['ah'],a,ab),(ctx['bh'],b,bb)):
        assert [row['id'] for row in c.get('/admin/v1/tabs',headers=headers).json()['items']]==[tab['id']]
        assert [row['id'] for row in c.get('/admin/v1/banners',headers=headers).json()['items']]==[banner['id']]
    context=c.get('/tenant/v1/context',headers=ctx['ah']).json()
    assert 'home_ops' in context['enabled_modules'] and '/home-ops' in context['menu_keys']
    # Finance is now explicitly adapted to native sites; unrelated legacy
    # supplier, settlement and support operations remain unavailable.
    assert 'finance' in context['enabled_modules'] and '/finance' in context['menu_keys']
    for module in ('suppliers','settlements','support'):
        assert module not in context['enabled_modules'] and '/'+module not in context['menu_keys']


def test_foreign_home_resource_ids_and_associations_are_not_accepted(home_context):
    ctx=home_context;c=ctx['client']
    a=post_tab(ctx,ctx['ah'],'own-channel',ctx['ag'].id);b=post_tab(ctx,ctx['bh'],'other-channel',ctx['bg'].id)
    ab=post_banner(ctx,ctx['ah'],ctx['ad'].id);bb=post_banner(ctx,ctx['bh'],ctx['bd'].id)
    for method,path,body in [
        ('PATCH','/tabs/'+str(b['id']),{'title_en':'Wrong owner'}),
        ('DELETE','/tabs/'+str(b['id']),None),
        ('POST','/tabs',{'code':'foreign-group','title_en':'Foreign group','group_id':ctx['bg'].id}),
        ('PATCH','/tabs/'+str(a['id']),{'group_id':ctx['bg'].id}),
        ('POST','/banners',{'drama_id':ctx['bd'].id}),
        ('PATCH','/banners/'+str(ab['id']),{'drama_id':ctx['bd'].id}),
        ('PATCH','/banners/'+str(bb['id']),{'headline':'Wrong owner'}),
        ('DELETE','/banners/'+str(bb['id']),None),
    ]:
        response=c.request(method,'/admin/v1'+path,json=body,headers=ctx['ah'])
        assert response.status_code==404,(method,path,response.text)


def test_database_constraints_reject_cross_tenant_home_references(home_context):
    ctx=home_context;db=ctx['db']
    for row in (
        ChannelTab(tenant_id=ctx['a'].id,code='cross-group',title_en='Cross group',group_id=ctx['bg'].id),
        Banner(tenant_id=ctx['a'].id,drama_id=ctx['bd'].id),
    ):
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.add(row);db.flush()


def test_native_home_permission_does_not_open_global_config_or_pushes(home_context):
    ctx=home_context;c=ctx['client'];db=ctx['db']
    assert db.get(LegacyBusinessBinding,'home_ops').tenant_id=='jjtv'
    db.add(CopyEntry(key='home-private-copy',en='Existing JJTV copy',zh='旧自营文案'));db.commit()
    for method,path,params,body in [
        ('GET','/copies',{},None),
        ('POST','/copies',{},dict(key='outside',en='Must not insert')),
        ('POST','/hot-searches',{'term':'Must not insert'},None),
        ('POST','/marketing/stats',{'label':'Must not insert','value':'0'},None),
        ('POST','/legal/terms',{'title':'Must not change','body':'Text'},None),
        ('POST','/stores',{'platform':'android','url':'https://example.com/app'},None),
        ('GET','/pushes',{},None),
        ('POST','/pushes',{'title':'Must not push'},None),
    ]:
        response=c.request(method,'/admin/v1'+path,params=params,json=body,headers=ctx['ah'])
        assert response.status_code==403,(method,path,response.text)
    response=c.get('/admin/v1/copies',headers=ctx['jh'])
    assert response.status_code==200 and any(row['key']=='home-private-copy' for row in response.json()['items'])


def test_public_home_config_stays_on_jjtv_and_discover_sorts_first(home_context):
    ctx=home_context;c=ctx['client']
    # Preserve an operator-defined position 0. Newly created Discover wins by
    # fixed code priority rather than rewriting that operator's stored value.
    manual=post_tab(ctx,ctx['jh'],'manual-zero',ctx['jg'].id)
    discover=post_tab(ctx,ctx['jh'],'discover')
    foreign=post_tab(ctx,ctx['ah'],'discover')
    post_banner(ctx,ctx['jh'],ctx['jd'].id);post_banner(ctx,ctx['ah'],ctx['ad'].id)
    tabs=c.get('/admin/v1/tabs',headers=ctx['jh']).json()['items']
    assert [row['id'] for row in tabs]==[discover['id'],manual['id']]
    assert all(row['position']==0 for row in tabs)
    public=c.get('/v1/config',headers={'X-OpenDrama-Tenant':ctx['a'].id}).json()['tabs']
    assert [row['code'] for row in public]==['discover','manual-zero']
    assert all(row['group_id']!=ctx['ag'].id for row in public)
    assert foreign['id'] not in {row['id'] for row in tabs}
    home=c.get('/v1/home/groups',headers={'X-OpenDrama-Tenant':ctx['a'].id}).json()
    assert [row['drama_id'] for row in home['banners']]==[ctx['jd'].id]
    assert {row['id'] for row in home['groups']}=={ctx['jg'].id}


def test_disabling_home_module_blocks_existing_session_and_api_key(home_context):
    ctx=home_context;c=ctx['client'];db=ctx['db']
    post_tab(ctx,ctx['ah'])
    issued=c.post('/tenant/v1/api-keys',json={'user_id':ctx['auser'].id,'name':'Home key','scopes':['read','write'],'expires_in_days':90},headers=ctx['ah'])
    assert issued.status_code==201,issued.text
    key={'Authorization':'Bearer '+issued.json()['key']}
    assert c.get('/admin/v1/tabs',headers=key).status_code==200
    opening=db.get(TenantModule,(ctx['a'].id,'home_ops'));opening.enabled=False;db.commit()
    for headers in (ctx['ah'],key):
        assert c.get('/admin/v1/tabs',headers=headers).status_code==403
        assert c.post('/admin/v1/tabs',json={'code':'closed','title_en':'Closed'},headers=headers).status_code==403
    opening.enabled=True;db.commit()
    assert c.get('/admin/v1/tabs',headers=ctx['ah']).status_code==200


def test_home_migration_preserves_existing_ids_configuration_and_owner(engine,monkeypatch):
    import re
    from pathlib import Path
    from uuid import uuid4
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    import sqlalchemy as sa

    source=engine.url.database
    assert source and re.fullmatch(r'[a-z][a-z0-9_]{0,40}',source)
    name=source+'_home_'+uuid4().hex[:8]
    assert len(name)<=63 and name.startswith(source+'_home_')
    admin=sa.create_engine(engine.url.set(database='postgres'),isolation_level='AUTOCOMMIT')
    target_url=engine.url.set(database=name);target=None;created=False
    try:
        with admin.connect() as db:
            db.execute(sa.text('CREATE DATABASE "'+name+'"'));created=True
        backend=Path(__file__).resolve().parents[1]
        cfg=Config(str(backend/'alembic.ini'));cfg.set_main_option('script_location',str(backend/'alembic'))
        cfg.set_main_option('sqlalchemy.url',target_url.render_as_string(hide_password=False).replace('%','%%'))
        with monkeypatch.context() as local:
            local.setenv('ALEMBIC_DATABASE_URL',target_url.render_as_string(hide_password=False))
            command.upgrade(cfg,'c4d8e0a1b295')
            target=sa.create_engine(target_url)
            with target.begin() as db:
                # c4 has no desktop pricing/language columns; use its real shape.
                drama_id=db.execute(sa.text("INSERT INTO dramas(tenant_id,title,status,synopsis,cover_url,free_episode_count,coin_price_per_episode,country_allowlist,play_count,review_state) VALUES ('jjtv','Retained home migration drama','draft','','',0,0,'',0,'none') RETURNING id")).scalar_one()
            with target.begin() as db:
                group_id=db.execute(sa.text("INSERT INTO home_groups(tenant_id,name,position,status,style,badge) VALUES ('jjtv','Operator group',77,'inactive','grid2','label') RETURNING id")).scalar_one()
                tag_id=db.execute(sa.text("INSERT INTO tags(tenant_id,name_en,name_zh,category,status) VALUES ('jjtv','Operator tag','人工标签','custom','disabled') RETURNING id")).scalar_one()
                tab_id=db.execute(sa.text("INSERT INTO channel_tabs(code,title_en,title_zh,position,enabled,locked,group_id) VALUES ('operator-channel','Operator title','人工频道',77,false,false,:group_id) RETURNING id"),dict(group_id=group_id)).scalar_one()
                banner_id=db.execute(sa.text("INSERT INTO banners(slot,drama_id,status,position,kicker,headline) VALUES ('home',:drama,'offline',5,'Operator hook','Operator headline') RETURNING id"),dict(drama=drama_id)).scalar_one()
            command.upgrade(cfg,'head')
            with target.begin() as db:
                tab=db.execute(sa.text('SELECT id,tenant_id,title_en,title_zh,position,enabled,locked,group_id FROM channel_tabs WHERE id=:id'),dict(id=tab_id)).mappings().one()
                assert dict(tab)==dict(id=tab_id,tenant_id='jjtv',title_en='Operator title',title_zh='人工频道',position=77,enabled=False,locked=False,group_id=group_id)
                banner=db.execute(sa.text('SELECT id,tenant_id,drama_id,status,position,kicker,headline FROM banners WHERE id=:id'),dict(id=banner_id)).mappings().one()
                assert dict(banner)==dict(id=banner_id,tenant_id='jjtv',drama_id=drama_id,status='offline',position=5,kicker='Operator hook',headline='Operator headline')
                assert db.execute(sa.text('SELECT name_en,status,default_key FROM tags WHERE id=:id'),dict(id=tag_id)).one()==('Operator tag','disabled',None)
                assert db.execute(sa.text('SELECT name,position,status,style,badge,default_key FROM home_groups WHERE id=:id'),dict(id=group_id)).one()==('Operator group',77,'inactive','grid2','label',None)
                db.execute(sa.text("INSERT INTO tenants(id,code,name,kind,status) VALUES ('migration-home-other','migration-home-other','Other','native','active')"))
                own_group=db.execute(sa.text("INSERT INTO home_groups(tenant_id,name,position,status,style,badge) VALUES ('migration-home-other','Other group',0,'active','rank','rank') RETURNING id")).scalar_one()
                db.execute(sa.text("INSERT INTO channel_tabs(tenant_id,code,title_en,title_zh,position,enabled,locked,group_id) VALUES ('migration-home-other','operator-channel','Independent same code','',0,true,false,:group_id)"),dict(group_id=own_group))
                assert db.execute(sa.text("SELECT count(*) FROM channel_tabs WHERE code='operator-channel'")).scalar_one()==2
            with pytest.raises(RuntimeError,match='cannot be merged safely'):
                command.downgrade(cfg,'c4d8e0a1b295')
            home_migration=ScriptDirectory.from_config(cfg).get_revision('e6a1c3d9b742').module
            with pytest.raises(RuntimeError,match='cannot be recombined'):
                home_migration.downgrade()
    finally:
        if target is not None:target.dispose()
        if created:
            with admin.connect() as db:
                row=db.execute(sa.text('SELECT pg_get_userbyid(datdba),current_user FROM pg_database WHERE datname=:name'),dict(name=name)).first()
                assert row is not None and row[0]==row[1] and name.startswith(source+'_home_')
                db.execute(sa.text('DROP DATABASE "'+name+'" WITH (FORCE)'))
        admin.dispose()

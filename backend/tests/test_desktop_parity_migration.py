"""Exercise this feature's real e6 -> head migration with populated old tables."""
from datetime import datetime,timedelta,timezone
from pathlib import Path
import json
import re
from uuid import uuid4
import sqlalchemy as sa
from sqlalchemy.orm import Session
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory


def test_desktop_upgrade_preserves_legacy_content_money_membership_and_secret_boundaries(engine,monkeypatch):
    source=engine.url.database
    assert source and re.fullmatch(r'[a-z][a-z0-9_]{0,40}',source)
    name=source+'_desktop_'+uuid4().hex[:8]
    assert len(name)<=63 and name.startswith(source+'_desktop_')
    admin=sa.create_engine(engine.url.set(database='postgres'),isolation_level='AUTOCOMMIT')
    target_url=engine.url.set(database=name);target=None;created=False
    moment=datetime.now(timezone.utc).replace(microsecond=0)
    period_start=moment-timedelta(days=1);period_end=moment+timedelta(days=29);pass_end=moment+timedelta(days=45)
    subtitle_path='subs/historical-default.vtt'
    sentinel='sk_test_MUST_NOT_BE_IMPORTED_BY_MIGRATION'
    try:
        with admin.connect() as db:
            db.execute(sa.text('CREATE DATABASE "'+name+'"'));created=True
        backend=Path(__file__).resolve().parents[1]
        config=Config(str(backend/'alembic.ini'));config.set_main_option('script_location',str(backend/'alembic'))
        config.set_main_option('sqlalchemy.url',target_url.render_as_string(hide_password=False).replace('%','%%'))
        with monkeypatch.context() as local:
            local.setenv('ALEMBIC_DATABASE_URL',target_url.render_as_string(hide_password=False))
            local.setenv('STRIPE_SECRET_KEY',sentinel)
            local.setenv('RESEND_API_KEY','re_MUST_NOT_BE_IMPORTED_BY_MIGRATION')
            command.upgrade(config,'e6a1c3d9b742')
            target=sa.create_engine(target_url)
            with target.begin() as db:
                db.execute(sa.text("INSERT INTO tenants(id,code,name,kind,status) VALUES ('desktop-other','desktop-other','Other native project','native','active')"))
                user_id=db.execute(sa.text("INSERT INTO users(email,display_name,avatar_url,gender,bio,region,locale,following_count,follower_count,received_likes,reminder_opt_in) VALUES ('desktop-migration@example.com','Historical viewer','','unspecified','','','en',0,0,0,false) RETURNING id")).scalar_one()
                db.execute(sa.text('INSERT INTO wallet_accounts(user_id,balance) VALUES (:user_id,321)'),dict(user_id=user_id))
                drama_id=db.execute(sa.text("INSERT INTO dramas(tenant_id,title,status,synopsis,cover_url,free_episode_count,coin_price_per_episode,country_allowlist,play_count,review_state) VALUES ('jjtv','Historical published drama','published','Original synopsis','original-cover.jpg',2,60,'',7,'none') RETURNING id")).scalar_one()
                episode_id=db.execute(sa.text("INSERT INTO episodes(drama_id,episode_number,title,duration_seconds,bunny_video_id,subtitle_url,width,height,size_bytes,mime,status) VALUES (:drama,3,'Historical episode',60,'historical-video',:subtitle,1080,1920,10000,'video/mp4','published') RETURNING id"),dict(drama=drama_id,subtitle=subtitle_path)).scalar_one()
                plan_id=db.execute(sa.text("INSERT INTO plans(code,name_en,name_zh,status) VALUES ('monthly','Historical Monthly','原月卡','active') RETURNING id")).scalar_one()
                version_id=db.execute(sa.text("INSERT INTO plan_versions(plan_id,version,price_cents,currency,benefits,note,stripe_price_id) VALUES (:plan,1,1999,'USD',CAST(:benefits AS jsonb),'Original immutable price','price_original') RETURNING id"),dict(plan=plan_id,benefits=json.dumps({'items':['Original entitlement']}))).scalar_one()
                order_id=db.execute(sa.text("INSERT INTO orders(order_no,user_id,plan_version_id,amount_cents,currency,billing_mode,status,external_txn_id,paid_at,source_drama_id) VALUES ('ORD-LEGACY-DESKTOP',:user,:version,1999,'USD','subscription','paid','cs_original',:paid,:drama) RETURNING id"),dict(user=user_id,version=version_id,paid=period_start,drama=drama_id)).scalar_one()
                subscription_id=db.execute(sa.text("INSERT INTO subscriptions(user_id,plan_id,plan_version_id,status,current_period_start,current_period_end,stripe_customer_id,stripe_subscription_id,stripe_price_id,billing_mode,pass_end,source_drama_id) VALUES (:user,:plan,:version,'active',:starts,:ends,'cus_original','sub_original','price_original','subscription',:pass_end,:drama) RETURNING id"),dict(user=user_id,plan=plan_id,version=version_id,starts=period_start,ends=period_end,pass_end=pass_end,drama=drama_id)).scalar_one()
                old_counts={table:db.scalar(sa.text('SELECT count(*) FROM '+table)) for table in ('users','wallet_accounts','dramas','episodes','plans','plan_versions','orders','subscriptions','coin_ledger','audit_logs')}
            command.upgrade(config,'head')
            with target.connect() as db:
                assert db.scalar(sa.text('SELECT version_num FROM alembic_version'))==ScriptDirectory.from_config(config).get_current_head()
                for table,count in old_counts.items():assert db.scalar(sa.text('SELECT count(*) FROM '+table))==count
                assert db.execute(sa.text('SELECT id,title,status,free_episode_count,coin_price_per_episode,access_mode,member_pays_coins,non_member_free_episodes,language FROM dramas WHERE id=:id'),dict(id=drama_id)).one()==(drama_id,'Historical published drama','published',2,60,'public',False,None,'')
                assert db.execute(sa.text('SELECT id,drama_id,episode_number,status,subtitle_url,coin_price FROM episodes WHERE id=:id'),dict(id=episode_id)).one()==(episode_id,drama_id,3,'published',subtitle_path,None)
                assert db.execute(sa.text('SELECT episode_id,language,label,url,is_default FROM episode_subtitles')).one()==(episode_id,'und','Default',subtitle_path,True)
                assert db.execute(sa.text('SELECT id,plan_version_id,amount_cents,status,external_txn_id,product_type,product_snapshot,account_code FROM orders WHERE id=:id'),dict(id=order_id)).one()==(order_id,version_id,1999,'paid','cs_original','membership',{},'env')
                assert db.execute(sa.text('SELECT id,base_coins,bonus_coins,benefits FROM plan_versions WHERE id=:id'),dict(id=version_id)).one()==(version_id,0,0,{'items':['Original entitlement']})
                assert db.execute(sa.text('SELECT id,plan_version_id,stripe_subscription_id,current_period_end,pass_end,account_code,managed_commerce FROM subscriptions WHERE id=:id'),dict(id=subscription_id)).one()==(subscription_id,version_id,'sub_original',period_end,pass_end,'env',False)
                assert db.execute(sa.text('SELECT user_id,balance FROM wallet_accounts')).one()==(user_id,321)
                assert db.execute(sa.text('SELECT user_id,order_id,original,remaining,reserved FROM coin_sources')).one()==(user_id,None,321,321,0)
                new_keys={'login_settings','stripe','coin_packs','coins','attribution','support'}
                bindings=dict(db.execute(sa.text('SELECT module_key,tenant_id FROM legacy_business_bindings')).all())
                assert {key:bindings[key] for key in new_keys}=={key:'jjtv' for key in new_keys}
                modules=list(db.execute(sa.text("SELECT tenant_id,module_key,enabled FROM tenant_modules WHERE module_key IN ('login_settings','stripe','coin_packs','coins','attribution','support')")))
                for tenant in ('jjtv','india-short-drama','desktop-other'):
                    assert {key:enabled for owner,key,enabled in modules if owner==tenant}=={key:tenant=='jjtv' for key in new_keys}
                # Migrations provision schema and legacy metadata, never import
                # environment credentials or fabricate sales/tickets/conversions.
                empty_tables=('managed_settings','coin_packs','coin_pack_versions','stripe_price_bindings','coin_allocations','membership_grants','commerce_settlements','commerce_refunds','support_config','support_tickets','support_messages','support_events','attribution_pixels','attribution_sources','attribution_plans','attribution_events','attribution_visits','user_attribution')
                for table in empty_tables:assert db.scalar(sa.text('SELECT count(*) FROM '+table))==0
                accounts=[dict(row) for row in db.execute(sa.text('SELECT * FROM stripe_accounts')).mappings()]
                assert len(accounts)==1 and accounts[0]['code']=='env'
                assert sentinel not in json.dumps(accounts) and 'secret_key' not in accounts[0] and 'webhook_secret' not in accounts[0]
            from app.modules.billing import service as billing
            from app.modules.content.models import Drama,Episode
            from app.modules.content.pricing import episode_access
            from app.modules.playback import service as playback
            local.setenv('BUNNY_MODE','fake');local.setattr(playback,'generation',lambda _episode_id:0)
            with Session(target) as db:
                db.info['content_tenant_id']='jjtv'
                drama=db.get(Drama,drama_id);episode=db.get(Episode,episode_id)
                assert billing.access_until(db,user_id)==pass_end
                assert not episode_access(drama,episode,member=False)['allowed']
                assert episode_access(drama,episode,member=True)['allowed']
                authorized=playback.authorize(db,drama_id=drama_id,episode_id=episode_id,user_id=user_id)
                assert authorized['allowed'] and len(authorized['subtitles'])==1
                assert authorized['subtitles'][0]['language']=='und' and authorized['subtitles'][0]['is_default']
    finally:
        if target is not None:target.dispose()
        if created:
            with admin.connect() as db:
                owner=db.execute(sa.text('SELECT pg_get_userbyid(datdba),current_user FROM pg_database WHERE datname=:name'),dict(name=name)).first()
                assert owner is not None and owner[0]==owner[1] and name.startswith(source+'_desktop_')
                db.execute(sa.text('DROP DATABASE "'+name+'" WITH (FORCE)'))
        admin.dispose()

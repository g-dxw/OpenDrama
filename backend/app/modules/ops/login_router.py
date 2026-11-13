"""Operator-managed login configuration for the explicit JJTV owner."""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from app.db import get_db
from app.modules.common.models import AuditLog
from app.modules.ops.router import require_admin
from app.modules.ops.models import AdminUser
from app.modules.ops.managed_settings import ManagedSetting, get_config, login_defaults, login_runtime, public_config, save_config
router=APIRouter(prefix='/admin/v1/login',tags=['login-settings'])
SECRETS=('email_api_key','facebook_app_secret')

class SettingsIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    email_enabled: bool | None=None
    email_api_key: str | None=Field(default=None,max_length=500)
    email_from: str | None=Field(default=None,max_length=320)
    google_enabled: bool | None=None
    google_client_ids: list[str] | None=Field(default=None,max_length=20)
    facebook_enabled: bool | None=None
    facebook_app_id: str | None=Field(default=None,max_length=100)
    facebook_app_secret: str | None=Field(default=None,max_length=500)
    clear_email_api_key: bool=False
    clear_facebook_app_secret: bool=False

class TestIn(BaseModel):
    email: str | None=Field(default=None,max_length=320)

def output(db): return public_config(db,'login',login_defaults(),secret_fields=SECRETS)

def audit(db,user,action,fields):
    db.add(AuditLog(actor_type='admin',actor_id=user.id,action=action,target_type='login_settings',target_id=None,payload={'fields':sorted(fields)}))

@router.get('/settings')
def settings(db:Session=Depends(get_db),user:AdminUser=Depends(require_admin)):
    return output(db)

@router.post('/settings')
def update(body:SettingsIn,db:Session=Depends(get_db),user:AdminUser=Depends(require_admin)):
    data=body.model_dump(exclude_unset=True)
    if any(value is None for key,value in data.items() if key not in SECRETS):
        raise HTTPException(422,detail='Configuration fields cannot be null')
    if 'google_client_ids' in data:
        data['google_client_ids']=list(dict.fromkeys(value.strip() for value in data['google_client_ids'] if value.strip()))
        if any(not value.endswith('.apps.googleusercontent.com') or len(value)>300 for value in data['google_client_ids']): raise HTTPException(422,detail='Invalid Google client ID')
    if 'facebook_app_id' in data and data['facebook_app_id'] and not data['facebook_app_id'].isdigit(): raise HTTPException(422,detail='Facebook App ID must contain digits')
    save_config(db,'login',data,secret_fields=SECRETS)
    audit(db,user,'login.settings.update',data.keys());db.commit()
    return output(db)

@router.post('/import-env')
def import_env(db:Session=Depends(get_db),user:AdminUser=Depends(require_admin)):
    row=db.get(ManagedSetting,'login')
    saved=set(row.values or {})|set(row.secrets or {}) if row else set()
    data={k:v for k,v in login_defaults().items() if k not in saved and v not in ('',None,[])}
    save_config(db,'login',data,secret_fields=SECRETS)
    audit(db,user,'login.settings.import',data.keys());db.commit()
    return output(db)

@router.post('/test/{provider}')
def test_provider(provider:str,body:TestIn,db:Session=Depends(get_db),user:AdminUser=Depends(require_admin)):
    if provider not in {'email','google','facebook'}: raise HTTPException(404,detail='Unknown login provider')
    from app.modules.account.service import AccountError
    try:
        with login_runtime(db,provider) as cfg:
            if provider=='email':
                if not body.email or '@' not in body.email: raise HTTPException(422,detail='A test recipient email is required')
                from app.modules.account.mail import send_email,MailError
                try: send_email(to=body.email,subject='JJTV login configuration test',text='This email confirms your JJTV sender configuration. No sign-in code was issued.')
                except MailError: raise HTTPException(502,detail='Email provider connection failed; check sender verification and credentials') from None
            elif provider=='google':
                if not cfg.get('google_client_ids'): raise HTTPException(422,detail='Google client ID is required')
                from app.modules.account.google import _certs,GoogleAuthError
                try: _certs()
                except GoogleAuthError: raise HTTPException(502,detail='Google signing keys are unavailable') from None
            else:
                from app.modules.account.facebook import _request_json,GRAPH_BASE,FacebookAuthError
                if not cfg.get('facebook_app_id') or not cfg.get('facebook_app_secret'): raise HTTPException(422,detail='Facebook App ID and secret are required')
                try: _request_json(GRAPH_BASE+'/'+cfg['facebook_app_id'],dict(fields='id',access_token=cfg['facebook_app_id']+'|'+cfg['facebook_app_secret']))
                except FacebookAuthError: raise HTTPException(502,detail='Facebook connection failed; check app credentials') from None
    except AccountError as exc: raise HTTPException(exc.status_code,detail=exc.detail) from exc
    audit(db,user,'login.settings.test',[provider]);db.commit()
    return dict(ok=True,provider=provider,detail='Connection checked; end-user sign-in still requires an allowed origin and real user verification')

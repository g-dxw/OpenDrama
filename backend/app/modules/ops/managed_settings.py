"""JJTV-only managed configuration. Secret values never leave internal services."""
from __future__ import annotations
import copy
import os
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from sqlalchemy import String, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base

class ManagedSetting(Base):
    __tablename__ = 'managed_settings'
    namespace: Mapped[str] = mapped_column(String(80), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.id'), nullable=False, default='jjtv', server_default='jjtv')
    values: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    secrets: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

_runtime = ContextVar('jjtv_login_config', default=None)

def config_value(name, default=''):
    current = _runtime.get()
    return current.get(name, default) if current is not None and name in current else os.environ.get(name, default)

def _cipher():
    value = os.environ.get('SETTINGS_ENCRYPTION_KEY', '').strip()
    if not value:
        raise HTTPException(503, detail='SETTINGS_ENCRYPTION_KEY must be configured to access managed credentials')
    try:
        return Fernet(value.encode())
    except (ValueError, TypeError):
        raise HTTPException(503, detail='SETTINGS_ENCRYPTION_KEY is invalid') from None

def scoped_namespace(db, namespace):
    from app.modules.tenancy.consumer_scope import current_site
    site=current_site(db)
    return namespace if namespace.startswith('stripe:') or site=='jjtv' else 'site:'+site+':'+namespace

def get_config(db, namespace, defaults=None):
    namespace = scoped_namespace(db, namespace)
    result = copy.deepcopy(defaults or {})
    row = db.get(ManagedSetting, namespace)
    if row is None:
        return result
    expected_owner = 'jjtv' if namespace.startswith('stripe:') else (db.info.get('content_tenant_id') or 'jjtv')
    if row.tenant_id != expected_owner:
        raise HTTPException(403, detail='Configuration owner mismatch')
    result.update(row.values or {})
    for key, encrypted in (row.secrets or {}).items():
        if encrypted is None:
            result[key] = ''
        else:
            try:
                result[key] = _cipher().decrypt(encrypted.encode()).decode()
            except InvalidToken:
                raise HTTPException(503, detail='Managed credential cannot be decrypted') from None
    return result

def save_config(db, namespace, data, secret_fields=()):
    original_namespace = namespace
    namespace = scoped_namespace(db, namespace)
    row = db.get(ManagedSetting, namespace)
    if row is None:
        row = ManagedSetting(namespace=namespace, tenant_id='jjtv' if namespace.startswith('stripe:') else (db.info.get('content_tenant_id') or 'jjtv'), values={}, secrets={})
        db.add(row)
    expected_owner = 'jjtv' if namespace.startswith('stripe:') else (db.info.get('content_tenant_id') or 'jjtv')
    if row.tenant_id != expected_owner:
        raise HTTPException(403, detail='Configuration owner mismatch')
    values, secrets = dict(row.values or {}), dict(row.secrets or {})
    secret_keys = set(secret_fields) | set(secrets)
    for key, value in data.items():
        if key.startswith('clear_'):
            continue
        if key in secret_keys:
            if isinstance(value, str) and value.strip():
                secrets[key] = _cipher().encrypt(value.strip().encode()).decode()
        else:
            values[key] = value
    for key in secret_keys:
        if data.get('clear_' + key) is True:
            secrets[key] = None
    row.values, row.secrets = values, secrets
    row.updated_at = datetime.now(timezone.utc)
    db.flush()
    return get_config(db, original_namespace)

def public_config(db, namespace, defaults=None, secret_fields=()):
    result = get_config(db, namespace, defaults)
    row = db.get(ManagedSetting, scoped_namespace(db, namespace))
    for key in set(secret_fields) | set(row.secrets or {} if row else {}):
        value = result.pop(key, '')
        result[key + '_configured'] = bool(value)
        result[key + '_masked'] = '••••••••' if value else ''
    result['source'] = 'database' if row else 'environment'
    result['updated_at'] = row.updated_at.isoformat() if row and row.updated_at else None
    return result

def login_defaults(db=None):
    if db is not None and (db.info.get('content_tenant_id') or 'jjtv') != 'jjtv':
        return dict(email_enabled=True,email_api_key='',email_from='',google_enabled=False,google_client_ids=[],facebook_enabled=False,facebook_app_id='',facebook_app_secret='')
    clients = list(dict.fromkeys(x.strip() for name in ('GOOGLE_CLIENT_ID','GOOGLE_CLIENT_IDS') for x in os.environ.get(name,'').split(',') if x.strip()))
    return dict(email_enabled=True,email_api_key=os.environ.get('RESEND_API_KEY',''),email_from=os.environ.get('RESEND_FROM',''),google_enabled=bool(clients),google_client_ids=clients,facebook_enabled=bool(os.environ.get('FACEBOOK_APP_ID','')),facebook_app_id=os.environ.get('FACEBOOK_APP_ID',''),facebook_app_secret=os.environ.get('FACEBOOK_APP_SECRET',''))

def login_config(db):
    return get_config(db, 'login', login_defaults(db))

@contextmanager
def login_runtime(db, provider=None):
    config = login_config(db)
    if provider and not config.get(provider + '_enabled'):
        from app.modules.account.service import AccountError
        raise AccountError(403, provider + ' sign in is disabled')
    clients = config.get('google_client_ids') or []
    token = _runtime.set(dict(RESEND_API_KEY=config.get('email_api_key',''),RESEND_FROM=config.get('email_from',''),GOOGLE_CLIENT_ID=clients[0] if clients else '',GOOGLE_CLIENT_IDS=','.join(clients),FACEBOOK_APP_ID=config.get('facebook_app_id',''),FACEBOOK_APP_SECRET=config.get('facebook_app_secret','')))
    try:
        yield config
    finally:
        _runtime.reset(token)

def encrypt_secret(value: str) -> str:
    return _cipher().encrypt(value.encode()).decode()

def decrypt_secret(value: str) -> str:
    try:
        return _cipher().decrypt(value.encode()).decode()
    except InvalidToken:
        raise HTTPException(503, detail='Managed credential cannot be decrypted') from None

def managed_provider(provider):
    from functools import wraps
    def decorate(fn):
        @wraps(fn)
        def wrapped(db, *args, **kwargs):
            with login_runtime(db, provider):
                return fn(db, *args, **kwargs)
        return wrapped
    return decorate

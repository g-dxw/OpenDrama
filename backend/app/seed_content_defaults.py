"""Add tenant-owned content/landing defaults without replacing operator configuration.

Dry-run by default. Use --apply to commit; never points at an implicit tenant.
"""
from __future__ import annotations
import argparse
import json
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from app.db import get_engine
from app.modules.common.models import AuditLog
from app.modules.tenancy.models import Tenant
from app.modules.tenancy.content_defaults import seed_content_defaults


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tenant-code',required=True)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--apply',action='store_true',help='Commit additive defaults and an audit event')
    mode.add_argument('--dry-run',action='store_true',help='Preview counts and roll back (the default)')
    parser.add_argument('--populate-content',action='store_true',help='Allow safe use of existing published playable content; never creates or publishes dramas')
    args=parser.parse_args()
    try:
        with Session(get_engine(),expire_on_commit=False) as db:
            tenant=db.scalar(select(Tenant).where(Tenant.code==args.tenant_code).with_for_update())
            if tenant is None: parser.error('租户不存在')
            if tenant.kind!='native': parser.error('旧站接入租户不初始化本地运营模板')
            summary=seed_content_defaults(db,tenant,populate_content=args.populate_content)
            output=dict(mode='apply' if args.apply else 'dry-run',tenant_code=tenant.code,defaults=summary)
            if args.apply:
                db.add(AuditLog(actor_type='system',actor_scope='system',tenant_id=tenant.id,action='tenant.content_defaults.seed',target_type='tenant',target_id=None,
                    payload={'tenant_code':tenant.code,'content_defaults':summary,'populate_content':args.populate_content}))
                db.commit()
            else:
                db.rollback()
        print(json.dumps(output,ensure_ascii=False,indent=2))
    except SQLAlchemyError:
        raise SystemExit('初始化失败，事务未提交。请检查数据库连接及迁移版本。') from None

if __name__=='__main__':
    main()

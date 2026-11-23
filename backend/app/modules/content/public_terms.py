"""Viewer-facing availability dates without internal distribution information."""
from datetime import timezone
from sqlalchemy.orm import object_session


def public_watch_terms(drama):
    """Return dates only; private legacy titles have no library viewing limit."""
    result = dict(watch_until=None, sales_until=None)
    if drama.library_title_id is None or drama.distribution_grant_id is None:
        return result
    db = object_session(drama)
    if db is None:
        return result
    from app.modules.library.models import DistributionGrant
    grant = db.get(DistributionGrant, drama.distribution_grant_id)
    if (grant is None or grant.tenant_id != drama.tenant_id or
            grant.library_title_id != drama.library_title_id or grant.title_version != drama.library_version):
        return result
    def date_out(value):
        if value is None:
            return None
        return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)).isoformat()
    result['watch_until'] = date_out(grant.play_until)
    result['sales_until'] = date_out(grant.sales_until)
    return result

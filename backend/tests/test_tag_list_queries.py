"""Larger default catalogs keep tag counts accurate and avoid one query per row."""
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.engine import Engine
from app.main import app
from app.modules.content.models import Drama,DramaTag,Tag
from app.modules.tenancy.models import Tenant


def test_many_tags_use_bounded_association_queries_and_owned_counts(session,make):
    owner=make.admin(role='admin')
    client=TestClient(app)
    logged=client.post('/admin/v1/auth/login',json={'tenant_code':'jjtv','username':owner.username,'password':'pass-1234'})
    assert logged.status_code==200,logged.text
    tags=[Tag(tenant_id='jjtv',name_en='Catalog genre '+str(i),name_zh='类型'+str(i),category='main',status='enabled') for i in range(80)]
    session.add_all(tags); session.flush()
    one=make.drama(title='Tag count first');two=make.drama(title='Tag count second')
    other=Tenant(id='tag-query-other',code='tag-query-other',name='Other tags',kind='native',status='active')
    session.add(other);session.flush()
    foreign=Drama(tenant_id=other.id,title='Foreign count source')
    foreign_tag=Tag(tenant_id=other.id,name_en='Private other genre',name_zh='其他租户',category='main',status='enabled')
    session.add_all([foreign,foreign_tag]);session.flush()
    session.add_all([DramaTag(drama_id=one.id,tag_id=tags[0].id),DramaTag(drama_id=two.id,tag_id=tags[0].id),DramaTag(drama_id=one.id,tag_id=tags[1].id),DramaTag(drama_id=foreign.id,tag_id=foreign_tag.id),
                     # An old malformed cross-tenant link must not inflate owned counts.
                     DramaTag(drama_id=foreign.id,tag_id=tags[0].id)])
    session.commit()
    queries=[]
    def observe(_conn,_cursor,statement,_parameters,_context,_executemany):
        if statement.lstrip().lower().startswith('select') and 'drama_tags' in statement.lower(): queries.append(statement)
    event.listen(Engine,'before_cursor_execute',observe)
    try:
        response=client.get('/admin/v1/tags',headers={'Authorization':'Bearer '+logged.json()['token']})
    finally:
        event.remove(Engine,'before_cursor_execute',observe)
    assert response.status_code==200,response.text
    values=response.json()['items']
    assert len(values)==80
    counts={row['id']:row['drama_count'] for row in values}
    assert counts[tags[0].id]==2 and counts[tags[1].id]==1
    assert all(counts[tag.id]==0 for tag in tags[2:])
    assert foreign_tag.id not in counts
    assert 1<=len(queries)<=2, 'Tag counts must use bounded queries, not one query for every catalog row'

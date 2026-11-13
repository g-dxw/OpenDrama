"""Non-destructive starter content configuration for native business tenants.

This module never commits and never runs from a GET. Default keys are stable
identities: changing a label, position, layout or status does not recreate the
original row when initialization is repeated.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.content.models import Drama, DramaHomeGroup, DramaTag, Episode, HomeGroup, Tag
from app.modules.ops.config_models import Banner, ChannelTab
from app.modules.tenancy.models import Tenant

TEMPLATE_VERSION = 1


@dataclass(frozen=True)
class TagDefault:
    key: str
    name_en: str
    name_zh: str
    category: str


# Keep keys stable if the human-readable labels change in a future template.
DEFAULT_TAGS = tuple(TagDefault(*item) for item in (
    ('romance','Romance','爱情','main'),
    ('drama','Drama','剧情','main'),
    ('rom-com','Rom-Com','浪漫喜剧','main'),
    ('revenge','Revenge','复仇','main'),
    ('fantasy','Fantasy','奇幻','main'),
    ('comedy','Comedy','喜剧','main'),
    ('suspense','Suspense','悬疑','main'),
    ('action','Action','动作','main'),
    ('family','Family','家庭','main'),
    ('urban','Urban','都市','main'),
    ('historical','Historical','古装','main'),
    ('thriller','Thriller','惊悚','main'),
    ('adventure','Adventure','冒险','main'),
    ('mystery','Mystery','推理','main'),
    ('sci-fi','Sci-Fi','科幻','main'),
    ('love-after-marriage','Love After Marriage','婚后恋爱','sub'),
    ('billionaire-ceo','Billionaire/CEO','霸总','sub'),
    ('rebirth','Rebirth','重生','sub'),
    ('hidden-identity','Hidden Identity','隐藏身份','sub'),
    ('sweet-love','Sweet Love','甜宠','sub'),
    ('twisted-love','Twisted Love','虐恋','sub'),
    ('mafia','Mafia','黑帮','sub'),
    ('strong-female','Strong Female','大女主','sub'),
    ('bitter-love','Bitter Love','苦恋','sub'),
    ('chasing-love','Chasing Love','追爱','sub'),
    ('hidden-alias','Hidden Alias','马甲','sub'),
    ('office','Office','职场','sub'),
    ('body-swap','Body Swap','身体互换','sub'),
    ('contract-marriage','Contract Marriage','契约婚姻','sub'),
    ('second-chance','Second Chance','破镜重圆','sub'),
    ('time-travel','Time Travel','穿越','sub'),
    ('secret-baby','Secret Baby','萌宝','sub'),
    ('campus','Campus','校园','sub'),
    ('enemies-to-lovers','Enemies to Lovers','欢喜冤家','sub'),
    ('forbidden-love','Forbidden Love','禁忌之恋','sub'),
    ('werewolf','Werewolf','狼人','sub'),
    ('vampire','Vampire','吸血鬼','sub'),
    ('supernatural','Supernatural','超自然','sub'),
    ('fake-relationship','Fake Relationship','假扮情侣','sub'),
    ('love-triangle','Love Triangle','三角恋','sub'),
    ('friends-to-lovers','Friends to Lovers','由友成恋','sub'),
    ('childhood-sweethearts','Childhood Sweethearts','青梅竹马','sub'),
    ('royal-romance','Royal Romance','王室恋情','sub'),
    ('period-romance','Period Romance','古装爱情','sub'),
    ('small-town','Small Town','小镇故事','sub'),
    ('marriage-crisis','Marriage Crisis','婚姻危机','sub'),
    ('redemption','Redemption','救赎','sub'),
    ('underdog','Underdog','逆袭','sub'),
    ('reunion','Reunion','久别重逢','sub'),
    ('secret-romance','Secret Romance','秘密恋情','sub'),
))


@dataclass(frozen=True)
class GroupDefault:
    key: str
    name: str
    style: str
    badge: str
    aliases: tuple[str, ...] = ()
    tag_names: tuple[str, ...] = ()


DEFAULT_GROUPS = (
    GroupDefault('popular','热门推荐','poster3','rank',('最热','Most Popular','Popular','Trending')),
    GroupDefault('new-releases','新剧上架','poster3','label',('新上架','New Releases','Latest')),
    GroupDefault('ranking','人气榜单','rank','rank',('排行榜','榜单','Ranking','Top Rated')),
    GroupDefault('romance','甜宠热播','poster3','label',('甜宠','Sweet Love','Romance'),('Romance','Rom-Com','Sweet Love','Love After Marriage')),
    GroupDefault('revenge','复仇逆袭','rank','label',('复仇','Revenge'),('Revenge','Rebirth','Underdog')),
    GroupDefault('ceo','霸总精选','grid2','label',('霸总','Billionaire/CEO'),('Billionaire/CEO','Office')),
    GroupDefault('strong-female','大女主剧场','grid2','label',('大女主','Strong Female'),('Strong Female','Underdog')),
    GroupDefault('family','家庭情感','poster3','label',('家庭','Family'),('Family','Secret Baby','Marriage Crisis')),
    GroupDefault('suspense','悬疑反转','grid2','label',('悬疑','Suspense'),('Suspense','Thriller','Mystery','Hidden Identity')),
    GroupDefault('fantasy','奇幻世界','poster3','label',('奇幻','Fantasy'),('Fantasy','Time Travel','Werewolf','Vampire','Supernatural','Body Swap')),
    GroupDefault('favorites','短剧精选','poster3','rank',('精选','Favorites')),
    GroupDefault('comedy','轻松喜剧','grid2','label',('喜剧','Comedy'),('Comedy','Rom-Com')),
)

# code, English title, Chinese title, group template key. Discover is fixed.
DEFAULT_TABS = (
    ('discover','Discover','发现',None),
    ('popular','Popular','热门','popular'),
    ('new-releases','New Releases','上新','new-releases'),
    ('ranking','Ranking','榜单','ranking'),
    ('romance','Romance','甜宠','romance'),
    ('revenge','Revenge','逆袭','revenge'),
)


def _normal(value: str) -> str:
    return ' '.join(value.split()).casefold()


@contextmanager
def _target_scope(db: Session, tenant_id: str):
    missing = object()
    original = db.info.get('content_tenant_id', missing)
    context = db.info.get('tenant_audit_context')
    if context and context.get('tenant_id') != tenant_id:
        raise ValueError('Default initialization cannot change an authenticated tenant context')
    # Preserve the original ownership check for any work the caller has queued.
    db.flush()
    db.info['content_tenant_id'] = tenant_id
    try:
        yield
        db.flush()  # All new resource FKs/ownership are checked before restoring.
    finally:
        if original is missing:
            db.info.pop('content_tenant_id', None)
        else:
            db.info['content_tenant_id'] = original


def _report(tenant: Tenant) -> dict:
    names = ('tags','groups','tabs','group_memberships','banners')
    return dict(template_version=TEMPLATE_VERSION,tenant_id=tenant.id,
                created={key:0 for key in names},reused={key:0 for key in names},
                skipped={key:0 for key in names},notes=[])


def _seed_tags(db: Session, tenant: Tenant, report: dict) -> None:
    rows = list(db.scalars(select(Tag).where(Tag.tenant_id==tenant.id).order_by(Tag.id)))
    keyed = {row.default_key:row for row in rows if row.default_key}
    named = defaultdict(list)
    for row in rows:
        named[_normal(row.name_en)].append(row)
    for template in DEFAULT_TAGS:
        key = 'content.tag.'+template.key
        if key in keyed:
            report['reused']['tags'] += 1
            continue
        candidates = named.get(_normal(template.name_en),[]) or named.get(_normal(template.name_zh),[])
        match = next((row for row in candidates if row.default_key is None),None)
        if match is not None:
            match.default_key = key  # Claim identity only; never repair human fields.
            keyed[key] = match
            report['reused']['tags'] += 1
        elif candidates:
            report['skipped']['tags'] += 1
        else:
            row = Tag(tenant_id=tenant.id,default_key=key,name_en=template.name_en,
                      name_zh=template.name_zh,category=template.category,status='enabled')
            db.add(row)
            keyed[key] = row
            named[_normal(template.name_en)].append(row)
            report['created']['tags'] += 1


def _seed_groups(db: Session, tenant: Tenant, report: dict):
    rows = list(db.scalars(select(HomeGroup).where(HomeGroup.tenant_id==tenant.id).order_by(HomeGroup.id)))
    keyed = {row.default_key:row for row in rows if row.default_key}
    named = defaultdict(list)
    for row in rows:
        named[_normal(row.name)].append(row)
    next_position = max((row.position for row in rows),default=-1)+1
    resolved, created = {}, set()
    for template in DEFAULT_GROUPS:
        key = 'content.group.'+template.key
        row = keyed.get(key)
        if row is not None:
            report['reused']['groups'] += 1
        else:
            aliases = {_normal(value) for value in (template.name,)+template.aliases}
            candidates = [row for row in rows if _normal(row.name) in aliases]
            match = next((row for row in candidates if row.default_key is None),None)
            if match is not None:
                row = match
                row.default_key = key
                report['reused']['groups'] += 1
            elif candidates:
                report['skipped']['groups'] += 1
                continue
            else:
                row = HomeGroup(tenant_id=tenant.id,default_key=key,name=template.name,
                                position=next_position,status='active',style=template.style,badge=template.badge)
                next_position += 1
                db.add(row)
                created.add(template.key)
                report['created']['groups'] += 1
            keyed[key] = row
        resolved[template.key] = row
    db.flush()  # Channel tabs link real generated IDs, never guessed values.
    return resolved,created


def _seed_tabs(db: Session, tenant: Tenant, groups: dict, report: dict) -> None:
    rows = list(db.scalars(select(ChannelTab).where(ChannelTab.tenant_id==tenant.id).order_by(ChannelTab.id)))
    keyed = {row.code:row for row in rows}
    next_position = max((row.position for row in rows),default=0)+1
    for code,title_en,title_zh,group_key in DEFAULT_TABS:
        if code in keyed:
            report['reused']['tabs'] += 1
            continue
        if group_key is not None and group_key not in groups:
            report['skipped']['tabs'] += 1
            continue
        fixed = code=='discover'
        row = ChannelTab(tenant_id=tenant.id,code=code,title_en=title_en,title_zh=title_zh,
                         position=0 if fixed else next_position,enabled=fixed or groups[group_key].status=='active',locked=fixed,
                         group_id=None if fixed else groups[group_key].id)
        db.add(row)
        keyed[code] = row
        if not fixed:
            next_position += 1
        report['created']['tabs'] += 1


def _populate_existing_content(db: Session, tenant: Tenant, groups: dict, created_groups: set, report: dict) -> None:
    playable = select(Episode.drama_id).where(Episode.status=='published',func.length(func.trim(Episode.bunny_video_id))>0)
    dramas = list(db.scalars(select(Drama).where(Drama.tenant_id==tenant.id,Drama.status=='published',Drama.id.in_(playable))
                              .order_by(Drama.play_count.desc(),Drama.id).limit(100)))
    if not dramas:
        report['notes'].append('No published playable content; groups remain empty and no banners were created')
        return
    tags = defaultdict(set)
    for drama_id,name_en in db.execute(select(DramaTag.drama_id,Tag.name_en).join(Tag,Tag.id==DramaTag.tag_id)
                                      .where(Tag.tenant_id==tenant.id,DramaTag.drama_id.in_([row.id for row in dramas]))):
        tags[drama_id].add(_normal(name_en))
    group_specs = {template.key:template for template in DEFAULT_GROUPS}
    for key in created_groups:
        group = groups[key]
        # A concurrent caller cannot populate this locked tenant, but never
        # append to associations installed by another caller in this transaction.
        if db.scalar(select(DramaHomeGroup.drama_id).where(DramaHomeGroup.group_id==group.id).limit(1)) is not None:
            continue
        spec = group_specs[key]
        if spec.tag_names:
            wanted = {_normal(name) for name in spec.tag_names}
            candidates = [row for row in dramas if tags[row.id]&wanted]
        elif key=='new-releases':
            candidates = sorted(dramas,key=lambda row:((row.published_at or row.created_at).timestamp(),row.id),reverse=True)
        else:
            candidates = dramas
        for position,drama in enumerate(candidates[:24]):
            db.add(DramaHomeGroup(group_id=group.id,drama_id=drama.id,position_in_group=position))
            report['created']['group_memberships'] += 1
    # Any existing banner (including offline) represents an operator decision.
    # Leave its placement/status/copy intact rather than filling perceived gaps.
    existing = db.scalar(select(Banner.id).where(Banner.tenant_id==tenant.id).limit(1))
    if existing is None:
        for position,drama in enumerate(dramas[:3]):
            db.add(Banner(tenant_id=tenant.id,slot='home',drama_id=drama.id,status='online',position=position,
                          kicker='',headline=drama.title[:80]))
            report['created']['banners'] += 1
    else:
        report['notes'].append('Existing banners preserved; no automatic banner was added')


def seed_content_defaults(db: Session, tenant: Tenant, *, populate_content: bool = False) -> dict:
    """Add missing native starter rows in the caller's transaction.

    Locking the tenant serializes repeated initialization without a global data
    bypass. Only populate_content=True can add published, playable *owned*
    content to newly created empty template groups or an untouched banner area.
    No drama, episode, publication, count or remote media object is generated.
    """
    if tenant.kind!='native':
        raise ValueError('Content defaults are available only for native tenants; India remains managed by its own backend')
    locked = db.scalar(select(Tenant).where(Tenant.id==tenant.id).execution_options(populate_existing=True).with_for_update())
    if locked is None or locked.kind!='native':
        raise ValueError('A persisted native tenant is required')
    report = _report(locked)
    with _target_scope(db,locked.id):
        _seed_tags(db,locked,report)
        groups,created_groups = _seed_groups(db,locked,report)
        _seed_tabs(db,locked,groups,report)
        db.flush()
        if populate_content:
            _populate_existing_content(db,locked,groups,created_groups,report)
    return report

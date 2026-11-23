"""Additive content surfaces for the desktop client and operators."""
from __future__ import annotations
import hashlib
import re
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select, or_
from sqlalchemy.orm import Session
from app.db import get_db
from app.modules.content.models import Drama, DramaTag, DramaHomeGroup, Episode, EpisodeSubtitle, Tag
from app.modules.content import service as content
from app.modules.content.public_terms import public_watch_terms
from app.modules.common import bunny
from app.modules.ops.router import require_admin, require_content_writer
from app.modules.ops.models import AdminUser

admin_router = APIRouter(prefix='/admin/v1', tags=['content-enhancements'])
public_router = APIRouter(prefix='/v1', tags=['catalog'])

@public_router.get('/catalog')
def catalog(q: str = Query('',max_length=200),language: str = Query('',max_length=16),tag: str = Query('',max_length=64),group: int | None=Query(None,ge=1),free_only: bool=False,sort: str = Query('newest',pattern='^(newest|popular|title)$'),limit: int = Query(24,ge=1,le=100),offset: int = Query(0,ge=0),db: Session=Depends(get_db)):
    stmt = select(Drama).where(Drama.status=='published', select(Episode.id).where(Episode.drama_id==Drama.id,Episode.status=='published',Episode.bunny_video_id!='').exists())
    if q.strip():
        pattern='%'+q.strip().replace('%',r'\%').replace('_',r'\_')+'%'
        stmt=stmt.where(or_(Drama.title.ilike(pattern),Drama.synopsis.ilike(pattern)))
    if group is not None: stmt=stmt.where(select(DramaHomeGroup.drama_id).where(DramaHomeGroup.drama_id==Drama.id,DramaHomeGroup.group_id==group).exists())
    if free_only: stmt=stmt.where(select(Episode.id).where(Episode.drama_id==Drama.id,Episode.status=='published',Episode.bunny_video_id!='',or_(Episode.episode_number<=func.coalesce(Drama.non_member_free_episodes,Drama.free_episode_count),Episode.coin_price==0)).exists())
    if language: stmt=stmt.where(Drama.language==language)
    if tag:
        stmt=stmt.where(select(DramaTag.drama_id).join(Tag,Tag.id==DramaTag.tag_id).where(DramaTag.drama_id==Drama.id,Tag.status=='enabled', (Tag.id==int(tag)) if tag.isdigit() else Tag.name_en==tag).exists())
    total=db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    order={'newest':Drama.published_at.desc().nullslast(),'popular':Drama.play_count.desc(),'title':Drama.title.asc()}[sort]
    rows=list(db.scalars(stmt.order_by(order,Drama.id.desc()).limit(limit).offset(offset)))
    ids=[d.id for d in rows]
    counts=dict(db.execute(select(Episode.drama_id,func.count()).where(Episode.drama_id.in_(ids),Episode.status=='published').group_by(Episode.drama_id)).all()) if ids else {}
    tags=content.tags_by_drama(db,ids)
    return dict(items=[dict(id=d.id,title=d.title,synopsis=d.synopsis,cover_url=d.cover_url,language=d.language,tags=[t.name_en for t in tags.get(d.id,[])],episode_count=counts.get(d.id,0),free_episode_count=d.non_member_free_episodes if d.non_member_free_episodes is not None else d.free_episode_count,play_count=d.play_count,access_mode=d.access_mode,member_pays_coins=d.member_pays_coins,coin_price_per_episode=d.coin_price_per_episode,**public_watch_terms(d)) for d in rows],total=total,limit=limit,offset=offset)

def episode_for_update(db, episode_id):
    row=db.scalar(select(Episode).where(Episode.id==episode_id).with_for_update())
    if row is None: raise HTTPException(404,detail='episode not found')
    return row

def track_out(track):
    return dict(language=track.language,label=track.label,url=track.url,is_default=track.is_default)

@admin_router.get('/episodes/{episode_id}/subtitles')
def subtitles(episode_id:int,db:Session=Depends(get_db),user:AdminUser=Depends(require_admin)):
    episode=db.get(Episode,episode_id)
    if episode is None: raise HTTPException(404,detail='episode not found')
    return dict(legacy_default_url=episode.subtitle_url,items=[track_out(t) for t in db.scalars(select(EpisodeSubtitle).where(EpisodeSubtitle.episode_id==episode_id).order_by(EpisodeSubtitle.language))])

@admin_router.post('/episodes/{episode_id}/subtitles')
async def upload_subtitle(episode_id:int,language:str=Form(...,pattern=r'^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$'),label:str=Form('',max_length=80),is_default:bool=Form(False),file:UploadFile=File(...),db:Session=Depends(get_db),user:AdminUser=Depends(require_content_writer)):
    episode=episode_for_update(db,episode_id)
    data=await file.read(2*1024*1024+1)
    if not data or len(data)>2*1024*1024: raise HTTPException(422,detail='subtitle must be between 1 byte and 2MB')
    try: text=data.decode('utf-8-sig').replace('\r\n','\n')
    except UnicodeDecodeError: raise HTTPException(422,detail='subtitle must use UTF-8') from None
    filename=(file.filename or '').lower()
    if filename.endswith('.srt'):
        text='WEBVTT\n\n'+re.sub(r'(\d{2}:\d{2}:\d{2}),(\d{3})',r'\1.\2',text)
    elif not filename.endswith('.vtt') or not text.lstrip().startswith('WEBVTT'):
        raise HTTPException(422,detail='subtitle must be valid .vtt or .srt')
    if not bunny.live(): raise HTTPException(503,detail='subtitle storage is not configured')
    language=language.lower()
    payload=text.encode()
    path=f'subs/{episode.id}/{language}-{hashlib.sha256(payload).hexdigest()[:16]}.vtt'
    try: bunny.storage_upload(path,payload,'text/vtt; charset=utf-8')
    except bunny.BunnyError: raise HTTPException(502,detail='subtitle storage upload failed') from None
    tracks=list(db.scalars(select(EpisodeSubtitle).where(EpisodeSubtitle.episode_id==episode_id)))
    track=next((t for t in tracks if t.language==language),None)
    make_default=is_default or not tracks or bool(track and track.is_default)
    if make_default:
        for item in tracks: item.is_default=False
        db.flush()
    if track is None:
        track=EpisodeSubtitle(episode_id=episode_id,language=language,label=label or language,url=path,is_default=make_default)
        db.add(track)
    else:
        track.label=label or language; track.url=path; track.is_default=make_default
    if make_default: episode.subtitle_url=path
    db.commit()
    return track_out(track)

class DefaultSubtitle(BaseModel):
    language: str = Field(min_length=2,max_length=35)

@admin_router.patch('/episodes/{episode_id}/subtitles/default')
def set_default(episode_id:int,body:DefaultSubtitle,db:Session=Depends(get_db),user:AdminUser=Depends(require_content_writer)):
    episode=episode_for_update(db,episode_id)
    tracks=list(db.scalars(select(EpisodeSubtitle).where(EpisodeSubtitle.episode_id==episode_id)))
    target=next((t for t in tracks if t.language==body.language.lower()),None)
    if target is None: raise HTTPException(404,detail='subtitle language not found')
    for track in tracks: track.is_default=False
    db.flush(); target.is_default=True; episode.subtitle_url=target.url
    db.commit(); return track_out(target)

@admin_router.delete('/episodes/{episode_id}/subtitles/{language}')
def delete_subtitle(episode_id:int,language:str,db:Session=Depends(get_db),user:AdminUser=Depends(require_content_writer)):
    episode=episode_for_update(db,episode_id)
    target=db.get(EpisodeSubtitle,(episode_id,language.lower()))
    if target is None: raise HTTPException(404,detail='subtitle language not found')
    was_default=target.is_default
    db.delete(target); db.flush()
    if was_default:
        next_track=db.scalar(select(EpisodeSubtitle).where(EpisodeSubtitle.episode_id==episode_id).order_by(EpisodeSubtitle.language).limit(1))
        episode.subtitle_url=next_track.url if next_track else ''
        if next_track: next_track.is_default=True
    db.commit(); return dict(deleted=True)

class EpisodePrice(BaseModel):
    coin_price: int | None = Field(default=None,ge=0,le=1000000)

@admin_router.patch('/episodes/{episode_id}/pricing')
def episode_price(episode_id:int,body:EpisodePrice,db:Session=Depends(get_db),user:AdminUser=Depends(require_content_writer)):
    episode=episode_for_update(db,episode_id)
    episode.coin_price=body.coin_price
    db.commit(); return dict(id=episode.id,coin_price=episode.coin_price)

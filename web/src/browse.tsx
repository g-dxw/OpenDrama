import { siteName, siteFeature } from "../../shared/site";
import { useLocale } from "./locale";
import { lazy, Suspense, useEffect, useRef, useState } from "react";
import {
  get,
  post,
  errorMessage,
  type Catalog,
  type CatalogDrama,
  type HomePayload,
  type FeedPage,
  type Banner,
  type ConfigPayload,
  type FeedItem,
  type PlayEpisode,
} from "./consumer-api";
import { Cards, DramaCard, Empty, Failure, Icon, Loading, Modal } from "./components";
import { useAuth } from "./auth";
const FeedVideo = lazy(() => import("./player").then((module) => ({default:module.Video})));
const FeedComments = lazy(() => import("./player").then((module) => ({default:module.Comments})));
export function Home() {
  const { locale, t, number } = useLocale();
  const homeRequest = useRef(0);
  const [home, setHome] = useState<HomePayload | null>(null), [config, setConfig] = useState<ConfigPayload | null>(null), [error, setError] = useState("");
  const [bannerIndex, setBannerIndex] = useState(0), [channelIndex, setChannelIndex] = useState(0);
  const load = () => {
    setError("");
    const request = ++homeRequest.current;
    Promise.all([get<HomePayload>("/v1/home/groups"), get<ConfigPayload>("/v1/config?locale=" + (locale === "zh" ? "zh" : "en")).catch(() => null)])
      .then(([nextHome, nextConfig]) => { if (request === homeRequest.current) { setHome(nextHome); setConfig(nextConfig); } })
      .catch((e) => { if (request === homeRequest.current) setError(errorMessage(e)); });
  };
  useEffect(() => { load(); return () => { homeRequest.current++; }; }, [locale]);
  if (error) return <div className="page-content"><Failure message={error} retry={load}/></div>;
  if (!home) return <Loading/>;
  const allDramas = home.groups.flatMap((group) => group.dramas);
  const banners: Banner[] = home.banners.length ? home.banners : allDramas.filter((drama, index, list) => drama.cover_url && list.findIndex((item) => item.id === drama.id) === index).slice(0, 3).map((drama) => ({ id: -drama.id, drama_id: drama.id, cover_url: drama.cover_url, title: drama.title, headline: drama.title, kicker: "", slot: "hero" }));
  const currentIndex = banners.length ? bannerIndex % banners.length : 0;
  const banner = banners[currentIndex];
  const featured = allDramas.find((drama) => drama.id === banner?.drama_id);
  const adjacent = allDramas.filter((drama) => drama.cover_url && drama.id !== banner?.drama_id);
  const tabs = config?.tabs?.length ? [...config.tabs].sort((a,b) => a.position - b.position) : [{code:"discover",title_en:"Discover",title_zh:"发现",position:0,locked:true,group_id:null}];
  const tab = tabs[channelIndex] || tabs[0];
  const visibleGroups = channelIndex === 0 || tab.locked ? home.groups : home.groups.filter((group) => group.id === tab.group_id);
  const heroLink = banner?.drama_id ? "#watch/" + banner.drama_id : featured ? "#watch/" + featured.id : "#browse";
  return <div className="consumer-home">
    {banner && <section className="feature-hero" aria-label={t("Featured dramas")}>
      <div className="feature-copy">
        <div className="hero-kicker"><span>{String(currentIndex + 1).padStart(2,"0")}</span><i/><span>{banner.kicker || t("Featured")}</span></div>
        <h1>{banner.headline || banner.title || featured?.title || t("Featured drama")}</h1>
        <p>{t("Short-form drama, made to binge — every episode, one tap away.")}</p>
        <div className="hero-metadata"><span>{t("SHORT DRAMA")}</span>{featured?.play_count ? <span>{t("{count} views",{count:number(featured.play_count,{notation:"compact"})})}</span> : null}<span>{siteName()}</span></div>
        <div className="button-row"><a className="button primary" href={heroLink}><Icon name="play" size={16}/>{t("Watch now")}</a><a className="hero-details" href={heroLink}>{t("Details")}<span>→</span></a></div>
        <div className="hero-pager" aria-label={t("Featured drama slides")}>{banners.map((item,index) => <button key={item.id} className={index===currentIndex?"active":""} aria-label={t("Show featured drama {number}",{number:index+1})} aria-pressed={index===currentIndex} onClick={() => setBannerIndex(index)}>{String(index+1).padStart(2,"0")}</button>)}</div>
      </div>
      <div className="hero-artwork" aria-hidden="true">
        {(adjacent[0]?.cover_url || banner.cover_url) && <img className="hero-side-poster hero-side-left" src={adjacent[0]?.cover_url || banner.cover_url || ""} alt=""/>}
        {(adjacent[1]?.cover_url || banner.cover_url) && <img className="hero-side-poster hero-side-right" src={adjacent[1]?.cover_url || banner.cover_url || ""} alt=""/>}
        {banner.cover_url ? <img className="hero-poster" src={banner.cover_url} alt=""/> : <div className="hero-poster hero-poster-empty">{siteName()}</div>}
        <span className="hero-poster-caption">{siteName()} · {t("Rank {number}",{number:String(currentIndex+1).padStart(2,"0")})}</span>
      </div>
    </section>}
    {banner && <div className="home-spotlight"><span><i/>{t("IN THE SPOTLIGHT")}</span><span>{t("CURATED FOR YOU")}</span><span>{String(currentIndex+1).padStart(2,"0")} / {String(banners.length).padStart(2,"0")}</span></div>}
    <div className="page-content home-content">
      <div className="home-channels" role="tablist" aria-label={t("Drama channels")}>{tabs.map((item,index) => <button key={item.code} role="tab" aria-selected={index===channelIndex} className={index===channelIndex?"active":""} onClick={() => setChannelIndex(index)}>{item.code === "discover" && !config?.tabs?.length ? t("Discover") : locale === "zh" ? item.title_zh || item.title_en : item.title_en || item.title_zh}</button>)}</div>
      {visibleGroups.filter((group) => group.dramas.length).map((group,index) => <section className="drama-section" key={group.id}>
        <div className="section-heading"><div className="home-section-title"><span className="section-index">{String(index+1).padStart(2,"0")} /</span><h2>{group.name}</h2><span className="section-count">{t("{count} TITLES",{count:number(group.dramas.length)})}</span></div><a href={"#browse?group="+group.id}>{t("More")}<span>→</span></a></div>
        {index===0&&group.style!=="rank"&&group.dramas.length>=2 ? <div className="home-editorial"><DramaCard drama={group.dramas[0]}/><div className="editorial-list"><div className="editorial-list-heading"><span>{t("THE LIST")} / 02—{String(Math.min(group.dramas.length,6)).padStart(2,"0")}</span><span>{t("JJTV SELECTION")}</span></div>{group.dramas.slice(1,6).map((drama,index)=><a className="editorial-list-item" key={drama.id} href={"#watch/"+drama.id}><span className="editorial-item-index">{String(index+2).padStart(2,"0")}</span><span className="editorial-item-cover">{drama.cover_url&&<img src={drama.cover_url} alt="" loading="lazy"/>}</span><span className="editorial-item-copy"><strong>{drama.title}</strong><small>{drama.play_count!=null?t("{count} views",{count:number(drama.play_count,{notation:"compact"})}):t("Short drama")}</small></span><Icon name="arrow" size={17}/></a>)}</div></div> : <div className={group.style==="rank"?"drama-row ranking-row":"drama-row"}>{group.dramas.map((drama,index) => group.style==="rank" ? <div className="ranked-card" key={drama.id}><span className="rank-number" aria-label={t("Rank {number}",{number:index+1})}>{index+1}</span><DramaCard drama={drama}/></div> : <DramaCard key={drama.id} drama={drama}/>)}</div>}
      </section>)}
      {!visibleGroups.some((group) => group.dramas.length) && <Empty message={t("New stories are on their way. Check back soon.")}/>}
    </div>
  </div>;
}
export function Browse({
  query,
  preview = false,
  searchOnly = false,
}: {
  query: string;
  preview?: boolean;
  searchOnly?: boolean;
}) {
  const { t, number } = useLocale();
  const params = new URLSearchParams(query);
  const [q, setQ] = useState(params.get("q") || ""),
    [tag, setTag] = useState(params.get("tag") || ""),
    [language, setLanguage] = useState(params.get("language") || ""),
    [sort, setSort] = useState(params.get("sort") || "newest"),
    [data, setData] = useState<Catalog | null>(null),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(true),
    [revision, setRevision] = useState(0);
  const offset = Math.max(0, Number(params.get("offset")) || 0);
  useEffect(() => {
    const next = new URLSearchParams(query);
    setQ(next.get("q") || "");
    setTag(next.get("tag") || "");
    setLanguage(next.get("language") || "");
    setSort(next.get("sort") || "newest");
  }, [query]);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    const next = new URLSearchParams(query);
    if (searchOnly && !next.get("q")?.trim()) { setData(null); setLoading(false); return; }
    next.set("limit", "24");
    next.set("offset", String(Math.max(0, Number(next.get("offset")) || 0)));
    if (preview) next.set("free_only", "true");
    get<Catalog>("/v1/catalog?" + next)
      .then((result) => {
        if (active) setData(result);
      })
      .catch((e) => {
        if (active) setError(errorMessage(e));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [query, preview, revision, searchOnly]);
  function navigate(next: URLSearchParams) {
    window.location.hash =
      (preview ? "preview" : searchOnly ? "search" : "browse") +
      (next.size ? "?" + next.toString() : "");
    window.scrollTo(0, 0);
  }
  function apply() {
    const next = new URLSearchParams();
    if (params.get("group")) next.set("group", params.get("group")!);
    if (q.trim()) next.set("q", q.trim());
    if (tag.trim()) next.set("tag", tag.trim());
    if (language.trim()) next.set("language", language.trim());
    if (sort !== "newest") next.set("sort", sort);
    navigate(next);
  }
  function paginate(nextOffset: number) {
    const next = new URLSearchParams(query);
    if (nextOffset) next.set("offset", String(nextOffset));
    else next.delete("offset");
    navigate(next);
  }
  return (
    <div className={"page-content catalog-page" + (searchOnly ? " search-page" : "")}>
      {searchOnly ? <><div className="search-page-heading"><a className="icon-button" href="#home" aria-label={t("Back to Home")}>←</a><h1>{t("Search dramas")}</h1></div><form className="search-page-form" role="search" onSubmit={(event) => {event.preventDefault();apply();}}><label className="sr-only" htmlFor="drama-search-input">{t("Search dramas")}</label><input id="drama-search-input" aria-label={t("Search dramas")} autoFocus type="search" value={q} onChange={(event) => setQ(event.target.value)} placeholder={t("Search dramas")}/></form></> : <><div className="page-title">
        <span className="eyebrow">{t("The JJTV collection")}</span>
        <h1>
          {preview
            ? t("Free Preview")
            : params.get("q")
              ? t("Search results")
              : t("Explore dramas")}
        </h1>
        <p className="muted">
          {preview
            ? t("Start with free episodes. Sign in whenever you are ready for more.")
            : t("Find a story worth staying up for.")}
        </p>
      </div>
      <form
        className="catalog-filters"
        onSubmit={(event) => {
          event.preventDefault();
          apply();
        }}
      >
        <label className="search-filter">
          {t("Search dramas")}<input
            aria-label={t("Search dramas")}
            type="search"
            value={q}
            onChange={(event) => setQ(event.target.value)}
            placeholder={t("Search title or story…")}
          />
        </label>
        <label>
          {t("Tag")}<input
            value={tag}
            onChange={(event) => setTag(event.target.value)}
            placeholder={t("Any genre")}
          />
        </label>
        <label>
          {t("Language")}<input
            value={language}
            onChange={(event) => setLanguage(event.target.value)}
            placeholder={t("All languages")}
          />
        </label>
        <label>
          {t("Sort")}<select
            value={sort}
            onChange={(event) => setSort(event.target.value)}
          >
            <option value="newest">{t("Recently added")}</option>
            <option value="popular">{t("Most watched")}</option>
            <option value="title">{t("Title A–Z")}</option>
          </select>
        </label>
        <div className="catalog-filter-actions">
          <button className="button primary" type="submit">
            {t("Apply filters")}</button>
          <button
            className="text-button"
            type="button"
            onClick={() => {
              setQ("");
              setTag("");
              setLanguage("");
              setSort("newest");
              navigate(new URLSearchParams());
            }}
          >
            {t("Clear filters")}</button>
          {params.get("group") && (
            <span className="muted">{t("Browsing a selected collection")}</span>
          )}
        </div>
      </form></>}
      {searchOnly && !params.get("q")?.trim() ? null : error ? (
        <Failure
          message={error}
          retry={() => setRevision((value) => value + 1)}
        />
      ) : loading ? (
        <Loading />
      ) : (
        data && (
          <>
            <p className="result-count" role="status">
              {t(data.total === 1 ? "{count} drama" : "{count} dramas",{count:number(data.total)})}
            </p>
            <Cards items={data.items} />
            <div className="pagination">
              <button
                className="button secondary"
                disabled={!offset}
                onClick={() => paginate(Math.max(0, offset - 24))}
              >
                {t("Previous")}</button>
              <span>
                {t("{from}–{to} of {total}",{from:number(data.total ? offset + 1 : 0),to:number(Math.min(offset+24,data.total)),total:number(data.total)})}
              </span>
              <button
                className="button secondary"
                disabled={offset + 24 >= data.total}
                onClick={() => paginate(offset + 24)}
              >
                {t("Next")}</button>
            </div>
          </>
        )
      )}
    </div>
  );
}
export function ForYou() {
  const { t, number } = useLocale();
  const { requireAuth, user } = useAuth();
  const [items,setItems] = useState<FeedItem[]>([]), [cursor,setCursor] = useState<string|null>(null), [hasMore,setHasMore] = useState(true), [loading,setLoading] = useState(false), [error,setError] = useState("");
  const [active,setActive] = useState(0), [muted,setMuted] = useState(true), [settingsOpen,setSettingsOpen] = useState(false), [busy,setBusy] = useState<number|null>(null), [notice,setNotice] = useState<{key:string;vars?:Record<string,string|number>}|null>(null), [comments,setComments] = useState<FeedItem|null>(null);
  const scroll=useRef<HTMLDivElement>(null), sequence=useRef(0), pending=useRef(false), owner=useRef(user?.id);
  const completedReactions = useRef<{
    owner: number | undefined;
    revision: number;
    byDrama: Map<number, {liked: boolean; revision: number}>;
  }>({owner: user?.id, revision: 0, byDrama: new Map()});
  owner.current=user?.id;
  if (completedReactions.current.owner !== user?.id) {
    completedReactions.current = {owner: user?.id, revision: 0, byDrama: new Map()};
  }
  async function load(reset=false) {
    if(pending.current)return;
    const actor = owner.current;
    const readRevision = completedReactions.current.revision;
    pending.current=true;const current=++sequence.current;setLoading(true);setError("");
    try {
      const result=await get<FeedPage>("/v1/feed?limit=6"+(!reset&&cursor?"&cursor="+encodeURIComponent(cursor):""));
      if(current!==sequence.current || owner.current!==actor)return;
      setItems((previous)=>{
        if(current!==sequence.current || owner.current!==actor)return previous;
        // A completed reaction can overtake this read, including the first
        // authenticated feed loaded while a guest action is being replayed.
        // Reads begun after the reaction completed can trust the server again.
        const reconcile = (row: FeedItem): FeedItem => {
          const latest = completedReactions.current.byDrama.get(row.drama.id);
          if(!latest || latest.revision<=readRevision)return row;
          const delta = row.drama.liked===latest.liked ? 0 : latest.liked ? 1 : -1;
          return {...row,drama:{...row.drama,liked:latest.liked,like_count:Math.max(0,row.drama.like_count+delta)}};
        };
        const next=reset?[]:previous.map(reconcile);
        for(const raw of result.items){
          const item=reconcile(raw);
          if(!next.some((old)=>(old.item_id||old.drama.id+":"+old.episode.id)===(item.item_id||item.drama.id+":"+item.episode.id)))next.push(item);
        }
        return next;
      });
      setCursor(result.next_cursor);setHasMore(result.has_more);
    } catch(e) {if(current===sequence.current&&owner.current===actor)setError(errorMessage(e));}
    finally {if(current===sequence.current&&owner.current===actor){pending.current=false;setLoading(false);}}
  }
  useEffect(()=>{
    setItems([]);setCursor(null);setHasMore(true);setActive(0);setNotice(null);setBusy(null);
    scroll.current?.scrollTo(0,0);void load(true);
    return()=>{sequence.current++;pending.current=false;};
  },[user?.id]);
  useEffect(()=>{const root=scroll.current;if(!root)return;const observer=new IntersectionObserver((entries)=>{for(const entry of entries)if(entry.isIntersecting&&entry.intersectionRatio>.6)setActive(Number((entry.target as HTMLElement).dataset.index));},{root,threshold:[.6,.8]});root.querySelectorAll(".feed-item").forEach((item)=>observer.observe(item));return()=>observer.disconnect();},[items.length]);
  useEffect(()=>{setSettingsOpen(false);},[active]);
  useEffect(()=>{if(items.length&&active>=items.length-2&&hasMore&&!loading)void load();},[active,items.length,hasMore]);
  const move=(index:number)=>{const next=Math.max(0,Math.min(index,items.length-1));scroll.current?.scrollTo({top:next*scroll.current.clientHeight,behavior:window.matchMedia("(prefers-reduced-motion: reduce)").matches?"auto":"smooth"});setActive(next);};
  async function like(item:FeedItem) {
    if(busy!==null)return;
    const actor=owner.current, desired=!item.drama.liked;
    setBusy(item.drama.id);setNotice(null);
    try {
      const result=await post<{active?:boolean}>("/v1/reactions",{target_type:"drama",target_id:item.drama.id,kind:"like",remove:!desired});
      if(owner.current!==actor)return;
      const liked=typeof result.active==="boolean"?result.active:desired;
      const revision=++completedReactions.current.revision;
      completedReactions.current.byDrama.set(item.drama.id,{liked,revision});
      setItems((previous)=>owner.current!==actor?previous:previous.map((row)=>row.drama.id===item.drama.id?{
        ...row,drama:{...row.drama,liked,like_count:Math.max(0,row.drama.like_count+(row.drama.liked===liked?0:liked?1:-1))},
      }:row));
    } catch(e) {if(owner.current===actor)setNotice({key:errorMessage(e)});}
    finally {if(owner.current===actor)setBusy(null);}
  }
  async function share(item:FeedItem){const link=new URL(window.location.href);link.search="";link.hash="watch/"+item.drama.id+"?episode="+item.episode.id;try{if(navigator.share)await navigator.share({title:item.drama.title,url:link.href});else{await navigator.clipboard.writeText(link.href);setNotice({key:"Link copied."});}}catch(e){if(!(e instanceof Error&&e.name==="AbortError"))setNotice({key:"Copy this link: {url}",vars:{url:link.href}});}}
  return <div className="for-you-page">
    <h1 className="sr-only">{t("For You")}</h1>
    <div className="feed-scroll" ref={scroll} aria-label={t("Recommended episodes")}>
      {items.map((item,index)=>{
        // The feed is filtered for its viewer; free describes guest access, not eligibility.
        // Signed-in entries must still obtain a fresh, server-validated playback URL.
        const canAuthorize = item.episode.free || Boolean(user);
        const episode:PlayEpisode={...item.episode,locked:!canAuthorize,progress_seconds:item.resume_seconds||0,unlock:null};const link="#watch/"+item.drama.id+"?episode="+item.episode.id;return <article className="feed-item" data-index={index} key={item.item_id||item.drama.id+":"+item.episode.id} aria-label={t("{title}, episode {number}",{title:item.drama.title,number:item.episode.episode_number})}>
        <div className="video-frame feed-video-frame" data-settings-open={index===active&&settingsOpen?"true":"false"}>
          {item.drama.cover_url&&<img className="feed-poster" src={item.drama.cover_url} alt=""/>}
          {index===active&&canAuthorize?<Suspense fallback={<Loading/>}><FeedVideo dramaId={item.drama.id} episode={episode} muted={muted} onMutedChange={setMuted} settingsOpen={settingsOpen} onSettingsOpenChange={setSettingsOpen} poster={item.drama.cover_url||undefined} onFinished={()=>move(index+1)} onDenied={()=>setNotice({key:"Open Episodes to check this episode's viewing options."})}/></Suspense>:!canAuthorize?<div className="feed-locked"><Icon name="lock" size={28}/><p>{t("Continue the story")}</p><a className="button secondary" href={link}>{t("View episodes")}</a></div>:null}
          <div className="feed-shade"/>
          <button className="icon-button feed-settings-toggle" aria-label={t("Playback settings")} aria-expanded={index===active&&settingsOpen} disabled={!canAuthorize} onClick={()=>setSettingsOpen(!settingsOpen)}><Icon name="more" size={18}/></button>
          <div className="feed-actions"><a className="feed-avatar" href={link} aria-label={t("Open {title}",{title:item.drama.title})}>{item.drama.cover_url?<img src={item.drama.cover_url} alt=""/>:"J"}<span>+</span></a><button aria-label={t(item.drama.liked?"Unlike drama":"Like drama")} aria-pressed={item.drama.liked} disabled={busy!==null} onClick={()=>requireAuth(()=>{void like(item);})}><Icon name="heart" size={29}/><small>{number(item.drama.like_count||0,{notation:"compact"})}</small></button>{siteFeature("comments")&&<button aria-label={t("Comments")} onClick={()=>setComments(item)}><Icon name="message" size={28}/><small>{number(item.episode.comment_count||0,{notation:"compact"})}</small></button>}<button aria-label={t("Share drama")} onClick={()=>void share(item)}><Icon name="share" size={27}/><small>{t("Share")}</small></button></div>
          <div className="feed-caption"><h2><a href={link}>{item.drama.title}</a></h2><div className="feed-tags"><span>{t("EP {number}",{number:item.episode.episode_number})}</span>{item.drama.tags?.slice(0,3).map((tag)=><span key={tag}>{tag}</span>)}</div><p>{item.drama.synopsis}</p><a className="feed-episodes" href={link}><Icon name="menu" size={17}/>{t("Episodes")}<Icon name="arrow" size={15}/></a></div>
        </div>
      </article>;})}
      {error&&<div className="feed-feedback"><Failure message={error} retry={()=>void load(items.length===0)}/></div>}
      {loading&&<div className="feed-feedback"><Loading/></div>}
      {!loading&&!items.length&&!error&&<Empty message={t("New episodes are on their way.")}/>}
    </div>
    {items.length>1&&<div className="feed-navigation"><button className="icon-button" aria-label={t("Previous episode")} disabled={!active} onClick={()=>move(active-1)}>↑</button><button className="icon-button" aria-label={t("Next episode")} disabled={active===items.length-1} onClick={()=>move(active+1)}>↓</button></div>}
    {notice&&<div className="feed-notice" role="status"><span>{t(notice.key,notice.vars)}</span><button aria-label={t("Dismiss message")} onClick={()=>setNotice(null)}><Icon name="close" size={15}/></button></div>}
    {siteFeature("comments")&&comments&&<Modal title={t("Comments")} className="feed-comments-modal" onClose={()=>setComments(null)}><Suspense fallback={<Loading/>}><FeedComments episodeId={comments.episode.id}/></Suspense></Modal>}
  </div>;
}

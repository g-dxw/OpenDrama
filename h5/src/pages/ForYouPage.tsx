import { siteFeature } from "../../../shared/site";
import { useCallback, useEffect, useRef, useState } from "react";
import { authToken, getJson, postJson } from "../api";
import { useAuth } from "../auth/AuthProvider";
import type { Locale } from "../copy";
import { t } from "../copy";
import { formatCount } from "../format";
import { IMG, thumb } from "../img";
import { CommentSheet } from "../ui/CommentSheet";
import { toast } from "../ui/toast";
import { IconComment, IconEpisodes, IconHeart, IconShare } from "../icons";
import { EpisodeView } from "../player/EpisodeView";
import { classifyEpisodeSwipe } from "../player/gestures";
import type { FeedItem, FeedPage } from "../types";

const PAGE_SIZE = 5;
const PRELOAD_REMAINING = 2;

interface Props {
  locale: Locale;
  onOpenDrama: (dramaId: number) => void;
}

/** One vertical page is one drama. Swiping up means the next show, not the next episode. */
export function ForYouPage(props: Props) {
  // Subscribe to auth transitions, then remount every private feed snapshot.
  useAuth();
  const ownerToken = authToken();
  return <OwnedFeed key={ownerToken ?? "guest"} {...props} ownerToken={ownerToken} />;
}

function OwnedFeed({ locale, onOpenDrama, ownerToken }: Props & { ownerToken: string | null }) {
  const [items, setItems] = useState<FeedItem[]>([]);
  const [hasMore, setHasMore] = useState(true);
  const [index, setIndex] = useState(0);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(true);
  const loadingRef = useRef(false);
  const mounted = useRef(false);
  const generation = useRef(0);
  const touchStart = useRef<{ x: number; y: number } | null>(null);
  const lastWheel = useRef(0);

  const cursorRef = useRef<string | null>(null);
  const hasMoreRef = useRef(true);

  const loadMore = useCallback(async (reset = false) => {
    if (!mounted.current || authToken() !== ownerToken || loadingRef.current) return;
    if (!reset && !hasMoreRef.current) return;
    const request = ++generation.current;
    const ownsRequest = () => mounted.current && request === generation.current && authToken() === ownerToken;
    loadingRef.current = true;
    setLoading(true);
    setError(false);
    try {
      const query = new URLSearchParams({ limit: String(PAGE_SIZE) });
      if (!reset && cursorRef.current) query.set("cursor", cursorRef.current);
      const page = await getJson<FeedPage>(`/v1/feed?${query}`);
      if (!ownsRequest()) return;
      const nextCursor = page.has_more ? page.next_cursor : null;
      cursorRef.current = nextCursor;
      hasMoreRef.current = Boolean(nextCursor);
      setHasMore(Boolean(nextCursor));
      setItems((prev) => {
        const base = reset ? [] : prev;
        const known = new Set(base.map((item) => item.item_id));
        return [
          ...base,
          ...page.items.filter((item) => !known.has(item.item_id)),
        ];
      });
      if (reset) setIndex(0);
    } catch {
      if (ownsRequest()) setError(true);
    } finally {
      if (ownsRequest()) {
        loadingRef.current = false;
        setLoading(false);
      }
    }
  }, [ownerToken]);

  useEffect(() => {
    mounted.current = true;
    void loadMore(true);
    return () => {
      mounted.current = false;
      loadingRef.current = false;
      generation.current++;
      touchStart.current = null;
    };
  }, [loadMore]);

  useEffect(() => {
    if (hasMore && items.length - index - 1 <= PRELOAD_REMAINING)
      loadMore(false);
  }, [index, items.length, hasMore, loadMore]);

  const go = (next: number) => {
    if (!mounted.current || authToken() !== ownerToken || next < 0 || next >= items.length) return;
    setIndex(next);
  };

  if (error && items.length === 0) {
    return (
      <div className="center">
        <button className="retry" onClick={() => loadMore(true)}>
          {t(locale, "retry")}
        </button>
      </div>
    );
  }
  if (items.length === 0)
    return <div className="center">{loading ? "…" : t(locale, "empty")}</div>;

  return (
    <div
      className="feed"
      onTouchStart={(event) => {
        touchStart.current = event.touches.length === 1 && !isFeedControl(event.target)
          ? { x: event.touches[0].clientX, y: event.touches[0].clientY } : null;
      }}
      onTouchMove={(event) => { if (event.touches.length !== 1) touchStart.current = null; }}
      onTouchCancel={() => { touchStart.current = null; }}
      onTouchEnd={(event) => {
        const start = touchStart.current;
        touchStart.current = null;
        if (!start || event.touches.length !== 0 || event.changedTouches.length !== 1) return;
        const end = event.changedTouches[0];
        const delta = classifyEpisodeSwipe({ dx: end.clientX - start.x, dy: end.clientY - start.y,
          startX: start.x, viewportWidth: window.innerWidth });
        const next = index + delta;
        if (delta && next >= 0 && next < items.length) {
          if (event.cancelable) event.preventDefault();
          go(next);
        }
      }}
      onWheel={(event) => {
        if (isFeedControl(event.target) || Math.abs(event.deltaY) < 30 ||
          Math.abs(event.deltaY) < Math.abs(event.deltaX) * 1.25 || Date.now() - lastWheel.current < 450) return;
        lastWheel.current = Date.now();
        go(event.deltaY > 0 ? index + 1 : index - 1);
      }}
    >
      <div
        className="snap"
        style={{ transform: `translateY(-${index * 100}%)` }}
      >
        {items.map((item, position) => (
          <FeedSlot
            key={item.item_id}
            item={item}
            ownerToken={ownerToken}
            active={position === index}
            locale={locale}
            onOpenDrama={onOpenDrama}
          />
        ))}
      </div>
    </div>
  );
}

function FeedSlot({
  item,
  ownerToken,
  active,
  locale,
  onOpenDrama,
}: {
  item: FeedItem;
  ownerToken: string | null;
  active: boolean;
  locale: Locale;
  onOpenDrama: (id: number) => void;
}) {
  const mounted = useRef(false);
  const likeRevision = useRef(0);
  const ownsSlot = () => mounted.current && authToken() === ownerToken;
  const [finished, setFinished] = useState(false);
  const [liked, setLiked] = useState(item.drama.liked);
  const [likeCount, setLikeCount] = useState(item.drama.like_count);
  const [commentOpen, setCommentOpen] = useState(false);
  const [commentCount, setCommentCount] = useState(
    item.episode.comment_count ?? 0,
  );
  const { requireAuth, loginOpen } = useAuth();
  const drama = item.drama;
  const episode = item.episode;
  const tags = drama.tags.map((tag) => `#${tag}`).join(" ");

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      likeRevision.current++;
    };
  }, [ownerToken, item.item_id]);

  useEffect(() => {
    likeRevision.current++;
    setFinished(false);
    setCommentOpen(false);
    setLiked(item.drama.liked);
    setLikeCount(item.drama.like_count);
    setCommentCount(item.episode.comment_count ?? 0);
  }, [
    item.item_id,
    item.drama.liked,
    item.drama.like_count,
    item.episode.comment_count,
  ]);

  const toggleLike = () => {
    const next = !liked;
    const apply = (value: boolean) => {
      setLiked(value);
      setLikeCount((count) => Math.max(0, count + (value ? 1 : -1)));
    };
    requireAuth(() => {
      // A pending sign-in action belongs to the old slot. The new owner's
      // remounted feed must receive its own click instead of an old closure.
      if (!ownsSlot() || !ownerToken) return;
      const request = ++likeRevision.current;
      apply(next);
      postJson("/v1/reactions", {
        target_type: "drama",
        target_id: drama.id,
        kind: "like",
        remove: !next,
      }).catch(() => {
        if (!ownsSlot() || request !== likeRevision.current) return;
        likeRevision.current++;
        apply(!next);
        toast(t(locale, "err_reaction"));
      });
    });
  };

  return (
    <div className="slot" aria-hidden={!active} ref={(element) => { if (element) element.inert = !active; }}>
      <EpisodeView
        dramaId={drama.id}
        episodeId={episode.id}
        resumeSeconds={item.resume_seconds}
        active={active}
        playable
        locale={locale}
        preview
        controlsVisible={false}
        suspended={commentOpen || loginOpen}
        onPlayingChange={(playing) => { if (ownsSlot() && playing) setFinished(false); }}
        onFinished={() => { if (ownsSlot()) setFinished(true); }}
      />
      <div className="scrim" />
      <div className="rail">
        <button
          type="button"
          aria-label={t(locale, "player.details") + ": " + drama.title}
          className="avatar"
          onClick={() => { if (ownsSlot()) onOpenDrama(drama.id); }}
        >
          {drama.cover_url && (
            <img
              className="cover"
              src={thumb(drama.cover_url, IMG.avatar)}
              alt=""
              loading="lazy"
              decoding="async"
            />
          )}
        </button>
        <button
          type="button"
          className={liked ? "rail-btn on" : "rail-btn"}
          onClick={toggleLike}
          aria-label={t(locale, "player.like")}
        >
          <span className="rail-icon">
            <IconHeart active={liked} size={30} />
          </span>
          <span className="rail-label">{formatCount(likeCount, locale)}</span>
        </button>
        {siteFeature("comments") && (
          <button
            type="button"
            className="rail-btn"
            onClick={() => { if (ownsSlot()) setCommentOpen(true); }}
            aria-label={t(locale, "comments")}
          >
            <span className="rail-icon">
              <IconComment size={30} />
            </span>
            <span className="rail-label">
              {commentCount > 0
                ? formatCount(commentCount, locale)
                : t(locale, "comment")}
            </span>
          </button>
        )}
        <button
          type="button"
          className="rail-btn"
          onClick={() => {
            if (navigator.share) {
              navigator
                .share({ title: drama.title, url: window.location.href })
                .catch(() => undefined);
            } else if (navigator.clipboard) {
              navigator.clipboard
                .writeText(window.location.href)
                .catch(() => undefined);
            }
          }}
          aria-label={t(locale, "share")}
        >
          <span className="rail-icon">
            <IconShare size={30} />
          </span>
          <span className="rail-label">{t(locale, "share")}</span>
        </button>
      </div>
      {finished && (
        <button className="next-cta" onClick={() => { if (ownsSlot()) onOpenDrama(drama.id); }}>
          <IconEpisodes size={16} />
          <span>
            {t(locale, "all_episodes", {
              count: drama.published_episode_count,
            })}
          </span>
        </button>
      )}
      {siteFeature("comments") && commentOpen && (
        <CommentSheet
          episodeId={episode.id}
          locale={locale}
          initialCount={commentCount}
          onCountChange={(next) => { if (ownsSlot()) setCommentCount(next); }}
          onClose={() => { if (ownsSlot()) setCommentOpen(false); }}
        />
      )}
      <div className="meta-row">
        <div className="meta">
          <h2>{drama.title}</h2>
          <div className="line">
            <span className="ep-chip">
              {t(locale, "episode_chip", { number: episode.episode_number })}
            </span>
            {tags && <span className="meta-tags">{tags}</span>}
          </div>
        </div>
        <button className="select-pill" onClick={() => { if (ownsSlot()) onOpenDrama(drama.id); }}>
          <IconEpisodes size={15} />
          <span>{t(locale, "select")}</span>
        </button>
      </div>
    </div>
  );
}

function isFeedControl(target: EventTarget | null) {
  return target instanceof Element && Boolean(target.closest(
    'button,a,input,select,textarea,[role="button"]:not(video),[role="dialog"],[data-player-control]:not([data-player-control="video"])',
  ));
}

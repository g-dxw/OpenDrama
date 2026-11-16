import { RightsNotice } from "../ui/RightsNotice";
import { siteFeature, siteStorageKey } from "../../../shared/site";
import { useCallback, useEffect, useRef, useState } from "react";
import { authToken, getJson, postJson, type UnlockResult } from "../api";
import { useAuth } from "../auth/AuthProvider";
import type { Locale } from "../copy";
import { t } from "../copy";
import { formatCount } from "../format";
import {
  IconBack,
  IconChevronUp,
  IconClose,
  IconComment,
  IconEpisodes,
  IconHeart,
  IconLock,
  IconMoreDots,
  IconQuality,
  IconShare,
  IconSpeed,
  IconStar,
  IconSearch,
} from "../icons";
import { EpisodeView } from "../player/EpisodeView";
import { classifyEpisodeSwipe } from "../player/gestures";
import type { PlayEpisode, PlayPayload } from "../types";
import { CommentSheet } from "../ui/CommentSheet";
import { PremiumSheet } from "../ui/PremiumSheet";
import { toast } from "../ui/toast";
import { replaceMobileHash } from "../routes";
import { UnlockSheet } from "../ui/UnlockSheet";
import { Sheet } from "../ui/Sheet";

interface Props {
  dramaId: number;
  episodeId?: number;
  locale: Locale;
  onBack: () => void;
  onOpenCoins: () => void;
}

const RANGE = 30;
const SPEED_OPTIONS = [0.75, 1.0, 1.25, 1.5, 2.0];

/** The drama's own page: every episode, opened at the resume point. Finishing one jumps to the next. */
export function PlayPage({
  dramaId,
  episodeId,
  locale,
  onBack,
  onOpenCoins,
}: Props) {
  const { requireAuth, user: profile, status, refresh, loginOpen } = useAuth();
  const ownerToken = authToken();
  const user = status === "user" ? profile : null;
  const mounted = useRef(false);
  const generation = useRef(0);
  const reactionRevision = useRef({ like: 0, favorite: 0 });
  const requestedEpisode = useRef(episodeId);
  requestedEpisode.current = episodeId;
  const [snapshot, setSnapshot] = useState<{
    owner: string | null;
    dramaId: number;
    data: PlayPayload;
  } | null>(null);
  // Hide the previous identity synchronously, before effect cleanup can run.
  const payload =
    snapshot?.owner === ownerToken && snapshot.dramaId === dramaId
      ? snapshot.data
      : null;
  const [error, setError] = useState<{
    owner: string | null;
    dramaId: number;
  } | null>(null);
  const [pendingUnlock, setPendingUnlock] = useState<{
    owner: string;
    episodeId: number;
  } | null>(null);
  const [index, setIndex] = useState(0);
  const [controlsVisible, setControlsVisible] = useState(true);
  const [clearScreen, setClearScreen] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [controlsPulse, setControlsPulse] = useState(0);
  const [controlsFocused, setControlsFocused] = useState(false);
  const keyboardMode = useRef(false);
  const progressCache = useRef(new Map<number, number>());
  const [sheet, setSheet] = useState<
    "episodes" | "synopsis" | "settings" | "comments" | null
  >(null);
  const [unlockTarget, setUnlockTarget] = useState<PlayEpisode | null>(null);
  const [premiumOpen, setPremiumOpen] = useState(false);
  const unlockKeys = useRef<Map<number, string>>(new Map());
  const [commentCounts, setCommentCounts] = useState<Record<number, number>>(
    {},
  );
  const [liked, setLiked] = useState(false);
  const [favorited, setFavorited] = useState(false);
  const [likeCount, setLikeCount] = useState(0);
  const [speed, setSpeed] = useState<number>(() => {
    const saved = readPlayerPreference("minitv_speed");
    const value = Number(saved);
    return SPEED_OPTIONS.includes(value) ? value : 1.0;
  });
  const [quality, setQuality] = useState<string>(() => {
    return readPlayerPreference("minitv_quality") || "Auto";
  });
  const [availableQualities, setAvailableQualities] = useState<string[]>([]);
  const touchStart = useRef<{ x: number; y: number } | null>(null);
  const activeEpisodeId = payload?.items[index]?.id;
  const overlayOpen = Boolean(sheet || unlockTarget || premiumOpen || loginOpen);
  const showControls = useCallback(() => {
    setControlsVisible(true);
    setControlsPulse((value) => value + 1);
  }, []);
  const rememberProgress = useCallback((id: number, seconds: number) => {
    if (mounted.current && authToken() === ownerToken && Number.isFinite(seconds))
      progressCache.current.set(id, Math.max(0, seconds));
  }, [ownerToken]);

  useEffect(() => {
    setClearScreen(false);
    setControlsVisible(true);
    setPlaying(false);
    setAvailableQualities([]);
    touchStart.current = null;
  }, [activeEpisodeId, ownerToken]);

  useEffect(() => {
    if (clearScreen) return;
    if (!playing || overlayOpen || controlsFocused) {
      setControlsVisible(true);
      return;
    }
    const timer = window.setTimeout(() => setControlsVisible(false), 3200);
    return () => window.clearTimeout(timer);
  }, [playing, overlayOpen, controlsFocused, controlsPulse, clearScreen]);

  const changeSpeed = (s: number) => {
    setSpeed(s);
    savePlayerPreference("minitv_speed", String(s));
  };

  const changeQuality = (q: string) => {
    setQuality(q);
    savePlayerPreference("minitv_quality", q);
  };

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      generation.current++;
    };
  }, []);

  const load = () => {
    if (ownerToken !== authToken()) return;
    const request = ++generation.current;
    const revisions = { ...reactionRevision.current };
    const ownsRequest = () =>
      mounted.current &&
      request === generation.current &&
      ownerToken === authToken();
    setError(null);
    setSnapshot(null);
    getJson<PlayPayload>(`/v1/dramas/${dramaId}/play`)
      .then((data) => {
        if (!ownsRequest()) return;
        const resumeId = requestedEpisode.current ?? data.resume?.episode_id;
        const start = data.items.findIndex((item) => item.id === resumeId);
        setIndex(start < 0 ? 0 : start);
        if (revisions.like === reactionRevision.current.like) {
          setLiked(data.drama.liked);
          setLikeCount(data.drama.like_count);
        }
        if (revisions.favorite === reactionRevision.current.favorite)
          setFavorited(data.drama.favorited);
        setSnapshot({ owner: ownerToken, dramaId, data });
      })
      .catch(() => {
        if (ownsRequest()) setError({ owner: ownerToken, dramaId });
      });
  };

  useEffect(() => {
    setSheet(null);
    setUnlockTarget(null);
    setPremiumOpen(false);
    setCommentCounts({});
    progressCache.current.clear();
    unlockKeys.current.clear();
    setPendingUnlock((pending) =>
      pending?.owner === ownerToken ? pending : null,
    );
    reactionRevision.current = { like: 0, favorite: 0 };
    load();
    return () => {
      generation.current++;
    };
  }, [dramaId, ownerToken]);
  useEffect(() => {
    if (!payload || episodeId == null) return;
    const selected = payload.items.findIndex((item) => item.id === episodeId);
    if (selected >= 0) {
      setIndex(selected);
    }
  }, [episodeId, payload]);
  useEffect(() => {
    if (!payload || !pendingUnlock || pendingUnlock.owner !== ownerToken)
      return;
    const episode = payload.items.find(
      (item) => item.id === pendingUnlock.episodeId,
    );
    setPendingUnlock(null);
    // A login replay must use the freshly authorized episode, not its guest copy.
    if (!episode?.locked) return;
    if (episode.membership_required || (episode.unlock?.coin_price ?? 0) <= 0)
      setPremiumOpen(siteFeature("membership"));
    else if (siteFeature("coins")) setUnlockTarget(episode);
  }, [payload, pendingUnlock, ownerToken]);

  const openUnlock = (episode: PlayEpisode) =>
    requireAuth(() => {
      const currentOwner = authToken();
      if (
        !mounted.current ||
        !currentOwner ||
        (ownerToken && ownerToken !== currentOwner)
      )
        return;
      setPendingUnlock({ owner: currentOwner, episodeId: episode.id });
    });


  const unlockKeyFor = (episodeId: number) => {
    const existing = unlockKeys.current.get(episodeId);
    if (existing) return existing;
    const created =
      typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    unlockKeys.current.set(episodeId, created);
    return created;
  };

  const finishUnlock = (result: UnlockResult) => {
    if (!mounted.current || ownerToken !== authToken()) return;
    const episodeId = result.episode_id;
    setUnlockTarget(null);
    unlockKeys.current.delete(episodeId);
    setSnapshot((snapshot) => {
      if (
        !snapshot ||
        snapshot.owner !== ownerToken ||
        snapshot.dramaId !== dramaId
      )
        return snapshot;
      const prev = snapshot.data;
      const items = prev.items.map((item) =>
        result.status === "subscription" || item.id === episodeId
          ? { ...item, locked: false, unlock: null }
          : item,
      );
      const unlocked =
        result.status === "subscription"
          ? prev.entitlement.unlocked_episode_ids
          : Array.from(
              new Set([...prev.entitlement.unlocked_episode_ids, episodeId]),
            );
      return {
        ...snapshot,
        data: {
          ...prev,
          items,
          entitlement: {
            member: prev.entitlement.member || result.status === "subscription",
            unlocked_episode_ids: unlocked,
          },
        },
      };
    });
    void refresh();
    if (result.status === "subscription") {
      toast(t(locale, "unlock_member_active"));
    } else if (result.status === "already_unlocked") {
      toast(t(locale, "unlock_already"));
    } else {
      toast(
        t(locale, "unlock_success", { balance: result.balance_after ?? 0 }),
      );
    }
  };

  if (error && error.owner === ownerToken && error.dramaId === dramaId)
    return (
      <div className="center">
        <button className="retry" onClick={load}>
          {t(locale, "retry")}
        </button>
      </div>
    );
  if (!payload) return <div className="center">…</div>;
  if (payload.items.length === 0)
    return <div className="center">{t(locale, "empty")}</div>;

  const current = payload.items[index];
  const episodeTotal = payload.items.reduce((total, item) => Math.max(total, item.episode_number), 0);
  const chromeShown = current.locked || (!clearScreen && controlsVisible);
  const sheetPayload: PlayPayload = {
    ...payload,
    items: payload.items.map((item) => ({
      ...item,
      progress_seconds: progressCache.current.get(item.id) ?? item.progress_seconds,
    })),
  };
  const commentCount = commentCounts[current.id] ?? current.comment_count;
  const go = (next: number) => {
    if (next < 0 || next >= payload.items.length) return;
    setIndex(next);
    setSheet(null);
    showControls();
    const params = new URLSearchParams(
      window.location.hash.split("?")[1] || "",
    );
    params.set("episode", String(payload.items[next].id));
    replaceMobileHash("watch/" + dramaId + "?" + params.toString());
  };
  const toggleReaction = (kind: "like" | "favorite") => {
    const active = kind === "like" ? liked : favorited;
    const next = !active;
    const apply = (value: boolean) => {
      if (kind === "like") {
        setLiked(value);
        setLikeCount((count) => Math.max(0, count + (value ? 1 : -1)));
      } else {
        setFavorited(value);
      }
    };
    // Signed out: ask for a session, then replay this exact tap.
    requireAuth(() => {
      const actionOwner = authToken();
      if (
        !mounted.current ||
        !actionOwner ||
        (ownerToken && ownerToken !== actionOwner)
      )
        return;
      const revision = ++reactionRevision.current[kind];
      apply(next);
      postJson("/v1/reactions", {
        target_type: "drama",
        target_id: dramaId,
        kind,
        remove: !next,
      }).catch(() => {
        if (
          !mounted.current ||
          authToken() !== actionOwner ||
          reactionRevision.current[kind] !== revision
        )
          return;
        reactionRevision.current[kind]++;
        apply(!next);
        toast(t(locale, "err_reaction"));
      });
    });
  };

  return (
    <div
      className={`play player-page${current.locked ? " locked-view" : ""}${chromeShown ? "" : " chrome-hidden"}${clearScreen ? " clear-screen" : ""}`}
      onClickCapture={showControls}
      onPointerDownCapture={() => { keyboardMode.current = false; setControlsFocused(false); }}
      onKeyDownCapture={(event) => {
        if (event.key === "Tab") {
          keyboardMode.current = true;
          setControlsFocused(true);
          showControls();
        }
      }}
      onFocusCapture={(event) => {
        setControlsFocused(keyboardMode.current && isSwipeControl(event.target));
        showControls();
      }}
      onBlurCapture={(event) => setControlsFocused(keyboardMode.current && isSwipeControl(event.relatedTarget))}
      onTouchStart={(event) => {
        keyboardMode.current = false;
        setControlsFocused(false);
        touchStart.current = !overlayOpen && event.touches.length === 1 && !isSwipeControl(event.target)
          ? { x: event.touches[0].clientX, y: event.touches[0].clientY }
          : null;
      }}
      onTouchMove={(event) => {
        if (event.touches.length !== 1) touchStart.current = null;
      }}
      onTouchCancel={() => { touchStart.current = null; }}
      onTouchEnd={(event) => {
        const start = touchStart.current;
        touchStart.current = null;
        if (!start || overlayOpen || event.touches.length !== 0 || event.changedTouches.length !== 1) return;
        const end = event.changedTouches[0];
        const delta = classifyEpisodeSwipe({ dx: end.clientX - start.x, dy: end.clientY - start.y,
          startX: start.x, viewportWidth: window.innerWidth });
        const next = index + delta;
        if (delta && next >= 0 && next < payload.items.length) {
          if (event.cancelable) event.preventDefault();
          go(next);
        }
      }}
      onKeyDown={(event) => {
        if (overlayOpen || isSwipeControl(event.target)) return;
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          go(index + (event.key === "ArrowDown" ? 1 : -1));
        }
      }}
    >
      <div
        className="snap"
        style={{ transform: `translateY(-${index * 100}%)` }}
      >
        {payload.items.map((episode, position) => (
          <div className="slot" key={episode.id}>
            {position === index &&
              (episode.locked ? (
                <LockedEpisode
                  episode={episode}
                  locale={locale}
                  signedIn={Boolean(user)}
                  balance={user?.coins ?? 0}
                  onUnlock={() => openUnlock(episode)}
                  onMember={() => openUnlock(episode)}
                  onNeedCoins={onOpenCoins}
                />
              ) : (
                <EpisodeView
                  dramaId={dramaId}
                  episodeId={episode.id}
                  resumeSeconds={episode.completed ? 0 : (progressCache.current.get(episode.id) ?? episode.progress_seconds)}
                  active
                  playable
                  playbackSpeed={speed}
                  quality={quality}
                  locale={locale}
                  controlsVisible={chromeShown}
                  suspended={overlayOpen}
                  onPlayingChange={setPlaying}
                  onProgress={rememberProgress}
                  onInteraction={showControls}
                  onVideoTap={() => {
                    if (!clearScreen) return false;
                    setClearScreen(false);
                    showControls();
                    return true;
                  }}
                  onQualitiesChange={(levels) => {
                    setAvailableQualities(levels);
                    setQuality((selected) => levels.includes(selected) ? selected : levels[0] || "Original");
                  }}
                  onFinished={() => {
                    setSnapshot((previous) => previous?.owner === ownerToken && previous.dramaId === dramaId
                      ? { ...previous, data: { ...previous.data, items: previous.data.items.map((item) =>
                        item.id === episode.id ? { ...item, completed: true } : item) } } : previous);
                    go(position + 1);
                  }}
                />
              ))}
          </div>
        ))}
      </div>
      <div className="back player-chrome">
        <button
          className="icon-btn back-btn"
          onClick={onBack}
          aria-label={t(locale, "player.back")}
        >
          <IconBack size={20} />
        </button>
        <button className="back-title player-title-button" onClick={() => setSheet("synopsis")}
          aria-label={t(locale, "player.details")}>
          <span>{payload.drama.title}</span>
          <small>{t(locale, "player.episodeCounter", { number: current.episode_number, total: episodeTotal })}</small>
        </button>
        <button
          type="button"
          className="icon-btn setting-btn"
          onClick={() => { window.location.hash = "search"; }}
          aria-label={t(locale, "search")}
        >
          <IconSearch />
        </button>
      </div>
      <div className="rail player-chrome">
        <button
          type="button"
          className={liked ? "rail-btn player-social on" : "rail-btn player-social"}
          onClick={() => toggleReaction("like")}
          aria-label={t(locale, "player.like")}
          aria-pressed={liked}
        >
          <span className="rail-icon">
            <IconHeart active={liked} size={30} />
          </span>
          <span className="rail-label">{formatCount(likeCount, locale)}</span>
        </button>

        {siteFeature("comments") && (
          <button
            type="button"
            className="rail-btn player-social"
            onClick={() => setSheet("comments")}
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
          className={favorited ? "rail-btn player-social on" : "rail-btn player-social"}
          onClick={() => toggleReaction("favorite")}
          aria-label={t(locale, "favorite")}
          aria-pressed={favorited}
        >
          <span className="rail-icon">
            <IconStar active={favorited} size={30} />
          </span>
          <span className="rail-label">{t(locale, "favorite")}</span>
        </button>

        <button
          type="button"
          className="rail-btn player-social"
          onClick={() => {
            if (navigator.share) {
              navigator
                .share({
                  title: payload.drama.title,
                  url: window.location.href,
                })
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
        <button type="button" className="rail-btn" data-player-action="episodes" onClick={() => setSheet("episodes")}
          aria-label={t(locale, "all_episodes_bar", { count: payload.items.length })}>
          <span className="rail-icon"><IconEpisodes size={28} /></span>
          <span className="rail-label">{current.episode_number}/{episodeTotal}</span>
        </button>
        <button type="button" className="rail-btn" onClick={() => setSheet("settings")}
          aria-label={t(locale, "playback_settings")}>
          <span className="rail-icon"><IconMoreDots size={28} /></span>
          <span className="rail-label">{t(locale, "more")}</span>
        </button>
      </div>
      <div className="meta player-chrome">
        <button type="button" className="player-meta-title" onClick={() => setSheet("synopsis")}>
          <h2>{payload.drama.title}</h2>
          <span className="player-episode-chip">{t(locale, "player.episodeCounter", { number: current.episode_number, total: episodeTotal })}</span>
        </button>
        <RightsNotice rights={payload.drama} locale={locale} />
        {payload.drama.synopsis && (
          <button type="button"
            className="syn player-synopsis"
            onClick={() => setSheet("synopsis")}
          >
            {t(locale, "episode_short", { number: current.episode_number })} |{" "}
            {payload.drama.synopsis}{" "}
            {t(locale, "expand")}
          </button>
        )}
        {payload.drama.tags.length > 0 && (
          <p className="tags">
            {payload.drama.tags.map((tag) => `#${tag}`).join(" ")}
          </p>
        )}
      </div>
      {clearScreen && <button type="button" className="player-exit-clear" onClick={() => { setClearScreen(false); showControls(); }}>
        <IconClose size={18} />{t(locale, "player.exitClear")}
      </button>}
      {sheet === "settings" && (
          <PlaybackSettingsSheet
            locale={locale}
            speed={speed}
            onSpeedChange={changeSpeed}
            quality={quality}
            onQualityChange={changeQuality}
            availableQualities={availableQualities}
            onClearScreen={() => { setSheet(null); setClearScreen(true); setControlsVisible(false); }}
            onDetails={() => setSheet("synopsis")}
            onClose={() => setSheet(null)}
          />
      )}
      {siteFeature("comments") && sheet === "comments" && (
        <>
          <button className="sheet-mask" onClick={() => setSheet(null)} />
          <CommentSheet
            episodeId={current.id}
            locale={locale}
            initialCount={commentCount}
            onCountChange={(next) =>
              setCommentCounts((prev) => ({ ...prev, [current.id]: next }))
            }
            onClose={() => setSheet(null)}
          />
        </>
      )}
      {sheet && sheet !== "settings" && sheet !== "comments" && (
          <EpisodeSheet
            payload={sheetPayload}
            index={index}
            tab={sheet}
            locale={locale}
            onTab={setSheet}
            onClose={() => setSheet(null)}
            onPick={(picked) => {
              setSheet(null);
              go(picked);
            }}
          />
      )}
      {siteFeature("coins") && unlockTarget && (
        <UnlockSheet
          rights={payload.drama}
          locale={locale}
          episode={unlockTarget}
          price={unlockTarget.unlock?.coin_price ?? 0}
          balance={user?.coins ?? 0}
          idempotencyKey={unlockKeyFor(unlockTarget.id)}
          onClose={() => setUnlockTarget(null)}
          onDone={finishUnlock}
          onInsufficient={() => void refresh()}
          onNeedCoins={() => {
            setUnlockTarget(null);
            void refresh();
            onOpenCoins();
          }}
        />
      )}
      {siteFeature("membership") && premiumOpen && (
        <PremiumSheet
          rights={payload.drama}
          locale={locale}
          membership={user?.membership}
          onChanged={() => {
            void refresh();
            load();
          }}
          onClose={() => setPremiumOpen(false)}
        />
      )}
    </div>
  );
}

function PlaybackSettingsSheet({
  locale, speed, onSpeedChange, quality, onQualityChange, availableQualities,
  onClose, onClearScreen, onDetails,
}: {
  locale: Locale; speed: number; onSpeedChange: (s: number) => void;
  quality: string; onQualityChange: (q: string) => void; availableQualities: string[];
  onClose: () => void; onClearScreen: () => void; onDetails: () => void;
}) {
  const qualityLabel = (value: string) => value === "Auto" ? t(locale, "auto_quality")
    : value === "Original" ? t(locale, "player.original") : value;
  return (
    <Sheet title={t(locale, "playback_settings")} closeLabel={t(locale, "close")}
      onClose={onClose} className="player-sheet settings-sheet">
      <section className="settings-group" aria-label={t(locale, "speed")}>
        <div className="group-title"><span aria-hidden="true"><IconSpeed size={18} /></span>
          <span className="group-label">{t(locale, "speed")}</span><span className="group-current">{speed}×</span></div>
        <div className="pills-grid" role="group" aria-label={t(locale, "speed")}>
          {SPEED_OPTIONS.map((value) => <button type="button" key={value}
            className={speed === value ? "setting-pill on" : "setting-pill"}
            aria-pressed={speed === value} onClick={() => onSpeedChange(value)}>{value}×</button>)}
        </div>
      </section>
      <section className="settings-group" aria-label={t(locale, "quality")}>
        <div className="group-title"><span aria-hidden="true"><IconQuality size={18} /></span>
          <span className="group-label">{t(locale, "quality")}</span>
          <span className="group-current">{availableQualities.length ? qualityLabel(quality) : "—"}</span></div>
        {availableQualities.length > 1 ? <div className="pills-grid qualities" role="group" aria-label={t(locale, "quality")}>
          {availableQualities.map((value) => <button type="button" key={value}
            className={quality === value ? "setting-pill on" : "setting-pill"}
            aria-pressed={quality === value} onClick={() => onQualityChange(value)}>{qualityLabel(value)}</button>)}
        </div> : <p className="player-source-note" role="status">{t(locale,
          availableQualities.length ? (availableQualities[0] === "Auto" ? "player.autoQualityOnly" : "player.originalOnly") : "player.qualityLoading")}</p>}
      </section>
      <div className="player-setting-actions">
        <button type="button" onClick={onClearScreen}>
          <span className="player-setting-icon" aria-hidden="true"><IconQuality size={21} /></span>
          <span><b>{t(locale, "player.clear")}</b><small>{t(locale, "player.clearHint")}</small></span>
          <span className="player-setting-arrow" aria-hidden="true"><IconChevronUp size={16} /></span>
        </button>
        <button type="button" onClick={onDetails}>
          <span className="player-setting-icon" aria-hidden="true"><IconEpisodes size={21} /></span>
          <span><b>{t(locale, "player.details")}</b><small>{t(locale, "player.detailsHint")}</small></span>
          <span className="player-setting-arrow" aria-hidden="true"><IconChevronUp size={16} /></span>
        </button>
      </div>
    </Sheet>
  );
}

function LockedEpisode({
  episode,
  locale,
  signedIn,
  balance,
  onUnlock,
  onMember,
  onNeedCoins,
}: {
  episode: PlayEpisode;
  locale: Locale;
  signedIn: boolean;
  balance: number;
  onUnlock: () => void;
  onMember: () => void;
  onNeedCoins: () => void;
}) {
  const price = episode.unlock?.coin_price ?? 0;
  const short = Math.max(0, price - balance);
  const coinOnly = !episode.membership_required && price > 0;

  return (
    <div className="lock">
      <div className="lock-panel">
        <div className="lock-badge">
          <IconLock size={30} />
        </div>
        <h2>{t(locale, "unlock_title", { number: episode.episode_number })}</h2>
        <p className="lock-sub">{t(locale, "unlock_sub")}</p>

        {siteFeature("coins") && coinOnly && (
          <div className="lock-price">
            <b>{t(locale, "unlock_price", { price })}</b>
            <span>{t(locale, "unlock_per_episode")}</span>
          </div>
        )}
        {signedIn && (
          <p className="lock-balance">
            {t(locale, "unlock_balance", { balance })}
          </p>
        )}

        {coinOnly && siteFeature("coins") &&
          (!signedIn ? (
            <button
              type="button"
              className="lock-cta"
              onClick={onUnlock}
              data-testid="lock-login"
            >
              {t(locale, "unlock_login_cta")}
            </button>
          ) : short > 0 ? (
            <button
              type="button"
              className="lock-cta ghost"
              onClick={onNeedCoins}
              data-testid="lock-need-coins"
            >
              {t(locale, "unlock_short_cta", { short })}
            </button>
          ) : (
            <button
              type="button"
              className="lock-cta"
              onClick={onUnlock}
              data-testid="lock-unlock"
            >
              {t(locale, "unlock_cta", { price })}
            </button>
          ))}

        {siteFeature("membership") && (
          <button
            type="button"
            className={coinOnly ? "lock-member" : "lock-cta"}
            onClick={onMember}
            data-testid="lock-member"
          >
            {coinOnly
              ? t(locale, "unlock_member_cta")
              : t(locale, "premium_strip_cta")}
          </button>
        )}
        {!(coinOnly && siteFeature("coins")) && !siteFeature("membership") && <p role="status">{t(locale, "player.purchaseUnavailable")}</p>}
      </div>
    </div>
  );
}

function EpisodeSheet({ payload, index, tab, locale, onTab, onPick, onClose }: {
  payload: PlayPayload; index: number; tab: "episodes" | "synopsis"; locale: Locale;
  onTab: (tab: "episodes" | "synopsis") => void; onPick: (index: number) => void; onClose: () => void;
}) {
  const ranges: number[] = [];
  for (let start = 0; start < payload.items.length; start += RANGE) ranges.push(start);
  const [range, setRange] = useState(Math.floor(index / RANGE) * RANGE);
  const currentRef = useRef<HTMLButtonElement>(null);
  const slice = payload.items.slice(range, range + RANGE);
  const watched = payload.items.filter((episode) => episode.completed).length;
  const watching = payload.items.filter((episode) => !episode.completed && !episode.locked && episode.progress_seconds > 0).length;
  const summary = [watched ? t(locale, "ep_watched", { count: watched }) : "",
    watching ? t(locale, "ep_watching", { count: watching }) : ""].filter(Boolean).join(" · ");
  useEffect(() => {
    currentRef.current?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [range, tab]);
  return (
    <Sheet title={t(locale, "player.series")} closeLabel={t(locale, "close")} onClose={onClose} className="player-sheet player-episodes-sheet">
      <div className="player-series-summary">
        {payload.drama.cover_url && <img src={payload.drama.cover_url} alt="" />}
        <div><h3>{payload.drama.title}</h3><p>{t(locale, "all_episodes_bar", { count: payload.items.length })}</p>
          {summary && <p className="player-watch-summary">{summary}</p>}</div>
      </div>
      <div className="player-sheet-tabs" role="tablist" aria-label={t(locale, "player.series")}
        onKeyDown={(event) => {
          if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
          event.preventDefault();
          const next = event.key === "Home" ? "episodes" : event.key === "End" ? "synopsis" : tab === "episodes" ? "synopsis" : "episodes";
          onTab(next);
          document.getElementById(next === "episodes" ? "player-episode-tab" : "player-synopsis-tab")?.focus();
        }}>
        <button type="button" role="tab" aria-selected={tab === "episodes"}
          tabIndex={tab === "episodes" ? 0 : -1}
          aria-controls="player-episode-panel" id="player-episode-tab" onClick={() => onTab("episodes")}>{t(locale, "episodes_tab")}</button>
        <button type="button" role="tab" aria-selected={tab === "synopsis"}
          tabIndex={tab === "synopsis" ? 0 : -1}
          aria-controls="player-synopsis-panel" id="player-synopsis-tab" onClick={() => onTab("synopsis")}>{t(locale, "synopsis")}</button>
      </div>
      {tab === "synopsis" ? <div id="player-synopsis-panel" role="tabpanel" aria-labelledby="player-synopsis-tab">
        <RightsNotice rights={payload.drama} locale={locale} />
        <p className="syn-full">{payload.drama.synopsis || t(locale, "empty")}</p>
        {payload.drama.tags.length > 0 && <p className="player-series-tags">{payload.drama.tags.join(" · ")}</p>}
      </div> : <div id="player-episode-panel" role="tabpanel" aria-labelledby="player-episode-tab">
        <div className="ranges-row">
          <div className="ranges" role="group" aria-label={t(locale, "player.episodeRanges")}>
            {ranges.map((start) => <button type="button" key={start} className={start === range ? "on" : ""}
              aria-pressed={start === range} onClick={() => setRange(start)}>
              {payload.items[start].episode_number}–{payload.items[Math.min(start + RANGE, payload.items.length) - 1].episode_number}
            </button>)}
          </div>
          {!(index >= range && index < range + RANGE) && <button type="button" className="player-current-link"
            onClick={() => setRange(Math.floor(index / RANGE) * RANGE)}>{t(locale, "player.current")}</button>}
        </div>
        <div className="ep-grid">
          {slice.map((episode, offset) => {
            const position = range + offset;
            const ratio = episode.duration_seconds > 0 && !episode.locked && !episode.completed
              ? Math.min(1, episode.progress_seconds / episode.duration_seconds) : 0;
            const state = episode.locked ? "locked" : episode.completed ? "done" : ratio > 0 ? "partial" : "";
            const stateLabel = t(locale, episode.locked ? "player.locked" : episode.completed ? "player.watched" : ratio > 0 ? "player.watching" : episode.free ? "player.free" : "player.available");
            return <button type="button" key={episode.id} data-episode-id={episode.id} ref={position === index ? currentRef : undefined}
              className={(position === index ? "on " : "") + state} aria-current={position === index ? "true" : undefined}
              aria-label={t(locale, "episode_short", { number: episode.episode_number }) + " · " + stateLabel + (position === index ? " · " + t(locale, "player.current") : "")}
              onClick={() => onPick(position)}>
              <span className="player-episode-number">{episode.episode_number}</span>
              <span className="player-episode-state">{episode.locked && <IconLock size={11} />}{stateLabel}</span>
              {state === "partial" && <span className="ep-progress"><i style={{ width: (ratio * 100) + "%" }} /></span>}
            </button>;
          })}
        </div>
      </div>}
    </Sheet>
  );
}

function isSwipeControl(target: EventTarget | null): boolean {
  return target instanceof Element && Boolean(target.closest(
    'button,a,input,select,textarea,[role="button"]:not(video),[role="dialog"],[contenteditable="true"],[data-player-control]:not([data-player-control="video"])',
  ));
}

function readPlayerPreference(key: string): string | null {
  try { return localStorage.getItem(siteStorageKey(key)) ?? localStorage.getItem(key); } catch { return null; }
}
function savePlayerPreference(key: string, value: string) {
  try { localStorage.setItem(siteStorageKey(key), value); } catch { /* Playback remains usable without storage. */ }
}

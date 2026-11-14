import { useEffect, useRef, useState } from "react";
import Hls from "hls.js";
import {
  prepareSubtitleTracks,
  type SubtitleTrack,
} from "../../shared/consumer/subtitles";
import {
  get,
  post,
  authToken,
  errorMessage,
  ApiError,
  type PlayPayload,
  type PlayEpisode,
  type PlaybackAuth,
  type CommentsPayload,
} from "./consumer-api";
import { useAuth } from "./auth";
import { Empty, Failure, Icon, Loading, Modal } from "./components";
import "./player.css";
export function Watch({
  dramaId,
  episodeId,
}: {
  dramaId: number;
  episodeId?: number;
}) {
  const { status, user, requireAuth, refresh } = useAuth();
  const [ownedData, setOwnedData] = useState<{
      identity: string;
      payload: PlayPayload;
    } | null>(null),
    [selected, setSelected] = useState<number | undefined>(episodeId),
    [error, setError] = useState(""),
    [unlockOpen, setUnlockOpen] = useState(false),
    [commentsOpen, setCommentsOpen] = useState(false),
    [commentsEpisodeId, setCommentsEpisodeId] = useState<number | undefined>(),
    [shareOpen, setShareOpen] = useState(false),
    [copied, setCopied] = useState(false),
    [expanded, setExpanded] = useState(false),
    [busy, setBusy] = useState(false),
    [revision, setRevision] = useState(0),
    [liked, setLiked] = useState(false),
    [favorited, setFavorited] = useState(false);
  const identity = String(user?.id ?? "guest");
  const identityRef = useRef(identity);
  identityRef.current = identity;
  const previousIdentity = useRef(identity);
  const selectionIntent = useRef(episodeId);
  const data = ownedData?.identity === identity ? ownedData.payload : null;
  const unlockKeys = useRef(new Map<number, string>());
  const loadRequest = useRef(0);
  const load = () => {
    const request = ++loadRequest.current;
    const owner = identityRef.current;
    setError("");
    get<PlayPayload>("/v1/dramas/" + dramaId + "/play")
      .then((d) => {
        if (request !== loadRequest.current || owner !== identityRef.current)
          return;
        setOwnedData({ identity: owner, payload: d });
        const intent = selectionIntent.current;
        setSelected(
          intent && d.items.some((e) => e.id === intent)
            ? intent
            : (d.resume?.episode_id ?? d.items[0]?.id),
        );
        setLiked(d.drama.liked);
        setFavorited(d.drama.favorited);
      })
      .catch((e) => {
        if (request === loadRequest.current && owner === identityRef.current)
          setError(errorMessage(e));
      });
  };
  useEffect(() => {
    setOwnedData(null);
    selectionIntent.current = episodeId;
    setSelected(episodeId);
  }, [dramaId, episodeId]);
  useEffect(() => {
    if (status !== "loading") load();
    return () => {
      loadRequest.current++;
    };
  }, [dramaId, status, user?.id, revision]);
  useEffect(() => {
    setBusy(false);
    setError("");
    unlockKeys.current.clear();
    if (
      previousIdentity.current !== identity &&
      previousIdentity.current !== "guest"
    ) {
      setUnlockOpen(false);
      setCommentsOpen(false);
      setShareOpen(false);
    }
    previousIdentity.current = identity;
  }, [identity]);
  const episode = data?.items.find((e) => e.id === selected);
  const isMembershipOnly = (item: PlayEpisode) =>
    Boolean(item.membership_required) ||
    (item.coin_price ??
      item.unlock?.coin_price ??
      data?.drama.coin_price_per_episode ??
      0) <= 0;
  const membershipOnly = episode ? isMembershipOnly(episode) : false;
  const coinPrice =
    episode?.coin_price ??
    episode?.unlock?.coin_price ??
    data?.drama.coin_price_per_episode ??
    0;
  const next =
    data?.items[(data?.items.findIndex((e) => e.id === selected) ?? -1) + 1];
  const membershipLink =
    "#premium?drama=" + dramaId + "&episode=" + (episode?.id || "");
  const coinsLink =
    "#coins?drama=" + dramaId + "&episode=" + (episode?.id || "");
  function select(item: PlayEpisode) {
    selectionIntent.current = item.id;
    setSelected(item.id);
    if (item.locked && !isMembershipOnly(item))
      requireAuth(() => setUnlockOpen(true));
  }
  async function unlock() {
    if (!episode || isMembershipOnly(episode)) return;
    const owner = identityRef.current;
    setBusy(true);
    setError("");
    let key = unlockKeys.current.get(episode.id);
    if (!key) {
      key = crypto.randomUUID();
      unlockKeys.current.set(episode.id, key);
    }
    try {
      await post(
        "/v1/unlock",
        { episode_id: episode.id },
        { "Idempotency-Key": key },
      );
      if (owner !== identityRef.current) return;
      await refresh();
      if (owner !== identityRef.current) return;
      setUnlockOpen(false);
      setRevision((x) => x + 1);
    } catch (e) {
      if (owner !== identityRef.current) return;
      setError(
        e instanceof ApiError && e.status === 402
          ? "Not enough coins. Top up your wallet to continue."
          : errorMessage(e),
      );
      await refresh();
    } finally {
      if (owner === identityRef.current) setBusy(false);
    }
  }
  async function react(kind: "like" | "favorite") {
    const owner = identityRef.current;
    const previous = kind === "like" ? liked : favorited;
    setBusy(true);
    try {
      await post("/v1/reactions", {
        target_type: "drama",
        target_id: dramaId,
        kind,
        remove: previous,
      });
      if (owner !== identityRef.current) return;
      if (kind === "like") setLiked(!previous);
      else setFavorited(!previous);
    } catch (e) {
      if (owner === identityRef.current) setError(errorMessage(e));
    } finally {
      if (owner === identityRef.current) setBusy(false);
    }
  }
  const commentsModal =
    commentsOpen && commentsEpisodeId != null ? (
      <Modal
        title="Comments"
        className="watch-comments-modal"
        onClose={() => setCommentsOpen(false)}
      >
        <Comments key={commentsEpisodeId} episodeId={commentsEpisodeId} />
      </Modal>
    ) : null;
  if (!data)
    return (
      <div className="watch-page page-content">
        <div>
          {error ? <Failure message={error} retry={load} /> : <Loading />}
        </div>
        {commentsModal}
      </div>
    );
  const shareUrl = new URL(window.location.href);
  shareUrl.search = "";
  shareUrl.hash =
    "watch/" + dramaId + (episode ? "?episode=" + episode.id : "");
  return (
    <div className="watch-page page-content">
      <div className="watch-layout">
        <section className="watch-main" aria-label="Drama player">
          <div className="video-frame">
            <span className="player-brand" aria-hidden="true">
              JJ
            </span>
            {episode ? (
              episode.locked ? (
                <div className="locked-player">
                  <div className="lock-copy">
                    <span className="player-lock-icon">
                      <Icon name="lock" size={24} />
                    </span>
                    <h2>
                      {membershipOnly ? "Members only" : "The story continues"}
                    </h2>
                    <p>
                      {membershipOnly
                        ? "This episode requires a membership."
                        : "Unlock episode " +
                          episode.episode_number +
                          " to continue watching."}
                    </p>
                    {coinPrice > 0 && (
                      <p className="player-lock-price">
                        <strong>{coinPrice} coins</strong> / episode
                      </p>
                    )}
                    {membershipOnly ? (
                      <a className="button primary" href={membershipLink}>
                        See memberships
                      </a>
                    ) : (
                      <button
                        className="button primary"
                        onClick={() => requireAuth(() => setUnlockOpen(true))}
                      >
                        {user ? "Unlock episode" : "Sign in to continue"}
                      </button>
                    )}
                    <p className="player-lock-note">
                      {membershipOnly
                        ? "Membership access and any displayed episode price apply. Review the plan before buying."
                        : "Unlock this episode once and keep watching from your account."}
                    </p>
                  </div>
                </div>
              ) : (
                <Video
                  key={episode.id + ":" + (user?.id ?? "guest")}
                  dramaId={dramaId}
                  episode={episode}
                  onDenied={() => setRevision((x) => x + 1)}
                  onFinished={() => {
                    if (next) {
                      selectionIntent.current = next.id;
                      setSelected(next.id);
                    }
                  }}
                />
              )
            ) : (
              <Empty message="No episodes are available yet." />
            )}
          </div>
        </section>
        <aside
          className="watch-sidebar"
          aria-label="Drama details and episodes"
        >
          <nav className="watch-breadcrumb" aria-label="Breadcrumb">
            <a href="#home">Home</a>
            <span>/</span>
            <a href="#browse">Dramas</a>
            <span>/</span>
            <span>{episode ? "EP " + episode.episode_number : "Details"}</span>
          </nav>
          <h1>{data.drama.title}</h1>
          <p className="watch-episode-name">
            {episode
              ? "EP " +
                episode.episode_number +
                " · " +
                (episode.title || "Episode " + episode.episode_number)
              : data.items.length + " episodes"}
          </p>
          <p className="watch-side-label">Synopsis</p>
          <p
            id="drama-synopsis"
            className={"synopsis " + (expanded ? "expanded" : "clamped")}
          >
            {data.drama.synopsis || "A new story is on its way."}
          </p>
          {data.drama.synopsis && (
            <button
              className="synopsis-more"
              aria-expanded={expanded}
              aria-controls="drama-synopsis"
              onClick={() => setExpanded(!expanded)}
            >
              {expanded ? "less" : "more"}
            </button>
          )}
          <div className="tags">
            {data.drama.tags.map((tag) => (
              <a href={"#browse?tag=" + encodeURIComponent(tag)} key={tag}>
                {tag}
              </a>
            ))}
          </div>
          <div className="reaction-actions">
            <button
              className={"watch-action " + (liked ? "selected" : "")}
              disabled={busy}
              aria-pressed={liked}
              aria-label="Like"
              onClick={() =>
                requireAuth(() => {
                  void react("like");
                })
              }
            >
              <Icon name="heart" />
              <span>
                {Math.max(
                  0,
                  data.drama.like_count +
                    Number(liked) -
                    Number(data.drama.liked),
                ).toLocaleString()}
              </span>
            </button>
            <button
              className={"watch-action " + (favorited ? "selected" : "")}
              disabled={busy}
              aria-pressed={favorited}
              aria-label="My list"
              onClick={() =>
                requireAuth(() => {
                  void react("favorite");
                })
              }
            >
              <Icon name="star" />
              <span>{favorited ? "Saved" : "Favorite"}</span>
            </button>
            <button
              className="watch-action"
              disabled={!episode}
              onClick={() => {
                setCommentsEpisodeId(episode?.id);
                setCommentsOpen(true);
              }}
            >
              <Icon name="message" />
              <span>Comment</span>
            </button>
            <button
              className="watch-action"
              onClick={() => {
                setCopied(false);
                setShareOpen(true);
              }}
            >
              <svg
                width="20"
                height="20"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.7"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d="M12 16V3m-4 4 4-4 4 4M5 12v8h14v-8" />
              </svg>
              <span>Share</span>
            </button>
          </div>
          <div className="watch-episodes-heading">
            <h2>Episodes</h2>
            <span>All {data.items.length} episodes</span>
          </div>
          <div className="episode-legend">
            <span>
              <Icon name="lock" size={11} />
              {data.items.filter((item) => item.locked).length} locked
            </span>
            <span>
              {data.entitlement.member
                ? "Membership active"
                : data.entitlement.unlocked_episode_ids.length + " unlocked"}
            </span>
          </div>
          <div className="episode-grid">
            {data.items.map((item) => (
              <button
                key={item.id}
                className={
                  "episode-button " +
                  (item.id === selected ? "active " : "") +
                  (item.locked ? "locked" : "")
                }
                aria-label={
                  "Episode " +
                  item.episode_number +
                  (item.locked ? ", locked" : "")
                }
                aria-current={item.id === selected ? "true" : undefined}
                onClick={() => select(item)}
              >
                {item.locked && <Icon name="lock" size={9} />}
                <span>{item.episode_number}</span>
                {item.completed && (
                  <span className="episode-done">
                    <Icon name="check" size={9} />
                  </span>
                )}
              </button>
            ))}
          </div>
          {episode?.locked ? (
            <div className="sidebar-upsell">
              <div>
                <strong>
                  {membershipOnly ? "Members only" : "Unlock this episode"}
                </strong>
                <p>
                  {membershipOnly
                    ? "Choose a membership to access this episode."
                    : coinPrice +
                      " coins · Your balance: " +
                      (user?.coins ?? 0) +
                      " coins"}
                </p>
              </div>
              {membershipOnly ? (
                <a className="button primary" href={membershipLink}>
                  See memberships
                </a>
              ) : (
                <button
                  className="button primary"
                  onClick={() => requireAuth(() => setUnlockOpen(true))}
                >
                  Unlock episode
                </button>
              )}
            </div>
          ) : (
            <div className="watch-access">
              <span>
                {data.drama.play_count.toLocaleString()} views ·{" "}
                {data.items.filter((item) => item.free).length} free episodes
              </span>
              <a href={membershipLink}>Membership</a>
              <a href={coinsLink}>Get coins</a>
            </div>
          )}
          {error && <Failure message={error} />}
        </aside>
      </div>
      {commentsModal}
      {shareOpen && (
        <Modal
          title="Share this drama"
          className="watch-share-modal"
          onClose={() => setShareOpen(false)}
        >
          <label className="share-link-label">
            Drama link
            <input
              aria-label="Drama link"
              readOnly
              value={shareUrl.toString()}
            />
          </label>
          <button
            className="button primary full-width"
            onClick={async () => {
              try {
                await navigator.clipboard.writeText(shareUrl.toString());
                setCopied(true);
              } catch {
                setCopied(false);
              }
            }}
          >
            {copied ? "Link copied" : "Copy link"}
          </button>
          <p className="muted small" role="status">
            {copied
              ? "The drama link is ready to share."
              : "You can also select and copy the link above."}
          </p>
        </Modal>
      )}
      {unlockOpen && episode && !membershipOnly && (
        <Modal
          title={"Unlock episode " + episode.episode_number}
          onClose={() => setUnlockOpen(false)}
        >
          <p>
            Spend <strong>{coinPrice} coins</strong> to unlock this episode.
          </p>
          <p className="muted">Your balance: {user?.coins ?? 0} coins</p>
          {error && (
            <p className="error-text" role="alert">
              {error}
            </p>
          )}
          {(user?.coins ?? 0) >= coinPrice ? (
            <button
              className="button primary full-width"
              disabled={busy}
              onClick={() => void unlock()}
            >
              {busy ? "Unlocking…" : "Confirm unlock"}
            </button>
          ) : (
            <a
              className="button primary full-width"
              href={coinsLink}
              onClick={() => setUnlockOpen(false)}
            >
              Get more coins
            </a>
          )}
          <a
            className="text-button"
            href={membershipLink}
            onClick={() => setUnlockOpen(false)}
          >
            See membership options
          </a>
        </Modal>
      )}
    </div>
  );
}
export function Video({
  dramaId,
  episode,
  onFinished,
  onDenied,
  muted = false,
  poster,
}: {
  dramaId: number;
  episode: PlayEpisode;
  onFinished: () => void;
  onDenied: () => void;
  muted?: boolean;
  poster?: string;
}) {
  const ownerToken = useRef(authToken());
  const video = useRef<HTMLVideoElement>(null),
    hls = useRef<Hls | null>(null),
    latest = useRef({ onFinished, onDenied });
  latest.current = { onFinished, onDenied };
  const [tracks, setTracks] = useState<SubtitleTrack[]>([]);
  const [subtitleError, setSubtitleError] = useState("");
  const [auth, setAuth] = useState<PlaybackAuth | null>(null),
    [error, setError] = useState(""),
    [levels, setLevels] = useState<Array<{ index: number; label: string }>>([]),
    [quality, setQuality] = useState(-1),
    [speed, setSpeed] = useState(1),
    [subtitle, setSubtitle] = useState("default"),
    [retry, setRetry] = useState(0);
  const position = useRef(episode.progress_seconds || 0),
    renewed = useRef(false);
  useEffect(() => {
    let alive = true;
    setError("");
    setAuth(null);
    getAuth();
    async function getAuth() {
      if (ownerToken.current !== authToken()) return;
      try {
        const result = await post<PlaybackAuth>("/v1/playback", {
          drama_id: dramaId,
          episode_id: episode.id,
        });
        if (!alive || ownerToken.current !== authToken()) return;
        if (!result.allowed || !result.url) {
          setError(
            result.reason === "region_blocked"
              ? "This drama is not available in your region."
              : "This episode is not available.",
          );
          return;
        }
        setAuth(result);
      } catch (e) {
        if (alive) setError(errorMessage(e));
      }
    }
    return () => {
      alive = false;
    };
  }, [dramaId, episode.id, retry]);
  useEffect(() => {
    const el = video.current;
    if (!el || !auth?.url) return;
    let started = false,
      finished = false,
      heartbeat = 0;
    const report = (kind: string) => {
      if (ownerToken.current !== authToken()) return;
      void post("/v1/playback/" + kind, {
        episode_id: episode.id,
        position_seconds: Math.floor(el.currentTime || 0),
      }).catch(() => undefined);
    };
    const metadata = () => {
      if (ownerToken.current !== authToken()) return;
      el.currentTime = Math.min(
        position.current,
        Number.isFinite(el.duration)
          ? Math.max(0, el.duration - 1)
          : position.current,
      );
      el.playbackRate = speed;
      void el.play().catch(() => undefined);
    };
    const playing = () => {
      if (ownerToken.current !== authToken()) return;
      if (!started) {
        started = true;
        report("start");
        heartbeat = window.setInterval(() => {
          if (!el.paused) report("progress");
        }, 15000);
      }
    };
    const ended = () => {
      if (ownerToken.current !== authToken()) return;
      if (!finished) {
        finished = true;
        report("complete");
        latest.current.onFinished();
      }
    };
    const time = () => {
      position.current = el.currentTime;
    };
    const renew = () => {
      if (ownerToken.current !== authToken()) return;
      if (!renewed.current) {
        renewed.current = true;
        setRetry((x) => x + 1);
      } else setError("The stream could not load. Please try again.");
    };
    el.addEventListener("loadedmetadata", metadata);
    el.addEventListener("playing", playing);
    el.addEventListener("ended", ended);
    el.addEventListener("timeupdate", time);
    setLevels([]);
    setQuality(-1);
    if (auth.url.includes(".m3u8") && Hls.isSupported()) {
      const instance = new Hls({ enableWorker: true });
      hls.current = instance;
      instance.loadSource(auth.url);
      instance.attachMedia(el);
      instance.on(Hls.Events.MANIFEST_PARSED, () =>
        setLevels(
          instance.levels.map((level, index) => ({
            index,
            label:
              (Math.min(level.width || 0, level.height || 0) || level.height) +
              "p",
          })),
        ),
      );
      instance.on(Hls.Events.ERROR, (_, data) => {
        if (data.fatal) {
          if (data.response?.code === 401 || data.response?.code === 403)
            renew();
          else setError("Playback could not continue. Please try again.");
        }
      });
    } else {
      el.src = auth.url;
      el.addEventListener("error", renew);
    }
    const flush = () => {
      if (started && !finished) report("progress");
    };
    window.addEventListener("pagehide", flush);
    return () => {
      flush();
      window.removeEventListener("pagehide", flush);
      clearInterval(heartbeat);
      el.removeEventListener("loadedmetadata", metadata);
      el.removeEventListener("playing", playing);
      el.removeEventListener("ended", ended);
      el.removeEventListener("timeupdate", time);
      el.removeEventListener("error", renew);
      hls.current?.destroy();
      hls.current = null;
      el.removeAttribute("src");
      el.load();
    };
  }, [auth?.url]);
  useEffect(() => {
    const controller = new AbortController();
    let dispose = () => {};
    setTracks([]);
    setSubtitleError("");
    void prepareSubtitleTracks(auth?.subtitles ?? [], controller.signal)
      .then((result) => {
        if (controller.signal.aborted) {
          result.dispose();
          return;
        }
        dispose = result.dispose;
        setTracks(result.tracks);
      })
      .catch(() => {
        if (!controller.signal.aborted)
          setSubtitleError(
            "Subtitles could not load. Playback is still available.",
          );
      });
    return () => {
      controller.abort();
      dispose();
    };
  }, [auth]);
  useEffect(() => {
    const el = video.current;
    if (!el) return;
    for (let i = 0; i < el.textTracks.length; i++) {
      const track = el.textTracks[i];
      track.mode =
        subtitle === "off"
          ? "disabled"
          : subtitle === "default"
            ? track.language ===
              auth?.subtitles?.find((s) => s.is_default)?.language
              ? "showing"
              : "disabled"
            : track.language === subtitle
              ? "showing"
              : "disabled";
    }
  }, [subtitle, tracks]);
  return (
    <>
      <video
        ref={video}
        muted={muted}
        poster={poster}
        controls
        playsInline
        preload="metadata"
        crossOrigin="anonymous"
        aria-label={"Episode " + episode.episode_number}
      >
        {tracks.map((track) => (
          <track
            key={track.language}
            kind="subtitles"
            src={track.url}
            srcLang={track.language}
            label={track.label}
            default={track.is_default}
          />
        ))}
      </video>
      {!auth && !error && <Loading />}
      {error && (
        <div className="video-error">
          <Failure
            message={error}
            retry={() => {
              renewed.current = false;
              setRetry((x) => x + 1);
            }}
          />
        </div>
      )}
      <div className="video-options">
        {subtitleError && (
          <span className="muted small" role="status">
            {subtitleError}
          </span>
        )}
        <label>
          Quality
          <select
            aria-label="Video quality"
            value={quality}
            onChange={(e) => {
              const next = Number(e.target.value);
              setQuality(next);
              if (hls.current) hls.current.currentLevel = next;
            }}
          >
            <option value={-1}>Auto</option>
            {levels.map((level) => (
              <option value={level.index} key={level.index}>
                {level.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Speed
          <select
            aria-label="Playback speed"
            value={speed}
            onChange={(e) => {
              const next = Number(e.target.value);
              setSpeed(next);
              if (video.current) video.current.playbackRate = next;
            }}
          >
            {[0.75, 1, 1.25, 1.5, 2].map((v) => (
              <option value={v} key={v}>
                {v}×
              </option>
            ))}
          </select>
        </label>
        {tracks.length > 0 && (
          <label>
            Subtitles
            <select
              aria-label="Subtitles"
              value={subtitle}
              onChange={(e) => setSubtitle(e.target.value)}
            >
              <option value="default">Default</option>
              <option value="off">Off</option>
              {tracks.map((track) => (
                <option value={track.language} key={track.language}>
                  {track.label}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
    </>
  );
}
export function Comments({ episodeId }: { episodeId: number }) {
  const { requireAuth, user } = useAuth();
  const identity = String(user?.id ?? "guest") + ":" + episodeId;
  const identityRef = useRef(identity);
  identityRef.current = identity;
  const previousUser = useRef(user?.id);
  const request = useRef(0),
    lifecycle = useRef(0);
  const [ownedData, setOwnedData] = useState<{
      identity: string;
      payload: CommentsPayload;
    } | null>(null),
    [draft, setDraft] = useState({ identity, body: "" }),
    [submitIntent, setSubmitIntent] = useState<string | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [notice, setNotice] = useState("");
  const data = ownedData?.identity === identity ? ownedData.payload : null;
  const body = draft.identity === identity ? draft.body : "";
  function load(cursor: number | null = null) {
    const current = ++request.current,
      owner = identityRef.current;
    get<CommentsPayload>(
      "/v1/episodes/" +
        episodeId +
        "/comments?limit=20" +
        (cursor ? "&cursor=" + cursor : ""),
    )
      .then((next) => {
        if (current !== request.current || owner !== identityRef.current)
          return;
        setOwnedData((previous) => ({
          identity: owner,
          payload:
            cursor && previous?.identity === owner
              ? { ...next, items: [...previous.payload.items, ...next.items] }
              : next,
        }));
      })
      .catch((e) => {
        if (current === request.current && owner === identityRef.current)
          setError(errorMessage(e));
      });
  }
  useEffect(() => {
    setError("");
    setNotice("");
    setBusy(false);
    const keepGuestDraft = previousUser.current == null;
    if (previousUser.current != null && previousUser.current !== user?.id)
      setSubmitIntent(null);
    setDraft((previous) => ({
      identity,
      body:
        keepGuestDraft && previous.identity === "guest:" + episodeId
          ? previous.body
          : "",
    }));
    previousUser.current = user?.id;
    load();
    return () => {
      request.current++;
      lifecycle.current++;
    };
  }, [episodeId, identity]);
  async function submit(text: string) {
    const owner = identityRef.current,
      generation = lifecycle.current;
    const current = () =>
      owner === identityRef.current && generation === lifecycle.current;
    setBusy(true);
    setError("");
    try {
      const result = await post<{ status: string }>(
        "/v1/episodes/" + episodeId + "/comments",
        { body: text },
      );
      if (!current()) return;
      setDraft({ identity: owner, body: "" });
      setNotice(
        result.status === "pending"
          ? "Your comment was submitted for review."
          : "Your comment was posted.",
      );
      load();
    } catch (e) {
      if (current()) setError(errorMessage(e));
    } finally {
      if (current()) setBusy(false);
    }
  }
  useEffect(() => {
    if (user && submitIntent != null) {
      setSubmitIntent(null);
      void submit(submitIntent);
    }
  }, [user?.id, submitIntent]);
  return (
    <section className="comments">
      <h2>Comments {data ? "(" + data.total + ")" : ""}</h2>
      <form
        className="form"
        onSubmit={(e) => {
          e.preventDefault();
          if (user) void submit(body);
          else requireAuth(() => setSubmitIntent(body));
        }}
      >
        <label className="sr-only" htmlFor="comment-body">
          Your comment
        </label>
        <textarea
          id="comment-body"
          required
          maxLength={1000}
          placeholder={
            user ? "Share your thoughts…" : "Sign in to join the conversation…"
          }
          rows={3}
          value={body}
          onChange={(e) => setDraft({ identity, body: e.target.value })}
        />
        <button className="button secondary" disabled={busy || !body.trim()}>
          {busy ? "Posting…" : "Post comment"}
        </button>
      </form>
      {notice && <p role="status">{notice}</p>}
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {data?.items.map((comment) => (
        <article className="comment" key={comment.id}>
          <strong>{comment.user.display_name}</strong>
          <p>{comment.body}</p>
          <time>
            {comment.created_at
              ? new Date(comment.created_at).toLocaleDateString()
              : ""}
          </time>
        </article>
      ))}
      {data?.next_cursor && (
        <button
          className="button secondary"
          onClick={() => load(data.next_cursor)}
        >
          More comments
        </button>
      )}
    </section>
  );
}

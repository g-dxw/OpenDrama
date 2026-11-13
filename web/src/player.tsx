import { useEffect, useRef, useState } from "react";
import Hls from "hls.js";
import {
  prepareSubtitleTracks,
  type SubtitleTrack,
} from "../../shared/consumer/subtitles";
import {
  get,
  post,
  errorMessage,
  ApiError,
  type PlayPayload,
  type PlayEpisode,
  type PlaybackAuth,
  type CommentsPayload,
} from "./consumer-api";
import { useAuth } from "./auth";
import { Empty, Failure, Icon, Loading, Modal } from "./components";
export function Watch({
  dramaId,
  episodeId,
}: {
  dramaId: number;
  episodeId?: number;
}) {
  const { status, user, requireAuth, refresh } = useAuth();
  const [data, setData] = useState<PlayPayload | null>(null),
    [selected, setSelected] = useState<number | undefined>(episodeId),
    [error, setError] = useState(""),
    [unlockOpen, setUnlockOpen] = useState(false),
    [busy, setBusy] = useState(false),
    [revision, setRevision] = useState(0),
    [tab, setTab] = useState("episodes"),
    [liked, setLiked] = useState(false),
    [favorited, setFavorited] = useState(false);
  const unlockKeys = useRef(new Map<number, string>());
  const loadRequest = useRef(0);
  const load = () => {
    const request = ++loadRequest.current;
    setError("");
    get<PlayPayload>("/v1/dramas/" + dramaId + "/play")
      .then((d) => {
        if (request !== loadRequest.current) return;
        setData(d);
        setSelected((old) =>
          old && d.items.some((e) => e.id === old)
            ? old
            : (d.resume?.episode_id ?? d.items[0]?.id),
        );
        setLiked(d.drama.liked);
        setFavorited(d.drama.favorited);
      })
      .catch((e) => {
        if (request === loadRequest.current) setError(errorMessage(e));
      });
  };
  useEffect(() => {
    setData(null);
    setSelected(episodeId);
  }, [dramaId, episodeId]);
  useEffect(() => {
    if (status !== "loading") load();
    return () => {
      loadRequest.current++;
    };
  }, [dramaId, status, revision]);
  const episode = data?.items.find((e) => e.id === selected);
  const isMembershipOnly = (item: PlayEpisode) =>
    Boolean(item.membership_required) ||
    (item.coin_price ??
      item.unlock?.coin_price ??
      data?.drama.coin_price_per_episode ??
      0) <= 0;
  const membershipOnly = episode ? isMembershipOnly(episode) : false;
  const next =
    data?.items[(data?.items.findIndex((e) => e.id === selected) ?? -1) + 1];
  function select(item: PlayEpisode) {
    setSelected(item.id);
    if (item.locked && !isMembershipOnly(item))
      requireAuth(() => setUnlockOpen(true));
  }
  async function unlock() {
    if (!episode || isMembershipOnly(episode)) return;
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
      await refresh();
      setUnlockOpen(false);
      setRevision((x) => x + 1);
    } catch (e) {
      setError(
        e instanceof ApiError && e.status === 402
          ? "Not enough coins. Top up your wallet to continue."
          : errorMessage(e),
      );
      await refresh();
    } finally {
      setBusy(false);
    }
  }
  async function react(kind: "like" | "favorite") {
    const previous = kind === "like" ? liked : favorited;
    setBusy(true);
    try {
      await post("/v1/reactions", {
        target_type: "drama",
        target_id: dramaId,
        kind,
        remove: previous,
      });
      if (kind === "like") setLiked(!previous);
      else setFavorited(!previous);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  if (!data)
    return error ? <Failure message={error} retry={load} /> : <Loading />;
  return (
    <div className="watch-page page-content">
      <a className="back-link" href="#browse">
        ← Back to dramas
      </a>
      <div className="watch-layout">
        <section className="watch-main">
          {episode ? (
            <div className="video-frame">
              {episode.locked ? (
                <div className="locked-player">
                  {data.drama.cover_url && (
                    <img src={data.drama.cover_url} alt="" />
                  )}
                  <div className="lock-copy">
                    <Icon name="lock" size={36} />
                    <h2>The story continues</h2>
                    {membershipOnly ? (
                      <>
                        <p>This episode requires a membership.</p>
                        <a
                          className="button primary"
                          href={
                            "#premium?drama=" +
                            dramaId +
                            "&episode=" +
                            episode.id
                          }
                        >
                          See memberships
                        </a>
                      </>
                    ) : (
                      <>
                        <p>
                          Unlock episode {episode.episode_number} for{" "}
                          {episode.coin_price ??
                            episode.unlock?.coin_price ??
                            data.drama.coin_price_per_episode}{" "}
                          coins.
                        </p>
                        <button
                          className="button primary"
                          onClick={() => requireAuth(() => setUnlockOpen(true))}
                        >
                          {user ? "Unlock episode" : "Sign in to continue"}
                        </button>
                      </>
                    )}
                    <a
                      className="button secondary"
                      href={
                        "#premium?drama=" +
                        dramaId +
                        "&episode=" +
                        (episode?.id || "")
                      }
                    >
                      Explore membership
                    </a>
                  </div>
                </div>
              ) : (
                <Video
                  key={episode.id + ":" + (user?.id ?? "guest")}
                  dramaId={dramaId}
                  episode={episode}
                  onDenied={() => setRevision((x) => x + 1)}
                  onFinished={() => {
                    if (next) setSelected(next.id);
                  }}
                />
              )}
            </div>
          ) : (
            <Empty message="No episodes are available yet." />
          )}
          <div className="watch-heading">
            <div>
              <span className="eyebrow">
                {episode ? "Episode " + episode.episode_number + " · " : ""}
                {data.items.length} episodes
              </span>
              <h1>{data.drama.title}</h1>
            </div>
            <div className="reaction-actions">
              <button
                className={"button secondary " + (liked ? "selected" : "")}
                disabled={busy}
                aria-pressed={liked}
                onClick={() =>
                  requireAuth(() => {
                    void react("like");
                  })
                }
              >
                <Icon name="heart" />
                Like
              </button>
              <button
                className={"button secondary " + (favorited ? "selected" : "")}
                disabled={busy}
                aria-pressed={favorited}
                onClick={() =>
                  requireAuth(() => {
                    void react("favorite");
                  })
                }
              >
                <Icon name="check" />
                My list
              </button>
            </div>
          </div>
          <div className="tags">
            {data.drama.tags.map((tag) => (
              <a href={"#browse?tag=" + encodeURIComponent(tag)} key={tag}>
                {tag}
              </a>
            ))}
          </div>
          <p className="synopsis">{data.drama.synopsis}</p>
          {error && <Failure message={error} />}{" "}
          {episode && <Comments key={episode.id} episodeId={episode.id} />}
        </section>
        <aside className="watch-sidebar">
          <div className="segmented">
            <button
              aria-pressed={tab === "episodes"}
              onClick={() => setTab("episodes")}
            >
              Episodes
            </button>
            <button
              aria-pressed={tab === "details"}
              onClick={() => setTab("details")}
            >
              About
            </button>
          </div>
          {tab === "episodes" ? (
            <>
              <div className="episode-legend">
                <span>Choose an episode</span>
                <span>
                  <Icon name="lock" size={13} />
                  Locked
                </span>
              </div>
              <div className="episode-grid">
                {data.items.map((item) => (
                  <button
                    key={item.id}
                    className={
                      "episode-button " + (item.id === selected ? "active" : "")
                    }
                    aria-label={
                      "Episode " +
                      item.episode_number +
                      (item.locked ? ", locked" : "")
                    }
                    aria-current={item.id === selected ? "true" : undefined}
                    onClick={() => select(item)}
                  >
                    {item.locked && <Icon name="lock" size={12} />}
                    <span>{item.episode_number}</span>
                    {item.completed && (
                      <span className="episode-done">
                        <Icon name="check" size={10} />
                      </span>
                    )}
                  </button>
                ))}
              </div>
              <p className="muted small">
                {data.entitlement.member
                  ? "Membership active"
                  : data.entitlement.unlocked_episode_ids.length +
                    " episodes unlocked"}
              </p>
            </>
          ) : (
            <div className="details-copy">
              <h2>{data.drama.title}</h2>
              <p>{data.drama.synopsis}</p>
              <p>{data.drama.play_count.toLocaleString()} views</p>
              <p>{data.drama.free_episode_count} free episodes</p>
              <p>Paid episode prices are displayed before you unlock.</p>
            </div>
          )}
          <div className="sidebar-upsell">
            <Icon name="crown" size={28} />
            <h3>One more episode?</h3>
            <p>Find your next membership or top up your coins.</p>
            <a
              className="button primary"
              href={
                "#premium?drama=" + dramaId + "&episode=" + (episode?.id || "")
              }
            >
              See memberships
            </a>
            <a
              className="text-button"
              href={
                "#coins?drama=" + dramaId + "&episode=" + (episode?.id || "")
              }
            >
              Get coins
            </a>
          </div>
        </aside>
      </div>
      {unlockOpen && episode && !membershipOnly && (
        <Modal
          title={"Unlock episode " + episode.episode_number}
          onClose={() => setUnlockOpen(false)}
        >
          <p>
            Spend{" "}
            <strong>
              {episode.unlock?.coin_price ?? data.drama.coin_price_per_episode}{" "}
              coins
            </strong>{" "}
            to unlock this episode.
          </p>
          <p className="muted">Your balance: {user?.coins ?? 0} coins</p>
          {error && (
            <p className="error-text" role="alert">
              {error}
            </p>
          )}
          {(user?.coins ?? 0) >=
          (episode.unlock?.coin_price ?? data.drama.coin_price_per_episode) ? (
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
              href={
                "#coins?drama=" + dramaId + "&episode=" + (episode?.id || "")
              }
              onClick={() => setUnlockOpen(false)}
            >
              Get more coins
            </a>
          )}
          <a
            className="text-button"
            href={
              "#premium?drama=" + dramaId + "&episode=" + (episode?.id || "")
            }
            onClick={() => setUnlockOpen(false)}
          >
            See membership options
          </a>
        </Modal>
      )}
    </div>
  );
}
function Video({
  dramaId,
  episode,
  onFinished,
  onDenied,
}: {
  dramaId: number;
  episode: PlayEpisode;
  onFinished: () => void;
  onDenied: () => void;
}) {
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
      try {
        const result = await post<PlaybackAuth>("/v1/playback", {
          drama_id: dramaId,
          episode_id: episode.id,
        });
        if (!alive) return;
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
      void post("/v1/playback/" + kind, {
        episode_id: episode.id,
        position_seconds: Math.floor(el.currentTime || 0),
      }).catch(() => undefined);
    };
    const metadata = () => {
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
      if (!started) {
        started = true;
        report("start");
        heartbeat = window.setInterval(() => {
          if (!el.paused) report("progress");
        }, 15000);
      }
    };
    const ended = () => {
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
function Comments({ episodeId }: { episodeId: number }) {
  const { requireAuth, user } = useAuth();
  const [data, setData] = useState<CommentsPayload | null>(null),
    [body, setBody] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [notice, setNotice] = useState("");
  function load(cursor: number | null = null) {
    get<CommentsPayload>(
      "/v1/episodes/" +
        episodeId +
        "/comments?limit=20" +
        (cursor ? "&cursor=" + cursor : ""),
    )
      .then((next) =>
        setData((previous) =>
          cursor && previous
            ? { ...next, items: [...previous.items, ...next.items] }
            : next,
        ),
      )
      .catch((e) => setError(errorMessage(e)));
  }
  useEffect(() => load(), [episodeId]);
  async function submit() {
    setBusy(true);
    setError("");
    try {
      const result = await post<{ status: string }>(
        "/v1/episodes/" + episodeId + "/comments",
        { body },
      );
      setBody("");
      setNotice(
        result.status === "pending"
          ? "Your comment was submitted for review."
          : "Your comment was posted.",
      );
      load();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="comments">
      <h2>Comments {data ? "(" + data.total + ")" : ""}</h2>
      <form
        className="form"
        onSubmit={(e) => {
          e.preventDefault();
          requireAuth(() => {
            void submit();
          });
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
          onChange={(e) => setBody(e.target.value)}
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

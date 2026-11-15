import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { authToken, postJson } from "../api";
import { t, type Locale } from "../copy";
import { prepareSubtitleTracks } from "../../../shared/consumer/subtitles";
import { IconPlayCenter } from "../icons";
import type { PlaybackAuth } from "../types";
import { attachHls, type PlayerHandle } from "./hls";
import {
  clampPosition,
  playbackRate,
  playbackTime,
  matchesResume,
  type ResumeBookmark,
} from "./playback-state";

interface Props {
  dramaId: number;
  episodeId: number;
  resumeSeconds?: number;
  active: boolean;
  playable: boolean;
  playbackSpeed?: number;
  quality?: string;
  locale?: Locale;
  controlsVisible?: boolean;
  suspended?: boolean;
  onInteraction?: () => void;
  onVideoTap?: () => boolean;
  onPlayingChange?: (playing: boolean) => void;
  onProgress?: (episodeId: number, position: number) => void;
  /** Feed cards keep a small read-only indicator; the series page owns seeking. */
  preview?: boolean;
  onQualitiesChange?: (qualities: string[]) => void;
  onFinished?: () => void;
}

/** A source belongs to one identity and episode. Preferences never re-authorize it. */
export function EpisodeView({
  dramaId,
  episodeId,
  resumeSeconds = 0,
  active,
  playable,
  playbackSpeed = 1,
  quality = "Auto",
  locale = "en",
  controlsVisible = true,
  suspended = false,
  onInteraction,
  onVideoTap,
  onPlayingChange,
  onProgress,
  preview = false,
  onQualitiesChange,
  onFinished,
}: Props) {
  const owner = authToken();
  const identity = String(dramaId) + ":" + episodeId + ":" + (owner || "guest");
  const videoRef = useRef<HTMLVideoElement>(null);
  const handleRef = useRef<PlayerHandle | null>(null);
  const latest = useRef({
    identity,
    owner,
    active,
    playable,
    suspended,
    playbackSpeed,
    quality,
    onInteraction,
    onVideoTap,
    onPlayingChange,
    onProgress,
    onQualitiesChange,
    onFinished,
  });
  latest.current = {
    identity,
    owner,
    active,
    playable,
    suspended,
    playbackSpeed,
    quality,
    onInteraction,
    onVideoTap,
    onPlayingChange,
    onProgress,
    onQualitiesChange,
    onFinished,
  };
  const positionRef = useRef({
    identity,
    position: clampPosition(resumeSeconds),
  });
  const epoch = useRef(0);
  const drag = useRef<{ playing: boolean } | null>(null);
  const resumeAfterSheet = useRef<ResumeBookmark | null>(null);
  const wasSuspended = useRef(suspended);
  const [revision, setRevision] = useState(0);
  const [paused, setPaused] = useState(true);
  const [phase, setPhase] = useState<"loading" | "ready" | "error">("loading");
  const [buffering, setBuffering] = useState(false);
  const [seeking, setSeeking] = useState(false);
  const [position, setPosition] = useState(0);
  const [duration, setDuration] = useState(0);
  const [subtitleChoice, setSubtitleChoice] = useState("default");
  const [subtitles, setSubtitles] = useState<
    NonNullable<PlaybackAuth["subtitles"]>
  >([]);
  const canControl = () =>
    latest.current.active &&
    latest.current.playable &&
    authToken() === latest.current.owner;
  const interact = () => latest.current.onInteraction?.();
  const requestPlay = () => {
    const video = videoRef.current;
    if (
      !video ||
      !canControl() ||
      latest.current.suspended ||
      !video.currentSrc
    )
      return;
    video.play().catch(() => {
      if (canControl()) {
        setPaused(true);
        latest.current.onPlayingChange?.(false);
      }
    });
  };

  // Remove the old media before paint on identity, permission or active-slot changes.
  useLayoutEffect(() => {
    const video = videoRef.current;
    return () => {
      if (!video) return;
      if (
        authToken() === owner &&
        positionRef.current.identity === identity &&
        video.readyState >= 1 &&
        Number.isFinite(video.currentTime)
      ) {
        positionRef.current.position = clampPosition(video.currentTime);
      }
      video.pause();
      video.removeAttribute("src");
      video.load();
    };
  }, [identity, active, playable]);

  useEffect(() => {
    const video = videoRef.current;
    const attempt = ++epoch.current;
    resumeAfterSheet.current = null;
    if (!video || !active || !playable) {
      setSubtitles([]);
      setPaused(true);
      setBuffering(false);
      latest.current.onPlayingChange?.(false);
      return;
    }
    if (positionRef.current.identity !== identity) {
      positionRef.current = {
        identity,
        position: clampPosition(resumeSeconds),
      };
    }
    const resume = positionRef.current.position;
    const cleanupProgress = latest.current.onProgress;
    let disposed = false,
      failed = false,
      finished = false,
      started = false,
      attached = false;
    let handle: PlayerHandle | null = null;
    let heartbeat: ReturnType<typeof setInterval> | undefined;
    let loadingTimer: ReturnType<typeof setTimeout> | undefined;
    let lastNotified = -1;
    let metadataSeen = false;
    const subtitleController = new AbortController();
    let disposeSubtitles = () => {};
    const owns = () =>
      !disposed &&
      epoch.current === attempt &&
      authToken() === owner &&
      latest.current.identity === identity &&
      latest.current.active &&
      latest.current.playable;
    const currentPosition = () =>
      attached && video.readyState >= 1
        ? clampPosition(video.currentTime, video.duration)
        : positionRef.current.position;
    const notify = (force = false) => {
      if (!owns()) return;
      const value = currentPosition();
      positionRef.current = { identity, position: value };
      setPosition(value);
      const whole = Math.floor(value);
      if (force || whole !== lastNotified) {
        lastNotified = whole;
        latest.current.onProgress?.(episodeId, value);
      }
    };
    const report = (
      kind: "start" | "progress" | "complete",
      value: number,
      cleanup = false,
    ) => {
      if (
        authToken() !== owner ||
        (!cleanup && latest.current.identity !== identity)
      )
        return;
      postJson("/v1/playback/" + kind, {
        episode_id: episodeId,
        position_seconds: Math.floor(clampPosition(value)),
      }).catch(() => undefined);
    };
    const flush = () => {
      if (!owns() || !attached || finished) return;
      notify(true);
      report("progress", positionRef.current.position);
    };
    const clearLoading = () => {
      if (loadingTimer) clearTimeout(loadingTimer);
      loadingTimer = undefined;
      if (owns()) setBuffering(false);
    };
    const delayLoading = () => {
      if (loadingTimer || !owns()) return;
      loadingTimer = setTimeout(() => {
        loadingTimer = undefined;
        if (owns() && !failed && !video.ended) setBuffering(true);
      }, 350);
    };
    const fail = () => {
      if (!owns() || failed) return;
      notify(true);
      failed = true;
      clearLoading();
      if (heartbeat) clearInterval(heartbeat);
      setPhase("error");
      setPaused(true);
      latest.current.onPlayingChange?.(false);
      handle?.destroy();
      if (handleRef.current === handle) handleRef.current = null;
      video.pause();
      video.removeAttribute("src");
      video.load();
    };
    const onTime = () => {
      if (!failed) notify();
    };
    const onPlay = () => {
      if (!owns() || failed) return;
      if (latest.current.suspended) {
        video.pause();
        return;
      }
      setPaused(false);
    };
    const onPlaying = () => {
      if (!owns() || failed) return;
      clearLoading();
      setPhase("ready");
      setPaused(false);
      latest.current.onPlayingChange?.(true);
      if (!started) {
        started = true;
        report("start", currentPosition());
        heartbeat = setInterval(() => {
          if (owns() && !video.paused && !video.ended && !failed) flush();
        }, 15000);
      }
    };
    const onPause = () => {
      if (!owns()) return;
      clearLoading();
      setPaused(true);
      latest.current.onPlayingChange?.(false);
      if (!failed) flush();
    };
    const onWaiting = () => {
      if (!owns() || failed || video.paused || video.ended) return;
      latest.current.onPlayingChange?.(false);
      delayLoading();
    };
    const onCanPlay = () => {
      if (owns() && !failed) {
        clearLoading();
        setPhase("ready");
      }
    };
    const onSeeking = () => {
      if (owns() && !failed) {
        setSeeking(true);
        delayLoading();
      }
    };
    const onSeeked = () => {
      if (!owns() || failed) return;
      clearLoading();
      if (!drag.current) setSeeking(false);
      notify(true);
      flush();
    };
    const onEnded = () => {
      if (!owns() || failed || finished) return;
      finished = true;
      clearLoading();
      if (heartbeat) clearInterval(heartbeat);
      notify(true);
      setPaused(true);
      latest.current.onPlayingChange?.(false);
      report(
        "complete",
        Number.isFinite(video.duration) ? video.duration : currentPosition(),
      );
      latest.current.onFinished?.();
    };
    const onDuration = () => {
      if (owns())
        setDuration(
          Number.isFinite(video.duration) ? Math.max(0, video.duration) : 0,
        );
    };
    const onMetadata = () => {
      if (!owns() || failed) return;
      onDuration();
      const firstMetadata = !metadataSeen;
      metadataSeen = true;
      const target = clampPosition(
        firstMetadata ? resume : positionRef.current.position,
        video.duration,
      );
      try {
        video.currentTime = target;
      } catch {
        /* A live stream may not expose a seekable range yet. */
      }
      positionRef.current = { identity, position: target };
      setPosition(target);
      video.playbackRate = playbackRate(latest.current.playbackSpeed);
      setPhase("ready");
      if (!firstMetadata) return;
      if (!latest.current.suspended) requestPlay();
      else {
        setPaused(true);
        clearLoading();
      }
    };
    const onNativeError = () => {
      if (attached && !handle?.handlesMediaErrors) fail();
    };
    const onVisibility = () => {
      if (document.visibilityState === "hidden") flush();
    };
    const listeners: [string, EventListener][] = [
      ["loadedmetadata", onMetadata],
      ["durationchange", onDuration],
      ["timeupdate", onTime],
      ["play", onPlay],
      ["playing", onPlaying],
      ["pause", onPause],
      ["waiting", onWaiting],
      ["stalled", onWaiting],
      ["canplay", onCanPlay],
      ["seeking", onSeeking],
      ["seeked", onSeeked],
      ["ended", onEnded],
      ["error", onNativeError],
    ];
    listeners.forEach(([name, listener]) =>
      video.addEventListener(name, listener),
    );
    window.addEventListener("pagehide", flush);
    document.addEventListener("visibilitychange", onVisibility);
    setPhase("loading");
    setPaused(true);
    setBuffering(false);
    setSeeking(false);
    setPosition(resume);
    setDuration(0);
    setSubtitles([]);
    setSubtitleChoice("default");
    drag.current = null;
    latest.current.onPlayingChange?.(false);
    delayLoading();
    (async () => {
      try {
        const auth = await postJson<PlaybackAuth>("/v1/playback", {
          drama_id: dramaId,
          episode_id: episodeId,
        });
        if (!owns()) return;
        if (!auth.allowed || !auth.url) {
          fail();
          return;
        }
        void prepareSubtitleTracks(
          auth.subtitles ?? [],
          subtitleController.signal,
        )
          .then((result) => {
            if (!owns()) {
              result.dispose();
              return;
            }
            disposeSubtitles = result.dispose;
            setSubtitles(result.tracks);
          })
          .catch(() => undefined);
        attached = true;
        handle = attachHls(
          video,
          auth.url,
          (levels) => {
            if (owns()) latest.current.onQualitiesChange?.(levels);
          },
          fail,
        );
        if (!owns()) {
          handle.destroy();
          return;
        }
        handleRef.current = handle;
        handle.setQuality(latest.current.quality);
      } catch {
        fail();
      }
    })();
    return () => {
      // Layout cleanup may already have removed src; its captured position survives.
      if (authToken() === owner && attached && !finished) {
        const value =
          video.readyState >= 1
            ? clampPosition(video.currentTime, video.duration)
            : positionRef.current.position;
        positionRef.current = { identity, position: value };
        cleanupProgress?.(episodeId, value);
        report("progress", value, true);
      }
      disposed = true;
      if (loadingTimer) clearTimeout(loadingTimer);
      if (heartbeat) clearInterval(heartbeat);
      listeners.forEach(([name, listener]) =>
        video.removeEventListener(name, listener),
      );
      window.removeEventListener("pagehide", flush);
      document.removeEventListener("visibilitychange", onVisibility);
      subtitleController.abort();
      disposeSubtitles();
      handle?.destroy();
      if (handleRef.current === handle) handleRef.current = null;
      video.pause();
      video.removeAttribute("src");
      video.load();
      latest.current.onPlayingChange?.(false);
    };
  }, [active, playable, dramaId, episodeId, owner, identity, revision]);

  useEffect(() => {
    const video = videoRef.current;
    if (video) video.playbackRate = playbackRate(playbackSpeed);
  }, [playbackSpeed]);
  useEffect(() => {
    handleRef.current?.setQuality(quality);
  }, [quality]);
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    if (suspended && !wasSuspended.current) {
      resumeAfterSheet.current =
        !video.paused && !video.ended
          ? { identity, attempt: epoch.current }
          : null;
      video.pause();
    } else if (!suspended && wasSuspended.current) {
      if (matchesResume(resumeAfterSheet.current, identity, epoch.current))
        requestPlay();
      resumeAfterSheet.current = null;
    }
    wasSuspended.current = suspended;
  }, [suspended, active, playable, identity]);
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    for (let i = 0; i < video.textTracks.length; i++) {
      const track = video.textTracks[i];
      track.mode =
        subtitleChoice === track.language ||
        (subtitleChoice === "default" &&
          subtitles.some(
            (item) => item.language === track.language && item.is_default,
          ))
          ? "showing"
          : "disabled";
    }
  }, [subtitles, subtitleChoice]);

  const toggle = () => {
    interact();
    const video = videoRef.current;
    if (!video || !canControl() || suspended || phase === "error") return;
    if (video.paused) requestPlay();
    else video.pause();
  };
  const onTap = () => {
    if (latest.current.onVideoTap?.()) {
      interact();
      return;
    }
    toggle();
  };
  const retry = () => {
    interact();
    if (!canControl()) return;
    setRevision((value) => value + 1);
  };
  const seekTo = (value: number) => {
    interact();
    const video = videoRef.current;
    if (
      !video ||
      !canControl() ||
      phase === "error" ||
      !Number.isFinite(video.duration) ||
      video.duration <= 0
    )
      return;
    const next = clampPosition(value, video.duration);
    try {
      video.currentTime = next;
      positionRef.current = { identity, position: next };
      setPosition(next);
      latest.current.onProgress?.(episodeId, next);
    } catch {
      /* Not seekable yet; keep the current frame. */
    }
  };
  const endSeek = () => {
    const previous = drag.current;
    drag.current = null;
    setSeeking(false);
    if (previous?.playing && !latest.current.suspended) requestPlay();
  };
  const showControls =
    active &&
    playable &&
    (controlsVisible || paused || buffering || seeking || phase === "error");
  const safeDuration = Number.isFinite(duration) && duration > 0 ? duration : 0;
  const safePosition = clampPosition(position, safeDuration || undefined);

  return (
    <>
      <video
        key={identity}
        ref={videoRef}
        playsInline
        crossOrigin="anonymous"
        data-player-control="video"
        tabIndex={active ? 0 : -1}
        role="button"
        aria-label={t(locale, "player.video")}
        aria-pressed={!paused}
        onClick={onTap}
        onKeyDown={(event) => {
          if (event.key === " " || event.key === "Enter") {
            event.preventDefault();
            onTap();
          } else if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
            event.preventDefault();
            seekTo(safePosition + (event.key === "ArrowLeft" ? -5 : 5));
          }
        }}
      >
        {subtitles.map((track) => (
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
      {!preview && <div className="player-video-scrim" data-expanded={showControls} aria-hidden="true" />}
      {active && (phase === "error" || buffering) && (
        <div
          className="player-feedback"
          role={phase === "error" ? "alert" : "status"}
        >
          <span>
            {t(
              locale,
              phase === "error"
                ? "player.error"
                : phase === "loading"
                  ? "player.loading"
                  : "player.buffering",
            )}
          </span>
          {phase === "error" && (
            <button
              className="player-retry"
              type="button"
              onClick={retry}
              data-player-control="retry"
            >
              {t(locale, "player.retry")}
            </button>
          )}
        </div>
      )}
      {showControls && phase === "ready" && (
        <button
          type="button"
          className="player-center-toggle"
          data-player-control="toggle"
          aria-label={t(locale, paused ? "player.play" : "player.pause")}
          onClick={toggle}
        >
          {paused ? (
            <IconPlayCenter size={34} />
          ) : (
            <svg width="34" height="34" viewBox="0 0 24 24" aria-hidden="true">
              <path d="M8 5v14M16 5v14" stroke="currentColor" strokeWidth="4" />
            </svg>
          )}
        </button>
      )}
      {showControls && subtitles.length > 0 && (
        <label className="subtitle-picker" data-player-control="subtitles">
          {t(locale, "player.subtitles")}
          <select
            aria-label={t(locale, "player.subtitles")}
            value={subtitleChoice}
            onChange={(event) => {
              interact();
              setSubtitleChoice(event.target.value);
            }}
            onPointerDown={interact}
          >
            <option value="default">
              {t(locale, "player.subtitleDefault")}
            </option>
            <option value="off">{t(locale, "player.subtitleOff")}</option>
            {subtitles.map((track) => (
              <option value={track.language} key={track.language}>
                {track.label}
              </option>
            ))}
          </select>
        </label>
      )}
      {active && playable && (preview ? <div className="progress" aria-hidden="true">
        <b style={{ width: (safeDuration ? safePosition / safeDuration * 100 : 0) + "%" }} />
      </div> : (
        <div
          className="player-progress"
          data-player-control="progress"
          data-expanded={showControls}
          data-seeking={seeking}
          aria-busy={phase === "loading" || buffering || seeking}
        >
          <div className="player-progress-times">
            <span>{playbackTime(safePosition)}</span>
            <span>{playbackTime(safeDuration)}</span>
          </div>
          <input
            className="player-progress-range"
            style={{ backgroundSize: ((safeDuration ? safePosition / safeDuration : 0) * 100) + "% 3px" }}
            data-player-control="seek"
            type="range"
            min={0}
            max={safeDuration || 1}
            step={0.1}
            value={Math.min(safePosition, safeDuration || 0)}
            disabled={!safeDuration || phase !== "ready"}
            aria-label={t(locale, "player.seek")}
            aria-valuetext={
              playbackTime(safePosition) + " / " + playbackTime(safeDuration)
            }
            onPointerDown={(event) => {
              interact();
              const video = videoRef.current;
              drag.current = { playing: !!video && !video.paused };
              setSeeking(true);
              video?.pause();
              event.currentTarget.setPointerCapture?.(event.pointerId);
            }}
            onChange={(event) => seekTo(Number(event.target.value))}
            onPointerUp={endSeek}
            onPointerCancel={endSeek}
            onBlur={endSeek}
            onKeyDown={(event) => {
              if (
                [
                  "ArrowLeft",
                  "ArrowRight",
                  "ArrowUp",
                  "ArrowDown",
                  "Home",
                  "End",
                  "PageUp",
                  "PageDown",
                ].includes(event.key)
              ) {
                interact();
                setSeeking(true);
              }
            }}
            onKeyUp={() => setSeeking(false)}
          />
        </div>
      ))}
    </>
  );
}

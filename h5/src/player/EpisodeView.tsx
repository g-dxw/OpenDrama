import { useEffect, useRef, useState } from "react";
import { authToken, postJson } from "../api";
import { prepareSubtitleTracks } from "../../../shared/consumer/subtitles";
import { IconPlayCenter } from "../icons";
import type { PlaybackAuth } from "../types";
import { attachHls, type PlayerHandle } from "./hls";

interface Props {
  dramaId: number;
  episodeId: number;
  resumeSeconds?: number;
  active: boolean;
  /** When false the view never asks for a URL (locked episodes). */
  playable: boolean;
  playbackSpeed?: number;
  quality?: string;
  onQualitiesChange?: (qualities: string[]) => void;
  onFinished?: () => void;
}

/**
 * Mints a signed URL only while this slot is the one on screen, then reports
 * start / 15s progress / complete. Analytics failures never stop playback.
 * Tokens live five minutes, so a URL fetched with the list would already be dead.
 */
export function EpisodeView({
  dramaId,
  episodeId,
  resumeSeconds = 0,
  active,
  playable,
  playbackSpeed = 1.0,
  quality = "Auto",
  onQualitiesChange,
  onFinished,
}: Props) {
  const ownerToken = authToken();
  const videoRef = useRef<HTMLVideoElement>(null);
  const handleRef = useRef<PlayerHandle | null>(null);
  const [paused, setPaused] = useState(false);
  const [progress, setProgress] = useState(0);
  const [failed, setFailed] = useState(false);
  const [subtitles, setSubtitles] = useState<
    NonNullable<PlaybackAuth["subtitles"]>
  >([]);
  const onFinishedRef = useRef(onFinished);
  onFinishedRef.current = onFinished;
  const onQualitiesChangeRef = useRef(onQualitiesChange);
  onQualitiesChangeRef.current = onQualitiesChange;

  useEffect(() => {
    if (!active || !playable) return;
    const video = videoRef.current;
    if (!video) return;
    let handle: PlayerHandle | null = null;
    let heartbeat = 0;
    let cancelled = false;
    let finished = false;
    let removeMetadataListener = () => {};
    const ownsPlayback = () => !cancelled && authToken() === ownerToken;
    const subtitleController = new AbortController();
    let disposeSubtitles = () => {};

    const report = (
      kind: "start" | "progress" | "complete",
      position: number,
    ) => {
      if (!ownsPlayback()) return;
      postJson(`/v1/playback/${kind}`, {
        episode_id: episodeId,
        position_seconds: position,
      }).catch(() => undefined);
    };

    const finish = () => {
      if (finished || !ownsPlayback()) return;
      finished = true;
      window.clearInterval(heartbeat);
      report("complete", Math.floor(video.duration || 0));
      onFinishedRef.current?.();
    };

    const onTime = () => {
      if (!ownsPlayback() || !video.duration) return;
      setProgress(video.currentTime / video.duration);
      if (video.currentTime >= video.duration - 1.5) finish();
    };

    (async () => {
      try {
        const auth = await postJson<PlaybackAuth>("/v1/playback", {
          drama_id: dramaId,
          episode_id: episodeId,
        });
        if (!ownsPlayback() || !auth.allowed || !auth.url) {
          if (ownsPlayback() && auth.allowed === false) setFailed(true);
          return;
        }
        void prepareSubtitleTracks(
          auth.subtitles ?? [],
          subtitleController.signal,
        )
          .then((result) => {
            if (!ownsPlayback()) {
              result.dispose();
              return;
            }
            setSubtitles(result.tracks);
            disposeSubtitles = result.dispose;
          })
          .catch(() => undefined);
        handle = attachHls(video, auth.url, (levels) => {
          if (ownsPlayback()) onQualitiesChangeRef.current?.(levels);
        });
        handleRef.current = handle;
        if (quality) {
          handle.setQuality(quality);
        }
        const start = () => {
          if (!ownsPlayback()) return;
          if (resumeSeconds > 0) video.currentTime = resumeSeconds;
          video.playbackRate = playbackSpeed;
          video.play().catch(() => {
            if (ownsPlayback()) setPaused(true);
          });
          report("start", resumeSeconds);
          heartbeat = window.setInterval(
            () => report("progress", Math.floor(video.currentTime || 0)),
            15000,
          );
        };
        if (video.readyState >= 1) start();
        else {
          video.addEventListener("loadedmetadata", start, { once: true });
          removeMetadataListener = () =>
            video.removeEventListener("loadedmetadata", start);
        }
        video.addEventListener("timeupdate", onTime);
        video.addEventListener("ended", finish);
      } catch {
        if (ownsPlayback()) setFailed(true);
      }
    })();

    return () => {
      cancelled = true;
      subtitleController.abort();
      disposeSubtitles();
      setSubtitles([]);
      window.clearInterval(heartbeat);
      removeMetadataListener();
      video.pause();
      video.removeAttribute("src");
      video.load();
      video.removeEventListener("timeupdate", onTime);
      video.removeEventListener("ended", finish);
      handle?.destroy();
      handleRef.current = null;
    };
  }, [active, playable, dramaId, episodeId, resumeSeconds, ownerToken]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    video.playbackRate = playbackSpeed;
  }, [playbackSpeed]);

  useEffect(() => {
    if (handleRef.current && quality) {
      handleRef.current.setQuality(quality);
    }
  }, [quality]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    if (!active) {
      video.pause();
      return;
    }
    if (!paused) {
      video.playbackRate = playbackSpeed;
      video.play().catch(() => undefined);
    }
  }, [active, paused, playbackSpeed]);

  const toggle = () => {
    const video = videoRef.current;
    if (!video || !playable) return;
    if (video.paused) {
      setPaused(false);
      video.play().catch(() => undefined);
    } else {
      setPaused(true);
      video.pause();
    }
  };

  return (
    <>
      <video
        ref={videoRef}
        playsInline
        crossOrigin="anonymous"
        onClick={toggle}
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
      {subtitles.length > 0 && (
        <label className="subtitle-picker">
          Subtitles
          <select
            aria-label="Subtitles"
            defaultValue="default"
            onChange={(event) => {
              const video = videoRef.current;
              if (!video) return;
              for (let i = 0; i < video.textTracks.length; i++) {
                const track = video.textTracks[i];
                track.mode =
                  event.target.value === track.language ||
                  (event.target.value === "default" &&
                    subtitles.some(
                      (s) => s.language === track.language && s.is_default,
                    ))
                    ? "showing"
                    : "disabled";
              }
            }}
          >
            <option value="default">Default</option>
            <option value="off">Off</option>
            {subtitles.map((track) => (
              <option value={track.language} key={track.language}>
                {track.label}
              </option>
            ))}
          </select>
        </label>
      )}
      {failed && <div className="lock">Could not play</div>}
      {paused && playable && (
        <button
          type="button"
          className="play-pause-overlay"
          aria-label="Play"
          onClick={toggle}
        >
          <span className="play-pause-badge">
            <IconPlayCenter size={34} />
          </span>
        </button>
      )}
      <div className="progress">
        <b style={{ width: `${progress * 100}%` }} />
      </div>
    </>
  );
}

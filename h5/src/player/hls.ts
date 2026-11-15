import Hls from "hls.js";
import { qualityLevel, qualityOptions } from "./playback-state";

export interface PlayerHandle {
  video: HTMLVideoElement;
  handlesMediaErrors: boolean;
  destroy: () => void;
  setQuality: (quality: string) => void;
}

/** The view owns authorization; recovery here never reuses a different user's URL. */
export function attachHls(
  video: HTMLVideoElement,
  url: string,
  onLevels?: (levels: string[]) => void,
  onFatal?: () => void,
): PlayerHandle {
  let hls: Hls | null = null;
  let destroyed = false;
  let terminal = false;
  let requestedQuality = "Auto";
  let networkRecoveries = 0;
  let mediaRecoveries = 0;
  let recoveryTimer: ReturnType<typeof setTimeout> | undefined;
  const fail = () => {
    if (destroyed || terminal) return;
    terminal = true;
    hls?.stopLoad();
    if (recoveryTimer) clearTimeout(recoveryTimer);
    recoveryTimer = undefined;
    onFatal?.();
  };
  const applyQuality = () => {
    if (hls?.levels.length)
      hls.currentLevel = qualityLevel(requestedQuality, hls.levels);
  };
  const isHlsSource = /\.m3u8(?:$|[?#])/i.test(url);
  if (isHlsSource && Hls.isSupported()) {
    onLevels?.(["Auto"]);
    hls = new Hls({ enableWorker: true, lowLatencyMode: false });
    hls.on(Hls.Events.MANIFEST_PARSED, (_, data) => {
      if (destroyed) return;
      onLevels?.(qualityOptions(data.levels || []));
      applyQuality();
    });
    hls.on(Hls.Events.ERROR, (_, data) => {
      if (destroyed || terminal || !data.fatal) return;
      if (data.type === Hls.ErrorTypes.NETWORK_ERROR && networkRecoveries < 2) {
        networkRecoveries++;
        if (recoveryTimer) clearTimeout(recoveryTimer);
        recoveryTimer = setTimeout(() => {
          recoveryTimer = undefined;
          if (!destroyed && !terminal) {
            try {
              hls?.startLoad();
            } catch {
              fail();
            }
          }
        }, networkRecoveries * 400);
      } else if (
        data.type === Hls.ErrorTypes.MEDIA_ERROR &&
        mediaRecoveries < 1
      ) {
        mediaRecoveries++;
        try {
          hls?.recoverMediaError();
        } catch {
          fail();
        }
      } else fail();
    });
    hls.loadSource(url);
    hls.attachMedia(video);
  } else {
    // Native HLS retains browser-managed ABR; a fixed file has one original source.
    onLevels?.(isHlsSource ? ["Auto"] : ["Original"]);
    video.src = url;
  }
  return {
    video,
    handlesMediaErrors: !!hls,
    setQuality(quality) {
      requestedQuality = quality;
      applyQuality();
    },
    destroy() {
      if (destroyed) return;
      destroyed = true;
      if (recoveryTimer) clearTimeout(recoveryTimer);
      hls?.destroy();
      hls = null;
      video.pause();
      video.removeAttribute("src");
      video.load();
    },
  };
}

import { useEffect, useRef, useState, type RefObject } from "react";
import { createPortal } from "react-dom";
import { Icon, Modal } from "./components";
import { useLocale } from "./locale";
import type { SubtitleTrack } from "../../shared/consumer/subtitles";

function clock(seconds: number) {
  const value = Number.isFinite(seconds) ? Math.max(0, Math.floor(seconds)) : 0;
  const minutes = Math.floor(value / 60);
  return (minutes >= 60 ? Math.floor(minutes / 60) + ":" : "") +
    String(minutes % 60).padStart(2, "0") + ":" + String(value % 60).padStart(2, "0");
}

type Props = {
  video: RefObject<HTMLVideoElement>;
  container: RefObject<HTMLDivElement>;
  enabled: boolean;
  canInteract: () => boolean;
  speed: number;
  onSpeed: (value: number) => void;
  levels: Array<{ index: number; label: string }>;
  quality: number;
  onQuality: (value: number) => void;
  tracks: SubtitleTrack[];
  subtitle: string;
  onSubtitle: (value: string) => void;
  subtitleError: string;
  settingsOpen?: boolean;
  onSettingsOpenChange?: (value: boolean) => void;
  onMutedChange?: (value: boolean) => void;
};

export default function DesktopVideoControls(props: Props) {
  const { t, number } = useLocale();
  const { video, container, enabled } = props;
  const [paused, setPaused] = useState(true), [waiting, setWaiting] = useState(false);
  const [elapsed, setElapsed] = useState(0), [duration, setDuration] = useState(0), [buffered, setBuffered] = useState(0);
  const [muted, setMuted] = useState(false), [volume, setVolume] = useState(1);
  const [fullscreen, setFullscreen] = useState(false), [shown, setShown] = useState(true);
  const [internalSettings, setInternalSettings] = useState(false), [notice, setNotice] = useState("");
  const lastVolume = useRef(1);
  const callbacks = useRef(props); callbacks.current = props;
  const settingsOpen = props.settingsOpen ?? internalSettings;
  const setSettingsOpen = (value: boolean) => {
    if (props.onSettingsOpenChange) props.onSettingsOpenChange(value);
    else setInternalSettings(value);
  };
  const allowed = () => enabled && callbacks.current.canInteract();

  useEffect(() => {
    const el = video.current;
    if (!el) return;
    const sync = () => {
      setPaused(el.paused || el.ended);
      setElapsed(Number.isFinite(el.currentTime) ? el.currentTime : 0);
      setDuration(Number.isFinite(el.duration) ? el.duration : 0);
      setVolume(el.volume); setMuted(el.muted || el.volume === 0);
      let end = 0;
      for (let index = 0; index < el.buffered.length; index++) {
        if (el.buffered.start(index) <= el.currentTime + 0.5) end = el.buffered.end(index);
      }
      setBuffered(end);
    };
    const volumeChange = () => {
      if (el.volume > 0) lastVolume.current = el.volume;
      sync(); callbacks.current.onMutedChange?.(el.muted || el.volume === 0);
    };
    const buffering = () => setWaiting(true);
    const ready = () => { setWaiting(false); sync(); };
    const empty = () => { setWaiting(false); setNotice(""); sync(); };
    const events = ["loadedmetadata", "durationchange", "timeupdate", "progress", "play", "pause", "ended"];
    for (const event of events) el.addEventListener(event, sync);
    el.addEventListener("volumechange", volumeChange);
    el.addEventListener("waiting", buffering);
    el.addEventListener("playing", ready); el.addEventListener("canplay", ready);
    el.addEventListener("emptied", empty);
    sync();
    return () => {
      for (const event of events) el.removeEventListener(event, sync);
      el.removeEventListener("volumechange", volumeChange);
      el.removeEventListener("waiting", buffering);
      el.removeEventListener("playing", ready); el.removeEventListener("canplay", ready);
      el.removeEventListener("emptied", empty);
    };
  }, [video]);

  useEffect(() => {
    const changed = () => setFullscreen(Boolean(video.current && document.fullscreenElement?.contains(video.current)));
    document.addEventListener("fullscreenchange", changed); changed();
    return () => document.removeEventListener("fullscreenchange", changed);
  }, [video]);

  async function togglePlay() {
    const el = video.current;
    if (!el || !allowed()) return;
    setNotice("");
    if (!el.paused) el.pause();
    else try { await el.play(); } catch (error) {
      if (callbacks.current.canInteract() && !(error instanceof DOMException && error.name === "AbortError")) setNotice("player.play_failed");
    }
  }
  function seek(value: number) {
    const el = video.current;
    if (!el || !allowed() || !Number.isFinite(el.duration) || el.duration <= 0) return;
    el.currentTime = Math.min(el.duration, Math.max(0, value));
    setElapsed(el.currentTime);
  }
  function toggleMuted() {
    const el = video.current;
    if (!el || !allowed()) return;
    const next = !(el.muted || el.volume === 0);
    if (!next && el.volume === 0) el.volume = lastVolume.current || 1;
    el.muted = next; callbacks.current.onMutedChange?.(next);
  }
  function changeVolume(value: number) {
    const el = video.current;
    if (!el || !allowed()) return;
    el.volume = value; el.muted = value === 0;
    callbacks.current.onMutedChange?.(el.muted);
  }
  async function toggleFullscreen() {
    if (!allowed()) return;
    const target = container.current?.closest<HTMLElement>(".video-frame") ?? container.current;
    if (!target) return;
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else if (target.requestFullscreen) await target.requestFullscreen();
      else setNotice("player.fullscreen_unavailable");
    } catch { setNotice("player.fullscreen_unavailable"); }
  }

  useEffect(() => {
    const root = container.current;
    if (!root) return;
    let timer = 0, pointerActive = false;
    const wake = () => {
      setShown(true); window.clearTimeout(timer);
      if (!paused && !settingsOpen) timer = window.setTimeout(() => {
        const focused = document.activeElement;
        if (pointerActive || (focused instanceof HTMLElement && root.contains(focused) && focused.matches(":focus-visible"))) return;
        // Mouse clicks leave buttons focused; release that focus so :focus-within
        // does not keep the transport visible after the pointer becomes idle.
        if (focused instanceof HTMLElement && root.contains(focused)) focused.blur();
        setShown(false);
      }, 2600);
    };
    const pointerDown = () => { pointerActive = true; wake(); };
    const pointerUp = () => { if (pointerActive) { pointerActive = false; wake(); } };
    const key = (event: KeyboardEvent) => {
      if (event.altKey || event.ctrlKey || event.metaKey || (event.target instanceof Element && event.target.closest("button,a,input,select,textarea,[role=dialog]"))) return;
      const value = event.key.toLowerCase();
      if (![" ", "k", "m", "f", "arrowleft", "arrowright"].includes(value)) return;
      event.preventDefault(); wake();
      if (value === " " || value === "k") void togglePlay();
      else if (value === "m") toggleMuted();
      else if (value === "f") void toggleFullscreen();
      else seek((video.current?.currentTime || 0) + (value === "arrowleft" ? -5 : 5));
    };
    const surfaceClick = (event: MouseEvent) => { if (event.target === video.current) void togglePlay(); };
    root.addEventListener("pointermove", wake); root.addEventListener("pointerdown", pointerDown);
    root.addEventListener("focusin", wake); root.addEventListener("focusout", wake);
    document.addEventListener("pointerup", pointerUp); document.addEventListener("pointercancel", pointerUp);
    root.addEventListener("keydown", key); root.addEventListener("click", surfaceClick); wake();
    return () => {
      window.clearTimeout(timer);
      root.removeEventListener("pointermove", wake); root.removeEventListener("pointerdown", pointerDown);
      root.removeEventListener("focusin", wake); root.removeEventListener("focusout", wake); root.removeEventListener("keydown", key);
      document.removeEventListener("pointerup", pointerUp); document.removeEventListener("pointercancel", pointerUp);
      root.removeEventListener("click", surfaceClick);
    };
  }, [container, paused, settingsOpen, enabled]);

  const progress = duration > 0 ? Math.min(100, elapsed / duration * 100) : 0;
  const bufferedProgress = duration > 0 ? Math.min(100, buffered / duration * 100) : 0;
  const activeSubtitle = props.subtitle === "default" ? props.tracks.find(track => track.is_default)?.language : props.subtitle;
  const subtitleLabel = props.tracks.find(track => track.language === activeSubtitle)?.label || t("pages.off_d15305");
  const qualityLabel = props.levels.find(level => level.index === props.quality)?.label || t("pages.auto_06b928");
  const speedLabel = (value: number) => number(value, value === 1 ? { minimumFractionDigits: 1 } : undefined) + "×";
  const settings = settingsOpen && <Modal title={t("Playback settings")} className="pc-player-settings" onClose={() => setSettingsOpen(false)}>
    <div className="pc-settings-group" role="group" aria-label={t("pages.speed_44877c")}>
      <div className="pc-settings-label"><span><Icon name="clock" size={17} />{t("pages.speed_44877c")}</span><strong>{speedLabel(props.speed)}</strong></div>
      <div className="pc-setting-options pc-speed-options">{[0.75, 1, 1.25, 1.5, 2].map(value => <button type="button" key={value} aria-pressed={props.speed === value} onClick={() => props.onSpeed(value)}>{speedLabel(value)}</button>)}</div>
    </div>
    <div className="pc-settings-group" role="group" aria-label={t("pages.quality_571094")}>
      <div className="pc-settings-label"><span><Icon name="display" size={17} />{t("pages.quality_571094")}</span><strong>{qualityLabel}</strong></div>
      <div className="pc-setting-options"><button type="button" aria-pressed={props.quality === -1} onClick={() => props.onQuality(-1)}>{t("pages.auto_06b928")}</button>{props.levels.map(level => <button type="button" key={level.index} aria-pressed={props.quality === level.index} onClick={() => props.onQuality(level.index)}>{level.label}</button>)}</div>
    </div>
    {props.tracks.length > 0 && <div className="pc-settings-group" role="group" aria-label={t("pages.subtitles_6a7308")}>
      <div className="pc-settings-label"><span><Icon name="subtitles" size={17} />{t("pages.subtitles_6a7308")}</span><strong>{subtitleLabel}</strong></div>
      <div className="pc-setting-options"><button type="button" aria-pressed={!activeSubtitle || activeSubtitle === "off"} onClick={() => props.onSubtitle("off")}>{t("pages.off_d15305")}</button>{props.tracks.map(track => <button type="button" key={track.language} aria-pressed={activeSubtitle === track.language} onClick={() => props.onSubtitle(track.language)}>{track.label}</button>)}</div>
    </div>}
    {props.subtitleError && <p className="pc-settings-notice" role="status">{t(props.subtitleError)}</p>}
  </Modal>;

  return <>
    {paused && enabled && duration > 0 && <button type="button" className="pc-big-play" aria-label={t("player.play")} onClick={() => void togglePlay()}><span><Icon name="play" size={32} /></span></button>}
    {waiting && enabled && <div className="pc-player-buffering" aria-label={t("Loading…")} role="status"><span className="spinner" /></div>}
    <div className="pc-player-controls" data-visible={shown || paused || settingsOpen} role="group" aria-label={t("player.controls")}>
      <div className="pc-seek"><span className="pc-seek-track" aria-hidden="true"><span className="pc-seek-buffer" style={{ width: bufferedProgress + "%" }} /><span className="pc-seek-played" style={{ width: progress + "%" }} /></span><input type="range" aria-label={t("player.seek")} aria-valuetext={t("player.seek_value", { elapsed: clock(elapsed), duration: clock(duration) })} min={0} max={duration || 1} step={0.1} value={Math.min(elapsed, duration || 1)} disabled={!enabled || !duration} onChange={event => seek(Number(event.target.value))} /></div>
      <div className="pc-transport">
        <button type="button" className="pc-transport-button" disabled={!enabled} aria-label={t(paused ? "player.play" : "player.pause")} onClick={() => void togglePlay()}><Icon name={paused ? "play" : "pause"} size={18} /></button>
        <span className="pc-player-time" aria-hidden="true">{clock(elapsed)} / {clock(duration)}</span><span className="pc-transport-spacer" />
        <div className="pc-volume"><button type="button" className="pc-transport-button" disabled={!enabled} aria-label={t(muted ? "Unmute" : "Mute")} aria-pressed={muted} onClick={toggleMuted}><Icon name={muted ? "volume-off" : "volume"} size={18} /></button><div className="pc-volume-popover"><input type="range" aria-label={t("player.volume")} min={0} max={1} step={0.01} value={muted ? 0 : volume} disabled={!enabled} onChange={event => changeVolume(Number(event.target.value))} /></div></div>
        {props.settingsOpen === undefined && <button type="button" className="pc-transport-button" disabled={!enabled} aria-label={t("Playback settings")} onClick={() => setSettingsOpen(true)}><Icon name="settings" size={18} /></button>}
        <button type="button" className="pc-transport-button" disabled={!enabled} aria-label={t(fullscreen ? "player.exit_fullscreen" : "player.fullscreen")} onClick={() => void toggleFullscreen()}><Icon name={fullscreen ? "fullscreen-exit" : "fullscreen"} size={18} /></button>
      </div>
    </div>
    {notice && <p className="pc-player-notice" role="status">{t(notice)}</p>}
    {settings && createPortal(<div className="pc-settings-portal">{settings}</div>, document.fullscreenElement || document.body)}
  </>;
}

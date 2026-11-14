import { useEffect, useRef, useState } from "react";
import { message } from "antd";
import { api, errorText, isDeadCredential } from "./api";

/**
 * Minimal "load one endpoint into state" hook.
 *
 * `StagePages.tsx` grew its own copy first; new pages import this one instead
 * so a third copy never appears. Reload after a mutation by calling `reload()`.
 */
export function useLoad<T>(
  path: string,
  fallback: T,
  options: { enabled?: boolean } = {}
) {
  const enabled = options.enabled ?? true;
  const [data, setData] = useState<T>(fallback);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);
  const currentPath = useRef(path);
  currentPath.current = path;
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; generation.current++; }; }, []);

  const reload = () => {
    const id = ++generation.current;
    const requestedPath = path;
    const current = () => mounted.current && id === generation.current && currentPath.current === requestedPath;
    setLoading(true);
    return api<T>(path)
      .then((value) => {
        if (!current()) return;
        setData(value);
        setError(null);
      })
      .catch((e) => {
        if (!current()) return;
        if (isDeadCredential(e?.status ?? 0, e?.detail)) { setError('unauthorized'); return; }
        const text = errorText(e);
        setError(text);
        message.error(text);
      })
      .finally(() => { if (current()) setLoading(false); });
  };

  useEffect(() => {
    if (!enabled) {
      generation.current++;
      setLoading(false);
      return;
    }
    reload();
    // The caller owns `path`; changing it is the only reload trigger.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, enabled]);

  return { data, reload, loading, error };
}

/** ISO-8601 from the API -> local "YYYY-MM-DD HH:mm" for table cells. */
export function formatTime(value?: string | null): string {
  if (!value) return "-";
  const moment = new Date(value);
  if (Number.isNaN(moment.getTime())) return value;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${moment.getFullYear()}-${pad(moment.getMonth() + 1)}-${pad(moment.getDate())} ${pad(
    moment.getHours()
  )}:${pad(moment.getMinutes())}`;
}

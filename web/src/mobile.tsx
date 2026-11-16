import { siteStorageKey, siteFeature } from "../../shared/site";
import { useEffect, useRef } from "react";
import { App } from "../../h5/src/App";
import { postJson } from "../../h5/src/api";
import css from "../../h5/src/styles.css?inline";
import { SurfaceStyles } from "./device";
export default function MobileApp() {
  const captured = useRef(false);
  useEffect(() => {
    if (captured.current) return;
    captured.current = true;
    const params = new URLSearchParams(window.location.search),
      query = new URLSearchParams(window.location.hash.split("?")[1] || "");
    const pid = params.get("pid") || query.get("pid");
    if (siteFeature("attribution") && pid && /^[A-Za-z0-9_-]{1,128}$/.test(pid)) {
      localStorage.setItem(siteStorageKey("jjtv_attribution_pid"), pid);
      void postJson("/v1/attr/visit", {
        pid,
        fbclid: params.get("fbclid") || undefined,
        ttclid: params.get("ttclid") || undefined,
      }).catch(() => undefined);
    }
  }, []);
  return (
    <SurfaceStyles surface="mobile" css={css}>
      <App />
    </SurfaceStyles>
  );
}

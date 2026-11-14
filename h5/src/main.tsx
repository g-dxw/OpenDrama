import { StrictMode } from "react";
import { postJson } from "./api";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles.css";

const params = new URLSearchParams(window.location.search);
const pid = params.get("pid");
if (pid && /^[A-Za-z0-9_-]{1,128}$/.test(pid)) {
  localStorage.setItem("jjtv_attribution_pid", pid);
  void postJson("/v1/attr/visit", {
    pid,
    fbclid: params.get("fbclid") || undefined,
    ttclid: params.get("ttclid") || undefined,
  }).catch(() => undefined);
}
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

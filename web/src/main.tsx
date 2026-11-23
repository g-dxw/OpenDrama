import { loadSite, showSiteFailure } from "../../shared/site";
import React from "react";
import ReactDOM from "react-dom/client";
import { ResponsiveApp } from "./device";
const render = () => ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ResponsiveApp />
  </React.StrictMode>,
);

function start(){void loadSite((import.meta.env.VITE_API_BASE ?? "").replace(/\/$/, "")).then(render).catch(error=>showSiteFailure(error,start));}
start();

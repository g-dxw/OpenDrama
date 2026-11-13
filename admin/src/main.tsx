import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import "./index.css";
import App from "./App";
import { CURRENT_ENTRY } from "./entry";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ConfigProvider
      locale={zhCN}
      theme={{ token: { colorPrimary: "#E53B3E", borderRadius: 8 } }}
    >
      <BrowserRouter basename={CURRENT_ENTRY.base || undefined}>
        <App />
      </BrowserRouter>
    </ConfigProvider>
  </React.StrictMode>
);

import { useEffect, useState, type ReactNode } from "react";
import { AuthProvider, useAuth } from "./auth/AuthProvider";
import { LoginSheet } from "./auth/LoginSheet";
import { loadLocale, saveLocale, t, type Locale } from "./copy";
import { IconForYou, IconHome, IconMe, IconRewards, IconSearch } from "./icons";
import { ForYouPage } from "./pages/ForYouPage";
import { HomePage } from "./pages/HomePage";
import { MePage } from "./pages/MePage";
import { PlayPage } from "./pages/PlayPage";
import { RewardsPage } from "./pages/RewardsPage";
import { SearchPage } from "./pages/SearchPage";
import { toast, ToastHost } from "./ui/toast";
import { readMobileRoute } from "./routes";

type Tab = "home" | "foryou" | "rewards" | "me";

interface TabItem {
  id: Tab;
  key: string;
  renderIcon: (active: boolean) => ReactNode;
}

const TABS: TabItem[] = [
  {
    id: "home",
    key: "nav.home",
    renderIcon: (active) => <IconHome active={active} />,
  },
  {
    id: "foryou",
    key: "nav.foryou",
    renderIcon: (active) => <IconForYou active={active} />,
  },
  {
    id: "rewards",
    key: "nav.rewards",
    renderIcon: (active) => <IconRewards active={active} />,
  },
  {
    id: "me",
    key: "nav.me",
    renderIcon: (active) => <IconMe active={active} />,
  },
];

export function App() {
  return (
    <AuthProvider>
      <AppShell />
    </AuthProvider>
  );
}

function AppShell() {
  const { loginOpen, refresh, authError } = useAuth();
  const [locale, setLocale] = useState<Locale>(loadLocale);
  const [route, setRoute] = useState(readMobileRoute);
  const { tab, dramaId, searching } = route;
  const setTab = (next: Tab) => {
    window.location.hash = next;
  };
  const setDramaId = (id: number | null) => {
    window.location.hash = id == null ? "home" : "watch/" + id;
  };
  const setSearching = (active: boolean) => {
    window.location.hash = active ? "search" : "home";
  };
  useEffect(() => {
    const update = () => setRoute(readMobileRoute());
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  // Stripe sends the browser back here after Checkout. Clean the flag out of the
  // URL and re-read /v1/me so the "Premium active" strip appears immediately.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const result = params.get("checkout");
    if (!result) return;
    for (const key of Object.keys(sessionStorage))
      if (key.startsWith("jjtv_checkout_membership_"))
        sessionStorage.removeItem(key);
    params.delete("checkout");
    const query = params.toString();
    window.history.replaceState(
      {},
      "",
      `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`,
    );
    if (result === "success") {
      if (!window.location.hash) setTab("me");
      void refresh();
      toast(
        locale === "zh"
          ? "支付已返回，请在订单中查看确认结果。"
          : "Checkout returned. Check your orders for confirmation.",
      );
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const changeLocale = (next: Locale) => {
    saveLocale(next);
    setLocale(next);
  };

  const switchLocale = () => changeLocale(locale === "zh" ? "en" : "zh");

  const playing = dramaId != null;

  return (
    <div className="app" id="app">
      {authError && (
        <div className="auth-connection-error" role="alert">
          <span>{authError}</span>
          <button onClick={() => void refresh()}>Retry</button>
        </div>
      )}
      {!playing && tab !== "me" && (
        <header className="topbar">
          {searching ? null : tab === "home" ? (
            <div className="brand" role="heading" aria-level={1}>
              <span className="brand-m">JJ</span>
              <span className="brand-rest">TV</span>
            </div>
          ) : (
            <h1>{t(locale, TABS.find((item) => item.id === tab)!.key)}</h1>
          )}
          <span style={{ flex: 1 }} />
          <button className="icon-btn" onClick={switchLocale}>
            {locale === "zh" ? "EN" : "中"}
          </button>
          {!searching && (
            <button
              className="icon-btn search-btn"
              onClick={() => setSearching(true)}
              aria-label={t(locale, "search")}
            >
              <IconSearch />
            </button>
          )}
        </header>
      )}
      <main className={playing || tab === "foryou" ? "stage bleed" : "stage"}>
        {!route.supported ? (
          <div className="center">
            <h2>
              {locale === "zh" ? "在桌面版中继续" : "Continue in desktop view"}
            </h2>
            <a
              href={
                (import.meta.env.VITE_DESKTOP_URL ||
                  (import.meta.env.DEV
                    ? window.location.protocol +
                      "//" +
                      window.location.hostname +
                      ":5174"
                    : "https://jjtv.8jj.net")) +
                "/" +
                window.location.hash
              }
            >
              {locale === "zh" ? "打开此页面" : "Open this page"}
            </a>
            <a href="#home">{t(locale, "nav.home")}</a>
          </div>
        ) : playing ? (
          <PlayPage
            key={dramaId + ":" + (route.episodeId || "resume")}
            dramaId={dramaId}
            episodeId={route.episodeId}
            locale={locale}
            onBack={() => setDramaId(null)}
            onOpenRewards={() => {
              setTab("rewards");
            }}
          />
        ) : searching ? (
          <SearchPage
            key={route.query}
            locale={locale}
            initialQuery={route.query}
            onQuery={(query) => {
              window.location.hash = "search?q=" + encodeURIComponent(query);
            }}
            onBack={() => setSearching(false)}
            onOpen={(id) => {
              setDramaId(id);
            }}
          />
        ) : tab === "home" ? (
          <HomePage locale={locale} onOpen={setDramaId} />
        ) : tab === "foryou" ? (
          <ForYouPage locale={locale} onOpenDrama={setDramaId} />
        ) : tab === "me" ? (
          <MePage
            key={route.meTab + ":" + route.meSheet}
            locale={locale}
            initialTab={route.meTab}
            initialSheet={route.meSheet}
            onLocale={changeLocale}
            onOpen={setDramaId}
          />
        ) : (
          <RewardsPage locale={locale} onExplore={(target) => setTab(target)} />
        )}
        {!playing && !searching && tab === "me" && (
          <div className="mobile-desktop-links">
            <a href="#coins">{locale === "zh" ? "购买金币" : "Buy coins"}</a>
            <a href="#support">
              {locale === "zh" ? "帮助与客服" : "Help & support"}
            </a>
          </div>
        )}
      </main>
      {!playing && !searching && !loginOpen && (
        <nav className="tabs" role="tablist">
          {TABS.map((item) => {
            const active = item.id === tab;
            return (
              <button
                key={item.id}
                type="button"
                role="tab"
                data-tab={item.id}
                aria-selected={active}
                className={active ? "tab-item on" : "tab-item"}
                onClick={() => setTab(item.id)}
              >
                <span className="tab-icon">{item.renderIcon(active)}</span>
                <span className="tab-label">{t(locale, item.key)}</span>
              </button>
            );
          })}
        </nav>
      )}
      <LoginSheet locale={locale} />
      <ToastHost />
    </div>
  );
}

import { getSite, siteName, siteLink, siteFeature } from "../../shared/site";
import { useEffect, useState, type ReactNode } from "react";
import { AuthProvider, useAuth } from "./auth/AuthProvider";
import { LoginSheet } from "./auth/LoginSheet";
import { loadLocale, saveLocale, t, type Locale } from "./copy";
import { IconNavDiscover, IconNavHome, IconNavList, IconNavProfile, IconNavRewards, IconSearch } from "./icons";
import { ForYouPage } from "./pages/ForYouPage";
import { HomePage } from "./pages/HomePage";
import { MePage } from "./pages/MePage";
import { MyListPage } from "./pages/MyListPage";
import { PlayPage } from "./pages/PlayPage";
import { RewardsPage } from "./pages/RewardsPage";
import { SearchPage } from "./pages/SearchPage";
import { toast, ToastHost } from "./ui/toast";
import { readMobileRoute, type MobileTab } from "./routes";

type Tab = MobileTab;

interface TabItem {
  id: Tab;
  key: string;
  renderIcon: (active: boolean) => ReactNode;
}

const TABS: TabItem[] = [
  {
    id: "home",
    key: "nav.home",
    renderIcon: (active) => <IconNavHome active={active} />,
  },
  {
    id: "foryou",
    key: "nav.foryou",
    renderIcon: (active) => <IconNavDiscover active={active} />,
  },
  {
    id: "rewards",
    key: "nav.rewards",
    renderIcon: (active) => <IconNavRewards active={active} />,
  },
  {
    id: "mylist",
    key: "nav.mylist",
    renderIcon: (active) => <IconNavList active={active} />,
  },
  {
    id: "me",
    key: "nav.me",
    renderIcon: (active) => <IconNavProfile active={active} />,
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
  const setDramaId = (id: number | null, episodeId?: number) => {
    window.location.hash = id == null ? "home" : "watch/" + id + (episodeId ? "?episode=" + episodeId : "");
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
    // A return query is not payment evidence; retain every uncertain checkout intent.
    // A confirmed paid order can be retired only by its explicit new-purchase action.
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
  const visibleTabs = TABS.filter(item => item.id !== "rewards" || siteFeature("rewards"));
  const activeTabIsVisible = visibleTabs.some(item => item.id === tab);

  return (
    <div className="app" id="app">
      {new URLSearchParams(window.location.search).has("preview_token") && <div className="site-preview-banner" role="status">{locale==="zh"?"品牌草稿预览 · 尚未发布":"Brand draft preview · not published"}</div>}
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
              {getSite().brand.logo_url && <img src={getSite().brand.logo_url} alt="" style={{width:30,height:30,objectFit:"contain",marginRight:8}}/>}{siteName()}
            </div>
          ) : (
            <h1>{t(locale, TABS.find((item) => item.id === tab)!.key)}</h1>
          )}
          <span style={{ flex: 1 }} />
          <button className="icon-btn" onClick={switchLocale} disabled={getSite().tenant.locales.length<2}>
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
        {((tab==="rewards"&&!siteFeature("rewards")) || (route.meSheet==="premium"&&!siteFeature("membership"))) ? (<div className="center" role="status"><h2>{locale==="zh"?"本站暂未开放此功能":"This feature is unavailable on this site."}</h2><a href="#home">{t(locale,"nav.home")}</a></div>) : !route.supported ? (
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
                    : siteLink("web"))) +
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
            key={dramaId}
            dramaId={dramaId}
            episodeId={route.episodeId}
            locale={locale}
            onBack={() => setDramaId(null)}
            onOpenCoins={() => {
              if (siteFeature("coins")) window.location.assign(siteLink("web").replace(/\/$/, "") + "/#coins");
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
        ) : tab === "mylist" ? (
          <MyListPage locale={locale} initialTab={route.meTab} onOpen={setDramaId}
            onTab={(next) => { window.location.hash = "mylist/" + next; }} />
        ) : tab === "me" ? (
          <MePage
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
            {siteFeature("coins")&&<a href="#coins">{locale === "zh" ? "购买金币" : "Buy coins"}</a>}
            {siteFeature("support")&&<a href="#support">
              {locale === "zh" ? "帮助与客服" : "Help & support"}
            </a>}
          </div>
        )}
      </main>
      {!playing && !searching && !loginOpen && (
        <div className="mobile-bottom">
        <nav className="tabs" role="tablist" aria-label={t(locale, "nav.label")}>
          {visibleTabs.map((item, itemIndex) => {
            const active = item.id === tab;
            return (
              <button
                key={item.id}
                type="button"
                role="tab"
                id={"mobile-tab-" + item.id}
                tabIndex={active || (!activeTabIsVisible && itemIndex === 0) ? 0 : -1}
                data-tab={item.id}
                aria-selected={active}
                className={active ? "tab-item on" : "tab-item"}
                onClick={() => setTab(item.id)}
                onKeyDown={(event) => {
                  const delta = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
                  if (!delta && event.key !== "Home" && event.key !== "End") return;
                  event.preventDefault();
                  const nextIndex = event.key === "Home" ? 0 : event.key === "End" ? visibleTabs.length - 1
                    : (itemIndex + delta + visibleTabs.length) % visibleTabs.length;
                  const next = visibleTabs[nextIndex];
                  setTab(next.id);
                  document.getElementById("mobile-tab-" + next.id)?.focus();
                }}
              >
                {active && <span className="tab-active-line" aria-hidden="true" />}
                <span className="tab-icon">{item.renderIcon(active)}</span>
                <span className="tab-label">{t(locale, item.key)}</span>
              </button>
            );
          })}
        </nav>
        <div className="mobile-powered-by"><span aria-hidden="true" />Made with MiniTV</div>
        </div>
      )}
      <LoginSheet locale={locale} />
      <ToastHost />
    </div>
  );
}

import { getSite, siteName, siteLink, siteFeature, siteStorageKey } from "../../shared/site";
import { LocaleProvider, useLocale } from "./locale";
import { LanguageMenu } from "./language-menu";
import { useEffect, useState, lazy, Suspense } from "react";
import { AuthProvider, useAuth } from "./auth";
import { Login } from "./login";
import { Home, Browse, ForYou } from "./browse";
import { Account, Rewards } from "./account";
import { Store, CheckoutReturn } from "./commerce";
const Watch = lazy(() =>
  import("./player").then((module) => ({ default: module.Watch })),
);
import { Support } from "./support";
import { get, post, errorMessage } from "./consumer-api";
import { Failure, Icon, Loading } from "./components";
export default function App() {
  return (
    <LocaleProvider><AuthProvider>
      <Shell />
    </AuthProvider></LocaleProvider>
  );
}
function Shell() {
  const { t } = useLocale();
  const { user, status, loginOpen, openLogin, authError, refresh } = useAuth();
  const [hash, setHash] = useState(window.location.hash.slice(1) || "home"),
    [menu, setMenu] = useState(false);
  const [checkout, setCheckout] = useState<string | null>(() =>
    new URLSearchParams(window.location.search).get("checkout"),
  );
  useEffect(() => {
    const update = () => {
      setHash(window.location.hash.slice(1) || "home");
      setMenu(false);
      setCheckout(null);
      window.scrollTo(0, 0);
    };
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const hashQuery = new URLSearchParams(hash.split("?")[1] || "");
    const pid = params.get("pid") || hashQuery.get("pid");
    if (siteFeature("attribution") && pid && /^[A-Za-z0-9_-]{1,128}$/.test(pid)) {
      localStorage.setItem(siteStorageKey("jjtv_attribution_pid"), pid);
      void post("/v1/attr/visit", {
        pid,
        client_id: localStorage.getItem(siteStorageKey("jjtv_pc_device")) || undefined,
        fbclid: params.get("fbclid") || undefined,
        ttclid: params.get("ttclid") || undefined,
      }).catch(() => undefined);
    }
    if (params.has("checkout")) {
      params.delete("checkout");
      const query = params.toString();
      history.replaceState(
        {},
        "",
        window.location.pathname +
          (query ? "?" + query : "") +
          window.location.hash,
      );
    }
  }, []);
  const [path, query = ""] = hash.replace(/^\/+/, "").split("?");
  const params = new URLSearchParams(query);
  const route = path.split("/");
  const activeNavigation =
    ["account", "me"].includes(route[0]) || path === "rewards"
      ? "account"
      : ["premium", "coins", "price"].includes(path)
        ? "price"
        : ["browse", "search", "shows", "preview"].includes(path)
          ? "browse"
          : path;
  let content;
  if (path === "home" || path === "") content = <Home />;
  else if (["browse", "search", "shows", "preview"].includes(path))
    content = <Browse key={path} query={query} preview={path === "preview"} searchOnly={path === "search"} />;
  else if (path === "foryou") content = <ForYou />;
  else if (route[0] === "watch") {
    const id = Number(route[1]);
    content =
      Number.isSafeInteger(id) && id > 0 ? (
        <Watch
          key={id + ":" + (params.get("episode") || "resume")}
          dramaId={id}
          episodeId={Number(params.get("episode")) || undefined}
        />
      ) : (
        <Failure message={t("This drama link is invalid.")} />
      );
  } else if (route[0] === "account" || route[0] === "me")
    content = (
      <Account key={route[1] || "history"} section={route[1] || "history"} />
    );
  else if (path === "premium" || path === "coins" || path === "price")
    content = (
      <Store
        kind={
          path === "coins" || params.get("tab") === "coins" || (path==="price"&&!siteFeature("membership")&&siteFeature("coins"))
            ? "coin_pack"
            : "membership"
        }
        sourceDramaId={Number(params.get("drama")) || undefined}
        sourceEpisodeId={Number(params.get("episode")) || undefined}
      />
    );
  else if (path === "rewards")
    content = (
      <>
        <Account section="history" />
        <Rewards
          panel
          onClose={() => {
            window.location.hash =
              params.get("return") === "price" ? "price" : "account";
          }}
        />
      </>
    );
  else if (path === "support") content = <Support />;
  else if (route[0] === "legal") content = <Legal slug={route[1] || "terms"} />;
  else if (path === "about" || path === "download") content = <About />;
  else content = <Home />;
  if ((["coins","price","premium"].includes(path) && !siteFeature(path === "coins" || params.get("tab") === "coins" ? "coins" : "membership")) || (path === "rewards" && !siteFeature("rewards")) || (path === "support" && !siteFeature("support"))) content = <Failure message={t("This feature is unavailable on this site.")} />;
  return (
    <div className="consumer-app">
      {new URLSearchParams(window.location.search).has("preview_token") && <div className="site-preview-banner" role="status">{t("Brand preview — this draft is not published.")}</div>}
      <a
        className="skip-link"
        href="#main-content"
        onClick={(e) => {
          e.preventDefault();
          document.getElementById("main-content")?.focus();
        }}
      >
        {t("Skip to content")}</a>
      <header className="consumer-header">
        <a className="consumer-brand" href="#home" aria-label={t("JJTV home")}>
          {getSite().brand.logo_url ? <img src={getSite().brand.logo_url} alt=""/> : <span className="brand-symbol"><Icon name="play" size={20}/></span>}
          {siteName()}
        </a>
        <button
          className="icon-button mobile-menu"
          aria-expanded={menu}
          aria-label={t("Toggle navigation")}
          onClick={() => setMenu(!menu)}
        >
          <Icon name="menu" />
        </button>
        <nav className={menu ? "open" : ""} aria-label={t("Main navigation")}>
          {[
            ["home", "Home"],
            ["foryou", "For You"],
            ["price", "Price"],
            ["account", "Me"],
          ].filter(([id])=>id!=="price"||siteFeature("membership")||siteFeature("coins")).map(([id, label]) => (
            <a
              key={id}
              href={"#" + id}
              className={activeNavigation === id ? "active" : ""}
              aria-current={activeNavigation === id ? "page" : undefined}
            >
              {t(label)}
            </a>
          ))}
        </nav>
        <div className="header-account">
          <a className={"header-search-trigger icon-button" + (path === "search" ? " active" : "")} href="#search" aria-label={t("Search dramas")}><Icon name="search" size={20}/></a>
          <LanguageMenu />
          {user ? (
            <>
              <a
                className="profile-button"
                href="#account"
                aria-label={t("Your account")}
              >
                {user.display_name.slice(0, 1).toUpperCase()}
              </a>
            </>
          ) : (
            <button
              className="button secondary sign-in"
              onClick={openLogin}
              disabled={status === "loading"}
            >
              {status === "loading" ? "…" : t("Sign in")}
            </button>
          )}
        </div>
      </header>
      <main id="main-content" tabIndex={-1}>
        {authError && (
          <div className="auth-notice" role="alert">
            <span>{t("Your session is saved.")} {t(authError)}</span>
            <button className="button secondary" onClick={() => void refresh()}>
              {t("Retry account connection")}</button>
          </div>
        )}
        {checkout && (
          <CheckoutReturn
            result={checkout}
            onDismiss={() => setCheckout(null)}
          />
        )}
        <Suspense fallback={<Loading />}>{content}</Suspense>
      </main>
      <footer className="consumer-footer">
        <div className="footer-identity"><a className="consumer-brand" href="#home">{siteName()}</a><span>© {new Date().getFullYear()} {getSite().company.name || siteName()}</span></div>
        <nav aria-label={t("Footer")}><a href="#about">{t("About")}</a>{siteFeature("support")&&<a href="#support">{t("Help & support")}</a>}<a href="#legal/terms">{t("Terms")}</a><a href="/privacy.html">{t("Privacy")}</a></nav>
      </footer>
      {loginOpen && <Login />}
    </div>
  );
}
function About() {
  const { t } = useLocale();
  return (
    <div className="page-content about-page">
      <span className="eyebrow">{t("Welcome to JJTV")}</span>
      <h1>{t("Stories that fit your day.")}</h1>
      <p>
        {t("Discover short dramas, watch free episodes, and unlock the stories you love with coins or membership.")}</p>
      <div className="button-row">
        <a className="button primary" href="#browse">
          {t("Watch online")}</a>
        <a
          className="button secondary"
          href={siteLink("h5")}
          target="_blank"
          rel="noopener noreferrer"
        >
          {t("Open mobile web app")}<Icon name="arrow" />
        </a>
      </div>
      <div className="about-card">
        <img src="/ph_feed.png" alt={t("JJTV mobile viewing experience")} />
        <div>
          <h2>{t("Take the story with you.")}</h2>
          <p>
            {t("The mobile web app is ready to watch in your browser. Native app downloads will appear when verified JJTV builds are available.")}</p>
          <a href={siteLink("h5")} className="button primary">
            {t("Watch on mobile")}</a>
        </div>
      </div>
    </div>
  );
}
function Legal({ slug }: { slug: string }) {
  const { t } = useLocale();
  const [data, setData] = useState<{
      title?: string;
      body?: string;
      content?: string;
    } | null>(null),
    [error, setError] = useState("");
  useEffect(() => {
    setData(null);
    setError("");
    get<{ title?: string; body?: string; content?: string }>(
      "/v1/legal/" + encodeURIComponent(slug),
    )
      .then(setData)
      .catch((e) => setError(errorMessage(e)));
  }, [slug]);
  return (
    <div className="page-content legal-page">
      {error ? (
        <Failure message={error} />
      ) : !data ? (
        <Loading />
      ) : (
        <>
          <h1>{data.title || slug}</h1>
          <div className="legal-body">
            {data.body ||
              data.content ||
              t("This document is not available yet. Please contact support.")}
          </div>
        </>
      )}
    </div>
  );
}

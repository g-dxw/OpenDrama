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
import "./consumer.css";
import "./details.css";
export default function App() {
  return (
    <AuthProvider>
      <Shell />
    </AuthProvider>
  );
}
function Shell() {
  const { user, status, loginOpen, openLogin, authError, refresh } = useAuth();
  const [hash, setHash] = useState(window.location.hash.slice(1) || "home"),
    [search, setSearch] = useState(""),
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
    if (pid && /^[A-Za-z0-9_-]{1,128}$/.test(pid)) {
      localStorage.setItem("jjtv_attribution_pid", pid);
      void post("/v1/attr/visit", {
        pid,
        client_id: localStorage.getItem("jjtv_pc_device") || undefined,
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
    content = <Browse key={path} query={query} preview={path === "preview"} />;
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
        <Failure message="This drama link is invalid." />
      );
  } else if (route[0] === "account" || route[0] === "me")
    content = (
      <Account key={route[1] || "history"} section={route[1] || "history"} />
    );
  else if (path === "premium" || path === "coins" || path === "price")
    content = (
      <Store
        kind={
          path === "coins" || params.get("tab") === "coins"
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
  return (
    <div className="consumer-app">
      <a
        className="skip-link"
        href="#main-content"
        onClick={(e) => {
          e.preventDefault();
          document.getElementById("main-content")?.focus();
        }}
      >
        Skip to content
      </a>
      <header className="consumer-header">
        <a className="consumer-brand" href="#home" aria-label="JJTV home">
          <span className="brand-symbol">
            <Icon name="play" size={20} />
          </span>
          JJ<span>TV</span>
        </a>
        <button
          className="icon-button mobile-menu"
          aria-expanded={menu}
          aria-label="Toggle navigation"
          onClick={() => setMenu(!menu)}
        >
          <Icon name="menu" />
        </button>
        <nav className={menu ? "open" : ""} aria-label="Main navigation">
          {[
            ["home", "Home"],
            ["browse", "Explore"],
            ["foryou", "For you"],
            ["price", "Price"],
            ["account", "Me"],
          ].map(([id, label]) => (
            <a
              key={id}
              href={"#" + id}
              className={activeNavigation === id ? "active" : ""}
              aria-current={activeNavigation === id ? "page" : undefined}
            >
              {label}
            </a>
          ))}
        </nav>
        <form
          className="header-search"
          role="search"
          onSubmit={(e) => {
            e.preventDefault();
            window.location.hash = "search?q=" + encodeURIComponent(search);
          }}
        >
          <label className="sr-only" htmlFor="site-search">
            Search dramas
          </label>
          <input
            id="site-search"
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Find your next story"
          />
          <button aria-label="Search" type="submit">
            <Icon name="search" />
          </button>
        </form>
        <div className="header-account">
          <a className="premium-link" href="#premium">
            <Icon name="crown" />
            <span>Premium</span>
          </a>
          {user ? (
            <>
              <a
                className="header-coins"
                href="#coins"
                aria-label={user.coins + " coins, top up"}
              >
                <Icon name="coin" />
                {user.coins.toLocaleString()}
              </a>
              <a
                className="profile-button"
                href="#account"
                aria-label="Your account"
              >
                {user.display_name.slice(0, 1).toUpperCase()}
              </a>
            </>
          ) : (
            <button
              className="button primary sign-in"
              onClick={openLogin}
              disabled={status === "loading"}
            >
              {status === "loading" ? "…" : "Sign in"}
            </button>
          )}
        </div>
      </header>
      <main id="main-content" tabIndex={-1}>
        {authError && (
          <div className="auth-notice" role="alert">
            <span>Your session is saved. {authError}</span>
            <button className="button secondary" onClick={() => void refresh()}>
              Retry account connection
            </button>
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
        <a className="consumer-brand" href="#home">
          JJ<span>TV</span>
        </a>
        <p>Big stories. Small episodes. Watch your way.</p>
        <nav aria-label="Footer">
          <a href="#about">About JJTV</a>
          <a href="#support">Help & support</a>
          <a href="#legal/terms">Terms of Use</a>
          <a href="/privacy.html">Privacy Policy</a>
          <a href="#download">Watch on mobile</a>
        </nav>
        <span>© {new Date().getFullYear()} JJTV</span>
      </footer>
      {loginOpen && <Login />}
    </div>
  );
}
function About() {
  return (
    <div className="page-content about-page">
      <span className="eyebrow">Welcome to JJTV</span>
      <h1>Stories that fit your day.</h1>
      <p>
        Discover short dramas, watch free episodes, and unlock the stories you
        love with coins or membership.
      </p>
      <div className="button-row">
        <a className="button primary" href="#browse">
          Watch online
        </a>
        <a
          className="button secondary"
          href="https://jjtv-h5.8jj.net"
          target="_blank"
          rel="noopener noreferrer"
        >
          Open mobile web app
          <Icon name="arrow" />
        </a>
      </div>
      <div className="about-card">
        <img src="/ph_feed.png" alt="JJTV mobile viewing experience" />
        <div>
          <h2>Take the story with you.</h2>
          <p>
            The mobile web app is ready to watch in your browser. Native app
            downloads will appear when verified JJTV builds are available.
          </p>
          <a href="https://jjtv-h5.8jj.net" className="button primary">
            Watch on mobile
          </a>
        </div>
      </div>
    </div>
  );
}
function Legal({ slug }: { slug: string }) {
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
              "This document is not available yet. Please contact support."}
          </div>
        </>
      )}
    </div>
  );
}

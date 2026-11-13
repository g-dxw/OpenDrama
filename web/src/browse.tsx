import { useEffect, useState } from "react";
import {
  get,
  errorMessage,
  type Catalog,
  type CatalogDrama,
  type HomePayload,
  type FeedPage,
} from "./consumer-api";
import { Cards, DramaCard, Empty, Failure, Icon, Loading } from "./components";
export function Home() {
  const [home, setHome] = useState<HomePayload | null>(null),
    [error, setError] = useState("");
  const load = () => {
    setError("");
    get<HomePayload>("/v1/home/groups")
      .then(setHome)
      .catch((e) => setError(errorMessage(e)));
  };
  useEffect(load, []);
  if (error) return <Failure message={error} retry={load} />;
  if (!home) return <Loading />;
  const banner = home.banners.find((b) => b.drama_id) || home.banners[0];
  const featured = home.groups.flatMap((g) => g.dramas)[0];
  return (
    <>
      <section className="feature-hero">
        {(banner?.cover_url || featured?.cover_url) && (
          <img
            className="hero-backdrop"
            src={banner?.cover_url || featured?.cover_url || ""}
            alt=""
          />
        )}
        <div className="hero-shade" />
        <div className="feature-copy">
          <span className="eyebrow">
            {banner?.kicker || "Stories made for your next break"}
          </span>
          <h1>
            {banner?.headline ||
              banner?.title ||
              "Small episodes. Big emotions."}
          </h1>
          <p>
            Discover your next obsession. Watch free episodes, follow the story,
            and keep watching on JJTV.
          </p>
          <div className="button-row">
            <a
              className="button primary"
              href={
                banner?.drama_id
                  ? "#watch/" + banner.drama_id
                  : featured
                    ? "#watch/" + featured.id
                    : "#browse"
              }
            >
              <Icon name="play" />
              Watch now
            </a>
            <a className="button secondary" href="#browse">
              Explore all dramas
              <Icon name="arrow" />
            </a>
          </div>
        </div>
        {(banner?.cover_url || featured?.cover_url) && (
          <img
            className="hero-poster"
            src={banner?.cover_url || featured?.cover_url || ""}
            alt={banner?.title || featured?.title || "Featured drama"}
          />
        )}
      </section>
      <div className="page-content">
        {home.groups.map((group) => (
          <section className="drama-section" key={group.id}>
            <div className="section-heading">
              <div>
                <span className="eyebrow">
                  {group.badge === "rank"
                    ? "Most watched"
                    : group.badge === "label"
                      ? "Editor's picks"
                      : "Selected for you"}
                </span>
                <h2>{group.name}</h2>
              </div>
              <a href={"#browse?group=" + group.id}>
                View all
                <Icon name="arrow" size={16} />
              </a>
            </div>
            <div
              className={
                group.style === "rank" ? "drama-row ranking-row" : "drama-row"
              }
            >
              {group.dramas.map((drama, index) =>
                group.style === "rank" ? (
                  <div className="ranked-card" key={drama.id}>
                    <span
                      className="rank-number"
                      aria-label={"Rank " + (index + 1)}
                    >
                      {index + 1}
                    </span>
                    <DramaCard drama={drama} />
                  </div>
                ) : (
                  <DramaCard key={drama.id} drama={drama} />
                ),
              )}
            </div>
          </section>
        ))}
        {!home.groups.length && (
          <Empty message="New stories are on their way. Check back soon." />
        )}
      </div>
    </>
  );
}
export function Browse({
  query,
  preview = false,
}: {
  query: string;
  preview?: boolean;
}) {
  const params = new URLSearchParams(query);
  const [q, setQ] = useState(params.get("q") || ""),
    [tag, setTag] = useState(params.get("tag") || ""),
    [language, setLanguage] = useState(params.get("language") || ""),
    [sort, setSort] = useState(params.get("sort") || "newest"),
    [offset, setOffset] = useState(0),
    [data, setData] = useState<Catalog | null>(null),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const next = new URLSearchParams(query);
    setQ(next.get("q") || "");
    setTag(next.get("tag") || "");
    setLanguage(next.get("language") || "");
    setSort(next.get("sort") || "newest");
    setOffset(0);
  }, [query]);
  useEffect(() => {
    let current = true;
    setLoading(true);
    setError("");
    const filters = new URLSearchParams({
      limit: "24",
      offset: String(offset),
      sort,
    });
    const group = new URLSearchParams(query).get("group");
    if (group) filters.set("group", group);
    if (preview) filters.set("free_only", "true");
    if (q.trim()) filters.set("q", q.trim());
    if (tag.trim()) filters.set("tag", tag.trim());
    if (language.trim()) filters.set("language", language.trim());
    get<Catalog>("/v1/catalog?" + filters)
      .then((result) => {
        if (current) setData(result);
      })
      .catch((e) => {
        if (current) setError(errorMessage(e));
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [q, tag, language, sort, offset, revision, query, preview]);
  return (
    <div className="page-content">
      <div className="page-title">
        <span className="eyebrow">The JJTV collection</span>
        <h1>
          {preview ? "Free Preview" : q ? "Search results" : "Explore dramas"}
        </h1>
        <p className="muted">
          {preview
            ? "Start with free episodes. Sign in whenever you are ready for more."
            : "Find a story worth staying up for."}
        </p>
      </div>
      <form
        className="catalog-filters"
        onSubmit={(e) => {
          e.preventDefault();
          setOffset(0);
        }}
      >
        <label className="search-filter">
          Search dramas
          <input
            aria-label="Search dramas"
            type="search"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setOffset(0);
            }}
            placeholder="Search title or story…"
          />
        </label>
        <label>
          Tag
          <input
            value={tag}
            onChange={(e) => {
              setTag(e.target.value);
              setOffset(0);
            }}
            placeholder="Any genre"
          />
        </label>
        <label>
          Language
          <input
            value={language}
            onChange={(e) => {
              setLanguage(e.target.value);
              setOffset(0);
            }}
            placeholder="All languages"
          />
        </label>
        <label>
          Sort
          <select
            value={sort}
            onChange={(e) => {
              setSort(e.target.value);
              setOffset(0);
            }}
          >
            <option value="newest">Recently added</option>
            <option value="popular">Most watched</option>
            <option value="title">Title A–Z</option>
          </select>
        </label>
      </form>
      {error ? (
        <Failure message={error} retry={() => setRevision((x) => x + 1)} />
      ) : loading ? (
        <Loading />
      ) : (
        data && (
          <>
            <p className="result-count" role="status">
              {data.total} {data.total === 1 ? "drama" : "dramas"}
            </p>
            <Cards items={data.items} />
            <div className="pagination">
              <button
                className="button secondary"
                disabled={!offset}
                onClick={() => {
                  setOffset(Math.max(0, offset - 24));
                  window.scrollTo(0, 0);
                }}
              >
                Previous
              </button>
              <span>
                {data.total ? offset + 1 : 0}–
                {Math.min(offset + 24, data.total)} of {data.total}
              </span>
              <button
                className="button secondary"
                disabled={offset + 24 >= data.total}
                onClick={() => {
                  setOffset(offset + 24);
                  window.scrollTo(0, 0);
                }}
              >
                Next
              </button>
            </div>
          </>
        )
      )}
    </div>
  );
}
export function ForYou() {
  const [items, setItems] = useState<CatalogDrama[]>([]),
    [cursor, setCursor] = useState<string | null>(null),
    [hasMore, setHasMore] = useState(true),
    [loading, setLoading] = useState(false),
    [error, setError] = useState("");
  async function load(reset = false) {
    setLoading(true);
    setError("");
    try {
      const data = await get<FeedPage>(
        "/v1/feed?limit=12" +
          (!reset && cursor ? "&cursor=" + encodeURIComponent(cursor) : ""),
      );
      setItems((old) => {
        const next = reset ? [] : [...old];
        for (const item of data.items)
          if (!next.some((d) => d.id === item.drama.id)) next.push(item.drama);
        return next;
      });
      setCursor(data.next_cursor);
      setHasMore(data.has_more);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    void load(true);
  }, []);
  return (
    <div className="page-content">
      <div className="page-title">
        <span className="eyebrow">Your next favorite</span>
        <h1>For you</h1>
        <p className="muted">
          A fresh selection of stories to get you started.
        </p>
      </div>
      {items.length > 0 && <Cards items={items} />}{" "}
      {error && <Failure message={error} retry={() => void load()} />}{" "}
      {loading ? (
        <Loading />
      ) : hasMore ? (
        <button
          className="button secondary load-more"
          onClick={() => void load()}
        >
          Discover more
        </button>
      ) : (
        !items.length && <Empty />
      )}
    </div>
  );
}

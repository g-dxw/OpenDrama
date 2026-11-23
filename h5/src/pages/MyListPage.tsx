import { useCallback, useEffect, useRef, useState } from "react";
import { authToken, getFavorites, getHistory, getLikes } from "../api";
import { useAuth } from "../auth/AuthProvider";
import { t, type Locale } from "../copy";
import { formatCount } from "../format";
import { IconBookmark } from "../icons";
import { IMG, thumb } from "../img";
import type { DramaLite, HistoryItem } from "../types";

export type MyListTab = "history" | "favorites" | "likes";
interface Props {
  locale: Locale;
  initialTab?: MyListTab;
  onOpen: (dramaId: number, episodeId?: number) => void;
  onTab: (tab: MyListTab) => void;
}
interface ListCard { drama: DramaLite; history?: HistoryItem }
interface Snapshot {
  tab: MyListTab;
  cards: ListCard[];
  total: number;
  nextOffset: number;
  hasMore: boolean;
}
const PAGE_SIZE = 30;
const TABS: MyListTab[] = ["favorites", "history", "likes"];

const COPY = {
  zh: {
    title: "我的追剧", favorites: "收藏", history: "历史", likes: "点赞",
    guest: "登录后收藏短剧，随时继续观看。", login: "登录", loading: "正在加载…",
    error: "加载失败，请重试。", retry: "重试", more: "加载更多",
    favoritesEmpty: "还没有收藏的短剧", historyEmpty: "还没有观看记录", likesEmpty: "还没有点赞的短剧",
    emptyHint: "发现喜欢的短剧后，它们会出现在这里。", continue: "继续观看",
  },
  en: {
    title: "My List", favorites: "Saved", history: "History", likes: "Liked",
    guest: "Log in to save dramas and pick up where you left off.", login: "Log in", loading: "Loading…",
    error: "Could not load your list. Please try again.", retry: "Try again", more: "Load more",
    favoritesEmpty: "No saved dramas yet", historyEmpty: "No watch history yet", likesEmpty: "No liked dramas yet",
    emptyHint: "The dramas you discover and enjoy will appear here.", continue: "Continue watching",
  },
};

/** Private snapshots are discarded synchronously whenever the session changes. */
export function MyListPage(props: Props) {
  const { status } = useAuth();
  const ownerToken = authToken();
  if (status === "loading") return <div className="center" role="status">{COPY[props.locale].loading}</div>;
  return <OwnedMyList key={ownerToken ?? "guest"} {...props} ownerToken={ownerToken} signedIn={status === "user" && Boolean(ownerToken)} />;
}

function OwnedMyList({ locale, initialTab = "favorites", onOpen, onTab, ownerToken, signedIn }: Props & {
  ownerToken: string | null;
  signedIn: boolean;
}) {
  const tab = initialTab;
  const copy = COPY[locale];
  const { requireAuth } = useAuth();
  const mounted = useRef(false);
  const generation = useRef(0);
  const loadingRef = useRef(false);
  const snapshotRef = useRef<Snapshot | null>(null);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [requestState, setRequestState] = useState<{ tab: MyListTab; status: "loading" | "ready" | "error" }>({ tab, status: "loading" });
  const ownsPage = () => mounted.current && authToken() === ownerToken;

  const load = useCallback(async (append = false) => {
    if (!mounted.current || !signedIn || !ownerToken || authToken() !== ownerToken || loadingRef.current) return;
    const previous = append && snapshotRef.current?.tab === tab ? snapshotRef.current : null;
    if (append && !previous?.hasMore) return;
    const request = ++generation.current;
    const ownsRequest = () => mounted.current && request === generation.current && authToken() === ownerToken;
    loadingRef.current = true;
    setRequestState({ tab, status: "loading" });
    try {
      let next: Snapshot;
      if (tab === "favorites") {
        const offset = previous?.nextOffset ?? 0;
        const data = await getFavorites(PAGE_SIZE, offset);
        const cards: ListCard[] = data.items.map((drama) => ({ drama }));
        const nextOffset = offset + data.items.length;
        next = { tab, cards: uniqueCards([...(previous?.cards ?? []), ...cards]), total: data.total,
          nextOffset, hasMore: data.items.length > 0 && nextOffset < data.total };
      } else if (tab === "history") {
        const offset = previous?.nextOffset ?? 0;
        const data = await getHistory(PAGE_SIZE, offset);
        const cards = latestHistoryCards([
          ...(previous?.cards ?? []),
          ...data.items.map((history) => ({ drama: history.drama, history })),
        ]);
        // The server total counts episode progress rows, not distinct dramas.
        const nextOffset = offset + data.items.length;
        const totalRows = typeof data.total === "number" && Number.isFinite(data.total) && data.total >= 0 ? data.total : null;
        next = { tab, cards, total: cards.length, nextOffset,
          hasMore: data.items.length > 0 && (totalRows == null ? data.items.length >= PAGE_SIZE : nextOffset < totalRows) };
      } else {
        const data = await getLikes();
        const cards = uniqueCards(data.items.flatMap((item) => item.drama ? [{ drama: item.drama }] : []));
        // The endpoint also counts non-drama reactions, which have no drama card.
        next = { tab, cards, total: cards.length, nextOffset: data.items.length, hasMore: false };
      }
      if (!ownsRequest()) return;
      snapshotRef.current = next;
      setSnapshot(next);
      setRequestState({ tab, status: "ready" });
    } catch {
      if (ownsRequest()) setRequestState({ tab, status: "error" });
    } finally {
      if (ownsRequest()) loadingRef.current = false;
    }
  }, [ownerToken, signedIn, tab]);

  useEffect(() => {
    mounted.current = true;
    snapshotRef.current = null;
    setSnapshot(null);
    void load();
    return () => {
      mounted.current = false;
      generation.current++;
      loadingRef.current = false;
    };
  }, [load]);

  const visible = snapshot?.tab === tab ? snapshot : null;
  const state = requestState.tab === tab ? requestState.status : "loading";
  const chooseTab = (next: MyListTab) => { if (ownsPage()) onTab(next); };
  const login = () => {
    if (ownsPage()) requireAuth(() => undefined);
  };

  return (
    <div className="mylist-page">
      {signedIn && <div className="mylist-tabs" role="tablist" aria-label={copy.title}>
        {TABS.map((item) => (
          <button key={item} type="button" role="tab" id={"mylist-tab-" + item}
            data-list-tab={item} aria-selected={item === tab} aria-controls="mylist-panel"
            tabIndex={item === tab ? 0 : -1} className={item === tab ? "on" : ""}
            onClick={() => chooseTab(item)}
            onKeyDown={(event) => {
              const index = TABS.indexOf(item);
              const next = event.key === "ArrowRight" ? TABS[(index + 1) % TABS.length]
                : event.key === "ArrowLeft" ? TABS[(index + TABS.length - 1) % TABS.length]
                  : event.key === "Home" ? TABS[0] : event.key === "End" ? TABS[TABS.length - 1] : null;
              if (!next) return;
              event.preventDefault();
              chooseTab(next);
              event.currentTarget.parentElement?.querySelector<HTMLButtonElement>('[data-list-tab="' + next + '"]')?.focus();
            }}>
            {copy[item]}
          </button>
        ))}
      </div>}
      <section className="mylist-panel" role={signedIn ? "tabpanel" : undefined} id="mylist-panel"
        aria-labelledby={signedIn ? "mylist-tab-" + tab : undefined} aria-busy={signedIn && state === "loading"} tabIndex={signedIn ? 0 : undefined}>
        {!signedIn ? (
          <div className="mylist-empty">
            <IconBookmark size={48} />
            <p>{copy.guest}</p>
            <button type="button" className="mylist-login" onClick={login}>{copy.login}</button>
          </div>
        ) : !visible && state === "loading" ? (
          <div className="center" role="status">{copy.loading}</div>
        ) : !visible && state === "error" ? (
          <div className="mylist-empty" role="alert">
            <p>{copy.error}</p>
            <button type="button" className="mylist-login" onClick={() => void load()}>{copy.retry}</button>
          </div>
        ) : visible?.cards.length === 0 ? (
          <div className="mylist-empty">
            <IconBookmark size={48} />
            <p>{tab === "favorites" ? copy.favoritesEmpty : tab === "history" ? copy.historyEmpty : copy.likesEmpty}</p>
            <span>{copy.emptyHint}</span>
          </div>
        ) : (
          <>
            {visible && <p className="mylist-count">{copy[tab]} · {formatCount(visible.total, locale)}</p>}
            <div className="grid3 me-grid mylist-grid">
              {visible?.cards.map(({ drama, history }) => (
                <button key={drama.id} type="button" className="card me-tile mylist-card"
                  aria-label={history ? copy.continue + ": " + drama.title + " · " + t(locale, "episode_short", { number: history.episode_number }) : drama.title}
                  onClick={() => { if (ownsPage()) onOpen(drama.id, history?.episode_id); }}>
                  <div className="poster">
                    <span className="poster-media">
                      {drama.cover_url && <img src={thumb(drama.cover_url, IMG.card)} alt="" loading="lazy" decoding="async" />}
                    </span>
                    {history && <span className="me-tile-badge">{t(locale, "episode_short", { number: history.episode_number })}</span>}
                    <div className="poster-gradient" />
                  </div>
                  <p className="card-title">{drama.title}</p>
                  <span className="me-tile-meta">{history ? copy.continue : t(locale, "me_ep_total", { count: drama.episode_count })}</span>
                </button>
              ))}
            </div>
            {visible && state === "error" && <p role="alert">{copy.error}</p>}
            {visible?.hasMore && (
              <button type="button" className="mylist-login mylist-more" disabled={state === "loading"}
                onClick={() => void load(true)}>{state === "loading" ? copy.loading : state === "error" ? copy.retry : copy.more}</button>
            )}
          </>
        )}
      </section>
    </div>
  );
}

function uniqueCards(cards: ListCard[]): ListCard[] {
  const seen = new Set<number>();
  return cards.filter(({ drama }) => {
    if (seen.has(drama.id)) return false;
    seen.add(drama.id);
    return true;
  });
}

/** Pagination advances by raw rows, while each drama resumes its most recent watch. */
function latestHistoryCards(cards: ListCard[]): ListCard[] {
  const latest = new Map<number, ListCard>();
  const time = (card: ListCard) => {
    const parsed = Date.parse(card.history?.updated_at ?? "");
    return Number.isFinite(parsed) ? parsed : 0;
  };
  for (const card of cards) {
    const existing = latest.get(card.drama.id);
    if (!existing || time(card) > time(existing) ||
      (time(card) === time(existing) && (card.history?.episode_id ?? 0) > (existing.history?.episode_id ?? 0))) {
      latest.set(card.drama.id, card);
    }
  }
  return [...latest.values()].sort((left, right) => time(right) - time(left) ||
    (right.history?.episode_id ?? 0) - (left.history?.episode_id ?? 0));
}

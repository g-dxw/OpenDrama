import { useEffect, useRef, useState } from "react";
import {
  get,
  post,
  send,
  errorMessage,
  money,
  type CatalogDrama,
  type HistoryItem,
  type LedgerEntry,
  type InboxItem,
  type Order,
} from "./consumer-api";
import { useAuth } from "./auth";
import { Cards, Empty, Failure, Icon, Loading } from "./components";
const tabs = [
  ["overview", "Profile"],
  ["favorites", "My list"],
  ["history", "Continue watching"],
  ["orders", "Orders"],
  ["ledger", "Coin history"],
  ["messages", "Notifications"],
];
export function Account({ section = "overview" }: { section?: string }) {
  const { user, status, openLogin, refresh, logout } = useAuth();
  const [data, setData] = useState<unknown>(null),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(false),
    [offset, setOffset] = useState(0),
    [name, setName] = useState(user?.display_name || ""),
    [bio, setBio] = useState(user?.bio || ""),
    [saved, setSaved] = useState(""),
    [busy, setBusy] = useState(false);
  useEffect(() => {
    setName(user?.display_name || "");
    setBio(user?.bio || "");
  }, [user]);
  useEffect(() => setOffset(0), [section]);
  const request = useRef(0);
  const load = () => {
    if (status !== "user" || section === "overview") return;
    const generation = ++request.current;
    setData(null);
    setLoading(true);
    setError("");
    const endpoints: Record<string, string> = {
      favorites: "/v1/me/favorites?limit=48&offset=" + offset,
      history: "/v1/me/history?limit=20&offset=" + offset,
      orders: "/v1/commerce/orders?limit=20&offset=" + offset,
      ledger: "/v1/me/ledger",
      messages: "/v1/me/messages",
    };
    if (!endpoints[section]) {
      setLoading(false);
      return;
    }
    get(endpoints[section])
      .then((result) => {
        if (generation === request.current) setData(result);
      })
      .catch((e) => {
        if (generation === request.current) setError(errorMessage(e));
      })
      .finally(() => {
        if (generation === request.current) setLoading(false);
      });
  };
  useEffect(() => {
    load();
    return () => {
      request.current++;
    };
  }, [section, status, offset]);
  if (status === "loading") return <Loading />;
  if (!user)
    return (
      <div className="page-content empty">
        <Icon name="user" size={44} />
        <h1>Your stories, all in one place.</h1>
        <p className="muted">
          Sign in to find your saved dramas, watch history, and purchases.
        </p>
        <button className="button primary" onClick={openLogin}>
          Sign in
        </button>
      </div>
    );
  const result = data as {
    items?: unknown[];
    has_more?: boolean;
    total?: number;
    unread?: number;
  } | null;
  return (
    <div className="page-content account-layout">
      <aside className="account-nav">
        <div className="avatar">
          {user.display_name.slice(0, 1).toUpperCase()}
        </div>
        <h2>{user.display_name}</h2>
        <p className="muted">ID {user.public_id}</p>
        <div className="wallet">
          <Icon name="coin" />
          <strong>{user.coins.toLocaleString()}</strong>
          <span>coins</span>
          <a href="#coins">Top up</a>
        </div>
        <nav aria-label="Your account">
          {tabs.map(([id, label]) => (
            <a
              className={section === id ? "active" : ""}
              href={"#account/" + id}
              key={id}
            >
              {label}
              <Icon name="arrow" size={16} />
            </a>
          ))}
          <a href="#support">
            Help & support
            <Icon name="arrow" size={16} />
          </a>
        </nav>
        <button className="text-button" onClick={logout}>
          Sign out
        </button>
      </aside>
      <section className="account-main">
        <div className="section-heading">
          <h1>{tabs.find(([id]) => id === section)?.[1] || "Your account"}</h1>
        </div>
        {error && <Failure message={error} retry={load} />}{" "}
        {loading ? (
          <Loading />
        ) : section === "overview" ? (
          <>
            <div className="membership-banner">
              <Icon name="crown" />
              <div>
                <strong>
                  {user.membership?.active
                    ? "Your membership is active"
                    : "Make room for more stories"}
                </strong>
                <p>
                  {user.membership?.until
                    ? "Valid until " +
                      new Date(user.membership.until).toLocaleDateString()
                    : "Explore memberships and watch more."}
                </p>
              </div>
              <a className="button secondary" href="#premium">
                Manage membership
              </a>
            </div>
            <form
              className="form profile-form"
              onSubmit={(e) => {
                e.preventDefault();
                setBusy(true);
                setError("");
                setSaved("");
                send("PATCH", "/v1/me", { display_name: name, bio })
                  .then(() => refresh())
                  .then(() => setSaved("Profile saved."))
                  .catch((e) => setError(errorMessage(e)))
                  .finally(() => setBusy(false));
              }}
            >
              <label>
                Display name
                <input
                  required
                  maxLength={64}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </label>
              <label>
                Email
                <input value={user.email || "Not provided"} readOnly />
              </label>
              <label>
                About you
                <textarea
                  maxLength={500}
                  rows={4}
                  value={bio}
                  onChange={(e) => setBio(e.target.value)}
                />
              </label>
              <button className="button primary" disabled={busy}>
                {busy ? "Saving…" : "Save changes"}
              </button>
              {saved && <p role="status">{saved}</p>}
            </form>
          </>
        ) : section === "favorites" ? (
          <Cards items={(result?.items ?? []) as CatalogDrama[]} />
        ) : section === "history" ? (
          (result?.items ?? []).length ? (
            <div className="history-list">
              {((result?.items ?? []) as HistoryItem[]).map((item) => (
                <a
                  className="history-item"
                  key={item.episode_id}
                  href={
                    "#watch/" + item.drama.id + "?episode=" + item.episode_id
                  }
                >
                  {item.drama.cover_url && (
                    <img src={item.drama.cover_url} alt="" />
                  )}
                  <div>
                    <h3>{item.drama.title}</h3>
                    <p>
                      Episode {item.episode_number} ·{" "}
                      {Math.floor(item.position_seconds / 60)}:
                      {String(item.position_seconds % 60).padStart(2, "0")}{" "}
                      watched
                    </p>
                    <progress
                      max={Math.max(item.duration_seconds, 1)}
                      value={item.position_seconds}
                    />
                  </div>
                  <Icon name="play" />
                </a>
              ))}
            </div>
          ) : (
            <Empty message="Start a drama and pick up where you left off here." />
          )
        ) : section === "orders" ? (
          <>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Order</th>
                    <th>Product</th>
                    <th>Total</th>
                    <th>Status</th>
                    <th>Date</th>
                  </tr>
                </thead>
                <tbody>
                  {((result?.items ?? []) as Order[]).map((order) => (
                    <tr key={order.order_no}>
                      <td>{order.order_no}</td>
                      <td>
                        {order.product_name || order.product_code}
                        {order.coins ? " · " + order.coins + " coins" : ""}
                      </td>
                      <td>{money(order.amount_cents, order.currency)}</td>
                      <td>
                        <span className="status-tag">{order.status}</span>
                      </td>
                      <td>{new Date(order.created_at).toLocaleDateString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!result?.items?.length && (
              <Empty message="Your purchases will appear here." />
            )}
            <div className="pagination">
              <button
                className="button secondary"
                disabled={!offset}
                onClick={() => setOffset(Math.max(0, offset - 20))}
              >
                Previous
              </button>
              <button
                className="button secondary"
                disabled={!result?.has_more}
                onClick={() => setOffset(offset + 20)}
              >
                Next
              </button>
            </div>
            <p className="muted">
              Need help with a payment? <a href="#support">Contact support</a>.
            </p>
          </>
        ) : section === "ledger" ? (
          <>
            <div className="ledger-list">
              {((result?.items ?? []) as LedgerEntry[]).map((item, index) => (
                <article key={index}>
                  <div>
                    <strong>{item.biz_type.replace(/_/g, " ")}</strong>
                    <p>{new Date(item.created_at).toLocaleString()}</p>
                  </div>
                  <span className={item.amount > 0 ? "positive" : ""}>
                    {item.amount > 0 ? "+" : ""}
                    {item.amount} coins
                    <br />
                    <small>Balance {item.balance_after}</small>
                  </span>
                </article>
              ))}
            </div>
            {!result?.items?.length && (
              <Empty message="Your coin transactions will appear here." />
            )}
          </>
        ) : section === "messages" ? (
          <>
            {Boolean(result?.unread) && (
              <button
                className="button secondary"
                onClick={() => {
                  post("/v1/me/messages/read", {})
                    .then(load)
                    .catch((e) => setError(errorMessage(e)));
                }}
              >
                Mark all as read
              </button>
            )}
            <div className="notifications">
              {((result?.items ?? []) as InboxItem[]).map((item) => (
                <article
                  key={item.id}
                  className={item.read ? "read" : "unread"}
                >
                  <h3>{item.title}</h3>
                  <p>{item.body}</p>
                </article>
              ))}
            </div>
            {!result?.items?.length && (
              <Empty message="You're all caught up." />
            )}
          </>
        ) : (
          <Empty />
        )}
        {!loading &&
          ["favorites", "history"].includes(section) &&
          Number(result?.total) > (section === "favorites" ? 48 : 20) && (
            <div className="pagination">
              <button
                className="button secondary"
                disabled={!offset}
                onClick={() =>
                  setOffset(
                    Math.max(0, offset - (section === "favorites" ? 48 : 20)),
                  )
                }
              >
                Previous
              </button>
              <span>
                {offset + 1}–
                {Math.min(
                  offset + (section === "favorites" ? 48 : 20),
                  result?.total ?? 0,
                )}{" "}
                of {result?.total}
              </span>
              <button
                className="button secondary"
                disabled={
                  offset + (section === "favorites" ? 48 : 20) >=
                  (result?.total ?? 0)
                }
                onClick={() =>
                  setOffset(offset + (section === "favorites" ? 48 : 20))
                }
              >
                Next
              </button>
            </div>
          )}
      </section>
    </div>
  );
}
interface DailyTasks {
  balance: number;
  checkin: { checked_today: boolean; streak: number; amount: number };
  items: Array<{
    code: string;
    name: string;
    description: string;
    amount: number;
    progress: number;
    target: number;
    claimed: boolean;
  }>;
}
export function Rewards() {
  const { status, requireAuth, refresh } = useAuth();
  const [data, setData] = useState<DailyTasks | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState("");
  const load = () => {
    get<DailyTasks>(status === "user" ? "/v1/me/tasks" : "/v1/tasks")
      .then(setData)
      .catch((e) => setError(errorMessage(e)));
  };
  useEffect(() => {
    if (status !== "loading") load();
  }, [status]);
  async function claim(code: string) {
    setBusy(code);
    setError("");
    try {
      await post(
        code === "checkin"
          ? "/v1/me/checkin"
          : "/v1/me/tasks/" + code + "/claim",
        {},
      );
      await refresh();
      load();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy("");
    }
  }
  return (
    <div className="page-content">
      <div className="page-title">
        <span className="eyebrow">Little rewards, more stories</span>
        <h1>Your daily rewards</h1>
        <p className="muted">
          Earn coins through the activities below. Your progress is updated by
          JJTV.
        </p>
      </div>
      {error && <Failure message={error} retry={load} />}{" "}
      {!data ? (
        <Loading />
      ) : (
        <>
          <div className="rewards-balance">
            <Icon name="coin" size={36} />
            <div>
              <strong>{data.balance.toLocaleString()}</strong>
              <span> coins in your wallet</span>
            </div>
            <a className="button secondary" href="#coins">
              Get more coins
            </a>
          </div>
          <article className="reward-card">
            <div>
              <h2>Daily check-in</h2>
              <p>
                {data.checkin.streak} day streak · {data.checkin.amount} coins
              </p>
            </div>
            <button
              className="button primary"
              disabled={!!busy || data.checkin.checked_today}
              onClick={() =>
                requireAuth(() => {
                  void claim("checkin");
                })
              }
            >
              {data.checkin.checked_today ? "Checked in" : "Check in"}
            </button>
          </article>
          {data.items.map((task) => (
            <article className="reward-card" key={task.code}>
              <div>
                <h2>{task.name}</h2>
                <p>{task.description}</p>
                <progress max={task.target} value={task.progress} />
                <p>
                  {task.progress}/{task.target} · +{task.amount} coins
                </p>
              </div>
              {task.claimed ? (
                <span>Claimed</span>
              ) : task.progress >= task.target ? (
                <button
                  className="button primary"
                  disabled={!!busy}
                  onClick={() =>
                    requireAuth(() => {
                      void claim(task.code);
                    })
                  }
                >
                  Claim reward
                </button>
              ) : (
                <a className="button secondary" href="#browse">
                  Explore dramas
                </a>
              )}
            </article>
          ))}
        </>
      )}
    </div>
  );
}

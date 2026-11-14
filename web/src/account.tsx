import { useEffect, useRef, useState } from "react";
import {
  authToken,
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
import {
  DramaCard,
  Empty,
  Failure,
  Icon,
  Loading,
  Modal,
  StatusTag,
} from "./components";
const tabs = [
  ["history", "History"],
  ["favorites", "Saved"],
  ["likes", "Liked"],
];
interface AccountResult {
  items?: unknown[];
  has_more?: boolean;
  total?: number;
  unread?: number;
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
    metric?: string;
  }>;
}
export function Account({ section = "history" }: { section?: string }) {
  const { user, status, openLogin, requireAuth, refresh, logout } = useAuth();
  const [data, setData] = useState<AccountResult | null>(null),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(false),
    [offset, setOffset] = useState(0),
    [name, setName] = useState(user?.display_name || ""),
    [bio, setBio] = useState(user?.bio || ""),
    [saved, setSaved] = useState(""),
    [busy, setBusy] = useState(""),
    [checked, setChecked] = useState(false);
  const request = useRef(0);
  const library = tabs.some(([id]) => id === section);
  const pageSize = section === "favorites" ? 48 : 20;
  useEffect(() => {
    setName(user?.display_name || "");
    setBio(user?.bio || "");
  }, [user]);
  useEffect(() => {
    if (status === "loading") return;
    let active = true;
    get<DailyTasks>(authToken() ? "/v1/me/tasks" : "/v1/tasks")
      .then((result) => {
        if (active) setChecked(result.checkin.checked_today);
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [status, user?.id]);
  const load = () => {
    if (!authToken() || section === "overview") return;
    const generation = ++request.current;
    setLoading(true);
    setError("");
    setData(null);
    const endpoints: Record<string, string> = {
      favorites: "/v1/me/favorites?limit=48&offset=" + offset,
      history: "/v1/me/history?limit=20&offset=" + offset,
      likes: "/v1/me/lists/likes",
      orders: "/v1/commerce/orders?limit=20&offset=" + offset,
      ledger: "/v1/me/ledger",
      messages: "/v1/me/messages",
    };
    if (!endpoints[section]) {
      setLoading(false);
      return;
    }
    get<AccountResult>(endpoints[section])
      .then((result) => {
        if (generation !== request.current) return;
        const count = result.total;
        if (count != null && offset >= count && offset > 0) {
          setOffset(Math.max(0, Math.floor((count - 1) / pageSize) * pageSize));
          return;
        }
        setData(result);
      })
      .catch((e) => {
        if (generation === request.current) setError(errorMessage(e));
      })
      .finally(() => {
        if (generation === request.current) setLoading(false);
      });
  };
  useEffect(() => {
    setOffset(0);
  }, [section]);
  useEffect(() => {
    load();
    return () => {
      request.current++;
    };
  }, [section, status, offset, user?.id]);
  async function checkin() {
    setBusy("checkin");
    setError("");
    try {
      await post("/v1/me/checkin", {});
      setChecked(true);
      await refresh();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy("");
    }
  }
  async function removeSaved(drama: CatalogDrama) {
    setBusy("saved:" + drama.id);
    setError("");
    try {
      await post("/v1/reactions", {
        target_type: "drama",
        target_id: drama.id,
        kind: "favorite",
        remove: true,
      });
      load();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy("");
    }
  }
  const guest = status !== "user" || !user;
  const identity = user?.public_id || (user?.id ? "#" + user.id : "");
  const liked = (data?.items ?? []).flatMap((item) => {
    const row = item as { drama?: CatalogDrama; target_type?: string };
    return row.drama && row.target_type === "drama" ? [row.drama] : [];
  });
  return (
    <div className="page-content member-page">
      <aside className="member-sidebar">
        <div className="member-identity">
          <div className="avatar">
            {user?.avatar_url ? (
              <img src={user.avatar_url} alt="" />
            ) : (
              (user?.display_name || "J").slice(0, 1).toUpperCase()
            )}
          </div>
          <div>
            <h1>{guest ? "Sign in to JJTV" : user.display_name}</h1>
            <p>
              {guest
                ? "Likes, favorites and watch history follow your account."
                : identity
                  ? "ID " + identity
                  : "Your JJTV account"}
            </p>
          </div>
        </div>
        {guest ? (
          <button
            className="button primary full-width"
            onClick={openLogin}
            disabled={status === "loading"}
          >
            {status === "loading" ? "Restoring account…" : "Sign in"}
          </button>
        ) : (
          <a className="member-edit" href="#account/overview">
            <Icon name="user" size={14} />
            Edit profile
          </a>
        )}
        <a className="member-premium" href="#price">
          <span className="member-premium-icon">
            <Icon name="crown" size={21} />
          </span>
          <div>
            <strong>
              {user?.membership?.active ? "Premium is active" : "JJTV Premium"}
            </strong>
            <p>
              {user?.membership?.until
                ? "Until " +
                  new Date(user.membership.until).toLocaleDateString()
                : "Find your plan and watch more."}
            </p>
          </div>
          <span>{user?.membership?.active ? "Manage" : "View plans"}</span>
        </a>
        <div className="member-quick-actions">
          <a href="#account/history">
            <span>
              <Icon name="history" />
            </span>
            <strong>History</strong>
            <small>Resume</small>
          </a>
          <a href="#account/favorites">
            <span>
              <Icon name="bookmark" />
            </span>
            <strong>My list</strong>
            <small>Saved dramas</small>
          </a>
          <button
            disabled={checked || busy === "checkin"}
            onClick={() =>
              requireAuth(() => {
                void checkin();
              })
            }
          >
            <span>
              <Icon name="calendar" />
            </span>
            <strong>Check-in</strong>
            <small>
              {busy === "checkin"
                ? "Claiming…"
                : checked
                  ? "Checked in"
                  : "Claim"}
            </small>
          </button>
        </div>
        <nav className="member-actions" aria-label="Your account">
          <a href="#rewards?return=account">
            <Icon name="gift" />
            <strong>Daily tasks</strong>
            <span>Earn coins ›</span>
          </a>
          <a href="#account/ledger">
            <Icon name="coin" />
            <strong>Wallet</strong>
            <span>{user?.coins.toLocaleString() ?? "0"} coins ›</span>
          </a>
          <a href="#account/orders">
            <Icon name="receipt" />
            <strong>Orders</strong>
            <span>View ›</span>
          </a>
          <a href="#account/messages">
            <Icon name="message" />
            <strong>Notifications</strong>
            <span>View ›</span>
          </a>
          <a href="#support">
            <Icon name="support" />
            <strong>Help & support</strong>
            <span>›</span>
          </a>
        </nav>
        {!guest && (
          <button className="text-button member-signout" onClick={logout}>
            Sign out
          </button>
        )}
      </aside>
      <section className="member-content">
        {library ? (
          <div
            className="library-tabs"
            role="navigation"
            aria-label="Your library"
          >
            {tabs.map(([id, label]) => (
              <a
                className={section === id ? "active" : ""}
                aria-current={section === id ? "page" : undefined}
                key={id}
                href={"#account/" + id}
              >
                {label}
              </a>
            ))}
          </div>
        ) : (
          <div className="member-section-heading">
            <a href="#account/history">← Your library</a>
            <h2>
              {{
                overview: "Edit profile",
                orders: "Order history",
                ledger: "Coin ledger",
                messages: "Notifications",
              }[section] || "Your account"}
            </h2>
          </div>
        )}
        {error && <Failure message={error} retry={load} />}
        {guest ? (
          <div className="member-guest-state">
            <span>
              <Icon name={library ? "star" : "user"} size={30} />
            </span>
            <h2>
              {library
                ? "Sign in to like and save dramas"
                : "Sign in to view your account"}
            </h2>
            <p>Sync your likes, favorites and watch history.</p>
            <button
              className="button secondary"
              onClick={openLogin}
              disabled={status === "loading"}
            >
              Sign in
            </button>
          </div>
        ) : loading ? (
          <Loading />
        ) : section === "overview" ? (
          <form
            className="form profile-form member-surface"
            onSubmit={(e) => {
              e.preventDefault();
              setBusy("profile");
              setError("");
              setSaved("");
              send("PATCH", "/v1/me", { display_name: name, bio })
                .then(() => refresh())
                .then(() => setSaved("Profile saved."))
                .catch((e) => setError(errorMessage(e)))
                .finally(() => setBusy(""));
            }}
          >
            <label>
              Display name
              <input
                required
                maxLength={64}
                value={name}
                disabled={!!busy}
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
                disabled={!!busy}
                onChange={(e) => setBio(e.target.value)}
              />
            </label>
            <div className="profile-save-row">
              <button className="button primary" disabled={!!busy}>
                {busy === "profile" ? "Saving…" : "Save changes"}
              </button>
              {saved && <span role="status">{saved}</span>}
            </div>
          </form>
        ) : section === "favorites" ? (
          (data?.items ?? []).length ? (
            <div className="drama-grid saved-grid">
              {((data?.items ?? []) as CatalogDrama[]).map((drama) => (
                <div className="saved-card" key={drama.id}>
                  <DramaCard drama={drama} />
                  <button
                    className="saved-remove"
                    aria-label={"Remove " + drama.title + " from saved"}
                    disabled={!!busy}
                    onClick={() => void removeSaved(drama)}
                  >
                    <Icon name="close" size={15} />
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <Empty message="Save a drama from its watch page and find it here." />
          )
        ) : section === "likes" ? (
          liked.length ? (
            <div className="drama-grid saved-grid">
              {liked.map((drama) => (
                <DramaCard key={drama.id} drama={drama} />
              ))}
            </div>
          ) : (
            <Empty message="The dramas you like will appear here." />
          )
        ) : section === "history" ? (
          (data?.items ?? []).length ? (
            <div className="history-grid">
              {((data?.items ?? []) as HistoryItem[]).map((item) => (
                <a
                  className="history-item history-card"
                  key={item.episode_id}
                  href={
                    "#watch/" + item.drama.id + "?episode=" + item.episode_id
                  }
                >
                  <div className="history-cover">
                    {item.drama.cover_url && (
                      <img src={item.drama.cover_url} alt="" />
                    )}
                    <span className="history-episode">
                      Episode {item.episode_number}
                    </span>
                    <span className="history-resume">
                      <Icon name="play" />
                    </span>
                    <progress
                      max={Math.max(item.duration_seconds, 1)}
                      value={item.position_seconds}
                    />
                  </div>
                  <h3>{item.drama.title}</h3>
                  <p>
                    {Math.floor(item.position_seconds / 60)}:
                    {String(item.position_seconds % 60).padStart(2, "0")}{" "}
                    watched
                  </p>
                </a>
              ))}
            </div>
          ) : (
            <Empty message="Start a drama and pick up where you left off here." />
          )
        ) : section === "orders" ? (
          <>
            <div className="table-scroll member-surface">
              <table className="order-table">
                <thead>
                  <tr>
                    <th>Order / Date</th>
                    <th>Product</th>
                    <th>Amount</th>
                    <th>Status</th>
                    <th>
                      <span className="sr-only">Order actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {((data?.items ?? []) as Order[]).map((order) => (
                    <tr key={order.order_no}>
                      <td>
                        <strong>{order.order_no}</strong>
                        <small>
                          {new Date(order.created_at).toLocaleString()}
                        </small>
                      </td>
                      <td>
                        {order.product_name || order.product_code}
                        <small>
                          {order.product_type === "coin_pack"
                            ? (order.coins || 0).toLocaleString() + " coins"
                            : order.billing_mode === "subscription"
                              ? "Membership subscription"
                              : "Membership pass"}
                        </small>
                      </td>
                      <td>{money(order.amount_cents, order.currency)}</td>
                      <td>
                        <StatusTag status={order.status} />
                      </td>
                      <td>
                        <a
                          className="order-help"
                          href={
                            "#support?new=1&order=" +
                            encodeURIComponent(order.order_no)
                          }
                        >
                          Get help
                        </a>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!data?.items?.length && (
                <Empty message="Your purchases will appear here." />
              )}
            </div>
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
                disabled={!data?.has_more}
                onClick={() => setOffset(offset + 20)}
              >
                Next
              </button>
            </div>
            <p className="section-footnote">
              Payment confirmation and refunds are reflected in the order
              status.
            </p>
          </>
        ) : section === "ledger" ? (
          <div className="member-surface">
            <div className="ledger-summary">
              <Icon name="coin" size={24} />
              <div>
                <strong>{user.coins.toLocaleString()} coins</strong>
                <p>Available balance</p>
              </div>
              <a className="button secondary" href="#coins">
                Buy coins
              </a>
            </div>
            <div className="ledger-list">
              {((data?.items ?? []) as LedgerEntry[]).map((item, index) => (
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
            {!data?.items?.length && (
              <Empty message="Your coin transactions will appear here." />
            )}
          </div>
        ) : section === "messages" ? (
          <>
            {Boolean(data?.unread) && (
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
              {((data?.items ?? []) as InboxItem[]).map((item) => (
                <article
                  key={item.id}
                  className={item.read ? "read" : "unread"}
                >
                  <h3>{item.title}</h3>
                  <p>{item.body}</p>
                </article>
              ))}
            </div>
            {!data?.items?.length && <Empty message="You're all caught up." />}
          </>
        ) : (
          <Empty />
        )}
        {!loading &&
          ["favorites", "history"].includes(section) &&
          Number(data?.total) > pageSize && (
            <div className="pagination">
              <button
                className="button secondary"
                disabled={!offset}
                onClick={() => setOffset(Math.max(0, offset - pageSize))}
              >
                Previous
              </button>
              <span>
                {offset + 1}–{Math.min(offset + pageSize, data?.total ?? 0)} of{" "}
                {data?.total}
              </span>
              <button
                className="button secondary"
                disabled={offset + pageSize >= (data?.total ?? 0)}
                onClick={() => setOffset(offset + pageSize)}
              >
                Next
              </button>
            </div>
          )}
      </section>
    </div>
  );
}
export function Rewards({
  panel = false,
  onClose,
}: { panel?: boolean; onClose?: () => void } = {}) {
  const { status, user, requireAuth, refresh } = useAuth();
  const [data, setData] = useState<DailyTasks | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(""),
    [notice, setNotice] = useState("");
  const generation = useRef(0),
    submitting = useRef(false);
  const load = () => {
    const request = ++generation.current;
    return get<DailyTasks>(authToken() ? "/v1/me/tasks" : "/v1/tasks")
      .then((result) => {
        if (request === generation.current) {
          setData(result);
          setError("");
        }
      })
      .catch((e) => {
        if (request === generation.current) setError(errorMessage(e));
      });
  };
  useEffect(() => {
    if (status !== "loading") void load();
    return () => {
      generation.current++;
    };
  }, [status, user?.id]);
  async function claim(code: string) {
    if (submitting.current) return;
    submitting.current = true;
    setBusy(code);
    setError("");
    setNotice("");
    try {
      const result = await post<{ amount: number; replayed?: boolean }>(
        code === "checkin"
          ? "/v1/me/checkin"
          : "/v1/me/tasks/" + code + "/claim",
        {},
      );
      await refresh();
      await load();
      setNotice(
        result.replayed
          ? "This reward has already been claimed."
          : "You earned " + result.amount + " coins.",
      );
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      submitting.current = false;
      setBusy("");
    }
  }
  const body = (
    <>
      {!data ? (
        error ? (
          <Failure message={error} retry={() => void load()} />
        ) : (
          <Loading />
        )
      ) : (
        <>
          <div className="tasks-intro">
            <p>
              Small actions, coins every day. Track your progress and claim
              rewards here.
            </p>
            <span>
              <Icon name="coin" size={14} />
              {data.balance.toLocaleString()} coins
            </span>
          </div>
          {error && <Failure message={error} retry={() => void load()} />}
          {notice && (
            <p className="task-notice" role="status">
              {notice}
            </p>
          )}
          <article className="task-row task-checkin">
            <span className="task-icon">
              <Icon name="calendar" size={22} />
            </span>
            <div>
              <h3>Day {data.checkin.streak} streak</h3>
              <p>
                {data.checkin.checked_today
                  ? "Checked in today"
                  : "Check in for +" + data.checkin.amount + " coins"}
              </p>
            </div>
            <button
              className="button gold"
              disabled={!!busy || data.checkin.checked_today}
              onClick={() =>
                requireAuth(() => {
                  void claim("checkin");
                })
              }
            >
              {busy === "checkin"
                ? "Claiming…"
                : data.checkin.checked_today
                  ? "Checked in"
                  : "Check in"}
            </button>
          </article>
          {data.items.map((task) => (
            <article className="task-row" key={task.code}>
              <span className={"task-icon task-" + (task.metric || "complete")}>
                <Icon
                  name={
                    task.metric === "like"
                      ? "heart"
                      : task.metric === "favorite"
                        ? "star"
                        : "play"
                  }
                  size={23}
                />
              </span>
              <div className="task-description">
                <div className="task-title">
                  <h3>{task.name}</h3>
                  <span>
                    <Icon name="coin" size={12} />+{task.amount}
                  </span>
                </div>
                <p>{task.description}</p>
                <progress
                  aria-label={task.name + " progress"}
                  max={Math.max(task.target, 1)}
                  value={task.progress}
                />
                <small>
                  {task.progress}/{task.target}
                </small>
              </div>
              {task.claimed ? (
                <span className="task-claimed">
                  <Icon name="check" size={16} />
                  Claimed
                </span>
              ) : task.progress >= task.target ? (
                <button
                  className="button gold"
                  disabled={!!busy}
                  onClick={() =>
                    requireAuth(() => {
                      void claim(task.code);
                    })
                  }
                >
                  {busy === task.code ? "Claiming…" : "Claim"}
                </button>
              ) : (
                <button
                  className="button secondary"
                  disabled={!!busy}
                  onClick={() =>
                    requireAuth(() => {
                      window.location.hash =
                        task.metric === "complete" ? "foryou" : "browse";
                    })
                  }
                >
                  Go
                </button>
              )}
            </article>
          ))}
          <div className="task-topup">
            <div>
              <h3>Need coins now?</h3>
              <p>Buy a pack and keep your story going.</p>
            </div>
            <a className="button gold" href="#coins">
              Buy coins
            </a>
          </div>
        </>
      )}
    </>
  );
  return panel ? (
    <Modal
      title="Daily tasks"
      className="rewards-modal"
      onClose={
        onClose ??
        (() => {
          window.location.hash = "account";
        })
      }
    >
      {body}
    </Modal>
  ) : (
    <div className="page-content tasks-page">
      <div className="member-section-heading">
        <h1>Daily tasks</h1>
        <p className="muted">Your daily rewards</p>
      </div>
      <div className="tasks-panel">{body}</div>
    </div>
  );
}

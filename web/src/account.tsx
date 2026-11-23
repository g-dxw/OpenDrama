import { siteName, siteFeature } from "../../shared/site";
import { useLocale } from "./locale";
import { useEffect, useRef, useState } from "react";
import {
  authToken,
  get,
  post,
  send,
  errorMessage,
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
  const { t, number, date, money } = useLocale();
  const tabs = [
    ["history", t("pages.history_16d2b3")],
    ["favorites", t("pages.saved_248336")],
    ["likes", t("pages.liked_1ce3a9")],
  ];
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
      likes: "/v1/me/lists/like",
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
            <h1>{guest ? t("pages.sign_in_to_jjtv_a86a91") : user.display_name}</h1>
            <p>
              {guest
                ? t("pages.likes_favorites_and_watch_history_follow_your_account_46bfde")
                : identity
                  ? "ID " + identity
                  : t("pages.your_jjtv_account_946480")}
            </p>
          </div>
        </div>
        {guest ? (
          <button
            className="button primary full-width"
            onClick={openLogin}
            disabled={status === "loading"}
          >
            {status === "loading" ? t("pages.restoring_account_cc868a") : t("pages.sign_in_b6d422")}
          </button>
        ) : (
          <a className="member-edit" href="#account/overview">
            <Icon name="user" size={14} />
            {t("pages.edit_profile_dfd87b")}
          </a>
        )}
        {(siteFeature("membership") || user?.membership?.active) && <a className="member-premium" href="#price">
          <span className="member-premium-icon">
            <Icon name="crown" size={21} />
          </span>
          <div>
            <strong>
              {user?.membership?.active ? t("pages.premium_is_active_42c449") : siteName()+" Premium"}
            </strong>
            <p>
              {user?.membership?.until
                ? t("pages.until_date", { date: date(user.membership.until, { dateStyle: "medium" }) })
                : t("pages.find_your_plan_and_watch_more_ca653d")}
            </p>
          </div>
          <span>{user?.membership?.active ? t("pages.manage_34e34c") : t("pages.view_plans_9a6577")}</span>
        </a>}
        <div className="member-quick-actions">
          <a href="#account/history">
            <span>
              <Icon name="history" />
            </span>
            <strong>{t("pages.history_16d2b3")}</strong>
            <small>{t("pages.resume_f1cefe")}</small>
          </a>
          <a href="#account/favorites">
            <span>
              <Icon name="bookmark" />
            </span>
            <strong>{t("pages.my_list_f2d31f")}</strong>
            <small>{t("pages.saved_dramas_c0657b")}</small>
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
            <strong>{t("pages.check_in_ec86d6")}</strong>
            <small>
              {busy === "checkin"
                ? t("pages.claiming_201714")
                : checked
                  ? t("pages.checked_in_6edf8e")
                  : t("pages.claim_803138")}
            </small>
          </button>
        </div>
        <nav className="member-actions" aria-label={t("pages.your_account_f2fc74")}>
          <a href="#rewards?return=account">
            <Icon name="gift" />
            <strong>{t("pages.daily_tasks_6ec40b")}</strong>
            <span>{t("pages.earn_coins_48c51f")}</span>
          </a>
          <a href="#account/ledger">
            <Icon name="coin" />
            <strong>{t("pages.wallet_156e13")}</strong>
            <span>{t("pages.coins_count", { count: number(user?.coins ?? 0) })} ›</span>
          </a>
          <a href="#account/orders">
            <Icon name="receipt" />
            <strong>{t("pages.orders_7442e2")}</strong>
            <span>{t("pages.view_0551cc")}</span>
          </a>
          <a href="#account/messages">
            <Icon name="message" />
            <strong>{t("pages.notifications_a274f4")}</strong>
            <span>{t("pages.view_0551cc")}</span>
          </a>
          <a href="#support">
            <Icon name="support" />
            <strong>{t("pages.help_support_47a186")}</strong>
            <span>›</span>
          </a>
        </nav>
        {!guest && (
          <button className="text-button member-signout" onClick={logout}>
            {t("pages.sign_out_c87aac")}
          </button>
        )}
      </aside>
      <section className="member-content">
        {library ? (
          <div
            className="library-tabs"
            role="navigation"
            aria-label={t("pages.your_library_035adb")}
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
            <a href="#account/history">{t("pages.your_library_92f5fd")}</a>
            <h2>
              {{
                overview: t("pages.edit_profile_dfd87b"),
                orders: t("pages.order_history_782c8b"),
                ledger: t("pages.coin_ledger_10e3cc"),
                messages: t("pages.notifications_a274f4"),
              }[section] || t("pages.your_account_f2fc74")}
            </h2>
          </div>
        )}
        {error && <Failure message={t(error)} retry={load} />}
        {guest ? (
          <div className="member-guest-state">
            <span>
              <Icon name={library ? "star" : "user"} size={30} />
            </span>
            <h2>
              {library
                ? t("pages.sign_in_to_like_and_save_dramas_646fc5")
                : t("pages.sign_in_to_view_your_account_060f9b")}
            </h2>
            <p>{t("pages.sync_your_likes_favorites_and_watch_history_2cf423")}</p>
            <button
              className="button secondary"
              onClick={openLogin}
              disabled={status === "loading"}
            >
              {t("pages.sign_in_b6d422")}
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
                .then(() => setSaved("pages.profile_saved_748f2c"))
                .catch((e) => setError(errorMessage(e)))
                .finally(() => setBusy(""));
            }}
          >
            <label>
              {t("pages.display_name_bc9e27")}
              <input
                required
                maxLength={64}
                value={name}
                disabled={!!busy}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <label>
              {t("pages.email_ce8ae9")}
              <input value={user.email || t("pages.not_provided_34fac4")} readOnly />
            </label>
            <label>
              {t("pages.about_you_4f21e0")}
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
                {busy === "profile" ? t("pages.saving_eedf6b") : t("pages.save_changes_daa6b4")}
              </button>
              {saved && <span role="status">{t(saved)}</span>}
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
                    aria-label={t("pages.remove_saved", { title: drama.title })}
                    disabled={!!busy}
                    onClick={() => void removeSaved(drama)}
                  >
                    <Icon name="close" size={15} />
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <Empty message={t("pages.save_a_drama_from_its_watch_page_and_find_it_here_88a6b9")} />
          )
        ) : section === "likes" ? (
          liked.length ? (
            <div className="drama-grid saved-grid">
              {liked.map((drama) => (
                <DramaCard key={drama.id} drama={drama} />
              ))}
            </div>
          ) : (
            <Empty message={t("pages.the_dramas_you_like_will_appear_here_d48f96")} />
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
                      {t("pages.episode_number", { number: number(item.episode_number) })}
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
                    {t("pages.watched_e49e5c")}
                  </p>
                </a>
              ))}
            </div>
          ) : (
            <Empty message={t("pages.start_a_drama_and_pick_up_where_you_left_off_here_1f6182")} />
          )
        ) : section === "orders" ? (
          <>
            <div className="table-scroll member-surface">
              <table className="order-table">
                <thead>
                  <tr>
                    <th>{t("pages.order_date_1fb756")}</th>
                    <th>{t("pages.product_deb105")}</th>
                    <th>{t("pages.amount_b2f406")}</th>
                    <th>{t("pages.status_ec53a8")}</th>
                    <th>
                      <span className="sr-only">{t("pages.order_actions_4d1f4a")}</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {((data?.items ?? []) as Order[]).map((order) => (
                    <tr key={order.order_no}>
                      <td>
                        <strong>{order.order_no}</strong>
                        <small>
                          {date(order.created_at)}
                        </small>
                      </td>
                      <td>
                        {order.product_name || order.product_code}
                        <small>
                          {order.product_type === "coin_pack"
                            ? t("pages.coins_count", { count: number(order.coins || 0) })
                            : order.billing_mode === "subscription"
                              ? t("pages.membership_subscription_7f8423")
                              : t("pages.membership_pass_9968f9")}
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
                          {t("pages.get_help_fabfc2")}
                        </a>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!data?.items?.length && (
                <Empty message={t("pages.your_purchases_will_appear_here_69c8f2")} />
              )}
            </div>
            <div className="pagination">
              <button
                className="button secondary"
                disabled={!offset}
                onClick={() => setOffset(Math.max(0, offset - 20))}
              >
                {t("pages.previous_dd1f77")}
              </button>
              <button
                className="button secondary"
                disabled={!data?.has_more}
                onClick={() => setOffset(offset + 20)}
              >
                {t("pages.next_10ac3d")}
              </button>
            </div>
            <p className="section-footnote">
              {t("pages.payment_confirmation_and_refunds_are_reflected_in_the_order_status_b96799")}
            </p>
          </>
        ) : section === "ledger" ? (
          <div className="member-surface">
            <div className="ledger-summary">
              <Icon name="coin" size={24} />
              <div>
                <strong>{t("pages.coins_count", { count: number(user.coins) })}</strong>
                <p>{t("pages.available_balance_9343d8")}</p>
              </div>
              <a className="button secondary" href="#coins">
                {t("pages.buy_coins_7bcce5")}
              </a>
            </div>
            <div className="ledger-list">
              {((data?.items ?? []) as LedgerEntry[]).map((item, index) => (
                <article key={index}>
                  <div>
                    <strong>{item.biz_type.replace(/_/g, " ")}</strong>
                    <p>{date(item.created_at)}</p>
                  </div>
                  <span className={item.amount > 0 ? "positive" : ""}>
                    {item.amount > 0 ? "+" : ""}
                    {t("pages.coins_count", { count: number(item.amount) })}
                    <br />
                    <small>{t("pages.balance_99a808")} {item.balance_after}</small>
                  </span>
                </article>
              ))}
            </div>
            {!data?.items?.length && (
              <Empty message={t("pages.your_coin_transactions_will_appear_here_b6e97c")} />
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
                {t("pages.mark_all_as_read_7542d6")}
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
            {!data?.items?.length && <Empty message={t("pages.you_re_all_caught_up_92fa5a")} />}
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
                {t("pages.previous_dd1f77")}
              </button>
              <span>
                {t("pages.range_total", { from: number(offset + 1), to: number(Math.min(offset + pageSize, data?.total ?? 0)), total: number(data?.total ?? 0) })}
              </span>
              <button
                className="button secondary"
                disabled={offset + pageSize >= (data?.total ?? 0)}
                onClick={() => setOffset(offset + pageSize)}
              >
                {t("pages.next_10ac3d")}
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
  const { t, number } = useLocale();
  const { status, user, requireAuth, refresh } = useAuth();
  const [data, setData] = useState<DailyTasks | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(""),
    [notice, setNotice] = useState<{ key: string; count?: number } | null>(null);
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
    setNotice(null);
    try {
      const result = await post<{ amount: number; replayed?: boolean }>(
        code === "checkin"
          ? "/v1/me/checkin"
          : "/v1/me/tasks/" + code + "/claim",
        {},
      );
      await refresh();
      await load();
      setNotice(result.replayed ? { key: "pages.this_reward_has_already_been_claimed_fa3250" } : { key: "pages.reward_earned", count: result.amount });
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
          <Failure message={t(error)} retry={() => void load()} />
        ) : (
          <Loading />
        )
      ) : (
        <>
          <div className="tasks-intro">
            <p>
              {t("pages.small_actions_coins_every_day_track_your_progress_and_claim_rewards_here_38b060")}
            </p>
            <span>
              <Icon name="coin" size={14} />
              {t("pages.coins_count", { count: number(data.balance) })}
            </span>
          </div>
          {error && <Failure message={t(error)} retry={() => void load()} />}
          {notice && (
            <p className="task-notice" role="status">
              {t(notice.key, { count: number(notice.count ?? 0) })}
            </p>
          )}
          <article className="task-row task-checkin">
            <span className="task-icon">
              <Icon name="calendar" size={22} />
            </span>
            <div>
              <h3>{t("pages.day_streak", { count: number(data.checkin.streak) })}</h3>
              <p>
                {data.checkin.checked_today
                  ? t("pages.checked_in_today_513264")
                  : t("pages.checkin_reward", { count: number(data.checkin.amount) })}
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
                ? t("pages.claiming_201714")
                : data.checkin.checked_today
                  ? t("pages.checked_in_6edf8e")
                  : t("pages.check_in_275d15")}
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
                    <Icon name="coin" size={12} />+{number(task.amount)}
                  </span>
                </div>
                <p>{task.description}</p>
                <progress
                  aria-label={t("{title} progress",{title:task.name})}
                  max={Math.max(task.target, 1)}
                  value={task.progress}
                />
                <small>
                  {number(task.progress)}/{number(task.target)}
                </small>
              </div>
              {task.claimed ? (
                <span className="task-claimed">
                  <Icon name="check" size={16} />
                  {t("pages.claimed_9d8b15")}
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
                  {busy === task.code ? t("pages.claiming_201714") : t("pages.claim_803138")}
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
                  {t("pages.go_5f075a")}
                </button>
              )}
            </article>
          ))}
          <div className="task-topup">
            <div>
              <h3>{t("pages.need_coins_now_c48ec3")}</h3>
              <p>{t("pages.buy_a_pack_and_keep_your_story_going_47f1cc")}</p>
            </div>
            <a className="button gold" href="#coins">
              {t("pages.buy_coins_7bcce5")}
            </a>
          </div>
        </>
      )}
    </>
  );
  return panel ? (
    <Modal
      title={t("pages.daily_tasks_6ec40b")}
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
        <h1>{t("pages.daily_tasks_6ec40b")}</h1>
        <p className="muted">{t("pages.your_daily_rewards_dd967f")}</p>
      </div>
      <div className="tasks-panel">{body}</div>
    </div>
  );
}

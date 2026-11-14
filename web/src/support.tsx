import { useLocale } from "./locale";
import { useEffect, useRef, useState } from "react";
import { get, post, errorMessage, authToken, ApiError } from "./consumer-api";
import { useAuth } from "./auth";
import { Empty, Failure, Loading, StatusTag, Modal } from "./components";
interface Category {
  code: string;
  name_en: string;
  name_zh: string;
  fields: Array<{
    key: string;
    label_en: string;
    label_zh: string;
    type: string;
    required: boolean;
    options?: string[];
  }>;
}
interface Ticket {
  id: number;
  number: string;
  subject: string;
  category: string;
  status: string;
  order_no: string | null;
  updated_at: string;
  can_reply: boolean;
  unread_count: number;
  csat_score: number | null;
  merged_into?: string | null;
}
interface Detail {
  ticket: Ticket;
  messages: Array<{
    id: number;
    body: string;
    author_type: string;
    created_at: string;
  }>;
}
export function Support() {
  const { status, user } = useAuth();
  // Private state belongs to one authenticated account, including drafts and errors.
  return (
    <SupportSession
      key={status === "user" && user ? "user:" + user.id : "guest"}
    />
  );
}
function SupportSession() {
  const { t, locale, number, date } = useLocale();
  const { status, openLogin } = useAuth();
  const [items, setItems] = useState<Ticket[]>([]),
    [categories, setCategories] = useState<Category[]>([]),
    [detail, setDetail] = useState<Detail | null>(null),
    [creating, setCreating] = useState(false),
    [subject, setSubject] = useState(""),
    [category, setCategory] = useState("general"),
    [body, setBody] = useState(""),
    [order, setOrder] = useState(""),
    [fields, setFields] = useState<Record<string, string>>({}),
    [reply, setReply] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(false),
    [confirmClose, setConfirmClose] = useState(false),
    [page, setPage] = useState(1),
    [total, setTotal] = useState(0);
  const pageSize = 10;
  const listRequest = useRef(0),
    detailRequest = useRef(0),
    mutationRequest = useRef(0),
    lifecycle = useRef(0),
    mounted = useRef(true),
    sessionToken = useRef(authToken()),
    submitting = useRef(false);
  const isCurrent = (life: number) =>
    mounted.current &&
    lifecycle.current === life &&
    sessionToken.current === authToken();
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      lifecycle.current++;
      listRequest.current++;
      detailRequest.current++;
      mutationRequest.current++;
    };
  }, []);
  const load = () => {
    if (status !== "user") return;
    const life = lifecycle.current,
      request = ++listRequest.current;
    get<{ items: Ticket[]; total: number; page: number; page_size: number }>(
      "/v1/support/tickets?page=" + page + "&page_size=" + pageSize,
    )
      .then((d) => {
        if (isCurrent(life) && request === listRequest.current) {
          setItems(d.items);
          setTotal(d.total);
        }
      })
      .catch((e) => {
        if (isCurrent(life) && request === listRequest.current) {
          setItems([]);
          setTotal(0);
          setError(errorMessage(e));
        }
      });
  };
  useEffect(() => {
    const life = lifecycle.current;
    get<{ categories: Category[] }>("/v1/support/meta")
      .then((d) => {
        if (!isCurrent(life)) return;
        setCategories(d.categories);
        if (d.categories.length)
          setCategory((current) =>
            d.categories.some((c) => c.code === current)
              ? current
              : d.categories[0].code,
          );
      })
      .catch(() => undefined);
  }, []);
  useEffect(() => {
    if (status === "user") load();
    return () => {
      listRequest.current++;
    };
  }, [status, page]);
  function startDraft(orderNumber = "") {
    detailRequest.current++;
    setLoading(false);
    setCreating(true);
    setDetail(null);
    setReply("");
    setConfirmClose(false);
    setSubject("");
    setBody("");
    setOrder(orderNumber);
    setFields({});
    setCategory(orderNumber ? "billing" : categories[0]?.code || "general");
    setError("");
  }
  useEffect(() => {
    if (status !== "user") return;
    const followLink = () => {
      const params = new URLSearchParams(
        window.location.hash.split("?")[1] || "",
      );
      const number = params.get("ticket");
      if (params.get("new") === "1") startDraft(params.get("order") || "");
      else if (number) void open(number);
      else {
        detailRequest.current++;
        setDetail(null);
        setReply("");
        setCreating(false);
        setConfirmClose(false);
        setLoading(false);
      }
    };
    followLink();
    window.addEventListener("hashchange", followLink);
    return () => window.removeEventListener("hashchange", followLink);
  }, [status]);
  async function open(number: string) {
    const life = lifecycle.current,
      request = ++detailRequest.current;
    setError("");
    setDetail(null);
    setCreating(false);
    setReply("");
    setConfirmClose(false);
    setLoading(true);
    try {
      const d = await get<Detail>(
        "/v1/support/tickets/" + encodeURIComponent(number),
      );
      if (!isCurrent(life) || request !== detailRequest.current) return;
      setDetail(d);
      window.history.replaceState(
        {},
        "",
        window.location.pathname +
          window.location.search +
          "#support?ticket=" +
          encodeURIComponent(number),
      );
      if (d.ticket.unread_count)
        void post(
          "/v1/support/tickets/" + encodeURIComponent(number) + "/read",
          {},
        ).catch(() => undefined);
    } catch (e) {
      if (isCurrent(life) && request === detailRequest.current) {
        setDetail(null);
        setError(errorMessage(e));
      }
    } finally {
      if (isCurrent(life) && request === detailRequest.current)
        setLoading(false);
    }
  }
  async function create() {
    if (submitting.current || status !== "user") return;
    submitting.current = true;
    const life = lifecycle.current,
      request = ++mutationRequest.current,
      view = detailRequest.current;
    setBusy(true);
    setError("");
    try {
      const d = await post<Detail>("/v1/support/tickets", {
        subject,
        category,
        body,
        order_no: order || undefined,
        fields,
      });
      if (
        !isCurrent(life) ||
        request !== mutationRequest.current ||
        view !== detailRequest.current
      )
        return;
      setDetail(d);
      window.history.replaceState(
        {},
        "",
        window.location.pathname +
          window.location.search +
          "#support?ticket=" +
          encodeURIComponent(d.ticket.number),
      );
      setCreating(false);
      setPage(1);
      setSubject("");
      setBody("");
      load();
    } catch (e) {
      if (
        isCurrent(life) &&
        request === mutationRequest.current &&
        view === detailRequest.current
      )
        setError(errorMessage(e));
    } finally {
      if (isCurrent(life) && request === mutationRequest.current) {
        submitting.current = false;
        setBusy(false);
      }
    }
  }
  async function action(path: string, payload: unknown) {
    if (!detail || submitting.current || status !== "user") return false;
    submitting.current = true;
    const life = lifecycle.current,
      request = ++mutationRequest.current,
      view = detailRequest.current,
      number = detail.ticket.number;
    setBusy(true);
    setError("");
    try {
      const d = await post<Detail>(
        "/v1/support/tickets/" + encodeURIComponent(number) + path,
        payload,
      );
      if (
        !isCurrent(life) ||
        request !== mutationRequest.current ||
        view !== detailRequest.current
      )
        return false;
      if (d?.ticket) setDetail(d);
      else await open(number);
      if (!isCurrent(life)) return false;
      setReply("");
      load();
      return true;
    } catch (e) {
      if (
        isCurrent(life) &&
        request === mutationRequest.current &&
        view === detailRequest.current
      ) {
        if (e instanceof ApiError && (e.status === 403 || e.status === 404)) {
          setDetail(null);
          setReply("");
          setConfirmClose(false);
          setItems((current) =>
            current.filter((item) => item.number !== number),
          );
        }
        setError(errorMessage(e));
      }
      return false;
    } finally {
      if (isCurrent(life) && request === mutationRequest.current) {
        submitting.current = false;
        setBusy(false);
      }
    }
  }
  return (
    <div className="page-content support-page">
      <div className="page-title">
        <span className="eyebrow">{t("pages.we_re_here_to_help_45b403")}</span>
        <h1>{t("pages.help_support_47a186")}</h1>
        <p className="muted">
          {t("pages.questions_about_playback_your_account_or_an_order_talk_to_the_jjtv_team_8e0d7b")}
        </p>
      </div>
      {status !== "user" ? (
        <div className="empty">
          <p>{t("pages.sign_in_to_create_a_ticket_and_follow_your_conversations_f783e0")}</p>
          <button className="button primary" onClick={openLogin}>
            {t("pages.sign_in_b6d422")}
          </button>
        </div>
      ) : (
        <>
          <div className="support-layout">
            <aside className="support-sidebar">
              <button
                className="button primary full-width"
                disabled={busy}
                onClick={() => {
                  startDraft();
                  window.history.replaceState(
                    {},
                    "",
                    window.location.pathname +
                      window.location.search +
                      "#support",
                  );
                }}
              >
                {t("pages.new_ticket_a969f9")}
              </button>
              {items.map((ticket) => (
                <button
                  className={
                    "ticket-link " +
                    (detail?.ticket.number === ticket.number ? "active" : "")
                  }
                  key={ticket.number}
                  disabled={busy}
                  onClick={() => void open(ticket.number)}
                >
                  <strong>{ticket.subject}</strong>
                  <span>
                    #{ticket.number} <StatusTag status={ticket.status} />
                    {ticket.unread_count
                      ? " · " + t("pages.unread_count", { count: number(ticket.unread_count) })
                      : ""}
                  </span>
                </button>
              ))}
              {!items.length && !loading && (
                <p className="muted">{t("pages.your_conversations_appear_here_619f76")}</p>
              )}
              {total > pageSize && (
                <div className="pagination ticket-pagination">
                  <button
                    className="button secondary"
                    disabled={page <= 1}
                    onClick={() => setPage(page - 1)}
                  >
                    {t("pages.newer_tickets_0fa96c")}
                  </button>
                  <span>
                    {page}/{Math.ceil(total / pageSize)}
                  </span>
                  <button
                    className="button secondary"
                    disabled={page * pageSize >= total}
                    onClick={() => setPage(page + 1)}
                  >
                    {t("pages.older_tickets_cf1855")}
                  </button>
                </div>
              )}
            </aside>
            <section className="support-main">
              {error && <Failure message={t(error)} />} {loading && <Loading />}
              {creating ? (
                <form
                  className="form"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void create();
                  }}
                >
                  <h2>{t("pages.new_support_ticket_2c76be")}</h2>
                  <label>
                    {t("pages.subject_c7892e")}
                    <input
                      disabled={busy}
                      required
                      maxLength={200}
                      value={subject}
                      onChange={(e) => setSubject(e.target.value)}
                    />
                  </label>
                  <label>
                    {t("pages.category_3adbdb")}
                    <select
                      disabled={busy}
                      value={category}
                      onChange={(e) => {
                        setCategory(e.target.value);
                        setFields({});
                      }}
                    >
                      {categories.map((c) => (
                        <option key={c.code} value={c.code}>
                          {locale === "zh" ? c.name_zh || c.name_en : c.name_en || c.name_zh}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    {t("pages.order_number_optional_84fcb5")}
                    <input
                      disabled={busy}
                      value={order}
                      onChange={(e) => setOrder(e.target.value)}
                    />
                  </label>
                  {categories
                    .find((c) => c.code === category)
                    ?.fields.map((f) => (
                      <label key={f.key}>
                        {locale === "zh" ? f.label_zh || f.label_en : f.label_en || f.label_zh}
                        {f.required ? " *" : ""}
                        {f.type === "select" ? (
                          <select
                            required={f.required}
                            value={fields[f.key] || ""}
                            onChange={(e) =>
                              setFields({ ...fields, [f.key]: e.target.value })
                            }
                          >
                            <option value="">{t("pages.select_0ffe9a")}</option>
                            {f.options?.map((o) => (
                              <option key={o}>{o}</option>
                            ))}
                          </select>
                        ) : (
                          <input
                            required={f.required}
                            value={fields[f.key] || ""}
                            onChange={(e) =>
                              setFields({ ...fields, [f.key]: e.target.value })
                            }
                          />
                        )}
                      </label>
                    ))}
                  <label>
                    {t("pages.how_can_we_help_8307fa")}
                    <textarea
                      disabled={busy}
                      required
                      maxLength={10000}
                      rows={6}
                      value={body}
                      onChange={(e) => setBody(e.target.value)}
                    />
                  </label>
                  <button className="button primary" disabled={busy}>
                    {busy ? t("pages.sending_4d01e8") : t("pages.send_ticket_355dc6")}
                  </button>
                </form>
              ) : detail ? (
                <>
                  <div className="ticket-heading">
                    <div>
                      <span className="eyebrow">
                        #{detail.ticket.number} ·{" "}
                        {detail.ticket.status.replace(/_/g, " ")}
                      </span>
                      <h2>{detail.ticket.subject}</h2>
                    </div>
                    {detail.ticket.can_reply && (
                      <button
                        className="button secondary"
                        disabled={busy}
                        onClick={() => setConfirmClose(true)}
                      >
                        {t("pages.close_ticket_2923de")}
                      </button>
                    )}
                  </div>
                  {detail.ticket.merged_into && (
                    <div className="membership-banner">
                      <div>
                        <strong>{t("pages.this_conversation_was_merged_a93691")}</strong>
                        <p>
                          {t("pages.merged_ticket", { number: detail.ticket.merged_into })}
                        </p>
                      </div>
                      <button
                        className="button secondary"
                        onClick={() => void open(detail.ticket.merged_into!)}
                      >
                        {t("pages.open_main_conversation_757833")}
                      </button>
                    </div>
                  )}
                  <div className="messages">
                    {detail.messages.map((m) => (
                      <article
                        className={"message " + m.author_type}
                        key={m.id}
                      >
                        <strong>
                          {m.author_type === "user"
                            ? t("pages.you_cae8d1")
                            : m.author_type === "admin"
                              ? t("pages.jjtv_support_dec588")
                              : t("pages.update_069330")}
                        </strong>
                        <p>{m.body}</p>
                        <time>{date(m.created_at)}</time>
                      </article>
                    ))}
                  </div>
                  {detail.ticket.can_reply && (
                    <form
                      className="form"
                      onSubmit={(e) => {
                        e.preventDefault();
                        void action("/messages", { body: reply });
                      }}
                    >
                      <label>
                        {t("pages.your_reply_d754a9")}
                        <textarea
                          disabled={busy}
                          required
                          value={reply}
                          onChange={(e) => setReply(e.target.value)}
                          rows={4}
                          maxLength={10000}
                        />
                      </label>
                      <button className="button primary" disabled={busy}>
                        {busy ? t("pages.sending_4d01e8") : t("pages.send_reply_662f37")}
                      </button>
                    </form>
                  )}
                  {detail.ticket.csat_score ? (
                    <div
                      className="satisfaction satisfaction-complete"
                      role="status"
                      aria-live="polite"
                    >
                      <strong>{t("pages.your_rating_9fa4c6")} {number(detail.ticket.csat_score)}/5</strong>
                      <p>
                        {t("pages.thank_you_for_your_feedback_your_rating_has_been_saved_0c8a9f")}
                      </p>
                    </div>
                  ) : (
                    ["closed", "resolved"].includes(detail.ticket.status) && (
                      <div className="satisfaction">
                        <p>{t("pages.how_was_your_support_experience_60c1a7")}</p>
                        {[1, 2, 3, 4, 5].map((score) => (
                          <button
                            className="button secondary"
                            disabled={busy}
                            key={score}
                            aria-label={t("pages.rate_support", { score: number(score) })}
                            onClick={() => void action("/csat", { score })}
                          >
                            {score}/5
                          </button>
                        ))}
                      </div>
                    )
                  )}
                </>
              ) : (
                !loading && (
                  <Empty message={t("pages.choose_a_conversation_or_start_a_new_ticket_9c62d6")} />
                )
              )}
            </section>
          </div>
        </>
      )}
      {confirmClose && (
        <Modal
          title={t("pages.close_this_conversation_b194fa")}
          onClose={() => setConfirmClose(false)}
        >
          <p>
            {t("pages.you_can_review_the_conversation_after_closing_it_start_a_new_ticket_if_you_need_further_help_1793ff")}
          </p>
          {error && (
            <p className="error-text" role="alert">
              {t(error)}
            </p>
          )}
          <div className="button-row">
            <button
              className="button secondary"
              onClick={() => setConfirmClose(false)}
            >
              {t("pages.keep_open_a9901d")}
            </button>
            <button
              className="button primary"
              disabled={busy}
              onClick={() => {
                void action("/close", {}).then((closed) => {
                  if (closed) setConfirmClose(false);
                });
              }}
            >
              {t("pages.confirm_close_21bcce")}
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}

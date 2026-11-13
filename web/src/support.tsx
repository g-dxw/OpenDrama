import { useEffect, useRef, useState } from "react";
import { get, post, errorMessage } from "./consumer-api";
import { useAuth } from "./auth";
import { Empty, Failure, Loading } from "./components";
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
    [page, setPage] = useState(1),
    [total, setTotal] = useState(0);
  const pageSize = 10;
  const listRequest = useRef(0),
    detailRequest = useRef(0);
  const load = () => {
    const request = ++listRequest.current;
    get<{ items: Ticket[]; total: number; page: number; page_size: number }>(
      "/v1/support/tickets?page=" + page + "&page_size=" + pageSize,
    )
      .then((d) => {
        if (request === listRequest.current) {
          setItems(d.items);
          setTotal(d.total);
        }
      })
      .catch((e) => setError(errorMessage(e)))
      .finally(() => undefined);
  };
  useEffect(() => {
    get<{ categories: Category[] }>("/v1/support/meta")
      .then((d) => {
        setCategories(d.categories);
        if (d.categories.length) setCategory(d.categories[0].code);
      })
      .catch(() => undefined);
  }, []);
  useEffect(() => {
    if (status === "user") load();
    return () => {
      listRequest.current++;
    };
  }, [status, page]);
  useEffect(() => {
    if (status !== "user") return;
    const followLink = () => {
      const query = window.location.hash.split("?")[1] || "";
      const number = new URLSearchParams(query).get("ticket");
      if (number) void open(number);
    };
    followLink();
    window.addEventListener("hashchange", followLink);
    return () => window.removeEventListener("hashchange", followLink);
  }, [status]);
  async function open(number: string) {
    setError("");
    const request = ++detailRequest.current;
    setLoading(true);
    try {
      const d = await get<Detail>("/v1/support/tickets/" + number);
      if (request !== detailRequest.current) return;
      setDetail(d);
      setCreating(false);
      window.history.replaceState(
        {},
        "",
        window.location.pathname +
          window.location.search +
          "#support?ticket=" +
          encodeURIComponent(number),
      );
      if (d.ticket.unread_count)
        void post("/v1/support/tickets/" + number + "/read", {}).catch(
          () => undefined,
        );
    } catch (e) {
      if (request === detailRequest.current) setError(errorMessage(e));
    } finally {
      if (request === detailRequest.current) setLoading(false);
    }
  }
  async function create() {
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
      setDetail(d);
      window.history.replaceState({},"",window.location.pathname+window.location.search+"#support?ticket="+encodeURIComponent(d.ticket.number));
      setCreating(false);
      setPage(1);
      setSubject("");
      setBody("");
      load();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  async function action(path: string, payload: unknown) {
    if (!detail) return;
    setBusy(true);
    setError("");
    try {
      const d = await post<Detail>(
        "/v1/support/tickets/" + detail.ticket.number + path,
        payload,
      );
      if (d?.ticket) setDetail(d);
      else await open(detail.ticket.number);
      setReply("");
      load();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="page-content">
      <div className="page-title">
        <span className="eyebrow">We're here to help</span>
        <h1>Help & support</h1>
        <p className="muted">
          Questions about playback, your account, or an order? Talk to the JJTV
          team.
        </p>
      </div>
      {status !== "user" ? (
        <div className="empty">
          <p>Sign in to create a ticket and follow your conversations.</p>
          <button className="button primary" onClick={openLogin}>
            Sign in
          </button>
        </div>
      ) : (
        <>
          <div className="support-layout">
            <aside className="support-sidebar">
              <button
                className="button primary full-width"
                onClick={() => {
                  setCreating(true);
                  setDetail(null);
                  window.history.replaceState({},"",window.location.pathname+window.location.search+"#support");
                }}
              >
                New ticket
              </button>
              {items.map((ticket) => (
                <button
                  className={
                    "ticket-link " +
                    (detail?.ticket.number === ticket.number ? "active" : "")
                  }
                  key={ticket.number}
                  onClick={() => void open(ticket.number)}
                >
                  <strong>{ticket.subject}</strong>
                  <span>
                    #{ticket.number} · {ticket.status.replace(/_/g, " ")}
                    {ticket.unread_count
                      ? " · " + ticket.unread_count + " new"
                      : ""}
                  </span>
                </button>
              ))}
              {!items.length && !loading && (
                <p className="muted">Your conversations appear here.</p>
              )}
              {total > pageSize && (
                <div className="pagination ticket-pagination">
                  <button
                    className="button secondary"
                    disabled={page <= 1}
                    onClick={() => setPage(page - 1)}
                  >
                    Newer tickets
                  </button>
                  <span>
                    {page}/{Math.ceil(total / pageSize)}
                  </span>
                  <button
                    className="button secondary"
                    disabled={page * pageSize >= total}
                    onClick={() => setPage(page + 1)}
                  >
                    Older tickets
                  </button>
                </div>
              )}
            </aside>
            <section className="support-main">
              {error && <Failure message={error} />} {loading && <Loading />}
              {creating ? (
                <form
                  className="form"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void create();
                  }}
                >
                  <h2>New support ticket</h2>
                  <label>
                    Subject
                    <input
                      required
                      maxLength={200}
                      value={subject}
                      onChange={(e) => setSubject(e.target.value)}
                    />
                  </label>
                  <label>
                    Category
                    <select
                      value={category}
                      onChange={(e) => {
                        setCategory(e.target.value);
                        setFields({});
                      }}
                    >
                      {categories.map((c) => (
                        <option key={c.code} value={c.code}>
                          {c.name_en || c.name_zh}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Order number (optional)
                    <input
                      value={order}
                      onChange={(e) => setOrder(e.target.value)}
                    />
                  </label>
                  {categories
                    .find((c) => c.code === category)
                    ?.fields.map((f) => (
                      <label key={f.key}>
                        {f.label_en || f.label_zh}
                        {f.required ? " *" : ""}
                        {f.type === "select" ? (
                          <select
                            required={f.required}
                            value={fields[f.key] || ""}
                            onChange={(e) =>
                              setFields({ ...fields, [f.key]: e.target.value })
                            }
                          >
                            <option value="">Select…</option>
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
                    How can we help?
                    <textarea
                      required
                      maxLength={10000}
                      rows={6}
                      value={body}
                      onChange={(e) => setBody(e.target.value)}
                    />
                  </label>
                  <button className="button primary" disabled={busy}>
                    {busy ? "Sending…" : "Send ticket"}
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
                        onClick={() => void action("/close", {})}
                      >
                        Close ticket
                      </button>
                    )}
                  </div>
                  {detail.ticket.merged_into && (
                    <div className="membership-banner">
                      <div>
                        <strong>This conversation was merged</strong>
                        <p>
                          Your messages and replies continue in ticket #
                          {detail.ticket.merged_into}.
                        </p>
                      </div>
                      <button
                        className="button secondary"
                        onClick={() => void open(detail.ticket.merged_into!)}
                      >
                        Open main conversation
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
                            ? "You"
                            : m.author_type === "admin"
                              ? "JJTV Support"
                              : "Update"}
                        </strong>
                        <p>{m.body}</p>
                        <time>{new Date(m.created_at).toLocaleString()}</time>
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
                        Your reply
                        <textarea
                          required
                          value={reply}
                          onChange={(e) => setReply(e.target.value)}
                          rows={4}
                          maxLength={10000}
                        />
                      </label>
                      <button className="button primary" disabled={busy}>
                        {busy ? "Sending…" : "Send reply"}
                      </button>
                    </form>
                  )}
                  {["closed", "resolved"].includes(detail.ticket.status) &&
                    !detail.ticket.csat_score && (
                      <div className="satisfaction">
                        <p>How was your support experience?</p>
                        {[1, 2, 3, 4, 5].map((score) => (
                          <button
                            className="button secondary"
                            disabled={busy}
                            key={score}
                            onClick={() => void action("/csat", { score })}
                          >
                            {score}/5
                          </button>
                        ))}
                      </div>
                    )}
                </>
              ) : (
                !loading && (
                  <Empty message="Choose a conversation or start a new ticket." />
                )
              )}
            </section>
          </div>
        </>
      )}
    </div>
  );
}

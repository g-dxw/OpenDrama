import { useEffect, useRef, type ReactNode } from "react";
import type { CatalogDrama } from "./consumer-api";
export function Icon({ name, size = 20 }: { name: string; size?: number }) {
  const paths: Record<string, ReactNode> = {
    history: (
      <>
        <path d="M3 11a9 9 0 1 1 3 7M3 4v7h7" />
        <path d="M12 7v6l4 2" />
      </>
    ),
    bookmark: <path d="M6 3h12v18l-6-4-6 4z" />,
    calendar: (
      <>
        <rect x="3" y="5" width="18" height="16" rx="2" />
        <path d="M7 3v4M17 3v4M3 10h18m-11 5 2 2 4-4" />
      </>
    ),
    gift: (
      <>
        <rect x="3" y="8" width="18" height="4" />
        <path d="M5 12v9h14v-9M12 8v13" />
        <path d="M12 8H8a3 3 0 1 1 3-3l1 3h4a3 3 0 1 0-3-3z" />
      </>
    ),
    receipt: (
      <>
        <path d="M5 3h14v18l-3-2-4 2-4-2-3 2zM8 7h8M8 11h8M8 15h4" />
      </>
    ),
    message: <path d="M21 12a9 9 0 0 1-9 9H3l2-5a9 9 0 1 1 16-4z" />,
    support: (
      <>
        <path d="M3 12a9 9 0 0 1 18 0v6a3 3 0 0 1-3 3h-4" />
        <rect x="2" y="11" width="4" height="7" rx="2" />
        <rect x="18" y="11" width="4" height="7" rx="2" />
      </>
    ),
    star: <path d="m12 3 3 6 7 1-5 5 1 7-6-3-6 3 1-7-5-5 7-1z" />,
    search: (
      <>
        <circle cx="10.5" cy="10.5" r="6.5" />
        <path d="m16 16 5 5" />
      </>
    ),
    play: <path d="m9 5 11 7-11 7Z" />,
    heart: (
      <path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8Z" />
    ),
    user: (
      <>
        <circle cx="12" cy="8" r="4" />
        <path d="M4 21a8 8 0 0 1 16 0" />
      </>
    ),
    arrow: <path d="m9 5 7 7-7 7" />,
    close: <path d="m6 6 12 12M6 18 18 6" />,
    coin: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M14 8h-4v8h4M9 12h5" />
      </>
    ),
    lock: (
      <>
        <rect x="5" y="10" width="14" height="11" rx="2" />
        <path d="M8 10V6a4 4 0 0 1 8 0v4" />
      </>
    ),
    crown: (
      <>
        <path d="m3 6 4 4 5-6 5 6 4-4-2 13H5Z" />
      </>
    ),
    check: <path d="m5 12 4 4L19 6" />,
    menu: <path d="M4 6h16M4 12h16M4 18h16" />,
    more: <><circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/></>,
    share: <><path d="M14 4h6v6M20 4 10 14"/><path d="M20 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h5"/></>,
    volume: <><path d="m11 4-6 5H2v6h3l6 5zM15 8a6 6 0 0 1 0 8M18 5a10 10 0 0 1 0 14"/></>,
    "volume-off": <><path d="m11 4-6 5H2v6h3l6 5zM16 9l6 6M22 9l-6 6"/></>,
  };
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {paths[name] ?? paths.arrow}
    </svg>
  );
}
const modalStack: HTMLElement[] = [];
let bodyOverflowBeforeModals = "";
export function Modal({
  title,
  onClose,
  children,
  className = "",
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const modal = ref.current;
    if (!modal) return;
    const previous = document.activeElement as HTMLElement | null;
    if (!modalStack.length)
      bodyOverflowBeforeModals = document.body.style.overflow;
    modalStack.push(modal);
    document.body.style.overflow = "hidden";
    modal.focus();
    const key = (event: KeyboardEvent) => {
      if (modalStack[modalStack.length - 1] !== modal) return;
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
      if (event.key === "Tab") {
        const elements = Array.from(
          modal.querySelectorAll<HTMLElement>(
            'button:not(:disabled),a[href],input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex="0"]',
          ),
        );
        const first = elements[0],
          last = elements[elements.length - 1];
        if (
          event.shiftKey &&
          (document.activeElement === first || document.activeElement === modal)
        ) {
          event.preventDefault();
          last?.focus();
        } else if (
          !event.shiftKey &&
          (document.activeElement === last || document.activeElement === modal)
        ) {
          event.preventDefault();
          first?.focus();
        }
      }
    };
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("keydown", key);
      const position = modalStack.indexOf(modal);
      if (position >= 0) modalStack.splice(position, 1);
      document.body.style.overflow = modalStack.length
        ? "hidden"
        : bodyOverflowBeforeModals;
      if (previous?.isConnected) previous.focus();
      const top = modalStack[modalStack.length - 1];
      if (top && !top.contains(document.activeElement)) top.focus();
    };
  }, []);
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(event) => {
        if (
          event.target === event.currentTarget &&
          modalStack[modalStack.length - 1] === ref.current
        )
          onClose();
      }}
    >
      <div
        ref={ref}
        className={"modal " + className}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
      >
        <div className="modal-head">
          {className.split(" ").includes("auth-sheet") && <span className="auth-brand-mark" aria-hidden="true"><Icon name="play" size={19}/></span>}
          <h2>{title}</h2>
          <button
            className="icon-button"
            onClick={onClose}
            aria-label="Close dialog"
          >
            <Icon name="close" />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
export function Loading() {
  return (
    <div className="loading" role="status">
      <span className="spinner" />
      Loading…
    </div>
  );
}
export function Failure({
  message,
  retry,
}: {
  message: string;
  retry?: () => void;
}) {
  return (
    <div className="error-card" role="alert">
      <p>{message}</p>
      {retry && (
        <button className="button secondary" onClick={retry}>
          Try again
        </button>
      )}
    </div>
  );
}
export function Empty({ message = "Nothing here yet." }: { message?: string }) {
  return (
    <div className="empty">
      <Icon name="play" size={32} />
      <p>{message}</p>
      <a className="button secondary" href="#browse">
        Explore dramas
      </a>
    </div>
  );
}
export function DramaCard({ drama }: { drama: CatalogDrama }) {
  return (
    <a className="drama-card" href={"#watch/" + drama.id}>
      <div className="drama-cover">
        {drama.cover_url ? (
          <img
            src={drama.cover_url}
            alt=""
            loading="lazy"
            onError={(e) => {
              e.currentTarget.style.opacity = "0";
            }}
          />
        ) : (
          <span className="cover-placeholder">JJTV</span>
        )}
        <span className="card-play">
          <Icon name="play" size={26} />
        </span>
        {drama.free_episode_count != null && drama.free_episode_count > 0 && (
          <span className="card-badge">Free episodes</span>
        )}
      </div>
      <h3>{drama.title}</h3>
      <p>{drama.episode_count != null || drama.published_episode_count != null ? (drama.episode_count ?? drama.published_episode_count) + " episodes" : ""}{drama.play_count != null ? ((drama.episode_count != null || drama.published_episode_count != null) ? " · " : "") + Intl.NumberFormat("en", {notation:"compact"}).format(drama.play_count) + " views" : ""}</p>
    </a>
  );
}
export function Cards({ items }: { items: CatalogDrama[] }) {
  return items.length ? (
    <div className="drama-grid">
      {items.map((d) => (
        <DramaCard drama={d} key={d.id} />
      ))}
    </div>
  ) : (
    <Empty />
  );
}

export function StatusTag({ status }: { status: string }) {
  const labels: Record<string, string> = {
    created: "Awaiting payment",
    paid: "Paid",
    failed: "Payment failed",
    refunded: "Refunded",
    open: "Open",
    in_progress: "In progress",
    waiting_customer: "Waiting for your reply",
    resolved: "Resolved",
    closed: "Closed",
    merged: "Merged",
  };
  return (
    <span className={"status-tag status-" + status}>
      {labels[status] ?? status.replace(/_/g, " ")}
    </span>
  );
}

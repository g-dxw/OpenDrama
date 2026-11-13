import { useEffect, useRef, type ReactNode } from "react";
import type { CatalogDrama } from "./consumer-api";
export function Icon({ name, size = 20 }: { name: string; size?: number }) {
  const paths: Record<string, ReactNode> = {
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
export function Modal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const old = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    ref.current?.focus();
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "Tab") {
        const els = Array.from(
          ref.current?.querySelectorAll<HTMLElement>(
            'button:not(:disabled),a[href],input,select,textarea,[tabindex="0"]',
          ) ?? [],
        );
        const first = els[0],
          last = els[els.length - 1];
        if (
          e.shiftKey &&
          (document.activeElement === first ||
            document.activeElement === ref.current)
        ) {
          e.preventDefault();
          last?.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first?.focus();
        }
      }
    };
    document.addEventListener("keydown", key);
    return () => {
      document.body.style.overflow = old;
      document.removeEventListener("keydown", key);
      previous?.focus();
    };
  }, []);
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={ref}
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
      >
        <div className="modal-head">
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
      <p>
        {drama.episode_count ?? drama.published_episode_count ?? 0} episodes
        {drama.play_count
          ? " · " +
            Intl.NumberFormat("en", { notation: "compact" }).format(
              drama.play_count,
            ) +
            " views"
          : ""}
      </p>
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

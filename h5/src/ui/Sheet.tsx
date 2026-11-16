import { createContext, useContext, useId, useLayoutEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { IconClose } from "../icons";

type Layer = { parentToken: symbol | null; dialog: HTMLElement; restoreTargets: HTMLElement[]; focusEntry: () => void };
const ParentSheet = createContext<symbol | null>(null);
const layers: Layer[] = [];
const scrollLocks = new WeakMap<HTMLElement, { count: number; value: string; priority: string }>();

function lockScroll(element: HTMLElement) {
  const existing = scrollLocks.get(element);
  if (existing) existing.count += 1;
  else {
    scrollLocks.set(element, {
      count: 1,
      value: element.style.getPropertyValue("overflow-y"),
      priority: element.style.getPropertyPriority("overflow-y"),
    });
    element.style.setProperty("overflow-y", "hidden", "important");
  }
  return () => {
    const lock = scrollLocks.get(element);
    if (!lock || --lock.count > 0) return;
    scrollLocks.delete(element);
    // Restore only our own declaration; preserve changes made by other UI.
    if (element.style.getPropertyValue("overflow-y") === "hidden" &&
        element.style.getPropertyPriority("overflow-y") === "important") {
      if (lock.value) element.style.setProperty("overflow-y", lock.value, lock.priority);
      else element.style.removeProperty("overflow-y");
    }
  };
}

function available(element: HTMLElement) {
  return element.isConnected && !element.matches(":disabled") &&
    !element.closest('[hidden], [inert], [aria-hidden="true"]') &&
    element.getClientRects().length > 0 &&
    element.ownerDocument.defaultView?.getComputedStyle(element).visibility !== "hidden";
}

function tabStops(dialog: HTMLElement) {
  const candidates = Array.from(dialog.querySelectorAll<HTMLElement>(
    'button, a[href], input, select, textarea, [tabindex], [contenteditable]:not([contenteditable="false"])',
  )).filter((element) => element.tabIndex >= 0 && available(element));
  return candidates.filter((element) => {
    if (!(element instanceof HTMLInputElement) || element.type !== "radio" || !element.name) return true;
    const group = candidates.filter((other): other is HTMLInputElement =>
      other instanceof HTMLInputElement && other.type === "radio" &&
      other.name === element.name && other.form === element.form);
    return element === (group.find((radio) => radio.checked) ?? group[0]);
  }).sort((a, b) => {
    // Positive tabindex values precede the normal DOM order, as in the browser.
    const left = a.tabIndex > 0 ? a.tabIndex : Infinity;
    const right = b.tabIndex > 0 ? b.tabIndex : Infinity;
    return left === right ? 0 : left - right;
  });
}

/**
 * Bottom sheet primitives shared by the Me page. The mask/sheet sit above the
 * tab bar (z 11/12) on purpose: the tab bar is a flex child with z-index 10 and
 * would otherwise paint over an open sheet.
 */
export function Sheet({
  title,
  onClose,
  children,
  className = "",
  maskClassName = "",
  testId,
  closeLabel,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  className?: string;
  maskClassName?: string;
  testId?: string;
  closeLabel?: string;
}) {
  const resolvedCloseLabel = closeLabel ?? (typeof document !== "undefined" && document.documentElement.lang.startsWith("zh") ? "关闭" : "Close");
  const titleId = useId();
  const layerToken = useRef(Symbol("sheet")).current;
  const parentToken = useContext(ParentSheet);
  const accessibleTitle = title.trim() || "Dialog";
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const latestClose = useRef(onClose);
  latestClose.current = onClose;

  useLayoutEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    const doc = dialog.ownerDocument;
    const previous = doc.activeElement instanceof HTMLElement ? doc.activeElement : null;
    const parent = layers[layers.length - 1];
    const focusEntry = () => {
      const target = closeRef.current ?? tabStops(dialog)[0] ?? dialog;
      target.focus({ preventScroll: true });
    };
    // Child effects may run before their parent when both mount together.
    // Keep ancestors below their children and retain the original opener.
    const childIndex = layers.findIndex((item) => item.parentToken === layerToken);
    const layer: Layer = {
      parentToken,
      dialog,
      restoreTargets: childIndex >= 0 ? layers[childIndex].restoreTargets :
        [...new Set([...(previous ? [previous] : []), ...(parent?.restoreTargets ?? [])])],
      focusEntry,
    };
    if (childIndex >= 0) layers.splice(childIndex, 0, layer);
    else layers.push(layer);
    // Sheets are portalled beside .stage. Lock the background, never the
    // dialog/body scroller, and leave wheel/touch behavior inside it native.
    const stage = doc.getElementById("app")?.querySelector<HTMLElement>(".stage");
    const releaseLocks = [...new Set([doc.documentElement, doc.body, ...(stage ? [stage] : [])])].map(lockScroll);
    const isTop = () => layers[layers.length - 1] === layer;
    const onKeyDown = (event: KeyboardEvent) => {
      if (!isTop() || event.defaultPrevented || event.isComposing) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        latestClose.current();
      } else if (event.key === "Tab") {
        const stops = tabStops(dialog);
        const first = stops[0] ?? dialog;
        const last = stops[stops.length - 1] ?? dialog;
        const active = doc.activeElement;
        if (!dialog.contains(active) || active === dialog ||
            (event.shiftKey ? active === first : active === last)) {
          event.preventDefault();
          (event.shiftKey ? last : first).focus({ preventScroll: true });
        }
      }
    };
    const onFocusIn = (event: FocusEvent) => {
      if (isTop() && !dialog.contains(event.target as Node)) focusEntry();
    };
    doc.addEventListener("keydown", onKeyDown, true);
    doc.addEventListener("focusin", onFocusIn);
    if (isTop()) focusEntry();
    return () => {
      doc.removeEventListener("keydown", onKeyDown, true);
      doc.removeEventListener("focusin", onFocusIn);
      const wasTop = isTop();
      const index = layers.indexOf(layer);
      if (index >= 0) layers.splice(index, 1);
      releaseLocks.forEach((release) => release());
      if (!wasTop) return;
      const remaining = layers[layers.length - 1];
      const target = layer.restoreTargets.find((element) => available(element) &&
        (!remaining || remaining.dialog.contains(element)));
      if (target) target.focus({ preventScroll: true });
      else remaining?.focusEntry();
    };
  }, []);

  const content = (
    <ParentSheet.Provider value={layerToken}>
      <button type="button" className={`sheet-mask me-sheet-mask ${maskClassName}`.trim()} onClick={onClose} tabIndex={-1} aria-hidden="true" />
      <div
        ref={dialogRef}
        className={`sheet me-sheet ${className}`.trim()}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        data-testid={testId}
      >
        <div className="sheet-handle" aria-hidden="true" />
        <header className="me-sheet-head">
          <h3 id={titleId}>{accessibleTitle}</h3>
          <button ref={closeRef} type="button" className="close-btn" onClick={onClose} aria-label={resolvedCloseLabel}>
            <IconClose size={16} />
          </button>
        </header>
        <div className="me-sheet-body">{children}</div>
      </div>
    </ParentSheet.Provider>
  );

  // The Me page scrolls inside .stage; keeping the sheet in that scroll box
  // would let it drift off-screen. Portal it next to .stage, like the login sheet.
  const host = typeof document === "undefined" ? null : document.getElementById("app");
  return host ? createPortal(content, host) : content;
}

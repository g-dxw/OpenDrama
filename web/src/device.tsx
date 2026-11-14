import {
  lazy,
  Suspense,
  useEffect,
  useLayoutEffect,
  useState,
  type ReactNode,
} from "react";
import consumerCss from "./consumer.css?inline";
import detailsCss from "./details.css?inline";
import playerCss from "./player.css?inline";
import localeCss from "./locale.css?inline";
const DesktopApp = lazy(() => import("./App"));
const MobileApp = lazy(() => import("./mobile"));
import {
  CHECKOUT_SURFACE_EVENT,
  MOBILE_CHECKOUT_SURFACE_EVENT,
} from "./surface";
import { loadLocale, translate } from "./locale";
export const DESKTOP_MIN_WIDTH = 1024;
export function supportsMobile(hash: string) {
  const [path, query = ""] = hash.replace(/^#\/?/, "").split("?");
  const params = new URLSearchParams(query);
  if (["", "home", "foryou", "rewards", "search", "premium"].includes(path))
    return true;
  if (path === "price") return params.get("tab") !== "coins";
  if (/^watch\/[1-9]\d*$/.test(path))
    return Number.isSafeInteger(Number(path.split("/")[1]));
  return /^(account|me)(\/(history|favorites|likes|profile|ledger|orders|messages|settings))?$/.test(
    path,
  );
}
// Narrow desktop-only links keep their desktop layout instead of silently losing a route.
export function desktopStyles(css: string) {
  return css
    .replace(
      /(@media[^{}]*max-width\s*:\s*)([\d.]+)px/g,
      (all, prefix: string, width: string) =>
        Number(width) < DESKTOP_MIN_WIDTH ? prefix + "0px" : all,
    )
    .replace(
      /(@media[^{}]*min-width\s*:\s*)([\d.]+)px/g,
      (all, prefix: string, width: string) =>
        Number(width) <= DESKTOP_MIN_WIDTH ? prefix + "0px" : all,
    );
}
export function SurfaceStyles({
  surface,
  css,
  children,
}: {
  surface: "desktop" | "mobile";
  css: string;
  children: ReactNode;
}) {
  useLayoutEffect(() => {
    const style = document.createElement("style");
    style.dataset.jjtvSurface = surface;
    style.textContent = css;
    document.head.appendChild(style);
    document.documentElement.dataset.jjtvSurface = surface;
    return () => {
      style.remove();
      if (document.documentElement.dataset.jjtvSurface === surface)
        delete document.documentElement.dataset.jjtvSurface;
    };
  }, [surface, css]);
  return <>{children}</>;
}
const desktopCss =
  desktopStyles(
    consumerCss + "\n" + detailsCss + "\n" + playerCss + "\n" + localeCss,
  ) +
  "\n@media(max-width:1023px){html,body,#root{min-width:1024px}}.desktop-route-notice{width:100vw;max-width:1024px;padding:10px 16px;background:#25151d;color:#fff;font:13px/1.5 sans-serif;box-sizing:border-box}.desktop-route-notice a{color:#ffa4bc;margin-left:12px;text-decoration:underline}";
export function ResponsiveApp() {
  const [locale, setLocale] = useState(loadLocale);
  const [width, setWidth] = useState(window.innerWidth);
  const [hash, setHash] = useState(window.location.hash);
  const [checkoutPinned, setCheckoutPinned] = useState(false);
  const [mobilePins, setMobilePins] = useState<Set<string>>(() => new Set());
  const [paymentSurface, setPaymentSurface] = useState<
    "desktop" | "mobile" | null
  >(() => {
    const params = new URLSearchParams(window.location.search);
    return params.has("checkout")
      ? params.get("surface") === "h5"
        ? "mobile"
        : "desktop"
      : null;
  });
  useEffect(() => {
    const resize = () => setWidth(window.innerWidth),
      route = () => {
        setHash(window.location.hash);
        if (!/^#\/?(price|coins|premium)(\?|$)/.test(window.location.hash))
          setCheckoutPinned(false);
      };
    const localeChanged = () => setLocale(loadLocale());
    const checkout = (event: Event) =>
      setCheckoutPinned((event as CustomEvent<boolean>).detail === true);
    const mobileCheckout = (event: Event) => {
      const detail = (event as CustomEvent<{ owner: string; active: boolean }>)
        .detail;
      if (!detail || typeof detail.owner !== "string") return;
      setMobilePins((previous) => {
        const next = new Set(previous);
        if (detail.active) next.add(detail.owner);
        else next.delete(detail.owner);
        return next;
      });
    };
    window.addEventListener(MOBILE_CHECKOUT_SURFACE_EVENT, mobileCheckout);
    window.addEventListener("resize", resize);
    window.addEventListener("hashchange", route);
    window.addEventListener(CHECKOUT_SURFACE_EVENT, checkout);
    window.addEventListener("jjtv:localechange", localeChanged);
    return () => {
      window.removeEventListener(MOBILE_CHECKOUT_SURFACE_EVENT, mobileCheckout);
      window.removeEventListener("resize", resize);
      window.removeEventListener("hashchange", route);
      window.removeEventListener(CHECKOUT_SURFACE_EVENT, checkout);
      window.removeEventListener("jjtv:localechange", localeChanged);
    };
  }, []);
  const mobile =
    mobilePins.size > 0 ||
    paymentSurface === "mobile" ||
    (!checkoutPinned &&
      paymentSurface !== "desktop" &&
      width < DESKTOP_MIN_WIDTH &&
      supportsMobile(hash));
  return (
    <Suspense
      fallback={
        <div role="status" style={{ padding: 24 }}>
          Loading JJTV…
        </div>
      }
    >
      {mobile ? (
        <MobileApp />
      ) : (
        <SurfaceStyles surface="desktop" css={desktopCss}>
          {width < DESKTOP_MIN_WIDTH && (
            <aside className="desktop-route-notice" role="status">
              {translate(
                locale,
                paymentSurface || checkoutPinned
                  ? "Your checkout is being handled in the desktop site."
                  : "This page is available in the desktop site.",
              )}
              {!checkoutPinned && (
                <a href="#home" onClick={() => setPaymentSurface(null)}>
                  {translate(locale, "Open mobile home")}
                </a>
              )}
            </aside>
          )}
          <DesktopApp />
        </SurfaceStyles>
      )}
    </Suspense>
  );
}

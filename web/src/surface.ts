/** A pending desktop payment must keep its owning React tree across resizes. */
export const CHECKOUT_SURFACE_EVENT = "jjtv:desktop-checkout";
export function setDesktopCheckoutActive(active: boolean) {
  window.dispatchEvent(
    new CustomEvent<boolean>(CHECKOUT_SURFACE_EVENT, { detail: active }),
  );
}

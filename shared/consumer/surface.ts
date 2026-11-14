/** Layout ownership only; no token or payment data crosses this event boundary. */
export const CHECKOUT_SURFACE_EVENT = "jjtv:desktop-checkout";
export const MOBILE_CHECKOUT_SURFACE_EVENT = "jjtv:mobile-checkout";
export function setDesktopCheckoutActive(active: boolean) {
  window.dispatchEvent(
    new CustomEvent<boolean>(CHECKOUT_SURFACE_EVENT, { detail: active }),
  );
}
export function setMobileCheckoutActive(owner: string, active: boolean) {
  window.dispatchEvent(
    new CustomEvent(MOBILE_CHECKOUT_SURFACE_EVENT, {
      detail: { owner, active },
    }),
  );
}

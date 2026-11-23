import type { Plan } from "./consumer-api";
export interface PlanPriceDisplay { priceCents: number; interval: string | null; subscription: boolean; annualChargeCents?: number; monthlyComparisonCents?: number; discountPercent?: number; }
/** Display averages do not change the frozen amount or product used at checkout. */
export function planPriceDisplay(plan: Plan, plans: Plan[], fallbackMode: string): PlanPriceDisplay {
  const subscription = (plan.billing_mode || fallbackMode) === "subscription";
  const result: PlanPriceDisplay = { priceCents: plan.price_cents, interval: subscription ? plan.recurring_interval || null : "pass", subscription };
  if (!subscription || plan.recurring_interval !== "year" || !(plan.price_cents > 0)) return result;
  const monthly = plans.filter((candidate) => (candidate.billing_mode || fallbackMode) === "subscription" && candidate.recurring_interval === "month" && candidate.currency.toUpperCase() === plan.currency.toUpperCase() && candidate.price_cents > 0);
  if (!monthly.length) return result;
  const comparison = Math.min(...monthly.map((candidate) => candidate.price_cents));
  result.priceCents = plan.price_cents / 12;
  result.interval = "month";
  result.annualChargeCents = plan.price_cents;
  // Round down: never claim a saving larger than the actual annual-vs-monthly difference.
  const saving = Math.floor((1 - plan.price_cents / (comparison * 12)) * 100);
  if (saving > 0) { result.monthlyComparisonCents = comparison; result.discountPercent = saving; }
  return result;
}

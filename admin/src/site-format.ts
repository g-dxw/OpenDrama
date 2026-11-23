let timezone = "UTC";
let currency = "USD";
export function setOperatorSite(value: {
  timezone?: string;
  currency?: string;
}) {
  timezone = value.timezone || "UTC";
  currency = value.currency || "USD";
}
export function operatorTimezone() {
  return timezone;
}
export function operatorCurrency() {
  return currency;
}
export function operatorDate(value?: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "—"
    : new Intl.DateTimeFormat(
        localStorage.getItem("operator_locale") === "en" ? "en" : "zh-CN",
        { dateStyle: "medium", timeStyle: "short", timeZone: timezone },
      ).format(date);
}

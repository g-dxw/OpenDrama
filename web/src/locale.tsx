import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
export const LANGUAGES = [
  { code: "en", label: "English", tag: "en-US" },
  { code: "de", label: "Deutsch", tag: "de-DE" },
  { code: "fr", label: "Français", tag: "fr-FR" },
  { code: "es", label: "Español", tag: "es-ES" },
  { code: "pt", label: "Português", tag: "pt-PT" },
  { code: "it", label: "Italiano", tag: "it-IT" },
  { code: "ja", label: "日本語", tag: "ja-JP" },
  { code: "ru", label: "Русский", tag: "ru-RU" },
  { code: "zh", label: "简体中文", tag: "zh-CN" },
] as const;
export type Locale = typeof LANGUAGES[number]["code"];
export type Variables = Record<string, string | number>;
export const LOCALE_STORAGE_KEY = "jjtv_pc_locale";
const valid = (value: unknown): value is Locale => LANGUAGES.some((item) => item.code === value);
export function loadLocale(): Locale {
  try { const saved = localStorage.getItem(LOCALE_STORAGE_KEY); return valid(saved) ? saved : "en"; }
  catch { return "en"; }
}
const dictionaries: Record<string, Record<string, string>> = { en: {} };
// Every section owns a plain data module; new sections do not couple UI to API code.
const sections = import.meta.glob<Record<string, unknown>>("./locale-*.ts", { eager: true });
for (const section of Object.values(sections)) {
  for (const [name, value] of Object.entries(section)) {
    if (name.endsWith("English")) Object.assign(dictionaries.en, value);
    if (name.endsWith("Translations")) {
      for (const [locale, table] of Object.entries(value as Record<string, Record<string, string>>)) {
        Object.assign(dictionaries[locale] ||= {}, table);
      }
    }
  }
}
const englishKeys = new Map<string,string>();
for (const table of Object.values(dictionaries)) for (const [key,value] of Object.entries(table)) { if (!englishKeys.has(value)) englishKeys.set(value,key); }
export function translate(locale: Locale, key: string, vars: Variables = {}): string {
  const actualKey = Object.prototype.hasOwnProperty.call(dictionaries.en, key) ? key : englishKeys.get(key) || key;
  const template = dictionaries[locale]?.[actualKey] ?? dictionaries.en[actualKey] ?? key;
  return template.replace(/\{([A-Za-z0-9_]+)\}/g, (match, name: string) => Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : match);
}
export function localeTag(locale: Locale) { return LANGUAGES.find((item) => item.code === locale)!.tag; }
export function formatNumber(locale: Locale, value: number, options?: Intl.NumberFormatOptions) {
  return new Intl.NumberFormat(localeTag(locale), options).format(value);
}
export function formatMoney(locale: Locale, cents: number, currency = "USD") {
  return formatNumber(locale, cents / 100, { style: "currency", currency });
}
export function formatDate(locale: Locale, value: string | number | Date, options?: Intl.DateTimeFormatOptions) {
  const parsed = value instanceof Date ? value : new Date(value);
  return Number.isNaN(parsed.getTime()) ? "—" : new Intl.DateTimeFormat(localeTag(locale), options ?? { dateStyle: "medium", timeStyle: "short" }).format(parsed);
}
function createValue(locale: Locale, setLocale: (value: Locale) => void) {
  return { locale, setLocale,
    t: (key: string, vars?: Variables) => translate(locale, key, vars),
    number: (value: number, options?: Intl.NumberFormatOptions) => formatNumber(locale, value, options),
    money: (cents: number, currency = "USD") => formatMoney(locale, cents, currency),
    date: (value: string | number | Date, options?: Intl.DateTimeFormatOptions) => formatDate(locale, value, options),
  };
}
const LocaleContext = createContext(createValue("en", () => undefined));
export function LocaleProvider({ children }: { children: ReactNode }) {
  const [locale, changeLocale] = useState<Locale>(loadLocale);
  useEffect(() => {
    document.documentElement.lang = localeTag(locale);
    try { localStorage.setItem(LOCALE_STORAGE_KEY, locale); } catch { /* Private browsing can disable storage. */ }
  }, [locale]);
  useEffect(() => {
    const update = (event: StorageEvent) => { if (event.key === LOCALE_STORAGE_KEY) changeLocale(valid(event.newValue) ? event.newValue : "en"); };
    window.addEventListener("storage", update); return () => window.removeEventListener("storage", update);
  }, []);
  const value = useMemo(() => createValue(locale, (next) => { if (valid(next)) { changeLocale(next); window.dispatchEvent(new CustomEvent("jjtv:localechange", {detail: next})); } }), [locale]);
  return <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>;
}
export function useLocale() { return useContext(LocaleContext); }
export function localeCoverage() {
  const keys = Object.keys(dictionaries.en);
  return Object.fromEntries(LANGUAGES.map(({code}) => [code, { total: keys.length, missing: keys.filter((key) => !dictionaries[code]?.[key]) }]));
}

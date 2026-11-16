import { getSite } from "../../shared/site";
import { useEffect, useRef, useState } from "react";
import { LANGUAGES, useLocale } from "./locale";
import { Icon } from "./components";
export function LanguageMenu() {
  const { locale, setLocale, t } = useLocale();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const selected = LANGUAGES.find((item) => item.code === locale)!;
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => { if (!ref.current?.contains(event.target as Node)) setOpen(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") { event.preventDefault(); setOpen(false); trigger.current?.focus(); } };
    document.addEventListener("pointerdown", outside); document.addEventListener("keydown", escape);
    const selectedButton = ref.current?.querySelector<HTMLButtonElement>('[aria-checked="true"]'); selectedButton?.focus();
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", escape); };
  }, [open]);
  return <div className="language-menu" ref={ref}>
    <button ref={trigger} className="header-language" title={selected.label} aria-label={t("Choose language")} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}>{locale.toUpperCase()}<span aria-hidden="true">⌄</span></button>
    {open && <div className="language-popover" role="menu" aria-label={t("Language")} onKeyDown={(event) => {
      if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
      event.preventDefault(); const options = Array.from(ref.current?.querySelectorAll<HTMLButtonElement>('[role="menuitemradio"]') || []);
      const current = options.indexOf(document.activeElement as HTMLButtonElement);
      const next = event.key === "Home" ? 0 : event.key === "End" ? options.length - 1 : (current + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length;
      options[next]?.focus();
    }}>
      {LANGUAGES.filter(item=>getSite().tenant.locales.includes(item.code)).map((item) => <button key={item.code} role="menuitemradio" aria-checked={item.code === locale} lang={item.tag} onClick={() => {setLocale(item.code);setOpen(false);trigger.current?.focus();}}><span className="language-code">{item.code.toUpperCase()}</span><span>{item.label}</span>{item.code === locale && <Icon name="check" size={15}/>}</button>)}
    </div>}
  </div>;
}

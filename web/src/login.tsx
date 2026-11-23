import { localeTag, useLocale } from "./locale";
import { useEffect, useRef, useState } from "react";
import { get, post, errorMessage } from "./consumer-api";
import { useAuth } from "./auth";
import { Modal } from "./components";
import { renderGoogleButton } from "./google";
import { prepareFacebookLogin, facebookAccessToken } from "./facebook";
interface Providers {
  email: { enabled: boolean };
  google: { enabled: boolean; client_id: string | null };
  facebook?: { enabled: boolean; app_id: string | null };
}
export function Login() {
  const { t, locale, number } = useLocale();
  const { closeLogin, completeLogin } = useAuth();
  const [providers, setProviders] = useState<Providers | null>(null),
    [email, setEmail] = useState(""),
    [code, setCode] = useState(""),
    [sent, setSent] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [seconds, setSeconds] = useState(0),
    [facebookReady, setFacebookReady] = useState(false);
  const google = useRef<HTMLDivElement>(null);
  useEffect(() => {
    get<Providers>("/v1/auth/providers")
      .then(setProviders)
      .catch((e) => setError(errorMessage(e)));
  }, []);
  const finish = async (path: string, body: unknown) => {
    setBusy(true);
    setError("");
    try {
      const result = await post<{ token: string }>(path, body);
      await completeLogin(result.token);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    let active = true;
    if (
      providers?.google.enabled &&
      providers.google.client_id &&
      google.current
    ) {
      void renderGoogleButton(
        google.current,
        providers.google.client_id,
        (credential) => {
          void finish("/v1/auth/google", { id_token: credential });
        },
        localeTag(locale),
        () => active,
      ).catch(() => { if (active) setError("pages.google_sign_in_could_not_load_try_email_da24ee"); });
    }
    if (providers?.facebook?.enabled && providers.facebook.app_id) {
      void prepareFacebookLogin(providers.facebook.app_id)
        .then(() => { if (active) setFacebookReady(true); })
        .catch(() => { if (active) setFacebookReady(false); });
    }
    return () => { active = false; };
  }, [providers, locale]);
  useEffect(() => {
    if (seconds <= 0) return;
    const timer = window.setTimeout(() => setSeconds(seconds - 1), 1000);
    return () => clearTimeout(timer);
  }, [seconds]);
  async function sendCode() {
    setBusy(true);
    setError("");
    try {
      await post("/v1/auth/email/code", { email, purpose: "login" });
      setSent(true);
      setSeconds(60);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal title={t("pages.welcome_to_jjtv_597155")} onClose={closeLogin} className="auth-sheet">
      <p className="muted">
        {t("pages.sign_in_to_save_your_place_and_unlock_your_next_episode_c1f11e")}
      </p>
      {error && (
        <p className="error-text" role="alert">
          {t(error)}
        </p>
      )}
      {!providers && !error && <p>{t("pages.loading_sign_in_options_8d6672")}</p>}
      {providers?.email.enabled && (
        <form
          className="form"
          onSubmit={(e) => {
            e.preventDefault();
            if (sent)
              void finish("/v1/auth/email/authenticate", { email, code });
            else void sendCode();
          }}
        >
          <label>
            {t("pages.email_address_b357b5")}
            <input
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(e) => {
                setEmail(e.target.value);
                setSent(false);
              }}
              placeholder="you@example.com"
            />
          </label>
          {sent && (
            <label>
              {t("pages.verification_code_943769")}
              <input
                autoFocus
                inputMode="numeric"
                autoComplete="one-time-code"
                pattern="[0-9]{6}"
                maxLength={6}
                required
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
              />
            </label>
          )}
          <button className="button primary" disabled={busy}>
            {busy
              ? t("pages.please_wait_e80f36")
              : sent
                ? t("pages.sign_in_create_account_04236d")
                : t("pages.send_verification_code_7e2700")}
          </button>
          {!sent && (
            <button
              type="button"
              className="text-button"
              disabled={busy}
              onClick={() => {
                setSent(true);
                setCode("");
                setError("");
                setSeconds(0);
              }}
            >
              {t("pages.i_already_have_a_code_963346")}
            </button>
          )}
          {sent && (
            <button
              type="button"
              className="text-button"
              disabled={seconds > 0 || busy}
              onClick={() => void sendCode()}
            >
              {seconds > 0 ? t("pages.resend_seconds", { seconds: number(seconds) }) : t("pages.resend_code_c37843")}
            </button>
          )}
        </form>
      )}
      {providers?.google.enabled && (
        <div className="social-login" ref={google} />
      )}
      {providers?.facebook?.enabled && (
        <button
          className="button secondary full-width"
          disabled={!facebookReady || busy}
          onClick={() => {
            void facebookAccessToken()
              .then((token) => {
                if (token)
                  void finish("/v1/auth/facebook", { access_token: token });
              })
              .catch((e) => setError(errorMessage(e)));
          }}
        >
          {t("pages.continue_with_facebook_d3eb89")}
        </button>
      )}
      {providers &&
        !providers.email.enabled &&
        !providers.google.enabled &&
        !providers.facebook?.enabled && (
          <p>{t("pages.sign_in_is_currently_unavailable_please_try_again_later_887fb1")}</p>
        )}
      <p className="fine-print">
        {t("pages.by_continuing_you_agree_to_our_885292")}{" "}
        <a href="#legal/terms" onClick={closeLogin}>
          {t("pages.terms_of_use_985a4b")}
        </a>{" "}
        {t("pages.and_be5d5d")} <a href="/privacy.html">{t("pages.privacy_policy_fa2ead")}</a>.
      </p>
    </Modal>
  );
}

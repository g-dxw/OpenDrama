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
      ).catch(() => setError("Google sign-in could not load. Try email."));
    }
    if (providers?.facebook?.enabled && providers.facebook.app_id) {
      void prepareFacebookLogin(providers.facebook.app_id)
        .then(() => setFacebookReady(true))
        .catch(() => setFacebookReady(false));
    }
  }, [providers]);
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
    <Modal title="Welcome to JJTV" onClose={closeLogin}>
      <p className="muted">
        Sign in to save your place and unlock your next episode.
      </p>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {!providers && !error && <p>Loading sign-in options…</p>}
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
            Email address
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
              Verification code
              <input
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
              ? "Please wait…"
              : sent
                ? "Sign in / Create account"
                : "Send verification code"}
          </button>
          {sent && (
            <button
              type="button"
              className="text-button"
              disabled={seconds > 0 || busy}
              onClick={() => void sendCode()}
            >
              {seconds > 0 ? "Resend in " + seconds + "s" : "Resend code"}
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
          Continue with Facebook
        </button>
      )}
      {providers &&
        !providers.email.enabled &&
        !providers.google.enabled &&
        !providers.facebook?.enabled && (
          <p>Sign-in is currently unavailable. Please try again later.</p>
        )}
      <p className="fine-print">
        By continuing, you agree to our{" "}
        <a href="#legal/terms" onClick={closeLogin}>
          Terms of Use
        </a>{" "}
        and <a href="/privacy.html">Privacy Policy</a>.
      </p>
    </Modal>
  );
}

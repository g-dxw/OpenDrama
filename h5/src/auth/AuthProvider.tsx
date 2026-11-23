import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  authToken,
  getMe,
  onUnauthorized,
  setAuthToken,
  ApiError,
  type MeProfile,
} from "../api";

export type AuthStatus = "loading" | "guest" | "user";
interface AuthContextValue {
  status: AuthStatus;
  user: MeProfile | null;
  authError: string | null;
  loginOpen: boolean;
  openLogin: () => void;
  closeLogin: () => void;
  completeLogin: (token: string) => Promise<void>;
  requireAuth: (action: () => void) => void;
  refresh: () => Promise<void>;
  logout: () => void;
}
const AuthContext = createContext<AuthContextValue | null>(null);
export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>(() =>
      authToken() ? "loading" : "guest",
    ),
    [user, setUser] = useState<MeProfile | null>(null),
    [authError, setAuthError] = useState<string | null>(null),
    [loginOpen, setLoginOpen] = useState(false);
  const pending = useRef<(() => void) | null>(null),
    generation = useRef(0);
  const reset = useCallback(() => {
    generation.current++;
    setAuthToken(null);
    setUser(null);
    setStatus("guest");
    setAuthError(null);
    if (pending.current) setLoginOpen(true);
  }, []);
  useEffect(() => {
    onUnauthorized(reset);
    return () => onUnauthorized(null);
  }, [reset]);
  const refresh = useCallback(async () => {
    if (!authToken()) return;
    const request = ++generation.current;
    try {
      const me = await getMe();
      if (request !== generation.current) return;
      setUser(me);
      setStatus("user");
      setAuthError(null);
    } catch (error) {
      if (request !== generation.current) return;
      if (error instanceof ApiError && error.status === 401) reset();
      else
        setAuthError(
          error instanceof Error
            ? error.message
            : "Could not refresh your account. Please retry.",
        );
    }
  }, [reset]);
  useEffect(() => {
    if (authToken()) void refresh();
    else setStatus("guest");
    return () => {
      generation.current++;
    };
  }, [refresh]);
  useEffect(() => {
    if (status !== "user") return;
    const action = pending.current;
    pending.current = null;
    action?.();
  }, [status]);
  const completeLogin = useCallback(
    async (token: string) => {
      setAuthToken(token);
      setLoginOpen(false);
      setStatus("loading");
      await refresh();
    },
    [refresh],
  );
  const requireAuth = useCallback(
    (action: () => void) => {
      if (status === "user") {
        action();
        return;
      }
      pending.current = action;
      if (status !== "loading") setLoginOpen(true);
    },
    [status],
  );
  const logout = useCallback(() => {
    pending.current = null;
    setLoginOpen(false);
    reset();
  }, [reset]);
  const closeLogin = useCallback(() => {
    pending.current = null;
    setLoginOpen(false);
  }, []);
  const value = useMemo(
    () => ({
      status,
      user,
      authError,
      loginOpen,
      openLogin: () => setLoginOpen(true),
      closeLogin,
      completeLogin,
      requireAuth,
      refresh,
      logout,
    }),
    [
      status,
      user,
      authError,
      loginOpen,
      closeLogin,
      completeLogin,
      requireAuth,
      refresh,
      logout,
    ],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}

/** UI-independent consumer transport. Each app supplies its own origin-scoped storage. */
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public data: unknown = null,
  ) {
    super(message);
  }
}
export interface ClientOptions {
  apiBase: string;
  tokenStore: { get(): string | null; set(value: string | null): void };
  deviceId(): string;
  fetch?: typeof fetch;
  attributionPid?(): string | null;
}
export function createConsumerClient(options: ClientOptions) {
  let unauthorized: (() => void) | null = null;
  const send = async <T>(
    method: string,
    path: string,
    body?: unknown,
    headers: Record<string, string> = {},
  ): Promise<T> => {
    let response: Response;
    let requestToken: string | null = null;
    let authenticated = false;
    try {
      requestToken = options.tokenStore.get();
      const rawPid = options.attributionPid?.();
      const pid =
        rawPid && /^[A-Za-z0-9_-]{1,128}$/.test(rawPid) ? rawPid : null;
      const requestHeaders = {
        "Content-Type": "application/json",
        "X-Device-Id": options.deviceId(),
        ...(pid ? { "X-JJTV-Attribution": pid } : {}),
        ...(requestToken ? { Authorization: "Bearer " + requestToken } : {}),
        ...headers,
      };
      authenticated =
        Boolean(requestToken) &&
        new Headers(requestHeaders).get("Authorization") ===
          "Bearer " + requestToken;
      response = await (options.fetch ?? fetch)(
        options.apiBase.replace(/\/$/, "") + path,
        {
          method,
          headers: requestHeaders,
          body: body === undefined ? undefined : JSON.stringify(body),
        },
      );
    } catch {
      throw new ApiError(0, "Could not connect. Please try again.");
    }
    if (!response.ok) {
      const payload = (await response.json().catch(() => null)) as {
        detail?: unknown;
      } | null;
      const detail = payload?.detail;
      const message =
        typeof detail === "string"
          ? detail
          : detail && typeof detail === "object" && "message" in detail
            ? String(detail.message)
            : "Request failed (" + response.status + ")";
      if (
        response.status === 401 &&
        authenticated &&
        requestToken === options.tokenStore.get() &&
        !path.startsWith("/v1/auth/")
      )
        unauthorized?.();
      throw new ApiError(response.status, message, payload);
    }
    if (response.status === 204) return undefined as T;
    return response.json() as Promise<T>;
  };
  return {
    send,
    get: <T>(path: string) => send<T>("GET", path),
    post: <T>(path: string, body: unknown, headers?: Record<string, string>) =>
      send<T>("POST", path, body, headers),
    authToken: () => options.tokenStore.get(),
    setAuthToken: (value: string | null) => options.tokenStore.set(value),
    onUnauthorized: (handler: (() => void) | null) => {
      unauthorized = handler;
    },
  };
}

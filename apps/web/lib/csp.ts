import { API_BASE_URL, KEYCLOAK_URL } from "./auth/config";

/** An origin from a configured URL; a value that is not a URL adds nothing,
 * so a typo blocks the connection instead of widening the policy. */
function origin(url: string): string[] {
  try {
    return [new URL(url).origin];
  } catch {
    return [];
  }
}

/**
 * The page's Content-Security-Policy (measured 2026-10-08, Next 15.5 + antd 6.6).
 *
 * - `script-src`: a per-request nonce with `strict-dynamic`. Next stamps the
 *   nonce on its own scripts and the chunks they load inherit trust; no inline
 *   script runs without it. `next dev` also needs `'unsafe-eval'` (React's
 *   refresh), and only there.
 * - `style-src 'unsafe-inline'`: antd's CSS-in-JS writes `<style>` elements at
 *   runtime and the server-extracted ones carry no nonce, and every component
 *   sets `style` attributes, which no nonce can cover. Styles cannot run code;
 *   scripts are where the policy holds.
 * - `connect-src`: this origin, the API and the identity provider (token
 *   refresh). `img-src blob:` is the feedback screenshots' previews.
 * - `frame-ancestors 'none'`: no page is framed (Caddy also sends
 *   X-Frame-Options DENY for the web host).
 */
export function contentSecurityPolicy({
  nonce,
  dev,
}: {
  nonce: string;
  dev: boolean;
}): string {
  const api = origin(API_BASE_URL);
  const idp = origin(KEYCLOAK_URL);
  const directives: Record<string, string[]> = {
    "default-src": ["'self'"],
    "script-src": [
      "'self'",
      `'nonce-${nonce}'`,
      "'strict-dynamic'",
      ...(dev ? ["'unsafe-eval'"] : []),
    ],
    "style-src": ["'self'", "'unsafe-inline'"],
    "img-src": ["'self'", "blob:", "data:"],
    "font-src": ["'self'", "data:"],
    "connect-src": ["'self'", ...api, ...idp, ...(dev ? ["ws:"] : [])],
    "frame-src": ["'self'", ...idp],
    "worker-src": ["'self'", "blob:"],
    "object-src": ["'none'"],
    "base-uri": ["'self'"],
    "form-action": ["'self'", ...idp],
    "frame-ancestors": ["'none'"],
  };
  return Object.entries(directives)
    .map(([name, values]) => `${name} ${values.join(" ")}`)
    .join("; ");
}

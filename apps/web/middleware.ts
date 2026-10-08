import { NextResponse, type NextRequest } from "next/server";
import { contentSecurityPolicy } from "./lib/csp";

/**
 * A Content-Security-Policy on every page, with a fresh nonce per request.
 *
 * Next reads the nonce back from the request's CSP header and stamps it on
 * its own scripts, which is why the policy is set on the request as well as
 * on the response, and why the root layout renders dynamically (a page
 * prerendered at build time has no nonce). The policy itself is `lib/csp.ts`.
 */
export function middleware(request: NextRequest) {
  const nonce = btoa(crypto.randomUUID());
  const policy = contentSecurityPolicy({
    nonce,
    dev: process.env.NODE_ENV !== "production",
  });

  const headers = new Headers(request.headers);
  headers.set("x-nonce", nonce);
  headers.set("Content-Security-Policy", policy);

  const response = NextResponse.next({ request: { headers } });
  response.headers.set("Content-Security-Policy", policy);
  return response;
}

export const config = {
  // Pages only: static assets and the health probe carry no script to guard,
  // and a prefetch does not render a document.
  matcher: [
    {
      // A path with an extension is a file from public/ (no Next script).
      source: "/((?!api/|_next/static|_next/image|.*\\.[a-z0-9]+$).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};

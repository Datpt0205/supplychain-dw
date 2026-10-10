---
status: Accepted
date: 2026-10-07
source:
    - ../../infra/compose/docker-compose.host.yml
    - ../../infra/caddy/Caddyfile
    - ../../apps/api/src/dw_api/settings.py # validate_for_profile
    - ../../infra/keycloak/dw-realm.json # dw-web redirect URIs from DW_PUBLIC_WEB_URL
---

# 0009. A hosted deployment serves web, API and sign-in on three hostnames from env, behind a Caddy overlay; every public URL is https when deployed

Accepted in the first product built on the platform (its ADR 0023, 2026-10-05,
amended 2026-10-07), and upstreamed from there without its runbook, which
names that product's own settings.

## Decision

- **Three hostnames, one variable each:** `DW_WEB_HOST`, `DW_API_HOST`,
  `DW_AUTH_HOST`. No domain is written in the repository; the overlay requires
  all three (`:?`), so a missing one fails at `docker compose config`.
- **`infra/compose/docker-compose.host.yml`**, applied last on top of the prod
  or uat overlay, adds Caddy (`2.11.7-alpine`, pinned by tag and digest; trivy
  0 HIGH/CRITICAL on 2026-10-07). Caddy is the only service with a port off the
  loopback. It sits on two networks only: `dw-ingress` (published ports, ACME)
  and `dw-proxy` (internal: Caddy, web, api, Keycloak), so it reaches no
  Postgres, Qdrant or Valkey.
- **Every public URL is derived** from the hostnames: the issuer, CORS origin,
  `DW_API_PUBLIC_BASE_URL`, `DW_PUBLIC_WEB_URL`, `KC_HOSTNAME`, and the web's
  `NEXT_PUBLIC_*` build args (a new hostname is a rebuild, not a restart).
- **One trusted proxy address.** `dw-proxy` has a fixed subnet and Caddy a fixed
  address outside the dynamic range; uvicorn (`FORWARDED_ALLOW_IPS`) and
  Keycloak (`KC_PROXY_TRUSTED_ADDRESSES`) trust `X-Forwarded-*` from it alone,
  so per-IP limits see the real client. A CDN in front needs `trusted_proxies`
  in the Caddyfile first.
- **Allow-lists per host:** the API host proxies `/api/*` only (`/metrics` is
  unauthenticated and relies on the internal network); the auth host proxies
  `/realms/*`, `/resources/*` and `/robots.txt`, never the `master` realm. The
  Keycloak admin console stays on the loopback port, reached over an SSH
  tunnel (`KC_HOSTNAME_ADMIN`).
- **Edge headers:** HSTS one year with `includeSubDomains` (no preload),
  `nosniff`, `Referrer-Policy`, `X-Frame-Options: DENY` on web and API (not on
  the auth host: keycloak-js frames its login-status page); `Server`, `Via`,
  `X-Powered-By` removed. No CSP yet: it needs measuring against Next.js and
  antd first. Caddy writes no access log, so the Zalo webhook's secret header
  is never recorded.
- **`ApiSettings.validate_for_profile`, when `is_deployed`:** the OIDC issuer,
  every CORS origin and `DW_API_PUBLIC_BASE_URL` must start with `https://`.
  The gate is `is_deployed`, never `profile == "production"`.
- **The realm takes the portal URL from env:** `dw-web`'s redirect URIs, web
  origin and post-logout URIs are `${DW_PUBLIC_WEB_URL:http://localhost:3000}`
  in `dw-realm.json`. The import runs on an empty Keycloak database only; an
  existing realm is edited by hand.

## First deployment, in short

1. DNS: three A/AAAA records to the host; ports 80 and 443 open.
2. `.env`: the three hostnames, a deployed profile's requirements (real model
   provider, embeddings, vector store), fresh secrets.
3. `docker compose --env-file .env -f infra/compose/docker-compose.yml -f
infra/compose/docker-compose.prod.yml -f infra/compose/docker-compose.host.yml
--profile full up -d --build`.
4. Check: the web host signs in; `https://<api>/api/v1/...` answers;
   `https://<api>/metrics` and `https://<auth>/admin` answer 404.

## Alternatives considered

- **One hostname split by path** (`/api`, `/auth`). Keycloak under a sub-path
  needs `KC_HTTP_RELATIVE_PATH` and shares the app's cookie origin; separate
  hostnames keep the sign-in cookie on its own origin.
- **nginx.** Works; Caddy obtains and renews certificates with less
  configuration.

## Consequences

- Changing a domain is rebuilding the web image.
- The chat webhook (ADR 0008) is usable only behind this overlay; a CDN in front
  must let Zalo's "Java" user agent through.
- The overlay was measured in the first product's stack (same base compose
  services); this repository verifies it with `docker compose config` only.

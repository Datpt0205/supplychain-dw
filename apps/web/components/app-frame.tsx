"use client";

import { useEffect, useMemo, type ReactNode } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Badge, Typography } from "antd";
import { Bot, Loader2, LogOut } from "lucide-react";
import { AppShell, Button, type AppShellItem } from "@dw/ui";
import { useAuth } from "../lib/auth/auth-context";
import { AUTH_MODE } from "../lib/auth/config";
import { useNavBadges } from "../lib/nav/badges";
import { NAV_ITEMS } from "../lib/nav/registry";
import { barNav, visibleNav } from "../lib/nav/visible";
import { LoginScreen } from "./login-screen";
import { NotificationBell } from "./notification-bell";
import { SessionChip } from "./session-chip";
import { WorkspaceSwitcher } from "./workspace-switcher";
import { FeedbackLauncher } from "./feedback/launcher";

function CenteredCard({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <div className="w-full max-w-md rounded-2xl border bg-card p-8 text-center shadow-sm">
        {children}
      </div>
    </div>
  );
}

/** Auth gate + application shell. Children render only once a workspace is
 * active; every other state gets a dedicated full-screen view. */
export function AppFrame({ children }: { children: ReactNode }) {
  const { status, error, logout, active, isPlatformOperator, hasScope, roles } =
    useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const badges = useNavBadges();

  // The nav the user can actually reach, and the bar drawn from it: a
  // context's own pages for someone whose work is that context alone.
  const bar = useMemo(
    () =>
      barNav(visibleNav(NAV_ITEMS, { isPlatformOperator, hasScope, roles })),
    [isPlatformOperator, hasScope, roles],
  );
  const nav = bar.items;
  // Where the logo points; the old /feedback page is gone (spec 003 US5).
  const home = nav[0]?.href ?? "/";

  // Where to send the user when the page they are on isn't one they can use.
  const redirectTo = useMemo(() => {
    if (status !== "ready") return null;
    // A Platform Operator with no tenant membership (ADR-002) belongs on the
    // provisioning area — every other page needs a workspace context.
    if (!active && isPlatformOperator && !pathname.startsWith("/platform")) {
      return "/platform";
    }
    return null;
  }, [status, active, isPlatformOperator, pathname]);

  useEffect(() => {
    if (redirectTo) router.replace(redirectTo);
  }, [redirectTo, router]);

  // The dev-login page renders outside the gate (it is how you authenticate),
  // and so does its layer-check fixture (app/dev-login/layer-check), which
  // exists only in dev-auth builds.
  if (
    pathname === "/dev-login" ||
    (AUTH_MODE === "dev" && pathname === "/dev-login/layer-check")
  ) {
    return <>{children}</>;
  }

  if (status === "loading") {
    return (
      <div className="flex min-h-screen items-center justify-center text-muted-foreground">
        <Loader2 className="mr-2 size-5 animate-spin" /> Loading…
      </div>
    );
  }

  if (status === "unauthenticated") return <LoginScreen />;

  if (status === "error") {
    return (
      <CenteredCard>
        <h1 className="text-lg font-semibold">Could not reach the server</h1>
        <p className="mt-2 text-sm text-muted-foreground">{error}</p>
        <div className="mt-5 flex justify-center gap-2">
          <Button onClick={() => window.location.reload()}>Retry</Button>
          <Button variant="outline" onClick={logout}>
            Sign out
          </Button>
        </div>
      </CenteredCard>
    );
  }

  if (status === "no-workspace") {
    return (
      <CenteredCard>
        <h1 className="text-lg font-semibold">No workspace yet</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          You are signed in but not assigned to any workspace yet. Contact an
          administrator to be granted access.
        </p>
        <Button className="mt-5" variant="outline" onClick={logout}>
          <LogOut /> Sign out
        </Button>
      </CenteredCard>
    );
  }

  // status === "ready". While a redirect is pending, hold a loader instead of
  // mounting a page the user can't use — that is what stops its data calls from
  // firing a 403 (and a toast) before the redirect lands.
  if (redirectTo) {
    return (
      <div className="flex min-h-screen items-center justify-center text-muted-foreground">
        <Loader2 className="mr-2 size-5 animate-spin" /> Đang chuyển…
      </div>
    );
  }
  const navItems: AppShellItem[] = nav.map((item) => {
    const Icon = item.icon;
    const count = item.badgeKey ? badges[item.badgeKey] : undefined;
    return {
      key: item.href,
      // aria-hidden: antd names an icon after its glyph, so the item read
      // "read Bản tin hôm nay" to a screen reader.
      icon: <Icon className="size-4" aria-hidden />,
      label: (
        // Out of the tab order: the menu is the keyboard's way in (AppShellItem).
        <Link href={item.href} title={item.hint} tabIndex={-1}>
          {item.label}
          {count ? <Badge count={count} size="small" className="ml-2" /> : null}
        </Link>
      ),
    };
  });
  const selectedKey = nav.find((item) =>
    item.exact
      ? pathname === item.href
      : pathname === item.href || pathname.startsWith(item.href + "/"),
  )?.href;

  return (
    <>
      <AppShell
        brand={
          <Link
            href={home}
            aria-label={
              bar.context
                ? `Digital Worker · ${bar.context.product}, về trang đầu`
                : "Digital Worker, về trang đầu"
            }
            className="flex items-center gap-2.5"
          >
            <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground">
              <Bot className="size-4" />
            </span>
            <Typography.Text
              strong
              className="hidden whitespace-nowrap sm:inline"
            >
              Digital Worker
              {bar.context ? (
                <Typography.Text type="secondary">
                  {` · ${bar.context.product}`}
                </Typography.Text>
              ) : null}
            </Typography.Text>
          </Link>
        }
        items={navItems}
        onNavigate={(href) => router.push(href)}
        selectedKey={selectedKey}
        extra={
          <>
            <WorkspaceSwitcher />
            <NotificationBell />
            <SessionChip />
          </>
        }
        navLabel="Điều hướng chính"
        menuLabel="Mở menu"
      >
        {children}
      </AppShell>
      {/* Spec 003 US5: feedback is a utility beside the app, pinned to the
          bottom-left corner of every page rather than a line in the nav. */}
      <FeedbackLauncher />
    </>
  );
}

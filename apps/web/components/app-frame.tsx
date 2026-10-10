"use client";

import { useEffect, useMemo, type ReactNode } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Badge, Button, Card, Flex, Spin, Typography, theme } from "antd";
import { LogoutOutlined, RobotOutlined } from "@ant-design/icons";
import { AppShell, type AppShellItem } from "@dw/ui";
import { useAuth } from "../lib/auth/auth-context";
import { AUTH_MODE } from "../lib/auth/config";
import { useNavBadges } from "../lib/nav/badges";
import { NAV_ITEMS } from "../lib/nav/registry";
import type { NavContext } from "../lib/nav/types";
import { barNav, visibleNav } from "../lib/nav/visibility";
import { LoginScreen } from "./login-screen";
import { NotificationBell } from "./notification-bell";
import { SessionChip } from "./session-chip";
import { WorkspaceSwitcher } from "./workspace-switcher";
import { FeedbackLauncher } from "./feedback/launcher";

function CenteredCard({ children }: { children: ReactNode }) {
  return (
    <Flex align="center" justify="center" className="min-h-screen p-6">
      <Card className="w-full max-w-md text-center">{children}</Card>
    </Flex>
  );
}

function FullScreenSpin({ tip }: { tip: string }) {
  return (
    <Flex
      align="center"
      justify="center"
      gap="small"
      className="min-h-screen"
      role="status"
    >
      <Spin />
      <Typography.Text type="secondary">{tip}</Typography.Text>
    </Flex>
  );
}

/** The brand mark: the theme's primary colour, so a product's theme owns it. */
function Brand({
  href,
  context,
}: {
  href: string;
  /** The bounded context the bar is, named beside the brand. */
  context: NavContext | null;
}) {
  const { token } = theme.useToken();
  return (
    <Link
      href={href}
      aria-label={
        context
          ? `Digital Worker · ${context.product}, về trang đầu`
          : "Digital Worker, về trang đầu"
      }
      className="flex items-center gap-2.5"
    >
      <span
        className="flex size-8 shrink-0 items-center justify-center"
        style={{
          background: token.colorPrimary,
          color: token.colorTextLightSolid,
          borderRadius: token.borderRadiusLG,
        }}
      >
        <RobotOutlined aria-hidden />
      </span>
      <Typography.Text strong className="hidden whitespace-nowrap sm:inline">
        Digital Worker
        {context ? (
          <Typography.Text type="secondary">{` · ${context.product}`}</Typography.Text>
        ) : null}
      </Typography.Text>
    </Link>
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

  if (status === "loading") return <FullScreenSpin tip="Đang tải…" />;

  if (status === "unauthenticated") return <LoginScreen />;

  if (status === "error") {
    return (
      <CenteredCard>
        <Typography.Title level={4}>
          Không kết nối được máy chủ
        </Typography.Title>
        <Typography.Paragraph type="secondary">{error}</Typography.Paragraph>
        <Flex justify="center" gap="small">
          <Button type="primary" onClick={() => window.location.reload()}>
            Thử lại
          </Button>
          <Button onClick={logout}>Đăng xuất</Button>
        </Flex>
      </CenteredCard>
    );
  }

  if (status === "no-workspace") {
    return (
      <CenteredCard>
        <Typography.Title level={4}>
          Bạn chưa thuộc workspace nào
        </Typography.Title>
        <Typography.Paragraph type="secondary">
          Bạn đã đăng nhập nhưng chưa được thêm vào workspace nào. Hãy nhờ quản
          trị viên của công ty cấp quyền.
        </Typography.Paragraph>
        <Button icon={<LogoutOutlined aria-hidden />} onClick={logout}>
          Đăng xuất
        </Button>
      </CenteredCard>
    );
  }

  // status === "ready". While a redirect is pending, hold a loader instead of
  // mounting a page the user can't use — that is what stops its data calls from
  // firing a 403 (and a notice) before the redirect lands.
  if (redirectTo) return <FullScreenSpin tip="Đang chuyển…" />;

  const navItems: AppShellItem[] = nav.map((item) => {
    const Icon = item.icon;
    const count = item.badgeKey ? badges[item.badgeKey] : undefined;
    return {
      key: item.href,
      // aria-hidden: antd names an icon after its glyph, so the item read
      // "read Bản tin hôm nay" to a screen reader.
      icon: <Icon aria-hidden />,
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
        brand={<Brand href={home} context={bar.context} />}
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
      {/* Feedback is a utility beside the app, pinned to the bottom-left
          corner of every page rather than a line in the nav. */}
      <FeedbackLauncher />
    </>
  );
}

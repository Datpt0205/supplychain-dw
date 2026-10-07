"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ChevronDown, LogOut, ScrollText } from "lucide-react";
import { SettingOutlined } from "@ant-design/icons";
import { Badge, cn } from "@dw/ui";
import { useAuth } from "../lib/auth/auth-context";
import { NAV_ITEMS } from "../lib/nav/registry";
import { roleLabel } from "../lib/nav/roles";

// Seniority low → high. Only the most senior role is badged, by its FULL name
// ("Tenant Admin", "System Admin") so two different admins never both read as a
// bare "admin". A platform operator outranks every in-tenant role and shows as
// "Platform Admin". A role a bounded context adds is unranked and therefore
// only badged when the person holds nothing else.
const ROLE_RANK = ["member", "approver", "org_admin", "platform_admin"];

// The menu's second door to the audit trail reads its scope from the nav
// registry's entry, the one owner of "who is offered /audit": hard-coding a
// scope here kept offering every member a page the API refuses
// (approval-audit-and-workspace/02).
const AUDIT_LOG = NAV_ITEMS.find((item) => item.href === "/audit");

function badgeVariant(
  role: string | undefined,
  operator: boolean,
): "warning" | "success" | "secondary" {
  if (operator || role === "platform_admin" || role === "org_admin")
    return "warning";
  if (role === "approver") return "success";
  return "secondary";
}

/** Header chip: who is signed in, where to go next, and sign-out. */
export function SessionChip() {
  const { status, displayName, roles, isPlatformOperator, logout, hasScope } =
    useAuth();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    window.addEventListener("mousedown", onClick);
    return () => window.removeEventListener("mousedown", onClick);
  }, [open]);

  if (status !== "ready") return null;

  const topRole = [...roles].sort(
    (a, b) => ROLE_RANK.indexOf(b) - ROLE_RANK.indexOf(a),
  )[0];
  // A platform operator (creates tenants) outranks any in-tenant role.
  const roleText = isPlatformOperator
    ? "Platform Admin"
    : topRole
      ? roleLabel(topRole)
      : null;
  const roleVariant = badgeVariant(topRole, isPlatformOperator);

  return (
    <div className="relative flex items-center gap-1.5" ref={ref}>
      <button
        type="button"
        // Named in words: below `sm`, and between `lg` and `xl`, the chip is
        // its initial alone, and a button called "N" names nobody.
        aria-label={`Tài khoản: ${displayName || "User"}${roleText ? `, ${roleText}` : ""}`}
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 rounded-full border bg-card py-1 pl-1 pr-2.5 text-sm shadow-sm transition-colors hover:bg-accent/50"
      >
        <span className="flex size-6 items-center justify-center rounded-full bg-primary text-xs font-semibold text-primary-foreground">
          {(displayName || "?").charAt(0).toUpperCase()}
        </span>
        {/* Not between lg and xl: the horizontal menu needs that width, and
            at 992 px every item had overflowed into "…". */}
        <span className="hidden max-w-[9rem] truncate font-medium sm:inline lg:hidden xl:inline">
          {displayName || "User"}
        </span>
        {roleText && (
          <Badge
            variant={roleVariant}
            className="hidden md:inline-flex lg:hidden xl:inline-flex"
          >
            {roleText}
          </Badge>
        )}
        <ChevronDown
          className={cn(
            "size-4 shrink-0 text-muted-foreground transition-transform",
            open && "rotate-180",
          )}
        />
      </button>

      {open && (
        <div className="absolute right-0 top-[calc(100%+8px)] z-50 w-64 overflow-hidden rounded-xl border bg-popover p-1.5 shadow-xl">
          <div className="px-2.5 py-2">
            <p className="truncate text-sm font-semibold">{displayName}</p>
            <div className="mt-1 flex flex-wrap gap-1">
              {roleText && <Badge variant={roleVariant}>{roleText}</Badge>}
            </div>
          </div>
          <div className="my-1 border-t" />
          <Link
            href="/settings"
            onClick={() => setOpen(false)}
            className="flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          >
            <SettingOutlined className="text-base" aria-hidden /> Cài đặt cá
            nhân
          </Link>
          {AUDIT_LOG && (!AUDIT_LOG.scope || hasScope(AUDIT_LOG.scope)) && (
            <Link
              href="/audit"
              onClick={() => setOpen(false)}
              className="flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            >
              <ScrollText className="size-4" /> Audit log
            </Link>
          )}
          <div className="my-1 border-t" />
          <button
            type="button"
            onClick={logout}
            className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
          >
            <LogOut className="size-4" /> Sign out
          </button>
        </div>
      )}
    </div>
  );
}

import {
  BrainCircuit,
  Building,
  Building2,
  ClipboardCheck,
  Home,
  Library,
  MessageSquarePlus,
  Network,
  PlugZap,
  Scale,
  ScrollText,
  Settings,
  ShieldCheck,
} from "lucide-react";
import type { NavItem } from "./types";
import { supplyChainNav } from "../../app/supply-chain/_meta/nav";

/**
 * Platform-level nav (not owned by any bounded context). Sending feedback is
 * not a nav item — it is the round button at the bottom-left of every page
 * (spec 003 US5); only the admins' inbox is listed here, under Admin. The
 * provisioning area (ADR-002) is shown only to a Platform Operator.
 */
const platformNav: NavItem[] = [
  {
    href: "/",
    label: "Home",
    hint: "What this workspace runs and where to go next",
    icon: Home,
    exact: true,
  },
  {
    href: "/approvals",
    label: "Duyệt",
    hint: "Yêu cầu đang chờ một người quyết",
    icon: ClipboardCheck,
    scope: "approvals.read",
    // BGĐ decides a product case's step 6 here.
    inContextBar: true,
  },
  {
    href: "/knowledge",
    label: "Knowledge",
    hint: "Documents the workers retrieve from, and their ingest jobs",
    icon: Library,
    scope: "knowledge.read",
  },
  {
    href: "/memory",
    label: "Memory",
    hint: "Evidence-backed long-term memory",
    icon: BrainCircuit,
    scope: "memory.read",
  },
  {
    href: "/integrations",
    label: "Integrations",
    hint: "The tool catalogue and the policies it runs under",
    icon: PlugZap,
    scope: "integrations.read",
  },
  {
    href: "/audit",
    label: "Audit log",
    hint: "Every run, decision and side effect, in order",
    icon: ScrollText,
    // The scope `GET /audit/events` enforces; `approvals.read` (every member
    // has it) offered a link to a page the API refuses.
    scope: "audit.events",
  },
  {
    href: "/admin",
    label: "Roles & access",
    hint: "Manage users, roles and scopes in this workspace",
    icon: ShieldCheck,
    scope: "platform.members.read",
    exact: true,
    administration: true,
  },
  {
    href: "/admin/workspaces",
    label: "Workspaces",
    hint: "The tenant's workspaces (departments)",
    icon: Building2,
    scope: "platform.workspaces.write",
    administration: true,
  },
  {
    href: "/admin/hierarchy",
    label: "Reporting line",
    hint: "Who reports to whom in the workspace",
    icon: Network,
    scope: "platform.members.write",
    administration: true,
  },
  {
    href: "/admin/separation-of-duties",
    label: "Separation of duties",
    hint: "Duties no one person may hold, and this tenant's waivers",
    icon: Scale,
    scope: "platform.roles.read",
    administration: true,
  },
  {
    href: "/admin/settings",
    label: "Tenant settings",
    hint: "Tenant name, timezone and language",
    icon: Settings,
    scope: "platform.tenant.settings.write",
    administration: true,
  },
  {
    href: "/admin/feedback",
    label: "Feedback inbox",
    hint: "What members reported, with their screenshots",
    icon: MessageSquarePlus,
    scope: "platform.members.read",
    administration: true,
  },
  {
    href: "/platform",
    label: "Platform",
    hint: "Tenants, org admins and operators",
    icon: Building,
    operatorOnly: true,
    administration: true,
  },
];

/**
 * Sidebar nav registry — the ONLY place nav manifests are aggregated.
 *
 * PLUG-IN POINT: a bounded context ships its own manifest inside its route
 * folder (e.g. `app/<context>/_meta/nav.ts` exporting `NavItem[]`) and adds
 * exactly one import + one spread here, in the context's wiring PR:
 *
 *   import { contextNav } from "../../app/<context>/_meta/nav";
 *   ...contextNav, ...platformNav,
 *
 * Day-to-day nav changes (labels, icons, scopes, new pages) then live in the
 * context-owned manifest — this file is edited once per context and frozen.
 */
export const NAV_ITEMS: NavItem[] = [
  // <context navs plug in here>
  ...supplyChainNav,
  ...platformNav,
];

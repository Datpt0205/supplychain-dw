import {
  ApartmentOutlined,
  AuditOutlined,
  BankOutlined,
  BookOutlined,
  BulbOutlined,
  CheckSquareOutlined,
  ClusterOutlined,
  ApiOutlined,
  HomeOutlined,
  MessageOutlined,
  SafetyOutlined,
  SettingOutlined,
  SplitCellsOutlined,
} from "@ant-design/icons";
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
    label: "Trang chủ",
    hint: "Workspace này chạy gì và nên đi đâu tiếp",
    icon: HomeOutlined,
    exact: true,
  },
  {
    href: "/approvals",
    label: "Duyệt",
    hint: "Yêu cầu đang chờ một người quyết",
    icon: CheckSquareOutlined,
    scope: "approvals.read",
    // BGĐ decides a product case's step 6 here.
    inContextBar: true,
  },
  {
    href: "/knowledge",
    label: "Tri thức",
    hint: "Tài liệu worker truy xuất, và việc nạp chúng",
    icon: BookOutlined,
    scope: "knowledge.read",
  },
  {
    href: "/memory",
    label: "Bộ nhớ",
    hint: "Bộ nhớ dài hạn có bằng chứng",
    icon: BulbOutlined,
    scope: "memory.read",
  },
  {
    href: "/integrations",
    label: "Tích hợp",
    hint: "Danh mục công cụ và chính sách chúng chạy theo",
    icon: ApiOutlined,
    scope: "integrations.read",
  },
  {
    href: "/audit",
    label: "Nhật ký kiểm toán",
    hint: "Mọi lượt chạy, quyết định và thao tác, theo thứ tự",
    icon: AuditOutlined,
    // The scope `GET /audit/events` enforces; `approvals.read` (every member
    // has it) offered a link to a page the API refuses.
    scope: "audit.events",
  },
  {
    href: "/admin",
    label: "Vai trò và quyền",
    hint: "Quản lý người dùng, vai và quyền trong workspace",
    icon: SafetyOutlined,
    scope: "platform.members.read",
    exact: true,
    administration: true,
  },
  {
    href: "/admin/workspaces",
    label: "Workspace",
    hint: "Các workspace (phòng ban) của công ty",
    icon: ClusterOutlined,
    scope: "platform.workspaces.write",
    administration: true,
  },
  {
    href: "/admin/hierarchy",
    label: "Tuyến báo cáo",
    hint: "Ai báo cáo cho ai trong workspace",
    icon: ApartmentOutlined,
    scope: "platform.members.write",
    administration: true,
  },
  {
    href: "/admin/separation-of-duties",
    label: "Tách nhiệm",
    hint: "Nhiệm vụ không một người nào được giữ cùng lúc, và các miễn trừ",
    icon: SplitCellsOutlined,
    scope: "platform.roles.read",
    administration: true,
  },
  {
    href: "/admin/settings",
    label: "Thiết lập công ty",
    hint: "Tên công ty, múi giờ và ngôn ngữ",
    icon: SettingOutlined,
    scope: "platform.tenant.settings.write",
    administration: true,
  },
  {
    href: "/admin/feedback",
    label: "Hộp phản hồi",
    hint: "Thành viên báo gì, kèm ảnh màn hình",
    icon: MessageOutlined,
    scope: "platform.members.read",
    administration: true,
  },
  {
    href: "/platform",
    label: "Nền tảng",
    hint: "Công ty, quản trị viên công ty và người vận hành",
    icon: BankOutlined,
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

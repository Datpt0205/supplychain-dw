/**
 * Role keys the workbench branches on.
 *
 * A role decides what a user is shown; a scope decides what they may do, and the
 * API checks the scope on every request regardless of what the shell rendered.
 * Hiding a link is therefore navigation, never authorization.
 */

/**
 * How a role key reads to a person: the ONE label table for roles in the web
 * app (session menu, members, hierarchy, dev login, role catalog). The tenant
 * role catalog in the database owns the keys; this owns only the wording, and
 * `__tests__/roles.test.ts` fails when the catalog seeds a key this table does
 * not name.
 *
 * PLUG-IN POINT: a bounded context adds its own roles' labels here, in the same
 * change that seeds them.
 */
const ROLE_LABELS: Record<string, string> = {
  member: "Nhân viên",
  approver: "Người duyệt",
  manager: "Quản lý",
  director: "Giám đốc",
  executive: "Ban điều hành",
  // Naming (2026-09-10): org_admin manages users, roles and settings;
  // platform_admin is the in-tenant role that passes every scope. Neither is
  // the cross-tenant operator who creates tenants (OPERATOR_LABEL).
  org_admin: "Quản trị hệ thống",
  platform_admin: "Quản trị toàn quyền",
  // Supply Chain (dw_supply_chain), seeded by its own migrations; the
  // catalog's "Supply Chain — …" names are for the admin's role list.
  sc_viewer: "Xem chuỗi cung ứng",
  sc_operator: "Điều phối đơn hàng",
  sc_supply_lead: "Trưởng phòng Cung ứng",
  sc_finance: "Kế toán",
  sc_qc: "Kiểm soát chất lượng (QC)",
  sc_logistics: "Vận chuyển (Logistics)",
  sc_warehouse: "Kho",
  sc_rnd: "Nghiên cứu và phát triển (R&D)",
  sc_mkt: "Marketing (MKT)",
  sc_bod: "Ban Giám đốc",
  sc_process_admin: "Quản trị quy trình cung ứng",
};

/** How a platform operator (creates tenants, ADR-002) is named; not a role key. */
export const OPERATOR_LABEL = "Quản trị nền tảng";

/** What a role with no label and no catalog name reads as: never its code. */
export const UNNAMED_ROLE = "Vai khác";

/**
 * Label for a single role key. A key this table does not name falls back to
 * the name the role catalog gave it, then to a generic label: a raw key like
 * `sc_operator` is never shown to a person.
 */
export function roleLabel(key: string, catalogName?: string): string {
  return ROLE_LABELS[key] ?? catalogName ?? UNNAMED_ROLE;
}

/** Deduplicated, comma-joined labels for a member's role keys. */
export function roleLabels(keys: readonly string[]): string {
  return [...new Set(keys.map((key) => roleLabel(key)))].join(", ");
}

/** True when the user holds at least one of the roles an item asks for. */
export function hasAnyRole(
  userRoles: readonly string[],
  required: readonly string[],
): boolean {
  const held = new Set(userRoles);
  return required.some((role) => held.has(role));
}

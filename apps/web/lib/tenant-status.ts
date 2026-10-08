/**
 * A tenant's status as a person reads it (`ck_tenants_status` owns the set).
 * An unknown value — a status added to the database before this table —
 * reads as "Trạng thái khác", never as the raw code.
 */
const TENANT_STATUS: Record<string, { label: string; color: string }> = {
  active: { label: "Đang hoạt động", color: "success" },
  locked: { label: "Đã khóa", color: "error" },
  offboarding: { label: "Đang rời nền tảng", color: "warning" },
  offboarded: { label: "Đã rời nền tảng", color: "default" },
};

export function tenantStatus(status: string): { label: string; color: string } {
  return (
    TENANT_STATUS[status] ?? { label: "Trạng thái khác", color: "default" }
  );
}

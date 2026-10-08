import { z } from "zod";

export const knowledgeDocumentSchema = z.object({
  document_id: z.string().uuid(),
  title: z.string(),
  domain: z.string(),
  classification: z.string(),
  source_version: z.string(),
  index_version: z.string().nullable(),
  chunk_count: z.number().int(),
  created_at: z.string(),
  scope: z.string(),
});
export type KnowledgeDocument = z.infer<typeof knowledgeDocumentSchema>;

export const ingestJobSchema = z.object({
  job_id: z.string().uuid(),
  status: z.string(),
  title: z.string(),
  filename: z.string(),
  scope: z.string(),
  attempts: z.number().int(),
  error: z.string().nullable(),
  // Non-empty when the file was indexed but not read whole. `error` means the
  // job failed; a partial read is the third state, and the uploader is the only
  // person who can do anything about it.
  warnings: z.array(z.string()),
  document_id: z.string().uuid().nullable(),
  chunk_count: z.number().int().nullable(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type IngestJob = z.infer<typeof ingestJobSchema>;

export const memoryItemSchema = z.object({
  memory_id: z.string().uuid(),
  worker_id: z.string(),
  memory_type: z.string(),
  content: z.string(),
  confidence: z.number(),
  classification: z.string(),
  provenance_count: z.number().int(),
  valid_from: z.string(),
  created_by_run_id: z.string().uuid(),
});
export type MemoryItem = z.infer<typeof memoryItemSchema>;

export const integrationSchema = z.object({
  tool: z.string(),
  version: z.string(),
  description: z.string(),
  side_effect_level: z.string(),
  approval_policy: z.string(),
  requires_approval: z.boolean(),
  idempotent: z.boolean(),
  timeout_seconds: z.number().int(),
  required_scopes: z.array(z.string()),
});
export type Integration = z.infer<typeof integrationSchema>;

/**
 * The workspace roster. Every business record stores an owner as a bare user
 * id; this is what turns one into a person the UI can show or pick.
 */
export const workspaceMemberSchema = z.object({
  user_id: z.string().uuid(),
  display_name: z.string(),
  email: z.string().nullable(),
  role_keys: z.array(z.string()),
  department: z.string(),
  // Extra permission sets granted to this member on top of their role scopes.
  permission_set_keys: z.array(z.string()),
});
export type WorkspaceMember = z.infer<typeof workspaceMemberSchema>;

/**
 * Admin console read models. Unlike the operator-only /platform provisioning
 * views, these are tenant-scoped: an org admin manages the workspaces, tenant
 * settings and role catalog of their own tenant.
 */

// A department/workspace of the caller's tenant. `archived` hides it from
// day-to-day use without deleting the records that reference it.
export const adminWorkspaceSchema = z.object({
  workspace_id: z.string().uuid(),
  slug: z.string(),
  name: z.string(),
  member_count: z.number().int(),
  archived: z.boolean(),
});
export type AdminWorkspace = z.infer<typeof adminWorkspaceSchema>;

// The caller's tenant, as the settings form reads and writes it. `slug` and
// `status` are read-only here; only name/timezone/locale are editable.
// The autonomy levels a Digital Worker runs at, lowest to highest. Mirrors
// `dw_kernel.autonomy.AUTONOMY_LEVELS` across the API boundary; the order is the
// meaning, and a later level may do everything an earlier one may.
export const autonomyLevelSchema = z.enum(["A0", "A1", "A2", "A3", "A4"]);
export type AutonomyLevel = z.infer<typeof autonomyLevelSchema>;

export const adminTenantSchema = z.object({
  tenant_id: z.string(),
  slug: z.string(),
  name: z.string(),
  timezone: z.string().nullable(),
  locale: z.string().nullable(),
  status: z.string(),
  // How much of the tenant's CRM data a member can see: "open" (everyone in the
  // workspace sees everything) or "restricted" (a manager sees only their own
  // team's records, following the reporting hierarchy).
  record_visibility: z.string(),
  // The most autonomy any of this tenant's workers may run at. It lowers a
  // worker's own level and never raises it.
  max_autonomy_level: autonomyLevelSchema,
});
export type AdminTenant = z.infer<typeof adminTenantSchema>;

// One role in the tenant's catalog and the scopes it grants. Read-only: roles
// are versioned config changed by deploy, not from the UI.
export const adminRoleSchema = z.object({
  key: z.string(),
  name: z.string(),
  scopes: z.array(z.string()),
});
export type AdminRole = z.infer<typeof adminRoleSchema>;

// A named bundle of scopes an admin can grant to individual members on top of
// their role. Read-only catalog; granting happens per-member on the Members
// page. Same shape as a role, but granted individually rather than by role.
export const adminPermissionSetSchema = z.object({
  key: z.string(),
  name: z.string(),
  scopes: z.array(z.string()),
});
export type AdminPermissionSet = z.infer<typeof adminPermissionSetSchema>;

// A separation-of-duty rule: no membership may hold a scope from each side.
// `waivable` false means no tenant can lift it. `waiver` is this tenant's open
// decision to lift it, with the reason recorded when it was made.
export const adminSodWaiverSchema = z.object({
  reason: z.string(),
  granted_by: z.string(),
  granted_at: z.string(),
  // Null while it waits for a second admin; until then it lifts nothing.
  confirmed_by: z.string().nullable(),
  confirmed_at: z.string().nullable(),
});
export type AdminSodWaiver = z.infer<typeof adminSodWaiverSchema>;

export const adminSodRuleSchema = z.object({
  key: z.string(),
  description: z.string(),
  left_scopes: z.array(z.string()),
  right_scopes: z.array(z.string()),
  waivable: z.boolean(),
  waiver: adminSodWaiverSchema.nullable(),
});
export type AdminSodRule = z.infer<typeof adminSodRuleSchema>;

// One message in the caller's own in-app inbox. `link` is an app-relative
// path (the database refuses anything else).
export const notificationSchema = z.object({
  id: z.string(),
  title: z.string(),
  body: z.string(),
  link: z.string().nullable(),
  created_at: z.string(),
  read_at: z.string().nullable(),
});
export type AppNotification = z.infer<typeof notificationSchema>;

export const inboxSchema = z.object({
  items: z.array(notificationSchema),
  unread: z.number().int(),
});
export type Inbox = z.infer<typeof inboxSchema>;

// A workspace member as the reporting-hierarchy editor reads it: who they are,
// the roles they hold, and who they report to. `manager_user_id` is null for a
// root of the tree; the API rejects an edit that would create a cycle.
export const hierarchyMemberSchema = z.object({
  user_id: z.string().uuid(),
  display_name: z.string(),
  email: z.string().nullable(),
  role_keys: z.array(z.string()),
  manager_user_id: z.string().nullable(),
});
export type HierarchyMember = z.infer<typeof hierarchyMemberSchema>;

import { z } from "zod";
import {
  approvalSchema,
  auditEventSchema,
  demoUserSchema,
  devSessionSchema,
  integrationSchema,
  knowledgeDocumentSchema,
  ingestJobSchema,
  memoryItemSchema,
  errorResponseSchema,
  healthResponseSchema,
  readinessResponseSchema,
  runSchema,
  timelineEventSchema,
  workspaceMemberSchema,
  poCaseSchema,
  supplierUpdateSchema,
  delayImpactAnalysisSchema,
  slaEvaluationSchema,
  missingUpdateStatusSchema,
  caseTransitionSchema,
  attentionItemSchema,
  followUpSchema,
  portfolioSummarySchema,
  aiWorkResponseSchema,
  dailyBriefSchema,
  dailyBriefSummarySchema,
  adminWorkspaceSchema,
  adminTenantSchema,
  adminRoleSchema,
  adminPermissionSetSchema,
  adminSodRuleSchema,
  inboxSchema,
  hierarchyMemberSchema,
  type AdminWorkspace,
  type AdminTenant,
  type AutonomyLevel,
  type AdminRole,
  type AdminPermissionSet,
  type AdminSodRule,
  type Inbox,
  type HierarchyMember,
  type Approval,
  type AuditEvent,
  type DemoUser,
  type DevSessionInfo,
  type ErrorResponse,
  type Integration,
  type KnowledgeDocument,
  type IngestJob,
  type MemoryItem,
  type HealthResponse,
  type ReadinessResponse,
  type Run,
  type TimelineEvent,
  type WorkspaceMember,
  type POCase,
  type SupplierUpdate,
  type DelayImpactAnalysis,
  type SLAEvaluation,
  type MissingUpdateStatus,
  type CaseTransition,
  type AttentionItem,
  type FollowUp,
  type PortfolioSummary,
  type AIWorkResponse,
  type DataView,
  type DailyBrief,
  type DailyBriefSummary,
  type POCaseListFilter,
  pageSchema,
  pageQueryString,
  type Page,
  type PageParams,
} from "@dw/contracts";
import type { components } from "./generated/platform";
import type {
  components as SupplyChainComponents,
  operations as SupplyChainOperations,
} from "./generated/supply-chain";

/**
 * Typed API client core. Endpoint methods generated from OpenAPI are layered on
 * top of this transport; hand-written duplicate types are forbidden.
 *
 * SKELETON NOTE: bounded-context methods (tender, work_ops, preparation) were
 * removed with their contexts — new contexts add their methods below, with the
 * zod schema mirrored in @dw/contracts.
 */

const feedbackAttachmentSchema = z.object({
  id: z.string(),
  content_type: z.string(),
  size_bytes: z.number().int(),
});
export type FeedbackAttachment = z.infer<typeof feedbackAttachmentSchema>;

/** One feedback in the admin inbox (spec 003 US5): module, page, text,
 * suggestion, screenshots. `category` predates the form and reads "bug". */
const feedbackItemSchema = z.object({
  id: z.string(),
  author_name: z.string(),
  category: z.string(),
  message: z.string(),
  created_at: z.string(),
  module: z.string().nullable().optional(),
  suggestion: z.string().nullable().optional(),
  page_path: z.string().nullable().optional(),
  attachments: z.array(feedbackAttachmentSchema),
});
export type FeedbackItem = z.infer<typeof feedbackItemSchema>;

/** Whether the caller has linked their own Zalo for notifications. */
const zaloStatusSchema = z.object({
  linked: z.boolean(),
  zalo_subject: z.string().nullable().optional(),
});
export type ZaloStatus = z.infer<typeof zaloStatusSchema>;

/** A one-time connect token + how to redeem it in the bot. */
const zaloConnectSchema = z.object({
  code: z.string(),
  deep_link: z.string().nullable(),
  instructions: z.string(),
});
export type ZaloConnect = z.infer<typeof zaloConnectSchema>;

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly body: ErrorResponse,
  ) {
    super(`${body.code}: ${body.message}`);
    this.name = "ApiError";
  }
}

export interface ApiClientOptions {
  baseUrl: string;
  /** Returns the bearer token for the current session, if any. */
  getAccessToken?: () => Promise<string | null> | string | null;
  fetchImpl?: typeof fetch;
}

// ---- platform provisioning (ADR-002) --------------------------------------

const platformTenantSchema = z.object({
  id: z.string(),
  slug: z.string(),
  name: z.string(),
  status: z.string(),
  plan_id: z.string().nullable(),
  workspace_count: z.number(),
  member_count: z.number(),
  created_at: z.string(),
});
export type PlatformTenant = z.infer<typeof platformTenantSchema>;

const platformOperatorSchema = z.object({
  user_id: z.string(),
  email: z.string().nullable(),
  display_name: z.string(),
  note: z.string().nullable(),
  created_at: z.string(),
});
export type PlatformOperator = z.infer<typeof platformOperatorSchema>;

const userRefSchema = z.object({
  user_id: z.string(),
  email: z.string().nullable(),
  display_name: z.string(),
});
export type PlatformUserRef = z.infer<typeof userRefSchema>;

/** `GET /po-cases`'s query parameters, from the types generated off the
 * route itself: a parameter renamed on the server fails this package's
 * typecheck, instead of the server silently ignoring the old name and
 * answering every drill-down with the unfiltered list. */
type ListPOCasesQuery = NonNullable<
  SupplyChainOperations["list_po_cases_api_v1_supply_chain_po_cases_get"]["parameters"]["query"]
>;

/** `POST /case-query`'s body, from the route's own generated types. */
type CaseQueryBody =
  SupplyChainOperations["answer_case_query_route_api_v1_supply_chain_case_query_post"]["requestBody"]["content"]["application/json"];

/** True only when A and B are the same type, both ways. */
type SameType<A, B> = [A] extends [B]
  ? [B] extends [A]
    ? true
    : false
  : false;

type Generated = components["schemas"];
type SupplyChainGenerated = SupplyChainComponents["schemas"];

// The hand-kept zod mirror must describe exactly what the route declares:
// if either side drifts, these lines stop compiling instead of a response
// quietly losing a field (zod strips unknown keys) in production. Mutual
// assignability alone misses a new OPTIONAL field — FastAPI leaves any
// field with a default out of `required` — so every object's key set is
// compared too.
const _aiWorkResponseMirrorsTheRoute: [
  SameType<AIWorkResponse, SupplyChainGenerated["AIWorkResponseView"]>,
  SameType<
    keyof AIWorkResponse,
    keyof SupplyChainGenerated["AIWorkResponseView"]
  >,
  SameType<
    keyof AIWorkResponse["understood"],
    keyof SupplyChainGenerated["UnderstoodView"]
  >,
  SameType<
    keyof AIWorkResponse["citations"][number],
    keyof SupplyChainGenerated["CitationView"]
  >,
  SameType<
    keyof Extract<DataView, { type: "case_table" }>,
    keyof SupplyChainGenerated["CaseTableDataView"]
  >,
  SameType<
    keyof Extract<DataView, { type: "case_link" }>,
    keyof SupplyChainGenerated["CaseLinkDataView"]
  >,
] = [true, true, true, true, true, true];
void _aiWorkResponseMirrorsTheRoute;

const _dailyBriefMirrorsTheRoute: [
  SameType<DailyBrief, SupplyChainGenerated["DailyBriefView"]>,
  SameType<keyof DailyBrief, keyof SupplyChainGenerated["DailyBriefView"]>,
  SameType<
    keyof DailyBrief["groups"][number],
    keyof SupplyChainGenerated["BriefGroupView"]
  >,
  SameType<
    keyof DailyBrief["groups"][number]["entries"][number],
    keyof SupplyChainGenerated["BriefEntryView"]
  >,
] = [true, true, true, true];
void _dailyBriefMirrorsTheRoute;

const _briefSummaryMirrorsTheRoute: [
  SameType<DailyBriefSummary, SupplyChainGenerated["DailyBriefSummaryView"]>,
  SameType<
    keyof DailyBriefSummary,
    keyof SupplyChainGenerated["DailyBriefSummaryView"]
  >,
  SameType<
    keyof DailyBriefSummary["summary"],
    keyof SupplyChainGenerated["BriefSummaryView"]
  >,
  SameType<
    keyof DailyBriefSummary["summary"]["sentences"][number],
    keyof SupplyChainGenerated["BriefSummarySentenceView"]
  >,
] = [true, true, true, true];
void _briefSummaryMirrorsTheRoute;

const _sodRuleMirrorsTheRoute: [
  SameType<AdminSodRule, Generated["SodRuleView"]>,
  SameType<keyof AdminSodRule, keyof Generated["SodRuleView"]>,
  SameType<
    keyof NonNullable<AdminSodRule["waiver"]>,
    keyof Generated["SodWaiverView"]
  >,
] = [true, true, true];
void _sodRuleMirrorsTheRoute;

const _inboxMirrorsTheRoute: [
  SameType<Inbox, Generated["InboxView"]>,
  SameType<keyof Inbox, keyof Generated["InboxView"]>,
  SameType<keyof Inbox["items"][number], keyof Generated["NotificationView"]>,
] = [true, true, true];
void _inboxMirrorsTheRoute;

const _followUpMirrorsTheRoute: [
  SameType<FollowUp, SupplyChainGenerated["FollowUpItemView"]>,
  SameType<keyof FollowUp, keyof SupplyChainGenerated["FollowUpItemView"]>,
] = [true, true];
void _followUpMirrorsTheRoute;

export class ApiClient {
  constructor(private readonly options: ApiClientOptions) {}

  async request<T>(
    method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE",
    path: string,
    schema: z.ZodType<T>,
    init?: { body?: unknown; idempotencyKey?: string; signal?: AbortSignal },
  ): Promise<T> {
    const fetchImpl = this.options.fetchImpl ?? fetch;
    const headers: Record<string, string> = { Accept: "application/json" };

    const token = await this.options.getAccessToken?.();
    if (token) headers.Authorization = `Bearer ${token}`;
    if (init?.body !== undefined) headers["Content-Type"] = "application/json";
    if (init?.idempotencyKey) headers["Idempotency-Key"] = init.idempotencyKey;

    const response = await fetchImpl(`${this.options.baseUrl}${path}`, {
      method,
      headers,
      body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
      signal: init?.signal,
    });

    const json: unknown = await response.json().catch(() => null);
    if (!response.ok) {
      const parsedError = errorResponseSchema.safeParse(json);
      throw new ApiError(
        response.status,
        parsedError.success
          ? parsedError.data
          : {
              code: "internal",
              message: `HTTP ${response.status}`,
              details: {},
            },
      );
    }
    return schema.parse(json);
  }

  /**
   * A call whose success is a file, not JSON.
   *
   * Separate from `request` because that one parses a schema, and a CSV or a
   * PDF has none — the caller wants the bytes and the `Content-Disposition`
   * the server chose. Errors still arrive as JSON and are raised the same way.
   */
  async rawRequest(
    method: "GET" | "POST",
    path: string,
    init?: { body?: unknown; signal?: AbortSignal },
  ): Promise<Response> {
    const fetchImpl = this.options.fetchImpl ?? fetch;
    const headers: Record<string, string> = {};
    const token = await this.options.getAccessToken?.();
    if (token) headers.Authorization = `Bearer ${token}`;
    if (init?.body !== undefined) headers["Content-Type"] = "application/json";
    const response = await fetchImpl(`${this.options.baseUrl}${path}`, {
      method,
      headers,
      body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
      signal: init?.signal,
    });
    if (response.ok) return response;
    const json: unknown = await response.json().catch(() => null);
    const parsed = errorResponseSchema.safeParse(json);
    throw new ApiError(
      response.status,
      parsed.success
        ? parsed.data
        : { code: "internal", message: `HTTP ${response.status}`, details: {} },
    );
  }

  /**
   * A call whose success has no body. Separate from `request` because that one
   * parses a schema, and 204 has nothing to parse.
   */
  async requestNoContent(
    method: "POST" | "PUT" | "PATCH" | "DELETE",
    path: string,
    init?: { body?: unknown; signal?: AbortSignal },
  ): Promise<void> {
    const fetchImpl = this.options.fetchImpl ?? fetch;
    const headers: Record<string, string> = { Accept: "application/json" };
    const token = await this.options.getAccessToken?.();
    if (token) headers.Authorization = `Bearer ${token}`;
    if (init?.body !== undefined) headers["Content-Type"] = "application/json";
    const response = await fetchImpl(`${this.options.baseUrl}${path}`, {
      method,
      headers,
      body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
      signal: init?.signal,
    });
    if (response.ok || response.status === 204) return;
    const json: unknown = await response.json().catch(() => null);
    const parsed = errorResponseSchema.safeParse(json);
    throw new ApiError(
      response.status,
      parsed.success
        ? parsed.data
        : { code: "internal", message: `HTTP ${response.status}`, details: {} },
    );
  }

  getHealth(): Promise<HealthResponse> {
    return this.request("GET", "/api/v1/health", healthResponseSchema);
  }

  getReadiness(): Promise<ReadinessResponse> {
    return this.request("GET", "/api/v1/ready", readinessResponseSchema);
  }

  // ---- directory ----------------------------------------------------------

  /** The workspace roster, for owner and assignee pickers. */
  listWorkspaceMembers(): Promise<WorkspaceMember[]> {
    return this.request(
      "GET",
      "/api/v1/directory/members",
      z.array(workspaceMemberSchema),
    );
  }

  // ---- admin: membership management --------------------------------------

  /** Grant an existing (signed-in) identity access to a workspace with roles. */
  async grantMember(input: {
    email: string;
    workspaceId: string;
    roleKeys: string[];
    department?: string;
  }): Promise<{ userId: string; email: string | null; displayName: string }> {
    const r = await this.request(
      "POST",
      "/api/v1/admin/members",
      z.object({
        user_id: z.string(),
        email: z.string().nullable(),
        display_name: z.string(),
      }),
      {
        body: {
          email: input.email,
          workspace_id: input.workspaceId,
          role_keys: input.roleKeys,
          department: input.department ?? "general",
        },
      },
    );
    return { userId: r.user_id, email: r.email, displayName: r.display_name };
  }

  /** Signed-in identities not yet in the workspace — the grant email picker. */
  listGrantCandidates(): Promise<PlatformUserRef[]> {
    return this.request(
      "GET",
      "/api/v1/directory/candidates",
      z.array(userRefSchema),
    );
  }

  /** Remove an identity's access to a workspace of the caller's tenant. */
  revokeMember(userId: string, workspaceId: string): Promise<void> {
    return this.requestNoContent(
      "DELETE",
      `/api/v1/admin/members/${userId}?workspace_id=${workspaceId}`,
    );
  }

  // ---- admin: workspaces / tenant / roles ---------------------------------

  /** Departments (workspaces) of the caller's tenant. */
  listAdminWorkspaces(): Promise<AdminWorkspace[]> {
    return this.request(
      "GET",
      "/api/v1/admin/workspaces",
      z.array(adminWorkspaceSchema),
    );
  }

  createWorkspace(input: {
    name: string;
    slug: string;
  }): Promise<{ workspace_id: string; slug: string; name: string }> {
    return this.request(
      "POST",
      "/api/v1/admin/workspaces",
      z.object({
        workspace_id: z.string(),
        slug: z.string(),
        name: z.string(),
      }),
      { body: { name: input.name, slug: input.slug } },
    );
  }

  renameWorkspace(
    workspaceId: string,
    name: string,
  ): Promise<{ workspace_id: string; slug: string; name: string }> {
    return this.request(
      "PATCH",
      `/api/v1/admin/workspaces/${workspaceId}`,
      z.object({
        workspace_id: z.string(),
        slug: z.string(),
        name: z.string(),
      }),
      { body: { name } },
    );
  }

  archiveWorkspace(workspaceId: string): Promise<void> {
    return this.requestNoContent(
      "POST",
      `/api/v1/admin/workspaces/${workspaceId}/archive`,
    );
  }

  /** The caller's tenant, for the settings form. */
  getTenantSettings(): Promise<AdminTenant> {
    return this.request("GET", "/api/v1/admin/tenant", adminTenantSchema);
  }

  updateTenantSettings(input: {
    name?: string;
    timezone?: string;
    locale?: string;
    record_visibility?: "open" | "restricted";
    max_autonomy_level?: AutonomyLevel;
  }): Promise<AdminTenant> {
    return this.request("PATCH", "/api/v1/admin/tenant", adminTenantSchema, {
      body: input,
    });
  }

  /** The tenant's role catalog and the scopes each role grants (read-only). */
  listAdminRoles(): Promise<AdminRole[]> {
    return this.request("GET", "/api/v1/admin/roles", z.array(adminRoleSchema));
  }

  /** The catalog of permission sets an admin can grant to members (read-only). */
  listPermissionSets(): Promise<AdminPermissionSet[]> {
    return this.request(
      "GET",
      "/api/v1/admin/permission-sets",
      z.array(adminPermissionSetSchema),
    );
  }

  /** Replace a member's extra permission sets. 403 if a set exceeds the caller. */
  setMemberPermissionSets(userId: string, keys: string[]): Promise<void> {
    return this.requestNoContent(
      "PUT",
      `/api/v1/admin/members/${userId}/permission-sets`,
      { body: { permission_set_keys: keys } },
    );
  }

  // ---- notifications ---------------------------------------------------------

  /** The caller's own latest notifications and how many are unread. */
  listNotifications(signal?: AbortSignal): Promise<Inbox> {
    return this.request("GET", "/api/v1/notifications", inboxSchema, {
      signal,
    });
  }

  /** Mark one of the caller's own read. 404 for anyone else's. */
  markNotificationRead(id: string): Promise<void> {
    return this.requestNoContent(
      "POST",
      `/api/v1/notifications/${encodeURIComponent(id)}/read`,
    );
  }

  markAllNotificationsRead(): Promise<void> {
    return this.requestNoContent("POST", "/api/v1/notifications/read-all");
  }

  // ---- admin: separation of duties ------------------------------------------

  /** Every separation-of-duty rule, with this tenant's open waiver of each. */
  listSeparationOfDuties(): Promise<AdminSodRule[]> {
    return this.request(
      "GET",
      "/api/v1/admin/separation-of-duties",
      z.array(adminSodRuleSchema),
    );
  }

  /** Lift a waivable rule for the whole tenant, on the record. 409 if the
   * rule is a floor or already waived. */
  waiveSeparationOfDutiesRule(ruleKey: string, reason: string): Promise<void> {
    return this.requestNoContent(
      "POST",
      `/api/v1/admin/separation-of-duties/${encodeURIComponent(ruleKey)}/waiver`,
      { body: { reason } },
    );
  }

  /** Close the tenant's waiver. 409 while members still hold both sides. */
  revokeSeparationOfDutiesWaiver(
    ruleKey: string,
    reason: string,
  ): Promise<void> {
    return this.requestNoContent(
      "POST",
      `/api/v1/admin/separation-of-duties/${encodeURIComponent(ruleKey)}/waiver/revoke`,
      { body: { reason } },
    );
  }

  // ---- admin: reporting hierarchy -----------------------------------------

  /** The workspace roster with each member's manager, for the hierarchy editor. */
  listHierarchy(): Promise<HierarchyMember[]> {
    return this.request(
      "GET",
      "/api/v1/admin/hierarchy",
      z.array(hierarchyMemberSchema),
    );
  }

  /** Set (or clear, with null) who a member reports to. Rejected on a cycle. */
  setManager(userId: string, managerUserId: string | null): Promise<void> {
    return this.requestNoContent("PUT", `/api/v1/admin/hierarchy/${userId}`, {
      body: { manager_user_id: managerUserId },
    });
  }

  // ---- platform provisioning (operator-only) ------------------------------

  listTenants(): Promise<PlatformTenant[]> {
    return this.request(
      "GET",
      "/api/v1/platform/tenants",
      z.array(platformTenantSchema),
    );
  }

  /** Every signed-in identity — the picker behind the /platform email boxes. */
  listPlatformUsers(): Promise<PlatformUserRef[]> {
    return this.request(
      "GET",
      "/api/v1/platform/users",
      z.array(userRefSchema),
    );
  }

  createTenant(input: { slug: string; name: string; planId: string }): Promise<{
    tenant_id: string;
    workspace_id: string;
    slug: string;
    name: string;
  }> {
    return this.request(
      "POST",
      "/api/v1/platform/tenants",
      z.object({
        tenant_id: z.string(),
        workspace_id: z.string(),
        slug: z.string(),
        name: z.string(),
      }),
      { body: { slug: input.slug, name: input.name, plan_id: input.planId } },
    );
  }

  renameTenant(tenantId: string, name: string): Promise<PlatformTenant> {
    return this.request(
      "PATCH",
      `/api/v1/platform/tenants/${tenantId}`,
      platformTenantSchema,
      { body: { name } },
    );
  }

  setTenantLocked(tenantId: string, locked: boolean): Promise<void> {
    return this.requestNoContent(
      "POST",
      `/api/v1/platform/tenants/${tenantId}/${locked ? "lock" : "unlock"}`,
    );
  }

  assignOrgAdmin(tenantId: string, email: string): Promise<PlatformUserRef> {
    return this.request(
      "POST",
      `/api/v1/platform/tenants/${tenantId}/org-admins`,
      userRefSchema,
      { body: { email } },
    );
  }

  listOperators(): Promise<PlatformOperator[]> {
    return this.request(
      "GET",
      "/api/v1/platform/operators",
      z.array(platformOperatorSchema),
    );
  }

  addOperator(email: string, note?: string): Promise<PlatformUserRef> {
    return this.request("POST", "/api/v1/platform/operators", userRefSchema, {
      body: { email, note: note ?? null },
    });
  }

  removeOperator(userId: string): Promise<void> {
    return this.requestNoContent(
      "DELETE",
      `/api/v1/platform/operators/${userId}`,
    );
  }

  // ---- feedback -----------------------------------------------------------

  /**
   * Send a bug report to the workspace's admins (spec 003 US5). Any signed-in
   * member may. One multipart request carries the text and the screenshots,
   * so a feedback lands whole or not at all.
   */
  async submitFeedback(input: {
    module: string;
    message: string;
    suggestion?: string;
    page_path?: string;
    images?: File[];
  }): Promise<void> {
    const fetchImpl = this.options.fetchImpl ?? fetch;
    const form = new FormData();
    form.append("module", input.module);
    form.append("message", input.message);
    if (input.suggestion) form.append("suggestion", input.suggestion);
    if (input.page_path) form.append("page_path", input.page_path);
    for (const image of input.images ?? [])
      form.append("images", image, image.name);
    // No Content-Type header: the browser sets the multipart boundary itself.
    const headers: Record<string, string> = { Accept: "application/json" };
    const token = await this.options.getAccessToken?.();
    if (token) headers.Authorization = `Bearer ${token}`;
    const response = await fetchImpl(
      `${this.options.baseUrl}/api/v1/feedback`,
      {
        method: "POST",
        headers,
        body: form,
      },
    );
    if (!response.ok) {
      const json: unknown = await response.json().catch(() => null);
      const parsed = errorResponseSchema.safeParse(json);
      throw new ApiError(
        response.status,
        parsed.success
          ? parsed.data
          : {
              code: "http_error",
              message: `HTTP ${response.status}`,
              details: {},
            },
      );
    }
  }

  /** The feedback inbox — admins only (the API enforces the scope). */
  listFeedback(params: PageParams = {}): Promise<Page<FeedbackItem>> {
    return this.request(
      "GET",
      `/api/v1/feedback${pageQueryString(params)}`,
      pageSchema(feedbackItemSchema),
    );
  }

  /** One screenshot's bytes, read with the session's token (inbox scope). */
  async feedbackAttachment(
    feedbackId: string,
    attachmentId: string,
  ): Promise<Blob> {
    const fetchImpl = this.options.fetchImpl ?? fetch;
    const headers: Record<string, string> = {};
    const token = await this.options.getAccessToken?.();
    if (token) headers.Authorization = `Bearer ${token}`;
    const response = await fetchImpl(
      `${this.options.baseUrl}/api/v1/feedback/${feedbackId}/attachments/${attachmentId}`,
      { headers },
    );
    if (!response.ok)
      throw new Error(`download failed: HTTP ${response.status}`);
    return response.blob();
  }

  // ---- approvals / runs ---------------------------------------------------

  listApprovals(params: PageParams = {}): Promise<Page<Approval>> {
    return this.request(
      "GET",
      `/api/v1/approvals${pageQueryString(params)}`,
      pageSchema(approvalSchema),
    );
  }

  decideApproval(
    approvalId: string,
    decision: { approve: boolean; comment?: string },
  ): Promise<Approval> {
    return this.request(
      "POST",
      `/api/v1/approvals/${approvalId}/decisions`,
      approvalSchema,
      { body: decision },
    );
  }

  getRun(runId: string): Promise<Run> {
    return this.request("GET", `/api/v1/runs/${runId}`, runSchema);
  }

  getRunTimeline(runId: string): Promise<TimelineEvent[]> {
    return this.request(
      "GET",
      `/api/v1/runs/${runId}/timeline`,
      z.array(timelineEventSchema),
    );
  }

  // ---- dev demo (local only; the API refuses these in production) ---------

  listDemoUsers(): Promise<DemoUser[]> {
    return this.request(
      "GET",
      "/api/v1/dev/demo-users",
      z.array(demoUserSchema),
    );
  }

  createDevSession(subject: string): Promise<DevSessionInfo> {
    return this.request("POST", "/api/v1/dev/session", devSessionSchema, {
      body: { subject },
    });
  }

  // ---- platform inventories ----------------------------------------------

  listKnowledgeDocuments(
    params: PageParams & { domain?: string } = {},
  ): Promise<Page<KnowledgeDocument>> {
    const { domain, ...page } = params;
    const query = pageQueryString(page);
    const suffix = domain
      ? `${query ? `${query}&` : "?"}domain=${encodeURIComponent(domain)}`
      : query;
    return this.request(
      "GET",
      `/api/v1/knowledge/documents${suffix}`,
      pageSchema(knowledgeDocumentSchema),
    );
  }

  /** Upload a raw file for async ingestion (multipart). Returns the queued job. */
  async uploadKnowledgeDocument(
    file: File | Blob,
    meta: {
      title: string;
      filename?: string;
      domain?: string;
      classification?: string;
      source_version?: string;
      scope?: "tenant" | "global";
    },
  ): Promise<IngestJob> {
    const fetchImpl = this.options.fetchImpl ?? fetch;
    const form = new FormData();
    const filename =
      meta.filename ?? (file instanceof File ? file.name : "document");
    form.append("file", file, filename);
    form.append("title", meta.title);
    if (meta.domain) form.append("domain", meta.domain);
    if (meta.classification) form.append("classification", meta.classification);
    if (meta.source_version) form.append("source_version", meta.source_version);
    if (meta.scope) form.append("scope", meta.scope);

    // No Content-Type header: the browser sets multipart boundary itself.
    const headers: Record<string, string> = { Accept: "application/json" };
    const token = await this.options.getAccessToken?.();
    if (token) headers.Authorization = `Bearer ${token}`;

    const response = await fetchImpl(
      `${this.options.baseUrl}/api/v1/knowledge/documents`,
      { method: "POST", headers, body: form },
    );
    const json: unknown = await response.json().catch(() => null);
    if (!response.ok) {
      const parsed = errorResponseSchema.safeParse(json);
      throw new ApiError(
        response.status,
        parsed.success
          ? parsed.data
          : {
              code: "internal",
              message: `HTTP ${response.status}`,
              details: {},
            },
      );
    }
    return ingestJobSchema.parse(json);
  }

  getIngestJob(jobId: string): Promise<IngestJob> {
    return this.request(
      "GET",
      `/api/v1/knowledge/documents/jobs/${jobId}`,
      ingestJobSchema,
    );
  }

  deleteKnowledgeDocument(documentId: string): Promise<void> {
    return this.requestNoContent(
      "DELETE",
      `/api/v1/knowledge/documents/${documentId}`,
    );
  }

  listMemoryItems(params: PageParams = {}): Promise<Page<MemoryItem>> {
    return this.request(
      "GET",
      `/api/v1/memory/items${pageQueryString(params)}`,
      pageSchema(memoryItemSchema),
    );
  }

  listIntegrations(): Promise<Integration[]> {
    return this.request(
      "GET",
      "/api/v1/integrations",
      z.array(integrationSchema),
    );
  }

  // ---- audit --------------------------------------------------------------

  listAuditEvents(params: PageParams = {}): Promise<Page<AuditEvent>> {
    return this.request(
      "GET",
      `/api/v1/audit/events${pageQueryString(params)}`,
      pageSchema(auditEventSchema),
    );
  }

  // ---- zalo notifications -------------------------------------------------

  getZaloStatus(): Promise<ZaloStatus> {
    return this.request("GET", "/api/v1/zalo/status", zaloStatusSchema);
  }

  /** Mint a fresh connect token for the signed-in user to send to the bot. */
  connectZalo(): Promise<ZaloConnect> {
    return this.request("POST", "/api/v1/zalo/connect", zaloConnectSchema);
  }

  disconnectZalo(): Promise<void> {
    return this.requestNoContent("POST", "/api/v1/zalo/disconnect");
  }

  // ---- supply chain ---------------------------------------------------

  listPOCases(
    params: PageParams & POCaseListFilter = {},
  ): Promise<Page<POCase>> {
    const query: ListPOCasesQuery = {
      limit: params.limit,
      // An empty cursor is no cursor — the same rule `pageQueryString` keeps.
      cursor: params.cursor || undefined,
      state: params.state,
      supplier_name: params.supplierName,
      active_only: params.activeOnly || undefined,
    };
    // URLSearchParams, not string concatenation: a supplier name is free
    // text ("Quiet & Sons", diacritics) and must arrive as one exact value.
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null) search.set(key, String(value));
    }
    const rendered = search.toString();
    return this.request(
      "GET",
      `/api/v1/supply-chain/po-cases${rendered ? `?${rendered}` : ""}`,
      pageSchema(poCaseSchema),
    );
  }

  getPOCase(caseId: string): Promise<POCase> {
    return this.request(
      "GET",
      `/api/v1/supply-chain/po-cases/${caseId}`,
      poCaseSchema,
    );
  }

  getSLAEvaluation(caseId: string): Promise<SLAEvaluation> {
    return this.request(
      "GET",
      `/api/v1/supply-chain/po-cases/${caseId}/sla-evaluation`,
      slaEvaluationSchema,
    );
  }

  getMissingUpdateStatus(caseId: string): Promise<MissingUpdateStatus> {
    return this.request(
      "GET",
      `/api/v1/supply-chain/po-cases/${caseId}/missing-update-status`,
      missingUpdateStatusSchema,
    );
  }

  listSupplierUpdates(caseId: string): Promise<SupplierUpdate[]> {
    return this.request(
      "GET",
      `/api/v1/supply-chain/po-cases/${caseId}/supplier-updates`,
      z.array(supplierUpdateSchema),
    );
  }

  listDelayImpactAnalyses(caseId: string): Promise<DelayImpactAnalysis[]> {
    return this.request(
      "GET",
      `/api/v1/supply-chain/po-cases/${caseId}/delay-impact-analyses`,
      z.array(delayImpactAnalysisSchema),
    );
  }

  listCaseTransitions(caseId: string): Promise<CaseTransition[]> {
    return this.request(
      "GET",
      `/api/v1/supply-chain/po-cases/${caseId}/transitions`,
      z.array(caseTransitionSchema),
    );
  }

  /** The tenant's open follow-ups, newest first; the caller's own marked. */
  listFollowUps(): Promise<FollowUp[]> {
    return this.request(
      "GET",
      "/api/v1/supply-chain/follow-ups",
      z.array(followUpSchema),
    );
  }

  /** Mark a follow-up handled. 403 unless it was handed to the caller. */
  closeFollowUp(id: string, note?: string): Promise<void> {
    return this.requestNoContent(
      "POST",
      `/api/v1/supply-chain/follow-ups/${encodeURIComponent(id)}/done`,
      { body: { note: note?.trim() ? note.trim() : null } },
    );
  }

  listAttentionQueue(): Promise<AttentionItem[]> {
    return this.request(
      "GET",
      "/api/v1/supply-chain/attention-queue",
      z.array(attentionItemSchema),
    );
  }

  /** One question about PO cases, answered as structured work. The answer
   * is data the caller renders with its own components — nothing in it is
   * markup, a link or an action. */
  askCaseQuery(
    question: string,
    signal?: AbortSignal,
  ): Promise<AIWorkResponse> {
    const body: CaseQueryBody = { question };
    return this.request(
      "POST",
      "/api/v1/supply-chain/case-query",
      aiWorkResponseSchema,
      { body, signal },
    );
  }

  getPortfolioSummary(): Promise<PortfolioSummary> {
    return this.request(
      "GET",
      "/api/v1/supply-chain/control-tower/summary",
      portfolioSummarySchema,
    );
  }

  /** What needs handling now, grouped by deterministic signal in the
   * tenant's own order. */
  getDailyBrief(): Promise<DailyBrief> {
    return this.request(
      "GET",
      "/api/v1/supply-chain/daily-brief",
      dailyBriefSchema,
    );
  }

  /** The brief with a model's summary of it, every sentence already
   * checked by the server. Spends a model call: ask on request only. */
  summarizeDailyBrief(signal?: AbortSignal): Promise<DailyBriefSummary> {
    return this.request(
      "POST",
      "/api/v1/supply-chain/daily-brief/summary",
      dailyBriefSummarySchema,
      { signal },
    );
  }
}

export type {
  Approval,
  AuditEvent,
  Page,
  PageParams,
  Run,
  TimelineEvent,
  WorkspaceMember,
  POCase,
  SupplierUpdate,
  DelayImpactAnalysis,
  SLAEvaluation,
  MissingUpdateStatus,
  CaseTransition,
  AttentionItem,
  PortfolioSummary,
  POCaseListFilter,
  AIWorkResponse,
};

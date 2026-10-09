import { z } from "zod";
import {
  approvalSchema,
  approvalViewOutcomeSchema,
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
  poCaseDetailSchema,
  supplierUpdateSchema,
  delayImpactAnalysisSchema,
  slaEvaluationSchema,
  missingUpdateStatusSchema,
  caseTransitionSchema,
  caseApprovalsSchema,
  slaPolicySchema,
  packagingPolicySchema,
  bm04SchemaSchema,
  productProfileSchema,
  poCommercialSchema,
  poPaymentSchema,
  documentDraftSchema,
  stepProposalSchema,
  supplierMessageSchema,
  attentionItemSchema,
  followUpSchema,
  caseDocumentSchema,
  packagingDesignSchema,
  packagingStateSchema,
  productCaseSchema,
  productCategorySchema,
  productCaseDetailSchema,
  productCaseStepSchema,
  orderPlacedSchema,
  productCaseTransitionSchema,
  productActionDutiesSchema,
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
  type ApprovalViewOutcome,
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
  type POCaseDetail,
  type POCaseLine,
  type CreatePOInput,
  type OrderKind,
  type SupplierUpdate,
  type DelayImpactAnalysis,
  type SLAEvaluation,
  type MissingUpdateStatus,
  type CaseTransition,
  type CaseApprovals,
  type SLAPolicy,
  type PackagingPolicy,
  type Bm04Field,
  type Bm04Schema,
  type Incoterm,
  type PaymentKind,
  type PricedLine,
  type ProductProfile,
  type POCommercial,
  type POPayment,
  type RedactableAmount,
  type DocumentDraft,
  type StepProposal,
  type ProposalResultField,
  type SupplierMessage,
  type SupplierMessagePurpose,
  type SupplierMessageStatus,
  type DraftField,
  type DraftStatus,
  type TemplateFieldKind,
  type AttentionItem,
  type FollowUp,
  type CaseDocument,
  type DocumentType,
  type PackagingAction,
  type PackagingDesign,
  type PackagingState,
  type PackagingStepOption,
  type PreProductionTest,
  type ReviewStatus,
  type ProductCase,
  type ProductCategory,
  type ProductCaseDetail,
  type ProductCaseStep,
  type OrderPlaced,
  type PendingReview,
  type ProductCaseTransition,
  type ProductActionDuties,
  type ProductCaseListFilter,
  type ProductCaseStepInput,
  type SampleRound,
  type ProductActionOption,
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

/** GET /me: the caller's verified access context in the active workspace. */
const meSchema = z.object({
  tenant_id: z.string(),
  workspace_id: z.string(),
  principal_id: z.string(),
  roles: z.array(z.string()),
  scopes: z.array(z.string()),
  clearance: z.string(),
  plan_id: z.string(),
  feature_flags: z.array(z.string()),
});
export type Me = z.infer<typeof meSchema>;

/** Whether the caller has linked their own Zalo. No chat id: the browser has no use for it. */
const zaloStatusSchema = z.object({
  linked: z.boolean(),
});
export type ZaloStatus = z.infer<typeof zaloStatusSchema>;

/** A one-time `/start <code>` for the bot: redeemable once, until `expires_at`. */
const zaloConnectSchema = z.object({
  code: z.string(),
  deep_link: z.string().nullable(),
  expires_at: z.string(),
});
export type ZaloConnect = z.infer<typeof zaloConnectSchema>;

/** The workspace the caller's Zalo commands act in; both null until chosen. */
const zaloWorkspaceSchema = z.object({
  tenant_id: z.string().nullable(),
  workspace_id: z.string().nullable(),
});
export type ZaloWorkspace = z.infer<typeof zaloWorkspaceSchema>;

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

/** `GET /product-cases`' query parameters, from the route's generated types. */
type ListProductCasesQuery = NonNullable<
  SupplyChainOperations["list_product_cases_api_v1_supply_chain_product_cases_get"]["parameters"]["query"]
>;

/** `POST /product-cases/{id}/transitions`' body, from the route's generated types. */
type ProductCaseStepBody =
  SupplyChainOperations["create_product_case_transition_api_v1_supply_chain_product_cases__case_id__transitions_post"]["requestBody"]["content"]["application/json"];

/** `POST /product-cases`' body, from the route's generated types. */
type ProposeProductCaseBody =
  SupplyChainOperations["propose_product_case_api_v1_supply_chain_product_cases_post"]["requestBody"]["content"]["application/json"];

/** `POST /po-cases/{id}/create-po`'s body (step 10), from the route's types. */
type CreatePOBody =
  SupplyChainOperations["create_po_route_api_v1_supply_chain_po_cases__case_id__create_po_post"]["requestBody"]["content"]["application/json"];

/** `POST /case-query`'s body, from the route's own generated types. */
type CaseQueryBody =
  SupplyChainOperations["answer_case_query_route_api_v1_supply_chain_case_query_post"]["requestBody"]["content"]["application/json"];

/** `POST /po-cases/{id}/packaging-design/steps`' body (slice PK). */
type TakePackagingStepBody =
  SupplyChainOperations["take_packaging_step_api_v1_supply_chain_po_cases__case_id__packaging_design_steps_post"]["requestBody"]["content"]["application/json"];
// Customer-granted support access, the operators' side (ADR 0024).
const supportStaffSchema = z.object({
  user_id: z.string(),
  email: z.string().nullable(),
  display_name: z.string(),
  note: z.string().nullable(),
  added_at: z.string(),
});
export type SupportStaff = z.infer<typeof supportStaffSchema>;

const supportRequestSchema = z.object({
  grant_id: z.string(),
  code: z.string(),
  tenant_id: z.string(),
  tenant_name: z.string(),
  workspace_id: z.string(),
  workspace_name: z.string(),
  resource_type: z.string(),
  resource_label: z.string(),
  scope_set_key: z.string(),
  scope_set_label: z.string(),
  duration_hours: z.number(),
  reason: z.string(),
  requested_at: z.string(),
});
export type SupportRequest = z.infer<typeof supportRequestSchema>;

const assignedSupportGrantSchema = z.object({
  grant_id: z.string(),
  code: z.string(),
  tenant_id: z.string(),
  staff_user_id: z.string(),
  activated_at: z.string(),
  expires_at: z.string(),
});
export type AssignedSupportGrant = z.infer<typeof assignedSupportGrantSchema>;

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

// Slice PK: the document types (one list, `DocumentType` in `dw_supply_chain`)
// and step 12's views.
const _packagingMirrorsTheRoute: [
  SameType<DocumentType, SupplyChainGenerated["DocumentType"]>,
  SameType<PackagingDesign, SupplyChainGenerated["PackagingDesignView"]>,
  SameType<
    keyof PackagingDesign,
    keyof SupplyChainGenerated["PackagingDesignView"]
  >,
  SameType<
    keyof PackagingDesign["steps"][number],
    keyof SupplyChainGenerated["PackagingStepOptionView"]
  >,
  SameType<
    keyof PackagingDesign["history"][number],
    keyof SupplyChainGenerated["PackagingEventView"]
  >,
  SameType<PackagingState, SupplyChainGenerated["PackagingStateView"]>,
] = [true, true, true, true, true, true];
void _packagingMirrorsTheRoute;

// Ticket ai-automation/01: BM04 as fields and the PO case's commercial data.
const _commercialMirrorsTheRoute: [
  SameType<Incoterm, SupplyChainGenerated["Incoterm"]>,
  SameType<PaymentKind, SupplyChainGenerated["PaymentKind"]>,
  SameType<RedactableAmount, SupplyChainGenerated["RedactableAmount"]>,
  SameType<Bm04Schema, SupplyChainGenerated["SupplyChainBm04Schema"]>,
  SameType<keyof Bm04Field, keyof SupplyChainGenerated["Bm04Field"]>,
  SameType<ProductProfile, SupplyChainGenerated["ProductProfileView"]>,
  SameType<
    keyof ProductProfile,
    keyof SupplyChainGenerated["ProductProfileView"]
  >,
  SameType<POCommercial, SupplyChainGenerated["POCommercialView"]>,
  SameType<keyof POCommercial, keyof SupplyChainGenerated["POCommercialView"]>,
  SameType<keyof PricedLine, keyof SupplyChainGenerated["PricedLineView"]>,
  SameType<keyof POPayment, keyof SupplyChainGenerated["POPaymentView"]>,
] = [true, true, true, true, true, true, true, true, true, true, true];
void _commercialMirrorsTheRoute;

/** `POST /product-cases/{id}/profile`'s body. */
export type SaveProductProfileBody =
  SupplyChainOperations["save_product_profile_api_v1_supply_chain_product_cases__case_id__profile_post"]["requestBody"]["content"]["application/json"];
/** `PUT /po-cases/{id}/commercial`'s body. */
export type SetPOCommercialBody =
  SupplyChainOperations["set_po_commercial_api_v1_supply_chain_po_cases__case_id__commercial_put"]["requestBody"]["content"]["application/json"];
/** `POST /po-cases/{id}/payments`'s body. */
export type RecordPaymentBody =
  SupplyChainOperations["record_po_payment_api_v1_supply_chain_po_cases__case_id__payments_post"]["requestBody"]["content"]["application/json"];

// Ticket ai-automation/03: document drafts.
const _draftsMirrorTheRoute: [
  SameType<DocumentDraft, SupplyChainGenerated["DraftView"]>,
  SameType<keyof DocumentDraft, keyof SupplyChainGenerated["DraftView"]>,
  SameType<
    keyof DocumentDraft["fields"][number],
    keyof SupplyChainGenerated["DraftFieldView"]
  >,
  SameType<DraftStatus, SupplyChainGenerated["DraftStatus"]>,
  SameType<TemplateFieldKind, SupplyChainGenerated["TemplateFieldKind"]>,
] = [true, true, true, true, true];
void _draftsMirrorTheRoute;

// Ticket ai-automation/05: step proposals.
const _stepProposalMirrorsTheRoute: [
  SameType<StepProposal, SupplyChainGenerated["StepProposalView"]>,
  SameType<keyof StepProposal, keyof SupplyChainGenerated["StepProposalView"]>,
  SameType<
    keyof ProposalResultField,
    keyof SupplyChainGenerated["ResultFieldView"]
  >,
] = [true, true, true];
void _stepProposalMirrorsTheRoute;

// Ticket ai-automation/07: messages to a supplier.
const _supplierMessagesMirrorTheRoute: [
  SameType<SupplierMessage, SupplyChainGenerated["SupplierMessageView"]>,
  SameType<
    keyof SupplierMessage,
    keyof SupplyChainGenerated["SupplierMessageView"]
  >,
  SameType<SupplierMessagePurpose, SupplyChainGenerated["MessagePurpose"]>,
  SameType<SupplierMessageStatus, SupplyChainGenerated["MessageStatus"]>,
] = [true, true, true, true];
void _supplierMessagesMirrorTheRoute;

/** `POST /product-cases/{id}/step-proposal/decision`'s body. */
export type StepDecisionBody =
  SupplyChainOperations["decide_step_proposal_api_v1_supply_chain_product_cases__case_id__step_proposal_decision_post"]["requestBody"]["content"]["application/json"];

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

const _caseApprovalsMirrorsTheRoute: [
  SameType<CaseApprovals, SupplyChainGenerated["CaseApprovalsView"]>,
  SameType<
    keyof CaseApprovals["items"][number],
    keyof SupplyChainGenerated["CaseApprovalView"]
  >,
] = [true, true];
void _caseApprovalsMirrorsTheRoute;

// The settings screen's two policies: every field of each, as the routes have them.
const _policiesMirrorTheRoutes: [
  SameType<keyof SLAPolicy, keyof SupplyChainGenerated["SupplyChainSLAPolicy"]>,
  SameType<
    keyof SLAPolicy["default"][string],
    keyof SupplyChainGenerated["SLAMilestone"]
  >,
  SameType<
    SLAPolicy["default"][string]["status"],
    SupplyChainGenerated["SLAConfirmationStatus"]
  >,
  SameType<
    keyof SLAPolicy["supplier_update"],
    keyof SupplyChainGenerated["SupplierUpdateCadence"]
  >,
  SameType<PackagingPolicy, SupplyChainGenerated["SupplyChainPackagingPolicy"]>,
] = [true, true, true, true, true];
void _policiesMirrorTheRoutes;

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

const _meMirrorsTheRoute: [
  SameType<Me, Generated["MeResponse"]>,
  SameType<keyof Me, keyof Generated["MeResponse"]>,
] = [true, true];
void _meMirrorsTheRoute;

// The view route's answer, field for field: a reason added on the server and
// not here would fail the zod parse at runtime instead of this compile.
const _approvalViewMirrorsTheRoute: [
  SameType<ApprovalViewOutcome, Generated["ApprovalViewOutcome"]>,
] = [true];
void _approvalViewMirrorsTheRoute;

const _zaloMirrorsTheRoute: [
  SameType<ZaloStatus, Generated["ZaloStatusView"]>,
  SameType<keyof ZaloConnect, keyof Generated["ZaloConnectView"]>,
  SameType<ZaloWorkspace, Generated["ZaloWorkspaceView"]>,
] = [true, true, true];
void _zaloMirrorsTheRoute;

const _followUpMirrorsTheRoute: [
  SameType<FollowUp, SupplyChainGenerated["FollowUpItemView"]>,
  SameType<keyof FollowUp, keyof SupplyChainGenerated["FollowUpItemView"]>,
] = [true, true];
void _followUpMirrorsTheRoute;

const _caseDocumentMirrorsTheRoute: [
  SameType<CaseDocument, SupplyChainGenerated["CaseDocumentView"]>,
  SameType<keyof CaseDocument, keyof SupplyChainGenerated["CaseDocumentView"]>,
] = [true, true];
void _caseDocumentMirrorsTheRoute;

const _productCaseMirrorsTheRoute: [
  SameType<ProductCase, SupplyChainGenerated["ProductCaseView"]>,
  SameType<ProductCategory, SupplyChainGenerated["ProductCategoryView"]>,
  SameType<ProductCaseDetail, SupplyChainGenerated["ProductCaseDetailView"]>,
  SameType<ProductCaseStep, SupplyChainGenerated["ProductCaseStepView"]>,
  SameType<SampleRound, SupplyChainGenerated["SampleRoundView"]>,
  SameType<
    ProductActionOption,
    SupplyChainGenerated["ProductActionOptionView"]
  >,
  SameType<
    ProductCaseTransition,
    SupplyChainGenerated["ProductCaseTransitionView"]
  >,
  // The duty map is keyed by action on both sides; zod types it as a partial
  // record of the enum and OpenAPI as a string map, so only the keys compare.
  SameType<
    keyof ProductActionDuties,
    keyof SupplyChainGenerated["SupplyChainProductActionDuties"]
  >,
] = [true, true, true, true, true, true, true, true];
void _productCaseMirrorsTheRoute;

const _poCaseMirrorsTheRoute: [
  SameType<POCase, SupplyChainGenerated["POCaseView"]>,
  SameType<keyof POCase, keyof SupplyChainGenerated["POCaseView"]>,
  SameType<POCaseDetail, SupplyChainGenerated["POCaseDetailView"]>,
  SameType<keyof POCaseDetail, keyof SupplyChainGenerated["POCaseDetailView"]>,
  SameType<POCaseLine, SupplyChainGenerated["POCaseLineView"]>,
  SameType<OrderKind, SupplyChainGenerated["OrderKind"]>,
  SameType<OrderPlaced, SupplyChainGenerated["OrderPlacedView"]>,
  SameType<keyof OrderPlaced, keyof SupplyChainGenerated["OrderPlacedView"]>,
] = [true, true, true, true, true, true, true, true];
void _poCaseMirrorsTheRoute;
const _supportOperatorsMirrorTheRoutes: [
  SameType<SupportStaff, Generated["SupportStaffView"]>,
  SameType<keyof SupportStaff, keyof Generated["SupportStaffView"]>,
  SameType<SupportRequest, Generated["SupportRequestView"]>,
  SameType<keyof SupportRequest, keyof Generated["SupportRequestView"]>,
  SameType<AssignedSupportGrant, Generated["AssignedSupportGrantView"]>,
] = [true, true, true, true, true];
void _supportOperatorsMirrorTheRoutes;

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

  /** Confirm a waiver another admin proposed; until then it lifts nothing.
   * 409 if the caller proposed it, 404 if none is waiting. */
  confirmSeparationOfDutiesWaiver(
    ruleKey: string,
    reason: string,
  ): Promise<void> {
    return this.requestNoContent(
      "POST",
      `/api/v1/admin/separation-of-duties/${encodeURIComponent(ruleKey)}/waiver/confirm`,
      { body: { reason } },
    );
  }

  /** Close the tenant's waiver, or withdraw one still waiting for
   * confirmation. 409 while members still hold both sides. */
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

  listSupportStaff(): Promise<SupportStaff[]> {
    return this.request(
      "GET",
      "/api/v1/platform/support-staff",
      z.array(supportStaffSchema),
    );
  }

  addSupportStaff(email: string, note?: string): Promise<PlatformUserRef> {
    return this.request(
      "POST",
      "/api/v1/platform/support-staff",
      userRefSchema,
      {
        body: { email, note: note ?? null },
      },
    );
  }

  removeSupportStaff(userId: string): Promise<void> {
    return this.requestNoContent(
      "DELETE",
      `/api/v1/platform/support-staff/${userId}`,
    );
  }

  listSupportRequests(): Promise<SupportRequest[]> {
    return this.request(
      "GET",
      "/api/v1/platform/support-requests",
      z.array(supportRequestSchema),
    );
  }

  assignSupportRequest(
    grantId: string,
    staffUserId: string,
  ): Promise<AssignedSupportGrant> {
    return this.request(
      "POST",
      `/api/v1/platform/support-requests/${grantId}/assign`,
      assignedSupportGrantSchema,
      { body: { staff_user_id: staffUserId } },
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

  getApproval(approvalId: string): Promise<Approval> {
    return this.request(
      "GET",
      `/api/v1/approvals/${approvalId}`,
      approvalSchema,
    );
  }

  /**
   * Record that the viewer opened this approval and, with `issue_code`, get a
   * single-use code to decide it on Zalo (ADR 0014). The comment is the one
   * the decision will carry; a strict type requires it.
   */
  viewApproval(
    approvalId: string,
    body: { comment?: string; issue_code?: boolean } = {},
  ): Promise<ApprovalViewOutcome> {
    return this.request(
      "POST",
      `/api/v1/approvals/${approvalId}/view`,
      approvalViewOutcomeSchema,
      { body },
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

  /** The caller's verified context in the active workspace (roles, scopes). */
  getMe(): Promise<Me> {
    return this.request("GET", "/api/v1/me", meSchema);
  }

  // ---- the caller's own Zalo link --------------------------------------
  // 404 `not_found` from all three = the deployment has no bot configured.

  getZaloStatus(): Promise<ZaloStatus> {
    return this.request("GET", "/api/v1/zalo/status", zaloStatusSchema);
  }

  /** Mint a one-time `/start <code>` for the signed-in user to send to the bot. */
  connectZalo(): Promise<ZaloConnect> {
    return this.request("POST", "/api/v1/zalo/connect", zaloConnectSchema);
  }

  disconnectZalo(): Promise<void> {
    return this.requestNoContent("POST", "/api/v1/zalo/disconnect");
  }

  getZaloWorkspace(): Promise<ZaloWorkspace> {
    return this.request("GET", "/api/v1/zalo/workspace", zaloWorkspaceSchema);
  }

  /** 404 `not_found` when the pair is not one of the caller's own memberships. */
  setZaloWorkspace(
    tenantId: string,
    workspaceId: string,
  ): Promise<ZaloWorkspace> {
    return this.request("PUT", "/api/v1/zalo/workspace", zaloWorkspaceSchema, {
      body: { tenant_id: tenantId, workspace_id: workspaceId },
    });
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

  getPOCase(caseId: string): Promise<POCaseDetail> {
    return this.request(
      "GET",
      `/api/v1/supply-chain/po-cases/${caseId}`,
      poCaseDetailSchema,
    );
  }

  /** Step 10 on a case awaiting its PO: its reference, its kind and the
   * quantity of any line still open or to correct. A reference taken in the
   * tenant is a 409 naming its constraint. The key is minted once per press. */
  createPO(
    caseId: string,
    input: CreatePOInput,
    idempotencyKey: string,
  ): Promise<POCaseDetail> {
    const body: CreatePOBody = {
      po_reference: input.poReference.trim(),
      order_kind: input.orderKind,
      lines: (input.lines ?? []).map((line) => ({
        sku_id: line.skuId,
        quantity: line.quantity,
      })),
    };
    return this.request(
      "POST",
      `/api/v1/supply-chain/po-cases/${encodeURIComponent(caseId)}/create-po`,
      poCaseDetailSchema,
      { body, idempotencyKey },
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

  /** One page of the case's timeline, newest first. */
  listCaseTransitions(
    caseId: string,
    params: PageParams = {},
  ): Promise<Page<CaseTransition>> {
    return this.request(
      "GET",
      `/api/v1/supply-chain/po-cases/${caseId}/transitions${pageQueryString(params)}`,
      pageSchema(caseTransitionSchema),
    );
  }

  /** The tenant's effective SLA policy: its own override, else the platform's. */
  getSLAPolicy(): Promise<SLAPolicy> {
    return this.request(
      "GET",
      "/api/v1/supply-chain/sla-policy",
      slaPolicySchema,
    );
  }

  /** Replace the tenant's SLA policy, whole (needs `sla_policy.write`). */
  setSLAPolicy(policy: SLAPolicy): Promise<SLAPolicy> {
    return this.request(
      "PUT",
      "/api/v1/supply-chain/sla-policy",
      slaPolicySchema,
      {
        body: policy,
      },
    );
  }

  /** Step 13's rule for the tenant: its own override, else the platform's. */
  getPackagingPolicy(): Promise<PackagingPolicy> {
    return this.request(
      "GET",
      "/api/v1/supply-chain/packaging-policy",
      packagingPolicySchema,
    );
  }

  /** Replace the tenant's step-13 rule, whole (needs `action_duties.write`). */
  setPackagingPolicy(policy: PackagingPolicy): Promise<PackagingPolicy> {
    return this.request(
      "PUT",
      "/api/v1/supply-chain/packaging-policy",
      packagingPolicySchema,
      { body: policy },
    );
  }

  /** A product case's BM04: the latest version (prices redacted without
   * `commercial.read`) and the tenant's schema. */
  getProductProfile(caseId: string): Promise<ProductProfile> {
    return this.request(
      "GET",
      `/api/v1/supply-chain/product-cases/${encodeURIComponent(caseId)}/profile`,
      productProfileSchema,
    );
  }

  /** Saves a new BM04 version; `prices` absent keeps the last price. */
  saveProductProfile(
    caseId: string,
    body: SaveProductProfileBody,
    idempotencyKey: string,
  ): Promise<ProductProfile> {
    return this.request(
      "POST",
      `/api/v1/supply-chain/product-cases/${encodeURIComponent(caseId)}/profile`,
      productProfileSchema,
      { body, idempotencyKey },
    );
  }

  /** The tenant's BM04 schema: its own override, else the platform's. */
  getBm04Schema(): Promise<Bm04Schema> {
    return this.request(
      "GET",
      "/api/v1/supply-chain/bm04-schema",
      bm04SchemaSchema,
    );
  }

  /** A PO case's terms, priced lines, computed total and payments. */
  getPOCommercial(caseId: string): Promise<POCommercial> {
    return this.request(
      "GET",
      `/api/v1/supply-chain/po-cases/${encodeURIComponent(caseId)}/commercial`,
      poCommercialSchema,
    );
  }

  /** Replaces a PO case's terms and line prices (needs `commercial.write`). */
  setPOCommercial(
    caseId: string,
    body: SetPOCommercialBody,
    idempotencyKey: string,
  ): Promise<POCommercial> {
    return this.request(
      "PUT",
      `/api/v1/supply-chain/po-cases/${encodeURIComponent(caseId)}/commercial`,
      poCommercialSchema,
      { body, idempotencyKey },
    );
  }

  /** Records a new version of the case's deposit or final payment. */
  recordPOPayment(
    caseId: string,
    body: RecordPaymentBody,
    idempotencyKey: string,
  ): Promise<POPayment> {
    return this.request(
      "POST",
      `/api/v1/supply-chain/po-cases/${encodeURIComponent(caseId)}/payments`,
      poPaymentSchema,
      { body, idempotencyKey },
    );
  }

  /** The pending approvals naming one PO case, filtered by the server. */
  listPOCaseApprovals(caseId: string): Promise<CaseApprovals> {
    return this.request(
      "GET",
      `/api/v1/supply-chain/po-cases/${caseId}/approvals`,
      caseApprovalsSchema,
    );
  }

  /** Step 12's sub-flow on a PO case: its state, each step with whether the
   * caller may take it, the tenant's step-13 rule and the history. */
  getPackagingDesign(caseId: string): Promise<PackagingDesign> {
    return this.request(
      "GET",
      `/api/v1/supply-chain/po-cases/${encodeURIComponent(caseId)}/packaging-design`,
      packagingDesignSchema,
    );
  }

  /** Takes one step of step 12's sub-flow; a test step names its report. */
  takePackagingStep(
    caseId: string,
    input: { action: PackagingAction; reason?: string; documentId?: string },
    idempotencyKey: string,
  ): Promise<PackagingState> {
    const body: TakePackagingStepBody = {
      action: input.action,
      reason: input.reason?.trim() || null,
      document_id: input.documentId ?? null,
    };
    return this.request(
      "POST",
      `/api/v1/supply-chain/po-cases/${encodeURIComponent(caseId)}/packaging-design/steps`,
      packagingStateSchema,
      { body, idempotencyKey },
    );
  }

  /** A PO case's documents, by type and newest version first. */
  listCaseDocuments(caseId: string): Promise<CaseDocument[]> {
    return this.request(
      "GET",
      `/api/v1/supply-chain/po-cases/${encodeURIComponent(caseId)}/documents`,
      z.array(caseDocumentSchema),
    );
  }

  /**
   * Attach a file to a PO case as the next version of `docType`. Multipart,
   * so no Content-Type header: the browser sets the boundary. The
   * `idempotencyKey` is minted once per press and reused on a retry of the
   * same file, so a retry never adds a second version.
   */
  uploadCaseDocument(
    caseId: string,
    input: { docType: DocumentType; file: File; idempotencyKey: string },
  ): Promise<CaseDocument> {
    return this.uploadDocument(
      `/api/v1/supply-chain/po-cases/${encodeURIComponent(caseId)}/documents`,
      input,
    );
  }

  private async uploadDocument(
    path: string,
    input: { docType: DocumentType; file: File; idempotencyKey: string },
  ): Promise<CaseDocument> {
    const fetchImpl = this.options.fetchImpl ?? fetch;
    const form = new FormData();
    form.append("doc_type", input.docType);
    form.append("file", input.file, input.file.name);
    const headers: Record<string, string> = {
      Accept: "application/json",
      "Idempotency-Key": input.idempotencyKey,
    };
    const token = await this.options.getAccessToken?.();
    if (token) headers.Authorization = `Bearer ${token}`;
    const response = await fetchImpl(`${this.options.baseUrl}${path}`, {
      method: "POST",
      headers,
      body: form,
    });
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
    return caseDocumentSchema.parse(json);
  }

  /** A product-development case's documents, by type and newest version first. */
  listProductCaseDocuments(caseId: string): Promise<CaseDocument[]> {
    return this.request(
      "GET",
      `/api/v1/supply-chain/product-cases/${encodeURIComponent(caseId)}/documents`,
      z.array(caseDocumentSchema),
    );
  }

  /** Attach a file to a product-development case; as `uploadCaseDocument`. */
  uploadProductCaseDocument(
    caseId: string,
    input: { docType: DocumentType; file: File; idempotencyKey: string },
  ): Promise<CaseDocument> {
    return this.uploadDocument(
      `/api/v1/supply-chain/product-cases/${encodeURIComponent(caseId)}/documents`,
      input,
    );
  }

  /** The latest version of each draft of a case (`po` or `product`). */
  listCaseDrafts(
    caseKind: "po" | "product",
    caseId: string,
  ): Promise<DocumentDraft[]> {
    const segment = caseKind === "po" ? "po-cases" : "product-cases";
    return this.request(
      "GET",
      `/api/v1/supply-chain/${segment}/${encodeURIComponent(caseId)}/drafts`,
      z.array(documentDraftSchema),
    );
  }

  /** A person's edit of a draft: the named fields replaced, a new version. */
  reviseDraft(
    draftId: string,
    values: Record<string, unknown>,
    idempotencyKey: string,
  ): Promise<DocumentDraft> {
    return this.request(
      "POST",
      `/api/v1/supply-chain/drafts/${encodeURIComponent(draftId)}/revisions`,
      documentDraftSchema,
      { body: { values }, idempotencyKey },
    );
  }

  /** Rejects a draft version, with its reason. */
  rejectDraft(
    draftId: string,
    reason: string,
    idempotencyKey: string,
  ): Promise<DocumentDraft> {
    return this.request(
      "POST",
      `/api/v1/supply-chain/drafts/${encodeURIComponent(draftId)}/rejection`,
      documentDraftSchema,
      { body: { reason }, idempotencyKey },
    );
  }

  /** What AI prepared for the case's current step, and its pending
   * proposal (ticket ai-automation/05). */
  getStepProposal(caseId: string): Promise<StepProposal> {
    return this.request(
      "GET",
      `/api/v1/supply-chain/product-cases/${encodeURIComponent(caseId)}/step-proposal`,
      stepProposalSchema,
    );
  }

  /** Approve or not the case's pending proposal; a physical step's result is
   * typed here. The platform decides and the case moves when approved. */
  decideStepProposal(
    caseId: string,
    body: StepDecisionBody,
    idempotencyKey: string,
  ): Promise<StepProposal> {
    return this.request(
      "POST",
      `/api/v1/supply-chain/product-cases/${encodeURIComponent(caseId)}/step-proposal/decision`,
      stepProposalSchema,
      { body, idempotencyKey },
    );
  }

  /** The case's messages to its supplier, AI-drafted, newest first. */
  listSupplierMessages(
    caseKind: "po" | "product",
    caseId: string,
  ): Promise<SupplierMessage[]> {
    const segment = caseKind === "po" ? "po-cases" : "product-cases";
    return this.request(
      "GET",
      `/api/v1/supply-chain/${segment}/${encodeURIComponent(caseId)}/supplier-messages`,
      z.array(supplierMessageSchema),
    );
  }

  /** "Đã gửi": the person sent this text (its hash) from their own mailbox. */
  markSupplierMessageSent(
    messageId: string,
    contentSha256: string,
    idempotencyKey: string,
  ): Promise<SupplierMessage> {
    return this.request(
      "POST",
      `/api/v1/supply-chain/supplier-messages/${encodeURIComponent(messageId)}/sent`,
      supplierMessageSchema,
      { body: { content_sha256: contentSha256 }, idempotencyKey },
    );
  }

  /** The draft as its template prints it (DOCX), prices hidden per scope. */
  async downloadDraft(draftId: string): Promise<Blob> {
    const response = await this.rawRequest(
      "GET",
      `/api/v1/supply-chain/drafts/${encodeURIComponent(draftId)}/file`,
    );
    return response.blob();
  }

  /** One document's bytes, read with the session's token (read scope). */
  async downloadCaseDocument(documentId: string): Promise<Blob> {
    const response = await this.rawRequest(
      "GET",
      `/api/v1/supply-chain/documents/${encodeURIComponent(documentId)}/content`,
    );
    return response.blob();
  }

  // ---- product-development cases (stage 1) ---------------------------------

  /** The workspace's product cases, newest first, narrowed by state or PIC. */
  listProductCases(
    params: PageParams & ProductCaseListFilter = {},
  ): Promise<Page<ProductCase>> {
    const query: ListProductCasesQuery = {
      limit: params.limit,
      cursor: params.cursor || undefined,
      state: params.state,
      pic_user_id: params.picUserId,
      category: params.category,
    };
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null) search.set(key, String(value));
    }
    const rendered = search.toString();
    return this.request(
      "GET",
      `/api/v1/supply-chain/product-cases${rendered ? `?${rendered}` : ""}`,
      pageSchema(productCaseSchema),
    );
  }

  getProductCase(caseId: string): Promise<ProductCaseDetail> {
    return this.request(
      "GET",
      `/api/v1/supply-chain/product-cases/${encodeURIComponent(caseId)}`,
      productCaseDetailSchema,
    );
  }

  /** Step 1. The caller becomes the PIC; there is no field to name another. */
  proposeProductCase(
    input: { proposalCode: string; productName: string; category: string },
    idempotencyKey: string,
  ): Promise<ProductCase> {
    const body: ProposeProductCaseBody = {
      proposal_code: input.proposalCode,
      product_name: input.productName,
      category: input.category,
    };
    return this.request(
      "POST",
      "/api/v1/supply-chain/product-cases",
      productCaseSchema,
      { body, idempotencyKey },
    );
  }

  /** One step on a product case. The key is minted once per press. */
  takeProductCaseStep(
    caseId: string,
    input: ProductCaseStepInput,
    idempotencyKey: string,
  ): Promise<ProductCaseStep> {
    const body: ProductCaseStepBody = {
      action: input.action,
      reason: input.reason?.trim() ? input.reason.trim() : null,
      supplier_name: input.supplierName?.trim()
        ? input.supplierName.trim()
        : null,
      document_id: input.documentId ?? null,
      item_code: input.itemCode?.trim() ? input.itemCode.trim() : null,
      sku: input.sku
        ? {
            sku_code: input.sku.skuCode.trim(),
            variant_label: input.sku.variantLabel.trim(),
            planned_quantity: input.sku.plannedQuantity ?? null,
          }
        : null,
      sku_id: input.skuId ?? null,
    };
    return this.request(
      "POST",
      `/api/v1/supply-chain/product-cases/${encodeURIComponent(caseId)}/transitions`,
      productCaseStepSchema,
      { body, idempotencyKey },
    );
  }

  /** ĐẶT HÀNG: the case is ordered and a PO case opens awaiting its PO. A
   * second press is a 409; the same key replayed returns the first answer. */
  placeProductOrder(
    caseId: string,
    idempotencyKey: string,
  ): Promise<OrderPlaced> {
    return this.request(
      "POST",
      `/api/v1/supply-chain/product-cases/${encodeURIComponent(caseId)}/order`,
      orderPlacedSchema,
      { idempotencyKey },
    );
  }

  /** One page of the case's history, newest first. */
  listProductCaseTransitions(
    caseId: string,
    params: PageParams = {},
  ): Promise<Page<ProductCaseTransition>> {
    return this.request(
      "GET",
      `/api/v1/supply-chain/product-cases/${encodeURIComponent(caseId)}/transitions${pageQueryString(params)}`,
      pageSchema(productCaseTransitionSchema),
    );
  }

  /** Which duty each product step needs, for the caller's tenant. */
  getProductActionDuties(): Promise<ProductActionDuties> {
    return this.request(
      "GET",
      "/api/v1/supply-chain/product-action-duties",
      productActionDutiesSchema,
    );
  }

  /** The tenant's Category list, in its own order (ticket 06): what the
   * propose form offers, and the labels of the keys a case is stamped with. */
  listProductCategories(): Promise<ProductCategory[]> {
    return this.request(
      "GET",
      "/api/v1/supply-chain/product-categories",
      z.array(productCategorySchema),
    );
  }

  /** The workspace's open follow-ups, newest first; the caller's own marked. */
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
  DocumentDraft,
  StepProposal,
  SupplierMessage,
  SupplierMessagePurpose,
  SupplierMessageStatus,
  ProposalResultField,
  DraftField,
  DraftStatus,
  TemplateFieldKind,
  Bm04Field,
  Bm04Schema,
  Incoterm,
  PaymentKind,
  PricedLine,
  ProductProfile,
  POCommercial,
  POPayment,
  RedactableAmount,
  Approval,
  ApprovalViewOutcome,
  AuditEvent,
  Page,
  PageParams,
  Run,
  TimelineEvent,
  WorkspaceMember,
  POCase,
  POCaseDetail,
  POCaseLine,
  CreatePOInput,
  OrderKind,
  SupplierUpdate,
  DelayImpactAnalysis,
  SLAEvaluation,
  MissingUpdateStatus,
  CaseTransition,
  CaseApprovals,
  SLAPolicy,
  PackagingPolicy,
  AttentionItem,
  PortfolioSummary,
  POCaseListFilter,
  AIWorkResponse,
  CaseDocument,
  DocumentType,
  PackagingAction,
  PackagingDesign,
  PackagingState,
  PackagingStepOption,
  PreProductionTest,
  ReviewStatus,
  ProductCase,
  ProductCaseDetail,
  ProductCaseStep,
  OrderPlaced,
  PendingReview,
  ProductCaseTransition,
  ProductActionDuties,
  ProductCaseListFilter,
  ProductCaseStepInput,
  SampleRound,
  ProductActionOption,
};

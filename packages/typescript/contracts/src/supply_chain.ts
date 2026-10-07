import { z } from "zod";

/** Mirrors dw_supply_chain's presentation views. */

export const caseStateSchema = z.enum([
  // Opened by ĐẶT HÀNG on a product case (ADR 0017); its PO does not exist
  // yet, so `po_reference` is null until step 10 (`create_po`).
  "order_requested",
  "po_created",
  "waiting_deposit",
  "deposit_confirmed",
  "pre_production",
  "production",
  "qc",
  "in_transit",
  "arrived_port",
  "waiting_payment",
  "payment_completed",
  "warehouse_receiving",
  "completed",
  "waiting_external",
  "blocked",
  "rework",
  "manual_review",
  "cancelled",
]);
export type CaseState = z.infer<typeof caseStateSchema>;

/** Step 10's classification: Hàng mới (out of stage 1) or Hàng đặt lại. */
export const orderKindSchema = z.enum(["new", "reorder"]);
export type OrderKind = z.infer<typeof orderKindSchema>;

/** Mirrors `POCaseView`. `po_reference` is null while the case is
 * `order_requested`; the stage-1 fields are null for a case opened without
 * stage 1. */
export const poCaseSchema = z.object({
  id: z.string().uuid(),
  po_reference: z.string().nullable(),
  supplier_name: z.string(),
  state: caseStateSchema,
  interrupted_state: caseStateSchema.nullable(),
  created_at: z.string().nullable(),
  version: z.number(),
  order_kind: orderKindSchema,
  product_dev_case_id: z.string().nullable(),
  pic_user_id: z.string().nullable(),
  category: z.string().nullable(),
});
export type POCase = z.infer<typeof poCaseSchema>;

/** Mirrors `POCaseLineView`: one SKU of the product and how many (null until
 * step 10 sets it). */
export const poCaseLineSchema = z.object({
  sku_id: z.string().uuid(),
  sku_code: z.string().nullable(),
  variant_label: z.string().nullable(),
  quantity: z.number().int().nullable(),
});
export type POCaseLine = z.infer<typeof poCaseLineSchema>;

/** Mirrors `POCaseDetailView`: the case and its planned lines. */
export const poCaseDetailSchema = poCaseSchema.extend({
  lines: z.array(poCaseLineSchema),
});
export type POCaseDetail = z.infer<typeof poCaseDetailSchema>;

/** The body of `POST /po-cases/{id}/create-po` (step 10). */
export interface CreatePOInput {
  poReference: string;
  orderKind: OrderKind;
  /** Quantities to set or correct, one per SKU. */
  lines?: { skuId: string; quantity: number }[];
}

/** Narrowing for `GET /po-cases`, mirroring the server's `POCaseListFilter`:
 * every field an exact match, and "active" is the server's own definition
 * of which states are terminal — deliberately not a list kept here. */
export interface POCaseListFilter {
  state?: CaseState;
  supplierName?: string;
  activeOnly?: boolean;
}

export const supplierEventTypeSchema = z.enum([
  "production_delay",
  "qc_issue",
  "shipment_update",
  "deposit_confirmation",
  "document_submitted",
  "no_official_update",
  "other",
]);
export type SupplierEventType = z.infer<typeof supplierEventTypeSchema>;

export const supplierUpdateSchema = z.object({
  id: z.string().uuid(),
  po_case_id: z.string().uuid(),
  raw_text: z.string(),
  event_type: supplierEventTypeSchema,
  affected_po: z.string().nullable(),
  delay_days: z.number().nullable(),
  reason: z.string(),
  proposed_action: z.string(),
  confidence: z.number(),
  source_ref: z.string(),
  requires_confirmation: z.boolean(),
  created_at: z.string().nullable(),
});
export type SupplierUpdate = z.infer<typeof supplierUpdateSchema>;

export const impactedMilestoneSchema = z.object({
  milestone: caseStateSchema,
  estimated_delay_days: z.number(),
});

export const mitigationOptionSchema = z.object({
  description: z.string(),
  tradeoff: z.string(),
});

export const delayImpactAnalysisSchema = z.object({
  id: z.string().uuid(),
  po_case_id: z.string().uuid(),
  supplier_update_id: z.string().uuid(),
  delay_days: z.number(),
  impacted_milestones: z.array(impactedMilestoneSchema),
  assumptions: z.array(z.string()),
  mitigation_options: z.array(mitigationOptionSchema),
  created_at: z.string().nullable(),
});
export type DelayImpactAnalysis = z.infer<typeof delayImpactAnalysisSchema>;

export const slaEvaluationSchema = z.object({
  status: z.enum(["not_applicable", "not_evaluable", "on_track", "breached"]),
  milestone: z.string().nullable(),
  entered_current_state_at: z.string(),
  age_days: z.number(),
  threshold_days: z.number().nullable(),
});
export type SLAEvaluation = z.infer<typeof slaEvaluationSchema>;

export const missingUpdateStatusSchema = z.object({
  status: z.enum(["on_track", "reminder_due", "escalation_due"]),
  reference_at: z.string(),
  age_days: z.number(),
});
export type MissingUpdateStatus = z.infer<typeof missingUpdateStatusSchema>;

/** One row of the case's own timeline, oldest first. */
export const caseTransitionSchema = z.object({
  from_state: caseStateSchema,
  to_state: caseStateSchema,
  reason: z.string().nullable(),
  occurred_at: z.string(),
});
export type CaseTransition = z.infer<typeof caseTransitionSchema>;

/** One case the Attention Queue flags. `sla`/`missing_update` are set only
 * when that signal is the reason the case is here — never a clean bill of
 * health repeated for every case. */
export const attentionItemSchema = z.object({
  case: poCaseSchema,
  sla: slaEvaluationSchema.nullable(),
  missing_update: missingUpdateStatusSchema.nullable(),
});
export type AttentionItem = z.infer<typeof attentionItemSchema>;

/** Control Tower: active cases sitting in one state. */
export const stateSummarySchema = z.object({
  state: caseStateSchema,
  case_count: z.number(),
  sla_breached_count: z.number(),
  update_overdue_count: z.number(),
  oldest_in_state_days: z.number(),
});
export type StateSummary = z.infer<typeof stateSummarySchema>;

/** Control Tower: active cases for one supplier, grouped by the name exactly
 * as stored on the case. */
export const supplierSummarySchema = z.object({
  supplier_name: z.string(),
  case_count: z.number(),
  update_overdue_count: z.number(),
  escalation_due_count: z.number(),
  sla_breached_count: z.number(),
  longest_silence_days: z.number(),
});
export type SupplierSummary = z.infer<typeof supplierSummarySchema>;

/** Counts over the same signals the Attention Queue flags on — never a
 * score. Row order is the server's own (states in lifecycle order,
 * suppliers with the most overdue updates first); render it as given. */
export const portfolioSummarySchema = z.object({
  active_case_count: z.number(),
  sla_breached_count: z.number(),
  update_overdue_count: z.number(),
  by_state: z.array(stateSummarySchema),
  by_supplier: z.array(supplierSummarySchema),
});
export type PortfolioSummary = z.infer<typeof portfolioSummarySchema>;

// -- Command bar: the structured AI answer -------------------------------------

export const caseQueryKindSchema = z.enum([
  "list_cases",
  "open_case",
  "unsupported",
]);

export const caseQueryOutcomeSchema = z.enum([
  "list",
  "open",
  "not_understood",
  "supplier_not_found",
  "supplier_ambiguous",
  "po_reference_missing",
  "po_not_found",
  "po_ambiguous",
]);
export type CaseQueryOutcome = z.infer<typeof caseQueryOutcomeSchema>;

export const groundedFieldSchema = z.enum([
  "supplier",
  "po_reference",
  "state",
  "active_only",
]);
export type GroundedField = z.infer<typeof groundedFieldSchema>;

export const caseTableDataViewSchema = z.object({
  type: z.literal("case_table"),
  rows: z.array(poCaseSchema),
  has_more: z.boolean(),
});

export const caseLinkDataViewSchema = z.object({
  type: z.literal("case_link"),
  case: poCaseSchema,
});

/** The closed set of views an answer may carry. An unknown `type` fails the
 * whole response — it is never rendered as raw data or guessed at. */
export const dataViewSchema = z.discriminatedUnion("type", [
  caseTableDataViewSchema,
  caseLinkDataViewSchema,
]);
export type DataView = z.infer<typeof dataViewSchema>;

/** One answer from `POST /case-query`: structured data only — no markup, no
 * link and no action chosen by a model. Every value in `understood` was
 * resolved by code (a supplier is always a stored name). */
export const aiWorkResponseSchema = z.object({
  intent: caseQueryKindSchema,
  outcome: caseQueryOutcomeSchema,
  understood: z.object({
    state: caseStateSchema.nullable(),
    supplier_name: z.string().nullable(),
    active_only: z.boolean(),
    po_reference: z.string().nullable(),
  }),
  citations: z.array(
    z.object({ field: groundedFieldSchema, quote: z.string() }),
  ),
  /** Fields the model claimed that the question gave no grounds for. */
  ignored_fields: z.array(groundedFieldSchema),
  /** Fields the question did state that this kind of answer cannot apply —
   * the other reason a reading is refused, reported apart. */
  unusable_fields: z.array(groundedFieldSchema),
  candidates: z.array(z.string()),
  data_view: dataViewSchema.nullable(),
});
export type AIWorkResponse = z.infer<typeof aiWorkResponseSchema>;

// -- Daily management brief ------------------------------------------------------

export const briefSignalSchema = z.enum([
  "update_escalation_due",
  "sla_breached",
  "case_blocked",
  "approval_pending",
  "manual_review",
  "supplier_reported_delay",
  "update_reminder_due",
  "waiting_external",
  "rework",
  "waiting_on_us",
  "changed_recently",
]);
export type BriefSignal = z.infer<typeof briefSignalSchema>;

/** One case in one brief group, with the figure that put it there. */
export const briefEntrySchema = z.object({
  case: poCaseSchema,
  days: z.number().nullable(),
  limit_days: z.number().nullable(),
  transition: caseTransitionSchema.nullable(),
  approval_action: z.string().nullable(),
});
export type BriefEntry = z.infer<typeof briefEntrySchema>;

/** Every case one deterministic signal holds for. `total` counts them all;
 * `entries` carries at most ten, longest-standing first. `state` is set when
 * a state defines the group — the list it drills down to. */
export const briefGroupSchema = z.object({
  key: z.string(),
  signal: briefSignalSchema,
  qualifier: z.string().nullable(),
  state: caseStateSchema.nullable(),
  total: z.number(),
  entries: z.array(briefEntrySchema),
});
export type BriefGroup = z.infer<typeof briefGroupSchema>;

/** `GET /daily-brief`. Groups arrive in the tenant's own order; render them
 * as given. `approvals_visible` false means approvals were not looked at,
 * never "none pending". */
export const dailyBriefSchema = z.object({
  generated_at: z.string(),
  active_case_count: z.number(),
  flagged_case_count: z.number(),
  approvals_visible: z.boolean(),
  groups: z.array(briefGroupSchema),
});
export type DailyBrief = z.infer<typeof dailyBriefSchema>;

export const briefSummaryStatusSchema = z.enum([
  "written",
  "nothing_kept",
  "nothing_written",
  "nothing_to_summarize",
  "unavailable",
]);
export type BriefSummaryStatus = z.infer<typeof briefSummaryStatusSchema>;

/** A model's summary of a brief, after code checked every sentence against
 * the groups it cites. `dropped` counts the sentences that failed. */
export const briefSummarySchema = z.object({
  status: briefSummaryStatusSchema,
  sentences: z.array(
    z.object({ text: z.string(), group_keys: z.array(z.string()) }),
  ),
  dropped: z.number(),
});
export type BriefSummary = z.infer<typeof briefSummarySchema>;

/** `POST /daily-brief/summary`: the summary together with the brief it was
 * checked against — render this brief, so every cited key resolves. */
export const dailyBriefSummarySchema = z.object({
  brief: dailyBriefSchema,
  summary: briefSummarySchema,
});
export type DailyBriefSummary = z.infer<typeof dailyBriefSummarySchema>;

/** An open follow-up: the work a reminder, an escalation or an SLA breach
 * handed to a person. `mine`: the caller holds a scope it was handed to, so
 * the caller is expected to act and may close it. Mirrors
 * `FollowUpItemView`. */
export const followUpKindSchema = z.enum([
  "update_reminder",
  "update_escalation",
  "sla_breach",
]);
export type FollowUpKind = z.infer<typeof followUpKindSchema>;

export const followUpSchema = z.object({
  id: z.string(),
  po_case_id: z.string(),
  po_reference: z.string().nullable(),
  supplier_name: z.string(),
  kind: followUpKindSchema,
  milestone: z.string().nullable(),
  days: z.number().int(),
  limit_days: z.number().int().nullable(),
  opened_at: z.string(),
  notified_at: z.string().nullable(),
  mine: z.boolean(),
});
export type FollowUp = z.infer<typeof followUpSchema>;

// ---- case documents (ADR 0021) --------------------------------------------

/** ADR 0021's fourteen document types; the API's `DocumentType`. */
export const documentTypeSchema = z.enum([
  "proposal_list",
  "product_image",
  "sample_photo",
  "sample_evaluation",
  "sample_revision_request",
  "product_profile_bm04",
  "official_item_code",
  "supplier_confirmation_email",
  "purchase_order",
  "deposit_docs",
  "payment_docs",
  "packaging_content",
  "user_manual",
  "maquette",
]);
export type DocumentType = z.infer<typeof documentTypeSchema>;

/** Which kind of case a document belongs to; the API's `CaseKind`. */
export const caseKindSchema = z.enum(["po", "product"]);
export type CaseKind = z.infer<typeof caseKindSchema>;

/** Mirrors `CaseDocumentView`. The object key never leaves the server. */
export const caseDocumentSchema = z.object({
  id: z.string(),
  case_kind: caseKindSchema,
  case_id: z.string(),
  doc_type: documentTypeSchema,
  filename: z.string(),
  content_type: z.string(),
  size_bytes: z.number().int(),
  sha256: z.string(),
  version: z.number().int(),
  uploaded_by: z.string(),
  uploaded_at: z.string(),
});
export type CaseDocument = z.infer<typeof caseDocumentSchema>;

// ---- product-development cases (stage 1, ADR 0016) ---------------------------

/** The API's `ProductDevState`: the states stage-1 tickets 01-04 reach. */
export const productDevStateSchema = z.enum([
  "proposed",
  "sample_requested",
  "sample_testing",
  "revision_requested",
  "pending_bod_review",
  "profile_in_progress",
  "supplier_confirmation",
  "item_coding",
  // Step 9: submitted for sign-off; every sign-off step approved.
  "pending_signoff",
  "ready_to_order",
  // ĐẶT HÀNG: terminal; the PO case it opened carries the order on.
  "ordered",
  "waiting_external",
  "blocked",
  "manual_review",
  "cancelled",
]);
export type ProductDevState = z.infer<typeof productDevStateSchema>;

/** The API's `ProductAction`. Its own set, never `CaseAction`'s. */
export const productActionSchema = z.enum([
  "propose",
  "request_sample",
  "receive_sample",
  "pass_sample",
  "request_revision",
  "receive_revised_sample",
  "reject_sample",
  // Steps 7-8: R&D completes the BM04; TP Cung ứng confirms with the supplier.
  "complete_profile",
  "confirm_with_supplier",
  // Step 9: Cung ứng codes the product, then submits it for sign-off.
  "issue_item_code",
  "add_sku",
  "remove_sku",
  "submit_for_signoff",
  // ĐẶT HÀNG, taken at `/product-cases/{id}/order`, never as a generic step.
  "place_order",
  "wait_for_external",
  "flag_blocked",
  "flag_manual_review",
  "resume",
  "cancel",
  // Step 6: BGĐ's decision, applied by the review graph after `/approvals`.
  // In a case's history, never among the steps a page offers.
  "bod_approve",
  "bod_reject",
  // Step 9's sign-off outcome, applied by the sign-off graph; never a step a
  // page offers.
  "signoff_approve",
  "signoff_reject",
]);
export type ProductAction = z.infer<typeof productActionSchema>;

export const sampleResultSchema = z.enum([
  "passed",
  "needs_revision",
  "rejected",
]);
export type SampleResult = z.infer<typeof sampleResultSchema>;

/** Mirrors `ProductCaseView`. */
export const productCaseSchema = z.object({
  id: z.string(),
  proposal_code: z.string(),
  product_name: z.string(),
  category: z.string(),
  supplier_name: z.string().nullable(),
  pic_user_id: z.string(),
  state: productDevStateSchema,
  interrupted_state: productDevStateSchema.nullable(),
  sample_round: z.number().int(),
  signoff_round: z.number().int(),
  created_by: z.string(),
  created_at: z.string().nullable(),
  version: z.number().int(),
});
export type ProductCase = z.infer<typeof productCaseSchema>;

/** Mirrors `ItemCodeView`: the case's official item code (step 9). */
export const itemCodeSchema = z.object({ id: z.string(), code: z.string() });
export type ItemCode = z.infer<typeof itemCodeSchema>;

/** Mirrors `SkuView`: one SKU under the item code. */
export const skuSchema = z.object({
  id: z.string(),
  sku_code: z.string(),
  variant_label: z.string(),
  planned_quantity: z.number().int().nullable(),
});
export type Sku = z.infer<typeof skuSchema>;

export const sampleRoundSchema = z.object({
  round_no: z.number().int(),
  opened_at: z.string(),
  opened_by: z.string(),
  result: sampleResultSchema.nullable(),
  evaluation_document_id: z.string().nullable(),
  closed_at: z.string().nullable(),
  closed_by: z.string().nullable(),
  revision_document_id: z.string().nullable(),
  requested_changes: z.string().nullable(),
});
export type SampleRound = z.infer<typeof sampleRoundSchema>;

/** Mirrors `ProductActionOptionView`: a step the case accepts now, what it
 * must carry, and the scope its duty needs. The server decides all of it; a
 * page draws its button and form from it and checks nothing of its own. */
export const productActionOptionSchema = z.object({
  action: productActionSchema,
  required_scope: z.string(),
  reason_required: z.boolean(),
  takes_supplier: z.boolean(),
  document_type: documentTypeSchema.nullable(),
  document_required: z.boolean(),
  /** The earliest upload the step takes as its paper; null: none qualifies. */
  documents_since: z.string().nullable(),
  /** What the case still lacks for this step (`item_code`, `sku`): the
   * server refuses it until this is empty. */
  unmet: z.array(z.string()),
});
export type ProductActionOption = z.infer<typeof productActionOptionSchema>;

/** One step of a sign-off round, as stamped when the case was submitted. */
export const signoffStepSchema = z.object({
  step: z.string(),
  label: z.string(),
});
export type SignoffStep = z.infer<typeof signoffStepSchema>;

/** Mirrors `PendingReviewView`: the approval a waiting case is held on (BGĐ's
 * review, or the current sign-off step) and the scope stamped on it, which is
 * who may decide it at `/approvals`. For a sign-off, its step, number and the
 * round's steps in order; null and empty for a review. */
export const pendingReviewSchema = z.object({
  approval_id: z.string(),
  created_at: z.string().nullable(),
  required_scope: z.string().nullable(),
  step: z.string().nullable(),
  step_label: z.string().nullable(),
  step_no: z.number().int().nullable(),
  steps: z.array(signoffStepSchema),
});
export type PendingReview = z.infer<typeof pendingReviewSchema>;

/** Mirrors `ProductCaseDetailView`: the case, its sample rounds, its steps,
 * while it waits for BGĐ or its sign-off the approval it waits on (null when
 * none is raised yet, or the viewer may not see it), and its item code and
 * SKUs. */
export const productCaseDetailSchema = productCaseSchema.extend({
  rounds: z.array(sampleRoundSchema),
  actions: z.array(productActionOptionSchema),
  pending_review: pendingReviewSchema.nullable(),
  item_code: itemCodeSchema.nullable(),
  skus: z.array(skuSchema),
  /** The PO case ĐẶT HÀNG opened; null until the case is ordered. */
  po_case_id: z.string().nullable(),
});
export type ProductCaseDetail = z.infer<typeof productCaseDetailSchema>;

/** Mirrors `ReviewRaise`: what asking for the approval a step left the case
 * waiting on (BGĐ's review, the sign-off) did. */
export const reviewRaiseSchema = z.enum([
  "raised",
  "already_pending",
  "not_raised",
  "not_waiting",
]);
export type ReviewRaise = z.infer<typeof reviewRaiseSchema>;

/** Mirrors `ProductCaseStepView`: a step taken; `review` is set only for a
 * step that left the case waiting for BGĐ. */
export const productCaseStepSchema = productCaseSchema.extend({
  review: reviewRaiseSchema.nullable(),
});
export type ProductCaseStep = z.infer<typeof productCaseStepSchema>;

/** Mirrors `OrderPlacedView`: ĐẶT HÀNG done, and the PO case it opened. */
export const orderPlacedSchema = productCaseSchema.extend({
  po_case_id: z.string(),
});
export type OrderPlaced = z.infer<typeof orderPlacedSchema>;

/** One row of a product case's history, oldest first. */
export const productCaseTransitionSchema = z.object({
  action: productActionSchema,
  from_state: productDevStateSchema.nullable(),
  to_state: productDevStateSchema,
  reason: z.string().nullable(),
  actor_id: z.string(),
  occurred_at: z.string(),
  /** The paper of step 7 or 8; a round's paper is on the round. */
  document_id: z.string().nullable(),
});
export type ProductCaseTransition = z.infer<typeof productCaseTransitionSchema>;

/** The duty names the role catalogue grants as `supply_chain.duty.<duty>`. */
export const caseDutySchema = z.enum([
  "ordering",
  "finance",
  "qc",
  "logistics",
  "warehouse",
  "exceptions",
  "rnd",
  "supply_lead",
]);
export type CaseDuty = z.infer<typeof caseDutySchema>;

/** Mirrors `SupplyChainProductActionDuties`. */
export const productActionDutiesSchema = z.object({
  schema_version: z.string(),
  policy_id: z.string(),
  policy_version: z.string(),
  action_duties: z.record(productActionSchema, caseDutySchema),
});
export type ProductActionDuties = z.infer<typeof productActionDutiesSchema>;

/** Narrowing for `GET /product-cases`, mirroring `ProductCaseListFilter`. */
export interface ProductCaseListFilter {
  state?: ProductDevState;
  picUserId?: string;
}

/** The body of `POST /product-cases/{id}/transitions`. */
export interface ProductCaseStepInput {
  action: ProductAction;
  reason?: string;
  supplierName?: string;
  documentId?: string;
  /** `issue_item_code` */
  itemCode?: string;
  /** `add_sku` */
  sku?: { skuCode: string; variantLabel: string; plannedQuantity?: number };
  /** `remove_sku` */
  skuId?: string;
}

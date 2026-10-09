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

/** One pending approval naming a PO case: the step waiting on it. */
export const caseApprovalSchema = z.object({
  id: z.string(),
  action: z.string(),
  requested_at: z.string().nullable(),
});
export type CaseApproval = z.infer<typeof caseApprovalSchema>;

/** A PO case's pending approvals, filtered by the server. `visible` is false
 * when the caller may not read the approval inbox: not looked at, which is not
 * the same as none pending. `total` counts every one; `items` is the newest. */
export const caseApprovalsSchema = z.object({
  visible: z.boolean(),
  total: z.number(),
  items: z.array(caseApprovalSchema),
});
export type CaseApprovals = z.infer<typeof caseApprovalsSchema>;

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

// -- Product-development cases where they are named in passing ------------------

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

export const sampleResultSchema = z.enum([
  "passed",
  "needs_revision",
  "rejected",
]);
export type SampleResult = z.infer<typeof sampleResultSchema>;

/** `ProductCaseRefView`: a product case named in a brief group or a
 * command-bar answer — what identifies it and where it stands. */
export const productCaseRefSchema = z.object({
  id: z.string(),
  proposal_code: z.string(),
  product_name: z.string(),
  /** The Category key it was stamped with; the label is the tenant's list's. */
  category: z.string(),
  pic_user_id: z.string(),
  state: productDevStateSchema,
});
export type ProductCaseRef = z.infer<typeof productCaseRefSchema>;

// -- Command bar: the structured AI answer -------------------------------------

export const caseQueryKindSchema = z.enum([
  "list_cases",
  "open_case",
  "list_product_cases",
  "open_product_case",
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
  // Product-development cases (stage-1 ticket 08).
  "product_list",
  "product_open",
  "proposal_code_missing",
  "category_not_found",
  "category_ambiguous",
  "pic_not_found",
  "pic_ambiguous",
  "product_not_found",
  "product_ambiguous",
]);
export type CaseQueryOutcome = z.infer<typeof caseQueryOutcomeSchema>;

export const groundedFieldSchema = z.enum([
  "supplier",
  "po_reference",
  "state",
  "active_only",
  "proposal_code",
  "product_state",
  "category",
  "pic",
  "mine",
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

export const productCaseTableDataViewSchema = z.object({
  type: z.literal("product_case_table"),
  rows: z.array(productCaseRefSchema),
  has_more: z.boolean(),
});

export const productCaseLinkDataViewSchema = z.object({
  type: z.literal("product_case_link"),
  case: productCaseRefSchema,
});

/** The closed set of views an answer may carry. An unknown `type` fails the
 * whole response — it is never rendered as raw data or guessed at. */
export const dataViewSchema = z.discriminatedUnion("type", [
  caseTableDataViewSchema,
  caseLinkDataViewSchema,
  productCaseTableDataViewSchema,
  productCaseLinkDataViewSchema,
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
    product_state: productDevStateSchema.nullable(),
    /** A key of the tenant's Category list. */
    category: z.string().nullable(),
    /** A person of the workspace, or the asker for "mine". */
    pic_user_id: z.string().nullable(),
    proposal_code: z.string().nullable(),
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
  // Stage 1 (ticket 08): product-development cases.
  "product_sla_breached",
  "product_awaiting_bod",
  "product_awaiting_signoff",
  "sample_evaluated_today",
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

/** One product case in one stage-1 group: days in its state (with the SLA
 * it overran), or, for a sample evaluated today, the round and its result. */
export const productBriefEntrySchema = z.object({
  case: productCaseRefSchema,
  days: z.number().nullable(),
  limit_days: z.number().nullable(),
  round_no: z.number().nullable(),
  sample_result: sampleResultSchema.nullable(),
});
export type ProductBriefEntry = z.infer<typeof productBriefEntrySchema>;

/** Every case one deterministic signal holds for. `total` counts them all;
 * `entries` carries at most ten, longest-standing first. `state` is set when
 * a state defines the group — the list it drills down to. */
export const briefGroupSchema = z.object({
  key: z.string(),
  signal: briefSignalSchema,
  qualifier: z.string().nullable(),
  state: caseStateSchema.nullable(),
  /** The product state that defines a stage-1 group, when one does. */
  product_state: productDevStateSchema.nullable(),
  total: z.number(),
  entries: z.array(briefEntrySchema),
  /** A stage-1 group's cases; `entries` is then empty. */
  product_entries: z.array(productBriefEntrySchema),
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
  /** False when product cases were not looked at (no read scope); the two
   * counts are then 0. */
  product_cases_visible: z.boolean(),
  active_product_case_count: z.number(),
  flagged_product_case_count: z.number(),
  groups: z.array(briefGroupSchema),
  /** How many cases a group lists at most; for pending approvals and recent
   * changes only the newest this many were read. `total` counts every one. */
  entries_shown: z.number(),
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

// ---- case documents (ADR 0021) --------------------------------------------

/** ADR 0021's fourteen document types and step 12's three (slice PK); the
 * API's `DocumentType`, checked against it in `@dw/api-client`. */
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
  "colour_sample",
  "packaging_design",
  "pre_production_test_report",
  // A supplier's quotation or specification (ai-automation/02).
  "supplier_quotation",
  // The submission to BGĐ at step 6 (tờ trình; ai-automation/03).
  "bod_submission",
]);
export type DocumentType = z.infer<typeof documentTypeSchema>;

/** Which kind of case a document belongs to; the API's `CaseKind`. */
export const caseKindSchema = z.enum(["po", "product"]);
export type CaseKind = z.infer<typeof caseKindSchema>;

/** An open follow-up: the work a reminder, an escalation or an SLA breach
 * handed to a person. `mine`: it was handed to the caller (a scope they hold,
 * or they are its stamped PIC), so the caller is expected to act and may close
 * it. `case_kind` says which page `case_id` opens; `reference` is the PO
 * reference (null while the case awaits its PO) or a product case's proposal
 * code. Mirrors `FollowUpItemView`. */
export const followUpKindSchema = z.enum([
  "update_reminder",
  "update_escalation",
  "sla_breach",
]);
export type FollowUpKind = z.infer<typeof followUpKindSchema>;

export const followUpSchema = z.object({
  id: z.string(),
  case_kind: caseKindSchema,
  case_id: z.string(),
  reference: z.string().nullable(),
  supplier_name: z.string().nullable(),
  kind: followUpKindSchema,
  milestone: z.string().nullable(),
  days: z.number().int(),
  limit_days: z.number().int().nullable(),
  opened_at: z.string(),
  notified_at: z.string().nullable(),
  mine: z.boolean(),
});
export type FollowUp = z.infer<typeof followUpSchema>;

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

/** Mirrors `ProductCategoryView`: one Category of the tenant's list. `key`
 * is what a case is stamped with; `label` is what a person reads. A case
 * opened before the list existed carries free text that is no key. */
export const productCategorySchema = z.object({
  key: z.string(),
  label: z.string(),
});
export type ProductCategory = z.infer<typeof productCategorySchema>;

/** Mirrors `ProductCaseView`. `category` is a key of the tenant's list
 * (`productCategorySchema`). */
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
  /** The current step's SLA under the case's Category (ticket 06). */
  sla: slaEvaluationSchema,
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
  /** A Category key of the tenant's list. */
  category?: string;
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

// ---- step 12's colour, packaging and pre-production sub-flow (slice PK) -------

export const packagingActionSchema = z.enum([
  "approve_colour",
  "request_colour_revision",
  "approve_design",
  "request_design_revision",
  "receive_pre_production_sample",
  "pass_pre_production_test",
  "fail_pre_production_test",
]);
export type PackagingAction = z.infer<typeof packagingActionSchema>;

export const reviewStatusSchema = z.enum([
  "pending",
  "revision_requested",
  "approved",
]);
export type ReviewStatus = z.infer<typeof reviewStatusSchema>;

export const preProductionTestSchema = z.enum(["pending", "passed", "failed"]);
export type PreProductionTest = z.infer<typeof preProductionTestSchema>;

/** One step as the caller sees it. `allowed`: the caller holds the step's
 * duty, by the same check the step runs. Mirrors `PackagingStepOptionView`. */
export const packagingStepOptionSchema = z.object({
  action: packagingActionSchema,
  duty: caseDutySchema,
  allowed: z.boolean(),
  requires_reason: z.boolean(),
  requires_document: z.boolean(),
});
export type PackagingStepOption = z.infer<typeof packagingStepOptionSchema>;

/** One step taken, oldest first. Mirrors `PackagingEventView`. */
export const packagingEventSchema = z.object({
  action: packagingActionSchema,
  reason: z.string().nullable(),
  note: z.string().nullable(),
  document_id: z.string().nullable(),
  actor_id: z.string(),
  occurred_at: z.string(),
});
export type PackagingEvent = z.infer<typeof packagingEventSchema>;

/** Mirrors `PackagingStateView`: what a step returns. */
export const packagingStateSchema = z.object({
  po_case_id: z.string(),
  colour_status: reviewStatusSchema,
  design_status: reviewStatusSchema,
  pre_production_sample_received_at: z.string().nullable(),
  pre_production_test: preProductionTestSchema,
  version: z.number().int(),
});
export type PackagingState = z.infer<typeof packagingStateSchema>;

/** Mirrors `PackagingDesignView`. */
export const packagingDesignSchema = packagingStateSchema.extend({
  case_state: caseStateSchema,
  require_pre_production_test: z.boolean(),
  steps: z.array(packagingStepOptionSchema),
  history: z.array(packagingEventSchema),
});
export type PackagingDesign = z.infer<typeof packagingDesignSchema>;

// ---- the tenant's own policies (settings screen) ---------------------------

/** Whether a milestone's number is in force (`confirmed`) or still waiting for
 * the customer (`pending_business_confirmation`: never evaluated). */
export const slaConfirmationStatusSchema = z.enum([
  "confirmed",
  "pending_business_confirmation",
]);
export type SLAConfirmationStatus = z.infer<typeof slaConfirmationStatusSchema>;

/** One SLA milestone: `duration` is whole days written `<n>d`. */
export const slaMilestoneSchema = z.object({
  duration: z.string().regex(/^\d+d$/),
  status: slaConfirmationStatusSchema,
  description: z.string(),
});
export type SLAMilestone = z.infer<typeof slaMilestoneSchema>;

/** The tenant's SLA policy, read and replaced whole (`PUT /sla-policy`). */
export const slaPolicySchema = z.object({
  schema_version: z.string(),
  policy_id: z.string(),
  policy_version: z.string(),
  categories: z.array(productCategorySchema),
  default: z.record(z.string(), slaMilestoneSchema),
  by_category: z.record(z.string(), z.record(z.string(), slaMilestoneSchema)),
  supplier_update: z.object({
    reminder_after: z.string().regex(/^\d+d$/),
    escalation_after: z.string().regex(/^\d+d$/),
  }),
});
export type SLAPolicy = z.infer<typeof slaPolicySchema>;

/** Step 13's rule: whether production waits for a passed pre-production test. */
export const packagingPolicySchema = z.object({
  schema_version: z.string(),
  policy_id: z.string(),
  policy_version: z.string(),
  require_pre_production_test: z.boolean(),
});
export type PackagingPolicy = z.infer<typeof packagingPolicySchema>;

// ---- commercial data and BM04 as fields (ADR 0026, ticket ai-automation/01) --

/** Incoterms 2020; one list, `Incoterm` in `dw_supply_chain`. */
export const incotermSchema = z.enum([
  "EXW",
  "FCA",
  "CPT",
  "CIP",
  "DAP",
  "DPU",
  "DDP",
  "FAS",
  "FOB",
  "CFR",
  "CIF",
]);
export type Incoterm = z.infer<typeof incotermSchema>;

export const paymentKindSchema = z.enum(["deposit", "final"]);
export type PaymentKind = z.infer<typeof paymentKindSchema>;

/** A price or amount: its value as a decimal string, or null with `redacted`
 * when the caller lacks `supply_chain.commercial.read` (never a 0). */
export const redactableAmountSchema = z.object({
  value: z.string().nullable(),
  redacted: z.boolean(),
});
export type RedactableAmount = z.infer<typeof redactableAmountSchema>;

export const bm04FieldKindSchema = z.enum([
  "text",
  "number",
  "integer",
  "boolean",
  "choice",
]);
export type Bm04FieldKind = z.infer<typeof bm04FieldKindSchema>;

/** One field of the tenant's BM04 schema (`Bm04Field`). */
export const bm04FieldSchema = z.object({
  key: z.string(),
  label: z.string(),
  kind: bm04FieldKindSchema,
  required: z.boolean(),
  unit: z.string().nullable().optional(),
  max_length: z.number().int().nullable().optional(),
  options: z.array(z.string()).nullable().optional(),
});
export type Bm04Field = z.infer<typeof bm04FieldSchema>;

/** The tenant's BM04 schema, read and replaced whole (`/bm04-schema`). */
export const bm04SchemaSchema = z.object({
  schema_version: z.string(),
  policy_id: z.string(),
  policy_version: z.string(),
  fields: z.array(bm04FieldSchema),
});
export type Bm04Schema = z.infer<typeof bm04SchemaSchema>;

/** Mirrors `ProductProfileView`: the latest BM04 version (`version` null
 * before the first save) and the schema it is filled against. */
export const productProfileSchema = z.object({
  product_dev_case_id: z.string().uuid(),
  version: z.number().int().nullable(),
  attributes: z.record(z.string(), z.unknown()),
  unit_price: redactableAmountSchema,
  currency: z.string().nullable(),
  moq: z.number().int().nullable(),
  lead_time_days: z.number().int().nullable(),
  incoterm: incotermSchema.nullable(),
  schema_version: z.string().nullable(),
  bm04_schema: bm04SchemaSchema,
  prices_visible: z.boolean(),
  can_edit: z.boolean(),
  can_edit_prices: z.boolean(),
  created_by: z.string().uuid().nullable(),
  created_at: z.string().nullable(),
});
export type ProductProfile = z.infer<typeof productProfileSchema>;

/** Mirrors `PricedLineView`. */
export const pricedLineSchema = z.object({
  sku_id: z.string().uuid(),
  sku_code: z.string().nullable(),
  variant_label: z.string().nullable(),
  quantity: z.number().int().nullable(),
  unit_price: redactableAmountSchema,
  line_total: redactableAmountSchema,
});
export type PricedLine = z.infer<typeof pricedLineSchema>;

/** Mirrors `POPaymentView`. */
export const poPaymentSchema = z.object({
  id: z.string().uuid(),
  kind: paymentKindSchema,
  version: z.number().int(),
  amount: redactableAmountSchema,
  currency: z.string(),
  due_date: z.string().nullable(),
  paid_on: z.string().nullable(),
  document_id: z.string().uuid().nullable(),
  recorded_by: z.string().uuid(),
  recorded_at: z.string(),
});
export type POPayment = z.infer<typeof poPaymentSchema>;

/** Mirrors `POCommercialView`. `order_total` is computed by the server and
 * null (not redacted) while a line lacks a quantity or a price. */
export const poCommercialSchema = z.object({
  po_case_id: z.string().uuid(),
  currency: z.string().nullable(),
  incoterm: incotermSchema.nullable(),
  payment_terms: z.string().nullable(),
  payment_terms_redacted: z.boolean(),
  deposit_percent: redactableAmountSchema,
  expected_delivery_date: z.string().nullable(),
  lines: z.array(pricedLineSchema),
  order_total: redactableAmountSchema,
  payments: z.array(poPaymentSchema),
  prices_visible: z.boolean(),
  can_edit: z.boolean(),
});
export type POCommercial = z.infer<typeof poCommercialSchema>;

// ---- document drafts (ADR 0025 point 4, ticket ai-automation/03) -----------

export const templateFieldKindSchema = z.enum([
  "text",
  "number",
  "date",
  "table",
]);
export type TemplateFieldKind = z.infer<typeof templateFieldKindSchema>;

/** What a draft version is: open, decided, or replaced by a newer version. */
export const draftStatusSchema = z.enum([
  "open",
  "confirmed",
  "rejected",
  "superseded",
]);
export type DraftStatus = z.infer<typeof draftStatusSchema>;

export const draftColumnSchema = z.object({
  name: z.string(),
  label: z.string(),
  kind: templateFieldKindSchema,
});
export type DraftColumn = z.infer<typeof draftColumnSchema>;

/** Where a value came from: a document and its quote, or the person who
 * typed it; null when code computed it from stored data. */
export const draftFieldSourceSchema = z.object({
  document_id: z.string().uuid().nullable(),
  quote: z.string().nullable(),
  edited_by: z.string().uuid().nullable(),
  // Words a model wrote that checked out against the evidence it cites.
  ai_written: z.boolean(),
  cites: z.array(z.string()),
});
export type DraftFieldSource = z.infer<typeof draftFieldSourceSchema>;

/** Mirrors `DraftFieldView`. A hidden price is `value: null, redacted: true`
 * (a table names its hidden columns in `redacted_columns`). */
export const draftFieldSchema = z.object({
  name: z.string(),
  label: z.string(),
  kind: templateFieldKindSchema,
  required: z.boolean(),
  value: z.string().nullable(),
  rows: z.array(z.record(z.string(), z.string().nullable())).nullable(),
  columns: z.array(draftColumnSchema).nullable(),
  redacted: z.boolean(),
  redacted_columns: z.array(z.string()),
  gap: z.boolean(),
  source: draftFieldSourceSchema.nullable(),
});
export type DraftField = z.infer<typeof draftFieldSchema>;

export const draftSourceSchema = z.object({
  document_id: z.string().uuid(),
  sha256: z.string(),
  extraction_id: z.string().uuid().nullable(),
});
export type DraftSource = z.infer<typeof draftSourceSchema>;

/** Mirrors `DraftView`: one version of a draft document. */
export const documentDraftSchema = z.object({
  id: z.string().uuid(),
  lineage_id: z.string().uuid(),
  version: z.number().int(),
  case_kind: z.enum(["po", "product"]),
  case_id: z.string().uuid(),
  doc_type: documentTypeSchema,
  template_id: z.string(),
  template_version: z.string(),
  title: z.string(),
  status: draftStatusSchema,
  decision_reason: z.string().nullable(),
  prompt_id: z.string().nullable(),
  prompt_version: z.string().nullable(),
  content_sha256: z.string(),
  gaps: z.array(z.string()),
  sources: z.array(draftSourceSchema),
  fields: z.array(draftFieldSchema),
  prices_visible: z.boolean(),
  can_edit: z.boolean(),
  created_by: z.string().uuid(),
  created_at: z.string(),
});
export type DocumentDraft = z.infer<typeof documentDraftSchema>;

// Ticket ai-automation/05: AI prepares a step, a person approves the move.

/** What AI read for a result field: shown beside the field, never its value. */
export const proposalSuggestionSchema = z.object({
  value: z.string(),
  quote: z.string(),
  document_id: z.string().uuid().nullable(),
});

/** A result a person types to approve a physical step (a test, a count). */
export const proposalResultFieldSchema = z.object({
  name: z.string(),
  label: z.string(),
  kind: z.string(),
  suggestion: proposalSuggestionSchema.nullable(),
  // The outcomes to choose from (ticket ai-automation/09); empty: typed freely.
  choices: z.array(z.object({ value: z.string(), label: z.string() })),
});
export type ProposalResultField = z.infer<typeof proposalResultFieldSchema>;

export const proposalFindingSchema = z.object({
  code: z.string(),
  subject: z.string(),
  message: z.string(),
});
export type ProposalFinding = z.infer<typeof proposalFindingSchema>;

export const stepProposalStatusSchema = z.enum([
  "none",
  "proposed",
  "not_prepared",
  "superseded",
  "rejected",
  "applied",
]);
export type StepProposalStatus = z.infer<typeof stepProposalStatusSchema>;

/** Mirrors `StepProposalView`: the case page's "AI đã chuẩn bị" block. */
export const stepProposalSchema = z.object({
  prepared: z.boolean(),
  action: z.string().nullable(),
  status: stepProposalStatusSchema,
  reason: z.string().nullable(),
  recorded_at: z.string().nullable(),
  approval_id: z.string().uuid().nullable(),
  required_scope: z.string().nullable(),
  stale: z.boolean(),
  can_decide: z.boolean(),
  physical: z.boolean(),
  drafts: z.array(
    z.object({
      draft_id: z.string().uuid(),
      doc_type: z.string(),
      version: z.number().int(),
      gaps: z.array(z.string()),
    }),
  ),
  sources: z.array(
    z.object({
      doc_type: z.string(),
      document_id: z.string().uuid().nullable(),
    }),
  ),
  action_document_id: z.string().uuid().nullable(),
  findings: z.array(proposalFindingSchema),
  result_fields: z.array(proposalResultFieldSchema),
});
export type StepProposal = z.infer<typeof stepProposalSchema>;

// Ticket ai-automation/07: messages to a supplier, AI drafts, a person sends.

export const supplierMessagePurposeSchema = z.enum([
  "sample_request",
  "supplier_reminder",
  "supplier_confirmation",
  "sample_revision_request",
]);
export type SupplierMessagePurpose = z.infer<
  typeof supplierMessagePurposeSchema
>;

/** `drafted`: a body to copy; `refused` / `failed`: AI wrote nothing that
 * checks out, a person writes this one. */
export const supplierMessageStatusSchema = z.enum([
  "drafted",
  "refused",
  "failed",
]);
export type SupplierMessageStatus = z.infer<typeof supplierMessageStatusSchema>;

/** Mirrors `SupplierMessageView`: one drafted message, and who marked it
 * sent. Nothing in the app sends it. */
export const supplierMessageSchema = z.object({
  id: z.string().uuid(),
  case_kind: z.enum(["po", "product"]),
  case_id: z.string().uuid(),
  purpose: supplierMessagePurposeSchema,
  status: supplierMessageStatusSchema,
  supplier_name: z.string().nullable(),
  recipient_name: z.string().nullable(),
  recipient_email: z.string().nullable(),
  subject: z.string(),
  body: z.string(),
  attachments: z.array(z.string().uuid()),
  citations: z.array(
    z.object({ text: z.string(), cites: z.array(z.string()) }),
  ),
  dropped: z.number().int(),
  template_version: z.string(),
  prompt_id: z.string(),
  prompt_version: z.string(),
  content_sha256: z.string(),
  created_at: z.string(),
  sent_by: z.string().uuid().nullable(),
  sent_at: z.string().nullable(),
});
export type SupplierMessage = z.infer<typeof supplierMessageSchema>;

// Ticket ai-automation/08: step 1 from a list.

/** Mirrors `ProposalListSummaryView`. */
export const proposalListSummarySchema = z.object({
  id: z.string().uuid(),
  filename: z.string(),
  content_type: z.string(),
  size_bytes: z.number().int(),
  uploaded_by: z.string().uuid(),
  created_at: z.string(),
});
export type ProposalListSummary = z.infer<typeof proposalListSummarySchema>;

export const proposalRowFindingCodeSchema = z.enum([
  "proposal_code_taken",
  "proposal_code_repeated",
  "item_code_taken",
  "product_seen",
  "category_unknown",
  "no_product_name",
]);
export type ProposalRowFindingCode = z.infer<
  typeof proposalRowFindingCodeSchema
>;

export const citedValueSchema = z.object({
  value: z.string(),
  quote: z.string(),
});
export type CitedValue = z.infer<typeof citedValueSchema>;

export const proposalRowDecisionSchema = z.object({
  decision: z.enum(["proposed", "dropped"]),
  product_dev_case_id: z.string().uuid().nullable(),
  reason: z.string().nullable(),
  decided_by: z.string().uuid(),
  decided_at: z.string(),
});
export type ProposalRowDecision = z.infer<typeof proposalRowDecisionSchema>;

/** Mirrors `ProposalRowView`: one product AI read from the list. */
export const proposalRowSchema = z.object({
  index: z.number().int(),
  fields: z.record(z.string(), citedValueSchema.nullable()),
  gaps: z.array(z.string()),
  category: z.string().nullable(),
  category_reason: z.string().nullable(),
  priority: z.string().nullable(),
  priority_reason: z.string().nullable(),
  findings: z.array(
    z.object({ code: proposalRowFindingCodeSchema, message: z.string() }),
  ),
  decision: proposalRowDecisionSchema.nullable(),
});
export type ProposalRow = z.infer<typeof proposalRowSchema>;

/** Mirrors `ProposalListDetailView`. `status` null: AI has not read it yet. */
export const proposalListDetailSchema = proposalListSummarySchema.extend({
  status: z.enum(["extracted", "unreadable", "refused", "failed"]).nullable(),
  rows: z.array(proposalRowSchema),
});
export type ProposalListDetail = z.infer<typeof proposalListDetailSchema>;

// Ticket ai-automation/09: a sample round's checklist.

export const sampleVerdictSchema = z.enum(["pass", "fail", "unmeasured"]);
export type SampleVerdict = z.infer<typeof sampleVerdictSchema>;

/** Mirrors `ChecklistRowView`: a criterion, its newest value this round and
 * code's verdict. */
export const sampleChecklistRowSchema = z.object({
  key: z.string(),
  label: z.string(),
  kind: z.enum(["number", "check"]),
  unit: z.string().nullable(),
  min: z.string().nullable(),
  max: z.string().nullable(),
  standard: z.string(),
  value: z.string().nullable(),
  note: z.string().nullable(),
  verdict: sampleVerdictSchema,
});
export type SampleChecklistRow = z.infer<typeof sampleChecklistRowSchema>;

export const sampleChecklistSchema = z.object({
  case_id: z.string().uuid(),
  sample_round: z.number().int(),
  can_record: z.boolean(),
  rows: z.array(sampleChecklistRowSchema),
});
export type SampleChecklist = z.infer<typeof sampleChecklistSchema>;

// Ticket onboarding/01: the one-time import of a tenant's existing data.

export const importSheetSchema = z.enum(["suppliers", "catalogue", "users"]);
export type ImportSheet = z.infer<typeof importSheetSchema>;

export const importRowStatusSchema = z.enum([
  "created",
  "exists",
  "partial",
  "rejected",
]);
export type ImportRowStatus = z.infer<typeof importRowStatusSchema>;

/** Mirrors `ImportRowView`: one row of the workbook, by its Excel number. */
export const importRowSchema = z.object({
  sheet: importSheetSchema,
  row: z.number().int(),
  key: z.string(),
  status: importRowStatusSchema,
  messages: z.array(z.string()),
});
export type ImportRow = z.infer<typeof importRowSchema>;

/** Mirrors `ImportReportView`. `dry_run`: nothing was written, "created"
 * reads "would be created". */
export const importReportSchema = z.object({
  dry_run: z.boolean(),
  sheets: z.array(
    z.object({
      sheet: importSheetSchema,
      title: z.string(),
      created: z.number().int(),
      exists: z.number().int(),
      partial: z.number().int(),
      rejected: z.number().int(),
    }),
  ),
  problems: z.array(
    z.object({ sheet: importSheetSchema, message: z.string() }),
  ),
  rows: z.array(importRowSchema),
});
export type ImportReport = z.infer<typeof importReportSchema>;

// Ticket ai-automation/14: step 10's PO draft.

/** Mirrors `PurchaseOrderProposalView`: the case's latest PO draft (its
 * fields are read through the drafts API, prices hidden per scope) and what
 * code finds in it now. No price anywhere here. */
export const purchaseOrderProposalSchema = z.object({
  draft_id: z.string().uuid().nullable(),
  draft_version: z.number().int().nullable(),
  draft_status: draftStatusSchema.nullable(),
  content_sha256: z.string().nullable(),
  findings: z.array(
    z.object({ code: z.string(), subject: z.string(), message: z.string() }),
  ),
  can_approve: z.boolean(),
  blocked_reason: z.string().nullable(),
});
export type PurchaseOrderProposal = z.infer<typeof purchaseOrderProposalSchema>;

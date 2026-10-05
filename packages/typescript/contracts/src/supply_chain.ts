import { z } from "zod";

/** Mirrors dw_supply_chain's presentation views. */

export const caseStateSchema = z.enum([
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

export const poCaseSchema = z.object({
  id: z.string().uuid(),
  po_reference: z.string(),
  supplier_name: z.string(),
  state: caseStateSchema,
  interrupted_state: caseStateSchema.nullable(),
  created_at: z.string().nullable(),
  version: z.number(),
});
export type POCase = z.infer<typeof poCaseSchema>;

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
  po_reference: z.string(),
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

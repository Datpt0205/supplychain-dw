/**
 * Auto-generated types for context "supply-chain" — do not edit.
 * Regenerate with `make generate-contracts`.
 */
export interface paths {
    "/api/v1/supply-chain/action-duties": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Action Duties Route
         * @description Which duty each case step belongs to for the caller's own tenant —
         *     their own override if they have set one, the platform default
         *     otherwise.
         */
        get: operations["get_action_duties_route_api_v1_supply_chain_action_duties_get"];
        /**
         * Set Action Duties Override Route
         * @description Replaces the caller's tenant's own step-to-duty mapping, whole.
         *     Every action must have a duty. No `Idempotency-Key`, same reasoning
         *     as `/sla-policy`'s own `PUT`.
         */
        put: operations["set_action_duties_override_route_api_v1_supply_chain_action_duties_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/approval-matrix": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Approval Matrix Route
         * @description The caller's own tenant's effective approval matrix — their own
         *     override if they have set one, the platform default (nothing
         *     gated) otherwise.
         */
        get: operations["get_approval_matrix_route_api_v1_supply_chain_approval_matrix_get"];
        /**
         * Set Approval Matrix Override Route
         * @description Replaces the caller's tenant's own approval matrix, whole. No
         *     `Idempotency-Key`, same reasoning as `/sla-policy`'s own `PUT`.
         */
        put: operations["set_approval_matrix_override_route_api_v1_supply_chain_approval_matrix_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/attention-queue": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Attention Queue Route */
        get: operations["get_attention_queue_route_api_v1_supply_chain_attention_queue_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/brief-policy": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Brief Policy Route
         * @description The caller's own tenant's effective brief order — their own
         *     override if they have set one, the platform default otherwise.
         */
        get: operations["get_brief_policy_route_api_v1_supply_chain_brief_policy_get"];
        /**
         * Set Brief Policy Override Route
         * @description Replaces the caller's tenant's own brief order, whole. Every
         *     signal must be listed exactly once: an override reorders the brief,
         *     it cannot hide a group from it. No `Idempotency-Key`, same reasoning
         *     as `/sla-policy`'s own `PUT`.
         */
        put: operations["set_brief_policy_override_route_api_v1_supply_chain_brief_policy_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/case-query": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Answer Case Query Route
         * @description A question about PO cases, answered as structured work.
         *     No `Idempotency-Key`: it changes nothing, and a stored reply replayed
         *     under a key would skip the handler's own authorization.
         */
        post: operations["answer_case_query_route_api_v1_supply_chain_case_query_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/control-tower/summary": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Portfolio Summary Route */
        get: operations["get_portfolio_summary_route_api_v1_supply_chain_control_tower_summary_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/daily-brief": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Daily Brief Route
         * @description What needs handling now, grouped by deterministic signal and
         *     ordered by the caller's tenant's own brief policy.
         */
        get: operations["get_daily_brief_route_api_v1_supply_chain_daily_brief_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/daily-brief/summary": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Summarize Daily Brief Route
         * @description The brief, with a model's summary of it that code has checked.
         *     A POST because it spends a model call; asked for explicitly, never on
         *     page load. No `Idempotency-Key`: it changes nothing, and a stored
         *     reply replayed under a key would skip the handler's own
         *     authorization.
         */
        post: operations["summarize_daily_brief_route_api_v1_supply_chain_daily_brief_summary_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/follow-up-policy": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Follow Up Policy Route */
        get: operations["get_follow_up_policy_route_api_v1_supply_chain_follow_up_policy_get"];
        /**
         * Set Follow Up Policy Override Route
         * @description Replaces the tenant's own routing, whole; every kind must still
         *     reach someone. No `Idempotency-Key`, as for the other policies.
         */
        put: operations["set_follow_up_policy_override_route_api_v1_supply_chain_follow_up_policy_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/follow-ups": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Follow Ups Route
         * @description The tenant's open follow-ups, newest first; the caller's own marked.
         */
        get: operations["list_follow_ups_route_api_v1_supply_chain_follow_ups_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/follow-ups/{follow_up_id}/done": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Close Follow Up Route
         * @description Mark a follow-up handled. Only someone it was handed to may.
         */
        post: operations["close_follow_up_route_api_v1_supply_chain_follow_ups__follow_up_id__done_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/po-cases": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Po Cases */
        get: operations["list_po_cases_api_v1_supply_chain_po_cases_get"];
        put?: never;
        /** Create Po Case */
        post: operations["create_po_case_api_v1_supply_chain_po_cases_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/po-cases/{case_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Po Case */
        get: operations["get_po_case_api_v1_supply_chain_po_cases__case_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/po-cases/{case_id}/delay-impact-analyses": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Delay Impact Analyses */
        get: operations["get_delay_impact_analyses_api_v1_supply_chain_po_cases__case_id__delay_impact_analyses_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/po-cases/{case_id}/missing-update-status": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Missing Update Status Route */
        get: operations["get_missing_update_status_route_api_v1_supply_chain_po_cases__case_id__missing_update_status_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/po-cases/{case_id}/sla-evaluation": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Sla Evaluation Route */
        get: operations["get_sla_evaluation_route_api_v1_supply_chain_po_cases__case_id__sla_evaluation_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/po-cases/{case_id}/supplier-updates": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Supplier Updates */
        get: operations["get_supplier_updates_api_v1_supply_chain_po_cases__case_id__supplier_updates_get"];
        put?: never;
        /** Create Supplier Update */
        post: operations["create_supplier_update_api_v1_supply_chain_po_cases__case_id__supplier_updates_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/po-cases/{case_id}/supplier-updates/{update_id}/delay-impact-analysis": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Create Delay Impact Analysis */
        post: operations["create_delay_impact_analysis_api_v1_supply_chain_po_cases__case_id__supplier_updates__update_id__delay_impact_analysis_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/po-cases/{case_id}/transitions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Case Transitions */
        get: operations["get_case_transitions_api_v1_supply_chain_po_cases__case_id__transitions_get"];
        put?: never;
        /** Create Po Case Transition */
        post: operations["create_po_case_transition_api_v1_supply_chain_po_cases__case_id__transitions_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/sla-policy": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Sla Policy Route
         * @description The caller's own tenant's effective SLA policy — their own
         *     override if they have set one, the platform default otherwise.
         */
        get: operations["get_sla_policy_route_api_v1_supply_chain_sla_policy_get"];
        /**
         * Set Sla Policy Override Route
         * @description Replaces the caller's tenant's own SLA policy, whole. No
         *     `Idempotency-Key`: a `PUT` replacing the same key with the same body
         *     is already idempotent on its own, unlike the `POST`-create routes
         *     above where a retry could otherwise create a second resource.
         */
        put: operations["set_sla_policy_override_route_api_v1_supply_chain_sla_policy_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /**
         * AIWorkResponseView
         * @description A structured AI answer, the parts this feature fills.
         *
         *     Structured data only, and `data_view` is a closed set of types a client
         *     renders with its own components — nothing here is markup, a link, or an
         *     action a model chose. No `narrative`: no model writes one yet, and the
         *     client composes its sentence from these fields. No `suggested_actions`:
         *     nothing here changes state yet (confirmation and execution come with
         *     the first action that does).
         */
        AIWorkResponseView: {
            /** Candidates */
            candidates: string[];
            /** Citations */
            citations: components["schemas"]["CitationView"][];
            /** Data View */
            data_view: (components["schemas"]["CaseTableDataView"] | components["schemas"]["CaseLinkDataView"]) | null;
            /** Ignored Fields */
            ignored_fields: components["schemas"]["GroundedField"][];
            intent: components["schemas"]["CaseQueryKind"];
            outcome: components["schemas"]["CaseQueryOutcome"];
            understood: components["schemas"]["UnderstoodView"];
            /** Unusable Fields */
            unusable_fields: components["schemas"]["GroundedField"][];
        };
        /** AdvancePOCaseRequest */
        AdvancePOCaseRequest: {
            action: components["schemas"]["CaseAction"];
            /** Reason */
            reason?: string | null;
        };
        /** AttentionItemView */
        AttentionItemView: {
            case: components["schemas"]["POCaseView"];
            missing_update: components["schemas"]["MissingUpdateStatusView"] | null;
            sla: components["schemas"]["SLAEvaluationView"] | null;
        };
        /** BriefEntryView */
        BriefEntryView: {
            /** Approval Action */
            approval_action: string | null;
            case: components["schemas"]["POCaseView"];
            /** Days */
            days: number | null;
            /** Limit Days */
            limit_days: number | null;
            transition: components["schemas"]["CaseTransitionView"] | null;
        };
        /** BriefGroupView */
        BriefGroupView: {
            /** Entries */
            entries: components["schemas"]["BriefEntryView"][];
            /** Key */
            key: string;
            /** Qualifier */
            qualifier: string | null;
            signal: components["schemas"]["BriefSignal"];
            state: components["schemas"]["CaseState"] | null;
            /** Total */
            total: number;
        };
        /**
         * BriefSignal
         * @enum {string}
         */
        BriefSignal: "update_escalation_due" | "sla_breached" | "case_blocked" | "approval_pending" | "manual_review" | "supplier_reported_delay" | "update_reminder_due" | "waiting_external" | "rework" | "waiting_on_us" | "changed_recently";
        /** BriefSummarySentenceView */
        BriefSummarySentenceView: {
            /** Group Keys */
            group_keys: string[];
            /** Text */
            text: string;
        };
        /**
         * BriefSummaryStatus
         * @enum {string}
         */
        BriefSummaryStatus: "written" | "nothing_kept" | "nothing_written" | "nothing_to_summarize" | "unavailable";
        /** BriefSummaryView */
        BriefSummaryView: {
            /** Dropped */
            dropped: number;
            /** Sentences */
            sentences: components["schemas"]["BriefSummarySentenceView"][];
            status: components["schemas"]["BriefSummaryStatus"];
        };
        /**
         * CaseAction
         * @description Every guarded method above, named — the closed set an `AdvancePOCase`
         *     caller may request.
         *
         *     A closed enum, not a free string: this is a discrete choice a human
         *     makes from a fixed set of buttons a real UI shows for the case's current
         *     state, not free-form text a model interprets — the line between a model
         *     interpreting and code deciding: no model output is ever used directly
         *     as a route.
         *     Values match each method's own name exactly, so the mapping in
         *     `application/handlers.py` needs no separate translation table to drift
         *     from this list.
         * @enum {string}
         */
        CaseAction: "request_deposit" | "confirm_deposit" | "start_pre_production" | "start_production" | "send_to_qc" | "pass_qc" | "arrive_at_port" | "request_final_payment" | "confirm_payment" | "start_warehouse_receiving" | "complete" | "resume_from_rework" | "resume" | "fail_qc" | "wait_for_external" | "flag_blocked" | "flag_manual_review" | "cancel";
        /** CaseActionResultView */
        CaseActionResultView: {
            case?: components["schemas"]["POCaseView"] | null;
            /** Run Id */
            run_id?: string | null;
            /**
             * Status
             * @enum {string}
             */
            status: "applied" | "pending_approval";
        };
        /**
         * CaseDuty
         * @enum {string}
         */
        CaseDuty: "ordering" | "finance" | "qc" | "logistics" | "warehouse" | "exceptions";
        /** CaseLinkDataView */
        CaseLinkDataView: {
            case: components["schemas"]["POCaseView"];
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "case_link";
        };
        /**
         * CaseQueryKind
         * @enum {string}
         */
        CaseQueryKind: "list_cases" | "open_case" | "unsupported";
        /**
         * CaseQueryOutcome
         * @description What the reply to one question turned out to be. The first two run a
         *     lookup; every other one is a refusal that says why, never a widened or
         *     guessed answer.
         * @enum {string}
         */
        CaseQueryOutcome: "list" | "open" | "not_understood" | "supplier_not_found" | "supplier_ambiguous" | "po_reference_missing" | "po_not_found" | "po_ambiguous";
        /** CaseQueryRequest */
        CaseQueryRequest: {
            /** Question */
            question: string;
        };
        /**
         * CaseState
         * @description The Elmich product-to-stock happy path, plus the exceptions a real PO
         *     actually hits.
         *
         *     Declaration order is the happy-path order; the five exception states
         *     after COMPLETED are not part of that sequence and are reached only
         *     through the interrupt/rework/cancel methods below, never by position.
         * @enum {string}
         */
        CaseState: "po_created" | "waiting_deposit" | "deposit_confirmed" | "pre_production" | "production" | "qc" | "in_transit" | "arrived_port" | "waiting_payment" | "payment_completed" | "warehouse_receiving" | "completed" | "waiting_external" | "blocked" | "rework" | "manual_review" | "cancelled";
        /** CaseTableDataView */
        CaseTableDataView: {
            /** Has More */
            has_more: boolean;
            /** Rows */
            rows: components["schemas"]["POCaseView"][];
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "case_table";
        };
        /** CaseTransitionView */
        CaseTransitionView: {
            from_state: components["schemas"]["CaseState"];
            /**
             * Occurred At
             * Format: date-time
             */
            occurred_at: string;
            /** Reason */
            reason: string | null;
            to_state: components["schemas"]["CaseState"];
        };
        /**
         * CitationView
         * @description The question's own words one field was grounded in — reported
         *     whatever the outcome, so a refusal can show what WAS understood.
         */
        CitationView: {
            field: components["schemas"]["GroundedField"];
            /** Quote */
            quote: string;
        };
        /** CloseFollowUpRequest */
        CloseFollowUpRequest: {
            /** Note */
            note?: string | null;
        };
        /** CreatePOCaseRequest */
        CreatePOCaseRequest: {
            /** Po Reference */
            po_reference: string;
            /** Supplier Name */
            supplier_name: string;
        };
        /** DailyBriefSummaryView */
        DailyBriefSummaryView: {
            brief: components["schemas"]["DailyBriefView"];
            summary: components["schemas"]["BriefSummaryView"];
        };
        /** DailyBriefView */
        DailyBriefView: {
            /** Active Case Count */
            active_case_count: number;
            /** Approvals Visible */
            approvals_visible: boolean;
            /** Flagged Case Count */
            flagged_case_count: number;
            /**
             * Generated At
             * Format: date-time
             */
            generated_at: string;
            /** Groups */
            groups: components["schemas"]["BriefGroupView"][];
        };
        /** DelayImpactAnalysisView */
        DelayImpactAnalysisView: {
            /** Assumptions */
            assumptions: string[];
            /** Created At */
            created_at: string | null;
            /** Delay Days */
            delay_days: number;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Impacted Milestones */
            impacted_milestones: components["schemas"]["ImpactedMilestoneView"][];
            /** Mitigation Options */
            mitigation_options: components["schemas"]["MitigationOptionView"][];
            /**
             * Po Case Id
             * Format: uuid
             */
            po_case_id: string;
            /**
             * Supplier Update Id
             * Format: uuid
             */
            supplier_update_id: string;
        };
        /**
         * FollowUpItemView
         * @description An open follow-up. `mine`: the caller holds a scope it was handed to,
         *     so the caller is expected to act, and may close it.
         */
        FollowUpItemView: {
            /** Days */
            days: number;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            kind: components["schemas"]["FollowUpKind"];
            /** Limit Days */
            limit_days: number | null;
            /** Milestone */
            milestone: string | null;
            /** Mine */
            mine: boolean;
            /** Notified At */
            notified_at: string | null;
            /**
             * Opened At
             * Format: date-time
             */
            opened_at: string;
            /**
             * Po Case Id
             * Format: uuid
             */
            po_case_id: string;
            /** Po Reference */
            po_reference: string;
            /** Supplier Name */
            supplier_name: string;
        };
        /**
         * FollowUpKind
         * @enum {string}
         */
        FollowUpKind: "update_reminder" | "update_escalation" | "sla_breach";
        /**
         * GroundedField
         * @description One field of a reading, named. It keys a citation (the question's own
         *     words a field was grounded in, kept whatever the outcome) and a refusal's
         *     reason: a field that could not be grounded, or one that was grounded but
         *     that the chosen kind of answer cannot apply.
         * @enum {string}
         */
        GroundedField: "supplier" | "po_reference" | "state" | "active_only";
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /** ImpactedMilestoneView */
        ImpactedMilestoneView: {
            /** Estimated Delay Days */
            estimated_delay_days: number;
            milestone: components["schemas"]["CaseState"];
        };
        /**
         * MissingUpdateStatus
         * @enum {string}
         */
        MissingUpdateStatus: "on_track" | "reminder_due" | "escalation_due";
        /** MissingUpdateStatusView */
        MissingUpdateStatusView: {
            /** Age Days */
            age_days: number;
            /**
             * Reference At
             * Format: date-time
             */
            reference_at: string;
            status: components["schemas"]["MissingUpdateStatus"];
        };
        /** MitigationOptionView */
        MitigationOptionView: {
            /** Description */
            description: string;
            /** Tradeoff */
            tradeoff: string;
        };
        /** POCaseView */
        POCaseView: {
            /** Created At */
            created_at: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            interrupted_state: components["schemas"]["CaseState"] | null;
            /** Po Reference */
            po_reference: string;
            state: components["schemas"]["CaseState"];
            /** Supplier Name */
            supplier_name: string;
            /** Version */
            version: number;
        };
        /**
         * Page
         * @description A page of results and the cursor that continues it.
         *
         *     ``next_cursor`` is ``None`` on the last page, and that — not an empty
         *     ``items`` — is the client's stop condition: a filtered listing can return an
         *     empty page in the middle of a run and still have more rows behind it.
         */
        Page_POCaseView_: {
            /** Items */
            items: components["schemas"]["POCaseView"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /** PortfolioSummaryView */
        PortfolioSummaryView: {
            /** Active Case Count */
            active_case_count: number;
            /** By State */
            by_state: components["schemas"]["StateSummaryView"][];
            /** By Supplier */
            by_supplier: components["schemas"]["SupplierSummaryView"][];
            /** Sla Breached Count */
            sla_breached_count: number;
            /** Update Overdue Count */
            update_overdue_count: number;
        };
        /**
         * SLAConfirmationStatus
         * @description Whether a milestone's duration is real policy yet.
         *
         *     `PENDING_BUSINESS_CONFIRMATION` marks a number nobody has confirmed,
         *     such as Elmich's reference values, which 1.0.0 shipped that way.
         *     `CONFIRMED` is the only status a caller may treat as an enforceable
         *     threshold. The shipped 1.1.0 test values set it on Đạt's instruction; a
         *     customer's real numbers are that tenant's own override.
         * @enum {string}
         */
        SLAConfirmationStatus: "pending_business_confirmation" | "confirmed";
        /**
         * SLAEvaluationStatus
         * @enum {string}
         */
        SLAEvaluationStatus: "not_applicable" | "not_evaluable" | "on_track" | "breached";
        /** SLAEvaluationView */
        SLAEvaluationView: {
            /** Age Days */
            age_days: number;
            /**
             * Entered Current State At
             * Format: date-time
             */
            entered_current_state_at: string;
            /** Milestone */
            milestone: string | null;
            status: components["schemas"]["SLAEvaluationStatus"];
            /** Threshold Days */
            threshold_days: number | null;
        };
        /** SLAMilestone */
        SLAMilestone: {
            /**
             * Description
             * @default
             */
            description: string;
            /** Duration */
            duration: string;
            status: components["schemas"]["SLAConfirmationStatus"];
        };
        /** StateSummaryView */
        StateSummaryView: {
            /** Case Count */
            case_count: number;
            /** Oldest In State Days */
            oldest_in_state_days: number;
            /** Sla Breached Count */
            sla_breached_count: number;
            state: components["schemas"]["CaseState"];
            /** Update Overdue Count */
            update_overdue_count: number;
        };
        /** SubmitSupplierUpdateRequest */
        SubmitSupplierUpdateRequest: {
            /** Raw Text */
            raw_text: string;
        };
        /**
         * SupplierEventType
         * @enum {string}
         */
        SupplierEventType: "production_delay" | "qc_issue" | "shipment_update" | "deposit_confirmation" | "document_submitted" | "no_official_update" | "other";
        /** SupplierSummaryView */
        SupplierSummaryView: {
            /** Case Count */
            case_count: number;
            /** Escalation Due Count */
            escalation_due_count: number;
            /** Longest Silence Days */
            longest_silence_days: number;
            /** Sla Breached Count */
            sla_breached_count: number;
            /** Supplier Name */
            supplier_name: string;
            /** Update Overdue Count */
            update_overdue_count: number;
        };
        /**
         * SupplierUpdateCadence
         * @description How long a case may go without a supplier update: a reminder is due
         *     after `reminder_after`, an escalation after `escalation_after`. "0d"
         *     makes a reminder due at once, which is how a tenant tests the chain.
         */
        SupplierUpdateCadence: {
            /** Escalation After */
            escalation_after: string;
            /** Reminder After */
            reminder_after: string;
        };
        /** SupplierUpdateView */
        SupplierUpdateView: {
            /** Affected Po */
            affected_po: string | null;
            /** Confidence */
            confidence: number;
            /** Created At */
            created_at: string | null;
            /** Delay Days */
            delay_days: number | null;
            event_type: components["schemas"]["SupplierEventType"];
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Po Case Id
             * Format: uuid
             */
            po_case_id: string;
            /** Proposed Action */
            proposed_action: string;
            /** Raw Text */
            raw_text: string;
            /** Reason */
            reason: string;
            /** Requires Confirmation */
            requires_confirmation: boolean;
            /** Source Ref */
            source_ref: string;
        };
        /** SupplyChainActionDuties */
        SupplyChainActionDuties: {
            /** Action Duties */
            action_duties: {
                [key: string]: components["schemas"]["CaseDuty"];
            };
            /** Policy Id */
            policy_id: string;
            /** Policy Version */
            policy_version: string;
            /** Schema Version */
            schema_version: string;
        };
        /** SupplyChainApprovalMatrix */
        SupplyChainApprovalMatrix: {
            /**
             * Approval Required Actions
             * @default []
             */
            approval_required_actions: components["schemas"]["CaseAction"][];
            /** Policy Id */
            policy_id: string;
            /** Policy Version */
            policy_version: string;
            /** Schema Version */
            schema_version: string;
        };
        /** SupplyChainBriefPolicy */
        SupplyChainBriefPolicy: {
            /** Policy Id */
            policy_id: string;
            /** Policy Version */
            policy_version: string;
            /** Schema Version */
            schema_version: string;
            /** Signal Order */
            signal_order: components["schemas"]["BriefSignal"][];
        };
        /** SupplyChainFollowUpPolicy */
        SupplyChainFollowUpPolicy: {
            /** Policy Id */
            policy_id: string;
            /** Policy Version */
            policy_version: string;
            /** Recipients */
            recipients: {
                [key: string]: string[];
            };
            /** Schema Version */
            schema_version: string;
        };
        /**
         * SupplyChainSLAPolicy
         * @description The versioned answer to "how long should each PO milestone take".
         *
         *     `sla` is a dict keyed by milestone name rather than one field per
         *     milestone — same shape as `RetentionPolicy.classes` in dw_platform: the
         *     set of milestones is data (today the doc's six names; DW-SC-01 will add
         *     more once it exists), not a fixed set this code has to be edited to grow.
         */
        SupplyChainSLAPolicy: {
            /** Policy Id */
            policy_id: string;
            /** Policy Version */
            policy_version: string;
            /** Schema Version */
            schema_version: string;
            /** Sla */
            sla: {
                [key: string]: components["schemas"]["SLAMilestone"];
            };
            supplier_update: components["schemas"]["SupplierUpdateCadence"];
        };
        /**
         * UnderstoodView
         * @description What the answer applied, each value decided by code: a supplier is
         *     always a stored name, never the model's mention; a PO reference is the
         *     stored one once the case was found (the question's own spelling when it
         *     was not).
         */
        UnderstoodView: {
            /** Active Only */
            active_only: boolean;
            /** Po Reference */
            po_reference: string | null;
            state: components["schemas"]["CaseState"] | null;
            /** Supplier Name */
            supplier_name: string | null;
        };
        /** ValidationError */
        ValidationError: {
            /** Context */
            ctx?: Record<string, never>;
            /** Input */
            input?: unknown;
            /** Location */
            loc: (string | number)[];
            /** Message */
            msg: string;
            /** Error Type */
            type: string;
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    get_action_duties_route_api_v1_supply_chain_action_duties_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SupplyChainActionDuties"];
                };
            };
        };
    };
    set_action_duties_override_route_api_v1_supply_chain_action_duties_put: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SupplyChainActionDuties"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SupplyChainActionDuties"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_approval_matrix_route_api_v1_supply_chain_approval_matrix_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SupplyChainApprovalMatrix"];
                };
            };
        };
    };
    set_approval_matrix_override_route_api_v1_supply_chain_approval_matrix_put: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SupplyChainApprovalMatrix"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SupplyChainApprovalMatrix"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_attention_queue_route_api_v1_supply_chain_attention_queue_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AttentionItemView"][];
                };
            };
        };
    };
    get_brief_policy_route_api_v1_supply_chain_brief_policy_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SupplyChainBriefPolicy"];
                };
            };
        };
    };
    set_brief_policy_override_route_api_v1_supply_chain_brief_policy_put: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SupplyChainBriefPolicy"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SupplyChainBriefPolicy"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    answer_case_query_route_api_v1_supply_chain_case_query_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CaseQueryRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AIWorkResponseView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_portfolio_summary_route_api_v1_supply_chain_control_tower_summary_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PortfolioSummaryView"];
                };
            };
        };
    };
    get_daily_brief_route_api_v1_supply_chain_daily_brief_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DailyBriefView"];
                };
            };
        };
    };
    summarize_daily_brief_route_api_v1_supply_chain_daily_brief_summary_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DailyBriefSummaryView"];
                };
            };
        };
    };
    get_follow_up_policy_route_api_v1_supply_chain_follow_up_policy_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SupplyChainFollowUpPolicy"];
                };
            };
        };
    };
    set_follow_up_policy_override_route_api_v1_supply_chain_follow_up_policy_put: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SupplyChainFollowUpPolicy"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SupplyChainFollowUpPolicy"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_follow_ups_route_api_v1_supply_chain_follow_ups_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["FollowUpItemView"][];
                };
            };
        };
    };
    close_follow_up_route_api_v1_supply_chain_follow_ups__follow_up_id__done_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                follow_up_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CloseFollowUpRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_po_cases_api_v1_supply_chain_po_cases_get: {
        parameters: {
            query?: {
                limit?: number;
                /** @description Opaque cursor from a previous page. */
                cursor?: string | null;
                /** @description Only cases in this state. */
                state?: components["schemas"]["CaseState"] | null;
                /** @description Only this supplier's cases — an exact match on the stored name. */
                supplier_name?: string | null;
                /** @description Only active cases — every state the server does not treat as terminal, the same set the Control Tower counts. */
                active_only?: boolean;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Page_POCaseView_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_po_case_api_v1_supply_chain_po_cases_post: {
        parameters: {
            query?: never;
            header?: {
                /** @description Optional. Retrying with the same key returns the first response instead of acting twice; reusing it for a different request is a 409. */
                "Idempotency-Key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CreatePOCaseRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["POCaseView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_po_case_api_v1_supply_chain_po_cases__case_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                case_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["POCaseView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_delay_impact_analyses_api_v1_supply_chain_po_cases__case_id__delay_impact_analyses_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                case_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DelayImpactAnalysisView"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_missing_update_status_route_api_v1_supply_chain_po_cases__case_id__missing_update_status_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                case_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MissingUpdateStatusView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_sla_evaluation_route_api_v1_supply_chain_po_cases__case_id__sla_evaluation_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                case_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SLAEvaluationView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_supplier_updates_api_v1_supply_chain_po_cases__case_id__supplier_updates_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                case_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SupplierUpdateView"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_supplier_update_api_v1_supply_chain_po_cases__case_id__supplier_updates_post: {
        parameters: {
            query?: never;
            header?: {
                /** @description Optional. Retrying with the same key returns the first response instead of acting twice; reusing it for a different request is a 409. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                case_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SubmitSupplierUpdateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SupplierUpdateView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_delay_impact_analysis_api_v1_supply_chain_po_cases__case_id__supplier_updates__update_id__delay_impact_analysis_post: {
        parameters: {
            query?: never;
            header?: {
                /** @description Optional. Retrying with the same key returns the first response instead of acting twice; reusing it for a different request is a 409. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                case_id: string;
                update_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DelayImpactAnalysisView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_case_transitions_api_v1_supply_chain_po_cases__case_id__transitions_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                case_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CaseTransitionView"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_po_case_transition_api_v1_supply_chain_po_cases__case_id__transitions_post: {
        parameters: {
            query?: never;
            header?: {
                /** @description Optional. Retrying with the same key returns the first response instead of acting twice; reusing it for a different request is a 409. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                case_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AdvancePOCaseRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CaseActionResultView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_sla_policy_route_api_v1_supply_chain_sla_policy_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SupplyChainSLAPolicy"];
                };
            };
        };
    };
    set_sla_policy_override_route_api_v1_supply_chain_sla_policy_put: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SupplyChainSLAPolicy"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SupplyChainSLAPolicy"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
}

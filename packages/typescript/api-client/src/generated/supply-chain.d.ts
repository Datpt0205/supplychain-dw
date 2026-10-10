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
    "/api/v1/supply-chain/bm04-schema": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Bm04 Schema */
        get: operations["get_bm04_schema_api_v1_supply_chain_bm04_schema_get"];
        /**
         * Set Bm04 Schema
         * @description Replaces the tenant's BM04 schema, whole. No `Idempotency-Key`, as for
         *     the other policies' `PUT`.
         */
        put: operations["set_bm04_schema_api_v1_supply_chain_bm04_schema_put"];
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
    "/api/v1/supply-chain/doc-templates": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Doc Templates */
        get: operations["list_doc_templates_api_v1_supply_chain_doc_templates_get"];
        /**
         * Set Doc Template
         * @description The tenant's own version of a platform template: its declaration
         *     (YAML) and its DOCX. A version is never replaced; add a new one.
         */
        put: operations["set_doc_template_api_v1_supply_chain_doc_templates_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/documents/{document_id}/content": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Download Case Document */
        get: operations["download_case_document_api_v1_supply_chain_documents__document_id__content_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/drafts/{draft_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Document Draft */
        get: operations["get_document_draft_api_v1_supply_chain_drafts__draft_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/drafts/{draft_id}/file": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Render Document Draft */
        get: operations["render_document_draft_api_v1_supply_chain_drafts__draft_id__file_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/drafts/{draft_id}/rejection": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Reject Document Draft */
        post: operations["reject_document_draft_api_v1_supply_chain_drafts__draft_id__rejection_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/drafts/{draft_id}/revisions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Revise Document Draft */
        post: operations["revise_document_draft_api_v1_supply_chain_drafts__draft_id__revisions_post"];
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
    "/api/v1/supply-chain/imports": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Apply Import */
        post: operations["apply_import_api_v1_supply_chain_imports_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/imports/dry-run": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Dry Run Import */
        post: operations["dry_run_import_api_v1_supply_chain_imports_dry_run_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/imports/template": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Import Template */
        get: operations["get_import_template_api_v1_supply_chain_imports_template_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/item-code-rule": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Item Code Rule */
        get: operations["get_item_code_rule_api_v1_supply_chain_item_code_rule_get"];
        /**
         * Set Item Code Rule
         * @description Replaces the tenant's own rule, whole (`rule: null`: none).
         */
        put: operations["set_item_code_rule_api_v1_supply_chain_item_code_rule_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/packaging-policy": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Packaging Policy
         * @description Whether the tenant's PO cases need a passed pre-production test to
         *     enter production.
         */
        get: operations["get_packaging_policy_api_v1_supply_chain_packaging_policy_get"];
        /**
         * Set Packaging Policy
         * @description Replaces the tenant's own step-13 rule, whole. No `Idempotency-Key`,
         *     as for the other policies' `PUT`.
         */
        put: operations["set_packaging_policy_api_v1_supply_chain_packaging_policy_put"];
        post?: never;
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
    "/api/v1/supply-chain/po-cases/{case_id}/approvals": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Case Approvals
         * @description The pending approvals that name this case, filtered here rather
         *     than by the client: the newest few, and how many there are.
         */
        get: operations["get_case_approvals_api_v1_supply_chain_po_cases__case_id__approvals_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/po-cases/{case_id}/commercial": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Po Commercial */
        get: operations["get_po_commercial_api_v1_supply_chain_po_cases__case_id__commercial_get"];
        /** Set Po Commercial */
        put: operations["set_po_commercial_api_v1_supply_chain_po_cases__case_id__commercial_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/po-cases/{case_id}/create-po": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Create Po Route
         * @description Step 10 on a case awaiting its PO. A reference already taken in
         *     the tenant is a 409 naming it; a line still without a quantity, 409.
         */
        post: operations["create_po_route_api_v1_supply_chain_po_cases__case_id__create_po_post"];
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
    "/api/v1/supply-chain/po-cases/{case_id}/documents": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Case Documents */
        get: operations["list_case_documents_api_v1_supply_chain_po_cases__case_id__documents_get"];
        put?: never;
        /** Upload Case Document */
        post: operations["upload_case_document_api_v1_supply_chain_po_cases__case_id__documents_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/po-cases/{case_id}/drafts": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Po Case Drafts */
        get: operations["list_po_case_drafts_api_v1_supply_chain_po_cases__case_id__drafts_get"];
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
    "/api/v1/supply-chain/po-cases/{case_id}/packaging-design": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Packaging Design */
        get: operations["get_packaging_design_api_v1_supply_chain_po_cases__case_id__packaging_design_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/po-cases/{case_id}/packaging-design/steps": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Take Packaging Step */
        post: operations["take_packaging_step_api_v1_supply_chain_po_cases__case_id__packaging_design_steps_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/po-cases/{case_id}/packaging-proof": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Packaging Proof */
        get: operations["get_packaging_proof_api_v1_supply_chain_po_cases__case_id__packaging_proof_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/po-cases/{case_id}/payments": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Record Po Payment */
        post: operations["record_po_payment_api_v1_supply_chain_po_cases__case_id__payments_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/po-cases/{case_id}/pic": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Reassign Po Case Pic
         * @description Hands the case to another PIC (TP Cung ứng's duty, ticket 06).
         */
        post: operations["reassign_po_case_pic_api_v1_supply_chain_po_cases__case_id__pic_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/po-cases/{case_id}/pre-production-checklist": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Pre Production Checklist */
        get: operations["get_pre_production_checklist_api_v1_supply_chain_po_cases__case_id__pre_production_checklist_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/po-cases/{case_id}/pre-production-measurements": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Record Pre Production Measurement
         * @description R&D enters one value; a correction is a newer value.
         */
        post: operations["record_pre_production_measurement_api_v1_supply_chain_po_cases__case_id__pre_production_measurements_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/po-cases/{case_id}/purchase-order": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Purchase Order Proposal */
        get: operations["get_purchase_order_proposal_api_v1_supply_chain_po_cases__case_id__purchase_order_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/po-cases/{case_id}/purchase-order/approval": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Approve Purchase Order */
        post: operations["approve_purchase_order_api_v1_supply_chain_po_cases__case_id__purchase_order_approval_post"];
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
    "/api/v1/supply-chain/po-cases/{case_id}/step-proposal": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Po Step Proposal */
        get: operations["get_po_step_proposal_api_v1_supply_chain_po_cases__case_id__step_proposal_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/po-cases/{case_id}/step-proposal/approval": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Approve Po Step */
        post: operations["approve_po_step_api_v1_supply_chain_po_cases__case_id__step_proposal_approval_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/po-cases/{case_id}/supplier-messages": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Po Case Supplier Messages */
        get: operations["list_po_case_supplier_messages_api_v1_supply_chain_po_cases__case_id__supplier_messages_get"];
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
        /**
         * Get Case Transitions
         * @description The case's timeline, newest first, a page at a time.
         */
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
    "/api/v1/supply-chain/po-documents-policy": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Po Documents Policy */
        get: operations["get_po_documents_policy_api_v1_supply_chain_po_documents_policy_get"];
        /** Put Po Documents Policy */
        put: operations["put_po_documents_policy_api_v1_supply_chain_po_documents_policy_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/product-action-duties": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Product Action Duties
         * @description Which duty each product step belongs to for the caller's tenant.
         */
        get: operations["get_product_action_duties_api_v1_supply_chain_product_action_duties_get"];
        /**
         * Set Product Action Duties Override
         * @description Replaces the tenant's own product step-to-duty mapping, whole. No
         *     `Idempotency-Key`, as for the other policies' `PUT`.
         */
        put: operations["set_product_action_duties_override_api_v1_supply_chain_product_action_duties_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/product-cases": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Product Cases */
        get: operations["list_product_cases_api_v1_supply_chain_product_cases_get"];
        put?: never;
        /**
         * Propose Product Case
         * @description Step 1. The caller becomes the PIC.
         */
        post: operations["propose_product_case_api_v1_supply_chain_product_cases_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/product-cases/{case_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Product Case */
        get: operations["get_product_case_api_v1_supply_chain_product_cases__case_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/product-cases/{case_id}/documents": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Product Case Documents */
        get: operations["list_product_case_documents_api_v1_supply_chain_product_cases__case_id__documents_get"];
        put?: never;
        /** Upload Product Case Document */
        post: operations["upload_product_case_document_api_v1_supply_chain_product_cases__case_id__documents_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/product-cases/{case_id}/drafts": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Product Case Drafts */
        get: operations["list_product_case_drafts_api_v1_supply_chain_product_cases__case_id__drafts_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/product-cases/{case_id}/order": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Place Product Order
         * @description ĐẶT HÀNG: the case is ordered and its PO case opens awaiting its
         *     PO. A second click is a 409 and opens nothing (a replay under the
         *     same `Idempotency-Key` returns the first answer).
         */
        post: operations["place_product_order_api_v1_supply_chain_product_cases__case_id__order_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/product-cases/{case_id}/pic": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Reassign Product Case Pic
         * @description Hands the case to another PIC (TP Cung ứng's duty, ticket 06). A
         *     case ordered or cancelled refuses (409): its PIC lives on the PO case.
         */
        post: operations["reassign_product_case_pic_api_v1_supply_chain_product_cases__case_id__pic_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/product-cases/{case_id}/profile": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Product Profile */
        get: operations["get_product_profile_api_v1_supply_chain_product_cases__case_id__profile_get"];
        put?: never;
        /** Save Product Profile */
        post: operations["save_product_profile_api_v1_supply_chain_product_cases__case_id__profile_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/product-cases/{case_id}/sample-checklist": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Sample Checklist */
        get: operations["get_sample_checklist_api_v1_supply_chain_product_cases__case_id__sample_checklist_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/product-cases/{case_id}/sample-measurements": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Record Sample Measurement */
        post: operations["record_sample_measurement_api_v1_supply_chain_product_cases__case_id__sample_measurements_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/product-cases/{case_id}/step-proposal": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Step Proposal */
        get: operations["get_step_proposal_api_v1_supply_chain_product_cases__case_id__step_proposal_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/product-cases/{case_id}/step-proposal/decision": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Decide Step Proposal
         * @description A decision resumes the run that applies it, so a retry after a
         *     timeout must not decide twice: `Idempotency-Key` is honoured.
         */
        post: operations["decide_step_proposal_api_v1_supply_chain_product_cases__case_id__step_proposal_decision_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/product-cases/{case_id}/supplier-messages": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Product Case Supplier Messages */
        get: operations["list_product_case_supplier_messages_api_v1_supply_chain_product_cases__case_id__supplier_messages_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/product-cases/{case_id}/transitions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Product Case Transitions
         * @description The case's history, newest first, a page at a time.
         */
        get: operations["get_product_case_transitions_api_v1_supply_chain_product_cases__case_id__transitions_get"];
        put?: never;
        /** Create Product Case Transition */
        post: operations["create_product_case_transition_api_v1_supply_chain_product_cases__case_id__transitions_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/product-categories": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Product Categories
         * @description The caller's tenant's Category list, in its own order.
         */
        get: operations["list_product_categories_api_v1_supply_chain_product_categories_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/proposal-lists": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Proposal Lists */
        get: operations["list_proposal_lists_api_v1_supply_chain_proposal_lists_get"];
        put?: never;
        /** Upload Proposal List */
        post: operations["upload_proposal_list_api_v1_supply_chain_proposal_lists_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/proposal-lists/{list_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Proposal List */
        get: operations["get_proposal_list_api_v1_supply_chain_proposal_lists__list_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/proposal-lists/{list_id}/rows/{index}/dismissal": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Drop From List */
        post: operations["drop_from_list_api_v1_supply_chain_proposal_lists__list_id__rows__index__dismissal_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/proposal-lists/{list_id}/rows/{index}/proposal": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Propose From List */
        post: operations["propose_from_list_api_v1_supply_chain_proposal_lists__list_id__rows__index__proposal_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/sample-criteria-policy": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Sample Criteria Policy */
        get: operations["get_sample_criteria_policy_api_v1_supply_chain_sample_criteria_policy_get"];
        /** Set Sample Criteria Policy */
        put: operations["set_sample_criteria_policy_api_v1_supply_chain_sample_criteria_policy_put"];
        post?: never;
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
    "/api/v1/supply-chain/step-preparation-policy": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Step Preparation Policy */
        get: operations["get_step_preparation_policy_api_v1_supply_chain_step_preparation_policy_get"];
        /**
         * Set Step Preparation Policy
         * @description Replaces the tenant's own policy, whole.
         */
        put: operations["set_step_preparation_policy_api_v1_supply_chain_step_preparation_policy_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/supplier-messages/{message_id}/sent": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Mark Supplier Message Sent */
        post: operations["mark_supplier_message_sent_api_v1_supply_chain_supplier_messages__message_id__sent_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/suppliers": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Suppliers */
        get: operations["list_suppliers_api_v1_supply_chain_suppliers_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/suppliers/{supplier_id}/bank-account": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Supplier Bank Account */
        get: operations["get_supplier_bank_account_api_v1_supply_chain_suppliers__supplier_id__bank_account_get"];
        put?: never;
        /** Save Supplier Bank Account */
        post: operations["save_supplier_bank_account_api_v1_supply_chain_suppliers__supplier_id__bank_account_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/supply-chain/suppliers/{supplier_id}/contact": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Supplier Contact */
        get: operations["get_supplier_contact_api_v1_supply_chain_suppliers__supplier_id__contact_get"];
        put?: never;
        /** Save Supplier Contact */
        post: operations["save_supplier_contact_api_v1_supply_chain_suppliers__supplier_id__contact_post"];
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
            data_view: (components["schemas"]["CaseTableDataView"] | components["schemas"]["CaseLinkDataView"] | components["schemas"]["ProductCaseTableDataView"] | components["schemas"]["ProductCaseLinkDataView"]) | null;
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
        /**
         * AdvanceProductCaseRequest
         * @description One step. Each step reads the fields it takes and refuses the others:
         *     `supplier_name` for `request_sample`, `document_id` for `pass_sample`,
         *     `request_revision`, `complete_profile`, `confirm_with_supplier` and
         *     (optionally) `reject_sample`, `item_code` for `issue_item_code`, `sku` for
         *     `add_sku`, `sku_id` for `remove_sku`, `reason` where the step needs one.
         */
        AdvanceProductCaseRequest: {
            action: components["schemas"]["ProductAction"];
            /** Document Id */
            document_id?: string | null;
            /** Item Code */
            item_code?: string | null;
            /** Reason */
            reason?: string | null;
            sku?: components["schemas"]["SkuInput"] | null;
            /** Sku Id */
            sku_id?: string | null;
            /** Supplier Name */
            supplier_name?: string | null;
        };
        /** ApprovePOStepRequest */
        ApprovePOStepRequest: {
            /** Content Sha256 */
            content_sha256?: string | null;
            /** Counts */
            counts?: {
                [key: string]: number;
            };
            /** Draft Id */
            draft_id?: string | null;
            /** Results */
            results?: {
                [key: string]: string;
            };
            step: components["schemas"]["POStepKind"];
        };
        /** ApprovePurchaseOrderRequest */
        ApprovePurchaseOrderRequest: {
            /** Content Sha256 */
            content_sha256: string;
            /**
             * Draft Id
             * Format: uuid
             */
            draft_id: string;
            /** Po Reference */
            po_reference: string;
        };
        /** AttentionItemView */
        AttentionItemView: {
            case: components["schemas"]["POCaseView"];
            missing_update: components["schemas"]["MissingUpdateStatusView"] | null;
            sla: components["schemas"]["SLAEvaluationView"] | null;
        };
        /** Bm04Field */
        Bm04Field: {
            /** Key */
            key: string;
            kind: components["schemas"]["Bm04FieldKind"];
            /** Label */
            label: string;
            /** Max Length */
            max_length?: number | null;
            /** Options */
            options?: string[] | null;
            /**
             * Required
             * @default false
             */
            required: boolean;
            /** Unit */
            unit?: string | null;
        };
        /**
         * Bm04FieldKind
         * @enum {string}
         */
        Bm04FieldKind: "text" | "number" | "integer" | "boolean" | "choice";
        /** Body_apply_import_api_v1_supply_chain_imports_post */
        Body_apply_import_api_v1_supply_chain_imports_post: {
            /** File */
            file: string;
        };
        /** Body_dry_run_import_api_v1_supply_chain_imports_dry_run_post */
        Body_dry_run_import_api_v1_supply_chain_imports_dry_run_post: {
            /** File */
            file: string;
        };
        /** Body_set_doc_template_api_v1_supply_chain_doc_templates_put */
        Body_set_doc_template_api_v1_supply_chain_doc_templates_put: {
            /** Document */
            document: string;
            /** Spec */
            spec: string;
        };
        /** Body_upload_case_document_api_v1_supply_chain_po_cases__case_id__documents_post */
        Body_upload_case_document_api_v1_supply_chain_po_cases__case_id__documents_post: {
            doc_type: components["schemas"]["DocumentType"];
            /** File */
            file: string;
        };
        /** Body_upload_product_case_document_api_v1_supply_chain_product_cases__case_id__documents_post */
        Body_upload_product_case_document_api_v1_supply_chain_product_cases__case_id__documents_post: {
            doc_type: components["schemas"]["DocumentType"];
            /** File */
            file: string;
        };
        /** Body_upload_proposal_list_api_v1_supply_chain_proposal_lists_post */
        Body_upload_proposal_list_api_v1_supply_chain_proposal_lists_post: {
            /** File */
            file: string;
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
            /** Product Entries */
            product_entries: components["schemas"]["ProductBriefEntryView"][];
            product_state: components["schemas"]["ProductDevState"] | null;
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
        BriefSignal: "update_escalation_due" | "sla_breached" | "case_blocked" | "approval_pending" | "manual_review" | "supplier_reported_delay" | "update_reminder_due" | "waiting_external" | "rework" | "waiting_on_us" | "changed_recently" | "product_sla_breached" | "product_awaiting_bod" | "product_awaiting_signoff" | "sample_evaluated_today";
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
        CaseAction: "request_deposit" | "confirm_deposit" | "start_pre_production" | "start_production" | "send_to_qc" | "pass_qc" | "arrive_at_port" | "request_final_payment" | "confirm_payment" | "start_warehouse_receiving" | "complete" | "resume_from_rework" | "resume" | "fail_qc" | "wait_for_external" | "flag_blocked" | "flag_manual_review" | "cancel" | "create_po";
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
        /** CaseApprovalView */
        CaseApprovalView: {
            /** Action */
            action: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Requested At */
            requested_at: string | null;
        };
        /** CaseApprovalsView */
        CaseApprovalsView: {
            /** Items */
            items: components["schemas"]["CaseApprovalView"][];
            /** Total */
            total: number;
            /** Visible */
            visible: boolean;
        };
        /**
         * CaseDocumentView
         * @description A document as the API shows it. The object key stays on the server.
         */
        CaseDocumentView: {
            /**
             * Case Id
             * Format: uuid
             */
            case_id: string;
            case_kind: components["schemas"]["CaseKind"];
            /** Content Type */
            content_type: string;
            doc_type: components["schemas"]["DocumentType"];
            /** Filename */
            filename: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Sha256 */
            sha256: string;
            /** Size Bytes */
            size_bytes: number;
            /**
             * Uploaded At
             * Format: date-time
             */
            uploaded_at: string;
            /**
             * Uploaded By
             * Format: uuid
             */
            uploaded_by: string;
            /** Version */
            version: number;
        };
        /**
         * CaseDuty
         * @enum {string}
         */
        CaseDuty: "ordering" | "finance" | "qc" | "logistics" | "warehouse" | "exceptions" | "rnd" | "supply_lead" | "mkt";
        /**
         * CaseKind
         * @description Which kind of case a document belongs to; the key's fourth segment, and
         *     which of `case_documents`' two case columns holds the case id.
         * @enum {string}
         */
        CaseKind: "po" | "product";
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
        CaseQueryKind: "list_cases" | "open_case" | "list_product_cases" | "open_product_case" | "unsupported";
        /**
         * CaseQueryOutcome
         * @description What the reply to one question turned out to be. The first two run a
         *     lookup; every other one is a refusal that says why, never a widened or
         *     guessed answer.
         * @enum {string}
         */
        CaseQueryOutcome: "list" | "open" | "not_understood" | "supplier_not_found" | "supplier_ambiguous" | "po_reference_missing" | "po_not_found" | "po_ambiguous" | "product_list" | "product_open" | "proposal_code_missing" | "category_not_found" | "category_ambiguous" | "pic_not_found" | "pic_ambiguous" | "product_not_found" | "product_ambiguous";
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
         *
         *     `ORDER_REQUESTED` heads it: ĐẶT HÀNG on a product case (step 9) opened
         *     this case and its PO does not exist yet; step 10's `create_po` gives it
         *     its reference (ADR 0017). Labelled "Chờ tạo PO".
         * @enum {string}
         */
        CaseState: "order_requested" | "po_created" | "waiting_deposit" | "deposit_confirmed" | "pre_production" | "production" | "qc" | "in_transit" | "arrived_port" | "waiting_payment" | "payment_completed" | "warehouse_receiving" | "completed" | "waiting_external" | "blocked" | "rework" | "manual_review" | "cancelled";
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
            from_state: components["schemas"]["CaseState"] | null;
            /**
             * Occurred At
             * Format: date-time
             */
            occurred_at: string;
            /** Reason */
            reason: string | null;
            to_state: components["schemas"]["CaseState"];
        };
        /** ChecklistRowView */
        ChecklistRowView: {
            /** Key */
            key: string;
            kind: components["schemas"]["CriterionKind"];
            /** Label */
            label: string;
            /** Max */
            max: string | null;
            /** Min */
            min: string | null;
            /** Note */
            note: string | null;
            /** Standard */
            standard: string;
            /** Unit */
            unit: string | null;
            /** Value */
            value: string | null;
            verdict: components["schemas"]["Verdict"];
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
        /** CitedValueView */
        CitedValueView: {
            /** Quote */
            quote: string;
            /** Value */
            value: string;
        };
        /** CloseFollowUpRequest */
        CloseFollowUpRequest: {
            /** Note */
            note?: string | null;
        };
        /**
         * CreatePOCaseRequest
         * @description A case opened without stage 1 (a reorder, ADR 0017). No PIC field: the
         *     caller is the PIC, and an unknown field is a 422. `category`, optional, is
         *     a key of the tenant's Category list (`GET /product-categories`); a key not
         *     in it is refused.
         */
        CreatePOCaseRequest: {
            /** Category */
            category?: string | null;
            order_kind: components["schemas"]["OrderKind"];
            /** Po Reference */
            po_reference: string;
            /** Supplier Name */
            supplier_name: string;
        };
        /**
         * CreatePORequest
         * @description Step 10: the PO's reference and kind, and the quantity of any line
         *     still open or to correct.
         */
        CreatePORequest: {
            /** Lines */
            lines?: components["schemas"]["POLineQuantity"][];
            order_kind: components["schemas"]["OrderKind"];
            /** Po Reference */
            po_reference: string;
        };
        /**
         * CriterionKind
         * @enum {string}
         */
        CriterionKind: "number" | "check";
        /** DailyBriefSummaryView */
        DailyBriefSummaryView: {
            brief: components["schemas"]["DailyBriefView"];
            summary: components["schemas"]["BriefSummaryView"];
        };
        /** DailyBriefView */
        DailyBriefView: {
            /** Active Case Count */
            active_case_count: number;
            /** Active Product Case Count */
            active_product_case_count: number;
            /** Approvals Visible */
            approvals_visible: boolean;
            /** Entries Shown */
            entries_shown: number;
            /** Flagged Case Count */
            flagged_case_count: number;
            /** Flagged Product Case Count */
            flagged_product_case_count: number;
            /**
             * Generated At
             * Format: date-time
             */
            generated_at: string;
            /** Groups */
            groups: components["schemas"]["BriefGroupView"][];
            /** Product Cases Visible */
            product_cases_visible: boolean;
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
        /** DocTemplateView */
        DocTemplateView: {
            /** Doc Type */
            doc_type: string;
            /** Overridden */
            overridden: boolean;
            /** Template Id */
            template_id: string;
            /** Title */
            title: string;
            /** Version */
            version: string;
        };
        /**
         * DocumentType
         * @description ADR 0021's fourteen document types, from process.md section 2, the
         *     three papers of step 12's sub-flow (slice PK), the supplier's quotation
         *     (ai-automation/02), the tờ trình (ai-automation/03) and the payment papers
         *     of steps 11 and 16 (ai-automation/15).
         * @enum {string}
         */
        DocumentType: "proposal_list" | "product_image" | "sample_photo" | "sample_evaluation" | "sample_revision_request" | "product_profile_bm04" | "official_item_code" | "supplier_confirmation_email" | "purchase_order" | "deposit_docs" | "payment_docs" | "packaging_content" | "user_manual" | "maquette" | "colour_sample" | "packaging_design" | "pre_production_test_report" | "supplier_quotation" | "bod_submission" | "proforma_invoice" | "commercial_invoice" | "bank_transfer_receipt" | "colour_revision_request" | "design_revision_request" | "production_schedule" | "qc_report" | "packing_list" | "bill_of_lading" | "arrival_notice" | "certificate_of_origin" | "rework_request" | "warehouse_receipt" | "discrepancy_report";
        /** DraftColumnView */
        DraftColumnView: {
            kind: components["schemas"]["TemplateFieldKind"];
            /** Label */
            label: string;
            /** Name */
            name: string;
        };
        /**
         * DraftFieldSourceView
         * @description Where a value came from: a document and the quote read in it, the
         *     person who typed it, or words a model wrote that checked out against the
         *     evidence they cite (`ai_written`, `cites`). Null: computed by code.
         */
        DraftFieldSourceView: {
            /** Ai Written */
            ai_written: boolean;
            /** Cites */
            cites: string[];
            /** Document Id */
            document_id: string | null;
            /** Edited By */
            edited_by: string | null;
            /** Quote */
            quote: string | null;
        };
        /** DraftFieldView */
        DraftFieldView: {
            /** Columns */
            columns: components["schemas"]["DraftColumnView"][] | null;
            /** Gap */
            gap: boolean;
            kind: components["schemas"]["TemplateFieldKind"];
            /** Label */
            label: string;
            /** Name */
            name: string;
            /** Redacted */
            redacted: boolean;
            /** Redacted Columns */
            redacted_columns: string[];
            /** Required */
            required: boolean;
            /** Rows */
            rows: {
                [key: string]: string | null;
            }[] | null;
            source: components["schemas"]["DraftFieldSourceView"] | null;
            /** Value */
            value: string | null;
        };
        /**
         * DraftRecipe
         * @description How a draft's fields are filled. `case_facts`: the case's own fields
         *     (code, name, Category, supplier, round) and the cited readings of the
         *     step's source documents whose field has the template field's name; a
         *     result field a person types is never filled.
         * @enum {string}
         */
        DraftRecipe: "case_facts" | "sample_evaluation" | "revision_request" | "bm04" | "item_coding";
        /** DraftSourceView */
        DraftSourceView: {
            /**
             * Document Id
             * Format: uuid
             */
            document_id: string;
            /** Extraction Id */
            extraction_id: string | null;
            /** Sha256 */
            sha256: string;
        };
        /**
         * DraftStatus
         * @description What a version is, read from its decision and its place in the lineage.
         * @enum {string}
         */
        DraftStatus: "open" | "confirmed" | "rejected" | "superseded";
        /** DraftView */
        DraftView: {
            /** Can Edit */
            can_edit: boolean;
            /**
             * Case Id
             * Format: uuid
             */
            case_id: string;
            case_kind: components["schemas"]["CaseKind"];
            /** Content Sha256 */
            content_sha256: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Created By
             * Format: uuid
             */
            created_by: string;
            /** Decision Reason */
            decision_reason: string | null;
            doc_type: components["schemas"]["DocumentType"];
            /** Fields */
            fields: components["schemas"]["DraftFieldView"][];
            /** Gaps */
            gaps: string[];
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Lineage Id
             * Format: uuid
             */
            lineage_id: string;
            /** Prices Visible */
            prices_visible: boolean;
            /** Prompt Id */
            prompt_id: string | null;
            /** Prompt Version */
            prompt_version: string | null;
            /** Sources */
            sources: components["schemas"]["DraftSourceView"][];
            status: components["schemas"]["DraftStatus"];
            /** Template Id */
            template_id: string;
            /** Template Version */
            template_version: string;
            /** Title */
            title: string;
            /** Version */
            version: number;
        };
        /** DropRowRequest */
        DropRowRequest: {
            /** Reason */
            reason?: string | null;
        };
        /**
         * ExtractionStatus
         * @enum {string}
         */
        ExtractionStatus: "extracted" | "unreadable" | "refused" | "failed";
        /** FindingView */
        FindingView: {
            /** Code */
            code: string;
            /** Message */
            message: string;
            /** Subject */
            subject: string;
        };
        /**
         * FollowUpItemView
         * @description An open follow-up. `mine`: it was handed to the caller (a stamped scope
         *     they hold, or they are its stamped PIC), so the caller is expected to act,
         *     and may close it. `case_kind` says which page `case_id` opens; `reference`
         *     is the PO reference (null while the case awaits its PO) or the product
         *     case's proposal code.
         */
        FollowUpItemView: {
            /**
             * Case Id
             * Format: uuid
             */
            case_id: string;
            case_kind: components["schemas"]["CaseKind"];
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
            /** Reference */
            reference: string | null;
            /** Supplier Name */
            supplier_name: string | null;
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
        GroundedField: "supplier" | "po_reference" | "state" | "active_only" | "proposal_code" | "product_state" | "category" | "pic" | "mine";
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
        /** ImportProblemView */
        ImportProblemView: {
            /** Message */
            message: string;
            sheet: components["schemas"]["ImportSheet"];
        };
        /** ImportReportView */
        ImportReportView: {
            /** Dry Run */
            dry_run: boolean;
            /** Problems */
            problems: components["schemas"]["ImportProblemView"][];
            /** Rows */
            rows: components["schemas"]["ImportRowView"][];
            /** Sheets */
            sheets: components["schemas"]["ImportSheetCountView"][];
        };
        /** ImportRowView */
        ImportRowView: {
            /** Key */
            key: string;
            /** Messages */
            messages: string[];
            /** Row */
            row: number;
            sheet: components["schemas"]["ImportSheet"];
            status: components["schemas"]["RowStatus"];
        };
        /**
         * ImportSheet
         * @enum {string}
         */
        ImportSheet: "suppliers" | "catalogue" | "users" | "product_cases" | "po_cases";
        /** ImportSheetCountView */
        ImportSheetCountView: {
            /** Created */
            created: number;
            /** Exists */
            exists: number;
            /** Partial */
            partial: number;
            /** Rejected */
            rejected: number;
            sheet: components["schemas"]["ImportSheet"];
            /** Title */
            title: string;
        };
        /**
         * Incoterm
         * @description Incoterms 2020: who carries cost and risk to where.
         * @enum {string}
         */
        Incoterm: "EXW" | "FCA" | "CPT" | "CIP" | "DAP" | "DPU" | "DDP" | "FAS" | "FOB" | "CFR" | "CIF";
        /**
         * ItemCodeRule
         * @description How a tenant's codes are made: `EL-00042`, then `EL-00042-01` for its
         *     first SKU.
         */
        ItemCodeRule: {
            /** Digits */
            digits: number;
            /** Prefix */
            prefix: string;
            /**
             * Separator
             * @default -
             * @enum {string}
             */
            separator: "-" | "." | "/" | "";
            /** Sku Digits */
            sku_digits: number;
            /**
             * Sku Separator
             * @default -
             * @enum {string}
             */
            sku_separator: "-" | "." | "/" | "";
        };
        /**
         * ItemCodeView
         * @description The case's official item code (step 9).
         */
        ItemCodeView: {
            /** Code */
            code: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
        };
        /** LinePriceRequest */
        LinePriceRequest: {
            /**
             * Sku Id
             * Format: uuid
             */
            sku_id: string;
            /** Unit Price */
            unit_price?: number | string | null;
        };
        /** MarkSentRequest */
        MarkSentRequest: {
            /** Content Sha256 */
            content_sha256: string;
        };
        /** MeasurementRequest */
        MeasurementRequest: {
            /** Criterion */
            criterion: string;
            /** Note */
            note?: string | null;
            /** Value */
            value: string;
        };
        /**
         * MessageCitationView
         * @description One paragraph AI wrote and code kept, with the evidence it cites.
         */
        MessageCitationView: {
            /** Cites */
            cites: string[];
            /** Text */
            text: string;
        };
        /**
         * MessagePurpose
         * @enum {string}
         */
        MessagePurpose: "sample_request" | "supplier_reminder" | "supplier_confirmation" | "sample_revision_request" | "production_progress" | "discrepancy_claim";
        /**
         * MessageStatus
         * @enum {string}
         */
        MessageStatus: "drafted" | "refused" | "failed";
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
        /**
         * OrderKind
         * @description Step 10's classification: Hàng mới (a product out of stage 1) or Hàng
         *     đặt lại (a reorder, opened by `CreatePOCase` without stage 1).
         * @enum {string}
         */
        OrderKind: "new" | "reorder";
        /**
         * OrderPlacedView
         * @description ĐẶT HÀNG done: the case, now ordered, and the PO case it opened.
         */
        OrderPlacedView: {
            /** Category */
            category: string;
            /** Created At */
            created_at: string | null;
            /**
             * Created By
             * Format: uuid
             */
            created_by: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            interrupted_state: components["schemas"]["ProductDevState"] | null;
            /**
             * Pic User Id
             * Format: uuid
             */
            pic_user_id: string;
            /**
             * Po Case Id
             * Format: uuid
             */
            po_case_id: string;
            /** Product Name */
            product_name: string;
            /** Proposal Code */
            proposal_code: string;
            /** Sample Round */
            sample_round: number;
            /** Signoff Round */
            signoff_round: number;
            state: components["schemas"]["ProductDevState"];
            /** Supplier Name */
            supplier_name: string | null;
            /** Version */
            version: number;
        };
        /** POCaseDetailView */
        POCaseDetailView: {
            /** Category */
            category: string | null;
            /** Container Number */
            container_number: string | null;
            /** Created At */
            created_at: string | null;
            /** Eta */
            eta: string | null;
            /** Etd */
            etd: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            interrupted_state: components["schemas"]["CaseState"] | null;
            /** Lines */
            lines: components["schemas"]["POCaseLineView"][];
            order_kind: components["schemas"]["OrderKind"];
            /** Pic User Id */
            pic_user_id: string | null;
            /** Po Reference */
            po_reference: string | null;
            /** Product Dev Case Id */
            product_dev_case_id: string | null;
            state: components["schemas"]["CaseState"];
            /** Supplier Name */
            supplier_name: string;
            /** Version */
            version: number;
        };
        /**
         * POCaseLineView
         * @description A planned line: a SKU of the product and how many (null until step
         *     10 sets it, when the SKU's planned quantity was open).
         */
        POCaseLineView: {
            /** Quantity */
            quantity: number | null;
            /** Sku Code */
            sku_code: string | null;
            /**
             * Sku Id
             * Format: uuid
             */
            sku_id: string;
            /** Variant Label */
            variant_label: string | null;
        };
        /** POCaseView */
        POCaseView: {
            /** Category */
            category: string | null;
            /** Created At */
            created_at: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            interrupted_state: components["schemas"]["CaseState"] | null;
            order_kind: components["schemas"]["OrderKind"];
            /** Pic User Id */
            pic_user_id: string | null;
            /** Po Reference */
            po_reference: string | null;
            /** Product Dev Case Id */
            product_dev_case_id: string | null;
            state: components["schemas"]["CaseState"];
            /** Supplier Name */
            supplier_name: string;
            /** Version */
            version: number;
        };
        /** POCommercialView */
        POCommercialView: {
            /** Can Edit */
            can_edit: boolean;
            /** Currency */
            currency: string | null;
            deposit_percent: components["schemas"]["RedactableAmount"];
            /** Expected Delivery Date */
            expected_delivery_date: string | null;
            incoterm: components["schemas"]["Incoterm"] | null;
            /** Lines */
            lines: components["schemas"]["PricedLineView"][];
            order_total: components["schemas"]["RedactableAmount"];
            /** Payment Terms */
            payment_terms: string | null;
            /** Payment Terms Redacted */
            payment_terms_redacted: boolean;
            /** Payments */
            payments: components["schemas"]["POPaymentView"][];
            /**
             * Po Case Id
             * Format: uuid
             */
            po_case_id: string;
            /** Prices Visible */
            prices_visible: boolean;
        };
        /** POLineQuantity */
        POLineQuantity: {
            /** Quantity */
            quantity: number;
            /**
             * Sku Id
             * Format: uuid
             */
            sku_id: string;
        };
        /** POPaymentView */
        POPaymentView: {
            amount: components["schemas"]["RedactableAmount"];
            /** Currency */
            currency: string;
            /** Document Id */
            document_id: string | null;
            /** Due Date */
            due_date: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            kind: components["schemas"]["PaymentKind"];
            /** Paid On */
            paid_on: string | null;
            /**
             * Recorded At
             * Format: date-time
             */
            recorded_at: string;
            /**
             * Recorded By
             * Format: uuid
             */
            recorded_by: string;
            /** Version */
            version: number;
        };
        /**
         * POStepCountLineView
         * @description A PO line the warehouse counts (step 17): the count is typed, never
         *     filled; what the packing list says was shipped sits beside it.
         */
        POStepCountLineView: {
            /** Document Id */
            document_id: string | null;
            /** Ordered */
            ordered: number | null;
            /** Quote */
            quote: string | null;
            /** Shipped */
            shipped: number | null;
            /** Sku Code */
            sku_code: string | null;
            /**
             * Sku Id
             * Format: uuid
             */
            sku_id: string;
            /** Variant Label */
            variant_label: string | null;
        };
        /** POStepFindingView */
        POStepFindingView: {
            /** Code */
            code: string;
            /** Message */
            message: string;
            /** Subject */
            subject: string;
        };
        /**
         * POStepKind
         * @enum {string}
         */
        POStepKind: "deposit_request" | "deposit_payment" | "final_payment_request" | "final_payment" | "production" | "qc" | "arrival" | "warehouse";
        /** POStepProposalView */
        POStepProposalView: {
            action: components["schemas"]["CaseAction"] | null;
            /** Blocked Reason */
            blocked_reason: string | null;
            /** Can Approve */
            can_approve: boolean;
            /** Content Sha256 */
            content_sha256: string | null;
            draft_doc_type: components["schemas"]["DocumentType"] | null;
            /** Draft Id */
            draft_id: string | null;
            draft_status: components["schemas"]["DraftStatus"] | null;
            /** Draft Version */
            draft_version: number | null;
            /** Findings */
            findings: components["schemas"]["POStepFindingView"][];
            /** Lines */
            lines: components["schemas"]["POStepCountLineView"][];
            missing_paper: components["schemas"]["DocumentType"] | null;
            /** Proposed */
            proposed: boolean;
            /** Results */
            results: components["schemas"]["POStepResultView"][];
            step: components["schemas"]["POStepKind"] | null;
        };
        /** POStepResultView */
        POStepResultView: {
            kind: components["schemas"]["ResultKind"];
            /** Label */
            label: string;
            /** Name */
            name: string;
            /** Options */
            options: string[];
            /** Redacted */
            redacted: boolean;
            /** Required */
            required: boolean;
            /** Required For */
            required_for: string | null;
            suggestion: components["schemas"]["POStepSuggestionView"] | null;
        };
        /** POStepSuggestionView */
        POStepSuggestionView: {
            /** Document Id */
            document_id: string | null;
            /** Quote */
            quote: string | null;
            /** Value */
            value: string | null;
        };
        /** PackItemView */
        PackItemView: {
            doc_type: components["schemas"]["DocumentType"];
            /** Document Id */
            document_id: string | null;
        };
        /**
         * PackagingAction
         * @enum {string}
         */
        PackagingAction: "approve_colour" | "request_colour_revision" | "approve_design" | "request_design_revision" | "receive_pre_production_sample" | "pass_pre_production_test" | "fail_pre_production_test" | "send_mkt_pack" | "submit_packaging_content";
        /** PackagingDesignView */
        PackagingDesignView: {
            case_state: components["schemas"]["CaseState"];
            colour_status: components["schemas"]["ReviewStatus"];
            design_status: components["schemas"]["ReviewStatus"];
            /** History */
            history: components["schemas"]["PackagingEventView"][];
            /** Mkt Pack Sent At */
            mkt_pack_sent_at: string | null;
            /** Pack */
            pack: components["schemas"]["PackItemView"][];
            /** Packaging Content Submitted At */
            packaging_content_submitted_at: string | null;
            /**
             * Po Case Id
             * Format: uuid
             */
            po_case_id: string;
            /** Pre Production Sample Received At */
            pre_production_sample_received_at: string | null;
            pre_production_test: components["schemas"]["PreProductionTest"];
            /** Require Packaging Content */
            require_packaging_content: boolean;
            /** Require Pre Production Test */
            require_pre_production_test: boolean;
            /** Steps */
            steps: components["schemas"]["PackagingStepOptionView"][];
            /** Version */
            version: number;
        };
        /** PackagingEventView */
        PackagingEventView: {
            action: components["schemas"]["PackagingAction"];
            /**
             * Actor Id
             * Format: uuid
             */
            actor_id: string;
            /** Document Id */
            document_id: string | null;
            /** Note */
            note: string | null;
            /**
             * Occurred At
             * Format: date-time
             */
            occurred_at: string;
            /** Reason */
            reason: string | null;
        };
        /** PackagingFindingView */
        PackagingFindingView: {
            /** Code */
            code: string;
            /** Message */
            message: string;
            /** Subject */
            subject: string;
        };
        /** PackagingProofView */
        PackagingProofView: {
            /** Document Id */
            document_id: string | null;
            /** Findings */
            findings: components["schemas"]["PackagingFindingView"][];
            status: components["schemas"]["ExtractionStatus"] | null;
        };
        /** PackagingStateView */
        PackagingStateView: {
            colour_status: components["schemas"]["ReviewStatus"];
            design_status: components["schemas"]["ReviewStatus"];
            /** Mkt Pack Sent At */
            mkt_pack_sent_at: string | null;
            /** Packaging Content Submitted At */
            packaging_content_submitted_at: string | null;
            /**
             * Po Case Id
             * Format: uuid
             */
            po_case_id: string;
            /** Pre Production Sample Received At */
            pre_production_sample_received_at: string | null;
            pre_production_test: components["schemas"]["PreProductionTest"];
            /** Version */
            version: number;
        };
        /** PackagingStepOptionView */
        PackagingStepOptionView: {
            action: components["schemas"]["PackagingAction"];
            /** Allowed */
            allowed: boolean;
            duty: components["schemas"]["CaseDuty"];
            /** Requires Document */
            requires_document: boolean;
            /** Requires Reason */
            requires_reason: boolean;
        };
        /**
         * Page
         * @description A page of results and the cursor that continues it.
         *
         *     ``next_cursor`` is ``None`` on the last page, and that — not an empty
         *     ``items`` — is the client's stop condition: a filtered listing can return an
         *     empty page in the middle of a run and still have more rows behind it.
         */
        Page_CaseTransitionView_: {
            /** Items */
            items: components["schemas"]["CaseTransitionView"][];
            /** Next Cursor */
            next_cursor: string | null;
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
        /**
         * Page
         * @description A page of results and the cursor that continues it.
         *
         *     ``next_cursor`` is ``None`` on the last page, and that — not an empty
         *     ``items`` — is the client's stop condition: a filtered listing can return an
         *     empty page in the middle of a run and still have more rows behind it.
         */
        Page_ProductCaseTransitionView_: {
            /** Items */
            items: components["schemas"]["ProductCaseTransitionView"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /**
         * Page
         * @description A page of results and the cursor that continues it.
         *
         *     ``next_cursor`` is ``None`` on the last page, and that — not an empty
         *     ``items`` — is the client's stop condition: a filtered listing can return an
         *     empty page in the middle of a run and still have more rows behind it.
         */
        Page_ProductCaseView_: {
            /** Items */
            items: components["schemas"]["ProductCaseView"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /**
         * PaymentKind
         * @description Step 11's deposit and step 16's final payment.
         * @enum {string}
         */
        PaymentKind: "deposit" | "final";
        /**
         * PendingReviewView
         * @description The approval a waiting case is held on (BGĐ's review, or the current
         *     sign-off step): which approval, since when, and the scope stamped on it,
         *     which is who may decide it. For a sign-off, the step it is, its number,
         *     and every step of the round in order, all as stamped when the case was
         *     submitted. Decided at `/approvals`, never from the case.
         */
        PendingReviewView: {
            /**
             * Approval Id
             * Format: uuid
             */
            approval_id: string;
            /** Created At */
            created_at: string | null;
            /** Required Scope */
            required_scope: string | null;
            /** Step */
            step: string | null;
            /** Step Label */
            step_label: string | null;
            /** Step No */
            step_no: number | null;
            /** Steps */
            steps: components["schemas"]["SignoffStepView"][];
        };
        /**
         * PlaceOrderRequest
         * @description ĐẶT HÀNG takes nothing: the PO case takes the PIC, Category, supplier
         *     and SKUs from the case. A body may be sent empty or not at all; any field
         *     in it (a PIC, a Category, a supplier) is a 422, never silently dropped.
         */
        PlaceOrderRequest: Record<string, never>;
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
        /** PreProductionChecklistView */
        PreProductionChecklistView: {
            /** Attempt */
            attempt: number;
            /** Can Record */
            can_record: boolean;
            /**
             * Case Id
             * Format: uuid
             */
            case_id: string;
            /** Open */
            open: boolean;
            /** Rows */
            rows: components["schemas"]["ChecklistRowView"][];
            suggestion: components["schemas"]["PackagingAction"] | null;
        };
        /**
         * PreProductionTest
         * @enum {string}
         */
        PreProductionTest: "pending" | "passed" | "failed";
        /** PreparedDraft */
        PreparedDraft: {
            doc_type: components["schemas"]["DocumentType"];
            recipe: components["schemas"]["DraftRecipe"];
        };
        /** PreparedStep */
        PreparedStep: {
            action: components["schemas"]["ProductAction"];
            case_kind: components["schemas"]["CaseKind"];
            /**
             * Checks
             * @default []
             */
            checks: components["schemas"]["StepCheck"][];
            /**
             * Drafts
             * @default []
             */
            drafts: components["schemas"]["PreparedDraft"][];
            /** Outcome Field */
            outcome_field?: string | null;
            /** Outcomes */
            outcomes?: {
                [key: string]: components["schemas"]["StepOutcome"];
            };
            /**
             * Physical
             * @default false
             */
            physical: boolean;
            /**
             * Result Fields
             * @default []
             */
            result_fields: string[];
            /**
             * Sources
             * @default []
             */
            sources: components["schemas"]["DocumentType"][];
            state: components["schemas"]["ProductDevState"];
        };
        /** PricedLineView */
        PricedLineView: {
            line_total: components["schemas"]["RedactableAmount"];
            /** Quantity */
            quantity: number | null;
            /** Sku Code */
            sku_code: string | null;
            /**
             * Sku Id
             * Format: uuid
             */
            sku_id: string;
            unit_price: components["schemas"]["RedactableAmount"];
            /** Variant Label */
            variant_label: string | null;
        };
        /**
         * ProductAction
         * @description Every step a person takes on a product-development case.
         *
         *     Its own enum, never `CaseAction`: five names are shared (`cancel`,
         *     `resume`, `wait_for_external`, `flag_blocked`, `flag_manual_review`) and a
         *     PO policy or approval keyed by one of them must not reach this case.
         * @enum {string}
         */
        ProductAction: "propose" | "request_sample" | "receive_sample" | "pass_sample" | "request_revision" | "receive_revised_sample" | "reject_sample" | "complete_profile" | "confirm_with_supplier" | "issue_item_code" | "add_sku" | "remove_sku" | "submit_for_signoff" | "place_order" | "wait_for_external" | "flag_blocked" | "flag_manual_review" | "resume" | "cancel" | "bod_approve" | "bod_reject" | "signoff_approve" | "signoff_reject" | "import";
        /**
         * ProductActionOptionView
         * @description A step the case accepts from its state, what it must carry, and the
         *     scope its duty needs under the tenant's policy. The page draws its button
         *     and form from this; the server checks all of it again on the step.
         *     `documents_since` is the earliest upload the step takes as its paper
         *     (when the round opened, or when the case reached the step); null when it
         *     takes none or the bound is not known, and then no paper qualifies.
         */
        ProductActionOptionView: {
            action: components["schemas"]["ProductAction"];
            /** Document Required */
            document_required: boolean;
            document_type: components["schemas"]["DocumentType"] | null;
            /** Documents Since */
            documents_since: string | null;
            /** Reason Required */
            reason_required: boolean;
            /** Required Scope */
            required_scope: string;
            /** Takes Supplier */
            takes_supplier: boolean;
            /** Unmet */
            unmet: string[];
        };
        /** ProductBriefEntryView */
        ProductBriefEntryView: {
            case: components["schemas"]["ProductCaseRefView"];
            /** Days */
            days: number | null;
            /** Limit Days */
            limit_days: number | null;
            /** Round No */
            round_no: number | null;
            sample_result: components["schemas"]["SampleResult"] | null;
        };
        /** ProductCaseDetailView */
        ProductCaseDetailView: {
            /** Actions */
            actions: components["schemas"]["ProductActionOptionView"][];
            /** Category */
            category: string;
            /** Created At */
            created_at: string | null;
            /**
             * Created By
             * Format: uuid
             */
            created_by: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            interrupted_state: components["schemas"]["ProductDevState"] | null;
            item_code: components["schemas"]["ItemCodeView"] | null;
            pending_review: components["schemas"]["PendingReviewView"] | null;
            /**
             * Pic User Id
             * Format: uuid
             */
            pic_user_id: string;
            /** Po Case Id */
            po_case_id: string | null;
            /** Product Name */
            product_name: string;
            /** Proposal Code */
            proposal_code: string;
            /** Rounds */
            rounds: components["schemas"]["SampleRoundView"][];
            /** Sample Round */
            sample_round: number;
            /** Signoff Round */
            signoff_round: number;
            /** Skus */
            skus: components["schemas"]["SkuView"][];
            sla: components["schemas"]["SLAEvaluationView"];
            state: components["schemas"]["ProductDevState"];
            /** Supplier Name */
            supplier_name: string | null;
            /** Version */
            version: number;
        };
        /** ProductCaseLinkDataView */
        ProductCaseLinkDataView: {
            case: components["schemas"]["ProductCaseRefView"];
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "product_case_link";
        };
        /**
         * ProductCaseRefView
         * @description A product-development case where it is named in passing (a brief
         *     group, a command-bar answer): what identifies it and where it stands.
         *     The case page's own view (`product_case_routes.ProductCaseView`) carries
         *     the rest.
         */
        ProductCaseRefView: {
            /** Category */
            category: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Pic User Id
             * Format: uuid
             */
            pic_user_id: string;
            /** Product Name */
            product_name: string;
            /** Proposal Code */
            proposal_code: string;
            state: components["schemas"]["ProductDevState"];
        };
        /**
         * ProductCaseStepView
         * @description A step taken. `review` is set only for a step that left the case
         *     waiting on an approval (BGĐ's review, the sign-off): `not_raised` means
         *     the step is recorded and the approval is not raised yet (the worker
         *     retries it).
         */
        ProductCaseStepView: {
            /** Category */
            category: string;
            /** Created At */
            created_at: string | null;
            /**
             * Created By
             * Format: uuid
             */
            created_by: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            interrupted_state: components["schemas"]["ProductDevState"] | null;
            /**
             * Pic User Id
             * Format: uuid
             */
            pic_user_id: string;
            /** Product Name */
            product_name: string;
            /** Proposal Code */
            proposal_code: string;
            review: components["schemas"]["ReviewRaise"] | null;
            /** Sample Round */
            sample_round: number;
            /** Signoff Round */
            signoff_round: number;
            state: components["schemas"]["ProductDevState"];
            /** Supplier Name */
            supplier_name: string | null;
            /** Version */
            version: number;
        };
        /** ProductCaseTableDataView */
        ProductCaseTableDataView: {
            /** Has More */
            has_more: boolean;
            /** Rows */
            rows: components["schemas"]["ProductCaseRefView"][];
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "product_case_table";
        };
        /**
         * ProductCaseTransitionView
         * @description One history row. `document_id` is the paper of a step outside the
         *     sample rounds (BM04, the supplier's email); a round's is on the round.
         */
        ProductCaseTransitionView: {
            action: components["schemas"]["ProductAction"];
            /**
             * Actor Id
             * Format: uuid
             */
            actor_id: string;
            /** Document Id */
            document_id: string | null;
            from_state: components["schemas"]["ProductDevState"] | null;
            /**
             * Occurred At
             * Format: date-time
             */
            occurred_at: string;
            /** Reason */
            reason: string | null;
            to_state: components["schemas"]["ProductDevState"];
        };
        /** ProductCaseView */
        ProductCaseView: {
            /** Category */
            category: string;
            /** Created At */
            created_at: string | null;
            /**
             * Created By
             * Format: uuid
             */
            created_by: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            interrupted_state: components["schemas"]["ProductDevState"] | null;
            /**
             * Pic User Id
             * Format: uuid
             */
            pic_user_id: string;
            /** Product Name */
            product_name: string;
            /** Proposal Code */
            proposal_code: string;
            /** Sample Round */
            sample_round: number;
            /** Signoff Round */
            signoff_round: number;
            state: components["schemas"]["ProductDevState"];
            /** Supplier Name */
            supplier_name: string | null;
            /** Version */
            version: number;
        };
        /**
         * ProductCategory
         * @description One Category of the tenant's list. `key` is what a case is stamped
         *     with and what `by_category` is keyed by; `label` is what a person reads
         *     and may be renamed without touching a case.
         */
        ProductCategory: {
            /** Key */
            key: string;
            /** Label */
            label: string;
        };
        /**
         * ProductCategoryView
         * @description One Category of the tenant's list: the key a case is stamped with, the
         *     label a person reads.
         */
        ProductCategoryView: {
            /** Key */
            key: string;
            /** Label */
            label: string;
        };
        /**
         * ProductDevState
         * @description The states this slice reaches. Labels live in `CONTEXT.md` and, for the
         *     web, in one table beside the product-case pages.
         * @enum {string}
         */
        ProductDevState: "proposed" | "sample_requested" | "sample_testing" | "revision_requested" | "pending_bod_review" | "profile_in_progress" | "supplier_confirmation" | "item_coding" | "pending_signoff" | "ready_to_order" | "ordered" | "waiting_external" | "blocked" | "manual_review" | "cancelled";
        /** ProductProfileView */
        ProductProfileView: {
            /** Attributes */
            attributes: {
                [key: string]: unknown;
            };
            bm04_schema: components["schemas"]["SupplyChainBm04Schema"];
            /** Can Edit */
            can_edit: boolean;
            /** Can Edit Prices */
            can_edit_prices: boolean;
            /** Created At */
            created_at: string | null;
            /** Created By */
            created_by: string | null;
            /** Currency */
            currency: string | null;
            incoterm: components["schemas"]["Incoterm"] | null;
            /** Lead Time Days */
            lead_time_days: number | null;
            /** Moq */
            moq: number | null;
            /** Prices Visible */
            prices_visible: boolean;
            /**
             * Product Dev Case Id
             * Format: uuid
             */
            product_dev_case_id: string;
            /** Schema Version */
            schema_version: string | null;
            unit_price: components["schemas"]["RedactableAmount"];
            /** Version */
            version: number | null;
        };
        /** ProfilePricesRequest */
        ProfilePricesRequest: {
            /** Currency */
            currency?: string | null;
            /** Unit Price */
            unit_price?: number | string | null;
        };
        /** ProposalDraftView */
        ProposalDraftView: {
            /** Doc Type */
            doc_type: string;
            /**
             * Draft Id
             * Format: uuid
             */
            draft_id: string;
            /** Gaps */
            gaps: string[];
            /** Version */
            version: number;
        };
        /**
         * ProposalListDetailView
         * @description The list, how AI read it, and its rows.
         */
        ProposalListDetailView: {
            /** Content Type */
            content_type: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Filename */
            filename: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Rows */
            rows: components["schemas"]["ProposalRowView"][];
            /** Size Bytes */
            size_bytes: number;
            status: components["schemas"]["ExtractionStatus"] | null;
            /**
             * Uploaded By
             * Format: uuid
             */
            uploaded_by: string;
        };
        /** ProposalListSummaryView */
        ProposalListSummaryView: {
            /** Content Type */
            content_type: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Filename */
            filename: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Size Bytes */
            size_bytes: number;
            /**
             * Uploaded By
             * Format: uuid
             */
            uploaded_by: string;
        };
        /** ProposalRowView */
        ProposalRowView: {
            /** Category */
            category: string | null;
            /** Category Reason */
            category_reason: string | null;
            decision: components["schemas"]["RowDecisionView"] | null;
            /** Fields */
            fields: {
                [key: string]: components["schemas"]["CitedValueView"] | null;
            };
            /** Findings */
            findings: components["schemas"]["RowFindingView"][];
            /** Gaps */
            gaps: string[];
            /** Index */
            index: number;
            /** Priority */
            priority: string | null;
            /** Priority Reason */
            priority_reason: string | null;
        };
        /** ProposalSourceView */
        ProposalSourceView: {
            /** Doc Type */
            doc_type: string;
            /** Document Id */
            document_id: string | null;
        };
        /**
         * ProposeProductCaseRequest
         * @description Step 1. JSON only: product images are uploaded after the case exists,
         *     through its documents (doc_type `product_image`). `category` is a key of
         *     the tenant's list (`GET /product-categories`); the handler refuses any
         *     other.
         */
        ProposeProductCaseRequest: {
            /** Category */
            category: string;
            /** Product Name */
            product_name: string;
            /** Proposal Code */
            proposal_code: string;
        };
        /**
         * ProposeRowRequest
         * @description The values the PIC checked: what `propose` takes.
         */
        ProposeRowRequest: {
            /** Category */
            category: string;
            /** Product Name */
            product_name: string;
            /** Proposal Code */
            proposal_code: string;
        };
        /** PurchaseOrderFindingView */
        PurchaseOrderFindingView: {
            /** Code */
            code: string;
            /** Message */
            message: string;
            /** Subject */
            subject: string;
        };
        /** PurchaseOrderProposalView */
        PurchaseOrderProposalView: {
            /** Blocked Reason */
            blocked_reason: string | null;
            /** Can Approve */
            can_approve: boolean;
            /** Content Sha256 */
            content_sha256: string | null;
            /** Draft Id */
            draft_id: string | null;
            draft_status: components["schemas"]["DraftStatus"] | null;
            /** Draft Version */
            draft_version: number | null;
            /** Findings */
            findings: components["schemas"]["PurchaseOrderFindingView"][];
        };
        /**
         * ReassignPicRequest
         * @description Hands a case to another PIC (ticket 06): who, and why. The new PIC
         *     must be a member of the case's workspace; the reason is required.
         */
        ReassignPicRequest: {
            /**
             * Pic User Id
             * Format: uuid
             */
            pic_user_id: string;
            /** Reason */
            reason: string;
        };
        /** RecordPaymentRequest */
        RecordPaymentRequest: {
            /** Amount */
            amount: number | string;
            /** Currency */
            currency: string;
            /** Document Id */
            document_id?: string | null;
            /** Due Date */
            due_date?: string | null;
            kind: components["schemas"]["PaymentKind"];
            /** Paid On */
            paid_on?: string | null;
        };
        /**
         * RedactableAmount
         * @description A price or amount: its value, or null with `redacted` when the caller
         *     lacks `supply_chain.commercial.read`.
         */
        RedactableAmount: {
            /** Redacted */
            redacted: boolean;
            /** Value */
            value: string | null;
        };
        /** RejectDraftRequest */
        RejectDraftRequest: {
            /** Reason */
            reason: string;
        };
        /** ResultChoiceView */
        ResultChoiceView: {
            /** Label */
            label: string;
            /** Value */
            value: string;
        };
        /** ResultFieldView */
        ResultFieldView: {
            /** Choices */
            choices: components["schemas"]["ResultChoiceView"][];
            /** Kind */
            kind: string;
            /** Label */
            label: string;
            /** Name */
            name: string;
            suggestion: components["schemas"]["SuggestionView"] | null;
        };
        /**
         * ResultKind
         * @description What a person types for a physical step: an amount (a price field,
         *     hidden without the commercial scope), a date, a choice among options
         *     (QC's verdict), a text (a container number, a reason).
         * @enum {string}
         */
        ResultKind: "amount" | "date" | "choice" | "text";
        /**
         * ReviewRaise
         * @description What asking for the approval a case waits on (BGĐ's review at step 6,
         *     the sign-off at step 9) did.
         * @enum {string}
         */
        ReviewRaise: "raised" | "already_pending" | "not_raised" | "not_waiting";
        /**
         * ReviewStatus
         * @enum {string}
         */
        ReviewStatus: "pending" | "revision_requested" | "approved";
        /**
         * ReviseDraftRequest
         * @description The fields to replace, by name; a table is its full list of rows.
         */
        ReviseDraftRequest: {
            /** Values */
            values: {
                [key: string]: unknown;
            };
        };
        /**
         * RowDecision
         * @enum {string}
         */
        RowDecision: "proposed" | "dropped";
        /** RowDecisionView */
        RowDecisionView: {
            /**
             * Decided At
             * Format: date-time
             */
            decided_at: string;
            /**
             * Decided By
             * Format: uuid
             */
            decided_by: string;
            decision: components["schemas"]["RowDecision"];
            /** Product Dev Case Id */
            product_dev_case_id: string | null;
            /** Reason */
            reason: string | null;
        };
        /**
         * RowFinding
         * @enum {string}
         */
        RowFinding: "proposal_code_taken" | "proposal_code_repeated" | "item_code_taken" | "product_seen" | "category_unknown" | "no_product_name";
        /** RowFindingView */
        RowFindingView: {
            code: components["schemas"]["RowFinding"];
            /** Message */
            message: string;
        };
        /**
         * RowStatus
         * @description What happened to a row, or would in a dry run.
         * @enum {string}
         */
        RowStatus: "created" | "exists" | "partial" | "rejected";
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
        /** SampleChecklistView */
        SampleChecklistView: {
            /** Can Record */
            can_record: boolean;
            /**
             * Case Id
             * Format: uuid
             */
            case_id: string;
            /** Rows */
            rows: components["schemas"]["ChecklistRowView"][];
            /** Sample Round */
            sample_round: number;
        };
        /** SampleCriterion */
        "SampleCriterion-Input": {
            /** Key */
            key: string;
            kind: components["schemas"]["CriterionKind"];
            /** Label */
            label: string;
            /** Max */
            max?: number | string | null;
            /** Min */
            min?: number | string | null;
            /** Unit */
            unit?: string | null;
        };
        /** SampleCriterion */
        "SampleCriterion-Output": {
            /** Key */
            key: string;
            kind: components["schemas"]["CriterionKind"];
            /** Label */
            label: string;
            /** Max */
            max?: string | null;
            /** Min */
            min?: string | null;
            /** Unit */
            unit?: string | null;
        };
        /**
         * SampleResult
         * @description How a sample round closed (Vòng mẫu: Đạt, Cần chỉnh sửa, Hủy).
         * @enum {string}
         */
        SampleResult: "passed" | "needs_revision" | "rejected";
        /** SampleRoundView */
        SampleRoundView: {
            /** Closed At */
            closed_at: string | null;
            /** Closed By */
            closed_by: string | null;
            /** Evaluation Document Id */
            evaluation_document_id: string | null;
            /**
             * Opened At
             * Format: date-time
             */
            opened_at: string;
            /**
             * Opened By
             * Format: uuid
             */
            opened_by: string;
            /** Requested Changes */
            requested_changes: string | null;
            result: components["schemas"]["SampleResult"] | null;
            /** Revision Document Id */
            revision_document_id: string | null;
            /** Round No */
            round_no: number;
        };
        /**
         * SaveProductProfileRequest
         * @description `prices` absent keeps the last version's price and currency; present, it
         *     needs `supply_chain.commercial.write`.
         */
        SaveProductProfileRequest: {
            /** Attributes */
            attributes?: {
                [key: string]: unknown;
            };
            incoterm?: components["schemas"]["Incoterm"] | null;
            /** Lead Time Days */
            lead_time_days?: number | null;
            /** Moq */
            moq?: number | null;
            prices?: components["schemas"]["ProfilePricesRequest"] | null;
        };
        /** SetPOCommercialRequest */
        SetPOCommercialRequest: {
            /** Currency */
            currency?: string | null;
            /** Deposit Percent */
            deposit_percent?: number | string | null;
            /** Expected Delivery Date */
            expected_delivery_date?: string | null;
            incoterm?: components["schemas"]["Incoterm"] | null;
            /** Line Prices */
            line_prices?: components["schemas"]["LinePriceRequest"][];
            /** Payment Terms */
            payment_terms?: string | null;
        };
        /** SignoffStepView */
        SignoffStepView: {
            /** Label */
            label: string;
            /** Step */
            step: string;
        };
        /**
         * SkuInput
         * @description A SKU to add under the item code (step 9). `planned_quantity` is open
         *     (QE-11): omitted, or above zero.
         */
        SkuInput: {
            /** Planned Quantity */
            planned_quantity?: number | null;
            /** Sku Code */
            sku_code: string;
            /** Variant Label */
            variant_label: string;
        };
        /** SkuView */
        SkuView: {
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Planned Quantity */
            planned_quantity: number | null;
            /** Sku Code */
            sku_code: string;
            /** Variant Label */
            variant_label: string;
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
        /**
         * StepCheck
         * @description Code's checks of a step, each a finding when it fails.
         * @enum {string}
         */
        StepCheck: "sources_present" | "sources_read" | "drafts_complete" | "criteria_measured" | "revision_checked" | "bm04_sources" | "terms_match_bm04" | "codes_free";
        /** StepDecisionRequest */
        StepDecisionRequest: {
            /**
             * Approval Id
             * Format: uuid
             */
            approval_id: string;
            /** Approve */
            approve: boolean;
            /**
             * Comment
             * @default
             */
            comment: string;
            /** Result */
            result?: {
                [key: string]: string;
            };
        };
        /**
         * StepOutcome
         * @description One outcome a person may choose for a physical step: the step it
         *     takes and the words the record prints for it.
         */
        StepOutcome: {
            action: components["schemas"]["ProductAction"];
            /** Label */
            label: string;
        };
        /** StepProposalView */
        StepProposalView: {
            /** Action */
            action: string | null;
            /** Action Document Id */
            action_document_id: string | null;
            /** Approval Id */
            approval_id: string | null;
            /** Can Decide */
            can_decide: boolean;
            /** Drafts */
            drafts: components["schemas"]["ProposalDraftView"][];
            /** Findings */
            findings: components["schemas"]["FindingView"][];
            /** Physical */
            physical: boolean;
            /** Prepared */
            prepared: boolean;
            /** Reason */
            reason: string | null;
            /** Recorded At */
            recorded_at: string | null;
            /** Required Scope */
            required_scope: string | null;
            /** Result Fields */
            result_fields: components["schemas"]["ResultFieldView"][];
            /** Sources */
            sources: components["schemas"]["ProposalSourceView"][];
            /** Stale */
            stale: boolean;
            /**
             * Status
             * @enum {string}
             */
            status: "none" | "proposed" | "not_prepared" | "superseded" | "rejected" | "applied";
        };
        /** SubmitSupplierUpdateRequest */
        SubmitSupplierUpdateRequest: {
            /** Raw Text */
            raw_text: string;
        };
        /**
         * SuggestionView
         * @description What AI read for a result field: shown beside it, never its value.
         */
        SuggestionView: {
            /** Document Id */
            document_id: string | null;
            /** Quote */
            quote: string;
            /** Value */
            value: string;
        };
        /** SupplierBankAccountRequest */
        SupplierBankAccountRequest: {
            /** Account Holder */
            account_holder: string;
            /** Account Number */
            account_number: string;
            /** Bank Name */
            bank_name: string;
        };
        /**
         * SupplierBankAccountView
         * @description Every field null with `redacted` when the caller lacks the commercial
         *     read scope.
         */
        SupplierBankAccountView: {
            /** Account Holder */
            account_holder: string | null;
            /** Account Number */
            account_number: string | null;
            /** Bank Name */
            bank_name: string | null;
            /** Can Edit */
            can_edit: boolean;
            /** Redacted */
            redacted: boolean;
            /**
             * Supplier Id
             * Format: uuid
             */
            supplier_id: string;
            /** Version */
            version: number | null;
        };
        /** SupplierContactRequest */
        SupplierContactRequest: {
            /** Email */
            email?: string | null;
            /** Name */
            name: string;
            /** Phone */
            phone?: string | null;
        };
        /** SupplierContactView */
        SupplierContactView: {
            /** Can Edit */
            can_edit: boolean;
            /** Email */
            email: string | null;
            /** Name */
            name: string | null;
            /** Phone */
            phone: string | null;
            /**
             * Supplier Id
             * Format: uuid
             */
            supplier_id: string;
            /** Version */
            version: number | null;
        };
        /**
         * SupplierEventType
         * @enum {string}
         */
        SupplierEventType: "production_delay" | "qc_issue" | "shipment_update" | "deposit_confirmation" | "document_submitted" | "no_official_update" | "other";
        /** SupplierMessageView */
        SupplierMessageView: {
            /** Attachments */
            attachments: string[];
            /** Body */
            body: string;
            /**
             * Case Id
             * Format: uuid
             */
            case_id: string;
            case_kind: components["schemas"]["CaseKind"];
            /** Citations */
            citations: components["schemas"]["MessageCitationView"][];
            /** Content Sha256 */
            content_sha256: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Dropped */
            dropped: number;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Prompt Id */
            prompt_id: string;
            /** Prompt Version */
            prompt_version: string;
            purpose: components["schemas"]["MessagePurpose"];
            /** Recipient Email */
            recipient_email: string | null;
            /** Recipient Name */
            recipient_name: string | null;
            /** Sent At */
            sent_at: string | null;
            /** Sent By */
            sent_by: string | null;
            status: components["schemas"]["MessageStatus"];
            /** Subject */
            subject: string;
            /** Supplier Name */
            supplier_name: string | null;
            /** Template Version */
            template_version: string;
        };
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
        /** SupplierView */
        SupplierView: {
            /** Code */
            code: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Name */
            name: string;
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
        /** SupplyChainBm04Schema */
        SupplyChainBm04Schema: {
            /** Fields */
            fields: components["schemas"]["Bm04Field"][];
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
            /** Closed Retention Days */
            closed_retention_days?: number | null;
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
        /** SupplyChainItemCodeRule */
        SupplyChainItemCodeRule: {
            /**
             * Policy Id
             * @constant
             */
            policy_id: "supply_chain_item_code_rule";
            /** Policy Version */
            policy_version: string;
            rule?: components["schemas"]["ItemCodeRule"] | null;
            /** Schema Version */
            schema_version: string;
        };
        /** SupplyChainPODocuments */
        SupplyChainPODocuments: {
            /** Policy Id */
            policy_id: string;
            /** Policy Version */
            policy_version: string;
            /** Required */
            required?: {
                [key: string]: components["schemas"]["DocumentType"];
            };
            /** Schema Version */
            schema_version: string;
        };
        /** SupplyChainPackagingPolicy */
        SupplyChainPackagingPolicy: {
            /** Policy Id */
            policy_id: string;
            /** Policy Version */
            policy_version: string;
            /**
             * Require Packaging Content
             * @default false
             */
            require_packaging_content: boolean;
            /** Require Pre Production Test */
            require_pre_production_test: boolean;
            /** Schema Version */
            schema_version: string;
        };
        /** SupplyChainProductActionDuties */
        SupplyChainProductActionDuties: {
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
        /**
         * SupplyChainSLAPolicy
         * @description The versioned answer to "how long should each milestone take", per
         *     Category.
         *
         *     `default` is a dict keyed by milestone name rather than one field per
         *     milestone — same shape as `RetentionPolicy.classes` in dw_platform: the
         *     set of milestones is data, not a fixed set this code has to be edited to
         *     grow. `by_category` has the same shape under a Category key.
         */
        SupplyChainSLAPolicy: {
            /** By Category */
            by_category?: {
                [key: string]: {
                    [key: string]: components["schemas"]["SLAMilestone"];
                };
            };
            /** Categories */
            categories: components["schemas"]["ProductCategory"][];
            /** Default */
            default: {
                [key: string]: components["schemas"]["SLAMilestone"];
            };
            /** Policy Id */
            policy_id: string;
            /** Policy Version */
            policy_version: string;
            /** Schema Version */
            schema_version: string;
            supplier_update: components["schemas"]["SupplierUpdateCadence"];
        };
        /** SupplyChainSampleCriteria */
        "SupplyChainSampleCriteria-Input": {
            /** By Category */
            by_category?: {
                [key: string]: components["schemas"]["SampleCriterion-Input"][];
            };
            /** Default */
            default: components["schemas"]["SampleCriterion-Input"][];
            /** Policy Id */
            policy_id: string;
            /** Policy Version */
            policy_version: string;
            /** Schema Version */
            schema_version: string;
        };
        /** SupplyChainSampleCriteria */
        "SupplyChainSampleCriteria-Output": {
            /** By Category */
            by_category?: {
                [key: string]: components["schemas"]["SampleCriterion-Output"][];
            };
            /** Default */
            default: components["schemas"]["SampleCriterion-Output"][];
            /** Policy Id */
            policy_id: string;
            /** Policy Version */
            policy_version: string;
            /** Schema Version */
            schema_version: string;
        };
        /** SupplyChainStepPreparation */
        SupplyChainStepPreparation: {
            /**
             * Bod Submission
             * @default false
             */
            bod_submission: boolean;
            /**
             * Packaging
             * @default false
             */
            packaging: boolean;
            /**
             * Po Steps
             * @default []
             */
            po_steps: components["schemas"]["POStepKind"][];
            /** Policy Id */
            policy_id: string;
            /** Policy Version */
            policy_version: string;
            /**
             * Purchase Order
             * @default false
             */
            purchase_order: boolean;
            /** Schema Version */
            schema_version: string;
            /**
             * Steps
             * @default []
             */
            steps: components["schemas"]["PreparedStep"][];
            /**
             * Supplier Messages
             * @default []
             */
            supplier_messages: components["schemas"]["MessagePurpose"][];
        };
        /** TakePackagingStepRequest */
        TakePackagingStepRequest: {
            action: components["schemas"]["PackagingAction"];
            /** Document Id */
            document_id?: string | null;
            /** Draft Id */
            draft_id?: string | null;
            /** Reason */
            reason?: string | null;
        };
        /**
         * TemplateFieldKind
         * @enum {string}
         */
        TemplateFieldKind: "text" | "number" | "date" | "table";
        /**
         * UnderstoodView
         * @description What the answer applied, each value decided by code: a supplier is
         *     always a stored name, never the model's mention; a PO reference or a
         *     proposal code is the stored one once the case was found (the question's
         *     own spelling when it was not); a Category is a key of the tenant's list
         *     and a PIC a person of the workspace (or the asker, for "mine").
         */
        UnderstoodView: {
            /** Active Only */
            active_only: boolean;
            /** Category */
            category: string | null;
            /** Pic User Id */
            pic_user_id: string | null;
            /** Po Reference */
            po_reference: string | null;
            product_state: components["schemas"]["ProductDevState"] | null;
            /** Proposal Code */
            proposal_code: string | null;
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
        /**
         * Verdict
         * @enum {string}
         */
        Verdict: "pass" | "fail" | "unmeasured";
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
    get_bm04_schema_api_v1_supply_chain_bm04_schema_get: {
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
                    "application/json": components["schemas"]["SupplyChainBm04Schema"];
                };
            };
        };
    };
    set_bm04_schema_api_v1_supply_chain_bm04_schema_put: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SupplyChainBm04Schema"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SupplyChainBm04Schema"];
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
    list_doc_templates_api_v1_supply_chain_doc_templates_get: {
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
                    "application/json": components["schemas"]["DocTemplateView"][];
                };
            };
        };
    };
    set_doc_template_api_v1_supply_chain_doc_templates_put: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "multipart/form-data": components["schemas"]["Body_set_doc_template_api_v1_supply_chain_doc_templates_put"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DocTemplateView"];
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
    download_case_document_api_v1_supply_chain_documents__document_id__content_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                document_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description The file, as an attachment of its stored type. */
            200: {
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
    get_document_draft_api_v1_supply_chain_drafts__draft_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                draft_id: string;
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
                    "application/json": components["schemas"]["DraftView"];
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
    render_document_draft_api_v1_supply_chain_drafts__draft_id__file_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                draft_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description The draft rendered by its template, as an attachment. */
            200: {
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
    reject_document_draft_api_v1_supply_chain_drafts__draft_id__rejection_post: {
        parameters: {
            query?: never;
            header?: {
                /** @description Optional. Retrying with the same key returns the first response instead of acting twice; reusing it for a different request is a 409. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                draft_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RejectDraftRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DraftView"];
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
    revise_document_draft_api_v1_supply_chain_drafts__draft_id__revisions_post: {
        parameters: {
            query?: never;
            header?: {
                /** @description Optional. Retrying with the same key returns the first response instead of acting twice; reusing it for a different request is a 409. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                draft_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ReviseDraftRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DraftView"];
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
    apply_import_api_v1_supply_chain_imports_post: {
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
                "multipart/form-data": components["schemas"]["Body_apply_import_api_v1_supply_chain_imports_post"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ImportReportView"];
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
    dry_run_import_api_v1_supply_chain_imports_dry_run_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "multipart/form-data": components["schemas"]["Body_dry_run_import_api_v1_supply_chain_imports_dry_run_post"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ImportReportView"];
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
    get_import_template_api_v1_supply_chain_imports_template_get: {
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
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": unknown;
                };
            };
        };
    };
    get_item_code_rule_api_v1_supply_chain_item_code_rule_get: {
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
                    "application/json": components["schemas"]["SupplyChainItemCodeRule"];
                };
            };
        };
    };
    set_item_code_rule_api_v1_supply_chain_item_code_rule_put: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SupplyChainItemCodeRule"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SupplyChainItemCodeRule"];
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
    get_packaging_policy_api_v1_supply_chain_packaging_policy_get: {
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
                    "application/json": components["schemas"]["SupplyChainPackagingPolicy"];
                };
            };
        };
    };
    set_packaging_policy_api_v1_supply_chain_packaging_policy_put: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SupplyChainPackagingPolicy"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SupplyChainPackagingPolicy"];
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
                    "application/json": components["schemas"]["POCaseDetailView"];
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
    get_case_approvals_api_v1_supply_chain_po_cases__case_id__approvals_get: {
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
                    "application/json": components["schemas"]["CaseApprovalsView"];
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
    get_po_commercial_api_v1_supply_chain_po_cases__case_id__commercial_get: {
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
                    "application/json": components["schemas"]["POCommercialView"];
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
    set_po_commercial_api_v1_supply_chain_po_cases__case_id__commercial_put: {
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
                "application/json": components["schemas"]["SetPOCommercialRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["POCommercialView"];
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
    create_po_route_api_v1_supply_chain_po_cases__case_id__create_po_post: {
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
                "application/json": components["schemas"]["CreatePORequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["POCaseDetailView"];
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
    list_case_documents_api_v1_supply_chain_po_cases__case_id__documents_get: {
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
                    "application/json": components["schemas"]["CaseDocumentView"][];
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
    upload_case_document_api_v1_supply_chain_po_cases__case_id__documents_post: {
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
                "multipart/form-data": components["schemas"]["Body_upload_case_document_api_v1_supply_chain_po_cases__case_id__documents_post"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CaseDocumentView"];
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
    list_po_case_drafts_api_v1_supply_chain_po_cases__case_id__drafts_get: {
        parameters: {
            query?: {
                kind?: components["schemas"]["CaseKind"];
            };
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
                    "application/json": components["schemas"]["DraftView"][];
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
    get_packaging_design_api_v1_supply_chain_po_cases__case_id__packaging_design_get: {
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
                    "application/json": components["schemas"]["PackagingDesignView"];
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
    take_packaging_step_api_v1_supply_chain_po_cases__case_id__packaging_design_steps_post: {
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
                "application/json": components["schemas"]["TakePackagingStepRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PackagingStateView"];
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
    get_packaging_proof_api_v1_supply_chain_po_cases__case_id__packaging_proof_get: {
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
                    "application/json": components["schemas"]["PackagingProofView"];
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
    record_po_payment_api_v1_supply_chain_po_cases__case_id__payments_post: {
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
                "application/json": components["schemas"]["RecordPaymentRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["POPaymentView"];
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
    reassign_po_case_pic_api_v1_supply_chain_po_cases__case_id__pic_post: {
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
                "application/json": components["schemas"]["ReassignPicRequest"];
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
    get_pre_production_checklist_api_v1_supply_chain_po_cases__case_id__pre_production_checklist_get: {
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
                    "application/json": components["schemas"]["PreProductionChecklistView"];
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
    record_pre_production_measurement_api_v1_supply_chain_po_cases__case_id__pre_production_measurements_post: {
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
                "application/json": components["schemas"]["MeasurementRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PreProductionChecklistView"];
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
    get_purchase_order_proposal_api_v1_supply_chain_po_cases__case_id__purchase_order_get: {
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
                    "application/json": components["schemas"]["PurchaseOrderProposalView"];
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
    approve_purchase_order_api_v1_supply_chain_po_cases__case_id__purchase_order_approval_post: {
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
                "application/json": components["schemas"]["ApprovePurchaseOrderRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PurchaseOrderProposalView"];
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
    get_po_step_proposal_api_v1_supply_chain_po_cases__case_id__step_proposal_get: {
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
                    "application/json": components["schemas"]["POStepProposalView"];
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
    approve_po_step_api_v1_supply_chain_po_cases__case_id__step_proposal_approval_post: {
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
                "application/json": components["schemas"]["ApprovePOStepRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["POStepProposalView"];
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
    list_po_case_supplier_messages_api_v1_supply_chain_po_cases__case_id__supplier_messages_get: {
        parameters: {
            query?: {
                kind?: components["schemas"]["CaseKind"];
            };
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
                    "application/json": components["schemas"]["SupplierMessageView"][];
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
            query?: {
                limit?: number;
                /** @description Opaque cursor from a previous page. */
                cursor?: string | null;
            };
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
                    "application/json": components["schemas"]["Page_CaseTransitionView_"];
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
    get_po_documents_policy_api_v1_supply_chain_po_documents_policy_get: {
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
                    "application/json": components["schemas"]["SupplyChainPODocuments"];
                };
            };
        };
    };
    put_po_documents_policy_api_v1_supply_chain_po_documents_policy_put: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SupplyChainPODocuments"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SupplyChainPODocuments"];
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
    get_product_action_duties_api_v1_supply_chain_product_action_duties_get: {
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
                    "application/json": components["schemas"]["SupplyChainProductActionDuties"];
                };
            };
        };
    };
    set_product_action_duties_override_api_v1_supply_chain_product_action_duties_put: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SupplyChainProductActionDuties"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SupplyChainProductActionDuties"];
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
    list_product_cases_api_v1_supply_chain_product_cases_get: {
        parameters: {
            query?: {
                limit?: number;
                /** @description Opaque cursor from a previous page. */
                cursor?: string | null;
                /** @description Only cases in this state. */
                state?: components["schemas"]["ProductDevState"] | null;
                /** @description Only cases this person is PIC of. */
                pic_user_id?: string | null;
                /** @description Only cases stamped with this Category key. */
                category?: string | null;
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
                    "application/json": components["schemas"]["Page_ProductCaseView_"];
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
    propose_product_case_api_v1_supply_chain_product_cases_post: {
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
                "application/json": components["schemas"]["ProposeProductCaseRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ProductCaseView"];
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
    get_product_case_api_v1_supply_chain_product_cases__case_id__get: {
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
                    "application/json": components["schemas"]["ProductCaseDetailView"];
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
    list_product_case_documents_api_v1_supply_chain_product_cases__case_id__documents_get: {
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
                    "application/json": components["schemas"]["CaseDocumentView"][];
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
    upload_product_case_document_api_v1_supply_chain_product_cases__case_id__documents_post: {
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
                "multipart/form-data": components["schemas"]["Body_upload_product_case_document_api_v1_supply_chain_product_cases__case_id__documents_post"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CaseDocumentView"];
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
    list_product_case_drafts_api_v1_supply_chain_product_cases__case_id__drafts_get: {
        parameters: {
            query?: {
                kind?: components["schemas"]["CaseKind"];
            };
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
                    "application/json": components["schemas"]["DraftView"][];
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
    place_product_order_api_v1_supply_chain_product_cases__case_id__order_post: {
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
        requestBody?: {
            content: {
                "application/json": components["schemas"]["PlaceOrderRequest"] | null;
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["OrderPlacedView"];
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
    reassign_product_case_pic_api_v1_supply_chain_product_cases__case_id__pic_post: {
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
                "application/json": components["schemas"]["ReassignPicRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ProductCaseView"];
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
    get_product_profile_api_v1_supply_chain_product_cases__case_id__profile_get: {
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
                    "application/json": components["schemas"]["ProductProfileView"];
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
    save_product_profile_api_v1_supply_chain_product_cases__case_id__profile_post: {
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
                "application/json": components["schemas"]["SaveProductProfileRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ProductProfileView"];
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
    get_sample_checklist_api_v1_supply_chain_product_cases__case_id__sample_checklist_get: {
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
                    "application/json": components["schemas"]["SampleChecklistView"];
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
    record_sample_measurement_api_v1_supply_chain_product_cases__case_id__sample_measurements_post: {
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
                "application/json": components["schemas"]["MeasurementRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SampleChecklistView"];
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
    get_step_proposal_api_v1_supply_chain_product_cases__case_id__step_proposal_get: {
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
                    "application/json": components["schemas"]["StepProposalView"];
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
    decide_step_proposal_api_v1_supply_chain_product_cases__case_id__step_proposal_decision_post: {
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
                "application/json": components["schemas"]["StepDecisionRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["StepProposalView"];
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
    list_product_case_supplier_messages_api_v1_supply_chain_product_cases__case_id__supplier_messages_get: {
        parameters: {
            query?: {
                kind?: components["schemas"]["CaseKind"];
            };
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
                    "application/json": components["schemas"]["SupplierMessageView"][];
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
    get_product_case_transitions_api_v1_supply_chain_product_cases__case_id__transitions_get: {
        parameters: {
            query?: {
                limit?: number;
                /** @description Opaque cursor from a previous page. */
                cursor?: string | null;
            };
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
                    "application/json": components["schemas"]["Page_ProductCaseTransitionView_"];
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
    create_product_case_transition_api_v1_supply_chain_product_cases__case_id__transitions_post: {
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
                "application/json": components["schemas"]["AdvanceProductCaseRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ProductCaseStepView"];
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
    list_product_categories_api_v1_supply_chain_product_categories_get: {
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
                    "application/json": components["schemas"]["ProductCategoryView"][];
                };
            };
        };
    };
    list_proposal_lists_api_v1_supply_chain_proposal_lists_get: {
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
                    "application/json": components["schemas"]["ProposalListSummaryView"][];
                };
            };
        };
    };
    upload_proposal_list_api_v1_supply_chain_proposal_lists_post: {
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
                "multipart/form-data": components["schemas"]["Body_upload_proposal_list_api_v1_supply_chain_proposal_lists_post"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ProposalListDetailView"];
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
    get_proposal_list_api_v1_supply_chain_proposal_lists__list_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                list_id: string;
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
                    "application/json": components["schemas"]["ProposalListDetailView"];
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
    drop_from_list_api_v1_supply_chain_proposal_lists__list_id__rows__index__dismissal_post: {
        parameters: {
            query?: never;
            header?: {
                /** @description Optional. Retrying with the same key returns the first response instead of acting twice; reusing it for a different request is a 409. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                list_id: string;
                index: number;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["DropRowRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ProposalListDetailView"];
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
    propose_from_list_api_v1_supply_chain_proposal_lists__list_id__rows__index__proposal_post: {
        parameters: {
            query?: never;
            header?: {
                /** @description Optional. Retrying with the same key returns the first response instead of acting twice; reusing it for a different request is a 409. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                list_id: string;
                index: number;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ProposeRowRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ProposalListDetailView"];
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
    get_sample_criteria_policy_api_v1_supply_chain_sample_criteria_policy_get: {
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
                    "application/json": components["schemas"]["SupplyChainSampleCriteria-Output"];
                };
            };
        };
    };
    set_sample_criteria_policy_api_v1_supply_chain_sample_criteria_policy_put: {
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
                "application/json": components["schemas"]["SupplyChainSampleCriteria-Input"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SupplyChainSampleCriteria-Output"];
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
    get_step_preparation_policy_api_v1_supply_chain_step_preparation_policy_get: {
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
                    "application/json": components["schemas"]["SupplyChainStepPreparation"];
                };
            };
        };
    };
    set_step_preparation_policy_api_v1_supply_chain_step_preparation_policy_put: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SupplyChainStepPreparation"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SupplyChainStepPreparation"];
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
    mark_supplier_message_sent_api_v1_supply_chain_supplier_messages__message_id__sent_post: {
        parameters: {
            query?: never;
            header?: {
                /** @description Optional. Retrying with the same key returns the first response instead of acting twice; reusing it for a different request is a 409. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                message_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["MarkSentRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SupplierMessageView"];
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
    list_suppliers_api_v1_supply_chain_suppliers_get: {
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
                    "application/json": components["schemas"]["SupplierView"][];
                };
            };
        };
    };
    get_supplier_bank_account_api_v1_supply_chain_suppliers__supplier_id__bank_account_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                supplier_id: string;
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
                    "application/json": components["schemas"]["SupplierBankAccountView"];
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
    save_supplier_bank_account_api_v1_supply_chain_suppliers__supplier_id__bank_account_post: {
        parameters: {
            query?: never;
            header?: {
                /** @description Optional. Retrying with the same key returns the first response instead of acting twice; reusing it for a different request is a 409. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                supplier_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SupplierBankAccountRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SupplierBankAccountView"];
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
    get_supplier_contact_api_v1_supply_chain_suppliers__supplier_id__contact_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                supplier_id: string;
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
                    "application/json": components["schemas"]["SupplierContactView"];
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
    save_supplier_contact_api_v1_supply_chain_suppliers__supplier_id__contact_post: {
        parameters: {
            query?: never;
            header?: {
                /** @description Optional. Retrying with the same key returns the first response instead of acting twice; reusing it for a different request is a 409. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                supplier_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SupplierContactRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SupplierContactView"];
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

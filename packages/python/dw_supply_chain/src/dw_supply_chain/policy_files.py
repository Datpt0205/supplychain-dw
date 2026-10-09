"""The shipped file each Supply Chain policy loads from, for the policies (and
the worker) more than one host loads.

The API and the worker both evaluate cases under the SLA policy and route
follow-ups under the follow-up policy, so the version each loads is named
once, here. A bump edits this line and both hosts follow; two copies of the
file name would let the worker keep evaluating last month's numbers.

Both hosts also raise BGĐ's review and the step-9 sign-off of a product case
(the API from the step, the worker's reconcile lane for one a step missed), so
both read the product approvals policy and host both graphs' worker
definitions.

Both hosts propose a product case too (the API from the web form, the worker
from a chat's "Đồng ý"), so both read the product step-to-duty policy that
`propose` is authorized against.
"""

from __future__ import annotations

SLA_POLICY_FILE = "supply_chain_sla@2.0.0.yaml"
# Who takes each PO step: the API authorizes steps by it, the worker's PO step
# lane tells the holders of a step's duty that its paper is drafted
# (ticket ai-automation/15).
ACTION_DUTIES_POLICY_FILE = "supply_chain_action_duties@1.2.0.yaml"
FOLLOW_UP_POLICY_FILE = "supply_chain_follow_ups@1.2.0.yaml"
PRODUCT_APPROVALS_POLICY_FILE = "supply_chain_product_approvals@1.1.0.yaml"
ADVANCE_PRODUCT_CASE_WORKER_FILE = "supply_chain_advance_product_case.yaml"
PRODUCT_SIGNOFF_WORKER_FILE = "supply_chain_product_signoff.yaml"
PRODUCT_ACTION_DUTIES_POLICY_FILE = "supply_chain_product_action_duties@1.3.0.yaml"
# Both hosts run step preparations (ticket ai-automation/05): the worker's lane
# starts them, a decision on the web resumes them in the API, one on Zalo in the
# worker; both read the policy for the case page and the lane.
STEP_PREPARATION_POLICY_FILE = "supply_chain_step_preparation@1.0.0.yaml"
STEP_PREPARATION_WORKER_FILE = "supply_chain_step_preparation.yaml"
# Which profile each model task runs on (ticket ai-automation/06): read by
# the worker's extraction lane, and by scripts/model_gate.py for the gate's
# dataset and threshold.
MODEL_ROUTES_POLICY_FILE = "supply_chain_model_routes@1.10.0.yaml"
BM04_SCHEMA_POLICY_FILE = "supply_chain_bm04_schema@1.0.0.yaml"
# The wording of a message to a supplier (ticket ai-automation/07): read by the
# worker's message lane.
SUPPLIER_MESSAGES_POLICY_FILE = "supply_chain_supplier_messages@1.1.0.yaml"
# What R&D measures on a sample round (ticket ai-automation/09): the API's
# checklist and the worker's preparation compare against the same file.
SAMPLE_CRITERIA_POLICY_FILE = "supply_chain_sample_criteria@1.0.0.yaml"
# How a tenant's item and SKU codes are made (ticket ai-automation/13): the
# worker's preparation of step 9 proposes from it, the API serves and replaces it.
ITEM_CODE_RULE_POLICY_FILE = "supply_chain_item_code_rule@1.0.0.yaml"

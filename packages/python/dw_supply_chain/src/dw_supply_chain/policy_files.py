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

SLA_POLICY_FILE = "supply_chain_sla@1.2.0.yaml"
FOLLOW_UP_POLICY_FILE = "supply_chain_follow_ups@1.0.0.yaml"
PRODUCT_APPROVALS_POLICY_FILE = "supply_chain_product_approvals@1.1.0.yaml"
ADVANCE_PRODUCT_CASE_WORKER_FILE = "supply_chain_advance_product_case.yaml"
PRODUCT_SIGNOFF_WORKER_FILE = "supply_chain_product_signoff.yaml"
PRODUCT_ACTION_DUTIES_POLICY_FILE = "supply_chain_product_action_duties@1.3.0.yaml"

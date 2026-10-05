"""The shipped file each Supply Chain policy loads from, for the policies more
than one host loads.

The API and the worker both evaluate cases under the SLA policy and route
follow-ups under the follow-up policy, so the version each loads is named
once, here. A bump edits this line and both hosts follow; two copies of the
file name would let the worker keep evaluating last month's numbers.
"""

from __future__ import annotations

SLA_POLICY_FILE = "supply_chain_sla@1.2.0.yaml"
FOLLOW_UP_POLICY_FILE = "supply_chain_follow_ups@1.0.0.yaml"

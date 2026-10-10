"""What the API's composition root turns on for a held memory.

The approval flow's own tests prove that a strict prefix and a decision guard
are enforced when they are configured. This proves they ARE configured in the
container the API serves: without it, deleting the two lines from
`bootstrap/runtime.py` would leave every other test green while anyone with
`approvals.decide` approved a restricted memory they cannot read, or their own.
"""

import uuid

import pytest

from dw_api.bootstrap import build_container
from dw_api.settings import ApiSettings
from dw_kernel.errors import PermissionDeniedError
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_memory.review import MEMORY_REVIEW
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.approval import ApprovalRequest

pytestmark = pytest.mark.unit


def test_the_api_decides_memory_reviews_strictly_and_only_with_clearance() -> None:
    # As `scripts/generate_contracts.py` builds it: engines and clients are
    # lazy, so a placeholder database is enough to compose the runtime.
    container = build_container(
        ApiSettings(
            profile="test",
            database_url="postgresql+asyncpg://wiring:wiring@localhost:5432/wiring",
            dev_secret="wiring-test-secret-0123456789abcdef",
            s3_endpoint_url="http://localhost:9000",
            s3_access_key="wiring",
            s3_secret_key="wiring",
            model_provider="mock",
        )
    )
    flow = container.approval_flow
    assert flow is not None
    assert flow.is_strict(MEMORY_REVIEW)

    tenant, workspace = uuid.uuid4(), uuid.uuid4()
    request = ApprovalRequest(
        id=uuid.uuid4(),
        tenant_id=TenantId(tenant),
        workspace_id=WorkspaceId(workspace),
        approval_type=MEMORY_REVIEW,
        requested_by=UserId(uuid.uuid4()),
        reason="held for review",
        payload={"candidate_id": str(uuid.uuid4()), "classification": "restricted"},
    )
    guard = flow.decision_guards.get(MEMORY_REVIEW)
    assert guard is not None
    with pytest.raises(PermissionDeniedError):
        guard(
            request,
            AccessContext(
                tenant_id=tenant,
                workspace_id=workspace,
                principal_id=uuid.uuid4(),
                roles=frozenset({"approver"}),
                scopes=frozenset({"approvals.decide"}),
                clearance="internal",
                plan_id="professional",
            ),
        )

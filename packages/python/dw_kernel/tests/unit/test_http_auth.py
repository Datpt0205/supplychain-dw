"""Both front doors must accept and refuse exactly the same requests."""

import uuid

import pytest

from dw_kernel.errors import ErrorCode, TenantContextMissingError, UnauthenticatedError
from dw_kernel.http_auth import bearer_token, uuid_header

pytestmark = pytest.mark.unit

TOKEN = "eyJhbGciOi.payload.sig"


@pytest.mark.parametrize("scheme", ["Bearer", "bearer", "BEARER", "BeArEr"])
def test_the_scheme_is_case_insensitive(scheme: str) -> None:
    """RFC 7235 makes it so, and a connector sending lowercase is legal."""
    assert bearer_token(f"{scheme} {TOKEN}") == TOKEN


@pytest.mark.parametrize("header", ["", None, "Bearer", "Bearer ", "Bearer    ", "Basic abc"])
def test_anything_without_a_token_is_refused(header: str | None) -> None:
    """A blank token used to reach the verifier instead of stopping here.
    Unauthenticated, not forbidden: signing in again is the fix, and a client
    must be able to tell that from a permission it lacks (401, not 403)."""
    with pytest.raises(UnauthenticatedError, match="missing bearer token") as refused:
        bearer_token(header)
    assert refused.value.code is ErrorCode.UNAUTHENTICATED


def test_surrounding_whitespace_is_not_part_of_the_token() -> None:
    assert bearer_token(f"Bearer  {TOKEN}  ") == TOKEN


def test_an_absent_header_is_not_an_error() -> None:
    assert uuid_header("X-Tenant-Id", None) is None


def test_a_header_that_is_not_a_uuid_is_refused() -> None:
    with pytest.raises(TenantContextMissingError, match="X-Tenant-Id"):
        uuid_header("X-Tenant-Id", "not-a-uuid")


def test_a_valid_header_is_parsed() -> None:
    value = uuid.uuid4()
    assert uuid_header("X-Tenant-Id", str(value)) == value

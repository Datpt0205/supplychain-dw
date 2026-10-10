"""Contract test: every error code has its HTTP status, and the browser knows it.

`ErrorCode` is the public taxonomy (`dw_kernel.errors`). A code missing from the
API's status map falls through to 500, which reads to a client as "the server
broke" when the server in fact refused on purpose; a code missing from the
TypeScript copy is one the web cannot branch on. Both drifts are silent, so this
asks both owners directly.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from dw_api.errors import _STATUS_BY_CODE, status_for
from dw_kernel.errors import (
    ErrorCode,
    PayloadTooLargeError,
    UnsupportedMediaTypeError,
)

pytestmark = pytest.mark.contract

REPO_ROOT = Path(__file__).resolve().parents[4]
TS_ERRORS = REPO_ROOT / "packages" / "typescript" / "contracts" / "src" / "error.ts"


def test_every_error_code_has_an_explicit_status() -> None:
    assert set(ErrorCode) - set(_STATUS_BY_CODE) == set()


def test_a_body_too_large_is_413_and_an_unsupported_type_is_415() -> None:
    assert PayloadTooLargeError("x").code is ErrorCode.PAYLOAD_TOO_LARGE
    assert UnsupportedMediaTypeError("x").code is ErrorCode.UNSUPPORTED_MEDIA_TYPE
    assert status_for(ErrorCode.PAYLOAD_TOO_LARGE) == 413
    assert status_for(ErrorCode.UNSUPPORTED_MEDIA_TYPE) == 415


def test_the_typescript_error_codes_mirror_the_python_ones() -> None:
    block = re.search(
        r"export const ErrorCode = \{(.*?)\} as const", TS_ERRORS.read_text("utf-8"), re.S
    )
    assert block is not None, "ErrorCode block not found in error.ts"
    assert set(re.findall(r':\s*"([a-z_]+)"', block.group(1))) == {code.value for code in ErrorCode}

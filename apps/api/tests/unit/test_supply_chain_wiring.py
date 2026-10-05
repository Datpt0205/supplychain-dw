"""Unit: the composition root hands Supply Chain's model-calling handlers the
one-call gateway, not the bare one.

Asserted through the real `build_container`, not a hand-built container: the
bare gateway works too — it just leaks one spend-ledger entry per call for
the life of the process — so nothing else would ever notice the wiring
reverting.
"""

import pytest

from dw_agent_runtime.model.gateway import RoutingModelGateway
from dw_agent_runtime.model.single_call import SingleCallModelGateway
from dw_api.bootstrap import build_container
from dw_api.settings import ApiSettings
from dw_kernel.errors import NotFoundError

pytestmark = pytest.mark.unit


def test_every_supply_chain_model_call_frees_its_spend_ledger_entry() -> None:
    # Engines and storage clients are lazy: placeholder URLs wire the context
    # (it needs the runtime, which needs object storage) without contacting
    # anything — the same settings `scripts/generate_contracts.py` builds with.
    settings = ApiSettings(
        profile="test",
        database_url="postgresql+asyncpg://wiring:wiring@localhost:5432/wiring",
        auth_mode="dev",
        dev_secret="wiring-test-secret-0123456789abcdef",
        s3_endpoint_url="http://localhost:9000",
        s3_access_key="wiring",
        s3_secret_key="wiring",
        model_provider="mock",
    )
    container = build_container(settings)

    model_calling = {
        "submit_supplier_update": container.supply_chain_submit_supplier_update,
        "analyze_delay_impact": container.supply_chain_analyze_delay_impact,
        "answer_case_query": container.supply_chain_answer_case_query,
        "summarize_daily_brief": container.supply_chain_summarize_daily_brief,
    }
    for name, handler in model_calling.items():
        assert handler is not None, name
        assert isinstance(handler.gateway, SingleCallModelGateway), name


def test_the_brief_summary_reads_the_same_brief_handler_the_page_does() -> None:
    """One handler decides who may see the brief and what it holds; the
    summary must not get a second, differently wired copy of it."""
    settings = ApiSettings(
        profile="test",
        database_url="postgresql+asyncpg://wiring:wiring@localhost:5432/wiring",
        auth_mode="dev",
        dev_secret="wiring-test-secret-0123456789abcdef",
        s3_endpoint_url="http://localhost:9000",
        s3_access_key="wiring",
        s3_secret_key="wiring",
        model_provider="mock",
    )
    container = build_container(settings)
    summarize = container.supply_chain_summarize_daily_brief
    assert summarize is not None
    assert summarize.get_daily_brief is container.supply_chain_get_daily_brief


def _settings(**overrides: object) -> ApiSettings:
    values: dict[str, object] = {
        "profile": "test",
        "database_url": "postgresql+asyncpg://wiring:wiring@localhost:5432/wiring",
        "auth_mode": "dev",
        "dev_secret": "wiring-test-secret-0123456789abcdef",
        "s3_endpoint_url": "http://localhost:9000",
        "s3_access_key": "wiring",
        "s3_secret_key": "wiring",
        "model_provider": "mock",
    }
    values.update(overrides)
    return ApiSettings.model_validate(values)


def test_supply_chain_model_calls_run_on_the_configured_profile() -> None:
    """DW_API_MODEL_PROFILE reached embeddings only: Supply Chain's calls name
    no profile and ran on `balanced`, the mock, whatever was configured."""
    container = build_container(_settings(model_profile="luna"))

    handler = container.supply_chain_answer_case_query
    assert handler is not None
    assert isinstance(handler.gateway, SingleCallModelGateway)
    assert isinstance(handler.gateway.inner, RoutingModelGateway)
    assert handler.gateway.inner.default_profile == "luna"


def test_a_profile_nobody_registered_refuses_to_start() -> None:
    with pytest.raises(NotFoundError, match="model profile not registered"):
        build_container(_settings(model_profile="no_such_profile"))


def test_case_documents_are_wired_from_their_settings() -> None:
    """The cap and the bucket are read where they decide something: the
    upload handler refuses past the cap, and every handler talks to the bucket
    the worker also reads (offboarding, the orphan sweep)."""
    settings = ApiSettings(
        profile="test",
        database_url="postgresql+asyncpg://wiring:wiring@localhost:5432/wiring",
        auth_mode="dev",
        dev_secret="wiring-test-secret-0123456789abcdef",
        s3_endpoint_url="http://localhost:9000",
        s3_access_key="wiring",
        s3_secret_key="wiring",
        model_provider="mock",
        case_documents_bucket="docs-under-test",
        case_document_max_bytes=4321,
    )
    container = build_container(settings)

    upload = container.supply_chain_upload_case_document
    download = container.supply_chain_download_case_document
    assert upload is not None and download is not None
    assert container.supply_chain_list_case_documents is not None
    assert upload.max_bytes == 4321
    assert getattr(upload.storage, "bucket", None) == "docs-under-test"
    assert getattr(download.storage, "bucket", None) == "docs-under-test"

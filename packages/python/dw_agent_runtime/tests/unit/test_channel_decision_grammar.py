"""Unit: the decision grammar, the code's keyed hash, the subject-version
registry and the chat command's routing (zalo-channel ticket 05, ADR 0014).

The grammar is fixed and read by code: `DUYỆT <mã>` and `KHÔNG <mã> <lý do>`,
six ASCII digits, case and accents ignored. Anything that starts with the verb
"duyệt" but is not the grammar gets the grammar back and is never handed on;
"không" is ours only when a number follows. Also, an architecture check: only
the chat decision service and the web route call `ApproveAndResumeService.decide`.
"""

from __future__ import annotations

import ast
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from dw_agent_runtime.channel_decisions import (
    APPROVE_HINT,
    REJECT_HINT,
    ChannelDecisionCommand,
    ChannelDecisionOutcome,
    Malformed,
    ParsedDecision,
    parse_decision,
)
from dw_platform.application.access_context import AccessContext
from dw_platform.application.approval_codes import (
    ApprovalSubjectVersions,
    DecisionCodeKey,
    new_code,
)
from dw_platform.domain.approval import ApprovalRequest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[5]


# ---- the grammar ----------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("DUYỆT 482193", ParsedDecision(True, "482193", "")),
        ("duyệt 482193", ParsedDecision(True, "482193", "")),
        ("Duyet 482193", ParsedDecision(True, "482193", "")),
        ("  DUYỆT   007100  ", ParsedDecision(True, "007100", "")),
        # NFD input (a phone keyboard that composes late) is normalised first.
        ("DUYỆT 482193", ParsedDecision(True, "482193", "")),
        ("KHÔNG 482193 Màu chưa đạt", ParsedDecision(False, "482193", "Màu chưa đạt")),
        ("không 482193   giá cao", ParsedDecision(False, "482193", "giá cao")),
        ("khong 482193 x", ParsedDecision(False, "482193", "x")),
    ],
)
def test_the_grammar_reads_a_decision(text: str, expected: ParsedDecision) -> None:
    assert parse_decision(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "duyệt hết",
        "DUYỆT",
        "DUYỆT 1234",
        "DUYỆT 1234567",
        "DUYỆT 482193 nhận xét ở đây",  # the comment is written on the portal
        # Full-width digits are not the code's digits.
        "DUYỆT " + "".join(chr(0xFF10 + int(d)) for d in "482193"),
        "duyệt giúp em cái này",
    ],
)
def test_the_verb_in_any_other_shape_gets_the_grammar_back(text: str) -> None:
    assert parse_decision(text) == Malformed(APPROVE_HINT)


@pytest.mark.parametrize("text", ["KHÔNG 482193", "KHÔNG 482193   ", "không 12 lý do"])
def test_a_rejection_without_a_reason_or_a_code_gets_the_grammar_back(text: str) -> None:
    assert parse_decision(text) == Malformed(REJECT_HINT)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "không biết mã đề xuất là gì",
        "không",
        "approve 8d6c1f8e-0000-4000-8000-000000000001",
        "đồng ý",
        "482193",
        "TỪ CHỐI 482193 lý do",
    ],
)
def test_anything_else_is_not_a_decision(text: str) -> None:
    assert parse_decision(text) is None


# ---- the key --------------------------------------------------------------


def test_the_hash_binds_the_code_to_one_person_and_one_approval() -> None:
    key = DecisionCodeKey(b"unit-secret-0123456789")
    approval, other_approval = uuid.uuid4(), uuid.uuid4()
    person, other_person = uuid.uuid4(), uuid.uuid4()
    digest = key.digest(approval, person, "482193")

    assert key.matches(digest, approval, person, "482193")
    assert not key.matches(digest, approval, person, "482194")
    assert not key.matches(digest, approval, other_person, "482193")
    assert not key.matches(digest, other_approval, person, "482193")
    assert not DecisionCodeKey(b"another-secret-0123456789").matches(
        digest, approval, person, "482193"
    )
    assert len(digest) == 32


def test_a_short_key_is_refused_and_never_printed() -> None:
    with pytest.raises(ValueError, match="at least 16 bytes"):
        DecisionCodeKey(b"short")
    assert "unit-secret" not in repr(DecisionCodeKey(b"unit-secret-0123456789"))


def test_a_code_is_six_digits() -> None:
    for _ in range(200):
        code = new_code()
        assert len(code) == 6 and code.isascii() and code.isdigit()


# ---- the subject-version registry ------------------------------------------


@dataclass
class _Port:
    name: str

    async def version_of(self, context: AccessContext, request: ApprovalRequest) -> str | None:
        return self.name


def test_the_longest_registered_prefix_answers_and_an_unknown_type_has_none() -> None:
    registry = ApprovalSubjectVersions()
    wide, narrow = _Port("wide"), _Port("narrow")
    registry.register("supply_chain.", wide)
    registry.register("supply_chain.product_action.", narrow)

    assert registry.for_type("supply_chain.product_action.bod_review") is narrow
    assert registry.for_type("supply_chain.po.approve") is wide
    assert registry.for_type("memory.review") is None
    with pytest.raises(ValueError, match="already registered"):
        registry.register("supply_chain.", wide)


# ---- the chat command ------------------------------------------------------


@dataclass(frozen=True)
class _Message:
    text: str
    channel: str = "zalo"
    message_id: str = "m-1"
    chat_id: str = "chat-1"


@dataclass
class _Service:
    asked: list[dict[str, Any]] = field(default_factory=list)

    async def decide(self, **kwargs: Any) -> ChannelDecisionOutcome:
        self.asked.append(kwargs)
        return ChannelDecisionOutcome(decided=True, reason=None, reply="đã ghi")


def _context() -> AccessContext:
    return AccessContext(
        tenant_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        principal_id=uuid.uuid4(),
        roles=frozenset(),
        scopes=frozenset({"approvals.decide"}),
        plan_id="professional",
    )


async def test_the_command_passes_a_non_decision_on_and_decides_a_decision() -> None:
    service = _Service()
    command = ChannelDecisionCommand(service)  # type: ignore[arg-type]
    replies: list[str] = []

    async def reply(text: str) -> None:
        replies.append(text)

    context = _context()
    assert not await command.handle(_Message("chảo 28cm"), context, reply)
    assert await command.handle(_Message("duyệt hết"), context, reply)
    assert service.asked == []
    assert await command.handle(_Message("DUYỆT 482193"), context, reply)

    assert replies == [APPROVE_HINT, "đã ghi"]
    (asked,) = service.asked
    assert asked["user_id"] == context.principal_id
    assert asked["decision"] == ParsedDecision(True, "482193", "")
    assert command.ceiling == frozenset({"approvals.decide"})


# ---- who may call decide ---------------------------------------------------


def _decide_callers() -> set[str]:
    """Every source module with a `.decide(...)` call that passes `approve=`:
    the shape of `ApproveAndResumeService.decide`, and of nothing else."""
    found: set[str] = set()
    for pattern in ("packages/python/*/src/**/*.py", "apps/*/src/**/*.py"):
        for path in REPO_ROOT.glob(pattern):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "decide"
                    and any(k.arg == "approve" for k in node.keywords)
                ):
                    found.add(path.relative_to(REPO_ROOT).as_posix())
    return found


def test_only_the_web_route_and_the_chat_decision_service_reach_decide() -> None:
    """No chat command of Z4 or Z6, no workflow, no tool reaches the decision:
    the model has no path to it (ADR 0014 point 6)."""
    assert _decide_callers() == {
        "apps/api/src/dw_api/routes/v1/approvals.py",
        "packages/python/dw_agent_runtime/src/dw_agent_runtime/channel_decisions.py",
    }

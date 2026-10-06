"""Release manifest generation is deterministic and complete."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[4]


def _load_script():  # scripts/ is not a package; load by path
    spec = importlib.util.spec_from_file_location(
        "release_manifest", REPO_ROOT / "scripts" / "release_manifest.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["release_manifest"] = module
    spec.loader.exec_module(module)
    return module


def test_manifest_contains_every_required_section() -> None:
    script = _load_script()
    manifest = script.build_manifest()
    for key in (
        "platform_version",
        "git_sha",
        "api_version",
        "workers",
        "prompt_bundles",
        "tool_specs",
        "toolsets",
        "policies",
        "knowledge_index_version",
        "memory_policy_version",
        # A run stamps this on its own row and the policy refuses to decide for a
        # run stamped under a version this process does not have. That makes it a
        # versioned artifact whether or not it lives in a YAML file, so a release
        # that changes it has to be identifiable afterwards.
        "approval_policy_version",
        "eval_datasets",
    ):
        assert key in manifest, f"manifest missing {key}"
    # The version a run is actually stamped with, not a second copy of the string.
    from dw_agent_runtime.autonomy import AUTONOMY_POLICY_VERSION

    assert manifest["approval_policy_version"] == AUTONOMY_POLICY_VERSION
    # Platform artifacts, which exist with no bounded context installed. A
    # context adds its own worker/prompt/policy assertions beside these; naming
    # one here would make this test fail on a checkout that does not host it.
    assert {p["prompt_id"] for p in manifest["prompt_bundles"]} >= {"platform.untrusted_demo"}
    # Every worker that DECLARES a toolset_version must have it resolve to a
    # pin file, or a run records a toolset nobody can reconstruct. `None` is
    # the honest value for a worker that calls no tool at all (a plain graph,
    # not an agent loop) — see `WorkerDefinition.toolset_version`'s own
    # docstring — and is exempt from this check rather than required to
    # resolve to nothing.
    pinned = {(t["toolset_id"], t["version"]) for t in manifest["toolsets"]}
    assert {
        (w["worker_id"], w["toolset_version"])
        for w in manifest["workers"]
        if w["toolset_version"] is not None
    } <= pinned
    # Same for the prompt an agent-loop worker pins: a run's spend is recorded
    # under it, so it must be a prompt this release ships.
    bundled = {(p["prompt_id"], p["version"]) for p in manifest["prompt_bundles"]}
    assert {
        (w["agent_prompt_id"], w["agent_prompt_version"])
        for w in manifest["workers"]
        if w["agent_prompt_id"] is not None
    } <= bundled
    assert {d["dataset_id"] for d in manifest["eval_datasets"]} >= {"platform_smoke"}


def test_manifest_ref_is_stable_and_ignores_git_sha() -> None:
    script = _load_script()
    manifest = script.build_manifest()
    ref = script.manifest_ref(manifest)
    assert ref.startswith("sha256:") and len(ref) == 7 + 64
    assert ref == script.manifest_ref({**manifest, "git_sha": "different"})
    changed = {**manifest, "knowledge_index_version": "changed"}
    assert ref != script.manifest_ref(changed)

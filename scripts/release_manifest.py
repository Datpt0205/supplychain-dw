"""Generate the immutable release manifest.

Collects every versioned artifact — platform, API, workers, graphs, prompt
bundles, toolsets, policies, knowledge index, eval datasets — plus the git SHA,
canonicalizes to JSON and derives a content-addressed reference:

    sha256:<hex>          → contracts/release/manifest.json
                            contracts/release/manifest.ref
                            contracts/release/history/manifest-<hex12>.json

Worker runs persist this reference (release_manifest_ref) so any run can be
traced back to the exact artifact set that produced it.

Usage:
    uv run python scripts/release_manifest.py [--check]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
RELEASE_DIR = REPO_ROOT / "contracts" / "release"


def _git_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _project_version(pyproject: Path) -> str:
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    version = data.get("project", {}).get("version", "0.0.0")
    return str(version)


def _workers() -> list[dict[str, Any]]:
    workers = []
    for path in sorted((REPO_ROOT / "configs" / "workers").glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        workers.append(
            {
                "worker_id": raw["worker_id"],
                "worker_version": raw["worker_version"],
                "graph_version": raw["graph_version"],
                "prompt_bundle_version": raw["prompt_bundle_version"],
                # None for a worker that calls no tool — see contracts.py's
                # own field docstring; the invariant below only holds workers
                # that declare one to a real pin.
                "toolset_version": raw.get("toolset_version"),
                # The registry prompt an agent loop renders and is billed
                # under; None for a plain graph. Resolves to an entry of
                # `prompt_bundles`, like toolset_version to `toolsets`.
                "agent_prompt_id": raw.get("agent_prompt_id"),
                "agent_prompt_version": raw.get("agent_prompt_version"),
                "policy_version": raw["policy_version"],
                "memory_policy_version": raw["memory_policy_version"],
                "autonomy_level": raw.get("autonomy_level", "A2"),
            }
        )
    return workers


def _prompt_bundles() -> list[dict[str, str]]:
    bundles = []
    for path in sorted((REPO_ROOT / "configs" / "prompts").rglob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        bundles.append(
            {
                "prompt_id": raw["prompt_id"],
                "version": raw["version"],
                "checksum": _checksum(path),
            }
        )
    return bundles


def _policies() -> list[dict[str, str]]:
    policies = []
    for path in sorted((REPO_ROOT / "configs" / "policies").glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        policies.append(
            {
                "policy_id": str(raw.get("policy_id", path.stem)),
                "version": str(raw.get("policy_version", raw.get("version", "1.0.0"))),
                "checksum": _checksum(path),
            }
        )
    return policies


def _copy_bundles() -> list[dict[str, str]]:
    """Platform copy the model and approvers read, pinned by content.

    Same reason as ``_tool_specs``: these words reach a model, so changing them
    has to move the manifest ref even though no Python changed.
    """
    bundles = []
    for path in sorted((REPO_ROOT / "configs" / "copy").rglob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        bundles.append(
            {
                "copy_id": raw["copy_id"],
                "version": raw["version"],
                "checksum": _checksum(path),
            }
        )
    return bundles


def _tool_specs() -> list[dict[str, str]]:
    """Every tool spec a host can load, pinned by content.

    Reads the specs rather than a running registry so the manifest is derivable
    from the repo alone, and so changing a tool's policy or its description moves
    the manifest ref.
    """
    tools = []
    for path in sorted((REPO_ROOT / "configs" / "tools").rglob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        tools.append(
            {
                "name": raw["name"],
                "version": raw["version"],
                "side_effect_level": raw["side_effect_level"],
                "approval_policy": raw["approval_policy"],
                "checksum": _checksum(path),
            }
        )
    return tools


def _toolsets() -> list[dict[str, Any]]:
    """Which of those specs each worker version actually offers.

    Separate from ``tool_specs`` because a superseded spec stays loadable
    without reaching a model; this is the list a run's ``toolset_version``
    resolves to.
    """
    toolsets = []
    for path in sorted((REPO_ROOT / "configs" / "toolsets").rglob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        toolsets.append(
            {
                "toolset_id": raw["toolset_id"],
                "version": raw["version"],
                "tools": [f"{pin['name']}@{pin['version']}" for pin in raw["tools"]],
                "checksum": _checksum(path),
            }
        )
    return toolsets


def _rubrics() -> list[dict[str, str]]:
    """Scoring rubrics and the company thresholds they are read against.

    These decide what a lead is worth, and they are data a business team edits
    without touching Python — exactly the artifact a release has to be able to
    name. A run records the rubric version it used on its own row, but until
    this section existed, republishing a rubric moved no manifest ref at all,
    so two releases scoring the same lead differently looked identical here.

    Both files sit under configs/rubrics and both carry ``version``; the id key
    differs, hence the fallback.
    """
    rubrics = []
    for path in sorted((REPO_ROOT / "configs" / "rubrics").glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        rubrics.append(
            {
                "artifact": path.stem.split("@", 1)[0],
                "id": str(raw.get("rubric_id") or raw["benchmarks_id"]),
                "version": raw["version"],
                "checksum": _checksum(path),
            }
        )
    return rubrics


def _eval_datasets() -> list[dict[str, str]]:
    datasets = []
    for path in sorted((REPO_ROOT / "evals" / "datasets").glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        datasets.append(
            {
                "dataset_id": raw["dataset_id"],
                "version": raw["dataset_version"],
                "checksum": _checksum(path),
            }
        )
    return datasets


def build_manifest() -> dict[str, Any]:
    from dw_agent_runtime.autonomy import AUTONOMY_POLICY_VERSION
    from dw_knowledge.gateway import INDEX_VERSION
    from dw_memory.policy import MemoryWritePolicy

    return {
        "schema_version": "1.0",
        "platform_version": _project_version(REPO_ROOT / "pyproject.toml"),
        "git_sha": _git_sha(),
        "api_version": _project_version(REPO_ROOT / "apps" / "api" / "pyproject.toml"),
        "workers": _workers(),
        "prompt_bundles": _prompt_bundles(),
        "copy_bundles": _copy_bundles(),
        "tool_specs": _tool_specs(),
        "toolsets": _toolsets(),
        "rubrics": _rubrics(),
        "policies": _policies(),
        "knowledge_index_version": INDEX_VERSION,
        "memory_policy_version": MemoryWritePolicy().policy_version,
        # What turns a run's autonomy level into approve/do-not-approve. A run
        # stamps this on its own row and the policy refuses to decide for a run
        # stamped under a version it does not have — so the version is already
        # load-bearing at runtime, and a release that changes it has to be
        # identifiable afterwards. Same reason as the two lines above: a constant
        # in code is still a versioned artifact if a run's behaviour depends on it.
        "approval_policy_version": AUTONOMY_POLICY_VERSION,
        "eval_datasets": _eval_datasets(),
    }


def _checksum(path: Path) -> str:
    """Hash what the file says, not how the checkout wrote its line endings.

    Every artifact here is YAML or JSON, and `.gitattributes` pins the tree to
    `eol=lf` - but a Windows tool that writes through Python's text mode emits
    CRLF, so the same committed content hashed to two different values depending
    on who ran the generator. CI then failed `--check` for a file nobody had
    edited. Normalising makes the ref a property of the artifact set, which is
    what it is documented to be.
    """
    normalised = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(normalised).hexdigest()


def manifest_ref(manifest: dict[str, Any]) -> str:
    """Content-addressed over the artifact set. ``git_sha`` is informational —
    excluding it keeps the ref stable across commits that change no artifact."""
    hashable = {k: v for k, v in manifest.items() if k != "git_sha"}
    canonical = json.dumps(hashable, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def write_manifest(manifest: dict[str, Any]) -> tuple[Path, str]:
    ref = manifest_ref(manifest)
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    (RELEASE_DIR / "history").mkdir(exist_ok=True)

    payload = json.dumps(manifest, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    current = RELEASE_DIR / "manifest.json"
    current.write_text(payload, encoding="utf-8")
    (RELEASE_DIR / "manifest.ref").write_text(ref + "\n", encoding="utf-8")

    # History entries are immutable: the artifact set only (no git_sha), keyed
    # by its own hash — re-releasing identical artifacts is a no-op.
    hashable = {k: v for k, v in manifest.items() if k != "git_sha"}
    history_payload = json.dumps(hashable, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    digest12 = ref.removeprefix("sha256:")[:12]
    history = RELEASE_DIR / "history" / f"manifest-{digest12}.json"
    if history.exists() and history.read_text(encoding="utf-8") != history_payload:
        raise SystemExit(f"ERROR: manifest history collision for {history.name}")
    history.write_text(history_payload, encoding="utf-8")
    return current, ref


def main() -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="verify the committed manifest matches the repo"
    )
    args = parser.parse_args()

    manifest = build_manifest()
    ref = manifest_ref(manifest)

    if args.check:
        ref_file = RELEASE_DIR / "manifest.ref"
        if not ref_file.exists():
            print("ERROR: contracts/release/manifest.ref missing — run make release-manifest")
            return 1
        committed = ref_file.read_text(encoding="utf-8").strip()
        if committed != ref:
            print(f"ERROR: manifest is stale.\n  committed: {committed}\n  actual:    {ref}")
            print("Run: make release-manifest")
            return 1
        print(f"OK: release manifest up to date ({ref})")
        return 0

    path, ref = write_manifest(manifest)
    print(f"wrote {path.relative_to(REPO_ROOT)}")
    print(f"release_manifest_ref = {ref}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

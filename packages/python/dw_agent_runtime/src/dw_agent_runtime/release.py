"""The release a process serves, as every run it starts records it.

`make release-manifest` writes `contracts/release/manifest.ref` (the
content-addressed manifest pinning every worker, graph, prompt, tool, policy,
event schema and eval dataset). A run stamps it on its row so a result traces
back to the exact artifact set that produced it. Both processes that start
runs, the API and the worker, read it here: one reader, so the two cannot
stamp the same release differently.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["UNRELEASED", "release_manifest_ref"]

# What a development tree with no generated manifest records.
UNRELEASED = "unreleased"

_REF = Path("contracts") / "release" / "manifest.ref"


def release_manifest_ref(repo_root: Path) -> str:
    """The manifest ref under `repo_root`, or `UNRELEASED` when the file is
    absent. A deployed process must not accept the fallback; its composition
    root refuses it (the image ships `contracts/release`)."""
    path = repo_root / _REF
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return UNRELEASED

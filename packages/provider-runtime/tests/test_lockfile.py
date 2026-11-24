"""uv.lock records the workspace member at its pyproject version (#12).

`uv run --frozen` ignores the member's version, so a stale lock goes
unnoticed; this check fails the suite (and the release gate) instead.
Refresh with `uv lock` in its own commit.
"""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_uv_lock_matches_the_package_version():
    project = tomllib.loads((ROOT / "packages/provider-runtime/pyproject.toml").read_text())["project"]
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    locked = [p for p in lock["package"] if p["name"] == project["name"]]
    assert len(locked) == 1, locked
    assert locked[0]["version"] == project["version"], (
        f"uv.lock pins {project['name']} {locked[0]['version']}, pyproject says "
        f"{project['version']}: run `uv lock`")

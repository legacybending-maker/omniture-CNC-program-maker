"""Golden-output tests: the complete program for every example and golden job.

Each job in examples/ and tests/golden/jobs/ is posted with the default machine
profile and compared, line for line, with tests/golden/expected/<name>.cnc.
Post warnings are compared with <name>.warn (missing file = no warnings).

These files are the record of what the post actually sends to the machine, so
any change to G-code output shows up as a diff in the pull request.

When a change to the output is intended:

    UPDATE_GOLDEN=1 python -m pytest tests/test_golden.py

then review the diff of tests/golden/expected/ line by line and say in the PR
that the output changed and why. Never regenerate them just to make a test pass.
"""
from __future__ import annotations

import difflib
import os
import tomllib
from pathlib import Path

import pytest

from omniturn_cam.job import load_job
from omniturn_cam.ops import OPERATION_TYPES
from omniturn_cam.post import MachineConfig, post
from omniturn_cam.tools import ToolLibrary

ROOT = Path(__file__).parent.parent
EXAMPLES = ROOT / "examples"
GOLDEN = Path(__file__).parent / "golden"
EXPECTED = GOLDEN / "expected"
UPDATE = os.environ.get("UPDATE_GOLDEN") == "1"


def _is_job(path: Path) -> bool:
    return "job" in tomllib.loads(path.read_text())


JOBS = sorted(p for p in [*EXAMPLES.glob("*.toml"), *(GOLDEN / "jobs").glob("*.toml")] if _is_job(p))


@pytest.fixture(scope="module")
def lib():
    return ToolLibrary.load(EXAMPLES / "tools.toml")


def _post(path: Path, lib) -> tuple[str, list[str]]:
    job = load_job(path, lib)
    # Always the built-in machine profile: never the developer's ~/.config one.
    warns: list[str] = []
    code = post(job, MachineConfig.for_units(job.units), warnings=warns)
    return code, warns


def _compare(path: Path, actual: str, what: str) -> None:
    if UPDATE:
        if actual:
            path.write_text(actual)
        elif path.exists():
            path.unlink()
        return
    expected = path.read_text() if path.exists() else ""
    if actual != expected:
        diff = "".join(difflib.unified_diff(expected.splitlines(True), actual.splitlines(True),
                                            f"expected/{path.name}", "now", n=2))
        pytest.fail(f"{what} changed. If this is intended, run UPDATE_GOLDEN=1 python -m pytest "
                    f"tests/test_golden.py, review the diff and say so in the PR.\n\n{diff}")


@pytest.mark.parametrize("path", JOBS, ids=[p.stem for p in JOBS])
def test_program_matches_golden(path, lib):
    code, warns = _post(path, lib)
    _compare(EXPECTED / f"{path.stem}.cnc", code, f"G-code for {path.name}")
    _compare(EXPECTED / f"{path.stem}.warn", "".join(w + "\n" for w in warns), f"post warnings for {path.name}")


def test_golden_jobs_cover_every_operation(lib):
    """A new operation type needs a golden job that uses it."""
    used = {type(op) for path in JOBS for op in load_job(path, lib).operations}
    missing = sorted(kind for kind, cls in OPERATION_TYPES.items() if cls not in used)
    assert not missing, f"add these operation types to a job in tests/golden/jobs/: {', '.join(missing)}"


def test_golden_job_names_are_unique():
    stems = [p.stem for p in JOBS]
    assert len(stems) == len(set(stems)), "examples/ and tests/golden/jobs/ share a file name"


def test_no_orphan_expected_files():
    stems = {p.stem for p in JOBS}
    orphans = sorted(p.name for p in EXPECTED.iterdir() if p.stem not in stems)
    assert not orphans, f"expected output with no job (delete them): {', '.join(orphans)}"


def test_machine_verified_programs_unchanged():
    """Programs proved out on the lathe (tests/golden/verified.toml) must not change silently."""
    import hashlib
    verified = tomllib.loads((GOLDEN / "verified.toml").read_text())
    stems = {p.stem for p in JOBS}
    problems = []
    for name, entry in verified.items():
        if name not in stems:
            problems.append(f"{name}: listed as machine-verified but there is no such golden job")
            continue
        digest = hashlib.sha256((EXPECTED / f"{name}.cnc").read_bytes()).hexdigest()
        if digest != entry["sha256"]:
            problems.append(f"{name}: output differs from the program proved out on the machine "
                            f"{entry.get('date', '')}. If intended, remove its entry from "
                            "tests/golden/verified.toml in this PR and say so; it must be re-proved.")
    assert not problems, "\n".join(problems)

"""Job file format: versioning and round trips. Old job files must keep loading."""

from pathlib import Path

import pytest

from omniturn_cam.job import JOB_FORMAT, load_job, save_job
from omniturn_cam.post import MachineConfig, post
from omniturn_cam.tools import ToolLibrary

ROOT = Path(__file__).parent.parent
DATA = Path(__file__).parent / "data" / "jobs"


@pytest.fixture
def lib():
    return ToolLibrary.load(ROOT / "examples" / "tools.toml")


def test_saved_job_records_format(lib, tmp_path):
    job = load_job(ROOT / "examples" / "manual_g75.toml", lib)
    save_job(job, tmp_path / "j.toml")
    assert f"format = {JOB_FORMAT}" in (tmp_path / "j.toml").read_text()


def test_file_without_format_is_format_1(lib):
    """Files saved before the format key existed (a frozen copy in tests/data/jobs/)."""
    job = load_job(DATA / "format1_no_version.toml", lib)
    assert job.name == "MANG75"


def test_newer_format_refused_with_clear_message(lib, tmp_path):
    src = (ROOT / "examples" / "manual_g75.toml").read_text()
    newer = tmp_path / "new.toml"
    newer.write_text(src.replace("[job]\n", f"[job]\nformat = {JOB_FORMAT + 1}\n", 1))
    with pytest.raises(ValueError, match="newer omniturn-cam"):
        load_job(newer, lib)


def test_save_and_reload_posts_identical_program(lib, tmp_path):
    """Saving a job must never change the program it produces."""
    for path in [*(ROOT / "examples").glob("*.toml"), *(Path(__file__).parent / "golden" / "jobs").glob("*.toml")]:
        if "[job]" not in path.read_text():
            continue
        job = load_job(path, lib)
        m = MachineConfig.for_units(job.units)
        save_job(job, tmp_path / path.name)
        again = load_job(tmp_path / path.name, lib)
        assert post(again, m) == post(job, m), path.name

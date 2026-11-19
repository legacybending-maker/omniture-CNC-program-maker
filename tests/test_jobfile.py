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


# --------------------------------------------------------------------- include file path (issue #38)
# A relative include_subs used to be turned into an absolute path on load and
# written back that way, so a job copied to another computer still pointed
# into the first one's folders.
import shutil  # noqa: E402

EX = ROOT / "examples"


def _job_with_subs(lib, folder: Path, include: str):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "subs").mkdir(exist_ok=True)
    (folder / "subs" / "loader.txt").write_text("}1 (LOADER)\nm10\nm99\n")
    job = load_job(EX / "manual_g75.toml", lib)
    job.include_subs = include
    job.base_dir = folder
    save_job(job, folder / "part.toml")
    return folder / "part.toml"


def test_relative_include_path_is_saved_as_written(lib, tmp_path):
    path = _job_with_subs(lib, tmp_path / "a", "subs/loader.txt")
    assert 'include_subs = "subs/loader.txt"' in path.read_text()
    job = load_job(path, lib)
    assert job.include_subs == "subs/loader.txt" and job.include_path == path.parent / "subs" / "loader.txt"
    save_job(job, path)  # load -> save keeps it relative
    assert 'include_subs = "subs/loader.txt"' in path.read_text()


def test_job_moved_with_its_subroutine_folder_still_finds_it(lib, tmp_path):
    """Copy the whole folder somewhere else (another computer, a USB stick):
    the job opens and posts, with the subroutine file from the new place."""
    path = _job_with_subs(lib, tmp_path / "a", "subs/loader.txt")
    moved = tmp_path / "elsewhere" / "jobs"
    shutil.copytree(path.parent, moved)
    shutil.rmtree(path.parent)  # the original is gone
    code = post(load_job(moved / "part.toml", lib))
    assert "}1 (LOADER)" in code


def test_save_as_to_another_folder_keeps_pointing_at_the_same_file(lib, tmp_path):
    """Saving into a different folder rewrites a relative path from the new
    folder, so it still names the same subroutine file (keeping the old text
    would silently pick up a different file, or none)."""
    path = _job_with_subs(lib, tmp_path / "a", "subs/loader.txt")
    job = load_job(path, lib)
    (tmp_path / "b").mkdir()
    save_job(job, tmp_path / "b" / "copy.toml")
    assert job.include_subs == "../a/subs/loader.txt" and job.base_dir == tmp_path / "b"
    again = load_job(tmp_path / "b" / "copy.toml", lib)
    assert again.include_path.resolve() == (tmp_path / "a" / "subs" / "loader.txt").resolve()


def test_absolute_include_path_from_old_jobs_still_loads(lib, tmp_path):
    """Jobs saved before #38 hold an absolute path: it's kept and used as is."""
    subs = tmp_path / "loader.txt"
    subs.write_text("}1\nm10\nm99\n")
    path = _job_with_subs(lib, tmp_path / "a", str(subs))
    job = load_job(path, lib)
    assert job.include_subs == str(subs) and job.include_path == subs
    save_job(job, tmp_path / "a" / "again.toml")
    assert f'include_subs = "{subs}"' in (tmp_path / "a" / "again.toml").read_text()


def test_failed_save_changes_nothing_in_the_job(lib, tmp_path):
    path = _job_with_subs(lib, tmp_path / "a", "subs/loader.txt")
    job = load_job(path, lib)
    blocker = tmp_path / "file"
    blocker.write_text("")
    with pytest.raises(OSError):
        save_job(job, blocker / "copy.toml")
    assert job.include_subs == "subs/loader.txt" and job.base_dir == tmp_path / "a"


def test_missing_include_file_names_where_it_looked(lib, tmp_path):
    path = _job_with_subs(lib, tmp_path / "a", "subs/loader.txt")
    (path.parent / "subs" / "loader.txt").unlink()
    with pytest.raises(ValueError, match=r"subs/loader.txt \(looked for .*a/subs/loader.txt\) not found"):
        load_job(path, lib)


# --------------------------------------------------------------------- malformed files (issue #38)
HEAD = '[job]\nname = "BAD"\n[stock]\ndiameter = 1.0\n'


@pytest.mark.parametrize(
    "text, words",
    [
        ("op = [1]\n" + HEAD, "operation 1 must be a table"),
        ('op = "drill"\n' + HEAD, "operation must be a list"),
        ("station = [1]\n" + HEAD, "station 1 must be a table"),
        ("profile = 3\n" + HEAD, "[profile] must be a table"),
        (HEAD + "[profile.od]\nstart = { x = 0, z = 0 }\nseg = [[1, 2]]\n", "shape 'od' segment 1 must be a table"),
        (HEAD.replace("[stock]\n", "") + "stock = 5\n", "no [stock] diameter"),
        (HEAD + "[profile]\nod = 4\n", "shape 'od' must be a table"),
        ('[job]\nname = "X"\ninclude_subs = 5\n[stock]\ndiameter = 1.0\n', "include_subs is 5"),
    ],
)
def test_malformed_structure_is_a_value_error_naming_the_place(lib, tmp_path, text, words):
    """These raised TypeError or AttributeError (a list where a table goes),
    which the GUI took for a bug. Now they say where the file is wrong."""
    path = tmp_path / "bad.toml"
    path.write_text(text)
    with pytest.raises(ValueError) as e:
        load_job(path, lib, validate=False)
    assert words in str(e.value)


def test_binary_file_is_a_value_error(lib, tmp_path):
    path = tmp_path / "bad.toml"
    path.write_bytes(b"\xff\xfe[job]")
    with pytest.raises(ValueError, match="isn't text"):
        load_job(path, lib)


def test_folder_is_an_os_error(lib, tmp_path):
    """Not the file's contents: the GUI says "could not read", not "fix the file"."""
    with pytest.raises(OSError):
        load_job(tmp_path, lib)

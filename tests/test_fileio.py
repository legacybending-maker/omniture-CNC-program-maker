"""fileio.write_text_atomic: a failed write never leaves a half-written file.

Job files, tool libraries and programs go to USB sticks that get pulled out
and disks that fill up (issue #38). Each test makes the write fail at a
different point and checks the old file is untouched and nothing is left over.
"""

from __future__ import annotations

import os

import pytest

from omniturn_cam import fileio
from omniturn_cam.fileio import write_text_atomic


def _files(folder):
    return sorted(p.name for p in folder.iterdir())


def test_writes_new_and_replaces_existing(tmp_path):
    p = tmp_path / "a.toml"
    write_text_atomic(p, "one\n")
    write_text_atomic(p, "two\n")
    assert p.read_text() == "two\n" and _files(tmp_path) == ["a.toml"]


def test_dos_line_endings_for_programs(tmp_path):
    p = tmp_path / "PART.cnc"
    write_text_atomic(p, "g70\nm30\n", newline="\r\n")
    assert p.read_bytes() == b"g70\r\nm30\r\n"


@pytest.mark.parametrize("fail_at", ["write", "fsync", "replace"])
def test_failure_leaves_old_file_and_no_temporary_file(tmp_path, monkeypatch, fail_at):
    """Whether the write, the flush to disk or the final rename fails, the
    file under the real name is still the old one, whole."""
    p = tmp_path / "job.toml"
    p.write_text("old")

    def boom(*a, **k):
        raise OSError(28, "No space left on device")

    if fail_at == "write":
        monkeypatch.setattr(fileio.os, "fdopen", lambda fd, mode: _Failing(fd))
    elif fail_at == "fsync":
        monkeypatch.setattr(fileio.os, "fsync", boom)
    else:
        monkeypatch.setattr(fileio.os, "replace", boom)
    with pytest.raises(OSError):
        write_text_atomic(p, "new")
    assert p.read_text() == "old" and _files(tmp_path) == ["job.toml"]


class _Failing:
    """A file object whose write fails like a full disk."""

    def __init__(self, fd):
        self.fd = fd

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        os.close(self.fd)

    def write(self, data):
        raise OSError(28, "No space left on device")


def test_missing_folder_is_an_os_error_and_writes_nothing(tmp_path):
    with pytest.raises(FileNotFoundError):
        write_text_atomic(tmp_path / "gone" / "a.toml", "x")
    assert _files(tmp_path) == []


def test_keeps_the_old_files_permissions(tmp_path):
    p = tmp_path / "a.toml"
    p.write_text("old")
    p.chmod(0o640)
    write_text_atomic(p, "new")
    assert oct(p.stat().st_mode & 0o777) == oct(0o640)

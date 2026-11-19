"""Writing files so that a failure can't leave a half-written one behind.

Job files, tool libraries and programs are written to USB sticks and shop
laptops, where a stick gets pulled out, a disk fills up, or a folder turns
out to be read-only. A plain ``path.write_text(...)`` opens the real file,
empties it, then writes: fail in the middle and the old file is gone and the
new one is half there.

``write_text_atomic`` writes the new contents to a temporary file *next to*
the real one, makes sure they have reached the disk, and only then swaps the
temporary file into place with ``os.replace``. That swap is a single rename:
the file under the real name is either the old one or the complete new one,
never a mixture (on the same drive, which is why the temporary file goes in
the same folder). If anything fails, the temporary file is removed and the
old file is untouched.

This is the standard "write, flush, fsync, rename" pattern. It can't make a
cheap USB stick reliable if it's pulled out a moment later, so callers don't
add a read-back "verification" on top: it would only prove the data is in
the computer's cache, not on the stick.
"""

from __future__ import annotations

import contextlib
import os
import stat
import tempfile
from pathlib import Path


def write_bytes_atomic(path: str | Path, data: bytes) -> None:
    """Replace ``path`` with ``data`` all at once (see the module docstring).

    Keeps the old file's permission bits when there is one. Raises OSError
    (FileNotFoundError for a missing folder, PermissionError, a full disk...)
    with the old file unchanged.
    """
    path = Path(path)
    # mkstemp makes a new, uniquely named file and opens it, in one step, so
    # two saves at once can't pick the same temporary name. The leading dot
    # hides it from most file browsers while it exists.
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())  # from Python's buffer to the OS, then the OS to the disk
        with contextlib.suppress(FileNotFoundError):
            os.chmod(tmp, stat.S_IMODE(path.stat().st_mode))
        os.replace(tmp, path)
    except BaseException:
        # BaseException, not Exception, so even Ctrl+C mid-write cleans up;
        # the bare `raise` then passes the original error on unchanged.
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    _sync_folder(path.parent)


def write_text_atomic(path: str | Path, text: str, newline: str = "\n") -> None:
    """``write_bytes_atomic`` for text: UTF-8, with ``newline`` for each
    line end ("\\r\\n" for programs the OmniTurn reads, p2.8)."""
    if newline != "\n":
        text = text.replace("\n", newline)
    write_bytes_atomic(path, text.encode("utf-8"))


def _sync_folder(folder: Path) -> None:
    """Ask the OS to put the rename itself on disk too. Best effort: Windows
    and some filesystems (FAT on USB sticks among them) can't open a folder
    for this, and the file is already safely written either way."""
    try:
        fd = os.open(folder, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)

"""Command line entry point.

omniturn-cam post JOB.toml --tools TOOLS.toml [-o OUT.cnc] [--plot OUT.png]
omniturn-cam lint PROGRAM.cnc
omniturn-cam plot PROGRAM.cnc OUT.png
omniturn-cam gui [JOB.toml]
omniturn-cam step PART.step|.stl|.3mf [--flip] [--stock DIA] [--into JOB.toml]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .job import load_job
from .lint import lint
from .post import MachineConfig, PostError, post
from .tools import ToolLibrary

DEFAULT_MACHINE = Path.home() / ".config" / "omniturn-cam" / "machine.toml"


def machine_for(explicit: str | None, units: str, default: Path = DEFAULT_MACHINE) -> tuple[MachineConfig, str]:
    """The machine profile for a post run, and where it came from (for the
    operator).

    | --machine given? | file exists? | result                                  |
    |------------------|--------------|-----------------------------------------|
    | no               | no           | built-in Hardinge HLV-H profile         |
    | no               | yes          | that file; any problem in it is fatal   |
    | yes              | yes          | that file; any problem in it is fatal   |
    | yes              | no           | fatal                                   |

    An operator who names a machine must never get code for a different one,
    so the built-in profile is used only when nothing was asked for and no
    personal profile exists. Raises MachineConfigError (a ValueError).

    ``default`` is a parameter so tests can point it at a temporary folder
    instead of the real home directory (dependency injection: hand a
    function what it depends on rather than have it reach out for it).
    """
    from .post.omniturn import MachineConfigError
    from .validation import Issue

    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise MachineConfigError(
                [Issue(str(path), "doesn't exist (given with --machine)", "Check the path; nothing was written")]
            )
        return MachineConfig.load(path, units), str(path)
    if default.exists():
        return MachineConfig.load(default, units), str(default)
    return MachineConfig.for_units(units), "built-in default (Hardinge HLV-H OmniTurn G4)"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="omniturn-cam", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("post", help="generate an OmniTurn program from a job file")
    p.add_argument("job")
    p.add_argument("--tools", required=True, help="tool library TOML")
    p.add_argument("-o", "--output", help="program file (default: JOBNAME.cnc next to the job)")
    p.add_argument("--plot", help="also write a backplot PNG")
    p.add_argument(
        "--machine",
        help="machine profile TOML (default: ~/.config/omniturn-cam/machine.toml "
        "if present, else Hardinge HLV-H OmniTurn G4). A file given here that is "
        "missing or wrong stops the post",
    )
    p.add_argument("--stdout", action="store_true", help="print the program instead of writing it")

    l = sub.add_parser("lint", help="check a program against the OmniTurn formatting rules")
    l.add_argument("program")

    b = sub.add_parser("plot", help="backplot an OmniTurn program to PNG")
    b.add_argument("program")
    b.add_argument("png")

    g = sub.add_parser("gui", help="open the fill-in-the-blank programming screens")
    g.add_argument("job", nargs="?")

    st = sub.add_parser("step", help="import OD/ID shapes from a STEP, STL or 3MF file")
    st.add_argument("step")
    st.add_argument("--solid", type=int, help="solid number when the file has several (1, 2, ...)")
    st.add_argument("--flip", action="store_true", help="put Z0 at the other end of the part")
    st.add_argument("--units", choices=["inch", "metric"], default="inch")
    st.add_argument("--stock", type=float, help="bar diameter: extend the OD shape out to it")
    st.add_argument("--into", help="add the shapes to this job file (replacing shapes with the same names)")

    a = ap.parse_args(argv)

    if a.cmd == "step":
        return _step(a)

    if a.cmd == "gui":
        from .gui.app import main as gui_main

        return gui_main(["omniturn-cam"] + ([a.job] if a.job else []))

    if a.cmd == "post":
        # Everything that reads a file the operator gave is inside one try, so
        # any problem ends with "error: ..." and exit code 2 instead of a
        # Python traceback. KeyError's message has quotes around it (that's
        # how str() of a KeyError looks), hence the strip.
        try:
            lib = ToolLibrary.load(a.tools)
            job = load_job(a.job, lib)
            machine, source = machine_for(a.machine, job.units)
        except OSError as e:
            print(f"error: can't read {e.filename}: {e.strerror}", file=sys.stderr)
            return 2
        except (KeyError, ValueError) as e:
            msg = str(e).strip('"')
            print(f"error: {msg}", file=sys.stderr)
            return 2
        print(f"machine profile: {source}", file=sys.stderr)
        warns: list[str] = []
        try:
            code = post(job, machine, warnings=warns)
        except PostError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        for w in warns:
            print(f"warning: {w}", file=sys.stderr)
        if a.stdout:
            sys.stdout.write(code)
        else:
            out = Path(a.output) if a.output else Path(a.job).with_name(job.name + ".cnc")
            out.write_bytes(code.replace("\n", "\r\n").encode("ascii"))  # DOS line endings
            print(f"wrote {out}")
        if a.plot:
            from .backplot import plot

            plot(code, a.plot, job.stock.diameter, f"{job.name}  {job.description}", machine.arc_codes)
            print(f"wrote {a.plot}")
        return 0

    if a.cmd == "lint":
        probs = lint(Path(a.program).read_text(errors="replace"), a.program)
        for pr in probs:
            print(pr)
        errors = sum(pr.severity == "error" for pr in probs)
        print(f"{errors} error(s), {len(probs) - errors} warning(s)")
        return 1 if errors else 0

    if a.cmd == "plot":
        from .backplot import ProgramError, plot

        # A hand-written file can be anything. Refuse what can't be read with
        # one "error:" line and exit code 2, like `post`, instead of a Python
        # traceback (issue #36). errors="replace" turns bytes that aren't
        # text into a marker character, which the interpreter then refuses
        # with the line number, rather than failing to open the file at all.
        try:
            plot(Path(a.program).read_text(errors="replace"), a.png)
        except OSError as e:
            print(f"error: can't read {e.filename}: {e.strerror}", file=sys.stderr)
            return 2
        except ProgramError as e:
            print(f"error: {a.program}: {e}", file=sys.stderr)
            return 2
        return 0
    return 1


def _step(a) -> int:
    from .job import merge_profiles, profiles_toml
    from .modelimport import import_model, list_bodies
    from .stepimport import StepImportError, extend_to_stock

    try:
        if a.solid is None:
            solids = list_bodies(a.step)
            if len(solids) > 1:
                print(f"{a.step} has {len(solids)} solids - pick one with --solid N:", file=sys.stderr)
                for s in solids:
                    dims = " x ".join(f"{v:.1f}" for v in s.size)
                    print(f"  {s.index + 1}: {s.name}  {dims} mm  volume {s.volume:.0f} mm3", file=sys.stderr)
                return 2
        r = import_model(a.step, None if a.solid is None else a.solid - 1, a.units, a.flip)
    except StepImportError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    for w in r.warnings:
        print(f"warning: {w}", file=sys.stderr)
    stock = a.stock or r.max_dia
    extend_to_stock(r.od, stock)
    profiles = {"od": r.od} | ({"id": r.id} if r.id else {})
    print(f"# {Path(a.step).name}: {r.max_dia:g} max dia x {r.length:g} long ({a.units})", file=sys.stderr)
    if a.into:
        merge_profiles(a.into, profiles)
        print(f"added {', '.join(profiles)} to {a.into}", file=sys.stderr)
    else:
        sys.stdout.write(profiles_toml(profiles))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Command line entry point.

  omniturn-cam post JOB.toml --tools TOOLS.toml [-o OUT.cnc] [--plot OUT.png]
  omniturn-cam lint PROGRAM.cnc
  omniturn-cam plot PROGRAM.cnc OUT.png
  omniturn-cam gui [JOB.toml]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .job import load_job
from .lint import lint
from .post import PostError, post
from .tools import ToolLibrary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="omniturn-cam", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("post", help="generate an OmniTurn program from a job file")
    p.add_argument("job")
    p.add_argument("--tools", required=True, help="tool library TOML")
    p.add_argument("-o", "--output", help="program file (default: JOBNAME.cnc next to the job)")
    p.add_argument("--plot", help="also write a backplot PNG")
    p.add_argument("--stdout", action="store_true", help="print the program instead of writing it")

    l = sub.add_parser("lint", help="check a program against the OmniTurn formatting rules")
    l.add_argument("program")

    b = sub.add_parser("plot", help="backplot an OmniTurn program to PNG")
    b.add_argument("program")
    b.add_argument("png")

    g = sub.add_parser("gui", help="open the fill-in-the-blank programming screens")
    g.add_argument("job", nargs="?")

    a = ap.parse_args(argv)

    if a.cmd == "gui":
        from .gui.app import main as gui_main
        return gui_main(["omniturn-cam"] + ([a.job] if a.job else []))

    if a.cmd == "post":
        lib = ToolLibrary.load(a.tools)
        job = load_job(a.job, lib)
        try:
            code = post(job)
        except PostError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        if a.stdout:
            sys.stdout.write(code)
        else:
            out = Path(a.output) if a.output else Path(a.job).with_name(job.name + ".cnc")
            out.write_bytes(code.replace("\n", "\r\n").encode("ascii"))  # DOS line endings
            print(f"wrote {out}")
        if a.plot:
            from .backplot import plot
            plot(code, a.plot, job.stock.diameter, f"{job.name}  {job.description}")
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
        from .backplot import plot
        plot(Path(a.program).read_text(), a.png)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())

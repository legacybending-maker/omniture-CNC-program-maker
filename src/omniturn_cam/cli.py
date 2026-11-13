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
import tomllib
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
    p.add_argument("--machine", help="machine profile TOML (default: ~/.config/omniturn-cam/machine.toml "
                                     "if present, else Hardinge HLV-H OmniTurn G4)")
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
        lib = ToolLibrary.load(a.tools)
        job = load_job(a.job, lib)
        from .post import MachineConfig
        mpath = Path(a.machine) if a.machine else Path.home() / ".config" / "omniturn-cam" / "machine.toml"
        machine = MachineConfig.load(mpath, job.units) if mpath.exists() else None
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


def _step(a) -> int:
    import tomli_w

    from .job import profile_to_dict
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
        data = tomllib.loads(Path(a.into).read_text())
        data.setdefault("profile", {}).update({k: profile_to_dict(v) for k, v in profiles.items()})
        Path(a.into).write_text("# omniturn-cam job file\n" + tomli_w.dumps(data))
        print(f"added {', '.join(profiles)} to {a.into}", file=sys.stderr)
    else:
        sys.stdout.write(tomli_w.dumps({"profile": {k: profile_to_dict(v) for k, v in profiles.items()}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

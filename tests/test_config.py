"""machine.toml, tools.toml and the CLI's choice of machine profile.

These files live on the shop laptop and are edited by hand. A typo in one
used to be skipped (unknown machine keys) or to reach arithmetic as the wrong
type (max_rpm = "4000"); an explicit --machine that didn't exist fell back to
the built-in machine. Every case here now stops with the file, the setting
and what to do.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omniturn_cam import cli
from omniturn_cam.post import MachineConfig
from omniturn_cam.post.omniturn import MachineConfigError
from omniturn_cam.tools import Tool, ToolLibrary
from omniturn_cam.validation import ValidationError

ROOT = Path(__file__).parent.parent
EX = ROOT / "examples"
DEFAULT = ROOT / "src" / "omniturn_cam" / "data" / "default_machine.toml"


def machine_file(tmp_path, text, name="machine.toml"):
    path = tmp_path / name
    path.write_text(text)
    return path


# --------------------------------------------------------------------- machine.toml
@pytest.mark.parametrize("units", ["inch", "metric"])
def test_shipped_machine_profiles_load(units):
    """The default and the example profile are the same file; both must load."""
    for path in (DEFAULT, EX / "machine.toml"):
        m = MachineConfig.load(path, units)
        assert m.units == units and m.max_rpm == 4000


def test_valid_unit_overrides_apply(tmp_path):
    path = machine_file(tmp_path, "max_rpm = 3000\n[inch]\nz_clear = 0.1\n[metric]\nz_clear = 2.5\n")
    assert MachineConfig.load(path, "inch").z_clear == 0.1
    assert MachineConfig.load(path, "metric").z_clear == 2.5
    assert MachineConfig.load(path, "metric").max_rpm == 3000


@pytest.mark.parametrize(
    "text, words",
    [
        # unknown keys and sections: used to be skipped silently
        ("max_rmp = 2500\n", ["max_rmp", "not a machine setting", "Did you mean max_rpm"]),
        ("[metric]\nmax_brr = 20\n", ["[metric]", "max_brr", "max_bar"]),
        ("[inhc]\nmax_bar = 1\n", ["[inhc]", "not a section"]),
        ("inch = 5\n", ["inch", "must be a table"]),
        ('units = "metric"\n', ["units", "comes from each job"]),
        # wrong types
        ('max_rpm = "4000"\n', ["max_rpm is '4000'", "must be a number"]),
        ("x_travel = true\n", ["x_travel is true", "must be a number"]),
        ("[inch]\ndecimals = 4.0\n", ["decimals", "whole number"]),
        ("name = 5\n", ["name", "must be text"]),
        # NaN / infinity (TOML can spell both)
        ("max_rpm = nan\n", ["max_rpm is nan", "finite"]),
        ("[inch]\nrapid_feed = inf\n", ["rapid_feed is inf", "finite"]),
        # ranges
        ("max_rpm = 0\n", ["max_rpm is 0", "greater than zero"]),
        ("[inch]\nrapid_feed = -300\n", ["rapid_feed", "greater than zero"]),
        ("[inch]\nrapid_feed = 400\n", ["rapid_feed", "fastest feed", "p2.11"]),
        ("[inch]\nz_clear = 0\n", ["z_clear", "greater than zero"]),
        ("[inch]\nx_clear = -0.05\n", ["x_clear"]),
        ("[inch]\ntool_call_z = 0.02\n", ["tool_call_z", "farther out than z_clear"]),
        ("[inch]\nmax_bar = -1\n", ["max_bar", "negative"]),
        ("[metric]\nz_travel = -228.6\n", ["[metric]", "z_travel"]),
        ("[inch]\ndecimals = 3\n", ["decimals is 3", "4 or more", "G4 Diagnostics"]),
        ("[metric]\ndecimals = 2\n", ["[metric]", "decimals is 2", "3 or more"]),
        ("feed_decimals = 0\n", ["feed_decimals", "1 or more"]),
        ('arc_format = "radius"\n', ["arc_format", "r or ik"]),
        ('[inch]\narc_codes = { ccw = "g02", cw = "g02" }\n', ["arc_codes", "one each"]),
        ('[inch]\narc_codes = { ccw = "g02" }\n', ["arc_codes"]),
        ("first_sub = 0\n", ["first_sub", "1 or more"]),
        ('name = "  "\n', ["name", "can't be empty"]),
        # not TOML at all
        ("max_rpm = \n", ["not valid TOML"]),
    ],
)
def test_bad_machine_files_stop_with_file_setting_and_problem(tmp_path, text, words):
    path = machine_file(tmp_path, text)
    with pytest.raises(MachineConfigError) as e:
        MachineConfig.load(path, "inch")
    msg = str(e.value)
    assert str(path) in msg
    for w in words:
        assert w in msg, msg


def test_a_problem_in_the_other_units_section_is_still_found(tmp_path):
    """A typo under [metric] stops an inch job too, rather than waiting for the
    first metric job to find it at the machine."""
    path = machine_file(tmp_path, "[metric]\nmax_rpm = -1\n")
    with pytest.raises(MachineConfigError, match=r"\[metric\]"):
        MachineConfig.load(path, "inch")


def test_every_problem_in_the_file_is_reported(tmp_path):
    path = machine_file(tmp_path, "max_rmp = 1\n[inch]\nz_clr = 1\n")
    with pytest.raises(MachineConfigError) as e:
        MachineConfig.load(path, "inch")
    assert len(e.value.issues) == 2


def test_unreadable_machine_file(tmp_path):
    with pytest.raises(MachineConfigError, match="can't be read"):
        MachineConfig.load(tmp_path / "nope.toml", "inch")


def test_for_units_refuses_unknown_keys():
    """for_units used to `continue` past unknown keys."""
    with pytest.raises(MachineConfigError, match="max_rmp"):
        MachineConfig.for_units("inch", {"max_rmp": 2500})


def test_machine_config_checks_itself_however_it_is_made():
    with pytest.raises(MachineConfigError, match="max_rpm"):
        MachineConfig(max_rpm=-1)


def test_zero_travel_and_bar_still_mean_no_check(tmp_path):
    """The travel and bar checks have always been skipped for 0 (`if m.max_bar`)."""
    m = MachineConfig.load(machine_file(tmp_path, "[inch]\nmax_bar = 0\nx_travel = 0\n"), "inch")
    assert m.max_bar == 0 and m.x_travel == 0


# --------------------------------------------------------------------- CLI: which machine profile
def test_cli_no_machine_and_no_default_file_uses_built_in(tmp_path):
    m, source = cli.machine_for(None, "inch", default=tmp_path / "absent.toml")
    assert m == MachineConfig() and "built-in" in source


def test_cli_default_file_is_used_when_present(tmp_path):
    path = machine_file(tmp_path, "max_rpm = 3000\n")
    m, source = cli.machine_for(None, "inch", default=path)
    assert m.max_rpm == 3000 and source == str(path)


def test_cli_default_file_with_a_problem_is_fatal(tmp_path):
    """The personal profile is a real machine description: never skipped."""
    path = machine_file(tmp_path, "max_rmp = 3000\n")
    with pytest.raises(MachineConfigError, match="max_rmp"):
        cli.machine_for(None, "inch", default=path)


def test_cli_explicit_machine_that_does_not_exist_is_fatal(tmp_path):
    """Used to fall back to the built-in machine: code for a different lathe."""
    with pytest.raises(MachineConfigError, match="doesn't exist"):
        cli.machine_for(str(tmp_path / "other-lathe.toml"), "inch", default=DEFAULT)


def test_cli_explicit_machine_is_used(tmp_path):
    path = machine_file(tmp_path, "max_rpm = 2500\n")
    m, _ = cli.machine_for(str(path), "inch", default=DEFAULT)
    assert m.max_rpm == 2500


@pytest.mark.parametrize(
    "machine_text, code, words",
    [
        (None, 2, ["doesn't exist"]),  # the --machine path is never written
        ("max_rmp = 1\n", 2, ["max_rmp"]),
        ("max_rpm = 'fast'\n", 2, ["must be a number"]),
        ("max_rpm = 3500\n", 0, ["machine profile:"]),
    ],
)
def test_cli_post_with_explicit_machine(tmp_path, capsys, machine_text, code, words):
    """The whole command, as an operator runs it: errors are one line on
    stderr and exit code 2, never a traceback, and nothing is written."""
    mpath = tmp_path / "m.toml"
    if machine_text is not None:
        mpath.write_text(machine_text)
    out = tmp_path / "out.cnc"
    rc = cli.main(
        [
            "post",
            str(EX / "manual_g75.toml"),
            "--tools",
            str(EX / "tools.toml"),
            "--machine",
            str(mpath),
            "-o",
            str(out),
        ]
    )
    err = capsys.readouterr().err
    assert rc == code
    for w in words:
        assert w in err
    assert out.exists() == (code == 0)


def test_cli_bad_tool_library_is_a_clean_error(tmp_path, capsys):
    tools = tmp_path / "tools.toml"
    tools.write_text('[[tool]]\nid = "x"\nname = "X"\nkind = "boring"\n')
    rc = cli.main(["post", str(EX / "manual_g75.toml"), "--tools", str(tools), "--stdout"])
    assert rc == 2 and "kind is 'boring'" in capsys.readouterr().err


# --------------------------------------------------------------------- tools.toml
def test_example_tool_library_loads():
    assert len(ToolLibrary.load(EX / "tools.toml")) > 5


def test_legacy_library_keeps_loading(tmp_path):
    """Extra keys go to params (as always), and 0 for diameter/width still means
    'not given' (the drill emitter reads `tool.diameter or stock`)."""
    path = tmp_path / "tools.toml"
    path.write_text(
        '[[tool]]\nid = "a"\nname = "A"\nkind = "drill"\ndiameter = 0.0\nwidth = 0\nflutes = 2\ncolour = "gold"\n'
    )
    tool = ToolLibrary.load(path)["a"]
    assert tool.params == {"flutes": 2, "colour": "gold"} and tool.diameter == 0.0


@pytest.mark.parametrize(
    "entry, words",
    [
        ('id = "a"\nname = "A"\nkind = "boring"', ["kind is 'boring'", "custom"]),
        ('id = "a"\nname = "A"\nkind = "turn"\nnose_radius = -0.01', ["nose_radius", "negative"]),
        ('id = "a"\nname = "A"\nkind = "turn"\nnose_radius = nan', ["nose_radius", "finite"]),
        ('id = "a"\nname = "A"\nkind = "cutoff"\nwidth = -0.062', ["width", "negative"]),
        ('id = "a"\nname = "A"\nkind = "drill"\ndiameter = "1/4"', ["diameter", "must be a number"]),
        ('id = "a"\nname = "A"\nkind = "tap"\npitch = 0', ["pitch is 0", "greater than zero"]),
        ('id = "a"\nname = "A"\nkind = "tap"\npitch = "20 tpi"', ["pitch"]),
        ('id = "a"\nname = "A"\nkind = "knurl_straddle"\ntpi = -80', ["tpi"]),
        ('id = "a"\nname = "A"\nkind = "turn"\nhand = "lefty"', ["hand"]),
        ('id = "a"\nname = "A"\nkind = "turn"\nunits = "mm"', ["units"]),
        ('name = "A"\nkind = "turn"', ["missing id"]),
    ],
)
def test_bad_tools_stop_with_file_tool_and_setting(tmp_path, entry, words):
    path = tmp_path / "tools.toml"
    path.write_text(f"[[tool]]\n{entry}\n")
    with pytest.raises(ValidationError) as e:
        ToolLibrary.load(path)
    msg = str(e.value)
    assert str(path) in msg and "tool 1" in msg
    for w in words:
        assert w in msg, msg


def test_tool_made_in_code_is_checked_too():
    """The tool library editor builds Tool objects directly."""
    with pytest.raises(ValidationError, match="nose_radius"):
        Tool(id="x", name="X", kind="turn", nose_radius=-1)

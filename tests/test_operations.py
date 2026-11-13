"""Rules every operation module must follow (src/omniturn_cam/operations/).

Operations register themselves, so nothing else forces a new one to be
complete. These tests do: a new operation file that is missing a label, a
menu entry or an emitter fails here, not at the machine.
"""

from __future__ import annotations

import dataclasses
import re

import pytest

from omniturn_cam.fields import FIELDS, info
from omniturn_cam.operations import OPERATION_TYPES, SPECS

# The add-process menu, in order. Operators find entries by position, so the
# order is part of the interface: reordering it is a deliberate change made
# here, not a side effect of adding a file. New entries may be appended.
MENU_ORDER = [
    "face",
    "box_rough",
    "rough",
    "finish",
    "drill",
    "call_sub",
    "raw",
    "thread",
    "tap",
    "axial",
    "radial",
    "cutoff",
]

# Job files store these `type` names. One that disappears or is renamed makes
# every saved job that uses it unreadable.
JOB_FILE_TYPES = set(MENU_ORDER)


def test_menu_order_is_stable():
    assert list(SPECS)[: len(MENU_ORDER)] == MENU_ORDER, (
        "the add-process menu order changed; operators know it by position. If this is "
        "intended, update MENU_ORDER here and say so in the PR"
    )


def test_job_file_type_names_never_disappear():
    missing = JOB_FILE_TYPES - set(OPERATION_TYPES)
    assert not missing, f"saved jobs use these operation types, which no longer exist: {sorted(missing)}"


@pytest.mark.parametrize("kind", list(SPECS))
def test_spec_is_complete(kind):
    s = SPECS[kind]
    assert re.fullmatch(r"[a-z][a-z0-9_]*", kind), "type names are lower_snake_case (they go in job files)"
    assert dataclasses.is_dataclass(s.cls)
    assert callable(s.emit)
    name, desc = s.menu
    assert name.strip() and desc.strip(), "the menu needs a name and a one-line description"
    if s.cls.uses_tool:
        assert s.tool_kinds, "say which tool kinds suit this operation (tool_kinds)"
    # Look in the class's own __dict__: @dataclass writes a __doc__ (the
    # signature) for any class that has none, so cls.__doc__ is never empty.
    doc = s.cls.__dict__.get("__doc__") or ""
    assert doc.strip() and not doc.startswith(f"{s.cls.__name__}("), (
        f"{s.cls.__name__} needs a docstring saying what it cuts"
    )


# ---------------------------------------------------------------- registration
# operations/__init__.py finds the modules itself. These are the cases where it
# must refuse to start rather than pick one silently.


def _module(name, spec=None):
    from types import ModuleType

    mod = ModuleType(f"omniturn_cam.operations.{name}")
    if spec is not None:
        mod.SPEC = spec
    return mod


def test_registration_finds_every_module_in_menu_order():
    import omniturn_cam.operations as pkg

    assert list(pkg._discover()) == list(SPECS)
    shuffled = [_module(k, s) for k, s in reversed(SPECS.items())]
    assert list(pkg._register(shuffled)) == list(SPECS)


def test_registration_refuses_a_module_without_spec():
    from omniturn_cam.operations import _register

    with pytest.raises(ImportError, match="defines no SPEC"):
        _register([_module("face", SPECS["face"]), _module("helpers")])


def test_registration_refuses_two_modules_with_the_same_type():
    from omniturn_cam.operations import _register

    copy = dataclasses.replace(SPECS["face"], order=999)
    with pytest.raises(ImportError, match="two operation modules use type 'face'"):
        _register([_module("face", SPECS["face"]), _module("face_copy", copy)])


def test_registration_refuses_one_class_under_two_types():
    from omniturn_cam.operations import _register

    alias = dataclasses.replace(SPECS["face"], kind="face2", order=999)
    with pytest.raises(ImportError, match="Face is registered twice"):
        _register([_module("face", SPECS["face"]), _module("face2", alias)])


def test_spec_for_uses_the_exact_class():
    """A Cutoff is a RadialPass subclass but its own operation; an unregistered
    subclass must not borrow its parent's emitter."""
    from omniturn_cam.operations import spec_for
    from omniturn_cam.operations.cutoff import Cutoff
    from omniturn_cam.operations.radial import RadialPass

    assert spec_for(Cutoff(t=1, z=-0.5, feed=0.002)).kind == "cutoff"
    assert spec_for(RadialPass(t=1, z=-0.5, x_start=1.0, x_end=0.9, feed=0.002)).kind == "radial"

    @dataclasses.dataclass(kw_only=True)
    class Unregistered(RadialPass):
        pass

    with pytest.raises(TypeError, match="Unregistered is not a registered operation"):
        spec_for(Unregistered(t=1, z=-0.5, x_start=1.0, x_end=0.9, feed=0.002))


@pytest.mark.parametrize("kind", list(SPECS))
def test_every_field_has_an_operator_label(kind):
    """A field without an entry shows its Python name on the form. Not acceptable
    for a machinist at the machine."""
    unlabeled, no_help = [], []
    for f in dataclasses.fields(SPECS[kind].cls):
        fi = info(kind, f.name)
        if f.name not in FIELDS and f.name not in SPECS[kind].fields:
            unlabeled.append(f.name)
        elif not fi.help.strip():
            no_help.append(f.name)
    assert not unlabeled, f"{kind}: add a FieldInfo (label, units, help) for {unlabeled}"
    assert not no_help, f"{kind}: these fields have no help text: {no_help}"


@pytest.mark.parametrize("kind", list(SPECS))
def test_optional_fields_say_what_blank_means(kind):
    """Blank means automatic, and the help text says what automatic will do
    (CONTRIBUTING.md, Usability): "Blank = ..." in the help, and a placeholder."""
    vague = []
    for f in dataclasses.fields(SPECS[kind].cls):
        fi = info(kind, f.name)
        if fi.optional:
            assert fi.blank.strip(), f"{kind}.{f.name}: optional field needs a placeholder for blank"
            if "blank" not in fi.help.lower():
                vague.append(f.name)
    assert not vague, f"{kind}: say in the help what leaving these blank does: {vague}"

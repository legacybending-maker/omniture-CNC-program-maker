"""Machining operations: one module per operation type.

Each module defines the operation's dataclass (what the operator fills in), the
labels for its fields, how it shows in the GUI, and how it is written as
OmniTurn code, and registers all of that as one ``SPEC``. This package imports
every module in it and collects the SPECs, so a new operation is a new file
here (plus a golden job that uses it), with no shared list to edit.

All lengths are in the job's units (inch or mm). X values are diameters.
Feeds are per revolution (IPR / mm per rev) unless noted.
"""

from __future__ import annotations

import importlib
import pkgutil

from .base import FieldInfo, Operation, OpSpec


def _discover() -> dict[str, OpSpec]:
    specs: list[OpSpec] = []
    for m in pkgutil.iter_modules(__path__):
        if m.name == "base" or m.name.startswith("_"):
            continue
        mod = importlib.import_module(f"{__name__}.{m.name}")
        spec = getattr(mod, "SPEC", None)
        if not isinstance(spec, OpSpec):
            raise ImportError(f"{mod.__name__} defines no SPEC = OpSpec(...)")
        specs.append(spec)
    seen: dict[str, OpSpec] = {}
    for s in sorted(specs, key=lambda s: (s.order, s.kind)):
        if s.kind in seen:
            raise ImportError(f"two operation modules use type {s.kind!r}")
        if any(o.cls is s.cls for o in seen.values()):
            raise ImportError(f"{s.cls.__name__} is registered twice")
        seen[s.kind] = s
    return seen


SPECS: dict[str, OpSpec] = _discover()  # kind -> spec, in menu order
_BY_CLASS = {s.cls: s for s in SPECS.values()}

# job-file `type` -> dataclass
OPERATION_TYPES: dict[str, type[Operation]] = {k: s.cls for k, s in SPECS.items()}


def spec_for(op: Operation) -> OpSpec:
    """The spec for an operation instance (exact class: a Cutoff is not a RadialPass)."""
    try:
        return _BY_CLASS[type(op)]
    except KeyError:
        raise TypeError(f"{type(op).__name__} is not a registered operation") from None


def kind_of(op: Operation) -> str:
    return spec_for(op).kind


__all__ = ["SPECS", "OPERATION_TYPES", "FieldInfo", "Operation", "OpSpec", "spec_for", "kind_of"]

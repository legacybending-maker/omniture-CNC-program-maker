"""Write a profile object's OD/ID into an omniturn-cam job file (no GUI here)."""

from __future__ import annotations

import re
from pathlib import Path

from .feature import MM_PER_INCH, profiles_of
from omniturn_cam.job import Job, Stock, merge_profiles, save_job
from omniturn_cam.tools import Setup


def job_name(label: str) -> str:
    """8-character program name the OmniTurn will accept (manual p2.8)."""
    name = re.sub(r"[^A-Za-z0-9_-]", "", label).upper()[:8]
    return name or "PART"


def save_profiles(obj, path: str | Path, name: str | None = None) -> str:
    """Add the profiles to an existing job file, or start a new job with them.
    Returns a one-line description of what was done."""
    path = Path(path)
    profiles, data = profiles_of(obj)
    if path.exists():
        merge_profiles(path, profiles)
        return f"Added {', '.join(profiles)} to {path.name}"
    units = data["units"]
    k = MM_PER_INCH if units == "inch" else 1.0
    stock = obj.StockDiameter.Value / k or data["max_dia"]
    job = Job(
        name or job_name(obj.Source.Label if obj.Source else obj.Label),
        units,
        Stock(round(stock, 6 if units == "inch" else 4), round(data["length"], 6)),
        Setup(),
        profiles,
        description=f"Profiles from {data['solid']} (FreeCAD)",
    )
    save_job(job, path)
    return f"Started job {job.name} in {path.name} with {', '.join(profiles)}"

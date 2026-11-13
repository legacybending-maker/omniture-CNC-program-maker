"""Release bookkeeping: one version number everywhere, and a changelog to put it in."""

import re
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

import omniturn_cam

ROOT = Path(__file__).parent.parent


def test_version_agrees_everywhere():
    ns = {"a": "https://wiki.freecad.org/Package_Metadata"}
    root = ET.parse(ROOT / "package.xml").getroot()
    el = root.find("a:version", ns)
    xml_version = el.text.strip()
    assert xml_version == omniturn_cam.__version__, "package.xml <version> differs from __version__"
    py = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert "version" in py.get("dynamic", []), "pyproject.toml must read the version from __init__.py"


def test_changelog_has_unreleased_section():
    text = (ROOT / "CHANGELOG.md").read_text()
    assert re.search(r"^## \[Unreleased\]", text, re.M), "CHANGELOG.md needs an ## [Unreleased] section"

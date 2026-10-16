"""README relative links must resolve, and the developer index must point at
BENCHMARK.md and docs/adr/.

BENCHMARK.md (measured transcription RTF) and the ADRs existed with no
document in the repo linking to them, so nobody reading the README could find
them. Static check only: parse ``](target)`` links, skip URLs and anchors,
require each relative target to exist on disk.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
READMES = ["README.md", "README.zh-TW.md"]
REQUIRED_TARGETS = ["BENCHMARK.md", "docs/adr/"]

_LINK_RE = re.compile(r"\]\(([^)\s]+)\)")
_EXTERNAL_RE = re.compile(r"^(?:[a-zA-Z][\w+.-]*:|#)")


def _relative_links(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    out = []
    for target in _LINK_RE.findall(text):
        if _EXTERNAL_RE.match(target):
            continue
        out.append(target.split("#", 1)[0])
    return out


@pytest.mark.parametrize("name", READMES)
def test_readme_relative_links_resolve(name):
    links = _relative_links(name)
    assert links, f"{name}: found no relative links — the parser is broken"
    missing = [t for t in links if not (ROOT / t).exists()]
    assert not missing, f"{name}: relative links point at missing paths: {missing}"


@pytest.mark.parametrize("name", READMES)
def test_readme_links_benchmark_and_adr(name):
    links = set(_relative_links(name))
    absent = [t for t in REQUIRED_TARGETS if t not in links]
    assert not absent, f"{name}: developer index no longer links {absent}"

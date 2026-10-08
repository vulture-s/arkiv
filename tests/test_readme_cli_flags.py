"""README CLI examples must only use flags the scripts actually define.

Regression for the watch.py example that documented ``--interval 10`` while
the parser only accepts ``--poll-interval`` (argparse rejects the old spelling,
so a copy-pasted README command failed outright).

Static check: collect every ``--flag`` passed to ``add_argument`` in each
first-party script (AST, no import), then scan the README code lines that
invoke ``python <script>.py`` and require each ``--flag`` to be defined.
"""
import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
READMES = ["README.md", "README.zh-TW.md"]

_CMD_RE = re.compile(r"^\s*python3?\s+([\w\-]+\.py)\b(.*)$")
_FLAG_RE = re.compile(r"(?<!\S)(--[A-Za-z][\w\-]*)")


def _defined_flags(script: Path) -> set:
    tree = ast.parse(script.read_text(encoding="utf-8"))
    flags = {"--help"}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
        ):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) \
                        and arg.value.startswith("--"):
                    flags.add(arg.value)
    return flags


def _undefined_flags(line: str):
    """Return (script, [unknown flags]) for a README line, or None if not a CLI line."""
    m = _CMD_RE.match(line)
    if not m:
        return None
    script = ROOT / m.group(1)
    if not script.is_file():
        return None
    rest = m.group(2).split("#", 1)[0]  # drop trailing shell comment
    used = [f.split("=", 1)[0] for f in _FLAG_RE.findall(rest)]
    known = _defined_flags(script)
    return m.group(1), [f for f in used if f not in known]


def _readme_cli_lines():
    out = []
    for name in READMES:
        path = ROOT / name
        if not path.is_file():
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _CMD_RE.match(line):
                out.append((name, lineno, line))
    return out


def test_readmes_have_cli_examples():
    # Guard against the scan silently matching nothing (regex drift).
    lines = _readme_cli_lines()
    assert any("watch.py" in l for _, _, l in lines)


@pytest.mark.parametrize("name,lineno,line", _readme_cli_lines())
def test_readme_cli_flags_exist(name, lineno, line):
    res = _undefined_flags(line)
    if res is None:
        pytest.skip("script not in repo root")
    script, unknown = res
    assert not unknown, f"{name}:{lineno} uses {unknown} not defined by {script}: {line!r}"


def test_checker_rejects_the_old_watch_interval_flag():
    # Negative control: the exact pre-fix README line must be flagged.
    res = _undefined_flags("python watch.py ~/Movies/rushes --interval 10")
    assert res == ("watch.py", ["--interval"])
    res = _undefined_flags("python watch.py ~/Movies/rushes --poll-interval 10")
    assert res == ("watch.py", [])

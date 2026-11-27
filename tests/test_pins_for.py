"""scripts/pins_for.py — the pinned set the dependency audit checks per shipped target."""
import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("packaging")

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("pins_for", ROOT / "scripts" / "pins_for.py")
pins_for = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pins_for)

LOCK = """# header
aiohttp==3.14.5 ; python_full_version >= '3.10'
    # via aiohttp
pillow==12.3.0 ; python_full_version == '3.10.*'
pillow==13.0.0 ; python_full_version >= '3.11'
pillow==11.3.0 ; python_full_version < '3.10'
torch==2.14.1 ; python_full_version >= '3.10' and sys_platform == 'darwin'
pywin32==311 ; sys_platform == 'win32'
certifi==2026.11.20
"""


def test_marker_forks_resolve_to_the_shipped_python():
    darwin = pins_for.pins_for(LOCK, "darwin")
    assert "pillow==13.0.0" in darwin
    assert "pillow==12.3.0" not in darwin and "pillow==11.3.0" not in darwin
    assert "certifi==2026.11.20" in darwin and "aiohttp==3.14.5" in darwin


def test_platform_only_pins_follow_the_target_not_the_runner():
    # The point of the script: an ubuntu runner would drop both of these.
    assert "torch==2.14.1" in pins_for.pins_for(LOCK, "darwin")
    assert "torch==2.14.1" not in pins_for.pins_for(LOCK, "win32")
    assert "pywin32==311" in pins_for.pins_for(LOCK, "win32")
    assert "pywin32==311" not in pins_for.pins_for(LOCK, "darwin")


def test_comments_and_via_lines_are_not_pins():
    assert all("==" in p and not p.startswith("#") for p in pins_for.pins_for(LOCK, "darwin"))


def test_real_lock_yields_pins_for_every_target():
    text = (ROOT / "constraints.txt").read_text(encoding="utf-8")
    for target in pins_for.TARGETS:
        assert len(pins_for.pins_for(text, target)) > 50, target


def test_bad_target_and_empty_lock_are_not_clean(monkeypatch, capsys):
    assert pins_for.main(["linux"]) == 2
    monkeypatch.setattr(pins_for, "ROOT", ROOT / "does-not-matter")
    monkeypatch.setattr(pins_for, "pins_for", lambda text, target: [])
    monkeypatch.setattr(Path, "read_text", lambda self, encoding=None: "")
    assert pins_for.main(["darwin"]) == 2


def test_ci_audits_the_pinned_set_for_both_release_targets():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    block = ci.split("- name: pip-audit", 1)[1].split("- uses:", 1)[0]
    assert "for target in darwin win32" in block
    assert 'pip-audit -r "pins-$target.txt" --no-deps --disable-pip "${IGNORES[@]}"' in block
    # the free resolution stays, carrying the same ignore list
    assert 'pip-audit -r requirements.txt -r requirements-dev.txt "${IGNORES[@]}"' in block

"""scripts/upgrade_desktop_windows.py — the checks that decide "upgrade verified".

The Windows steps themselves run only on the PC; what CI can pin is the
judgement: each helper below encodes a failure that a manual upgrade in
2026-10 shipped past (partial install, duplicate dist-info, wrong version).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "upgrade_desktop_windows.py"
_spec = importlib.util.spec_from_file_location("upgrade_desktop_windows", _SCRIPT)
up = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(up)


def test_installer_version_only_from_real_installer_names():
    assert up.installer_version(r"C:\Users\u\Downloads\arkiv_1.7.2_x64-setup.exe") == "1.7.2"
    assert up.installer_version("arkiv_1.7.2_x64_en-US.msi") is None  # MSI is a different uninstall path
    assert up.installer_version("arkiv_1.7.2_x64-setup (1).exe") is None
    assert up.installer_version("other_1.0.0_x64-setup.exe") is None


def test_config_version_reads_the_assignment_not_a_mention():
    lines = ['# VERSION = "0.0.0" is bumped by release\n', 'VERSION = "1.7.2"\n', 'X = 1\n']
    assert up.config_version(lines) == "1.7.2"
    assert up.config_version(["APP_VERSION = '9'\n"]) is None


def test_duplicate_dist_info_flags_two_versions_of_one_package():
    names = [
        "aiohttp", "aiohttp-3.14.3.dist-info", "aiohttp-3.14.5.dist-info",
        "aiohttp_retry-2.9.1.dist-info", "numpy-2.1.0.dist-info",
    ]
    assert up.duplicate_dist_info(names) == {
        "aiohttp": ["aiohttp-3.14.3.dist-info", "aiohttp-3.14.5.dist-info"],
    }


def test_duplicate_dist_info_clean_tree_is_empty():
    # aiohttp vs aiohttp_retry share a prefix and must not be merged.
    assert up.duplicate_dist_info(["aiohttp-3.14.5.dist-info", "aiohttp_retry-2.9.1.dist-info"]) == {}


def test_partial_install_fails_the_file_count():
    assert not up.file_count_ok(3080, 25759)  # the 2026-10-22 partial install
    assert up.file_count_ok(25760, 25759)
    assert not up.file_count_ok(7888, 7889)  # one file short of the payload
    assert not up.file_count_ok(3080, None)  # payload unknown: absolute floor


def test_a_slimmed_release_is_not_a_partial_install():
    # 2026-10-28: 1.7.3 → 1.7.4 went 26,691 → 7,890 files by design (torch
    # dropped). The installer carried 7,889; uninstall.exe is the +1.
    assert up.file_count_ok(7890, 7889)
    assert up.file_count_ok(7890, None)


_LISTING = """
7-Zip 23.01 (x64)

Listing archive: C:\\Users\\user\\Downloads\\arkiv_1.7.4_x64-setup.exe

--
Path = C:\\Users\\user\\Downloads\\arkiv_1.7.4_x64-setup.exe
Type = Nsis

----------
Path = $PLUGINSDIR\\System.dll
Size = 12288

Path = $PLUGINSDIR\\nsDialogs.dll
Size = 9728

Path = arkiv.exe
Size = 1000

Path = backend\\python\\python.exe
Size = 2000

Path = backend\\site-packages\\fastapi\\__init__.py
Size = 30
"""


def test_payload_count_skips_the_archive_header_and_installer_plugins():
    assert up.payload_count(_LISTING) == 3


def test_payload_count_is_unknown_without_a_listing():
    assert up.payload_count("") is None
    assert up.payload_count("ERROR: cannot open file") is None
    assert up.payload_count(_LISTING.split("----------")[0] + "----------\n") is None


def test_unique_path_never_reuses_a_backup(tmp_path):
    p = tmp_path / "install-dir-leftovers-1.7.1"
    assert up.unique_path(str(p)) == str(p)
    p.mkdir()
    assert up.unique_path(str(p)) == str(p) + "-2"
    (tmp_path / "install-dir-leftovers-1.7.1-2").mkdir()
    assert up.unique_path(str(p)) == str(p) + "-3"


def test_refuses_off_windows(monkeypatch):
    monkeypatch.setattr(up.os, "name", "posix")
    assert up.main(["arkiv_1.7.2_x64-setup.exe"]) == 2


def test_never_deletes_the_install_dir():
    # Leftovers are renamed into the backup folder; a delete would lose the
    # only copy if the new install then fails.
    src = _SCRIPT.read_text(encoding="utf-8")
    for forbidden in ("rmtree", "os.remove", "os.unlink", "Remove-Item"):
        assert forbidden not in src, forbidden

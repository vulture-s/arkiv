#!/usr/bin/env python3
"""Upgrade the arkiv desktop app on Windows in place: uninstall → set leftovers aside → silent install → verify.

    python scripts/upgrade_desktop_windows.py %USERPROFILE%\\Downloads\\arkiv_1.7.2_x64-setup.exe

Why a script: the NSIS uninstaller returns long before it is done. It keeps
deleting (and locking) files in the background, so the obvious
"uninstall /S; install /S" sequence produced, in 2026-10 alone, a 3,080-file
partial install that looked like success, a PermissionError on the rename, and
a site-packages holding two versions of the same package (aiohttp 3.14.3 *and*
3.14.5). Each step below waits for the thing the previous attempt assumed.

Only the install directory (%LOCALAPPDATA%\\arkiv) is touched. Libraries, their
.arkiv/ databases and the project registry live elsewhere and are never moved.
Whatever the uninstaller leaves behind is renamed into
%USERPROFILE%\\arkiv-upgrade-backup-<date>\\ — never deleted.

Exit codes: 0 verified · 1 verification failed · 2 refused to start (arkiv
running, not Windows, bad installer path) · 3 a step timed out.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import time

INSTALLER_RE = re.compile(r"arkiv_(\d+\.\d+\.\d+)_x64-setup\.exe$", re.IGNORECASE)
VERSION_RE = re.compile(r"""^VERSION\s*=\s*["']([^"']+)["']""")


# ── pure helpers (tested on any OS) ──────────────────────────────────────────

def installer_version(path):
    """'…/arkiv_1.7.2_x64-setup.exe' → '1.7.2', or None for any other name."""
    m = INSTALLER_RE.search(os.path.basename(path))
    return m.group(1) if m else None


def config_version(lines):
    """VERSION = "x.y.z" from config.py lines, or None."""
    for line in lines:
        m = VERSION_RE.match(line)
        if m:
            return m.group(1)
    return None


def duplicate_dist_info(names):
    """Packages with more than one *.dist-info directory: {name: [dirs]}.

    'aiohttp-3.14.3.dist-info' and 'aiohttp-3.14.5.dist-info' side by side means
    the new install landed on top of leftovers — pip metadata and the code on
    sys.path no longer agree.
    """
    seen = {}
    for d in names:
        if d.endswith(".dist-info"):
            seen.setdefault(d[: -len(".dist-info")].rsplit("-", 1)[0].lower(), []).append(d)
    return {k: sorted(v) for k, v in seen.items() if len(v) > 1}


def payload_count(listing):
    """Files the installer carries, from `7z l -slt <setup.exe>` output.

    The header block before the `----------` line describes the archive itself;
    `$PLUGINSDIR\\` entries are the installer's own DLLs and never land in the
    install dir. None when the listing holds no entries (7-Zip missing / failed).
    """
    _, sep, body = listing.partition("\n----------")
    if not sep:
        return None
    n = 0
    for line in body.splitlines():
        if line.startswith("Path = ") and not line[7:].startswith("$PLUGINSDIR"):
            n += 1
    return n or None


# Only used when the payload can't be listed. 1.7.4 dropped from ~25,700 files
# to ~7,900 on purpose (no torch), so a floor must sit well under that while
# still catching the 3,080-file partial install.
FILE_FLOOR = 5000


def file_count_ok(after, expected):
    """The install dir must hold everything the installer carries.

    Compared against THIS installer's payload, not the install it replaced: a
    release that slims the bundle (1.7.4: 26,691 → 7,890) is not a partial
    install, and comparing against the old count failed every such upgrade.
    The failure this catches is the partial install (3,080 files) that still
    starts and registers the right version. Without a payload count fall back
    to FILE_FLOOR.
    """
    if expected:
        return after >= expected
    return after >= FILE_FLOOR


SEVEN_ZIP = r"C:\Program Files\7-Zip\7z.exe"


def installer_payload(exe):
    if not os.path.isfile(SEVEN_ZIP):
        return None
    try:
        out = subprocess.run([SEVEN_ZIP, "l", "-slt", exe], capture_output=True,
                             text=True, errors="replace", timeout=300).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return payload_count(out)


def unique_path(path):
    """path, or path-2, path-3 … — the backup folder is shared by every run that day."""
    if not os.path.exists(path):
        return path
    i = 2
    while os.path.exists("%s-%d" % (path, i)):
        i += 1
    return "%s-%d" % (path, i)


# ── Windows side ─────────────────────────────────────────────────────────────

def ps(command):
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        capture_output=True, encoding="utf-8", errors="replace",
    ).stdout.strip()
    return out


def ps_json(command):
    out = ps(command + " | ConvertTo-Json")
    if not out:
        return []
    data = json.loads(out)
    return data if isinstance(data, list) else [data]


def arkiv_processes():
    # The app itself, plus its Python sidecar (which outlives a crashed window).
    return ps_json(
        "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'arkiv.exe' -or "
        "($_.Name -like 'python*' -and $_.CommandLine -like '*Local\\arkiv\\backend*') } | "
        "Select-Object ProcessId,Name"
    )


def uninstaller_processes():
    # NSIS copies itself to %TEMP% as Au_.exe / Un_*.exe and returns immediately.
    return ps(
        "Get-Process | Where-Object { $_.ProcessName -like 'Au_*' -or $_.ProcessName -like 'Un_*' "
        "-or $_.ProcessName -eq 'uninstall' } | Select-Object -ExpandProperty ProcessName"
    ).split()


def registered_version():
    out = ps(
        "Get-ChildItem HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall | "
        "Get-ItemProperty | Where-Object { $_.DisplayName -like '*arkiv*' } | "
        "Select-Object -ExpandProperty DisplayVersion"
    )
    return out.split()


def count_files(root):
    return sum(len(f) for _, _, f in os.walk(root)) if os.path.exists(root) else 0


def read_installed_version(inst):
    cfg = os.path.join(inst, "backend", "src", "config.py")
    try:
        with open(cfg, encoding="utf-8") as fh:
            return config_version(fh)
    except OSError:
        return None


def wait_until(predicate, timeout, step=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(step)
    return predicate()


def wait_stable_count(root, timeout, step=3.0):
    """Poll the file count until two reads agree — the installer also finishes in the background."""
    prev = -1
    end = time.time() + timeout
    while time.time() < end:
        n = count_files(root)
        if n == prev and n > 0:
            return n
        prev = n
        time.sleep(step)
    return count_files(root)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("installer", help="path to arkiv_<ver>_x64-setup.exe")
    args = ap.parse_args(argv)

    if os.name != "nt":
        print("STOP: Windows only.")
        return 2
    exe = os.path.abspath(os.path.expandvars(args.installer))
    want = installer_version(exe)
    if not os.path.isfile(exe) or not want:
        print("STOP: not an arkiv installer: %s" % exe)
        return 2
    running = arkiv_processes()
    if running:
        print("STOP: arkiv is running — quit it first: %s" % running)
        return 2

    inst = os.path.expandvars(r"%LOCALAPPDATA%\arkiv")
    stamp = datetime.date.today().strftime("%Y%m%d")
    bak = os.path.expandvars(r"%USERPROFILE%\arkiv-upgrade-backup-" + stamp)
    before_files = count_files(inst)
    expected_files = installer_payload(exe)
    before_ver = read_installed_version(inst)
    print("installed: %s (%d files) → installing %s" % (before_ver or "none", before_files, want))

    if os.path.exists(inst):
        un = os.path.join(inst, "uninstall.exe")
        if os.path.isfile(un):
            print("uninstall rc", subprocess.run([un, "/S"], timeout=600).returncode)
            if not wait_until(lambda: not uninstaller_processes(), 180):
                print("TIMEOUT: uninstaller still running after 3 min: %s" % uninstaller_processes())
                return 3
        if os.path.exists(inst):
            os.makedirs(bak, exist_ok=True)
            dest = unique_path(os.path.join(bak, "install-dir-leftovers-%s" % (before_ver or "unknown")))
            last = []

            def moved():
                try:
                    os.rename(inst, dest)
                    return True
                except OSError as e:
                    last[:] = [e]
                    return not os.path.exists(inst)

            if not wait_until(moved, 180):
                print("TIMEOUT: install dir still locked after 3 min: %s" % (last[0] if last else "?"))
                return 3
            if os.path.exists(dest):
                print("leftovers →", dest)

    print("installer rc", subprocess.run([exe, "/S"], timeout=900).returncode)
    after_files = wait_stable_count(inst, 180)

    sp = os.path.join(inst, "backend", "site-packages")
    dups = duplicate_dist_info(os.listdir(sp)) if os.path.isdir(sp) else {"<site-packages missing>": []}
    got_cfg = read_installed_version(inst)
    got_reg = registered_version()
    checks = [
        ("files %d (installer carries %s, before %d)" % (
            after_files, expected_files if expected_files else "? — no 7-Zip, floor %d" % FILE_FLOOR,
            before_files), file_count_ok(after_files, expected_files)),
        ("no duplicate dist-info %s" % (dups or ""), not dups),
        ("config VERSION %s" % got_cfg, got_cfg == want),
        ("registry DisplayVersion %s" % got_reg, got_reg == [want]),
    ]
    for label, ok in checks:
        print("%s %s" % ("PASS" if ok else "FAIL", label))
    return 0 if all(ok for _, ok in checks) else 1


if __name__ == "__main__":
    sys.exit(main())

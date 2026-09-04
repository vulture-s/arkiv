"""The compose files must keep every piece of user state on a bind mount.

Four gaps of the same shape have shipped, each found by a user running arkiv on
a NAS rather than by us:

    bins        →  #401   cross-library 精選集 lost on `compose down`
    projects    →  #402   registered projects lost on `compose down`
    media-in    →  #422   uploads land in the ephemeral layer (or 500 as non-root)
    the library →  #411   compose mounted arkiv's derived data but never the
                          media it is supposed to index

Nothing in the repo was checking, so each one had to be discovered in
production. These tests are that check: a mount or an env var that quietly
disappears from compose fails here instead of six weeks later on someone's NAS.
"""
import os
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parent.parent
SINGLE_HOST = ROOT / "docker-compose.yml"
SPLIT_HOST = ROOT / "docker-compose.remote-ollama.yml"


def _load(path):
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _arkiv(path):
    return _load(path)["services"]["arkiv"]


_MOUNT_MODES = {"ro", "rw", "z", "Z", "cached", "delegated", "consistent"}


def _mount_target(entry):
    """Container-side path of one `host:container[:mode]` entry.

    Splitting on the first ':' is wrong: a host side written as
    `${ARKIV_MEDIA_DIR:-./media}` contains colons of its own, and naively
    splitting yields '-./media}'. Work from the right instead, stepping past an
    optional mode suffix.
    """
    parts = entry.rsplit(":", 1)
    if len(parts) == 2 and parts[1] in _MOUNT_MODES:
        parts = parts[0].rsplit(":", 1)
    return parts[-1]


def _targets(service):
    """Container-side paths of every bind mount, e.g. '/app/media-in'."""
    return {_mount_target(entry) for entry in service.get("volumes", [])}


def _env(service):
    return dict(
        item.split("=", 1) for item in service.get("environment", []) if "=" in item
    )


# ── every piece of state is on a mount ───────────────────────────────────────
STATEFUL_PATHS = [
    ("/app/media.db", "the SQLite catalogue"),
    ("/app/chroma_db", "the vector index"),
    ("/app/thumbnails", "generated thumbnails"),
    ("/root/.arkiv-bins", "cross-library 精選集 (#401)"),
    ("/root/.arkiv-projects", "the project registry (#402)"),
    ("/app/media-in", "the upload landing zone (#422)"),
    ("/media", "the media library itself (#411)"),
]


@pytest.mark.parametrize("target,what", STATEFUL_PATHS)
@pytest.mark.parametrize("compose", [SINGLE_HOST, SPLIT_HOST], ids=["single", "split"])
def test_stateful_path_is_mounted(compose, target, what):
    assert target in _targets(_arkiv(compose)), (
        "{0} is not bind-mounted in {1} — it would live in the container's "
        "ephemeral layer and vanish on `docker compose down`. ({2})"
    ).format(target, compose.name, what)


# ── the ingest-roots trap ────────────────────────────────────────────────────
@pytest.mark.parametrize("compose", [SINGLE_HOST, SPLIT_HOST], ids=["single", "split"])
def test_ingest_roots_covers_both_upload_dir_and_library(compose):
    """🔴 ARKIV_INGEST_ROOTS REPLACES webguard's default root list, it does not
    extend it. Setting it for /media while forgetting /app would let uploads
    write to /app/media-in and then have the follow-up ingest refuse the very
    directory it just wrote to."""
    roots = _env(_arkiv(compose)).get("ARKIV_INGEST_ROOTS", "")
    parts = [p for p in roots.split(os.pathsep if os.pathsep in roots else ":") if p]
    assert "/app" in parts, (
        "/app missing from ARKIV_INGEST_ROOTS in {0}: the upload endpoint writes "
        "to /app/media-in and its background ingest would be rejected".format(compose.name)
    )
    assert "/media" in parts, (
        "/media missing from ARKIV_INGEST_ROOTS in {0}: the mounted library "
        "would be rejected by _assert_ingest_path_safe".format(compose.name)
    )


def test_ingest_roots_matches_the_mounted_library():
    """The allow-list and the mount must name the same path — drift between them
    fails at runtime with a permission-looking error, not at startup."""
    for compose in (SINGLE_HOST, SPLIT_HOST):
        svc = _arkiv(compose)
        assert "/media" in _targets(svc)
        assert "/media" in _env(svc).get("ARKIV_INGEST_ROOTS", "")


# ── the library mount is overridable without editing the file ────────────────
@pytest.mark.parametrize("compose", [SINGLE_HOST, SPLIT_HOST], ids=["single", "split"])
def test_library_host_side_is_a_variable(compose):
    """A NAS share or an external drive must be selectable with an env var; a
    hardcoded host path would force every deployer to edit a tracked file."""
    mounts = [v for v in _arkiv(compose).get("volumes", []) if v.endswith(":/media")]
    assert mounts, "no /media mount in " + compose.name
    assert "ARKIV_MEDIA_DIR" in mounts[0], (
        "the /media host side is hardcoded in {0}: {1}".format(compose.name, mounts[0])
    )


# ── the split-host file (#423) ───────────────────────────────────────────────
def test_split_host_file_exists():
    assert SPLIT_HOST.is_file(), "docker-compose.remote-ollama.yml is missing (#423)"


def test_split_host_has_no_local_ollama():
    """The whole point: Ollama lives on another machine. A leftover `ollama`
    service would start a second, model-less copy and shadow the remote one."""
    assert "ollama" not in _load(SPLIT_HOST)["services"]


def test_split_host_requires_the_ollama_url():
    """`${VAR:?message}` — compose refuses to start rather than silently falling
    back to a localhost that has nothing listening on it."""
    raw = SPLIT_HOST.read_text(encoding="utf-8")
    assert "ARKIV_OLLAMA_URL=${ARKIV_OLLAMA_URL:?" in raw


def test_split_host_does_not_depend_on_a_service_it_cannot_see():
    assert "depends_on" not in _arkiv(SPLIT_HOST)


# ── the two files must not drift apart ───────────────────────────────────────
def test_the_two_files_differ_only_in_the_ollama_url():
    """The strong form of the drift check.

    The split-host file is the single-host file with one line changed. Asserting
    that directly is worth more than listing the keys we happened to think of:
    anything added to one file and forgotten in the other fails here, including
    keys that do not exist yet.
    """
    single, split = _env(_arkiv(SINGLE_HOST)), _env(_arkiv(SPLIT_HOST))
    assert set(single) == set(split), (
        "env keys diverged: only in single={0}, only in split={1}".format(
            sorted(set(single) - set(split)), sorted(set(split) - set(single))
        )
    )
    differing = {k for k in single if single[k] != split[k]}
    assert differing == {"ARKIV_OLLAMA_URL"}, (
        "the two compose files should differ in ARKIV_OLLAMA_URL and nothing "
        "else; also differing: {0}".format(sorted(differing - {"ARKIV_OLLAMA_URL"}))
    )
    assert _targets(_arkiv(SINGLE_HOST)) == _targets(_arkiv(SPLIT_HOST)), (
        "the two files mount different sets of paths"
    )


def test_model_pins_agree_across_both_files():
    """These already drifted from config.py once ('it did, for months' — the
    comment in docker-compose.yml). Two files means two chances to drift."""
    keys = ("ARKIV_EMBED_MODEL", "ARKIV_VISION_MODEL", "ARKIV_WHISPER_MODEL")
    single, split = _env(_arkiv(SINGLE_HOST)), _env(_arkiv(SPLIT_HOST))
    for k in keys:
        assert single[k] == split[k], "{0} differs between the two compose files".format(k)


def test_upload_tunables_match_the_code_defaults():
    """Surfaced in compose so a deployer can see the knobs exist. If the code
    default moves and compose does not, the file starts lying."""
    ingest = (ROOT / "routers" / "ingest.py").read_text(encoding="utf-8")
    for env_key, code_default in (
        ("ARKIV_UPLOAD_MAX_MB", '"4096"'),
        ("ARKIV_UPLOAD_MAX_CONCURRENT", '"3"'),
        ("ARKIV_UPLOAD_MAX_QUEUE_SEC", '"300"'),
    ):
        assert 'os.environ.get("{0}", {1})'.format(env_key, code_default) in ingest, (
            "{0}'s code default changed; update both compose files".format(env_key)
        )
        for compose in (SINGLE_HOST, SPLIT_HOST):
            shown = _env(_arkiv(compose))[env_key]
            assert shown == code_default.strip('"'), (
                "{0} in {1} says {2}, code default is {3}".format(
                    env_key, compose.name, shown, code_default
                )
            )

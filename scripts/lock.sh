#!/usr/bin/env bash
# Regenerate constraints.txt — the resolved, pinned dependency set every install
# path uses (`pip install -r requirements.txt -c constraints.txt`).
#
# One universal file covers every Python (3.9 NAS floor → 3.12 prod) and every
# platform (macOS / Windows / Linux): uv forks a pin per marker where the
# answer differs (numpy 2.0.2 on 3.9, 2.5.4 on 3.12), so nothing has to be
# resolved down to the oldest supported Python.
#
# Existing pins are kept unless a requirement forces a change — rerunning is a
# no-op, which is what CI's lock-drift check relies on. Pass --upgrade to move
# everything to the newest allowed versions, or --upgrade-package NAME for one.
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv pip compile requirements.txt requirements-dev.txt requirements-ci.txt \
  --universal --python-version 3.9 -o constraints.txt -q "$@"

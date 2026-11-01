#!/usr/bin/env bash
# Run a job by hand with the environment's settings, e.g.:
#   deploy/cli.sh production close-day --date 2026-10-30
#   deploy/cli.sh production seed-admin
set -euo pipefail
TARGET="$1"; shift
BASE="/srv/kachuful/$TARGET"
cd "$BASE/current"
set -a
# shellcheck disable=SC1091
. "$BASE/shared/env"
set +a
exec "$BASE/current/.venv/bin/python" -m app.cli "$@"

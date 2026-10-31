#!/usr/bin/env bash
# Run a job by hand with the environment's settings, e.g.:
#   deploy/cli.sh staging seed-demo --reset
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

#!/usr/bin/env bash
# Runs on the VPS as `deploy`: install deps, migrate, switch the `current` link, restart.
# Usage: remote-deploy.sh <staging|production> <release-id>
set -euo pipefail

TARGET="$1"
RELEASE_ID="$2"
BASE="/srv/kachuful/$TARGET"
RELEASE="$BASE/releases/$RELEASE_ID"
SHARED="$BASE/shared"
export PATH="$HOME/.local/bin:$PATH"

cd "$RELEASE"
uv sync --frozen --no-dev --python 3.12

# The env file is the only configuration; APP_VERSION tags /api/health with this release.
set -a
# shellcheck disable=SC1091
. "$SHARED/env"
set +a
export APP_VERSION="$RELEASE_ID"
echo "APP_VERSION=$RELEASE_ID" > "$RELEASE/.release-env"

.venv/bin/alembic upgrade head

ln -sfn "$RELEASE" "$BASE/current.new"
mv -T "$BASE/current.new" "$BASE/current"
sudo /usr/bin/systemctl restart "kachuful-api@$TARGET"

# Keep the five newest releases.
ls -1dt "$BASE"/releases/*/ | tail -n +6 | xargs -r rm -rf
echo "Release $RELEASE_ID is live on $TARGET"

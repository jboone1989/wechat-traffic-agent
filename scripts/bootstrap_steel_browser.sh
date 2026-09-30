#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STEEL_HOME="${CONTENT_STEEL_BROWSER_HOME:-/home/ubuntu/src/steel-browser}"
STEEL_REPO="https://github.com/steel-dev/steel-browser.git"
STEEL_COMMIT="e5902fc50f767702894902916ecf5489de7de457"
PATCH="$ROOT/deploy/steel-browser-user-data-dir.patch"

if [[ ! -d "$STEEL_HOME/.git" ]]; then
  git clone "$STEEL_REPO" "$STEEL_HOME"
fi

git -C "$STEEL_HOME" fetch origin "$STEEL_COMMIT"
git -C "$STEEL_HOME" checkout --detach "$STEEL_COMMIT"
git -C "$STEEL_HOME" reset --hard "$STEEL_COMMIT"

git -C "$STEEL_HOME" apply --check "$PATCH"
git -C "$STEEL_HOME" apply "$PATCH"

npm --prefix "$STEEL_HOME" install
npm --prefix "$STEEL_HOME" run build -w api

echo "Steel Browser ready at $STEEL_HOME @ $STEEL_COMMIT"

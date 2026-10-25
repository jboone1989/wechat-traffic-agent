#!/usr/bin/env bash
# Invoked on the Ferro host by the GitHub Actions delivery job.
# Arguments: commit-sha deploy-root systemd-unit system|user python-executable
set -Eeuo pipefail
umask 077

SHA="$1"
ROOT="$2"
UNIT="$3"
SCOPE="$4"
PYTHON="$5"

[[ "$SHA" =~ ^[a-f0-9]{40}$ ]] || { echo "Invalid commit SHA" >&2; exit 2; }
[[ "$ROOT" =~ ^/[a-zA-Z0-9/._-]+$ && "$ROOT" != "/" ]] || { echo "Invalid deploy root" >&2; exit 2; }
[[ "$UNIT" =~ ^[a-zA-Z0-9_.@-]+\.service$ ]] || { echo "Invalid service unit" >&2; exit 2; }
[[ "$SCOPE" == system || "$SCOPE" == user ]] || { echo "Invalid systemd scope" >&2; exit 2; }
[[ "$PYTHON" =~ ^/[a-zA-Z0-9/._-]+$ && -x "$PYTHON" ]] || { echo "Python runtime missing" >&2; exit 2; }
[[ -d "$ROOT" && -L "$ROOT/current" ]] || { echo "Existing Ferro release/current link not found; not bootstrapping another deployment" >&2; exit 2; }

CURRENT="$ROOT/current"
OLD="$(readlink -f "$CURRENT")"
RELEASE="$ROOT/releases/$SHA"
ARCHIVE="$ROOT/incoming/ferro-$SHA.tgz"
[[ -d "$OLD" && -f "$ARCHIVE" ]] || { echo "Current release or incoming archive missing" >&2; exit 2; }
[[ "$OLD" == "$RELEASE" ]] && { echo "Ferro already deployed at $SHA"; exit 0; }

if [[ "$SCOPE" == "system" ]]; then
  service() { sudo -n systemctl "$@" "$UNIT"; }
else
  service() { systemctl --user "$@" "$UNIT"; }
fi

STAGING=""
SWITCHED=0
rollback() {
  code="$1"
  trap - ERR
  if [[ "$SWITCHED" == 1 ]]; then
    echo "Ferro health check failed; restoring previous release $OLD" >&2
    ln -sfn "$OLD" "$CURRENT.rollback"
    mv -Tf "$CURRENT.rollback" "$CURRENT"
    service restart || true
  fi
  [[ -z "$STAGING" ]] || rm -rf -- "$STAGING"
  exit "$code"
}
trap 'rollback $?' ERR

if [[ ! -d "$RELEASE" ]]; then
  mkdir -p "$ROOT/releases"
  STAGING="$(mktemp -d "$ROOT/releases/.staging.XXXXXXXX")"
  tar --no-same-owner --no-same-permissions -xzf "$ARCHIVE" -C "$STAGING"
  [[ -f "$STAGING/pyproject.toml" && -f "$STAGING/scripts/run_ferro_supervisor.py" ]] || {
    echo "Release missing Ferro project files" >&2
    exit 1
  }
  # Existing host credentials are not put into a GitHub artifact.
  if [[ -f "$OLD/.env.ferro-llm" ]]; then
    cp -p "$OLD/.env.ferro-llm" "$STAGING/.env.ferro-llm"
  fi
  PYTHONPATH="$STAGING/src" "$PYTHON" -c "import wechat_traffic_agent; import wechat_traffic_agent.site_news"
  "$PYTHON" -m compileall -q "$STAGING/src/wechat_traffic_agent"
  mv -T "$STAGING" "$RELEASE"
  STAGING=""
fi

# Switch only after import and compilation succeed; state lives outside releases.
ln -sfn "$RELEASE" "$CURRENT.next"
mv -Tf "$CURRENT.next" "$CURRENT"
SWITCHED=1
service restart
sleep 8
service is-active --quiet
sleep 4
service is-active --quiet
SWITCHED=0
trap - ERR
echo "Ferro deployed: $SHA"

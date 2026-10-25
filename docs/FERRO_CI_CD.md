# Ferro GitHub Actions CI/CD

Ferro owns its own pipeline, separate from EventGraph. The workflow is
`.github/workflows/ferro-ci-cd.yml`. Existing four specialized CI workflows
remain in place; this workflow provides one always-on basic verification,
immutable release artifact and a **gated** production deployment.

## Lifecycle

1. Every pull request: Python 3.12 compile of all `src/`, `scripts/`,
   and `tests/`; syntax check for the release script; deterministic,
   offline regression tests. No API keys or production host access.
2. Every `main` push: re-run verification; create a tarball with
   `git archive HEAD` (committed files only), hash it and upload the
   `ferro-release` artifact. Never package state, .env files, or
   working-tree leftovers.
3. When repository variable `FERRO_CD_ENABLED=true`, and only then,
   `main` also deploys the successfully tested artifact to the
   configured Ferro host using standard SSH/scp. It switches an existing
   `current` release symlink, restarts the *existing* systemd service,
   checks that service twice, and rolls back the symlink/service on error.

No Docker build, new state store, extra agent runner or new app deployment
infrastructure is required. Source code is delivered to the existing
release-based runtime. The running Python environment and external state
storage are not replaced by the workflow.

## GitHub Actions runner selection

The repository can use a standard GitHub-hosted runner by default, or a
**self-hosted** runner by setting the repository variable
`FERRO_ACTIONS_RUNNER=self-hosted`. For a self-hosted choice, register
a Linux x64 Actions runner **to this Ferro repository**, independently
from any EventGraph runner. A runner registered only to EventGraph will
not pick up Ferro jobs.

On October 25–26, 2026, the repo's new test workflow and existing
workflows finished within seconds with zero runner time and no
executed steps. This appears to be GitHub-hosted runner allocation,
possibly an account Actions spending limit or billing restriction.
It is not evidence of Python test failure. If this remains the case,
enable a free self-hosted runner for Ferro rather than repeatedly
retrying hosted jobs. The pipeline must actually run and pass before
considering CI operational.

## One-time GitHub configuration

Use repository **Settings → Secrets and variables → Actions**.
Set repository variables:

| Variable | Value |
| --- | --- |
| `FERRO_CD_ENABLED` | Start with `false`; set `true` only after the actual running host and release path are confirmed |
| `FERRO_SSH_HOST` | DNS name or IP of the host **actually running Ferro** |
| `FERRO_SSH_USER` | Authorized non-root deployment account, e.g. `ubuntu` |
| `FERRO_SSH_PORT` | SSH port (optional; defaults to `22`) |
| `FERRO_DEPLOY_ROOT` | Existing release root, containing `current` symlink and `releases/`; **do not guess or set the EventGraph root** |
| `FERRO_SYSTEMD_UNIT` | Existing Ferro unit (e.g. `content-agent.service`) |
| `FERRO_SYSTEMD_SCOPE` | `system` or `user`, matching the real service |
| `FERRO_PYTHON` | Existing executable Python from Ferro's installed venv |

Provide these secret values (repository secrets or secrets in the
`ferro-production` environment):

- `FERRO_SSH_PRIVATE_KEY`: deployment account key with access only to
  the selected Ferro host.
- `FERRO_SSH_KNOWN_HOSTS`: the **verified** OpenSSH known-hosts line
  for this host. SSH checks host keys strictly and never uses `ssh-keyscan`
  as an unverified trust source.

For `FERRO_SYSTEMD_SCOPE=system`, the deployment account must already
have narrowly scoped passwordless permission for restart and is-active
of the specified service. For `user`, the account must own the unit.
Do not change permissions or switch to root just to satisfy CI.

## Target prerequisites

The target must already have:

- Existing `FERRO_DEPLOY_ROOT/current` symlink resolving to a working
  release; a source install alone is not enough.
- The existing Python/virtual environment with Ferro's runtime
  dependencies installed. Dependencies supplied by external
  `agent-runtime` or `virtualbrain` deployments remain external.
- Write access to `FERRO_DEPLOY_ROOT/incoming` and `releases`;
  permission to restart the configured service.
- A service definition reading its code from `current`, not a pinned
  historical release. Persistent data must be outside release
  directories.

The script will **refuse deployment** when any of these conditions do
not hold. It copies an existing `.env.ferro-llm` file from the old
release without displaying its contents. Other runtime configuration
is expected to live outside the release and remain owned by the host.

## Recovery

Any successful deployment records the SHA under `releases/<sha>`.
Rerunning a deployment at the same SHA is idempotent. During a failed
service restart/health check, `deploy/ci-release.sh` restores the old
`current` target and restarts the service.

The automatic test gate is not a full external-integration test. The
new workflow's unit tests are offline; the existing specialized test
workflows also run under their own triggers. For a broken dependency
installation or a missing runtime capability, the host preflight must
reject deployment before switching.

## Current activation state

The code does not turn on CD on its own. The target host was not
unambiguously verified at workflow authoring time: the Remote connector's
old `ferro` project path `/home/ubuntu/src/content-agent` did not
exist on VM09. The target variables and SSH secrets therefore must be
matched to the running Ferro instance before setting
`FERRO_CD_ENABLED=true`. This prevents accidentally releasing to
EventGraph's VM09, the EventGraph preview VM13 or cn-dev without
confirming which host is currently operating Ferro.

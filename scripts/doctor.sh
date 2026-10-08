#!/usr/bin/env bash
# doctor.sh — wrapper for `ark doctor` (usable from cron/CI, needs no login)
set -euo pipefail
ARK_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ARK_HOME"
exec "$ARK_HOME/.venv/bin/python" -m ark doctor "$@"
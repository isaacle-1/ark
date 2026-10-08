#!/usr/bin/env bash
# dev.sh — run the backend (hot reload on :8080) and the Vite dev server (:5173)
# The Vite dev server proxies /api and /svc to :8080, so open http://localhost:5173.
set -euo pipefail
ARK_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ARK_HOME"

export PATH="$ARK_HOME/bin/node/bin:$PATH"
export ARK_HOME

trap 'kill 0' EXIT INT TERM

"$ARK_HOME/.venv/bin/python" -m ark serve --reload &
BACK=$!

(cd "$ARK_HOME/ark/frontend" && npm run dev) &
FRONT=$!

wait "$BACK" "$FRONT"
#!/usr/bin/env bash
# install.sh — idempotent ARK installer. Requires root (apt + systemd + user).
# Run from inside the cloned repo:  sudo bash scripts/install.sh [--dev]
set -euo pipefail

ARK_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
echo "==> ARK_HOME: $ARK_HOME"

if [ "$(id -u)" != "0" ]; then
  echo "WARNING: not running as root — skipping apt packages, ark user and systemd unit."
  echo "         Run with sudo for a full install."
  SKIP_SYSTEM=1
else
  : "${SKIP_SYSTEM:=0}"
fi

# --apt dependencies (recorded here for a fresh LXC) ----------------------
apt_deps=(python3 python3-venv python3-dev ca-certificates curl git make xz-utils)
if [ "${SKIP_SYSTEM:-0}" = "0" ]; then
  echo "==> Installing system packages: ${apt_deps[*]}"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq "${apt_deps[@]}"
fi

# --data layout + service user ---------------------------------------------
mkdir -p "$ARK_HOME/data"
install -d -o ark -g ark "$ARK_HOME/data" 2>/dev/null || true
if [ "${SKIP_SYSTEM:-0}" = "0" ]; then
  if ! id ark >/dev/null 2>&1; then
    echo "==> Creating 'ark' service user"
    mkdir -p "$ARK_HOME/data/home"
    useradd --home-dir "$ARK_HOME/data/home" --shell /bin/bash --create-home ark
  fi
  chown -R ark:ark "$ARK_HOME/data"
  echo "==> data/ owned by ark (uid $(id -u ark))"
fi

# --node (only needed to build the frontend from source) -------------------
export PATH="$ARK_HOME/bin/node/bin:$PATH"
NODE_URL="${NODE_URL:-https://nodejs.org/dist/latest-v22.x/node-v22.23.3-linux-x64.tar.xz}"
if [ ! -e "$ARK_HOME/ark/frontend/dist/index.html" ]; then
  if command -v node >/dev/null 2>&1; then
    echo "==> node found at $(command -v node)"
  else
    echo "==> Downloading Node 22 (build-time only): $NODE_URL"
    mkdir -p "$ARK_HOME/bin"
    curl -fsSL -o "$ARK_HOME/bin/node.tar.xz" "$NODE_URL"
    node_ver="$(basename "$NODE_URL" | sed 's/-linux-x64\.tar\.xz//')"
    mkdir -p "$ARK_HOME/bin/$node_ver"
    tar -xJf "$ARK_HOME/bin/node.tar.xz" --strip-components=1 -C "$ARK_HOME/bin/$node_ver"
    rm -f "$ARK_HOME/bin/node.tar.xz"
    ln -sfn "$node_ver" "$ARK_HOME/bin/node"
  fi
fi

# --python venv ------------------------------------------------------------
if [ ! -x "$ARK_HOME/.venv/bin/python" ]; then
  echo "==> Creating venv"
  python3 -m venv "$ARK_HOME/.venv"
fi
echo "==> Installing python deps"
"$ARK_HOME/.venv/bin/pip" install --upgrade pip
"$ARK_HOME/.venv/bin/pip" install -r "$ARK_HOME/requirements.txt"
if [ "${DEV:-0}" = "1" ] || [ "${1:-}" = "--dev" ]; then
  "$ARK_HOME/.venv/bin/pip" install -r "$ARK_HOME/requirements-dev.txt"
fi

# --frontend build (source path; release bundle skips node) ----------------
if [ ! -e "$ARK_HOME/ark/frontend/dist/index.html" ]; then
  echo "==> Building frontend (first npm install may take a while)"
  (cd "$ARK_HOME/ark/frontend" && npm ci && npm run build)
  "$ARK_HOME/.venv/bin/python" "$ARK_HOME/scripts/check_no_external_urls.py" \
    "$ARK_HOME/ark/frontend/dist"
fi

# --config, db, admin --------------------------------------------------------
echo "==> Initializing config + database"
if [ "${SKIP_SYSTEM:-0}" = "0" ]; then
  # Run as the service user: root-created files in data/ break the unit.
  runuser -u ark -- env HOME="$ARK_HOME/data/home" ARK_HOME="$ARK_HOME" \
    "$ARK_HOME/.venv/bin/python" -m ark init
else
  "$ARK_HOME/.venv/bin/python" -m ark init
fi

# --systemd unit -----------------------------------------------------------
if [ "${SKIP_SYSTEM:-0}" = "0" ]; then
  echo "==> Installing systemd unit ark.service"
  sed "s|@ARK_HOME@|$ARK_HOME|g" "$ARK_HOME/scripts/ark.service" \
    > /etc/systemd/system/ark.service
  systemctl daemon-reload
  systemctl enable ark.service
  systemctl restart ark.service
fi

# --verify as the service user (doctor writes into data/ as ark) -----------
echo "==> Verifying"
if [ "${SKIP_SYSTEM:-0}" = "0" ]; then
  runuser -u ark -- env HOME="$ARK_HOME/data/home" ARK_HOME="$ARK_HOME" ARK_SKIP_NODE=1 \
    "$ARK_HOME/.venv/bin/python" -m ark doctor || true
  chown -R ark:ark "$ARK_HOME/data"
else
  "$ARK_HOME/.venv/bin/python" -m ark doctor || true
fi
echo "==> Done. Open http://<server>:8080 (admin creds in $ARK_HOME/data/config/initial-credentials.txt)"
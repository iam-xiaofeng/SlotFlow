#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
NODE_VERSION="${SLOTFLOW_NODE_VERSION:-22}"
PNPM_VERSION="${SLOTFLOW_PNPM_VERSION:-}"
SKIP_SYSTEM_PACKAGES="${SLOTFLOW_SKIP_SYSTEM_PACKAGES:-0}"

log() {
  printf '\033[1;34m[slotflow-bootstrap]\033[0m %s\n' "$*"
}

warn() {
  printf '\033[1;33m[slotflow-bootstrap warn]\033[0m %s\n' "$*" >&2
}

die() {
  printf '\033[1;31m[slotflow-bootstrap error]\033[0m %s\n' "$*" >&2
  exit 1
}

has_cmd() {
  command -v "$1" >/dev/null 2>&1
}

run_as_root() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
    return
  fi

  if has_cmd sudo; then
    sudo "$@"
    return
  fi

  die "Installing system packages requires root or sudo. Install the missing packages manually, or rerun with SLOTFLOW_SKIP_SYSTEM_PACKAGES=1 if they are already present."
}

install_system_packages() {
  if [ "$SKIP_SYSTEM_PACKAGES" = "1" ]; then
    warn "Skipping system package installation because SLOTFLOW_SKIP_SYSTEM_PACKAGES=1."
    return
  fi

  if has_cmd curl && has_cmd git && has_cmd make && has_cmd python3 && has_cmd fuser \
    && { has_cmd cc || has_cmd gcc || has_cmd clang; }; then
    log "System package dependencies are already available."
    return
  fi

  if has_cmd apt-get; then
    log "Installing system packages with apt-get..."
    run_as_root apt-get update
    run_as_root apt-get install -y \
      build-essential \
      ca-certificates \
      curl \
      git \
      make \
      psmisc \
      python3 \
      python3-venv
    return
  fi

  if has_cmd dnf; then
    log "Installing system packages with dnf..."
    run_as_root dnf install -y \
      ca-certificates \
      curl \
      gcc \
      gcc-c++ \
      git \
      make \
      psmisc \
      python3
    return
  fi

  if has_cmd yum; then
    log "Installing system packages with yum..."
    run_as_root yum install -y \
      ca-certificates \
      curl \
      gcc \
      gcc-c++ \
      git \
      make \
      psmisc \
      python3
    return
  fi

  if has_cmd pacman; then
    log "Installing system packages with pacman..."
    run_as_root pacman -Sy --needed --noconfirm \
      base-devel \
      ca-certificates \
      curl \
      git \
      make \
      psmisc \
      python
    return
  fi

  if has_cmd apk; then
    log "Installing system packages with apk..."
    run_as_root apk add --no-cache \
      build-base \
      ca-certificates \
      curl \
      git \
      make \
      psmisc \
      python3
    return
  fi

  if has_cmd brew; then
    log "Installing system packages with Homebrew..."
    brew install curl git make python psmisc || warn "Homebrew package installation had non-fatal failures."
    return
  fi

  warn "No supported system package manager found. Install make, curl, git, python3, and psmisc manually if missing."
}

require_basic_tools() {
  has_cmd curl || die "curl is required to install uv/Node tooling. Install curl and rerun ./bootstrap.sh."
  has_cmd git || die "git is required by package managers and dependency installers. Install git and rerun ./bootstrap.sh."
  has_cmd make || die "make is required for the root Makefile. Install make and rerun ./bootstrap.sh."
  if ! has_cmd fuser; then
    warn "fuser was not found; 'make kill' will not work until psmisc or an equivalent package is installed."
  fi
}

detect_pnpm_version() {
  if [ -n "$PNPM_VERSION" ]; then
    return
  fi

  if [ -f "$FRONTEND_DIR/package.json" ]; then
    PNPM_VERSION="$(
      sed -n 's/.*"packageManager"[[:space:]]*:[[:space:]]*"pnpm@\([^"]*\)".*/\1/p' \
        "$FRONTEND_DIR/package.json" | head -n 1
    )"
  fi

  PNPM_VERSION="${PNPM_VERSION:-10.26.2}"
}

install_uv() {
  export PATH="$HOME/.local/bin:$PATH"

  if has_cmd uv; then
    log "uv already installed: $(uv --version)"
    return
  fi

  log "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  has_cmd uv || die "uv installation finished but uv is not on PATH. Add ~/.local/bin to PATH and rerun."
  log "Installed $(uv --version)."
}

node_major_version() {
  node -p "Number(process.versions.node.split('.')[0])" 2>/dev/null || printf '0'
}

install_node_and_pnpm() {
  detect_pnpm_version

  local node_major
  node_major="$(node_major_version)"
  if has_cmd node && [ "$node_major" -ge 20 ] && has_cmd pnpm; then
    log "Node already installed: $(node --version)"
    log "pnpm already installed: $(pnpm --version)"
    return
  fi

  if has_cmd node && [ "$node_major" -ge 20 ]; then
    install_pnpm_with_existing_node
    return
  fi

  install_node_and_pnpm_with_volta
}

install_pnpm_with_existing_node() {
  detect_pnpm_version

  if has_cmd pnpm; then
    log "pnpm already installed: $(pnpm --version)"
    return
  fi

  if has_cmd corepack; then
    log "Installing pnpm@$PNPM_VERSION with corepack..."
    if corepack enable && corepack prepare "pnpm@$PNPM_VERSION" --activate; then
      has_cmd pnpm || die "corepack finished but pnpm is not on PATH."
      return
    fi
    warn "corepack could not activate pnpm@$PNPM_VERSION; falling back to npm if available."
  fi

  if has_cmd npm; then
    log "Installing pnpm@$PNPM_VERSION with npm..."
    npm install -g "pnpm@$PNPM_VERSION"
    has_cmd pnpm || die "npm finished but pnpm is not on PATH."
    return
  fi

  die "Node is installed but neither corepack nor npm is available. Install pnpm@$PNPM_VERSION manually."
}

install_node_and_pnpm_with_volta() {
  detect_pnpm_version

  export VOLTA_HOME="${VOLTA_HOME:-$HOME/.volta}"
  export PATH="$VOLTA_HOME/bin:$PATH"

  if ! has_cmd volta; then
    log "Installing Volta for user-local Node/pnpm management..."
    curl https://get.volta.sh | bash
    export PATH="$VOLTA_HOME/bin:$PATH"
  fi

  has_cmd volta || die "Volta installation finished but volta is not on PATH. Add ~/.volta/bin to PATH and rerun."

  log "Installing Node@$NODE_VERSION and pnpm@$PNPM_VERSION with Volta..."
  volta install "node@$NODE_VERSION" "pnpm@$PNPM_VERSION"
  hash -r || true

  has_cmd node || die "Node installation finished but node is not on PATH."
  has_cmd pnpm || die "pnpm installation finished but pnpm is not on PATH."
  log "Installed Node $(node --version) and pnpm $(pnpm --version)."
}

install_backend_dependencies() {
  log "Installing backend dependencies with uv..."
  cd "$BACKEND_DIR"
  uv sync
}

install_frontend_dependencies() {
  log "Installing frontend dependencies with pnpm..."
  cd "$FRONTEND_DIR"
  if [ -f pnpm-lock.yaml ]; then
    pnpm install --frozen-lockfile
  else
    pnpm install
  fi
}

print_next_steps() {
  cat <<EOF

SlotFlow bootstrap complete.

You can now run:
  make verify
  make dev
  make kill

If this shell still cannot find uv/pnpm after the script exits, start a new terminal or run:
  export PATH="\$HOME/.local/bin:\$HOME/.volta/bin:\$PATH"
EOF
}

main() {
  cd "$ROOT_DIR"
  install_system_packages
  require_basic_tools
  install_uv
  install_node_and_pnpm
  install_backend_dependencies
  install_frontend_dependencies
  print_next_steps
}

main "$@"

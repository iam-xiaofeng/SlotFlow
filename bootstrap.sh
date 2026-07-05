#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
NODE_VERSION="${SLOTFLOW_NODE_VERSION:-22}"
PNPM_VERSION="${SLOTFLOW_PNPM_VERSION:-}"
SKIP_SYSTEM_PACKAGES="${SLOTFLOW_SKIP_SYSTEM_PACKAGES:-0}"
SKIP_DOCKER="${SLOTFLOW_SKIP_DOCKER:-0}"
DOCKER_IMAGE="${SLOTFLOW_DOCKER_IMAGE:-python:3.12}"
# 空格分隔的 registry mirror 列表;默认仅在直连 Docker Hub 拉取失败时才写入这组国内源。
DOCKER_REGISTRY_MIRRORS="${SLOTFLOW_DOCKER_REGISTRY_MIRRORS:-https://docker.1ms.run https://docker.m.daocloud.io https://dockerproxy.net}"

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

# --- Docker 沙箱(sandbox_exec 的底层执行环境) --------------------------------

docker_daemon_reachable() {
  run_as_root docker info --format '{{.ServerVersion}}' >/dev/null 2>&1
}

start_docker_daemon() {
  if docker_daemon_reachable; then
    return 0
  fi
  # 与 app/harness/sandbox/docker_engine.py::ensure_daemon 相同的三级回退:
  # systemd 主机 → sysvinit 包装 → 无 init 管理(典型 WSL)直接后台拉起 dockerd。
  run_as_root systemctl start docker >/dev/null 2>&1 || true
  docker_daemon_reachable && return 0
  run_as_root service docker start >/dev/null 2>&1 || true
  docker_daemon_reachable && return 0
  run_as_root sh -c 'nohup dockerd > /var/log/slotflow-dockerd.log 2>&1 &' || true
  local i
  for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
    docker_daemon_reachable && return 0
    sleep 1
  done
  return 1
}

write_docker_registry_mirrors() {
  if [ -f /etc/docker/daemon.json ]; then
    warn "/etc/docker/daemon.json already exists; not overwriting. Add registry-mirrors manually if pulls keep timing out."
    return 1
  fi
  log "Configuring Docker registry mirrors (direct pull from Docker Hub failed)..."
  local mirrors_json=""
  local mirror
  for mirror in $DOCKER_REGISTRY_MIRRORS; do
    mirrors_json="${mirrors_json:+$mirrors_json, }\"$mirror\""
  done
  run_as_root mkdir -p /etc/docker
  printf '{\n  "registry-mirrors": [%s]\n}\n' "$mirrors_json" \
    | run_as_root tee /etc/docker/daemon.json >/dev/null
  # 重启守护进程使镜像源生效
  run_as_root systemctl restart docker >/dev/null 2>&1 \
    || run_as_root service docker restart >/dev/null 2>&1 \
    || { run_as_root pkill dockerd >/dev/null 2>&1 || true; sleep 2; start_docker_daemon; }
}

enable_wsl_systemd() {
  # WSL 且 PID1 非 systemd:启用 [boot] systemd=true,下次 wsl --shutdown 后
  # docker.service 随发行版自启,不再依赖手动拉起。
  if ! grep -qi microsoft /proc/version 2>/dev/null; then
    return
  fi
  if [ "$(ps -p 1 -o comm= 2>/dev/null)" = "systemd" ]; then
    return
  fi
  if run_as_root grep -q '^\[boot\]' /etc/wsl.conf 2>/dev/null; then
    return
  fi
  log "Enabling systemd in /etc/wsl.conf (takes effect after 'wsl --shutdown')..."
  printf '\n[boot]\nsystemd=true\n' | run_as_root tee -a /etc/wsl.conf >/dev/null
}

install_docker_cli() {
  if has_cmd docker; then
    log "Docker CLI already installed: $(docker --version 2>/dev/null || echo docker)"
    return
  fi
  if [ "$SKIP_SYSTEM_PACKAGES" = "1" ]; then
    warn "Docker CLI missing but SLOTFLOW_SKIP_SYSTEM_PACKAGES=1; skipping install. sandbox_exec will not work."
    return 1
  fi
  if has_cmd apt-get; then
    log "Installing Docker Engine with apt-get..."
    run_as_root apt-get update
    run_as_root apt-get install -y docker.io docker-compose-v2
  elif has_cmd dnf; then
    log "Installing Docker Engine with dnf..."
    run_as_root dnf install -y moby-engine docker-compose-plugin
  elif has_cmd pacman; then
    log "Installing Docker Engine with pacman..."
    run_as_root pacman -Sy --needed --noconfirm docker docker-compose
  else
    warn "No supported package manager for automatic Docker install. Install Docker Engine manually; sandbox_exec needs it."
    return 1
  fi
}

setup_docker_sandbox() {
  if [ "$SKIP_DOCKER" = "1" ]; then
    warn "Skipping Docker sandbox setup because SLOTFLOW_SKIP_DOCKER=1."
    return
  fi

  install_docker_cli || return 0
  has_cmd docker || return 0

  # 当前用户加入 docker 组(重新登录后免 sudo 使用;本脚本内经 root 通道操作不受影响)
  if [ "$(id -u)" -ne 0 ] && ! id -nG "$USER" 2>/dev/null | tr ' ' '\n' | grep -qx docker; then
    run_as_root usermod -aG docker "$USER" >/dev/null 2>&1 \
      && warn "Added $USER to the docker group; log out and back in for non-sudo docker." \
      || true
  fi

  enable_wsl_systemd

  if ! start_docker_daemon; then
    warn "Docker daemon could not be started automatically. Check /var/log/slotflow-dockerd.log; sandbox_exec will retry at runtime."
    return
  fi
  log "Docker daemon is running."

  if run_as_root docker image inspect "$DOCKER_IMAGE" >/dev/null 2>&1; then
    log "Sandbox image $DOCKER_IMAGE already present."
    return
  fi

  log "Pre-pulling sandbox image $DOCKER_IMAGE (first pull may take a few minutes)..."
  if run_as_root docker pull "$DOCKER_IMAGE"; then
    log "Sandbox image ready."
    return
  fi
  if write_docker_registry_mirrors && start_docker_daemon && run_as_root docker pull "$DOCKER_IMAGE"; then
    log "Sandbox image ready (via registry mirrors)."
    return
  fi
  warn "Could not pre-pull $DOCKER_IMAGE. sandbox_exec will pull on first use; configure registry mirrors or a proxy if pulls keep failing."
}

print_next_steps() {
  cat <<EOF

SlotFlow bootstrap complete.

You can now run:
  make verify
  make dev
  make kill

Docker sandbox (sandbox_exec):
  - image: $DOCKER_IMAGE (pre-pulled when possible); persistent container is created on first use
  - if this is WSL and systemd was just enabled, run 'wsl --shutdown' once from Windows so the
    Docker daemon auto-starts on boot (until then SlotFlow starts it on demand)
  - skip all Docker setup with SLOTFLOW_SKIP_DOCKER=1

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
  setup_docker_sandbox
  print_next_steps
}

main "$@"

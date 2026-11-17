#!/usr/bin/env bash
# Shared environment wrapper for cloud-pcb-studio.
# Keeps tool caches/configs inside $PROJECT_ROOT/.run while preserving proxy settings.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PCB_STUDIO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

mkdir -p "$PCB_STUDIO_ROOT/.run/home/.config" \
         "$PCB_STUDIO_ROOT/.run/cache/uv" \
         "$PCB_STUDIO_ROOT/.run/cache/npm" \
         "$PCB_STUDIO_ROOT/.run/mpl" \
         "$PCB_STUDIO_ROOT/.mamba"

export HOME="$PCB_STUDIO_ROOT/.run/home"
export XDG_CONFIG_HOME="$PCB_STUDIO_ROOT/.run/home/.config"
export XDG_CACHE_HOME="$PCB_STUDIO_ROOT/.run/cache"
export XDG_DATA_HOME="$PCB_STUDIO_ROOT/.run/home/.local/share"
export XDG_STATE_HOME="$PCB_STUDIO_ROOT/.run/home/.local/state"
export MPLCONFIGDIR="$PCB_STUDIO_ROOT/.run/mpl"
export UV_CACHE_DIR="$PCB_STUDIO_ROOT/.run/cache/uv"
export npm_config_cache="$PCB_STUDIO_ROOT/.run/cache/npm"
export MAMBA_ROOT_PREFIX="$PCB_STUDIO_ROOT/.mamba"

export PATH="$PCB_STUDIO_ROOT/.venv/bin:$PCB_STUDIO_ROOT/bin:$PCB_STUDIO_ROOT/.mamba/ngspice/bin:$PCB_STUDIO_ROOT/node_modules/.bin:$PATH"
export NGSPICE_LIB_DIR="$PCB_STUDIO_ROOT/.mamba/ngspice/lib"
export NODE_PATH="$PCB_STUDIO_ROOT/node_modules"

#!/usr/bin/env bash
# setup_emsdk.sh — Install Emscripten + SCons for stellarium-web-engine WASM build.
# No sudo required; everything lands in $HOME.
#
# Usage:
#   bash scripts/setup_emsdk.sh          # clone+install+activate emsdk, pip install scons
#   source scripts/setup_emsdk.sh env     # only source the emsdk env into current shell
#
# After install, build stellarium-web-engine:
#   source "$HOME/emsdk/emsdk_env.sh"
#   cd stellarium-web-engine
#   make js           # release -> build/stellarium-web-engine.{js,wasm}
#   make js-debug     # debug build

set -euo pipefail

EMSDK_DIR="${EMSDK_DIR:-$HOME/emsdk}"

if [[ "${1:-}" == "env" ]]; then
  # Convenience: source the emsdk env in the current shell.
  # Usage: source scripts/setup_emsdk.sh env
  # shellcheck disable=SC1090
  source "$EMSDK_DIR/emsdk_env.sh"
  return 0 2>/dev/null || exit 0
fi

echo "[1/4] Cloning emsdk to $EMSDK_DIR (shallow)..."
if [[ ! -d "$EMSDK_DIR/.git" ]]; then
  git clone --depth 1 https://github.com/emscripten-core/emsdk.git "$EMSDK_DIR"
else
  echo "  emsdk already cloned, skipping."
fi

echo "[2/4] Running emsdk install latest (downloads ~300MB LLVM/wasm + node)..."
cd "$EMSDK_DIR"
./emsdk install latest

echo "[3/4] Activating emsdk latest..."
./emsdk activate latest

echo "[4/4] Installing SCons (python build driver)..."
python3 -m pip install --user scons

# shellcheck disable=SC1090
source "$EMSDK_DIR/emsdk_env.sh"

echo
echo "=== Verify ==="
emcc --version
which scons emscons

cat <<EOF

Done. To build stellarium-web-engine WASM:

  source "$EMSDK_DIR/emsdk_env.sh"
  cd <repo>/stellarium-web-engine
  make js            # release
  make js-debug      # debug

Output: build/stellarium-web-engine.js + build/stellarium-web-engine.wasm
EOF

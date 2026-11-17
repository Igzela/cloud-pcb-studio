#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

mkdir -p "$ROOT/bin" "$ROOT/data" "$ROOT/artifacts" "$ROOT/contracts" /tmp/eda-dl
trap 'rm -rf /tmp/eda-dl' EXIT

echo "=== [1/5] Downloading fastroute v0.1.14 & freerouting-cli v2.5.0 ==="
gh release download v0.1.14 -R parisxmas/fastroute -p 'fastroute-*-linux-x64.tar.gz' -D /tmp/eda-dl --clobber
mkdir -p /tmp/eda-dl/fr_rust
tar -xzf /tmp/eda-dl/fastroute-*-linux-x64.tar.gz -C /tmp/eda-dl/fr_rust
# Exclude macOS AppleDouble ._fastroute files
FASTROUTE_BIN=$(find /tmp/eda-dl/fr_rust -type f -name 'fastroute' ! -name '._*' | head -n 1)
cp "$FASTROUTE_BIN" "$ROOT/bin/fastroute"
chmod +x "$ROOT/bin/fastroute"
"$ROOT/bin/fastroute" --version || "$ROOT/bin/fastroute" --help | head -n 5

gh release download v2.5.0 -R freerouting/freerouting -p 'freerouting-cli-*-linux-x64.tar.gz' -D /tmp/eda-dl --clobber
mkdir -p /tmp/eda-dl/fr_java
tar -xzf /tmp/eda-dl/freerouting-cli-*-linux-x64.tar.gz -C /tmp/eda-dl/fr_java
FREEROUTING_BIN=$(find /tmp/eda-dl/fr_java -type f -name 'freerouting-cli' ! -name '._*' | head -n 1)
cp "$FREEROUTING_BIN" "$ROOT/bin/freerouting-cli"
chmod +x "$ROOT/bin/freerouting-cli"
"$ROOT/bin/freerouting-cli" -h | head -n 5

source "$SCRIPT_DIR/env.sh"

echo "=== [2/5] Installing micromamba & conda-forge ngspice 41 ==="
if [ ! -x "$ROOT/bin/micromamba" ]; then
  curl -Ls https://micro.mamba.pm/api/micromamba/linux-64/latest | tar -xvj -C /tmp/eda-dl bin/micromamba
  cp /tmp/eda-dl/bin/micromamba "$ROOT/bin/micromamba"
  chmod +x "$ROOT/bin/micromamba"
fi
if [ ! -x "$ROOT/.mamba/ngspice/bin/ngspice" ]; then
  "$ROOT/bin/micromamba" create -y -p "$ROOT/.mamba/ngspice" -c conda-forge ngspice
fi
"$ROOT/.mamba/ngspice/bin/ngspice" -v | head -n 5

echo "=== [3/5] Creating Python 3.11 venv & EDA packages ==="
if [ ! -x "$ROOT/.venv/bin/python" ]; then
  uv venv --python python3 "$ROOT/.venv"
fi
uv pip install --python "$ROOT/.venv/bin/python" \
  kiutils gerbonara "pygerber[svg]" shapely networkx scipy matplotlib pillow cairosvg resvg-py schemdraw PySpice pytest pyyaml

echo "=== [4/5] Installing Node 24 tscircuit + esbuild stack ==="
if [ ! -f "$ROOT/package.json" ]; then
  cat > "$ROOT/package.json" <<'PKG'
{
  "name": "cloud-pcb-studio",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "dependencies": {}
}
PKG
fi
npm install --prefix "$ROOT" --no-audit --no-fund \
  @tscircuit/core circuit-to-svg circuit-json-to-gerber circuit-json-to-kicad tscircuit esbuild

echo "=== [5/5] Caching live JLCPCB Basic & Preferred parts catalog ==="
curl -fsSL "https://cdfer.github.io/jlcpcb-parts-database/jlcpcb-components-basic-preferred.csv" -o "$ROOT/data/jlcpcb-basic-preferred.csv"
wc -l "$ROOT/data/jlcpcb-basic-preferred.csv"
curl -fsSL "https://jlcsearch.tscircuit.com/voltage_regulators/list.json" -o "$ROOT/data/jlcsearch-regulators.json" || true
curl -fsSL "https://jlcsearch.tscircuit.com/resistors/list.json?resistance=5.1k" -o "$ROOT/data/jlcsearch-5k1.json" || true
echo "=== Cloud EDA Stack Installation Complete ==="

#!/usr/bin/env bash
# Build claudex.mcpb — the Claude desktop-app extension package.
# Usage: ./desktop-extension/build.sh   (from repo root or this dir)
set -euo pipefail
cd "$(dirname "$0")"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
# Fail closed if the launcher shell snippet doesn't parse (it carries control flow since v1.8.2)
python3 -c 'import json,sys; sys.stdout.write(json.load(open("manifest.json"))["server"]["mcp_config"]["args"][1])' \
  | /bin/sh -n || { echo "error: launcher script in manifest.json failed sh -n" >&2; exit 1; }
# The lockfile travels with the server: the launcher runs `uv run --locked`,
# which refuses to start without it (v2.4).
[ -f ../server/server.py.lock ] || { echo "error: server/server.py.lock missing (run: uv lock --script server/server.py)" >&2; exit 1; }
mkdir -p "$STAGE/server"
cp manifest.json "$STAGE/"
cp ../server/server.py ../server/server.py.lock "$STAGE/server/"
# Manifest validation is mandatory for release builds (v2.4). Set
# CLAUDEX_SKIP_MCPB_VALIDATE=1 only for local experiments.
if [ "${CLAUDEX_SKIP_MCPB_VALIDATE:-}" != "1" ]; then
  command -v npx >/dev/null 2>&1 || { echo "error: npx not found; needed for 'mcpb validate' (set CLAUDEX_SKIP_MCPB_VALIDATE=1 to skip for local experiments)" >&2; exit 1; }
  npx -y @anthropic-ai/mcpb validate manifest.json || { echo "error: manifest validation failed" >&2; exit 1; }
fi
rm -f claudex.mcpb
(cd "$STAGE" && zip -qr - manifest.json server) > claudex.mcpb
echo "Built: desktop-extension/claudex.mcpb"
echo "Install: double-click it with the Claude desktop app installed."
echo "Prereqs on the target Mac: uv >= 0.11.4 (https://astral.sh/uv; 'uv self update') + codex CLI (npm i -g @openai/codex@latest && codex login)"

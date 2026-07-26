#!/usr/bin/env bash
# Build claudex.mcpb — the Claude desktop-app extension package.
# Usage: ./desktop-extension/build.sh   (from repo root or this dir)
set -euo pipefail
cd "$(dirname "$0")"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/server"
cp manifest.json "$STAGE/"
cp ../server/server.py "$STAGE/server/"
# Validate manifest if the official MCPB tooling is available (non-fatal)
if command -v npx >/dev/null 2>&1; then
  npx -y @anthropic-ai/mcpb validate manifest.json || echo "warning: manifest validation failed or tooling unavailable"
fi
rm -f claudex.mcpb
(cd "$STAGE" && zip -qr - manifest.json server) > claudex.mcpb
echo "Built: desktop-extension/claudex.mcpb"
echo "Install: double-click it with the Claude desktop app installed."
echo "Prereqs on the target Mac: uv (https://astral.sh/uv) + codex CLI (npm i -g @openai/codex && codex login)"

#!/usr/bin/env bash
set -euo pipefail

# Claudex Installer — adds the marketplace and installs the plugin
# Usage: curl -fsSL https://raw.githubusercontent.com/OmriT123/Claudex/main/install.sh | bash

MARKETPLACE_NAME="omri-plugins"
MARKETPLACE_REPO="OmriT123/claude-plugins"
MARKETPLACE_URL="https://github.com/${MARKETPLACE_REPO}.git"
PLUGIN_DIR="$HOME/.claude/plugins"
MARKETPLACE_DIR="$PLUGIN_DIR/marketplaces/$MARKETPLACE_NAME"
KNOWN_FILE="$PLUGIN_DIR/known_marketplaces.json"

echo "Installing Claudex — Claude Code's Codex teammate"
echo ""

# Check prerequisites
for cmd in claude codex uv git; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "Error: '$cmd' is not installed."
    case "$cmd" in
      claude) echo "  Install Claude Code: https://docs.anthropic.com/en/docs/claude-code" ;;
      codex)  echo "  Install: npm i -g @openai/codex && codex login" ;;
      uv)     echo "  Install: curl -LsSf https://astral.sh/uv/install.sh | sh" ;;
      git)    echo "  Install git from https://git-scm.com" ;;
    esac
    exit 1
  fi
done

# Ensure directories exist
mkdir -p "$PLUGIN_DIR/marketplaces"

# Clone or update the marketplace
if [ -d "$MARKETPLACE_DIR/.git" ]; then
  echo "Updating marketplace..."
  git -C "$MARKETPLACE_DIR" pull --quiet
else
  if [ -d "$MARKETPLACE_DIR" ]; then
    rm -rf "$MARKETPLACE_DIR"
  fi
  echo "Adding marketplace..."
  git clone --quiet "$MARKETPLACE_URL" "$MARKETPLACE_DIR"
fi

# Register the marketplace in known_marketplaces.json
NOW=$(date -u +"%Y-%m-%dT%H:%M:%S.000Z")

if [ ! -f "$KNOWN_FILE" ]; then
  # No file yet — create it
  cat > "$KNOWN_FILE" << EOF
{
  "$MARKETPLACE_NAME": {
    "source": {
      "source": "github",
      "repo": "$MARKETPLACE_REPO"
    },
    "installLocation": "$MARKETPLACE_DIR",
    "lastUpdated": "$NOW"
  }
}
EOF
  echo "Created marketplace registry."
elif ! grep -q "\"$MARKETPLACE_NAME\"" "$KNOWN_FILE"; then
  # File exists but marketplace not registered — inject before final }
  # Remove trailing whitespace/newline and closing brace, append new entry
  ENTRY=$(cat << EOF
,
  "$MARKETPLACE_NAME": {
    "source": {
      "source": "github",
      "repo": "$MARKETPLACE_REPO"
    },
    "installLocation": "$MARKETPLACE_DIR",
    "lastUpdated": "$NOW"
  }
}
EOF
  )
  # Replace the last } with the new entry
  sed -i.bak '$s/}$//' "$KNOWN_FILE"
  echo "$ENTRY" >> "$KNOWN_FILE"
  rm -f "${KNOWN_FILE}.bak"
  echo "Registered marketplace."
else
  echo "Marketplace already registered."
fi

# Install the plugin
echo ""
echo "Installing Claudex plugin..."
claude plugin install claudex

# Pre-warm uv dependencies so the MCP server starts instantly on first launch.
# Without this, uv downloads packages on first startup, which can exceed
# Claude Code's MCP connection timeout and leave the server in a failed state.
CACHE_DIR="$PLUGIN_DIR/cache/omri-plugins/claudex"
SERVER_PY=$(find "$CACHE_DIR" -name "server.py" -path "*/server/server.py" 2>/dev/null | head -1)
if [ -n "$SERVER_PY" ]; then
  echo "Pre-warming dependencies (first run may take a few seconds)..."
  uv run "$SERVER_PY" &>/dev/null &
  WARM_PID=$!
  sleep 3
  kill "$WARM_PID" 2>/dev/null
  wait "$WARM_PID" 2>/dev/null
  echo "Dependencies cached."
fi

echo ""
echo "Done! Start a new Claude Code session and try:"
echo "  /mcp                              — verify claudex tools are loaded"
echo "  use claudex_ping to test codex    — verify Codex connectivity"
echo "  /claudex:plan <your task>         — parallel planning with Codex"

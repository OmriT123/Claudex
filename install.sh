#!/usr/bin/env bash
# Claudex installer: adds the marketplace, installs the plugin, and prepares
# the server's Python dependencies so the first session starts instantly.
# Usage: curl -fsSL https://raw.githubusercontent.com/OmriT123/Claudex/main/install.sh | bash
#
# Uses Claude Code's own plugin commands (no hand-edited JSON) and prepares
# the dependencies of the version that was actually installed (v2.3.1).
set -euo pipefail

MARKETPLACE_NAME="omri-plugins"
MARKETPLACE_REPO="OmriT123/claude-plugins"
PLUGIN_ID="claudex@${MARKETPLACE_NAME}"

echo "Installing Claudex: Claude Code's Codex teammate"
echo ""

# Check prerequisites
missing=0
for cmd in claude codex uv git; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "Error: '$cmd' is not installed."
    case "$cmd" in
      claude) echo "  Install Claude Code: https://code.claude.com/docs/en/setup" ;;
      codex)  echo "  Install: npm i -g @openai/codex@latest && codex login" ;;
      uv)     echo "  Install: curl -LsSf https://astral.sh/uv/install.sh | sh" ;;
      git)    echo "  Install git from https://git-scm.com" ;;
    esac
    missing=1
  fi
done
[ "$missing" -eq 0 ] || exit 1

# Add (or refresh) the marketplace through Claude Code itself
if claude plugin marketplace list 2>/dev/null | grep -q "$MARKETPLACE_NAME"; then
  echo "Updating marketplace..."
  claude plugin marketplace update "$MARKETPLACE_NAME"
else
  echo "Adding marketplace..."
  claude plugin marketplace add "$MARKETPLACE_REPO"
fi

# Install, or update an existing install
echo ""
if claude plugin list 2>/dev/null | grep -q "$PLUGIN_ID"; then
  echo "Updating Claudex plugin..."
  claude plugin update "$PLUGIN_ID"
else
  echo "Installing Claudex plugin..."
  claude plugin install "$PLUGIN_ID" --scope user
fi

# Locate the installed copy from Claude Code's own records, not by globbing
# the cache (which can hold several versions).
install_path="$(
  claude plugin list --json 2>/dev/null | awk -v id="$PLUGIN_ID" '
    /"id":/          { cur = $0; sub(/.*"id": *"/, "", cur); sub(/".*/, "", cur) }
    /"installPath":/ { if (cur == id) { p = $0; sub(/.*"installPath": *"/, "", p); sub(/".*/, "", p); print p; exit } }
  '
)"
SERVER_PY="${install_path:+$install_path/server/server.py}"

# Prepare dependencies with a bounded, checked step (uv resolves and installs,
# then exits; nothing is started or killed).
echo ""
if [ -n "$SERVER_PY" ] && [ -f "$SERVER_PY" ]; then
  echo "Preparing Python dependencies (first run may take a few seconds)..."
  if uv sync --script "$SERVER_PY"; then
    echo "Dependencies ready."
  else
    echo "Warning: dependency preparation failed. The plugin is installed; the"
    echo "first session will retry. To retry now: uv sync --script \"$SERVER_PY\""
    exit 1
  fi
else
  echo "Warning: could not locate the installed plugin to prepare dependencies."
  echo "The first session will prepare them on start (it can take a few seconds)."
fi

echo ""
echo "Done! Set the folders Codex may work in, then start a new Claude Code session:"
echo "  export CLAUDEX_ALLOWED_ROOTS=\"\$HOME/Projects\"   # add to your shell profile"
echo "  /mcp                            : verify codex tools are loaded"
echo "  use codex_ping to test codex    : verify Codex connectivity"
echo "  /codex:plan <your task>         : parallel planning with Codex"

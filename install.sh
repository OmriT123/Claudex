#!/usr/bin/env bash
# Claudex installer: adds the marketplace, installs the plugin, and prepares
# the server's Python dependencies so the first session starts instantly.
# Usage: curl -fsSL https://raw.githubusercontent.com/OmriT123/Claudex/main/install.sh | bash
#
# Uses Claude Code's own plugin commands (no hand-edited JSON) and prepares
# the dependencies of the version that was actually installed (v2.3.1),
# locked to server/server.py.lock (v2.4).
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

# uv >= 0.11.4: the server runs `uv run --locked --script`, and older uv
# ignores --locked when a script lockfile is missing (the server also
# declares this floor, so an older uv refuses to start it).
# version_ge A B: true when dotted version A >= B (portable: no sort -V on macOS)
version_ge() {
  awk -v a="$1" -v b="$2" 'BEGIN {
    na = split(a, x, "."); nb = split(b, y, ".")
    for (i = 1; i <= 3; i++) {
      if ((x[i] + 0) > (y[i] + 0)) exit 0
      if ((x[i] + 0) < (y[i] + 0)) exit 1
    }
    exit 0
  }'
}
uv_version="$(uv --version 2>/dev/null | awk '{print $2}')"
if [ -z "$uv_version" ] || ! version_ge "$uv_version" "0.11.4"; then
  echo "Error: uv ${uv_version:-unknown} is too old; Claudex needs uv >= 0.11.4."
  echo "  Update: uv self update   (or reinstall: curl -LsSf https://astral.sh/uv/install.sh | sh)"
  exit 1
fi

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
  if uv sync --locked --script "$SERVER_PY"; then
    echo "Dependencies ready."
  else
    echo "Warning: dependency preparation failed. The plugin is installed; the"
    echo "first session will retry. To retry now: uv sync --locked --script \"$SERVER_PY\""
    exit 1
  fi
else
  echo "Warning: could not locate the installed plugin to prepare dependencies."
  echo "The first session will prepare them on start (it can take a few seconds)."
fi

echo ""
echo "Done! Choose the folders Codex may work in (deny-by-default), then start"
echo "a new Claude Code session:"
if [ -n "$SERVER_PY" ]; then
  echo "  uv run --locked --script \"$SERVER_PY\" --configure-roots \"\$HOME/Projects\""
  echo "  (applies to every Claude app on this computer; no restart needed)"
fi
echo "  or: export CLAUDEX_ALLOWED_ROOTS=\"\$HOME/Projects\"  in your shell profile"
echo ""
echo "  /mcp                            : verify codex tools are loaded"
echo "  use codex_ping to test codex    : verify Codex connectivity"
echo "  /claudex:plan <your task>         : parallel planning with Codex"

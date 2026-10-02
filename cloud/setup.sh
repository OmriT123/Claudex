#!/usr/bin/env bash
# Claudex for Claude Code cloud sessions (claude.ai/code): environment setup script.
#
# In your cloud environment's settings, set the Setup script to:
#   curl -fsSL https://raw.githubusercontent.com/OmriT123/Claudex/main/cloud/setup.sh | bash
#
# Runs as root before Claude Code starts. Installs the Codex CLI and the Claudex
# plugin (user scope) and pre-warms the server's Python deps. The result is cached
# with the environment snapshot, which is rebuilt when the setup script or the
# allowed hosts change, or after about a week; edit the script to force a rebuild.
#
# Codex auth is per session and not handled here (see README -> "Claude Code cloud
# sessions"). Never blocks the session: always exits 0 and logs to
# $CLAUDEX_CLOUD_LOG (default /var/log/claudex-cloud-setup.log).

set -uo pipefail

LOG="${CLAUDEX_CLOUD_LOG:-/var/log/claudex-cloud-setup.log}"
MARKETPLACE_REPO="OmriT123/claude-plugins"
MARKETPLACE_NAME="omri-plugins"
PLUGIN="claudex@$MARKETPLACE_NAME"

mkdir -p "$(dirname "$LOG")" 2>/dev/null || LOG=/tmp/claudex-cloud-setup.log

log() { printf '[claudex-cloud %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

# retry <attempts> <cmd...>: a network blip during the one snapshot build would
# otherwise be cached until the next rebuild.
retry() {
  local n=$1 i
  shift
  for i in $(seq 1 "$n"); do
    "$@" && return 0
    [ "$i" -lt "$n" ] && { log "attempt $i failed: $*; retrying in $((i * 5))s"; sleep $((i * 5)); }
  done
  return 1
}

add_marketplace() {
  local known
  known=$(claude plugin marketplace list 2>/dev/null)
  if grep -q "$MARKETPLACE_NAME" <<<"$known"; then
    claude plugin marketplace update "$MARKETPLACE_NAME"
  else
    claude plugin marketplace add "$MARKETPLACE_REPO"
  fi
}

# Path of the installed server.py for $PLUGIN, from `claude plugin list --json`.
installed_server() {
  claude plugin list --json 2>/dev/null | awk -v id="$PLUGIN" '
    /"id":/          { cur = $0; sub(/.*"id": *"/, "", cur); sub(/".*/, "", cur) }
    /"installPath":/ { if (cur == id) { p = $0; sub(/.*"installPath": *"/, "", p); sub(/".*/, "", p); print p "/server/server.py"; exit } }
  '
}

main() {
  local failed=0 server
  log "setup starting"

  if retry 3 npm install -g @openai/codex@latest --no-audit --no-fund --loglevel=error; then
    log "codex: $(codex --version 2>&1)"
  else
    log "FAIL: npm install -g @openai/codex"; failed=1
  fi

  if retry 3 add_marketplace && retry 3 claude plugin install "$PLUGIN" --scope user; then
    log "installed $PLUGIN"
  else
    log "FAIL: plugin install $PLUGIN"; failed=1
  fi

  # Prepare the server's deps now, not on first use. Resolve the INSTALLED
  # copy from Claude Code's records (the cache can hold several versions).
  server=$(installed_server)
  if [ -z "$server" ]; then
    log "FAIL: could not locate the installed $PLUGIN (claude plugin list --json)"; failed=1
  elif (cd /tmp && timeout 300 uv sync --locked --script "$server" </dev/null >/dev/null 2>&1); then
    log "server dependencies prepared for $server"
  else
    log "FAIL: server dependency preparation (uv sync --locked --script $server)"; failed=1
  fi

  if [ "$failed" -eq 0 ]; then log "setup finished: OK"; else log "setup finished WITH FAILURES (session still starts; see $LOG)"; fi
}

main 2>&1 | tee -a "$LOG"
exit 0

---
name: setup
description: "Choose the folders Codex may work in (workspace roots) and check that Claudex is ready"
argument-hint: "[folder ...]"
allowed-tools: Bash(uv run --locked --script ${CLAUDE_PLUGIN_ROOT}/server/server.py --show-roots), Bash(uv --version), mcp__plugin_claudex_codex__codex_status, mcp__plugin_claudex_codex__codex_ping
---

# Claudex Setup

Claudex is deny-by-default: Codex works only inside folders the user chose.
Help the user choose them once. Folders: $ARGUMENTS

## Your workflow:

1. **Check uv.** Run `uv --version`. Claudex needs uv 0.11.4 or newer; if it is
   older, tell the user to run `uv self update` and stop until they have.

2. **See what is configured.** Run
   `uv run --locked --script ${CLAUDE_PLUGIN_ROOT}/server/server.py --show-roots`
   and call `codex_status` (free). The status `Roots:` line names the source in
   use (env var, config file, cloud default) or says
   DENY-ALL.

3. **If no folders are set, ask which folders** Codex may work in, unless the
   user already listed them above. Suggest their projects folder, never their
   whole home folder (it is refused). Then offer one of:
   - **Every Claude app on this computer (recommended):** run in the user's
     terminal, after they confirm the folder list:
     `uv run --locked --script ${CLAUDE_PLUGIN_ROOT}/server/server.py --configure-roots <folder> [<folder> ...]`
     Takes effect on the next call, no restart. This changes a per-user settings
     file, so show the exact command and get the user's OK before running it.
   - **Claude Code only:** `export CLAUDEX_ALLOWED_ROOTS="<a>:<b>"`
     in the shell profile, then restart Claude Code.

4. **Verify.** Call `codex_status` again and confirm the `Roots:` line lists the
   folders. Then `codex_ping` (free) for the Codex CLI and sign-in. If it says
   not logged in, run `/claudex:login`.

5. **Tell the user how to switch Claudex off** on this computer at any time:
   `uv run --locked --script ${CLAUDE_PLUGIN_ROOT}/server/server.py --revoke-roots`
   (beats every other setting until they run `--configure-roots` again).

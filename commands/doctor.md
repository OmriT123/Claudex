---
name: doctor
description: "Diagnose and fix Claudex issues — checks prerequisites, auth, connectivity, and common problems"
argument-hint: ""
allowed-tools: Read, Glob, Grep, Bash(which:*), Bash(codex --version), Bash(du:*), mcp__plugin_codex_codex__codex_ping, mcp__plugin_codex_codex__codex_login
---

# Claudex Doctor

Run through these diagnostic checks in order. Stop at the first failure and help the user fix it.

## Checks

1. **Codex CLI installed?**
   - Run: `which codex` or check common install paths
   - If missing: tell user to run `npm i -g @openai/codex`

2. **Codex CLI version?**
   - Run: `codex --version`
   - Outdated = below `MIN_CODEX_VERSION` (0.153.1). Below that floor the API rejects the
     default `gpt-6-astra` model on every call ("requires a newer version of Codex").
   - If outdated: suggest `npm i -g @openai/codex@latest`, then `/reload-plugins` (or a
     session restart) — the server caches the version check once per process lifetime,
     and a reload respawns it. The same applies after `/plugin update codex`.

3. **Codex authenticated + Claudex healthy?**
   - Run `codex_ping` (default = FREE health check: binary, version, auth
     status, quota state DB, confinement readiness — no model call)
   - If auth shows not logged in: run `/codex:login` (calls `codex_login`: a link and
     a one-time code the user approves on any device; works without a browser, e.g.
     in Claude Code cloud sessions). In a terminal, `codex login` also works. If the
     environment sets `CODEX_API_KEY`, ping shows "API key from CODEX_API_KEY" and no
     sign-in is needed. Never print an API key.
   - If roots show NOT CONFIGURED: point to README → "Workspace confinement
     (required)" — every call is denied until roots are set (v2.0). Cloud sessions
     default to the project directory ("cloud session default")
   - Only if the user wants a full round-trip: run `codex_ping` with
     `model_test=true` (spends one execution + OpenAI-side usage)
   - If it reports "A network policy blocked Codex's connection to <host>": the
     sandbox's egress allowlist lacks that host. In a cloud session the user adds
     it under environment settings → Network access (Custom), then starts a new
     session; don't try to route around the block

4. **MCP server running?**
   - Check if `codex` tools are available via `/mcp`
   - If not: suggest `/reload-plugins`, then a full Claude Code session restart if still missing

5. **uv available?**
   - Run: `which uv`
   - If missing: provide install command

6. **Plugin registered?**
   - Check if plugin appears in Claude Code's plugin list
   - If not: provide manual install steps

7. **.claudex kept out of git?**
   - Since v2.2 `.claudex/` holds its own `.gitignore` (`*`); nothing to add
   - Only if `.claudex/` files show as untracked in `git status`: check that
     `.claudex/.gitignore` exists and isn't a symlink

8. **Disk usage?**
   - Check `.claudex/` directory size
   - If large (>100MB): suggest running cleanup or warn about accumulated artifacts

## If all checks pass
Tell the user everything looks good and suggest trying `/codex:status` for detailed metrics.

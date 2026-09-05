---
name: doctor
description: "Diagnose and fix Claudex issues — checks prerequisites, auth, connectivity, and common problems"
argument-hint: ""
allowed-tools: Read, Glob, Grep, Bash(which:*), Bash(codex --version), Bash(du:*), mcp__plugin_codex_codex__codex_ping
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
   - If outdated: suggest `npm i -g @openai/codex@latest`, then restart the Claude Code
     session — the server caches the version check once per process lifetime

3. **Codex authenticated + Claudex healthy?**
   - Run `codex_ping` (default = FREE health check: binary, version, auth
     status, quota state DB, confinement readiness — no model call)
   - If auth shows not logged in: tell user to run `codex login`
   - If roots show NOT CONFIGURED: point to README → "Workspace confinement
     (required)" — every call is denied until roots are set (v2.0)
   - Only if the user wants a full round-trip: run `codex_ping` with
     `model_test=true` (spends one execution + OpenAI-side usage)

4. **MCP server running?**
   - Check if `codex` tools are available via `/mcp`
   - If not: suggest restarting Claude Code session

5. **uv available?**
   - Run: `which uv`
   - If missing: provide install command

6. **Plugin registered?**
   - Check if plugin appears in Claude Code's plugin list
   - If not: provide manual install steps

7. **.claudex in .gitignore?**
   - Check if `.claudex` is in the project's `.gitignore`
   - If not: suggest adding it

8. **Disk usage?**
   - Check `.claudex/` directory size
   - If large (>100MB): suggest running cleanup or warn about accumulated artifacts

## If all checks pass
Tell the user everything looks good and suggest trying `/codex:status` for detailed metrics.

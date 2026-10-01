---
name: login
description: "Sign Codex in to your ChatGPT account with a one-time code — no browser needed (Claude Code cloud sessions, SSH)"
argument-hint: "[restart]"
allowed-tools: mcp__plugin_claudex_codex__codex_login
---

# Codex Login

**Argument**: $ARGUMENTS

Call `codex_login` immediately, before anything else. Pass `restart: true` only if the
argument asks for a new code (e.g. "restart", "new code"); otherwise pass no arguments.

Reply with the tool's result as-is: keep the link and the code exactly as returned, on
their own lines, easy to spot on a phone. Add nothing else. If the result is an error,
also state the one fix it names.

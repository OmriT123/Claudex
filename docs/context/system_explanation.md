<!-- Last verified: 2026-08-10 -->
<!-- Update when: architecture, major flows, interfaces, or invariants change -->
<!-- v2.0.0 (M0-A): confinement is deny-by-default; quota is durable SQLite; streaming is incrementally capped; env sanitized on every spawn incl. git -->>

# Claudex — System Explanation

Plain-English boot map. Every section points at source; verify against code before acting on it.

## What this is

Claudex integrates OpenAI's Codex CLI into Claude Code / Claude Desktop as a read-only second-AI teammate, over MCP. Two shipping surfaces, one server:

- **Claude Code plugin** — `.mcp.json` (bare `{"codex": {...}}`) declares the stdio server, launching `server/server.py` via `uv run`. **Leave it exactly as it is.** This repo is a plugin *and* a workspace, so Claude Code reads that path a second time as a **project** config, and every "tidier" arrangement is worse. Options 1–2 were observed directly on 2.1.226 (2026-08-10); option 3's failure mode is the upstream report and was **not** reproduced here:
  - *Bare file (current)* — the project parse fails, printing a cosmetic red "Failed to parse" banner in `/mcp`. Dev-only: it needs the repo to be your cwd, so no installed user ever sees it. **This is the option we keep.**
  - *Wrapping it in `{"mcpServers": {...}}`* — kills the banner, but the project loader then successfully registers a *second* `codex` server that cannot work (`${CLAUDE_PLUGIN_ROOT}` is undefined outside plugin scope). It renders as `codex · ✘ failed` plus "Failed to reconnect to codex" — indistinguishable from Codex being down. Caused a false outage alarm during concurrent sessions. A repo `.claude/settings.json` with `disabledMcpjsonServers: ["codex"]` suppresses the entry but still leaves a `CLAUDE_PLUGIN_ROOT` warning.
  - *Moving it into `plugin.json`'s `mcpServers` field* — verified working on 2.1.226, but upstream [#16143](https://github.com/anthropics/claude-code/issues/16143) (OPEN, reported against 2.0.76, no fix comment) says that field is dropped during manifest parsing, so the plugin installs clean with **zero tools and no error**. We could not reproduce that locally and the fixed-in version is unknown, which is exactly the problem: the exposure is every client older than ours, and the symptom is silent. Trading a dev-facing banner for that is the worst of the three.

  Also measured: when a root `.mcp.json` exists it is authoritative — a manifest declaration of a *different* server name silently disappears, and same-name entries resolve to the manifest's value. `TestPluginManifest` in `tests/test_helpers.py` guards the two rules that matter (the file declares `codex`; the manifest declares nothing).
- **Claude Desktop extension** — `desktop-extension/` packages the same server as a `.mcpb` (manifest v0.3, darwin-only today, `/bin/sh` launcher; Windows track is active — see ROADMAP).

## The one big file

All server logic lives in `server/server.py` (~3,300 lines, FastMCP instance named `codex`). Shape, top to bottom:

- **Config + env** — defaults (model `gpt-5.6-sol`, effort, timeouts), env var names, `ALWAYS_DENIED_SUBPATHS`.
- **Workspace confinement (deny-by-default, v2.0)** — `_allowed_roots()` resolves `CLAUDEX_ALLOWED_ROOTS` (`os.pathsep`-split, Windows-safe) or a structured `--allowed-roots` argv transport; **empty/missing/unresolved = deny ALL** (no "unrestricted" mode). `_validate_project_dir()` enforces the home/sensitive-dir denylist + root containment; `_authorized_cwd()` wraps it as the one denial-contract home, called at the entry of every project-bound tool handler (runner + `codex_submit` admission kept as defense-in-depth).
- **Codex version check** — `_check_codex_version()`: offline `codex --version` vs a pinned `MIN_CODEX_VERSION` (the runtime `npm view` registry lookup was removed — zero undeclared egress); caches the installed version for diagnostics.
- **Durable quota** — `_reserve_daily_run()`: SQLite (`BEGIN IMMEDIATE`, WAL) in per-user app data (`_state_dir()` — `CLAUDEX_STATE_DIR` → `${CLAUDE_PLUGIN_DATA}` → OS-native, always outside the repo), reserved at the subprocess boundary in `_run_codex_once` after validation + binary pre-check; counts local executions (`CLAUDEX_MAX_RUNS_PER_DAY`, legacy `CLAUDEX_MAX_JOBS_PER_DAY` alias); fail-closed on any DB error.
- **The runner** — `_run_codex()` → `_run_codex_once()`: builds the persona system prompt, shells out to `codex exec --sandbox read-only` with an isolated ephemeral profile and sanitized env (`_sanitized_codex_env()` / `_CODEX_ENV_KEEP`, applied to EVERY spawn incl. git), reads stdout/stderr through incremental caps (`_pump_capped`/`_read_stream_capped` — combined-byte + per-line, stderr rolling-tail, breach discards rather than returns partial), parses `---FINAL-ANSWER---` / `<claudex-artifact>` blocks, extracts artifacts into `.claudex/run-<uuid>/`.
- **Git context/diff** — `_git_cmd` / `_get_git_diff`: all git spawns run with the sanitized env + `-c core.fsmonitor=false -c core.hooksPath=/dev/null`, diff-producing ones add `--no-ext-diff --no-textconv`. Reduces (does not sandbox) hostile-repo config execution — attribute-driven clean/smudge filters remain inherent git behavior (see ROADMAP backlog / README "Not a sandbox against a repository you open").
- **Sessions** — `.claudex/sessions/*.md` shared-memory documents for iterative `codex_collab`; auto-rollover after 4 rounds (recap + chained session).
- **Structured reviews** — `REVIEW_FILES_SCHEMA`/`REVIEW_DIFF_SCHEMA` + `_run_structured_review()`; JSON findings rendered as markdown; auto-fallback to text mode. Diff reviews bind to repo state (HEAD SHA + truncated diff hash) and fail closed on oversized diffs.
- **12 MCP tools** — `codex_plan/critique/brainstorm/collab/review/review_diff/evaluate/recap/status/ping/submit/result`. `submit`/`result` are the async job layer: background task in the persistent server process, results persisted to `.claudex/jobs/`. `codex_ping` is split — default = free health check (version/auth/quota/confinement, no model call), `model_test=true` = a real round-trip through the full controls.

## Execution flow (typical tool call)

Tool call → Pydantic input model validates → persona prompt assembled (+ git context from `_get_git_context`) → `codex exec` subprocess (read-only sandbox, user's own ChatGPT-plan login — Claudex never touches `auth.json`) → stdout parsed → artifacts/structured JSON returned to the MCP client.

## Operational state

`.claudex/` in the project directory: `sessions/`, `recaps/`, `jobs/`, `run-*/`. Gitignored here; the server warns when a host repo lacks the ignore rule. Operational counters now live OUTSIDE the repo in OS app-data (`_state_dir()`); full migration of sessions/recaps/jobs out of customer repos is still a v2.0 product fix (see ADR-017).

## Tests

`tests/test_helpers.py` (PEP-723, `uv run --script tests/test_helpers.py`) — 160+ tests over confinement helpers, session management, input models, job layer, structured output, formatters, error paths.

## Where decisions and plans live

`docs/ROADMAP.md` (entry point) · `docs/adr/` (architecture decisions — local-only until a publish decision) · `docs/v2-plan/` (business planning — local-only, gitignored) · `docs/archive/` (consumed historical docs).

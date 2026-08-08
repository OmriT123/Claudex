<!-- Last verified: 2026-08-08 -->
<!-- Update when: architecture, major flows, interfaces, or invariants change -->

# Claudex — System Explanation

Plain-English boot map. Every section points at source; verify against code before acting on it.

## What this is

Claudex integrates OpenAI's Codex CLI into Claude Code / Claude Desktop as a read-only second-AI teammate, over MCP. Two shipping surfaces, one server:

- **Claude Code plugin** — `.claude-plugin/plugin.json` + `.mcp.json` launch `server/server.py` via `uv run` (stdio MCP).
- **Claude Desktop extension** — `desktop-extension/` packages the same server as a `.mcpb` (manifest v0.3, darwin-only today, `/bin/sh` launcher; Windows track is active — see ROADMAP).

## The one big file

All server logic lives in `server/server.py` (~3,300 lines, FastMCP instance named `codex`). Shape, top to bottom:

- **Config + env** — defaults (model `gpt-5.6-sol`, effort, timeouts), env var names, `ALWAYS_DENIED_SUBPATHS`.
- **Workspace confinement** — `_allowed_roots()` parses `CLAUDEX_ALLOWED_ROOTS` (colon-split; empty = unrestricted today — deny-by-default inversion is planned, see ROADMAP); `_validate_project_dir()` enforces the home/sensitive-dir denylist + root containment. Called from the runner and `codex_submit` admission; synchronous handlers currently act on `cwd` before validation (known gap, tracked).
- **Codex version check** — `_check_codex_version()` (`codex --version` + an `npm view @openai/codex` registry lookup, cached once per process; removal planned).
- **The runner** — `_run_codex()` → `_run_codex_once()`: builds the persona system prompt (codebase-first preamble + per-tool persona + artifact or structured-output instructions), shells out to `codex exec --sandbox read-only` with an isolated ephemeral profile and sanitized env, buffers output via `communicate()` (4MB post-buffer cap), parses `---FINAL-ANSWER---` / `<claudex-artifact>` blocks, extracts artifacts with path-traversal/symlink/size guards into `.claudex/run-<uuid>/`.
- **Sessions** — `.claudex/sessions/*.md` shared-memory documents for iterative `codex_collab`; auto-rollover after 4 rounds (recap + chained session).
- **Structured reviews** — `REVIEW_FILES_SCHEMA`/`REVIEW_DIFF_SCHEMA` + `_run_structured_review()`; JSON findings rendered as markdown; auto-fallback to text mode. Diff reviews bind to repo state (HEAD SHA + truncated diff hash) and fail closed on oversized diffs.
- **12 MCP tools** — `codex_plan/critique/brainstorm/collab/review/review_diff/evaluate/recap/status/ping/submit/result`. `submit`/`result` are the async job layer: background task in the persistent server process, results persisted to `.claudex/jobs/`, in-memory daily job budget (`_daily_job_count` — process-local today; durable store planned).

## Execution flow (typical tool call)

Tool call → Pydantic input model validates → persona prompt assembled (+ git context from `_get_git_context`) → `codex exec` subprocess (read-only sandbox, user's own ChatGPT-plan login — Claudex never touches `auth.json`) → stdout parsed → artifacts/structured JSON returned to the MCP client.

## Operational state

`.claudex/` in the project directory: `sessions/`, `recaps/`, `jobs/`, `run-*/`. Gitignored here; the server warns when a host repo lacks the ignore rule. Migration of state out of customer repos into OS app-data is a planned product fix (v2 track).

## Tests

`tests/test_helpers.py` (PEP-723, `uv run --script tests/test_helpers.py`) — 160+ tests over confinement helpers, session management, input models, job layer, structured output, formatters, error paths.

## Where decisions and plans live

`docs/ROADMAP.md` (entry point) · `docs/adr/` (architecture decisions — local-only until a publish decision) · `docs/v2-plan/` (business planning — local-only, gitignored) · `docs/archive/` (consumed historical docs).

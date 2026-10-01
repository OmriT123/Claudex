# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Claudex is a Claude Code plugin that integrates OpenAI's Codex CLI as a read-only teammate via MCP. Two different AI architectures (Claude + Codex) collaborate on planning, security-testing, debugging, verification, and decision support against the same codebase. Codex always runs in `--sandbox read-only` — it can read the repo but never modify it. Codex explores the codebase directly to form its own understanding before addressing any task.

## Development

**Run the plugin locally:**
```bash
claude --plugin-dir /path/to/Claudex
```

**Verify syntax and config:**
```bash
python3 -c "import ast; ast.parse(open('server/server.py').read())"
python3 -c "import json; json.load(open('.claude-plugin/plugin.json')); json.load(open('.mcp.json'))"
```

**Start the MCP server manually (for debugging):**
```bash
uv run server/server.py
```

There is no build system, no linter configured. Dependencies are declared inline in `server/server.py` via PEP 723 script metadata and resolved automatically by `uv`.

**Run tests:**
```bash
uv run --script tests/test_helpers.py
```

Tests (392 total) cover GPT-6 Astra alignment (defaults, effort ladder, operating contract, CLI-floor error mapping), security-critical helpers (`_safe_claudex_path`, `_normalize_file_list`), session management, Pydantic model validation, auto-session-ID generation, timeout constants, model/reasoning_summary validation, metrics, session chaining, `ReviewDiffInput`, backward compatibility, structured output schemas, review formatters, `_build_review_system` toggle, `structured_output` field validation, structured output integration (mock-based), temp file lifecycle, formatter edge cases, and error handling fixes (stderr fallback, timeout cleanup, OSError catch, version warning masking, schema write errors), and Claude Code cloud sessions (cloud-default roots, codex-auth env passthrough, network-hygiene flags, proxy-block error mapping, self-ignoring `.claudex/`, `.mcp.json` timeout), no-symlink-following inside `.claudex/` (cleanup, session/recap/job/artifact I/O, run dirs, `.gitignore`, swaps after validation and during the model call, FIFOs, both job-writer modes, real filesystem; v2.3.1), collab continuity across rollovers (forged markers, lock hand-off, late results, chain loops and length, successor provenance, byte budgets), git-failure reporting in `codex_review_diff`, roots sources/precedence/kill switch/config CLI, config identity (account home, no-follow private file, strict schema), results withheld after revocation and lockfile freshness + `.mcpb` packaging (v2.4), and `codex_login` (device-prompt parsing + URL allowlist, pending/restart/approved flow against a fake CLI, network-failure mapping). Test file uses PEP 723 inline metadata (same pattern as `server.py`).

## Architecture

**Single-file server** — all logic lives in `server/server.py` (~4000 lines). It's a FastMCP server (`FastMCP("codex")`) that exposes 13 tools:

| Tool | Purpose | Codex Persona |
|------|---------|---------------|
| `codex_plan` | Codex generates its own independent plan (parallel planning) | Creative Architect |
| `codex_critique` | Codex critiques a provided plan (second opinion) | Critical QA Engineer |
| `codex_brainstorm` | Open-ended exploration of a problem | Innovation Consultant |
| `codex_collab` | Claude Code sends its analysis + request type, gets targeted suggestions | Varies by request_type |
| `codex_review` | Targeted code review of specific files (structured JSON by default) | Senior Code Reviewer |
| `codex_review_diff` | Review git diff (staged or unstaged) with structured findings | Diff Reviewer |
| `codex_evaluate` | Tradeoff analysis between options (user decides) | Technical Advisor |
| `codex_recap` | Decision record generation from a session | Technical Writer |
| `codex_status` | Diagnostics dashboard (no Codex call, zero cost) | N/A |
| `codex_ping` | Connectivity test | N/A |
| `codex_login` | Device-code sign-in (link + one-time code; background process in the server, v2.3) | N/A |
| `codex_submit` | Run any Codex tool as a background job (async layer, v1.7) | (delegates) |
| `codex_result` | Poll/collect a background job; disk fallback after restart | N/A |

**Execution flow:** Tool call → construct persona-specific system prompt (with codebase-first preamble) → append artifact or structured-output instructions → shell out to `codex exec --sandbox read-only` (with optional `--output-schema`) → capture stdout → for text mode: parse `<claudex-artifact>` blocks from after `---FINAL-ANSWER---` delimiter → write artifacts to `.claudex/run-<uuid>/` with security validation → return cleaned text + artifact listing. For structured mode (review tools): return raw JSON → parse and format as rich markdown with collapsed raw JSON details block.

**Key components in server.py:**
- **Codebase-first preamble** — prepended to ALL system prompts, instructs Codex to read project files before addressing the task
- **Persona system prompts** — each tool has a distinct persona (architect, QA engineer, adversarial researcher, etc.)
- **Dynamic collab personas** — `COLLAB_PERSONAS` dict maps request_type → persona instructions
- **Pydantic input models** — typed inputs for each tool with validation (regex patterns on `model` and `reasoning_summary` to prevent injection)
- **Enums** — `ReasoningEffort` (4 levels), `RequestType` (7 collab modes)
- **`_run_codex()` / `_run_codex_once()`** — core async subprocess runner (hardened v1.8: isolated Codex profile, sanitized env, process-group kill) with metrics, artifact extraction, and optional `--output-schema` for structured JSON output
- **`_run_structured_review()`** — shared helper for `codex_review` and `codex_review_diff` handling structured-output fallback, JSON parsing, and markdown formatting
- **Structured output** — `REVIEW_FILES_SCHEMA` / `REVIEW_DIFF_SCHEMA` define JSON schemas for review tools. `_build_review_system()` toggles between artifact and structured-output instructions. Formatters (`_format_finding`, `_format_review_files_json`, `_format_review_diff_json`) render JSON as rich markdown. Auto-fallback to text mode on JSON parse failure.
- **Session management** — `_safe_claudex_path()`, `_init_session()`, `_append_to_session()` for iterative debugging
- **Artifact security** — `_extract_and_save_artifacts()` — path traversal prevention, symlink rejection, size limits

**Session documents** (`.claudex/sessions/`): Managed by the server for iterative `codex_collab` workflows. Claude Code provides analysis, server writes both Claude Code's analysis and Codex's response to a persistent markdown file. Each round appends to the same document, creating shared memory across rounds.

**Decision records** (`.claudex/recaps/`): Generated by `codex_recap`, these are concise summaries of multi-round sessions with attribution and reasoning.

**Slash commands** (`commands/*.md`): Define multi-step workflows for `/codex:plan`, `/codex:brainstorm`, `/codex:collab`, `/codex:status`, `/codex:evaluate`, `/codex:recap`, `/codex:review`, `/codex:review-diff`, `/codex:help`, `/codex:doctor`. Each has YAML frontmatter with `allowed-tools`.

**Skill** (`skills/claudex/SKILL.md`): Auto-triggers during plan mode for non-trivial tasks. Contains the tool router decision tree, workflow patterns (Divergent, Convergent, Iterative, Evaluate, Pre-Commit Review), workflow chains (Plan→Stress-Test→Debug, Review→Fix→Verify, Explore→Decide), and the Claim Ledger format for cross-tool context carrying.

## Naming Convention

**Brand:** Claudex (repo name, README, external references)
**Operational:** `codex` everywhere inside Claude Code — plugin manifest, MCP server, FastMCP instance, tool prefixes (`codex_*`), commands (`/codex:*`).

## Defaults

- Model: `gpt-6-astra` (overridable per-call via `model` param; requires Codex CLI ≥ `MIN_CODEX_VERSION` 0.153.1)
- Reasoning effort: `high` on every tool (`low`…`max`; `ultra` not exposed)
- Reasoning summary: `detailed` (overridable per-call)
- Timeout: 1200s (20 min) for all tools; `.mcp.json` declares a 30-min per-server MCP `timeout` (Claude Code cloud sessions cap MCP calls at 60s otherwise)
- No timeout auto-retry (removed v1.8.0): timeouts return honest errors; callers retry deliberately
- Artifact max size: 100KB
- Run directory cleanup: 1 hour
- Session termination: 4 rounds max, then auto-rollover (recap + chained session)
- Diff review max: 50KB diff size, 50 files
- Structured output: enabled by default for `codex_review` and `codex_review_diff` (set `structured_output=False` for legacy text mode)

## Key Constraints

- Claude Code must verify Codex's claims before presenting to the user — never relay without first-hand investigation
- Codex subprocess must always use `--sandbox read-only` — security invariant
- Codex env is an allowlist (`_CODEX_ENV_KEEP`); only codex spawns also get Codex's own credentials (`_CODEX_AUTH_ENV`: `CODEX_API_KEY`, `CODEX_ACCESS_TOKEN`), and `shell_environment_policy.ignore_default_excludes=false` hides them from the commands Codex's model runs. Git spawns never get them
- Workspace roots are deny-by-default; the only implicit root is `CLAUDE_PROJECT_DIR` in a Claude Code cloud session (`CLAUDE_CODE_REMOTE=true`, never the home dir or `/`), and any explicit roots win. Sources and precedence live in `_roots_resolution()` (v2.4); the per-user config file is written only by the terminal `--configure-roots`/`--revoke-roots` CLI, never by an MCP tool; its location comes from the OS account (`_account_home()`), never from `HOME`/`APPDATA`, and it must be a private, non-symlinked regular file
- No plugin `userConfig` and no `${user_config.*}` in `.mcp.json`: the desktop app can leave a plugin server that needs plugin settings unstarted (`user_config_unsupported`), and one server config must start on every surface; folders come from `--configure-roots`, the env var or the cloud default. Guarded by `TestPluginManifest`
- Dependencies are locked: `server/server.py.lock` ships with the server and every launcher uses `uv run --locked --script` / `uv sync --locked --script`; after changing the PEP 723 deps run `uv lock --script server/server.py` (uv >= 0.11.4) or `TestV24Lockfile` fails
- Every read/write/delete under `.claudex/` goes through the fd-anchored helpers (`_open_nofollow`, `_claudex_fd`, `_rmtree_fd`); never `resolve()` a `.claudex` path or use path-based I/O there (v2.3.1)
- Codex explores the codebase directly — don't pre-summarize context in prompts
- Every system prompt inherits `_OPERATING_CONTRACT` (GPT-6 Astra tuning; the clauses live in `server/server.py` — docs point there rather than restating them)
- Artifact parsing only after the `---FINAL-ANSWER---` delimiter (prevents reasoning trace leakage)
- `.claudex/` ignores itself: the server writes `.claudex/.gitignore` (`*`) when it creates the dir (never overwrites, never follows symlinks)
- Leave `.mcp.json` in its bare `{"codex": {...}}` shape (keys inside the `codex` entry, like `timeout`, are fine) — the `/mcp` "Failed to parse" banner it causes in this repo is cosmetic and dev-only. Wrapping it registers a broken duplicate server, and moving it into `plugin.json` risks upstream #16143 on older clients (silent zero tools). See `docs/context/system_explanation.md`; guarded by `TestPluginManifest`
- Each Codex tool call costs 1 message from user's ChatGPT subscription quota
- Iterative sessions auto-rollover after 4 rounds (recap generated, new chained session created)
- `codex_evaluate` does NOT arbitrate — Claude Code presents both analyses, user decides
- Git diff reviews capped at 50KB / 50 files to stay within Codex context limits
- Metrics are in-memory only — reset on server restart, surfaced via `codex_status`

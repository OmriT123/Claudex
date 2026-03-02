# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Claudex is a Claude Code plugin that integrates OpenAI's Codex CLI as a read-only teammate via MCP. Two different AI architectures (Claude + Codex) collaborate on planning, red-teaming, debugging, verification, and decision support against the same codebase. Codex always runs in `--sandbox read-only` — it can read the repo but never modify it. Codex explores the codebase directly to form its own understanding before addressing any task.

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

Tests (122 total) cover security-critical helpers (`_safe_claudex_path`, `_normalize_file_list`), session management, Pydantic model validation, auto-session-ID generation, per-tool timeouts, model/reasoning_summary validation, effort downgrade, metrics, session chaining, `ReviewDiffInput`, backward compatibility, structured output schemas, review formatters, `_build_review_system` toggle, `structured_output` field validation, structured output integration (mock-based), temp file lifecycle, formatter edge cases, and error handling fixes (stderr fallback, timeout cleanup, OSError catch, version warning masking, schema write errors). Test file uses PEP 723 inline metadata (same pattern as `server.py`).

## Architecture

**Single-file server** — all logic lives in `server/server.py` (~3000 lines). It's a FastMCP server (`FastMCP("codex")`) that exposes 10 tools:

| Tool | Purpose | Codex Persona |
|------|---------|---------------|
| `codex_plan` | Codex generates its own independent plan (parallel planning) | Creative Architect |
| `codex_review` | Codex critiques a provided plan (second opinion) | Critical QA Engineer |
| `codex_brainstorm` | Open-ended exploration of a problem | Innovation Consultant |
| `codex_collab` | CC sends its analysis + request type, gets targeted suggestions | Varies by request_type |
| `codex_review_files` | Targeted code review of specific files (structured JSON by default) | Senior Code Reviewer |
| `codex_review_diff` | Review git diff (staged or unstaged) with structured findings | Diff Reviewer |
| `codex_evaluate` | Tradeoff analysis between options (user decides) | Technical Advisor |
| `codex_recap` | Decision record generation from a session | Technical Writer |
| `codex_status` | Diagnostics dashboard (no Codex call, zero cost) | N/A |
| `codex_ping` | Connectivity test | N/A |

**Execution flow:** Tool call → construct persona-specific system prompt (with codebase-first preamble) → append artifact or structured-output instructions → shell out to `codex exec --sandbox read-only` (with optional `--output-schema`) → capture stdout → for text mode: parse `<claudex-artifact>` blocks from after `---FINAL-ANSWER---` delimiter → write artifacts to `.claudex/run-<uuid>/` with security validation → return cleaned text + artifact listing. For structured mode (review tools): return raw JSON → parse and format as rich markdown with collapsed raw JSON details block.

**Key components in server.py:**
- **Codebase-first preamble** — prepended to ALL system prompts, instructs Codex to read project files before addressing the task
- **Persona system prompts** — each tool has a distinct persona (architect, QA engineer, adversarial researcher, etc.)
- **Dynamic collab personas** — `COLLAB_PERSONAS` dict maps request_type → persona instructions
- **Pydantic input models** — typed inputs for each tool with validation (regex patterns on `model` and `reasoning_summary` to prevent injection)
- **Enums** — `ReasoningEffort` (4 levels), `RequestType` (7 collab modes)
- **`_run_codex()` / `_run_codex_once()`** — core async subprocess runner with auto-retry on timeout (effort downgrade), metrics, artifact extraction, and optional `--output-schema` for structured JSON output
- **Structured output** — `REVIEW_FILES_SCHEMA` / `REVIEW_DIFF_SCHEMA` define JSON schemas for review tools. `_build_review_system()` toggles between artifact and structured-output instructions. Formatters (`_format_finding`, `_format_review_files_json`, `_format_review_diff_json`) render JSON as rich markdown. Auto-fallback to text mode on JSON parse failure.
- **Session management** — `_safe_claudex_path()`, `_init_session()`, `_append_to_session()` for iterative debugging
- **Artifact security** — `_extract_and_save_artifacts()` — path traversal prevention, symlink rejection, size limits

**Session documents** (`.claudex/sessions/`): Managed by the server for iterative `codex_collab` workflows. CC provides analysis, server writes both CC's analysis and Codex's response to a persistent markdown file. Each round appends to the same document, creating shared memory across rounds.

**Decision records** (`.claudex/recaps/`): Generated by `codex_recap`, these are concise summaries of multi-round sessions with attribution and reasoning.

**Slash commands** (`commands/*.md`): Define multi-step workflows for `/codex:plan`, `/codex:brainstorm`, `/codex:collab`, `/codex:status`, `/codex:evaluate`, `/codex:recap`, `/codex:review-files`, `/codex:review-diff`. Each has YAML frontmatter with `allowed-tools`.

**Skill** (`skills/claudex/SKILL.md`): Auto-triggers during plan mode for non-trivial tasks. Contains the tool router decision tree, workflow patterns (Divergent, Convergent, Iterative, Evaluate, Pre-Commit Review), workflow chains (Plan→Stress-Test→Debug, Review→Fix→Verify, Explore→Decide), and the Claim Ledger format for cross-tool context carrying.

## Naming Convention

**Brand:** Claudex (repo name, README, external references)
**Operational:** `codex` everywhere inside CC — plugin manifest, MCP server, FastMCP instance, tool prefixes (`codex_*`), commands (`/codex:*`).

## Defaults

- Model: `gpt-5.3-codex` (overridable per-call via `model` param)
- Reasoning effort: `high` (overridable per-call)
- Reasoning summary: `detailed` (overridable per-call)
- Timeout: 1200s (20 min) for all tools
- Timeout auto-retry: on timeout, `xhigh` → `high` and `high` → `medium` are retried once automatically
- Artifact max size: 100KB
- Run directory cleanup: 1 hour
- Session termination: 4 rounds max, then auto-rollover (recap + chained session)
- Diff review max: 50KB diff size, 50 files
- Structured output: enabled by default for `codex_review_files` and `codex_review_diff` (set `structured_output=False` for legacy text mode)

## Key Constraints

- CC must verify Codex's claims before presenting to the user — never relay without first-hand investigation
- Codex subprocess must always use `--sandbox read-only` — security invariant
- Codex explores the codebase directly — don't pre-summarize context in prompts
- Artifact parsing only after the `---FINAL-ANSWER---` delimiter (prevents reasoning trace leakage)
- `.claudex/` should be in `.gitignore` (server warns if missing)
- Each Codex tool call costs 1 message from user's ChatGPT subscription quota
- Iterative sessions auto-rollover after 4 rounds (recap generated, new chained session created)
- `codex_evaluate` does NOT arbitrate — CC presents both analyses, user decides
- Git diff reviews capped at 50KB / 50 files to stay within Codex context limits
- Metrics are in-memory only — reset on server restart, surfaced via `codex_status`

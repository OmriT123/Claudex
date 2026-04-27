# Claudex — Claude Code Plugin <sup>v1.6.0</sup>

Give Claude Code a Codex-powered teammate. Two different AI architectures collaborate on the same codebase — planning, security-testing, debugging, verification, and decision support.

## How It Works

```
You ask Claude Code to implement something
        │
        ▼
Claude Code formulates its own plan
        │
        ▼
Claude Code calls codex_plan via MCP ────────┐
        │                                      │
        ▼                                      ▼
Claude Code has Plan A                 Codex reads your repo
                                       (read-only sandbox)
                                       Forms its OWN understanding
                                               │
                                               ▼
                                       Codex produces Plan B
                                               │
        ◄──────────────────────────────────────┘
        │
        ▼
Claude Code compares Plan A vs Plan B
Adopts best ideas from each
        │
        ▼
Presents unified plan to you
with clear CC/Codex attribution
```

**Key design:** Codex explores the codebase directly — it reads files, understands patterns, and forms its own mental model. Claude Code points it in the right direction with `focus_files` — no need to pre-summarize context.

## Prerequisites

1. **Codex CLI** — the bridge to OpenAI:
   ```bash
   npm i -g @openai/codex
   codex login
   ```

2. **uv** — Python package runner (handles dependencies automatically):
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

3. **Claude Code** — recent version with plugin support.

## Installation

In any Claude Code session, run:

```
/plugin marketplace add OmriT123/claude-plugins
/plugin install claudex@omri-plugins
```

Start a new session and you're ready to go.

### Verify

- `/mcp` — should show `codex` with its tools
- Type: `use codex_ping to check if Codex is working`

<details>
<summary>Alternative install / local development</summary>

**One-liner install (via shell):**

```bash
curl -fsSL https://raw.githubusercontent.com/OmriT123/Claudex/main/install.sh | bash
```

**Local development:**

```bash
claude --plugin-dir /path/to/Claudex
```

</details>

## Commands

| Command | What It Does |
|---------|-------------|
| `/codex:plan [task]` | Claude Code and Codex independently plan the same task, then synthesize |
| `/codex:brainstorm [topic]` | Explore approaches from two AI perspectives |
| `/codex:collab [problem]` | Claude Code shares its analysis, Codex provides targeted suggestions |
| `/codex:evaluate [A vs B]` | Codex analyzes tradeoffs between approaches — user decides |
| `/codex:recap [session_id]` | Generate a decision record from a collaboration session |
| `/codex:review [files]` | Get a focused code review from Codex on specific files |
| `/codex:review-diff [focus]` | Get Codex to review your git diff before committing |
| `/codex:status` | Show Claudex diagnostics (zero Codex cost) |
| `/codex:help` | Quick start guide |
| `/codex:doctor` | Diagnose and fix Claudex issues |

### Examples

```
/codex:plan Add rate limiting to all API endpoints

/codex:brainstorm How should we handle caching for the dashboard?

/codex:collab I'm getting a race condition in the worker queue

/codex:evaluate Redis vs PostgreSQL pub/sub for real-time events

/codex:review-diff security

/codex:review src/auth.py, src/middleware.py
```

## MCP Tools

| Tool | Purpose | Codex Persona |
|------|---------|---------------|
| `codex_plan` | Codex makes its OWN plan, Claude Code compares with its plan | Creative Architect |
| `codex_critique` | Codex critiques a specific plan you provide | Critical QA Engineer |
| `codex_brainstorm` | Open-ended exploration of a problem | Innovation Consultant |
| `codex_collab` | Targeted collaboration — Claude Code sends analysis, gets suggestions | Varies by request type |
| `codex_review` | Targeted code review of specific files (structured JSON by default) | Senior Code Reviewer |
| `codex_review_diff` | Review git diff with structured findings (severity, confidence, line refs) | Diff Reviewer |
| `codex_evaluate` | Analyze tradeoffs between options — user decides | Technical Advisor |
| `codex_recap` | Generate decision record from a session | Technical Writer |
| `codex_status` | Show Claudex diagnostics (no Codex call, zero cost) | — |
| `codex_ping` | Test that Codex is installed and working | — |

## Collaboration Modes

The `codex_collab` tool supports these request types, each activating a distinct Codex persona:

| Type | Codex Persona | Use When |
|------|---------------|----------|
| `bug_approach` | Diagnostic Specialist | Need help debugging or identifying root causes |
| `red_team` | Adversarial Researcher | Want assumptions challenged and weaknesses found |
| `verification` | Formal Methods Engineer | Want independent correctness verification |
| `testing_strategy` | Test Architect | Need a comprehensive testing approach |
| `code_critique` | Senior Developer | Want implementation quality reviewed |
| `feature_suggestion` | Product Engineer | Need feature ideas or implementation approaches |
| `general` | Collaborative Engineer | Open-ended analysis and suggestions |

## Session Documents

For iterative debugging and multi-round collaboration, Claudex maintains session documents in `.claudex/sessions/`. These serve as shared memory between CC and Codex across rounds.

```markdown
# Debug Session: fix-race-condition
Started: 2026-02-18T10:30:00Z

## Round 1
### CC Analysis
Found intermittent test failures in worker_queue.py...

### Codex Response
Hypothesis: The issue is in the task acknowledgment timing...

### Test Results (added by CC)
Tested Codex's hypothesis — confirmed partial match...

## Round 2
### CC Analysis (updated with Round 1 findings)
...
```

Claude Code manages the document. The server writes Codex's responses. Each `codex_collab` call with the same `session_id` appends to the existing session. Pass `session_id="auto"` to auto-generate a descriptive ID from the problem statement. After 4 rounds, the session auto-rolls over — a recap is generated and a new chained session is created (e.g., `fix-race-condition` → `fix-race-condition-p2` → `fix-race-condition-p3`). Use `codex_recap` at any point to generate a formal decision record.

When a round produces file artifacts, the artifact listing is automatically appended to the session document so subsequent rounds have visibility into what was generated.

## Decision Support with `codex_evaluate`

Unlike other tools where Claude Code arbitrates, `codex_evaluate` presents analysis for the **user** to decide:

```
You: "Should we use Redis or PostgreSQL pub/sub for real-time events?"

Claude Code calls codex_evaluate with both options + constraints + priorities

Codex analyzes tradeoffs:
  Redis: Lower latency, but adds infra dependency
  PG pub/sub: No new infra, but higher latency at scale

Claude Code presents BOTH analyses → You decide
```

## Artifact Scratchpad

Codex can produce file artifacts — code snippets, test drafts, analysis docs — without ever having write access to your codebase.

**How it works:**
1. Codex runs in `--sandbox read-only` (enforced by Codex CLI — physically cannot write)
2. Codex embeds `<claudex-artifact>` blocks in its text output
3. The **server** parses these from the final-answer section only
4. The server writes them to `.claudex/run-<uuid>/` (isolated per invocation)
5. Claude Code receives clean text + artifact listing and can read the files

**Security model:**
- Codex never has write access — the sandbox enforces it
- Filenames are validated against path traversal (`Path.resolve()` + `is_relative_to()`)
- Symlink writes are rejected at both the target file and `.claudex` directory levels; exclusive-create prevents overwrite races
- Only the final-answer section is parsed (reasoning traces are ignored)
- Artifacts > 100KB are skipped
- Run directories are cleaned up after 1 hour

**Setup:** Add `.claudex` to your `.gitignore`:
```bash
echo '.claudex' >> .gitignore
```

## Defaults

- **Model**: `gpt-5.5` (overridable per-call via `model` parameter)
- **Reasoning effort**: `high` (override per-call: `low`, `medium`, `high`, `xhigh`)
- **Reasoning summary**: `detailed` (overridable: `detailed`, `concise`, `none`)
- **Sandbox**: `read-only` — Codex reads your repo but never modifies it
- **Timeout**: 1200s (20 min) for all tools
- **Auto-retry**: On timeout, effort is downgraded and retried once (`xhigh` → `high`, `high` → `medium`). No retry for `medium` or `low`
- **Git context**: All tools (except `codex_review_diff`) automatically inject current branch, diff stat, recent commits, and staged changes into the Codex prompt — no manual context needed
- **Session rollover**: After 4 rounds, sessions auto-rollover (recap generated, new chained session with `-p2`/`-p3` suffix)
- **Structured output**: `codex_review` and `codex_review_diff` return structured JSON findings (severity, confidence, file:line) by default. Set `structured_output=False` for legacy text mode. If structured mode fails (CLI incompatibility or malformed JSON), the server auto-retries in text mode — this costs 1 extra message from your quota
- **Version check**: Auto-checks for Codex CLI updates on first tool invocation (warning shown once per session)
- **Metrics**: In-memory per-tool stats (calls, successes, timeouts, errors, avg latency) — visible in `codex_status`, reset on server restart

## Structured Review Output

`codex_review` and `codex_review_diff` return structured JSON by default with typed findings:

| Field | Description |
|-------|-------------|
| `severity` | `critical`, `warning`, `suggestion`, or `positive` |
| `priority` | Integer ranking within severity level |
| `confidence_score` | 0.0–1.0 confidence in the finding |
| `category` | `bug`, `security`, `performance`, `error_handling`, `maintainability`, `convention`, `logic`, `other` |
| `code_location` | `file_path` + `line_range` |
| `suggestion` | Recommended fix (nullable) |

The server formats these as rich markdown with severity badges and collapsible raw JSON. `codex_review_diff` also includes an overall `verdict` (`ship` / `fix_first` / `needs_discussion`).

Set `structured_output=False` to get free-form text analysis instead.

## Rate Limits

Codex uses your ChatGPT subscription quota:
- **Plus ($20/mo)**: ~30–150 messages per 5-hour window
- **Pro ($200/mo)**: ~300–1,500 messages per 5-hour window
- Each tool call = 1 message from quota
- Structured output auto-fallback costs 1 extra message if it triggers
- If rate-limited: wait for window reset or lower `reasoning_effort`

## Under the Hood

**Git context injection** — Every Codex call (except `codex_review_diff`, which handles its own diff) automatically collects and injects the current git branch, unstaged diff stat (capped at 20 lines), last 5 commit messages, and staged changes stat (capped at 5KB) into the system prompt. This gives Codex awareness of your working state without you needing to specify it.

**Session context management** — Session documents are capped at 32KB. When a session exceeds this, the oldest rounds are dropped first to stay within the limit. Sessions expire after 24 hours of inactivity.

**Error handling** — The server detects specific Codex CLI errors and returns user-friendly messages for "not authenticated", "rate limit/429", and empty output cases. All error responses use a consistent `[Claudex Error]` prefix.

**Version check** — On the first tool invocation per session, the server checks npm for Codex CLI updates. The warning is shown once and then suppressed. If the check itself fails (timeout, network error), it stays unresolved and retries next time rather than caching a failure.

## Plugin Structure

```
Claudex/
├── .claude-plugin/
│   └── plugin.json          # Plugin manifest
├── .mcp.json                # MCP server registration
├── server/
│   └── server.py            # Python MCP server (runs via uv)
├── commands/
│   ├── plan.md              # /codex:plan
│   ├── brainstorm.md        # /codex:brainstorm
│   ├── collab.md            # /codex:collab
│   ├── evaluate.md          # /codex:evaluate
│   ├── recap.md             # /codex:recap
│   ├── review.md            # /codex:review
│   ├── review-diff.md       # /codex:review-diff
│   ├── status.md            # /codex:status
│   ├── help.md              # /codex:help
│   └── doctor.md            # /codex:doctor
├── skills/
│   └── claudex/
│       └── SKILL.md         # Auto-triggers during plan mode
├── tests/
│   └── test_helpers.py      # Test suite (uv run --script)
├── .claudex/                # Scratchpad (gitignored)
│   ├── run-<uuid>/          # Per-run artifact directories
│   ├── sessions/            # Iterative session documents
│   └── recaps/              # Decision records from codex_recap
├── install.sh               # One-liner installer
├── docs/
│   └── initial-plan.md      # Original design document
├── CLAUDE.md
├── README.md
└── LICENSE
```

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| "Codex CLI not found" | `npm i -g @openai/codex` |
| "Not authenticated" | `codex login` |
| "Rate limit reached" | Wait for 5-hour window reset |
| Timeout | Lower `reasoning_effort` to `medium` |
| Empty response | Be more specific about the task |
| Tools not showing | Check `/mcp`, restart CC session |

## Credits

Created by **Omri Tal** — [GitHub](https://github.com/OmriT123) | [botique.co.il](https://www.botique.co.il) | hello@botique.co.il

Infrastructure & logic contributions by **Gad Cohen**, COO @ [Evolven](https://www.evolven.com) — [LinkedIn](https://www.linkedin.com/in/gad-cohen-a4856/)

## License

MIT

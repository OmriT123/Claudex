# Claudex — Claude Code Plugin

Give Claude Code a Codex-powered teammate. Two different AI architectures collaborate on the same codebase — planning, red-teaming, debugging, verification, and decision support.

## How It Works

```
You ask Claude Code to implement something
        │
        ▼
CC formulates its own plan
        │
        ▼
CC calls codex_plan via MCP ─────────────────┐
        │                                      │
        ▼                                      ▼
CC has Plan A                          Codex reads your repo
                                       (read-only sandbox)
                                       Forms its OWN understanding
                                               │
                                               ▼
                                       Codex produces Plan B
                                               │
        ◄──────────────────────────────────────┘
        │
        ▼
CC compares Plan A vs Plan B
Adopts best ideas from each
        │
        ▼
Presents unified plan to you
with clear CC/Codex attribution
```

**Key design:** Codex explores the codebase directly — it reads files, understands patterns, and forms its own mental model. CC points it in the right direction with `focus_files`, it doesn't pre-summarize context.

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

```bash
curl -fsSL https://raw.githubusercontent.com/OmriT123/Claudex/main/install.sh | bash
```

This checks prerequisites, registers the plugin marketplace, and installs Claudex globally. Start a new Claude Code session and you're ready to go.

### Verify

- `/mcp` — should show `codex` with its tools
- Type: `use codex_ping to check if Codex is working`

<details>
<summary>Manual install / local development</summary>

**Manual install:**

```bash
# 1. Clone the marketplace
git clone https://github.com/OmriT123/claude-plugins.git \
  ~/.claude/plugins/marketplaces/omri-plugins

# 2. Register it — add to ~/.claude/plugins/known_marketplaces.json:
#    "omri-plugins": {
#      "source": { "source": "github", "repo": "OmriT123/claude-plugins" },
#      "installLocation": "~/.claude/plugins/marketplaces/omri-plugins",
#      "lastUpdated": "2026-02-17T00:00:00.000Z"
#    }

# 3. Install
claude plugin install codex
```

**Local development:**

```bash
claude --plugin-dir /path/to/Claudex
```

</details>

## Commands

| Command | What It Does |
|---------|-------------|
| `/codex:plan [task]` | CC and Codex independently plan the same task, then CC synthesizes |
| `/codex:brainstorm [topic]` | Explore approaches from two AI perspectives |
| `/codex:collab [problem]` | CC shares its analysis, Codex provides targeted suggestions |
| `/codex:evaluate [options]` | Codex analyzes tradeoffs between approaches — user decides |
| `/codex:recap [session_id]` | Generate a decision record from a collaboration session |
| `/codex:review-files [files]` | Get a focused code review from Codex on specific files |
| `/codex:review-diff [focus]` | Get Codex to review your git diff before committing |
| `/codex:status` | Show Claudex diagnostics (zero Codex cost) |

### Examples

```
/codex:plan Add rate limiting to all API endpoints

/codex:brainstorm How should we handle caching for the dashboard?

/codex:collab I'm getting a race condition in the worker queue

/codex:evaluate Redis vs PostgreSQL pub/sub for real-time events

/codex:review-diff security
```

## MCP Tools

| Tool | Purpose | Codex Persona |
|------|---------|---------------|
| `codex_plan` | Codex makes its OWN plan, CC compares with its plan | Creative Architect |
| `codex_review` | Codex critiques a specific plan you provide | Critical QA Engineer |
| `codex_brainstorm` | Open-ended exploration of a problem | Innovation Consultant |
| `codex_collab` | Targeted collaboration — CC sends analysis, gets suggestions | Varies by request type |
| `codex_review_files` | Targeted code review of specific files (structured JSON by default) | Senior Code Reviewer |
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

CC manages the document. The server writes Codex's responses. Each `codex_collab` call with the same `session_id` appends to the existing session. Pass `session_id="auto"` to auto-generate a descriptive ID from the problem statement. After 4 rounds or resolution, use `codex_recap` to generate a formal decision record.

## Decision Support with `codex_evaluate`

Unlike other tools where CC arbitrates, `codex_evaluate` presents analysis for the **user** to decide:

```
You: "Should we use Redis or PostgreSQL pub/sub for real-time events?"

CC calls codex_evaluate with both options + constraints + priorities

Codex analyzes tradeoffs:
  Redis: Lower latency, but adds infra dependency
  PG pub/sub: No new infra, but higher latency at scale

CC presents BOTH analyses → You decide
```

## Artifact Scratchpad

Codex can produce file artifacts — code snippets, test drafts, analysis docs — without ever having write access to your codebase.

**How it works:**
1. Codex runs in `--sandbox read-only` (enforced by Codex CLI — physically cannot write)
2. Codex embeds `<claudex-artifact>` blocks in its text output
3. The **server** parses these from the final-answer section only
4. The server writes them to `.claudex/run-<uuid>/` (isolated per invocation)
5. Claude Code receives clean text + an artifact listing and can read the files

**Security model:**
- Codex never has write access — the sandbox enforces it
- Filenames are validated against path traversal (`Path.resolve()` + `is_relative_to()`)
- Symlink writes are rejected; exclusive-create prevents overwrite races
- Only the final-answer section is parsed (reasoning traces are ignored)
- Artifacts > 100KB are skipped
- Run directories are cleaned up after 1 hour

**Setup:** Add `.claudex` to your `.gitignore`:
```bash
echo '.claudex' >> .gitignore
```

## Defaults

- **Model**: `gpt-5.3-codex` (overridable per-call via `model` parameter)
- **Reasoning effort**: `high` (override per-call: `low`, `medium`, `high`, `xhigh`)
- **Reasoning summary**: `detailed` (overridable: `detailed`, `concise`, `none`)
- **Sandbox**: `read-only` — Codex reads your repo but never modifies it
- **Timeout**: 1200s (20 min) global default, per-tool overrides (e.g. `codex_review_files`: 300s, `codex_brainstorm`: 900s)
- **Auto-retry**: On timeout, `xhigh` → `high` and `high` → `medium` are retried once automatically
- **Session rollover**: After 4 rounds, sessions auto-rollover (recap generated, new chained session)
- **Structured output**: `codex_review_files` and `codex_review_diff` return structured JSON findings (severity, confidence, file:line) by default. Set `structured_output=False` for legacy text mode
- **Version check**: Auto-checks for Codex CLI updates on first invocation
- **Metrics**: In-memory per-tool stats (calls, successes, timeouts, errors, avg latency) — visible in `codex_status`

## Rate Limits

Codex uses your ChatGPT subscription quota:
- **Plus ($20/mo)**: ~30–150 messages per 5-hour window
- **Pro ($200/mo)**: ~300–1,500 messages per 5-hour window
- Each tool call = 1 message from quota
- If rate-limited: wait for window reset or use `gpt-5-codex-mini`

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
│   ├── review-files.md      # /codex:review-files
│   ├── review-diff.md       # /codex:review-diff
│   └── status.md            # /codex:status
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
| Timeout (>5min) | Use `gpt-5-codex-mini` or `medium` reasoning |
| Empty response | Be more specific about the task |
| Tools not showing | Check `/mcp`, restart CC session |

## Author

Created by **Omri Tal** — [GitHub](https://github.com/OmriT123) | [botique.co.il](https://www.botique.co.il) | hello@botique.co.il

## License

MIT

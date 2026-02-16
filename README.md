# Claudex — Claude Code Plugin

Give Claude Code a Codex-powered teammate. Two different AI architectures collaborate on the same codebase — planning, red-teaming, debugging, verification, and more.

## How It Works

```
You ask Claude Code to implement something
        |
        v
CC formulates its own plan
        |
        v
CC calls claudex_plan via MCP -----------------+
        |                                       |
        v                                       v
CC has Plan A                          Codex reads your repo
                                       (read-only sandbox)
                                               |
                                               v
                                       Codex produces Plan B
                                               |
        <--------------------------------------+
        |
        v
CC compares Plan A vs Plan B
Adopts best ideas from each
        |
        v
Presents unified plan to you
with clear CC/Codex attribution
```

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

### Option A: Install from GitHub

```bash
claude plugin install https://github.com/OmriT123/Codex-plan-plugin
```

### Option B: Install from local directory

```bash
claude --plugin-dir /path/to/Claudex
```

### Verify

Start a new CC session, then:
- `/mcp` — should show `claudex` with its tools
- Type: `use claudex_ping to check if Codex is working`

## Commands

| Command | What It Does |
|---------|-------------|
| `/claudex:plan [task]` | CC and Codex independently plan the same task, then CC synthesizes |
| `/claudex:brainstorm [topic]` | Explore approaches from two AI perspectives |
| `/claudex:collab [problem]` | CC shares its analysis, Codex provides targeted suggestions |

### Examples

```
/claudex:plan Add rate limiting to all API endpoints

/claudex:brainstorm How should we handle caching for the dashboard?

/claudex:collab I'm getting a race condition in the worker queue
```

## MCP Tools Reference

| Tool | Purpose | Quota Cost |
|------|---------|------------|
| `claudex_plan` | Codex makes its OWN plan, CC compares with its plan | 1 message |
| `claudex_review` | Codex critiques a specific plan you provide | 1 message |
| `claudex_brainstorm` | Open-ended exploration of a problem | 1 message |
| `claudex_collab` | Targeted collaboration — CC sends analysis, gets suggestions | 1 message |
| `claudex_review_files` | Targeted code review of specific files | 1 message |
| `claudex_ping` | Test that Codex is installed and working | 1 message |

## Collaboration Modes

The `claudex_collab` tool supports these request types:

| Type | Use When |
|------|----------|
| `feature_suggestion` | You need feature ideas or implementation approaches |
| `bug_approach` | You need help debugging or identifying root causes |
| `code_critique` | You want Codex to review your proposed solution |
| `red_team` | You want Codex to challenge assumptions and find weaknesses |
| `verification` | You want Codex to independently verify correctness |
| `testing_strategy` | You want Codex to suggest what and how to test |
| `general` | Open-ended analysis and suggestions |

## Defaults

- **Model**: `gpt-5.3-codex` — current best Codex model
- **Reasoning effort**: `xhigh` (extra high) — maximum reasoning depth
- **Reasoning summary**: `detailed` — includes Codex's chain-of-thought
- **Sandbox**: `read-only` — Codex can read your repo but never modify it

## Rate Limits

Codex uses your ChatGPT subscription quota:
- **Plus ($20/mo)**: ~30-150 messages per 5-hour window
- **Pro ($200/mo)**: ~300-1,500 messages per 5-hour window
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
│   ├── plan.md              # /claudex:plan
│   ├── brainstorm.md        # /claudex:brainstorm
│   └── collab.md            # /claudex:collab
├── skills/
│   └── claudex/
│       └── SKILL.md         # Auto-triggers during plan mode
├── docs/
│   └── initial-plan.md      # Original design document
├── README.md
└── LICENSE
```

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| "Codex CLI not found" | codex not installed or not in PATH | `npm i -g @openai/codex` |
| "Not authenticated" | Not logged in to ChatGPT | `codex login` |
| "Rate limit reached" | Hit subscription quota | Wait for 5-hour window reset |
| Timeout (>5min) | Complex prompt + high reasoning | Use `gpt-5-codex-mini` or `medium` reasoning |
| Empty response | Prompt too vague | Be more specific about the task |
| Tools not showing | Plugin not loaded | Check with `/mcp`, restart CC session |

## License

MIT

# Claudex — Claude Code Plugin <sup>v3.0.0</sup>

Give Claude Code a Codex-powered teammate. Two different AI architectures collaborate on the same codebase — planning, security-testing, debugging, verification, and decision support.

Ask in plain words ("use Codex to review this independently") or run a command
such as `/claudex:plan`. Works in Claude Code and, through your claude.ai
account, in chat and Cowork. Codex runs on your computer under your own OpenAI
sign-in; see [What Claudex sends, runs and stores](#what-claudex-sends-runs-and-stores).

> **Upgrading from 2.x?** Commands are now `/claudex:*` (were `/codex:*`) and
> plugin tools are `mcp__plugin_claudex_codex__*`. Natural requests like "use
> Codex" work as before. See [CHANGELOG.md](CHANGELOG.md#300-2026-10-01---plugin-renamed-to-claudex).

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

**Platform: macOS or Linux.** On Windows, install WSL2 (`wsl --install` in an
administrator PowerShell, then reboot) and run Claude Code, Codex and uv inside
the Ubuntu terminal. Native Windows and the Windows desktop app are not
supported yet: Claudex is untested there and Codex's native Windows sandbox is
still experimental.

1. **Codex CLI ≥ 0.153.1** — the bridge to OpenAI. Older CLIs are rejected by the API for the default `gpt-6-astra` model (`requires a newer version of Codex`). Your ChatGPT account must also have GPT-6 Astra access (OpenAI is rolling it out in stages; Enterprise workspaces enable it explicitly) — Claudex never falls back to another model silently:
   ```bash
   npm i -g @openai/codex@latest
   codex login
   ```

2. **uv 0.11.4 or newer**: Python package runner. The server's dependencies are
   pinned in `server/server.py.lock` and launched with `uv run --locked`; older uv
   ignored that flag when the lockfile was missing, so the server declares this floor
   and older uv refuses to start it ("Required uv version `>=0.11.4`"):
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh   # new install
   uv self update                                    # existing install
   ```

3. **Claude Code** — recent version with plugin support.

## Installation

**Claude Code:** in any session, run:

```
/plugin marketplace add OmriT123/claude-plugins
/plugin install claudex@omri-plugins
```

**Your claude.ai account (chat, Cowork and Claude Code):** in claude.ai or the
Claude desktop app, open **Customize > Plugins > Add > Add marketplace**, enter
`OmriT123/claude-plugins`, then select **Add** on Claudex. The skill and commands
load everywhere you use that account, and the plugin syncs to Claude Code. The
Codex tools themselves run on your computer: in the desktop app while it is
open, in Claude Code locally. If they don't appear in the desktop app, install
the desktop extension below.

Then choose the folders Codex may work in: run `/claudex:setup`, or see
[Workspace confinement](#workspace-confinement-required).

### Verify

- `/mcp` — should show `plugin:claudex:codex` with its tools
- `codex_status` (free) — shows the version, build and where the roots come from
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

## Workspace confinement (required)

**Breaking change (v2.0): Claudex is deny-by-default.** Every tool rejects every
project directory until you configure allowed workspace roots. Earlier versions
treated missing configuration as "unrestricted" — that mode is gone.

This is **working-directory confinement**, not an OS-level read sandbox: it
controls the directory Codex is launched in (`cwd`) and which directories you
may select, and `--sandbox read-only` prevents writes. Codex still runs with
your normal file permissions, so OS-level read isolation is not claimed here
(it's on the roadmap). Configure the working directories Codex may run in, in
whichever way fits:

- **Every Claude app on this computer (v2.4, recommended)**: one terminal
  command writes a per-user config file that every Claudex server on the machine
  reads (plugin, account plugin, desktop extension). It takes effect on the next
  call; nothing to restart. `codex_status` prints the exact path to use.

  ```bash
  uv run --locked --script /path/to/claudex/server/server.py --configure-roots ~/Projects ~/work
  uv run --locked --script /path/to/claudex/server/server.py --show-roots
  uv run --locked --script /path/to/claudex/server/server.py --revoke-roots   # kill switch
  ```

  `--revoke-roots` switches Claudex off on this computer: it beats every other
  setting until you run `--configure-roots` again. The file lives in
  `~/Library/Application Support/Botique/Claudex/config.json` (macOS),
  `~/.config/botique-claudex/config.json` (Linux) or
  `%APPDATA%\Botique\Claudex\config.json` (Windows), located from your OS
  account rather than from `HOME`/`APPDATA`, so a project that sets those
  variables cannot point Claudex at another file. It is never read from a
  project, and no MCP tool can change it. On macOS and Linux the file and its
  folder must belong to you and must not be symlinks, and no folder traversed
  on the way to it (through any symlink) may be writable by other accounts;
  otherwise every folder is denied. On
  Windows only the file itself is checked, until the Windows port. Your home folder itself, filesystem roots
  and protected locations are refused. Revoking stops new calls and withholds
  job results still held in memory; a Codex run already in progress finishes.

- **Claude Code only**: export `CLAUDEX_ALLOWED_ROOTS` in your shell
  profile before launching `claude`; the plugin's server inherits Claude Code's
  process environment.

  ```bash
  export CLAUDEX_ALLOWED_ROOTS="/Users/you/Projects:/Users/you/work"
  ```

  Multiple roots are separated by your OS path separator (`:` on macOS/Linux,
  `;` on Windows). Subdirectories of a root are allowed automatically. The server
  reads this at **spawn** time, so after changing it you must quit and relaunch
  Claude Code — reconnecting from `/mcp` respawns the server from the same stale
  parent environment.

- **Claude Desktop (extension)** — pick your project folders in the extension
  settings ("Allowed project folders"). The field is required.

- **Advanced** — the server also accepts `--allowed-roots <path> [<path> ...]`
  on its command line, which takes precedence over the env var and avoids
  separator parsing entirely. Running the server from your own MCP client config
  rather than the plugin? Set `CLAUDEX_ALLOWED_ROOTS` as an `env` block on that
  entry.

- **Claude Code cloud sessions** — nothing to configure: with no roots set, the
  session's project directory (`CLAUDE_PROJECT_DIR`) becomes the one root, only when
  Claude Code reports a cloud session (`CLAUDE_CODE_REMOTE=true`). An explicit
  `CLAUDEX_ALLOWED_ROOTS` still wins. See "Claude Code cloud sessions" below.

**Which setting wins** (first match; `codex_status` names the one in use):

| Order | Source | Notes |
|---|---|---|
| 1 | `--revoke-roots` in the config file | kill switch: denies everything |
| 2 | `--allowed-roots` on the server command line | desktop extension launcher on Windows |
| 3 | `CLAUDEX_ALLOWED_ROOTS` | shell profile, MCP `env` block, desktop extension folder picker |
| 4 | Config file from `--configure-roots` | an unreadable or malformed file denies everything |
| 5 | Claude Code cloud session default | the session's project directory |
| 6 | nothing | every project directory is denied |

Protected locations (`~/.ssh`, `~/.aws`, keychains, `~/.codex`, …) cannot be
selected as a working directory, even inside an allowed root (this bounds where
Codex runs — it is not a read-time filter on individual files). If a call fails
because no folders are allowed, this section (or `/claudex:setup`) is the fix.

**Not a sandbox against a repository you open.** Claudex runs your local `git`
against the working directory for diff review and context. A git repository can,
through its own `.git/config` and `.gitattributes` (clean/smudge filters, and
historically diff/hook drivers), execute programs on your machine when git
operates on it — this is inherent to git, exactly as your own shell, editor, or
build already trigger that tooling. Claudex disables the vectors git lets it
(`--no-ext-diff`, `--no-textconv`, `core.fsmonitor`, hooks) but git provides no
switch for attribute-driven filters, so **treat reviewing an untrusted
repository as running its tooling.** OS-level isolation of git operations is on
the roadmap.

**Upgrading from ≤1.8.x:** after updating, Claudex will refuse every call until
you set roots as above — this is intentional. One line of config restores your
workflow, now with an explicit boundary.

## What Claudex sends, runs and stores

- **Sends to OpenAI, under your own Codex sign-in:** the task text Claude passes
  in (including your message as `user_prompt`, when it is in scope), earlier
  rounds of a collab session, git context (branch, diff stat, last 5 commit
  subjects; the full diff for `codex_review_diff`) and the contents of any
  project files Codex chooses to read. Botique receives nothing; there is no
  Claudex server or telemetry.
- **Runs on your computer:** the Codex CLI (read-only sandbox), `git` in the
  project, and `uv`, which downloads the pinned Python packages on first start.
- **Stores:** `.claudex/` in the project (artifacts about 1 hour; sessions,
  recaps and job results about 24 hours, cleaned on later calls) and, per user,
  a daily execution counter and your allowed-folders file.

Details: [PRIVACY.md](PRIVACY.md). Decide before use whether you may share a
project's code with OpenAI.

## Claude Code cloud sessions (claude.ai/code, mobile)

Cloud sessions run in a fresh VM: nothing from your machine is there, MCP tool
calls are capped at 60s, egress goes through an allowlisting proxy, and untracked
files block the session's end. Since v2.2 Claudex handles its side: a 30-min
per-server tool timeout in `.mcp.json`, the project directory as the default
workspace root, `.claudex/` that ignores itself, fail-fast network errors that
name the blocked host, and `CODEX_API_KEY` passed through to Codex. You configure
the environment once (claude.ai/code → environment settings):

1. **Network access** → Custom, keep "Also include default list", and add
   `api.openai.com` (API-key auth) or `chatgpt.com` + `auth.openai.com`
   (ChatGPT-plan login). Codex telemetry and plugin traffic are switched off, so
   no other OpenAI host is needed.
2. **Setup script**:
   ```bash
   curl -fsSL https://raw.githubusercontent.com/OmriT123/Claudex/main/cloud/setup.sh | bash
   ```
   Installs the Codex CLI and this plugin (cached with the environment snapshot).
3. **Codex auth**, one of:
   - **API key** — environment variable `CODEX_API_KEY=sk-...`. No per-session
     step; billed to your OpenAI API account. Anyone who uses the environment,
     Claude included, can read environment variables.
   - **ChatGPT plan** — once per session, run **`/claudex:login`**: you get a link and
     a one-time code to approve on your phone, and Codex is ready (enable device
     code login under ChatGPT Settings → Security first). Don't copy your local
     `~/.codex/auth.json` instead: refresh tokens are single-use, so the second
     machine to refresh logs the other one out.

Then start a new session and run `/claudex:doctor` or `codex_ping`. In the VM Codex
runs as root with a read-only sandbox that can read any file there, so keep
secrets you don't want sent to OpenAI out of the environment.

Only want the phone/web UI, not cloud compute? `claude remote-control` in your
local project drives your local session (and your local Claudex) from claude.ai
with no setup.

## Commands

| Command | What It Does |
|---------|-------------|
| `/claudex:plan [task]` | Claude Code and Codex independently plan the same task, then synthesize |
| `/claudex:brainstorm [topic]` | Explore approaches from two AI perspectives |
| `/claudex:collab [problem]` | Claude Code shares its analysis, Codex provides targeted suggestions |
| `/claudex:evaluate [A vs B]` | Codex analyzes tradeoffs between approaches — user decides |
| `/claudex:recap [session_id]` | Generate a decision record from a collaboration session |
| `/claudex:review [files]` | Get a focused code review from Codex on specific files |
| `/claudex:review-diff [focus]` | Get Codex to review your git diff before committing |
| `/claudex:setup [folders]` | Choose the folders Codex may work in (workspace roots), then check readiness |
| `/claudex:login` | Sign Codex in to ChatGPT with a one-time code — no browser needed (cloud sessions, SSH) |
| `/claudex:status` | Show Claudex diagnostics (zero Codex cost) |
| `/claudex:help` | Quick start guide |
| `/claudex:doctor` | Diagnose and fix Claudex issues |

### Examples

Three end-to-end examples you can reproduce on any small git repository inside
an allowed folder:

1. **Independent plan.** Ask "Use Codex independently to plan adding rate limiting
   to the API", or run `/claudex:plan Add rate limiting to all API endpoints`. Claude
   writes its own plan, Codex reads the repo and writes another, and you get one
   synthesized plan with who-suggested-what.
2. **Pre-commit review.** Stage a change, then run `/claudex:review-diff security`.
   You get findings with severity, file and line, and a ship / fix-first verdict
   bound to the exact HEAD and diff hash.
3. **Decision support.** Run `/claudex:evaluate Redis vs PostgreSQL pub/sub for
   real-time events`. Codex lays out the tradeoffs; Claude presents both analyses;
   you decide.

More:

```
/claudex:plan Add rate limiting to all API endpoints

/claudex:brainstorm How should we handle caching for the dashboard?

/claudex:collab I'm getting a race condition in the worker queue

/claudex:evaluate Redis vs PostgreSQL pub/sub for real-time events

/claudex:review-diff security

/claudex:review src/auth.py, src/middleware.py
```

## Install as a Claude Desktop Extension (Cowork / desktop app)

Claudex also ships as a desktop extension (`.mcpb`) so the Codex tools are
available in the Claude desktop app and get proxied into Cowork cloud sessions:

```bash
./desktop-extension/build.sh    # produces desktop-extension/claudex.mcpb
```

Double-click `claudex.mcpb` to install (Settings → Extensions if prompted),
then fully restart the Claude desktop app. Prerequisites on the target Mac:
[uv](https://astral.sh/uv) 0.11.4 or newer and the Codex CLI
(`npm i -g @openai/codex@latest && codex login`). The bundle carries the server
and its lockfile; replace an older `.mcpb` rather than keeping both.

Note for capped transports (e.g. Cowork's device bridge, which limits tool calls
to 60s): use `codex_submit` / `codex_result` — see Async jobs below.

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
| `codex_login` | Sign Codex in with a device code (link + one-time code; completes in the background) | — |
| `codex_submit` | Run any tool above as a background job — returns a job_id in <1s | (delegates) |
| `codex_result` | Collect a background job's status/result (zero Codex cost) | — |

### Async jobs (v1.7)

Some MCP clients cap synchronous tool calls below Codex latency (the Claude
desktop-app device bridge kills calls at 60s; long calls also block a Claude Code
session). `codex_submit` accepts `{"tool": "review", "arguments": {...}}` — the same
arguments you'd pass synchronously — validates them immediately, runs the tool as a
background task (max 4 concurrent, 8 admitted), and returns a `job_id` instantly.
`codex_result` polls (bounded `wait_seconds` ≤ 45) or returns the finished output.
Results also persist to `.claudex/jobs/<job_id>.md`, so they survive client
disconnects and server restarts (retained for 24 hours, then cleaned up like
sessions); a job left `running` by a killed server is reported as interrupted,
never returned as a result.

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

**Git:** no setup needed. Since v2.2 Claudex creates `.claudex/.gitignore` with `*`,
so its files stay out of `git status` and commits (your own `.gitignore` is left
alone). It never overwrites a `.claudex/.gitignore` a repository already has;
`codex_status` warns unless that file's only rule is `*` (files git already
tracks stay tracked either way).

## Defaults

- **Model**: `gpt-6-astra` — OpenAI's GPT-6 Astra, the only sanctioned default (overridable per-call via `model` parameter for deliberate one-offs). Requires Codex CLI ≥ 0.153.1 (see Prerequisites)
- **Reasoning effort**: `high` on **every** tool, reviews and recaps included (override per-call: `low`, `medium`, `high`, `xhigh`, `max` — reserve `xhigh`/`max` for hard architectural decisions; Astra's `ultra` delegation tier is intentionally not exposed)
- **Reasoning summary**: `detailed` (overridable: `detailed`, `concise`, `none`)
- **Sandbox**: `read-only` — Codex reads your repo but never modifies it
- **Timeout**: 1200s (20 min) for all tools; the plugin's `.mcp.json` sets a 30-min per-server MCP timeout so clients with a shorter default cap (Claude Code cloud: 60s) don't cut calls
- **Git context**: All tools (except `codex_review_diff`) automatically inject current branch, diff stat, recent commits, and staged changes into the Codex prompt — no manual context needed
- **Session rollover**: After 4 rounds, sessions auto-rollover (recap generated, new chained session with `-p2`/`-p3` suffix)
- **Version check**: Compares the installed Codex CLI against a pinned minimum on first tool invocation — offline, no network call (warning shown once per session)
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

Codex uses your ChatGPT subscription quota (with `CODEX_API_KEY`, usage is billed to your OpenAI API account instead):
- **Plus ($20/mo)**: ~30–150 messages per 5-hour window
- **Pro ($200/mo)**: ~300–1,500 messages per 5-hour window
- Each tool call = 1 message from quota
- Structured output auto-fallback costs 1 extra message if it triggers
- If rate-limited: wait for window reset (lowering `reasoning_effort` is a last resort — `high` is the standing policy)

## Under the Hood

**Git context injection** — Every Codex call (except `codex_review_diff`, which handles its own diff) automatically collects and injects the current git branch, unstaged diff stat (capped at 20 lines), last 5 commit messages, and staged changes stat (capped at 5KB) into the system prompt. This gives Codex awareness of your working state without you needing to specify it.

**Session context management** — Session documents are capped at 32KB. When a session exceeds this, the oldest rounds are dropped first to stay within the limit. Sessions expire after 24 hours of inactivity.

**Error handling** — The server detects specific Codex CLI errors and returns user-friendly messages for "not authenticated", "rate limit/429", "model requires a newer version of Codex" (CLI too old for `gpt-6-astra` — upgrade hint with the pinned floor), a proxy/network policy blocking the OpenAI host (names the host), connection failures, and empty output cases. Codex runs with bounded connection retries, so an unreachable endpoint fails in under a minute instead of hanging until the timeout. All error responses use a consistent `[Claudex Error]` prefix.

**Environment** — Codex gets an allowlisted environment (`PATH`, `HOME`, proxy and CA variables, …); everything else is stripped. Codex's own credentials (`CODEX_API_KEY`, `CODEX_ACCESS_TOKEN`) are passed to codex spawns only (never to git) and are hidden from the commands Codex's model runs. Codex telemetry, plugin and app traffic are disabled.

**Version check** — On the first tool invocation per session, the server compares the installed Codex CLI against a locally pinned minimum version. This is **offline by design** since v2.0: it runs `codex --version` and compares locally, with no network call (the pre-v2.0 build queried the npm registry at runtime — an undeclared outbound call, removed). The warning is shown once and then suppressed. If the check itself fails, it stays unresolved and retries after a backoff rather than caching a failure.

## Plugin Structure

```
Claudex/
├── .claude-plugin/
│   └── plugin.json          # Plugin manifest
├── .mcp.json                # MCP server registration
├── server/
│   ├── server.py            # Python MCP server (runs via uv)
│   └── server.py.lock       # Pinned dependencies (uv run --locked)
├── commands/
│   ├── plan.md              # /claudex:plan
│   ├── brainstorm.md        # /claudex:brainstorm
│   ├── collab.md            # /claudex:collab
│   ├── evaluate.md          # /claudex:evaluate
│   ├── recap.md             # /claudex:recap
│   ├── review.md            # /claudex:review
│   ├── review-diff.md       # /claudex:review-diff
│   ├── status.md            # /claudex:status
│   ├── help.md              # /claudex:help
│   ├── doctor.md            # /claudex:doctor
│   ├── login.md             # /claudex:login
│   └── setup.md             # /claudex:setup
├── skills/
│   └── codex/
│       └── SKILL.md         # When and how Claude consults Codex (claudex:codex)
├── tests/
│   └── test_helpers.py      # Test suite (uv run --script)
├── .claudex/                # Scratchpad (gitignored)
│   ├── run-<uuid>/          # Per-run artifact directories
│   ├── sessions/            # Iterative session documents
│   └── recaps/              # Decision records from codex_recap
├── install.sh               # One-liner installer
├── cloud/
│   └── setup.sh             # Claude Code cloud environment setup script
├── docs/
│   └── initial-plan.md      # Original design document
├── CHANGELOG.md
├── PRIVACY.md
├── CLAUDE.md
├── README.md
└── LICENSE
```

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| "Codex CLI not found" | `npm i -g @openai/codex` |
| "Codex CLI is too old for model 'gpt-6-astra'" | `npm i -g @openai/codex@latest` — needs ≥ 0.153.1 |
| "Codex is not signed in" | `/claudex:login` (or `codex login` in a terminal; cloud: `CODEX_API_KEY` also works) |
| "A network policy blocked Codex's connection to <host>" | Allow that host in your sandbox's network settings (Claude Code cloud: environment → Network access), then start a new session |
| "Rate limit reached" | Wait for 5-hour window reset |
| Timeout | Narrow `focus_files` or raise `timeout_seconds` (lowering `reasoning_effort` is a last resort) |
| Empty response | Be more specific about the task |
| Tools not showing | Check `/mcp`, restart CC session |
| Server failed with "Required uv version `>=0.11.4`" | `uv self update`, then reconnect the `codex` server from `/mcp`. Claude Code holds a failed start for about 15 minutes, and neither a restart nor `/reload-plugins` clears it |
| `/codex:...` command not found | Since 3.0 the commands are `/claudex:...` |
| Claudex commands listed under both `/codex:` and `/claudex:` | `/codex:review`, `/codex:status`, `/codex:setup` and `/codex:rescue` alone belong to OpenAI's Codex plugin and are expected. If `/codex:plan`, `/codex:collab` or `/codex:review-diff` appear, an old Claudex copy is still installed (often the claude.ai account copy): update it, then `/reload-plugins` |
| Calls cut off after 60s (cloud session) | Update to Claudex ≥ 2.2.0 (adds the per-server MCP timeout) |

## Support

Questions, bugs or security reports: [hello@botique.co.il](mailto:hello@botique.co.il)
or [GitHub issues](https://github.com/OmriT123/Claudex/issues). Privacy and data
handling: [PRIVACY.md](PRIVACY.md).

## Credits

Created by **Omri Tal** — [GitHub](https://github.com/OmriT123) | [botique.co.il](https://www.botique.co.il) | hello@botique.co.il

Infrastructure & logic contributions by **Gad Cohen**, COO @ [Evolven](https://www.evolven.com) — [LinkedIn](https://www.linkedin.com/in/gad-cohen-a4856/)

## License

MIT

## Author

**Omri Tal — Botique AI Solutions**
AI systems, agents & automation for business · [www.botique.co.il](https://www.botique.co.il) · [hello@botique.co.il](mailto:hello@botique.co.il)

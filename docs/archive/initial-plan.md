# Claudex — Rebrand & Optimization Plan

## Context

The codex-plan plugin was just built (previous session). Now we need to:
1. **Rebrand** everything from `codex-plan` → `Claudex` — directory, plugin name, MCP key, tool names, skill, commands, README
2. **Expand the collaboration model** — Codex isn't just a second opinion, it's CC's specialist teammate for red-teaming, debugging, verification, testing, and more
3. **Optimize** the skill (better auto-trigger guidance, decision tree) and server (validate request_type via Enum, richer system prompts)
4. **Copy** the initial plan doc into the repo for reference

**Working directory**: `/Users/omri/Desktop/codex-plan plugin/` → will be renamed to `/Users/omri/Desktop/Claudex/`

---

## Step 1: Rename directory

```bash
mv "/Users/omri/Desktop/codex-plan plugin" "/Users/omri/Desktop/Claudex"
```

All subsequent paths reference `/Users/omri/Desktop/Claudex/`.

---

## Step 2: Update `.claude-plugin/plugin.json`

| Field | Old | New |
|-------|-----|-----|
| `name` | `codex-plan` | `claudex` |
| `description` | mentions "Codex" generically | Frame as "Claudex — Claude Code's Codex teammate" |
| `keywords` | `codex-plan` | `claudex` added |

---

## Step 3: Update `.mcp.json`

```json
{
  "claudex": {
    "command": "uv",
    "args": ["run", "${CLAUDE_PLUGIN_ROOT}/server/server.py"]
  }
}
```

Key changes from `codex-plan` → `claudex`.

---

## Step 4: Update `server/server.py`

### 4a. Identifier renames

| Old | New |
|-----|-----|
| `FastMCP("codex_plan")` | `FastMCP("claudex")` |
| `logger = ...("codex_plan")` | `logger = ...("claudex")` |
| Module docstring | "Claudex MCP Server" |

### 4b. Tool name rebrand

| Old Tool Name | New Tool Name |
|---------------|---------------|
| `codex_parallel_plan` | `claudex_plan` |
| `codex_second_opinion` | `claudex_review` |
| `codex_brainstorm` | `claudex_brainstorm` |
| `codex_collaborate` | `claudex_collab` |
| `codex_review_files` | `claudex_review_files` |
| `codex_ping` | `claudex_ping` |

Function names updated to match (e.g. `async def claudex_plan(...)`, `async def claudex_review(...)`).
Tool annotation `title` fields updated.

### 4c. Make `request_type` an Enum (optimization)

```python
class RequestType(str, Enum):
    FEATURE_SUGGESTION = "feature_suggestion"
    BUG_APPROACH = "bug_approach"
    CODE_CRITIQUE = "code_critique"
    RED_TEAM = "red_team"              # NEW — challenge assumptions, find weaknesses
    VERIFICATION = "verification"       # NEW — verify implementation correctness
    TESTING_STRATEGY = "testing_strategy" # NEW — suggest what/how to test
    GENERAL = "general"
```

Change `CollaborateInput.request_type` from `str` to `RequestType` with default `RequestType.GENERAL`.

### 4d. Expand COLLABORATE_SYSTEM prompt

Add the three new request types to the system prompt:

```
- red_team: Challenge every assumption. Find weaknesses, edge cases, failure modes.
  Act as an adversary trying to break the implementation.
- verification: Independently verify that the proposed implementation is correct.
  Check logic, data flow, error handling, and boundary conditions.
- testing_strategy: Suggest a comprehensive testing approach — what to test,
  edge cases to cover, integration points to validate, and test structure.
```

### 4e. Model/effort already user-selectable (confirmed)

Every tool input model already has `model: CodexModel` and `reasoning_effort: ReasoningEffort` fields with defaults of `gpt-5.3-codex` / `xhigh`. Users can override per-call. No change needed here.

---

## Step 5: Rename `skills/codex-plan/` → `skills/claudex/`

### 5a. Directory rename

```bash
mv skills/codex-plan skills/claudex
```

### 5b. Update `skills/claudex/SKILL.md`

Key changes:
- Frontmatter `name: codex-plan` → `name: claudex`
- Title: "Claudex — Claude Code Skill"
- Framing: Position Codex as CC's **specialist teammate** — a different AI architecture CC can consult as a tool, subagent, or teammate for any task
- All tool references: `codex_*` → `claudex_*`
- All command references: `/codex-plan:*` → `/claudex:*`
- Expand "When to Use" with the full range from insight #4:
  - Red team challenges
  - Implementation verification
  - Debugging assistance
  - Testing strategy
  - Backend/frontend architecture decisions
  - Error/gap prevention
- Update Workflow 4 (Collaboration) request_types to include `red_team`, `verification`, `testing_strategy`
- Add decision tree: "Which tool should I use?" quick reference
- Update tool table with new names

---

## Step 6: Update `commands/*.md`

All three command files:

### `commands/plan.md`
- Tool reference: `codex_parallel_plan` → `claudex_plan`
- Title/description: "Parallel Planning with Claudex"

### `commands/brainstorm.md`
- Tool reference: `codex_brainstorm` → `claudex_brainstorm`
- Title/description: "Brainstorm with Claudex"

### `commands/collab.md`
- Tool reference: `codex_collaborate` → `claudex_collab`
- Title/description: "Collaborate with Claudex"
- Add the three new request_types (`red_team`, `verification`, `testing_strategy`) to the type list

---

## Step 7: Rewrite `README.md`

Full rebrand:
- Title: "Claudex — Claude Code Plugin"
- Tagline: frame Claudex as giving CC a Codex-powered teammate
- All references `codex-plan` → `claudex`, tool names updated
- Commands: `/claudex:plan`, `/claudex:brainstorm`, `/claudex:collab`
- Plugin structure: directory names updated
- Tool reference table: new names
- Installation: `claude plugin install` URL (repo name stays same for now)
- Mention the expanded collaboration modes (red_team, verification, testing_strategy)

---

## Step 8: Copy initial plan doc

```bash
mkdir -p docs/
cp ~/.claude/plans/pure-drifting-thacker.md docs/initial-plan.md
```

---

## Files Summary

| File | Action |
|------|--------|
| Directory | **Rename** `codex-plan plugin/` → `Claudex/` |
| `.claude-plugin/plugin.json` | **Edit** — name, description, keywords |
| `.mcp.json` | **Edit** — key name |
| `server/server.py` | **Edit** — identifiers, tool names, functions, RequestType enum, COLLABORATE_SYSTEM |
| `skills/codex-plan/` | **Rename** → `skills/claudex/` |
| `skills/claudex/SKILL.md` | **Edit** — full rebrand + optimization |
| `commands/plan.md` | **Edit** — tool refs, title |
| `commands/brainstorm.md` | **Edit** — tool refs, title |
| `commands/collab.md` | **Edit** — tool refs, title, new request_types |
| `README.md` | **Rewrite** — full rebrand |
| `docs/initial-plan.md` | **Create** — copy of plan doc |

---

## Verification

1. **Directory renamed**: `ls ~/Desktop/Claudex/` shows all files
2. **No stale references**: `grep -r "codex.plan\|codex_plan\|codex-plan" ~/Desktop/Claudex/` returns zero hits (only `Codex CLI` and `Codex` the product name should remain)
3. **Python syntax**: `python3 -c "import ast; ast.parse(open('server/server.py').read())"` passes
4. **JSON valid**: `python3 -c "import json; json.load(open('.claude-plugin/plugin.json')); json.load(open('.mcp.json'))"` passes
5. **Tool names**: Verify all 6 tools are prefixed `claudex_` in server.py
6. **RequestType enum**: Verify 7 values including the 3 new ones
7. **Plugin loads**: `claude --plugin-dir ~/Desktop/Claudex` → `/mcp` shows `claudex` tools
8. **Commands**: `/claudex:plan`, `/claudex:brainstorm`, `/claudex:collab` all available

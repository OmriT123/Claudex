# Server Changes Specification

## Overview

This spec defines all changes needed in `server/server.py` to bring Claudex to production. Changes cover: persona-enriched system prompts, two new tools (`codex_evaluate`, `codex_recap`), session document management, codebase-first exploration instructions, and error handling improvements.

---

## 1. System Prompt Overhaul — Persona-Specific Instructions

Replace the current generic system prompts with persona-driven instructions. Each prompt should instruct Codex to **explore the codebase first** before addressing the task.

### Codebase-First Preamble (prepend to ALL system prompts)

```python
CODEBASE_FIRST_PREAMBLE = """
You have full read access to this project's codebase via the sandbox.
BEFORE addressing the task:
1. Read the project's CLAUDE.md, AGENTS.md, or README.md if present — understand the architecture
2. Read the focus files specified below — understand existing patterns and conventions
3. Form your OWN understanding of the codebase — do not rely on any summary provided

Only AFTER understanding the codebase context, address the task below.
""".strip()
```

### PARALLEL_PLAN_SYSTEM (for `codex_plan`)

```python
PARALLEL_PLAN_SYSTEM = f"""
{CODEBASE_FIRST_PREAMBLE}

## Your Role: Creative Architect

You are an independent technical architect creating your OWN implementation plan.
Another AI (Claude) is simultaneously creating its own plan for the same task.
Your plans will be compared — the best ideas from each will be synthesized.

Behavioral instructions:
- Explore broadly. Challenge conventions. Consider non-obvious approaches.
- Propose at least one approach the other AI is unlikely to think of.
- Be specific: name files, functions, data structures, endpoints.
- Flag risks and tradeoffs explicitly for each approach.
- If you find existing patterns in the codebase that inform your plan, reference them.

Structure your plan as:
1. **Context** — What you found in the codebase that's relevant
2. **Approach** — Your recommended implementation (specific, actionable)
3. **Alternatives Considered** — Other approaches and why you didn't pick them
4. **Risks & Mitigations** — What could go wrong and how to handle it
5. **Files to Create/Modify** — Exact paths and what changes
""".strip()
```

### SECOND_OPINION_SYSTEM (for `codex_review`)

```python
SECOND_OPINION_SYSTEM = f"""
{CODEBASE_FIRST_PREAMBLE}

## Your Role: Critical QA Engineer

You are reviewing a plan or implementation created by another AI (Claude).
Your job is NOT to agree — it's to find what's wrong, missing, or risky.

Behavioral instructions:
- Look for bugs, edge cases, security vulnerabilities, and performance issues.
- Check that the plan aligns with existing codebase patterns and conventions.
- Identify implicit assumptions that could fail in production.
- Focus on issues the author likely missed — don't repeat obvious checks.
- Suggest concrete fixes, not just "this could be better."

Structure your review as:
1. **Critical Issues** — Must fix before shipping (bugs, security, data loss risks)
2. **Warnings** — Should fix (edge cases, performance, maintainability)
3. **Suggestions** — Nice to have (style, alternative approaches, optimizations)
4. **What's Good** — Explicitly acknowledge solid design decisions (prevents over-rotation)
""".strip()
```

### BRAINSTORM_SYSTEM (for `codex_brainstorm`)

```python
BRAINSTORM_SYSTEM = f"""
{CODEBASE_FIRST_PREAMBLE}

## Your Role: Innovation Consultant

You are brainstorming solutions for a technical challenge. Think divergently.
Another AI (Claude) is brainstorming independently on the same topic.

Behavioral instructions:
- Generate at least 3-5 distinct approaches, not variations of the same idea.
- Draw from cross-domain patterns — what works in other contexts that applies here?
- For each idea, give a quick feasibility gut-check (easy/medium/hard).
- Don't self-censor — include bold ideas alongside safe ones.
- Consider the existing codebase: what can you leverage that's already built?

Structure your output as a list of approaches, each with:
- **Idea** — One-line summary
- **How It Works** — 2-3 sentence explanation
- **Leverages** — What existing code/infra it builds on
- **Tradeoff** — What you gain vs what it costs
- **Feasibility** — Easy / Medium / Hard with brief justification
""".strip()
```

### COLLABORATE_SYSTEM (for `codex_collab`)

This prompt should be dynamically constructed based on `request_type`:

```python
COLLAB_PERSONAS = {
    "bug_approach": """
## Your Role: Diagnostic Specialist
You are a systematic debugger helping to identify root causes.

Behavioral instructions:
- Rank hypotheses by likelihood based on the symptoms described.
- For each hypothesis, specify an exact test (command, assertion, file check).
- Consider: race conditions, state corruption, timing issues, env differences.
- If the other AI's analysis points in a direction, evaluate it critically.
- Don't suggest "add logging" unless you specify exactly WHERE and WHAT to log.
""",
    "red_team": """
## Your Role: Adversarial Security Researcher
You are trying to BREAK this implementation. Assume everything can fail.

Behavioral instructions:
- Find failure modes: concurrency bugs, input edge cases, resource exhaustion.
- Identify attack vectors: injection, privilege escalation, data leakage.
- Check error handling: what happens when dependencies fail?
- Look for implicit trust: unvalidated inputs, unchecked return values.
- Rate each finding: Critical / High / Medium / Low with exploitation scenario.
""",
    "verification": """
## Your Role: Formal Methods Engineer
You are independently verifying that this implementation is correct.

Behavioral instructions:
- Check logic: does the code do what it claims to do?
- Trace data flow: from input to output, what transformations happen?
- Verify error handling: are all error paths covered? Do they recover correctly?
- Check boundary conditions: empty inputs, max values, null/undefined, concurrent access.
- Validate invariants: what properties must ALWAYS hold? Do they?
""",
    "testing_strategy": """
## Your Role: Test Architect
You are designing a comprehensive testing strategy.

Behavioral instructions:
- Categorize tests: unit, integration, end-to-end, performance, security.
- Identify critical paths that MUST have test coverage.
- Suggest boundary conditions and edge cases specific to this implementation.
- Propose test structure: fixtures, mocks, test data setup/teardown.
- Prioritize: which tests give the most confidence per effort invested?
""",
    "code_critique": """
## Your Role: Senior Developer (Code Reviewer)
You are reviewing code for quality, maintainability, and correctness.

Behavioral instructions:
- Check: readability, naming, separation of concerns, DRY violations.
- Look for: anti-patterns, tech debt, performance bottlenecks, fragile code.
- Verify: error handling, input validation, resource cleanup.
- Assess: is this idiomatic for the language/framework? Does it follow project conventions?
- Prioritize your feedback — lead with what matters most.
""",
    "feature_suggestion": """
## Your Role: Product Engineer
You are suggesting features or implementation approaches.

Behavioral instructions:
- Evaluate feasibility given the existing codebase architecture.
- Estimate complexity: what needs to change, what can be reused?
- Consider user impact: what does this enable, what friction does it remove?
- Identify integration risks: what existing functionality could break?
- Suggest a phased approach if the feature is large.
""",
    "general": """
## Your Role: Collaborative Engineer
You are a knowledgeable colleague providing analysis and suggestions.

Behavioral instructions:
- Read the other AI's analysis carefully before responding.
- Add perspectives they may have missed.
- Be concrete: suggest specific code, files, approaches — not abstract advice.
- If you disagree with their analysis, explain why with evidence from the codebase.
"""
}

def build_collaborate_prompt(request_type: str) -> str:
    persona = COLLAB_PERSONAS.get(request_type, COLLAB_PERSONAS["general"])
    return f"{CODEBASE_FIRST_PREAMBLE}\n\n{persona}"
```

### REVIEW_FILES_SYSTEM (for `codex_review_files`)

```python
REVIEW_FILES_SYSTEM = f"""
{CODEBASE_FIRST_PREAMBLE}

## Your Role: Senior Code Reviewer

You are reviewing specific files for quality, correctness, and maintainability.
Focus your analysis on the files listed — but read surrounding code for context.

Behavioral instructions:
- Check patterns and anti-patterns specific to the language/framework.
- Look for: bugs, unhandled errors, resource leaks, race conditions.
- Assess naming, structure, separation of concerns, DRY compliance.
- Verify the code follows the project's existing conventions (check other files).
- Prioritize: critical issues first, style suggestions last.
- Be specific: reference line numbers and suggest concrete alternatives.

Structure your review per file:
1. **Summary** — What the file does and overall quality assessment
2. **Issues** — Bugs, risks, or correctness problems (with line references)
3. **Improvements** — Maintainability and performance suggestions
4. **Conventions** — Does it match the project's patterns? Deviations noted.
""".strip()
```

### EVALUATE_SYSTEM (NEW — for `codex_evaluate`)

```python
EVALUATE_SYSTEM = f"""
{CODEBASE_FIRST_PREAMBLE}

## Your Role: Technical Advisor

You are analyzing tradeoffs between multiple approaches so the USER can make
an informed decision. You do NOT recommend — you illuminate.

Behavioral instructions:
- For each option, analyze: complexity, performance, maintainability, risk profile.
- Make tradeoffs EXPLICIT: what you gain vs what you lose with each approach.
- Consider the existing codebase: which option integrates most naturally?
- Identify hidden costs: migration effort, learning curve, operational overhead.
- Flag irreversible decisions: which choices are hard to undo later?
- Present a comparison matrix if there are 3+ options.

Structure your analysis as:
1. **Context** — What you found in the codebase that's relevant to this decision
2. **Option Analysis** — For each option: how it works, what it costs, what it risks
3. **Comparison Matrix** — Side-by-side on key dimensions
4. **Key Tradeoff** — The single most important thing the decision-maker should weigh
5. **What I'd Want to Know** — Questions that could change the recommendation
""".strip()
```

### RECAP_SYSTEM (NEW — for `codex_recap`)

```python
RECAP_SYSTEM = f"""
{CODEBASE_FIRST_PREAMBLE}

## Your Role: Technical Writer

You are generating a concise decision record from a collaboration session
between two AIs (Claude and Codex). The audience is a developer who needs
to understand what was discussed, what was decided, and why.

Behavioral instructions:
- Be concise. This is a reference document, not a narrative.
- Attribute clearly: what did CC suggest vs what did Codex suggest?
- Focus on DECISIONS and REASONING, not process.
- Include: what was tried, what worked, what didn't, what was adopted.
- End with open items or next steps if any remain.

Structure the record as:
1. **Summary** — One paragraph: what was the problem and what was decided
2. **Key Findings** — Bullet list of important discoveries (attributed)
3. **Decision** — What approach was chosen and why
4. **Rejected Alternatives** — What was considered and why it was dropped
5. **Open Items** — Anything unresolved or needing follow-up
""".strip()
```

---

## 2. New Tool: `codex_evaluate`

### Input Model

```python
class EvaluateInput(BaseModel):
    """Input for codex_evaluate — tradeoff analysis for user decision-making."""
    options: str = Field(description="The options being evaluated. Describe each approach with enough detail for analysis. Separate with clear labels (Option A, Option B, etc.).")
    constraints: str = Field(default="", description="Non-negotiable requirements that any option must satisfy.")
    priorities: str = Field(default="", description="What the user is optimizing for (performance, maintainability, speed to ship, cost, etc.).")
    context: str = Field(default="", description="Additional context about why this decision matters or what's driving it.")
    focus_files: str = Field(default="", description="Comma-separated file/directory paths for Codex to read for codebase context.")
    project_dir: str = Field(default="", description="Project root directory for Codex to read.")
    model: CodexModel = Field(default=CodexModel.GPT_53_CODEX)
    reasoning_effort: ReasoningEffort = Field(default=ReasoningEffort.HIGH)
```

### Tool Function

```python
@mcp.tool(
    annotations=ToolAnnotations(title="Codex Evaluate — Tradeoff Analysis", readOnlyHint=True)
)
async def codex_evaluate(input: EvaluateInput) -> str:
    """Codex analyzes tradeoffs between approaches so the USER can decide.
    Unlike other tools, CC does NOT arbitrate — present both analyses to the user."""
    
    prompt_parts = [f"## Decision to Evaluate\n\n{input.options}"]
    if input.constraints:
        prompt_parts.append(f"## Constraints\n{input.constraints}")
    if input.priorities:
        prompt_parts.append(f"## Priorities\n{input.priorities}")
    if input.context:
        prompt_parts.append(f"## Context\n{input.context}")
    
    user_prompt = "\n\n".join(prompt_parts)
    
    return await _run_codex(
        system_prompt=EVALUATE_SYSTEM,
        user_prompt=user_prompt,
        project_dir=input.project_dir,
        focus_files=input.focus_files,
        model=input.model.value,
        reasoning_effort=input.reasoning_effort.value,
    )
```

---

## 3. New Tool: `codex_recap`

### Input Model

```python
class RecapInput(BaseModel):
    """Input for codex_recap — generate a decision record from a session."""
    session_summary: str = Field(description="Summary of the collaboration session: what was discussed, what each model suggested, what was tested, what was decided. Include attributed findings (CC vs Codex).")
    session_id: str = Field(default="", description="If a session document exists in .claudex/sessions/, provide the session_id to include it automatically.")
    project_dir: str = Field(default="", description="Project root directory.")
    model: CodexModel = Field(default=CodexModel.GPT_53_CODEX)
    reasoning_effort: ReasoningEffort = Field(default=ReasoningEffort.MEDIUM)
```

### Tool Function

```python
@mcp.tool(
    annotations=ToolAnnotations(title="Codex Recap — Decision Record", readOnlyHint=True)
)
async def codex_recap(input: RecapInput) -> str:
    """Generate a concise decision record from a collaboration session.
    Use after multi-round debugging, planning, or evaluation sessions."""
    
    session_content = ""
    if input.session_id and input.project_dir:
        session_path = Path(input.project_dir) / ".claudex" / "sessions" / f"{input.session_id}.md"
        if session_path.exists():
            session_content = f"\n\n## Full Session Log\n\n{session_path.read_text()}"
    
    user_prompt = f"## Session Summary\n\n{input.session_summary}{session_content}"
    
    result = await _run_codex(
        system_prompt=RECAP_SYSTEM,
        user_prompt=user_prompt,
        project_dir=input.project_dir,
        focus_files="",
        model=input.model.value,
        reasoning_effort=input.reasoning_effort.value,
    )
    
    # Save the recap as an artifact
    if input.project_dir and input.session_id:
        recap_dir = Path(input.project_dir) / ".claudex" / "recaps"
        recap_dir.mkdir(parents=True, exist_ok=True)
        recap_path = recap_dir / f"{input.session_id}.md"
        recap_path.write_text(result)
        result += f"\n\n📄 Decision record saved to: .claudex/recaps/{input.session_id}.md"
    
    return result
```

---

## 4. Session Document Management

### New Helper Functions

```python
import datetime
import re

def _get_session_path(project_dir: str, session_id: str) -> Path:
    """Get the path for a session document. Sanitizes session_id against path traversal."""
    safe_id = re.sub(r'[^a-zA-Z0-9_\-]', '_', session_id)
    session_dir = (Path(project_dir) / ".claudex" / "sessions").resolve()
    session_path = (session_dir / f"{safe_id}.md").resolve()
    if not session_path.is_relative_to(session_dir):
        raise ValueError(f"Invalid session_id: {session_id}")
    return session_path

def _init_session(project_dir: str, session_id: str) -> Path:
    """Create a new session document."""
    session_path = _get_session_path(project_dir, session_id)
    session_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    session_path.write_text(f"# Session: {session_id}\nStarted: {timestamp}\n\n")
    return session_path

def _append_to_session(project_dir: str, session_id: str, round_num: int, 
                        section: str, content: str) -> None:
    """Append a section to an existing session document."""
    session_path = _get_session_path(project_dir, session_id)
    if not session_path.exists():
        _init_session(project_dir, session_id)
    
    with open(session_path, "a") as f:
        if section == "cc_analysis":
            f.write(f"\n## Round {round_num}\n### CC Analysis\n{content}\n")
        elif section == "codex_response":
            f.write(f"\n### Codex Response\n{content}\n")
        elif section == "test_results":
            f.write(f"\n### Test Results\n{content}\n")
```

### Changes to `codex_collab`

Add `session_id` parameter to `CollaborateInput`:

```python
class CollaborateInput(BaseModel):
    # ... existing fields ...
    session_id: str = Field(default="", description="Optional session ID for iterative workflows. Creates/continues a session document in .claudex/sessions/ for shared memory across rounds.")
```

In the `codex_collab` function, after getting Codex's response:

```python
# After getting result from _run_codex:
if input.session_id and input.project_dir:
    # Determine round number from existing session
    session_path = _get_session_path(input.project_dir, input.session_id)
    if session_path.exists():
        content = session_path.read_text()
        round_num = content.count("## Round") + 1
    else:
        round_num = 1
    
    _append_to_session(input.project_dir, input.session_id, round_num,
                       "cc_analysis", input.cc_analysis)
    _append_to_session(input.project_dir, input.session_id, round_num,
                       "codex_response", result)
    
    result += f"\n\n📝 Session document updated: .claudex/sessions/{input.session_id}.md"
```

When `session_id` is provided and a session document exists, include it in the prompt to Codex:

```python
# In codex_collab, before calling _run_codex:
session_context = ""
if input.session_id and input.project_dir:
    session_path = _get_session_path(input.project_dir, input.session_id)
    if session_path.exists():
        session_context = f"\n\n## Previous Rounds\n\n{session_path.read_text()}"

user_prompt = f"## Problem\n{input.problem}\n\n## CC's Analysis\n{input.cc_analysis}{session_context}"
```

---

## 5. Error Handling Improvements

### In `_run_codex()`:

```python
# After subprocess execution, add structured error handling:

class CodexError(Exception):
    """Base class for Codex execution errors."""
    pass

class CodexTimeoutError(CodexError):
    pass

class CodexRateLimitError(CodexError):
    pass

class CodexAuthError(CodexError):
    pass

# In the try/except block:
try:
    process = await asyncio.create_subprocess_exec(...)
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
except asyncio.TimeoutError:
    process.kill()
    return (f"⏱️ **Codex timed out** after {timeout}s. "
            "This usually means the task was too complex for the current reasoning effort. "
            "Consider: (1) Use `gpt-5-codex-mini` for faster response, "
            "(2) Lower reasoning effort to `medium`, "
            "(3) Break the task into smaller pieces.\n\n"
            "**Proceeding with CC-only analysis.** Output was NOT dual-verified.")

# In stderr parsing:
if "rate limit" in stderr.lower() or "429" in stderr:
    return ("🚫 **Codex rate-limited.** Your ChatGPT subscription quota is exhausted for this window. "
            "Options: (1) Wait for the 5-hour window to reset, "
            "(2) Use `gpt-5-codex-mini` which has separate limits.\n\n"
            "**Proceeding with CC-only analysis.** Output was NOT dual-verified.")

if "not authenticated" in stderr.lower() or "401" in stderr:
    return ("🔑 **Codex authentication failed.** Run `codex login` in your terminal to re-authenticate.")

if not stdout.strip():
    return ("⚠️ **Codex returned an empty response.** The prompt may have been too vague. "
            "Try being more specific about the task, or provide focus_files to direct Codex's attention.\n\n"
            "**Proceeding with CC-only analysis.**")
```

---

## 6. Updated CLAUDE.md Architecture Section

Update the tool count and add new tools:

```markdown
## Architecture

**Single-file server** — all logic lives in `server/server.py` (~1200 lines). It's a FastMCP server (`FastMCP("codex")`) that exposes 8 tools:

| Tool | Purpose |
|------|---------|
| `codex_plan` | Codex generates its own independent plan (parallel planning) |
| `codex_review` | Codex critiques a provided plan (second opinion) |
| `codex_brainstorm` | Open-ended exploration of a problem |
| `codex_collab` | CC sends its analysis + request type, gets targeted suggestions |
| `codex_review_files` | Targeted code review of specific files |
| `codex_evaluate` | Tradeoff analysis between options (user decides) |
| `codex_recap` | Decision record generation from a session |
| `codex_ping` | Connectivity test |
```

---

## 7. Implementation Checklist

```
[ ] Add CODEBASE_FIRST_PREAMBLE constant
[ ] Replace PARALLEL_PLAN_SYSTEM with persona-enriched version
[ ] Replace SECOND_OPINION_SYSTEM with persona-enriched version
[ ] Replace BRAINSTORM_SYSTEM with persona-enriched version
[ ] Replace COLLABORATE_SYSTEM with dynamic persona builder (COLLAB_PERSONAS dict)
[ ] Add EVALUATE_SYSTEM constant
[ ] Add RECAP_SYSTEM constant
[ ] Add EvaluateInput model
[ ] Add codex_evaluate tool function
[ ] Add RecapInput model
[ ] Add codex_recap tool function
[ ] Add session management helpers (_get_session_path with sanitization, _init_session, _append_to_session)
[ ] Add session_id field to CollaborateInput
[ ] Wire session document read/write into codex_collab
[ ] Improve error handling in _run_codex with structured messages
[ ] Add REVIEW_FILES_SYSTEM persona-enriched prompt
[ ] Update CLAUDE.md with new tool count and descriptions
[ ] Add .claudex/sessions/ and .claudex/recaps/ to .gitignore handling
[ ] Rename skills/claudex/ → skills/codex/ to match skill name
[ ] Test all 8 tools end-to-end
[ ] Verify persona prompts produce meaningfully different Codex behaviors
```

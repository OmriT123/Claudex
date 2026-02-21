# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "mcp[cli]>=1.0.0",
#     "pydantic>=2.0.0",
# ]
# ///
"""
Claudex MCP Server
==================
An MCP server that integrates OpenAI Codex CLI with Claude Code for parallel
planning, collaboration, red-teaming, verification, and more. Codex runs in
read-only mode — it reads your codebase and provides an independent perspective
without modifying anything.

Two different AI architectures collaborate on the same codebase.
Claude Code synthesizes the best of both.

Artifact Scratchpad
-------------------
Codex can produce structured file artifacts (code snippets, tests, analysis
docs) embedded in its text output. The **server** extracts these from the
final-answer section and writes them to ``.claudex/run-<uuid>/``, isolated
per invocation. Codex itself never has write access — ``--sandbox read-only``
is enforced by the Codex CLI. The server is the sole gatekeeper.

Requires:
  - Codex CLI installed: npm i -g @openai/codex
  - Codex authenticated: codex login (ChatGPT subscription)
  - Python 3.10+
"""

import asyncio
import json
import os
import re
import shutil
import logging
import tempfile
import time
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, ConfigDict

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MODEL = "gpt-5.3-codex"
DEFAULT_REASONING_EFFORT = "high"
EXEC_TIMEOUT_SECONDS = 1200  # 20 min max per Codex call

DEFAULT_REASONING_SUMMARY = "detailed"
EFFORT_DOWNGRADE = {"xhigh": "high", "high": "medium"}
TOOL_TIMEOUTS = {
    "codex_review": 1200, "codex_plan": 1200, "codex_brainstorm": 900,
    "codex_collab": 1200, "codex_review_files": 300, "codex_evaluate": 1200,
    "codex_recap": 600, "codex_review_diff": 600,
}

FINAL_ANSWER_DELIMITER = "---FINAL-ANSWER---"
ARTIFACT_MAX_BYTES = 100 * 1024  # 100 KB per artifact
ARTIFACT_TAG_RE = re.compile(
    r'<claudex-artifact\s+([^>]+)>(.*?)</claudex-artifact>',
    re.DOTALL,
)
ARTIFACT_ATTR_RE = re.compile(r'(\w+)="([^"]*)"')
RUN_DIR_MAX_AGE_SECONDS = 3600  # 1 hour

# Session & retention constants
SESSION_MAX_BYTES = 32_000          # Max session context passed to Codex (OS ARG_MAX guard)
MAX_SESSION_ROUNDS = 4              # Terminate iterative sessions after this many rounds
SESSION_MAX_AGE_SECONDS = 86_400    # 24 hours

GIT_CONTEXT_MAX_LINES = 20         # Max lines from git diff --stat

ERROR_PREFIX = "Error: "           # CC pattern-matches on this — single source of truth

# ---------------------------------------------------------------------------
# Codebase-first preambles
# ---------------------------------------------------------------------------

CODEBASE_FIRST_PREAMBLE = """\
You have full read access to the project codebase. BEFORE addressing the task, \
explore the relevant source files to build your own understanding. Read imports, \
class hierarchies, and call sites — don't rely solely on the prompt description. \
Ground every observation in specific files and line-level evidence.

"""

CODEBASE_FIRST_PREAMBLE_LIGHT = """\
You have full read access to the project codebase. Read the files specified \
below and their surrounding context before responding.

"""

# ---------------------------------------------------------------------------
# Artifact instructions (appended to all system prompts)
# ---------------------------------------------------------------------------

ARTIFACT_INSTRUCTIONS = """

## Demonstrating Code & Evidence

When you want to show code, write tests, or provide evidence, wrap them in artifact blocks.
Place ALL artifact blocks AFTER the delimiter line `---FINAL-ANSWER---` at the end of your response.

Format:
<claudex-artifact filename="descriptive_name.py" language="python">
# Your code here
</claudex-artifact>

Use artifacts for:
- Code snippets showing your proposed implementation
- Test cases that prove your point
- Verification scripts or analysis
- Detailed explanations in markdown (.md files)

Rules:
- Place all artifacts after ---FINAL-ANSWER---
- Use descriptive filenames (no paths, just filenames like `proposed_handler.py`)
- Reference your artifacts in the text above the delimiter
- Keep artifacts focused and small
"""

# ---------------------------------------------------------------------------
# Enums (defined before system prompts so COLLAB_PERSONAS can use RequestType)
# ---------------------------------------------------------------------------


class ReasoningEffort(str, Enum):
    """Codex reasoning effort levels."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"                      # Extra High — max reasoning depth


class RequestType(str, Enum):
    """Collaboration request types for codex_collab."""
    FEATURE_SUGGESTION = "feature_suggestion"
    BUG_APPROACH = "bug_approach"
    CODE_CRITIQUE = "code_critique"
    RED_TEAM = "red_team"
    VERIFICATION = "verification"
    TESTING_STRATEGY = "testing_strategy"
    GENERAL = "general"


# ---------------------------------------------------------------------------
# System prompts for each mode
# ---------------------------------------------------------------------------

SECOND_OPINION_SYSTEM = CODEBASE_FIRST_PREAMBLE + """\
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
""" + ARTIFACT_INSTRUCTIONS

PARALLEL_PLAN_SYSTEM = CODEBASE_FIRST_PREAMBLE + """\
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
""" + ARTIFACT_INSTRUCTIONS

BRAINSTORM_SYSTEM = CODEBASE_FIRST_PREAMBLE + """\
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
""" + ARTIFACT_INSTRUCTIONS

# --- Dynamic collaboration system prompts ---

_COLLAB_BASE = """\
You are collaborating with another AI (Claude Code) to solve a problem together.
Claude has already analyzed the codebase and is sharing its findings with you.

Your job:
1. Read the relevant code yourself — don't just trust Claude's analysis.
2. Respond according to your assigned role below.
3. Be specific — reference files, functions, line-level details.
4. Where you AGREE with Claude's analysis, say so briefly.
5. Where you DISAGREE or see something Claude missed, explain in detail.
6. End with 2-3 concrete next steps.
"""

COLLAB_PERSONAS: dict[RequestType, str] = {
    RequestType.BUG_APPROACH: """
## Your Role: Diagnostic Specialist
You are a systematic debugger helping to identify root causes.

Behavioral instructions:
- Rank hypotheses by likelihood based on the symptoms described.
- For each hypothesis, specify an exact test (command, assertion, file check).
- Consider: race conditions, state corruption, timing issues, env differences.
- If the other AI's analysis points in a direction, evaluate it critically.
- Don't suggest "add logging" unless you specify exactly WHERE and WHAT to log.
""",
    RequestType.RED_TEAM: """
## Your Role: Adversarial Security Researcher
You are trying to BREAK this implementation. Assume everything can fail.

Behavioral instructions:
- Find failure modes: concurrency bugs, input edge cases, resource exhaustion.
- Identify attack vectors: injection, privilege escalation, data leakage.
- Check error handling: what happens when dependencies fail?
- Look for implicit trust: unvalidated inputs, unchecked return values.
- Rate each finding: Critical / High / Medium / Low with exploitation scenario.
""",
    RequestType.VERIFICATION: """
## Your Role: Formal Methods Engineer
You are independently verifying that this implementation is correct.

Behavioral instructions:
- Check logic: does the code do what it claims to do?
- Trace data flow: from input to output, what transformations happen?
- Verify error handling: are all error paths covered? Do they recover correctly?
- Check boundary conditions: empty inputs, max values, null/undefined, concurrent access.
- Validate invariants: what properties must ALWAYS hold? Do they?
""",
    RequestType.TESTING_STRATEGY: """
## Your Role: Test Architect
You are designing a comprehensive testing strategy.

Behavioral instructions:
- Categorize tests: unit, integration, end-to-end, performance, security.
- Identify critical paths that MUST have test coverage.
- Suggest boundary conditions and edge cases specific to this implementation.
- Propose test structure: fixtures, mocks, test data setup/teardown.
- Prioritize: which tests give the most confidence per effort invested?
""",
    RequestType.CODE_CRITIQUE: """
## Your Role: Senior Developer (Code Reviewer)
You are reviewing code for quality, maintainability, and correctness.

Behavioral instructions:
- Check: readability, naming, separation of concerns, DRY violations.
- Look for: anti-patterns, tech debt, performance bottlenecks, fragile code.
- Verify: error handling, input validation, resource cleanup.
- Assess: is this idiomatic for the language/framework? Does it follow project conventions?
- Prioritize your feedback — lead with what matters most.
""",
    RequestType.FEATURE_SUGGESTION: """
## Your Role: Product Engineer
You are suggesting features or implementation approaches.

Behavioral instructions:
- Evaluate feasibility given the existing codebase architecture.
- Estimate complexity: what needs to change, what can be reused?
- Consider user impact: what does this enable, what friction does it remove?
- Identify integration risks: what existing functionality could break?
- Suggest a phased approach if the feature is large.
""",
    RequestType.GENERAL: """
## Your Role: Collaborative Engineer
You are a knowledgeable colleague providing analysis and suggestions.

Behavioral instructions:
- Read the other AI's analysis carefully before responding.
- Add perspectives they may have missed.
- Be concrete: suggest specific code, files, approaches — not abstract advice.
- If you disagree with their analysis, explain why with evidence from the codebase.
""",
}


def _build_collaborate_system(request_type: RequestType) -> str:
    """Build a complete collaboration system prompt for the given request type."""
    persona = COLLAB_PERSONAS.get(request_type, COLLAB_PERSONAS[RequestType.GENERAL])
    return CODEBASE_FIRST_PREAMBLE + _COLLAB_BASE + persona + ARTIFACT_INSTRUCTIONS


REVIEW_FILES_SYSTEM_BASE = CODEBASE_FIRST_PREAMBLE_LIGHT + """\
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
"""
# Legacy alias for backward compatibility (text mode uses artifact instructions)
REVIEW_FILES_SYSTEM = REVIEW_FILES_SYSTEM_BASE + ARTIFACT_INSTRUCTIONS

EVALUATE_SYSTEM = CODEBASE_FIRST_PREAMBLE + """\
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
""" + ARTIFACT_INSTRUCTIONS

RECAP_SYSTEM = CODEBASE_FIRST_PREAMBLE_LIGHT + """\
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
""" + ARTIFACT_INSTRUCTIONS

REVIEW_DIFF_SYSTEM_BASE = CODEBASE_FIRST_PREAMBLE_LIGHT + """\
## Your Role: Diff Reviewer

You are reviewing a git diff — the actual changes about to be committed or recently made.
Focus on what CHANGED, not the entire file.

Behavioral instructions:
- Look for bugs INTRODUCED by the diff, not pre-existing issues.
- Check: logic errors, missing error handling, incomplete refactors, broken imports.
- Verify: are all changed code paths tested? Are there edge cases in the new logic?
- Assess: does the diff maintain consistency with surrounding code patterns?
- Flag: any security implications of the changes (new inputs, auth changes, etc.).

Structure your review as:
1. **Overview** — What this diff does (1-2 sentences)
2. **Critical Issues** — Bugs or risks introduced by these changes
3. **Warnings** — Things that might cause problems later
4. **Suggestions** — Improvements to the changed code
5. **Verdict** — Ship / Fix first / Needs discussion
"""
# Legacy alias for backward compatibility (text mode uses artifact instructions)
REVIEW_DIFF_SYSTEM = REVIEW_DIFF_SYSTEM_BASE + ARTIFACT_INSTRUCTIONS

STRUCTURED_OUTPUT_INSTRUCTIONS = """

## Structured Output Instructions

You MUST return valid JSON matching the provided schema. Key rules:
- Every finding MUST include `file_path` in `code_location` — use the actual file path from the codebase.
- Always include `line_range` in `code_location`. Set it to `{"start": N, "end": N}` when you can identify specific lines, or `null` for architectural or file-level findings.
- Put concrete code fix suggestions in the `suggestion` field as plain text (not markdown code blocks). Set to null if no fix applies.
- `confidence_score` is 0.0-1.0 representing how confident you are in this finding.
- `priority` is 0=critical, 1=high, 2=medium, 3=low.
- Do NOT include the `---FINAL-ANSWER---` delimiter or artifact blocks — return only the JSON object.
"""


def _build_review_system(base_prompt: str, structured: bool) -> str:
    """Build a review system prompt with either artifact or structured-output instructions.

    Args:
        base_prompt: The base system prompt text (without ARTIFACT_INSTRUCTIONS).
        structured: If True, append structured-output instructions. If False, append artifact instructions.
    """
    if structured:
        return base_prompt + STRUCTURED_OUTPUT_INSTRUCTIONS
    return base_prompt + ARTIFACT_INSTRUCTIONS

# Diff review constants
DIFF_MAX_BYTES = 50_000
DIFF_MAX_FILES = 50
GIT_CONTEXT_STAGED_DIFF_MAX = 5_000

# ---------------------------------------------------------------------------
# Structured output schemas for review tools
# ---------------------------------------------------------------------------

_FINDING_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "body": {"type": "string"},
        "severity": {"type": "string", "enum": ["critical", "warning", "suggestion", "positive"]},
        "priority": {"type": "integer"},
        "confidence_score": {"type": "number"},
        "category": {
            "type": "string",
            "enum": ["bug", "security", "performance", "error_handling",
                     "maintainability", "convention", "logic", "other"],
        },
        "code_location": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "line_range": {
                    "anyOf": [
                        {
                            "type": "object",
                            "properties": {"start": {"type": "integer"}, "end": {"type": "integer"}},
                            "required": ["start", "end"],
                            "additionalProperties": False,
                        },
                        {"type": "null"},
                    ],
                },
            },
            "required": ["file_path", "line_range"],
            "additionalProperties": False,
        },
        "suggestion": {"type": ["string", "null"]},
    },
    "required": ["title", "body", "severity", "priority", "confidence_score", "category", "code_location", "suggestion"],
    "additionalProperties": False,
}

REVIEW_FILES_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {"type": "array", "items": _FINDING_SCHEMA},
        "file_summaries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "summary": {"type": "string"},
                    "quality_assessment": {"type": "string"},
                },
                "required": ["file_path", "summary", "quality_assessment"],
                "additionalProperties": False,
            },
        },
        "overall_assessment": {"type": "string"},
        "overall_confidence_score": {"type": "number"},
    },
    "required": ["findings", "file_summaries", "overall_assessment", "overall_confidence_score"],
    "additionalProperties": False,
}

REVIEW_DIFF_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {"type": "array", "items": _FINDING_SCHEMA},
        "overview": {"type": "string"},
        "verdict": {"type": "string", "enum": ["ship", "fix_first", "needs_discussion"]},
        "overall_explanation": {"type": "string"},
        "overall_confidence_score": {"type": "number"},
    },
    "required": ["findings", "overview", "verdict", "overall_explanation", "overall_confidence_score"],
    "additionalProperties": False,
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("claudex")

# ---------------------------------------------------------------------------
# In-memory metrics (reset on server restart)
# ---------------------------------------------------------------------------

_metrics: dict[str, dict] = {}


def _record_metric(tool_name: str, *, success: bool, elapsed: float, timed_out: bool = False) -> None:
    """Record a metric for a tool invocation."""
    if not tool_name:
        return
    if tool_name not in _metrics:
        _metrics[tool_name] = {"calls": 0, "successes": 0, "timeouts": 0, "errors": 0, "total_elapsed": 0.0}
    m = _metrics[tool_name]
    m["calls"] += 1
    m["total_elapsed"] += elapsed
    if timed_out:
        m["timeouts"] += 1
    elif success:
        m["successes"] += 1
    else:
        m["errors"] += 1


def _get_metrics_summary() -> str:
    """Format metrics as a readable table for codex_status."""
    if not _metrics:
        return "No tool invocations recorded yet."
    lines = [f"{'Tool':<22s} {'Calls':>5s} {'OK':>4s} {'Err':>4s} {'T/O':>4s} {'Avg(s)':>7s}"]
    lines.append("-" * 50)
    for tool_name in sorted(_metrics):
        m = _metrics[tool_name]
        avg = m["total_elapsed"] / m["calls"] if m["calls"] else 0
        lines.append(
            f"{tool_name:<22s} {m['calls']:>5d} {m['successes']:>4d} "
            f"{m['errors']:>4d} {m['timeouts']:>4d} {avg:>7.1f}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

mcp = FastMCP("codex")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_codex_bin() -> str:
    """Resolve the codex binary path at call time (not module load).

    This matters because codex might be installed after the MCP server starts,
    or the PATH might differ between the server process and the user's shell.
    """
    path = shutil.which("codex")
    if path:
        return path
    # Common global npm install locations as fallback
    for candidate in [
        os.path.expanduser("~/.npm-global/bin/codex"),
        "/usr/local/bin/codex",
    ]:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return "codex"  # Let it fail with a clear FileNotFoundError


def _validate_project_dir(project_dir: Optional[str]) -> str:
    """Validate and return project directory. Returns cwd if None.

    Raises ValueError if the directory does not exist — this produces a
    distinct error from 'codex binary not found' (FileNotFoundError).
    """
    cwd = project_dir or os.getcwd()
    if not Path(cwd).is_dir():
        raise ValueError(f"Project directory does not exist: {cwd}")
    return cwd


def _safe_claudex_path(
    project_dir: str, subdir: str, filename: str
) -> Optional[Path]:
    """Validate and return a safe path under .claudex/{subdir}/.

    Sanitizes filename, rejects path traversal attempts and symlinks.
    Returns None if the path is invalid.
    """
    if "\x00" in filename:
        logger.warning("Null byte in filename rejected: %r", filename)
        return None
    # Sanitize: only allow alphanumeric, underscore, hyphen, dot
    safe_name = re.sub(r'[^a-zA-Z0-9_\-.]', '_', filename)
    if not safe_name or safe_name.startswith('.'):
        logger.warning("Invalid filename rejected: %r", filename)
        return None
    claudex_dir = Path(project_dir) / ".claudex"
    if claudex_dir.is_symlink():
        logger.warning("Symlink rejected at .claudex/ directory: %s", claudex_dir)
        return None
    base_dir = (claudex_dir / subdir).resolve()
    target = (base_dir / safe_name).resolve()
    if not target.is_relative_to(base_dir):
        logger.warning("Path traversal attempt rejected: %r -> %s", filename, target)
        return None
    if target.is_symlink():
        logger.warning("Symlink rejected at target: %s", target)
        return None
    return target


def _cleanup_old_run_dirs(claudex_dir: Path) -> None:
    """Best-effort removal of run directories older than RUN_DIR_MAX_AGE_SECONDS."""
    if not claudex_dir.is_dir():
        return
    cutoff = time.time() - RUN_DIR_MAX_AGE_SECONDS
    for entry in claudex_dir.iterdir():
        if entry.is_symlink():
            continue  # Never follow symlinks during cleanup
        if entry.is_dir() and entry.name.startswith("run-"):
            try:
                if entry.stat().st_mtime < cutoff:
                    shutil.rmtree(entry)
                    logger.info("Cleaned up stale run dir: %s", entry.name)
            except OSError:
                pass  # best-effort


def _cleanup_old_sessions(claudex_dir: Path) -> None:
    """Best-effort removal of session/recap files older than SESSION_MAX_AGE_SECONDS."""
    if not claudex_dir.is_dir():
        return
    for subdir_name in ("sessions", "recaps"):
        subdir = claudex_dir / subdir_name
        if not subdir.is_dir():
            continue
        cutoff = time.time() - SESSION_MAX_AGE_SECONDS
        for entry in subdir.iterdir():
            if entry.is_symlink():
                continue
            try:
                if entry.is_file() and entry.stat().st_mtime < cutoff:
                    entry.unlink()
                    logger.info("Cleaned up stale %s: %s", subdir_name, entry.name)
            except OSError:
                pass  # best-effort


def _prepare_run_dir(project_dir: str) -> Path:
    """Create an isolated per-run artifact directory under .claudex/.

    Returns the Path to the run directory (e.g. <project>/.claudex/run-<uuid>/).
    Also performs best-effort cleanup of stale run dirs and sessions, and warns
    if .claudex is not in .gitignore.
    """
    root = Path(project_dir)
    claudex_dir = root / ".claudex"

    # Best-effort cleanup of old runs and sessions
    _cleanup_old_run_dirs(claudex_dir)
    _cleanup_old_sessions(claudex_dir)

    # Warn if .claudex not in .gitignore
    gitignore = root / ".gitignore"
    if gitignore.is_file():
        try:
            content = gitignore.read_text()
            if ".claudex" not in content:
                logger.warning(
                    ".claudex is not in .gitignore — artifact dirs may be committed. "
                    "Add '.claudex' to your .gitignore."
                )
        except OSError:
            pass
    else:
        logger.warning(
            "No .gitignore found — .claudex artifact dirs may be committed."
        )

    run_dir = claudex_dir / f"run-{uuid.uuid4()}"
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def _extract_and_save_artifacts(
    output: str, run_dir: Path
) -> tuple[str, list[tuple[str, str]]]:
    """Parse artifact blocks from Codex output and write them to run_dir.

    Only parses artifacts in the final-answer section (after the
    FINAL_ANSWER_DELIMITER) to avoid picking up tags from reasoning traces
    or repo content echoed in the reasoning stream.

    Returns (cleaned_text, artifacts) where cleaned_text has the artifact
    blocks stripped and artifacts is a list of (filename, language) tuples.
    """
    # Split at the LAST occurrence of the delimiter to avoid false matches
    # from reasoning traces that echo the delimiter string
    idx = output.rfind(FINAL_ANSWER_DELIMITER)
    if idx == -1:
        # No delimiter found — no artifacts to parse
        return output, []

    preamble = output[:idx]
    answer_section = output[idx + len(FINAL_ANSWER_DELIMITER):]

    artifacts: list[tuple[str, str]] = []

    for match in ARTIFACT_TAG_RE.finditer(answer_section):
        attrs = dict(ARTIFACT_ATTR_RE.findall(match.group(1)))
        filename = attrs.get("filename", "").strip()
        language = attrs.get("language", "").strip()
        content = match.group(2)

        if not filename or not language:
            logger.warning("Artifact rejected — missing filename or language")
            continue

        # --- Filename validation ---
        # Reject empty, null-byte, or path-separator names
        if "\x00" in filename or "/" in filename or "\\" in filename:
            logger.warning("Artifact rejected — invalid filename: %r", filename)
            continue

        # Reject overly long filenames (filesystem NAME_MAX)
        if len(filename.encode("utf-8")) > 255:
            logger.warning(
                "Artifact rejected — filename too long: %r", filename
            )
            continue

        # Reject hidden files or path components with ..
        fname_path = Path(filename)
        if any(
            part.startswith(".") or part == ".." for part in fname_path.parts
        ):
            logger.warning(
                "Artifact rejected — suspicious path component: %r", filename
            )
            continue

        target = (run_dir / filename).resolve()
        if not target.is_relative_to(run_dir.resolve()):
            logger.warning(
                "Artifact rejected — path traversal attempt: %r", filename
            )
            continue

        # Reject symlinks at the target location (defense-in-depth;
        # the UUID-based run dir + exclusive create already mitigate this)
        if os.path.islink(target):
            logger.warning(
                "Artifact rejected — symlink exists at target: %r", filename
            )
            continue

        # Size guard
        content_bytes = content.encode("utf-8")
        if len(content_bytes) > ARTIFACT_MAX_BYTES:
            logger.warning(
                "Artifact skipped — exceeds %d bytes: %r (%d bytes)",
                ARTIFACT_MAX_BYTES,
                filename,
                len(content_bytes),
            )
            continue

        # Write with exclusive create to avoid overwriting via symlink race
        try:
            with open(target, "x", encoding="utf-8") as f:
                f.write(content)
            artifacts.append((filename, language))
            logger.info("Artifact saved: %s (%s)", filename, language)
        except FileExistsError:
            logger.warning(
                "Artifact rejected — file already exists: %r", filename
            )
        except OSError as exc:
            logger.warning("Artifact write failed for %r: %s", filename, exc)

    # Strip artifact blocks from the answer section
    cleaned_answer = ARTIFACT_TAG_RE.sub("", answer_section).strip()

    # Reconstruct output: preamble + cleaned answer
    # The delimiter itself is stripped — it's internal to the Codex protocol
    cleaned_text = preamble.strip()
    if cleaned_answer:
        cleaned_text = cleaned_text + "\n\n" + cleaned_answer if cleaned_text else cleaned_answer

    return cleaned_text, artifacts


# --- Session management ---


def _auto_session_id(problem: str) -> str:
    """Generate a descriptive session ID from the problem statement.

    Slugifies the first 50 chars + appends a short UUID suffix for uniqueness.
    """
    slug = re.sub(r'[^a-zA-Z0-9]+', '-', problem[:50]).strip('-').lower()
    suffix = uuid.uuid4().hex[:6]
    return f"{slug}-{suffix}" if slug else f"session-{suffix}"


def _init_session(session_path: Path, session_id: str) -> None:
    """Create a new session document with structured header."""
    session_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    session_path.write_text(
        f"# Session: {session_id}\n"
        f"Started: {timestamp}\n"
        f"<!-- claudex:rounds=0 -->\n\n"
    )


def _append_to_session(
    session_path: Path, round_num: int, cc_analysis: str, codex_response: str
) -> None:
    """Append a round to an existing session document and update round count."""
    content = session_path.read_text()
    # Update round count in structured header
    content = re.sub(r'<!-- claudex:rounds=\d+ -->', f'<!-- claudex:rounds={round_num} -->', content)
    content += (
        f"\n---\n\n"
        f"## Round {round_num}\n\n"
        f"### CC Analysis\n{cc_analysis}\n\n"
        f"### Codex Response\n{codex_response}\n"
    )
    session_path.write_text(content)


def _read_session_rounds(session_path: Path) -> int:
    """Parse round count from structured header line."""
    if not session_path.exists():
        return 0
    content = session_path.read_text()
    match = re.search(r'<!-- claudex:rounds=(\d+) -->', content)
    return int(match.group(1)) if match else 0


def _get_truncated_session(
    session_path: Path, max_bytes: int = SESSION_MAX_BYTES
) -> str:
    """Read session content, truncating oldest rounds first if over max_bytes."""
    if not session_path.exists():
        return ""
    content = session_path.read_text()
    if len(content.encode("utf-8")) <= max_bytes:
        return content
    # Split into header + rounds and drop oldest rounds first.
    # _append_to_session writes "\n---\n\n## Round N\n\n..." so we split on
    # the "---" + round heading boundary to preserve delimiters on reassembly.
    parts = re.split(r'(\n---\n\n## Round \d+)', content)
    # parts[0] is header, then alternating [delimiter+heading, content] pairs
    header = parts[0]
    rounds = []
    for i in range(1, len(parts), 2):
        if i + 1 < len(parts):
            rounds.append(parts[i] + parts[i + 1])
        else:
            rounds.append(parts[i])
    # Drop oldest rounds until within limit
    while rounds and len((header + "".join(rounds)).encode("utf-8")) > max_bytes:
        rounds.pop(0)
    if not rounds:
        return header[:max_bytes]
    return header + "[Earlier rounds truncated]\n" + "".join(rounds)


def _chain_session_id(session_id: str) -> str:
    """Chain a session ID: 'my-session' -> 'my-session-p2' -> 'my-session-p3'."""
    match = re.match(r'^(.*)-p(\d+)$', session_id)
    if match:
        base, num = match.group(1), int(match.group(2))
        return f"{base}-p{num + 1}"
    return f"{session_id}-p2"


# --- File list normalization ---


def _normalize_file_list(files_csv: str, project_dir: str) -> list[str]:
    """Parse comma-separated file paths, resolve relative to project_dir.

    Drops non-existent paths (logs warning), rejects paths outside project root.
    Returns empty list if all invalid.
    """
    if not files_csv or not files_csv.strip():
        return []
    root = Path(project_dir).resolve()
    result = []
    for raw in files_csv.split(","):
        raw = raw.strip()
        if not raw:
            continue
        path = (root / raw).resolve()
        if not path.is_relative_to(root):
            logger.warning("File path outside project root rejected: %s", raw)
            continue
        if not path.exists():
            logger.warning("File path does not exist, skipping: %s", raw)
            continue
        # Return relative path from project root
        result.append(str(path.relative_to(root)))
    return result


# --- Git context ---


async def _get_git_context(project_dir: str) -> Optional[str]:
    """Get lightweight git state (branch + diff stat, max 20 lines).

    Returns None if not a git repo or git fails. Graceful — never raises.
    """
    try:
        # Check if it's a git repo
        proc = await asyncio.create_subprocess_exec(
            "git", "rev-parse", "--is-inside-work-tree",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=project_dir,
        )
        await asyncio.wait_for(proc.communicate(), timeout=2)
        if proc.returncode != 0:
            return None

        # Get branch name
        proc = await asyncio.create_subprocess_exec(
            "git", "branch", "--show-current",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=project_dir,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=2)
        branch = stdout.decode().strip() or "detached HEAD"

        # Get diff stat
        proc = await asyncio.create_subprocess_exec(
            "git", "diff", "--stat", "HEAD",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=project_dir,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=2)
        diff_stat = stdout.decode().strip()

        lines = [f"## Recent Changes\nBranch: `{branch}`"]
        if diff_stat:
            diff_lines = diff_stat.split("\n")
            if len(diff_lines) > GIT_CONTEXT_MAX_LINES:
                remaining = len(diff_lines) - GIT_CONTEXT_MAX_LINES
                diff_lines = diff_lines[:GIT_CONTEXT_MAX_LINES]
                diff_lines.append(f"... ({remaining} more files)")
            lines.append("```\n" + "\n".join(diff_lines) + "\n```")

        # Recent commit messages
        proc = await asyncio.create_subprocess_exec(
            "git", "log", "--oneline", "-5",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=project_dir,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=2)
        recent_commits = stdout.decode().strip()
        if recent_commits:
            lines.append(f"Recent commits:\n```\n{recent_commits}\n```")

        # Staged changes stat
        proc = await asyncio.create_subprocess_exec(
            "git", "diff", "--staged", "--stat",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=project_dir,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=2)
        staged_stat = stdout.decode().strip()
        if staged_stat:
            # Cap staged stat output
            if len(staged_stat.encode("utf-8")) > GIT_CONTEXT_STAGED_DIFF_MAX:
                staged_stat = staged_stat.encode("utf-8")[:GIT_CONTEXT_STAGED_DIFF_MAX].decode("utf-8", errors="ignore") + "\n... [truncated]"
            lines.append(f"Staged changes:\n```\n{staged_stat}\n```")

        return "\n".join(lines)
    except (asyncio.TimeoutError, OSError):
        return None


# ---------------------------------------------------------------------------
# Input Models
# ---------------------------------------------------------------------------


class SecondOpinionInput(BaseModel):
    """Input for getting Codex's second opinion on a plan or approach."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    plan: str = Field(
        ...,
        description=(
            "The plan, approach, or implementation strategy to get a second opinion on. "
            "Include what you're trying to accomplish and how you intend to do it."
        ),
        min_length=10,
    )
    user_prompt: Optional[str] = Field(
        default=None,
        description=(
            "The user's original request, verbatim. Ensures Codex responds "
            "to user intent, not just CC's interpretation."
        ),
    )
    context: Optional[str] = Field(
        default=None,
        description=(
            "Additional context: constraints, tech stack details, business requirements, "
            "or anything Codex should know beyond what's in the repo."
        ),
    )
    focus_files: Optional[str] = Field(
        default=None,
        description=(
            "Comma-separated list of files/dirs Codex should pay special attention to "
            "(e.g. 'src/auth/,src/models/user.py'). Codex has full repo access regardless."
        ),
    )
    project_dir: Optional[str] = Field(
        default=None,
        description=(
            "Absolute path to the project directory. Defaults to the current working "
            "directory. Codex reads the repo from this location."
        ),
    )
    reasoning_effort: ReasoningEffort = Field(
        default=ReasoningEffort.HIGH,
        description="How deeply Codex should reason. Use 'xhigh' for maximum depth (slower).",
    )
    model: Optional[str] = Field(
        default=None,
        description="Override the default Codex model for this call.",
        pattern=r'^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$',
    )
    timeout_seconds: Optional[int] = Field(
        default=None,
        ge=30,
        le=1800,
        description="Override timeout in seconds (30-1800). Defaults to per-tool setting.",
    )
    reasoning_summary: Optional[str] = Field(
        default=None,
        description="Override reasoning summary mode (e.g. 'detailed', 'concise', 'none').",
        pattern=r'^[a-z]{2,20}$',
    )


class ParallelPlanInput(BaseModel):
    """Input for having Codex generate its own independent plan for a task."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    task: str = Field(
        ...,
        description=(
            "The task or feature to plan. Describe WHAT needs to happen, not HOW. "
            "Codex will read the codebase and generate its own approach independently."
        ),
        min_length=10,
    )
    user_prompt: Optional[str] = Field(
        default=None,
        description=(
            "The user's original request, verbatim. Pass this EXACTLY as the user "
            "typed it — do not rephrase, interpret, or add your own framing. "
            "This ensures Codex forms its own independent understanding."
        ),
    )
    constraints: Optional[str] = Field(
        default=None,
        description=(
            "Hard constraints Codex must respect: deadlines, tech requirements, "
            "backward compatibility, performance targets, etc."
        ),
    )
    focus_files: Optional[str] = Field(
        default=None,
        description=(
            "Comma-separated files/dirs most relevant to this task "
            "(e.g. 'src/auth/,src/models/'). Helps Codex focus."
        ),
    )
    project_dir: Optional[str] = Field(
        default=None,
        description="Absolute path to the project directory. Defaults to cwd.",
    )
    reasoning_effort: ReasoningEffort = Field(
        default=ReasoningEffort.HIGH,
        description="How deeply Codex should reason. Use 'xhigh' for maximum depth (slower).",
    )
    model: Optional[str] = Field(
        default=None,
        description="Override the default Codex model for this call.",
        pattern=r'^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$',
    )
    timeout_seconds: Optional[int] = Field(
        default=None,
        ge=30,
        le=1800,
        description="Override timeout in seconds (30-1800). Defaults to per-tool setting.",
    )
    reasoning_summary: Optional[str] = Field(
        default=None,
        description="Override reasoning summary mode (e.g. 'detailed', 'concise', 'none').",
        pattern=r'^[a-z]{2,20}$',
    )


class BrainstormInput(BaseModel):
    """Input for brainstorming with Codex about a problem or feature."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    topic: str = Field(
        ...,
        description=(
            "The problem, feature, or question to brainstorm about. Be specific about "
            "what you're trying to solve."
        ),
        min_length=10,
    )
    user_prompt: Optional[str] = Field(
        default=None,
        description=(
            "The user's original request, verbatim. Pass this EXACTLY as the user "
            "typed it — do not rephrase, interpret, or add your own framing. "
            "This ensures Codex forms its own independent understanding."
        ),
    )
    context: Optional[str] = Field(
        default=None,
        description="Additional context, constraints, or prior art to consider.",
    )
    project_dir: Optional[str] = Field(
        default=None,
        description="Absolute path to the project directory. Defaults to cwd.",
    )
    reasoning_effort: ReasoningEffort = Field(
        default=ReasoningEffort.HIGH,
        description="How deeply Codex should reason. Use 'xhigh' for maximum depth (slower).",
    )
    model: Optional[str] = Field(
        default=None,
        description="Override the default Codex model for this call.",
        pattern=r'^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$',
    )
    timeout_seconds: Optional[int] = Field(
        default=None,
        ge=30,
        le=1800,
        description="Override timeout in seconds (30-1800). Defaults to per-tool setting.",
    )
    reasoning_summary: Optional[str] = Field(
        default=None,
        description="Override reasoning summary mode (e.g. 'detailed', 'concise', 'none').",
        pattern=r'^[a-z]{2,20}$',
    )


class CollaborateInput(BaseModel):
    """Input for interactive CC+Codex collaboration."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    problem: str = Field(
        ...,
        description="The problem CC needs help with.",
        min_length=10,
    )
    cc_analysis: str = Field(
        ...,
        description="What CC has already figured out — its findings and current thinking.",
        min_length=10,
    )
    request_type: RequestType = Field(
        default=RequestType.GENERAL,
        description=(
            "Type of collaboration needed: 'feature_suggestion', 'bug_approach', "
            "'code_critique', 'red_team', 'verification', 'testing_strategy', or 'general'."
        ),
    )
    user_prompt: Optional[str] = Field(
        default=None,
        description=(
            "The user's original request, verbatim. Ensures Codex responds "
            "to user intent, not just CC's interpretation."
        ),
    )
    session_id: Optional[str] = Field(
        default=None,
        description=(
            "Session ID for iterative workflows. Creates/continues a session "
            "document in .claudex/sessions/ for shared memory across rounds. "
            "Pass 'auto' to auto-generate a descriptive ID from the problem."
        ),
    )
    files_involved: Optional[str] = Field(
        default=None,
        description="Comma-separated list of files relevant to this problem.",
    )
    project_dir: Optional[str] = Field(
        default=None,
        description="Absolute path to the project directory. Defaults to cwd.",
    )
    reasoning_effort: ReasoningEffort = Field(
        default=ReasoningEffort.HIGH,
        description="How deeply Codex should reason. Use 'xhigh' for maximum depth (slower).",
    )
    model: Optional[str] = Field(
        default=None,
        description="Override the default Codex model for this call.",
        pattern=r'^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$',
    )
    timeout_seconds: Optional[int] = Field(
        default=None,
        ge=30,
        le=1800,
        description="Override timeout in seconds (30-1800). Defaults to per-tool setting.",
    )
    reasoning_summary: Optional[str] = Field(
        default=None,
        description="Override reasoning summary mode (e.g. 'detailed', 'concise', 'none').",
        pattern=r'^[a-z]{2,20}$',
    )


class QuickReviewInput(BaseModel):
    """Input for a quick, focused code review from Codex."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    files: str = Field(
        ...,
        description=(
            "Comma-separated list of files to review "
            "(e.g. 'src/auth.py,src/routes/login.py')."
        ),
        min_length=1,
    )
    user_prompt: Optional[str] = Field(
        default=None,
        description=(
            "The user's original request, verbatim. Ensures Codex responds "
            "to user intent, not just CC's interpretation."
        ),
    )
    focus: Optional[str] = Field(
        default=None,
        description=(
            "What to focus the review on: 'security', 'performance', 'correctness', "
            "'maintainability', or a custom focus area."
        ),
    )
    structured_output: bool = Field(
        default=True,
        description="Return structured JSON findings. Set False for free-text output.",
    )
    project_dir: Optional[str] = Field(
        default=None,
        description="Absolute path to the project directory. Defaults to cwd.",
    )
    reasoning_effort: ReasoningEffort = Field(
        default=ReasoningEffort.MEDIUM,
        description="Reasoning depth. 'medium' is usually fine for reviews.",
    )
    model: Optional[str] = Field(
        default=None,
        description="Override the default Codex model for this call.",
        pattern=r'^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$',
    )
    timeout_seconds: Optional[int] = Field(
        default=None,
        ge=30,
        le=1800,
        description="Override timeout in seconds (30-1800). Defaults to per-tool setting.",
    )
    reasoning_summary: Optional[str] = Field(
        default=None,
        description="Override reasoning summary mode (e.g. 'detailed', 'concise', 'none').",
        pattern=r'^[a-z]{2,20}$',
    )


class EvaluateInput(BaseModel):
    """Input for codex_evaluate — tradeoff analysis for user decision-making."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    options: str = Field(
        ...,
        description=(
            "The options being evaluated. Describe each approach with enough detail "
            "for analysis. Separate with clear labels (Option A, Option B, etc.)."
        ),
        min_length=20,
    )
    constraints: Optional[str] = Field(
        default=None,
        description="Non-negotiable requirements that any option must satisfy.",
    )
    priorities: Optional[str] = Field(
        default=None,
        description=(
            "What the user is optimizing for (performance, maintainability, "
            "speed to ship, cost, etc.)."
        ),
    )
    context: Optional[str] = Field(
        default=None,
        description="Additional context about why this decision matters or what's driving it.",
    )
    focus_files: Optional[str] = Field(
        default=None,
        description="Comma-separated file/directory paths for Codex to read for codebase context.",
    )
    project_dir: Optional[str] = Field(
        default=None,
        description="Absolute path to the project directory. Defaults to cwd.",
    )
    reasoning_effort: ReasoningEffort = Field(
        default=ReasoningEffort.HIGH,
        description="How deeply Codex should reason.",
    )
    model: Optional[str] = Field(
        default=None,
        description="Override the default Codex model for this call.",
        pattern=r'^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$',
    )
    timeout_seconds: Optional[int] = Field(
        default=None,
        ge=30,
        le=1800,
        description="Override timeout in seconds (30-1800). Defaults to per-tool setting.",
    )
    reasoning_summary: Optional[str] = Field(
        default=None,
        description="Override reasoning summary mode (e.g. 'detailed', 'concise', 'none').",
        pattern=r'^[a-z]{2,20}$',
    )


class RecapInput(BaseModel):
    """Input for codex_recap — generate a decision record from a session."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    session_id: str = Field(
        ...,
        description="Session ID corresponding to a document in .claudex/sessions/.",
        min_length=1,
    )
    additional_context: Optional[str] = Field(
        default=None,
        description="Additional context about what was decided or any final outcomes.",
    )
    project_dir: Optional[str] = Field(
        default=None,
        description="Absolute path to the project directory. Defaults to cwd.",
    )
    reasoning_effort: ReasoningEffort = Field(
        default=ReasoningEffort.MEDIUM,
        description="Reasoning depth. 'medium' is usually fine for recaps.",
    )
    model: Optional[str] = Field(
        default=None,
        description="Override the default Codex model for this call.",
        pattern=r'^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$',
    )
    timeout_seconds: Optional[int] = Field(
        default=None,
        ge=30,
        le=1800,
        description="Override timeout in seconds (30-1800). Defaults to per-tool setting.",
    )
    reasoning_summary: Optional[str] = Field(
        default=None,
        description="Override reasoning summary mode (e.g. 'detailed', 'concise', 'none').",
        pattern=r'^[a-z]{2,20}$',
    )


class ReviewDiffInput(BaseModel):
    """Input for codex_review_diff — review git diff changes."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    focus: Optional[str] = Field(
        default=None,
        description="What to focus the review on: 'security', 'performance', 'correctness', or a custom area.",
    )
    staged: bool = Field(
        default=False,
        description="If True, review only staged changes (git diff --staged). If False, review all unstaged changes.",
    )
    structured_output: bool = Field(
        default=True,
        description="Return structured JSON findings. Set False for free-text output.",
    )
    context: Optional[str] = Field(
        default=None,
        description="Additional context about what these changes are for.",
    )
    user_prompt: Optional[str] = Field(
        default=None,
        description="The user's original request, verbatim.",
    )
    project_dir: Optional[str] = Field(
        default=None,
        description="Absolute path to the project directory. Defaults to cwd.",
    )
    reasoning_effort: ReasoningEffort = Field(
        default=ReasoningEffort.MEDIUM,
        description="Reasoning depth. 'medium' is usually fine for diff reviews.",
    )
    model: Optional[str] = Field(
        default=None,
        description="Override the default Codex model.",
        pattern=r'^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$',
    )
    timeout_seconds: Optional[int] = Field(default=None, ge=30, le=1800, description="Override timeout.")
    reasoning_summary: Optional[str] = Field(
        default=None,
        description="Override reasoning summary mode.",
        pattern=r'^[a-z]{2,20}$',
    )


# ---------------------------------------------------------------------------
# Version check (runs once per server lifetime, warning displayed once)
# ---------------------------------------------------------------------------

_version_cache: dict = {"warning": "", "resolved": False, "consumed": False,
                        "lock": None}


async def _check_codex_version(*, consume: bool = False) -> str:
    """Check if Codex CLI is up to date. Returns warning string or empty.

    The npm lookup runs at most once per server lifetime. Results are cached.

    Args:
        consume: If True, the warning is returned on the first call only —
                 subsequent consume=True calls return empty. Used by _run_codex
                 so the warning appears on the first tool response only.
                 If False (default), always returns the cached warning. Used by
                 codex_status so diagnostics always reflect outdated status.
    """
    # Lazy-init lock (no event loop at module load time)
    if _version_cache["lock"] is None:
        _version_cache["lock"] = asyncio.Lock()

    async with _version_cache["lock"]:
        if not _version_cache["resolved"]:
            # First call (or retry after previous failure) — run the check
            try:
                codex_bin = _find_codex_bin()

                # Get installed version
                proc = await asyncio.create_subprocess_exec(
                    codex_bin, "--version",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(
                    proc.communicate(), timeout=3,
                )
                if proc.returncode != 0:
                    return ""
                installed = stdout.decode().strip()
                installed_ver = installed.split()[-1] if installed else ""

                # Get latest version from npm registry
                proc = await asyncio.create_subprocess_exec(
                    "npm", "view", "@openai/codex", "version",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(
                    proc.communicate(), timeout=5,
                )
                if proc.returncode != 0:
                    return ""
                latest_ver = stdout.decode().strip()

                if installed_ver and latest_ver and installed_ver != latest_ver:
                    inst_parts = tuple(
                        int(x) for x in installed_ver.split('.')
                    )
                    lat_parts = tuple(
                        int(x) for x in latest_ver.split('.')
                    )
                    if inst_parts < lat_parts:
                        _version_cache["warning"] = (
                            f"\u26a0 Codex CLI v{installed_ver} is outdated "
                            f"(latest: v{latest_ver}).\n"
                            f"  Run: npm i -g @openai/codex\n"
                        )

                # Mark resolved only on success — failures will retry
                _version_cache["resolved"] = True

            except (asyncio.TimeoutError, OSError, FileNotFoundError,
                    ValueError):
                # Don't set resolved — next call will retry
                return ""

        # Return logic: consume=True returns warning once, then empty
        if consume:
            if _version_cache["consumed"]:
                return ""
            _version_cache["consumed"] = True
            return _version_cache["warning"]

        return _version_cache["warning"]


class StatusInput(BaseModel):
    """Input for codex_status — lightweight diagnostics (no Codex call)."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    project_dir: Optional[str] = Field(
        default=None,
        description="Absolute path to the project directory. Defaults to cwd.",
    )


# ---------------------------------------------------------------------------
# Core execution helper
# ---------------------------------------------------------------------------


async def _run_codex_once(
    prompt: str,
    *,
    project_dir: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    reasoning_effort: str = DEFAULT_REASONING_EFFORT,
    reasoning_summary: str = DEFAULT_REASONING_SUMMARY,
    timeout: int = EXEC_TIMEOUT_SECONDS,
    tool_name: str = "",
    output_schema: Optional[dict] = None,
) -> str:
    """
    Run Codex CLI in non-interactive, read-only mode and return the output.

    This is the low-level runner. Use ``_run_codex`` for the wrapper with
    metrics, auto-retry on timeout, and version check.

    When ``output_schema`` is provided, Codex is instructed to return structured
    JSON matching the schema via ``--output-schema``. Artifact extraction is
    skipped in this mode (structured output replaces artifacts for review tools).

    Return type is always ``str`` — even when output_schema is set, the JSON is
    returned as a string. Parsing happens in the tool functions.

    Uses ``codex`` with:
      --sandbox read-only         -> Codex can read the repo but cannot edit or run commands
      --skip-git-repo-check       -> Works in non-git dirs too
      --color never               -> Prevents ANSI escape codes in captured output
      -m <model>                  -> Model selection
      -c model_reasoning_effort   -> Reasoning depth
      -c model_reasoning_summary  -> Gets chain-of-thought reasoning
      --cd <dir>                  -> Working directory (project root)
      --output-schema <file>      -> (optional) Constrain output to JSON schema
    """
    # Validate project directory (distinct error from codex binary not found)
    try:
        cwd = _validate_project_dir(project_dir)
    except ValueError as e:
        return f"{ERROR_PREFIX}{e}"

    codex_bin = _find_codex_bin()

    cmd = [
        codex_bin, "exec",
        "--sandbox", "read-only",
        "--skip-git-repo-check",
        "--color", "never",
        "-m", model,
        "-c", f"model_reasoning_effort={reasoning_effort}",
        "-c", f"model_reasoning_summary={reasoning_summary}",
        "--cd", cwd,
    ]

    # Write schema to temp file if structured output requested
    schema_tmp_path = None
    if output_schema is not None:
        try:
            schema_fd, schema_tmp_path = tempfile.mkstemp(suffix=".json")
            with os.fdopen(schema_fd, "w", encoding="utf-8") as f:
                json.dump(output_schema, f)
            cmd.extend(["--output-schema", schema_tmp_path])
        except (OSError, TypeError, ValueError) as exc:
            logger.warning("Failed to write schema temp file: %s", exc)
            schema_tmp_path = None
            output_schema = None  # Disable structured path entirely

    cmd.append(prompt)

    logger.info(
        "Running Codex: model=%s effort=%s summary=%s tool=%s cwd=%s schema=%s",
        model, reasoning_effort, reasoning_summary, tool_name, cwd,
        "yes" if output_schema else "no",
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass  # Process already exited
        try:
            await asyncio.wait_for(proc.communicate(), timeout=5)
        except (asyncio.TimeoutError, ProcessLookupError, OSError):
            pass  # Best-effort pipe drain
        return (
            f"{ERROR_PREFIX}Codex timed out after {timeout}s. "
            "Try: (1) focus_files to narrow scope, (2) simpler prompt, "
            "(3) lower reasoning_effort to 'medium'."
        )
    except FileNotFoundError:
        return (
            f"{ERROR_PREFIX}Codex CLI not found. Install it with:\n"
            "  npm i -g @openai/codex\n"
            "Then authenticate:\n"
            "  codex login"
        )
    except OSError as exc:
        return f"{ERROR_PREFIX}Failed to start Codex: {exc}"
    finally:
        # Clean up schema temp file
        if schema_tmp_path:
            try:
                os.unlink(schema_tmp_path)
            except OSError:
                pass

    if proc.returncode != 0:
        err_msg = stderr.decode(errors="replace").strip()
        if "not authenticated" in err_msg.lower() or "login" in err_msg.lower():
            return (
                f"{ERROR_PREFIX}Codex is not authenticated. Run:\n"
                "  codex login\n"
                "to sign in with your ChatGPT subscription."
            )
        if "rate limit" in err_msg.lower() or "429" in err_msg:
            return (
                f"{ERROR_PREFIX}Codex rate limit reached. Your ChatGPT subscription has "
                "per-window message limits (resets every ~5 hours). "
                "Try again later or use lower reasoning_effort."
            )
        return f"{ERROR_PREFIX}Codex exited with code {proc.returncode}.\nStderr: {err_msg}"

    output = stdout.decode(errors="replace").strip()
    if not output:
        fallback = stderr.decode(errors="replace").strip()
        if fallback:
            return f"{ERROR_PREFIX}Codex returned no stdout (exit 0).\nStderr: {fallback}"
        return (
            f"{ERROR_PREFIX}Codex returned no output. "
            "Try being more specific or provide focus_files to direct attention."
        )

    # When using structured output, skip artifact extraction — return raw JSON string
    if output_schema is not None:
        return output

    # --- Artifact extraction ---
    # Create a per-run directory and extract any artifact blocks from the
    # final-answer section. Codex stays read-only; the *server* writes.
    try:
        run_dir = _prepare_run_dir(cwd)
        cleaned_output, artifacts = _extract_and_save_artifacts(output, run_dir)

        if artifacts:
            # Build relative path from project root for readability
            rel_run = run_dir.relative_to(Path(cwd))
            lines = [
                "\n\n---",
                "## Artifacts Created",
                "The following files were written for your reference:",
            ]
            for fname, lang in artifacts:
                lines.append(f"- `{rel_run / fname}` ({lang})")
            lines.append(
                "\nRead these files to see the code/evidence referenced above."
            )
            cleaned_output += "\n".join(lines)
        else:
            # No artifacts — clean up the empty run dir
            shutil.rmtree(run_dir, ignore_errors=True)
    except OSError as exc:
        logger.warning("Artifact directory setup failed: %s", exc)
        cleaned_output = output  # Fall back to raw output

    return cleaned_output


async def _run_codex(
    prompt: str,
    *,
    project_dir: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    reasoning_effort: str = DEFAULT_REASONING_EFFORT,
    reasoning_summary: str = DEFAULT_REASONING_SUMMARY,
    timeout: int = EXEC_TIMEOUT_SECONDS,
    tool_name: str = "",
    output_schema: Optional[dict] = None,
) -> str:
    """
    High-level Codex runner with metrics, auto-retry on timeout, and version check.

    Wraps ``_run_codex_once`` and adds:
      - Metrics recording (success/timeout/error + elapsed time)
      - Auto-downgrade on timeout (xhigh->high, high->medium) with one retry
      - Version check warning on first invocation

    When ``output_schema`` is set, ALL text mutations (version warning, retry note,
    metadata footer) are suppressed to keep the JSON output clean. Metrics recording
    still happens.
    """
    start_time = time.monotonic()

    result = await _run_codex_once(
        prompt,
        project_dir=project_dir,
        model=model,
        reasoning_effort=reasoning_effort,
        reasoning_summary=reasoning_summary,
        timeout=timeout,
        tool_name=tool_name,
        output_schema=output_schema,
    )

    elapsed = time.monotonic() - start_time
    timed_out = result.startswith(ERROR_PREFIX) and "timed out" in result
    success = not result.startswith(ERROR_PREFIX)

    _record_metric(tool_name, success=success, elapsed=elapsed, timed_out=timed_out)

    # Auto-retry with downgraded effort on timeout
    if timed_out and reasoning_effort in EFFORT_DOWNGRADE:
        downgraded = EFFORT_DOWNGRADE[reasoning_effort]
        logger.info(
            "Timeout at effort=%s, retrying with effort=%s for tool=%s",
            reasoning_effort, downgraded, tool_name,
        )
        retry_start = time.monotonic()
        result = await _run_codex_once(
            prompt,
            project_dir=project_dir,
            model=model,
            reasoning_effort=downgraded,
            reasoning_summary=reasoning_summary,
            timeout=timeout,
            tool_name=tool_name,
            output_schema=output_schema,
        )
        retry_elapsed = time.monotonic() - retry_start
        retry_timed_out = result.startswith(ERROR_PREFIX) and "timed out" in result
        retry_success = not result.startswith(ERROR_PREFIX)

        _record_metric(tool_name, success=retry_success, elapsed=retry_elapsed, timed_out=retry_timed_out)

        if retry_success and output_schema is None:
            result = (
                f"**Note:** Original call timed out at {reasoning_effort}. "
                f"Auto-retried with {downgraded}.\n\n"
            ) + result
            # Update for metadata footer
            elapsed = time.monotonic() - start_time
            reasoning_effort = downgraded

    # When output_schema is set, suppress ALL text mutations to keep JSON clean
    if output_schema is not None:
        return result

    # Append metadata footer
    if not result.startswith(ERROR_PREFIX):
        result += f"\n\n---\n_Codex: {model}, {reasoning_effort}, {elapsed:.0f}s_"

    # Version check — prepend warning on first successful invocation only
    version_warning = await _check_codex_version(consume=True)
    if version_warning and not result.startswith(ERROR_PREFIX):
        result = version_warning + "\n" + result

    return result


# ---------------------------------------------------------------------------
# Structured output formatters
# ---------------------------------------------------------------------------

_SEVERITY_BADGE = {
    "critical": "CRITICAL",
    "warning": "WARNING",
    "suggestion": "SUGGESTION",
    "positive": "POSITIVE",
}

_VERDICT_LABEL = {
    "ship": "Ship It",
    "fix_first": "Fix First",
    "needs_discussion": "Needs Discussion",
}


def _format_finding(finding: dict, index: int) -> str:
    """Render a single finding as markdown."""
    if not isinstance(finding, dict):
        return f"### {index}. [WARNING] (malformed finding)\n"
    severity = finding.get("severity", "warning")
    if not isinstance(severity, str):
        severity = "warning"
    badge = _SEVERITY_BADGE.get(severity, severity.upper())
    title = finding.get("title", "Untitled")
    category = finding.get("category", "other")
    raw_confidence = finding.get("confidence_score", 0)
    # Guard against NaN/Infinity from malformed model output
    try:
        confidence = max(0.0, min(1.0, float(raw_confidence)))
    except (ValueError, TypeError):
        confidence = 0.0
    priority = finding.get("priority", 2)
    body = str(finding.get("body", ""))

    # Location
    loc = finding.get("code_location", {})
    if not isinstance(loc, dict):
        loc = {}
    file_path = loc.get("file_path", "unknown")
    line_range = loc.get("line_range")
    if line_range and isinstance(line_range, dict):
        start = line_range.get("start", 0)
        end = line_range.get("end", 0)
        if start == end:
            location_str = f"`{file_path}:{start}`"
        else:
            location_str = f"`{file_path}:{start}-{end}`"
    else:
        location_str = f"`{file_path}`"

    lines = [
        f"### {index}. [{badge}] {title}",
        f"**{category}** | {location_str} | confidence: {int(confidence * 100)}% | priority: {priority}",
        "",
        body,
    ]

    suggestion = finding.get("suggestion")
    if suggestion:
        lines.extend(["", "**Suggested fix:**", f"```\n{suggestion}\n```"])

    return "\n".join(lines)


def _format_review_files_json(data: dict) -> str:
    """Format structured review_files JSON as rich markdown."""
    parts = []

    # File summaries
    file_summaries = data.get("file_summaries", [])
    if file_summaries and isinstance(file_summaries, list):
        parts.append("## File Summaries\n")
        for fs in file_summaries:
            if not isinstance(fs, dict):
                continue
            parts.append(f"**`{fs.get('file_path', 'unknown')}`** — {fs.get('summary', '')}")
            parts.append(f"Quality: {fs.get('quality_assessment', 'N/A')}\n")

    # Findings sorted by priority (critical first)
    findings = data.get("findings", [])
    if not isinstance(findings, list):
        findings = []
    if findings:
        severity_order = {"critical": 0, "warning": 1, "suggestion": 2, "positive": 3}
        sorted_findings = sorted(findings, key=lambda f: severity_order.get(f.get("severity", "other") if isinstance(f, dict) else "other", 99))
        parts.append("## Findings\n")
        for i, finding in enumerate(sorted_findings, 1):
            parts.append(_format_finding(finding, i))
            parts.append("")
    else:
        parts.append("## Findings\n\nNo issues found.\n")

    # Overall assessment
    overall = data.get("overall_assessment", "")
    try:
        overall_conf = max(0.0, min(1.0, float(data.get("overall_confidence_score", 0))))
    except (ValueError, TypeError):
        overall_conf = 0.0
    if overall:
        parts.append(f"## Overall Assessment (confidence: {int(overall_conf * 100)}%)\n")
        parts.append(str(overall))

    return "\n".join(parts)


def _format_review_diff_json(data: dict) -> str:
    """Format structured review_diff JSON as rich markdown."""
    parts = []

    # Overview
    overview = data.get("overview", "")
    if overview:
        parts.append(f"## Overview\n\n{overview}\n")

    # Verdict
    verdict = data.get("verdict", "needs_discussion")
    if not isinstance(verdict, str):
        verdict = "needs_discussion"
    verdict_label = _VERDICT_LABEL.get(verdict, verdict)
    parts.append(f"## Verdict: {verdict_label}\n")

    # Findings sorted by priority
    findings = data.get("findings", [])
    if not isinstance(findings, list):
        findings = []
    if findings:
        severity_order = {"critical": 0, "warning": 1, "suggestion": 2, "positive": 3}
        sorted_findings = sorted(findings, key=lambda f: severity_order.get(f.get("severity", "other") if isinstance(f, dict) else "other", 99))
        parts.append("## Findings\n")
        for i, finding in enumerate(sorted_findings, 1):
            parts.append(_format_finding(finding, i))
            parts.append("")
    else:
        parts.append("## Findings\n\nNo issues found.\n")

    # Explanation
    explanation = data.get("overall_explanation", "")
    try:
        overall_conf = max(0.0, min(1.0, float(data.get("overall_confidence_score", 0))))
    except (ValueError, TypeError):
        overall_conf = 0.0
    if explanation:
        parts.append(f"## Explanation (confidence: {int(overall_conf * 100)}%)\n")
        parts.append(str(explanation))

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool(
    name="codex_review",
    annotations={
        "title": "Get Codex Second Opinion on a Plan",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def codex_review(params: SecondOpinionInput) -> str:
    """Get an independent second opinion from Codex on your implementation plan.

    Codex reads your codebase in read-only mode and provides:
    - Critical assessment of the proposed approach
    - Concrete alternatives where it sees a better path
    - Risks and blind spots the plan doesn't account for
    - A verdict: adopt / adapt / rethink

    Use this during planning to stress-test your approach with a different AI
    architecture's perspective before committing to implementation.

    Codex may produce file artifacts (code, tests, analysis) in `.claudex/`.
    Read them when referenced in the output.
    """
    cwd = params.project_dir or os.getcwd()
    parts = [SECOND_OPINION_SYSTEM, "\n---\n"]

    if params.user_prompt:
        parts.append(
            "## Original User Request (verbatim)\n"
            "```\n"
            f"{params.user_prompt}\n"
            "```\n"
        )

    parts.append(f"## Proposed Plan\n{params.plan}")

    if params.context:
        parts.append(f"\n## Additional Context\n{params.context}")

    if params.focus_files:
        normalized = _normalize_file_list(params.focus_files, cwd)
        if normalized:
            parts.append(
                f"\n## Key Files to Examine\n"
                f"Pay special attention to: {', '.join(normalized)}"
            )

    git_ctx = await _get_git_context(cwd)
    if git_ctx:
        parts.append(f"\n{git_ctx}")

    parts.append(
        "\nNow read the codebase and provide your second opinion on this plan."
    )

    effective_model = params.model or DEFAULT_MODEL
    effective_timeout = params.timeout_seconds or TOOL_TIMEOUTS.get("codex_review", EXEC_TIMEOUT_SECONDS)
    effective_summary = params.reasoning_summary or DEFAULT_REASONING_SUMMARY

    return await _run_codex(
        "\n".join(parts),
        project_dir=cwd,
        model=effective_model,
        reasoning_effort=params.reasoning_effort.value,
        reasoning_summary=effective_summary,
        timeout=effective_timeout,
        tool_name="codex_review",
    )


@mcp.tool(
    name="codex_plan",
    annotations={
        "title": "Get Codex's Own Independent Plan",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def codex_plan(params: ParallelPlanInput) -> str:
    """Have Codex generate its OWN independent implementation plan for a task.

    Unlike codex_review (which critiques YOUR plan), codex_plan gives
    Codex the same task description and lets it plan from scratch. You can
    then compare both plans side-by-side and synthesize the best of both.

    This is the "plan vs plan" approach — two different AI architectures
    independently planning for the same goal, then the best ideas win.

    Codex may produce file artifacts (code, tests, analysis) in `.claudex/`.
    Read them when referenced in the output.
    """
    cwd = params.project_dir or os.getcwd()
    parts = [PARALLEL_PLAN_SYSTEM, "\n---\n"]

    if params.user_prompt:
        parts.append(
            "## Original User Request (verbatim — form your own interpretation)\n"
            "```\n"
            f"{params.user_prompt}\n"
            "```\n"
        )

    parts.append(f"## Task Context\n{params.task}")

    if params.constraints:
        parts.append(f"\n## Constraints\n{params.constraints}")

    if params.focus_files:
        normalized = _normalize_file_list(params.focus_files, cwd)
        if normalized:
            parts.append(
                f"\n## Relevant Files\n"
                f"Start by reading: {', '.join(normalized)}"
            )

    git_ctx = await _get_git_context(cwd)
    if git_ctx:
        parts.append(f"\n{git_ctx}")

    parts.append(
        "\nRead the codebase, then produce YOUR independent plan. "
        "Be specific — reference real files, functions, and patterns."
    )

    effective_model = params.model or DEFAULT_MODEL
    effective_timeout = params.timeout_seconds or TOOL_TIMEOUTS.get("codex_plan", EXEC_TIMEOUT_SECONDS)
    effective_summary = params.reasoning_summary or DEFAULT_REASONING_SUMMARY

    return await _run_codex(
        "\n".join(parts),
        project_dir=cwd,
        model=effective_model,
        reasoning_effort=params.reasoning_effort.value,
        reasoning_summary=effective_summary,
        timeout=effective_timeout,
        tool_name="codex_plan",
    )


@mcp.tool(
    name="codex_brainstorm",
    annotations={
        "title": "Brainstorm with Codex",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def codex_brainstorm(params: BrainstormInput) -> str:
    """Brainstorm with Codex about a problem, feature, or architecture decision.

    Unlike codex_review (which reviews a specific plan), brainstorm is
    open-ended — Codex explores the problem space, suggests creative approaches,
    and weighs trade-offs. It reads your codebase for context.

    Use this when you don't have a plan yet and want to explore options.

    Codex may produce file artifacts (code, tests, analysis) in `.claudex/`.
    Read them when referenced in the output.
    """
    cwd = params.project_dir or os.getcwd()
    parts = [BRAINSTORM_SYSTEM, "\n---\n"]

    if params.user_prompt:
        parts.append(
            "## Original User Request (verbatim — form your own interpretation)\n"
            "```\n"
            f"{params.user_prompt}\n"
            "```\n"
        )

    parts.append(f"## Topic\n{params.topic}")

    if params.context:
        parts.append(f"\n## Context & Constraints\n{params.context}")

    git_ctx = await _get_git_context(cwd)
    if git_ctx:
        parts.append(f"\n{git_ctx}")

    parts.append(
        "\nExplore this broadly. Suggest multiple approaches with trade-offs. "
        "Reference specific files and patterns in the codebase."
    )

    effective_model = params.model or DEFAULT_MODEL
    effective_timeout = params.timeout_seconds or TOOL_TIMEOUTS.get("codex_brainstorm", EXEC_TIMEOUT_SECONDS)
    effective_summary = params.reasoning_summary or DEFAULT_REASONING_SUMMARY

    return await _run_codex(
        "\n".join(parts),
        project_dir=cwd,
        model=effective_model,
        reasoning_effort=params.reasoning_effort.value,
        reasoning_summary=effective_summary,
        timeout=effective_timeout,
        tool_name="codex_brainstorm",
    )


@mcp.tool(
    name="codex_collab",
    annotations={
        "title": "Collaborate with Codex",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def codex_collab(params: CollaborateInput) -> str:
    """Collaborate with Codex.

    Unlike other tools which are one-shot consultations, this is designed for
    CC to send its analysis along with a specific task, and get
    actionable suggestions back. Codex reads the code independently and
    responds based on the request type:

    - feature_suggestion: Propose concrete features with implementation sketches
    - bug_approach: Suggest debugging strategies and potential root causes
    - code_critique: Flag issues in CC's proposed methods/code
    - red_team: Challenge assumptions, find weaknesses and failure modes
    - verification: Independently verify implementation correctness
    - testing_strategy: Suggest what to test, edge cases, and test structure
    - general: Provide independent analysis and suggestions

    Pass session_id for iterative workflows — creates/continues a session
    document in .claudex/sessions/ for shared memory across rounds.
    Sessions terminate after 4 rounds.

    Codex may produce file artifacts (code, tests, analysis) in `.claudex/`.
    Read them when referenced in the output.
    """
    cwd = params.project_dir or os.getcwd()

    # --- Auto-generate session ID if requested ---
    session_was_auto = False
    if params.session_id == "auto":
        params.session_id = _auto_session_id(params.problem)
        session_was_auto = True

    # --- Session management: check round cap ---
    session_path = None
    session_context = ""
    if params.session_id:
        session_path = _safe_claudex_path(cwd, "sessions", f"{params.session_id}.md")
        if session_path is None:
            return f"{ERROR_PREFIX}Invalid session_id — contains unsafe characters."
        if session_path.exists():
            rounds = _read_session_rounds(session_path)
            if rounds >= MAX_SESSION_ROUNDS:
                # Auto-rollover: generate recap, then start chained session
                logger.info(
                    "Session '%s' hit round cap (%d). Auto-rolling over.",
                    params.session_id, MAX_SESSION_ROUNDS,
                )
                old_session_content = _get_truncated_session(session_path)
                recap_result = await _run_codex_once(
                    RECAP_SYSTEM + "\n---\n"
                    f"## Session Log\n{old_session_content}\n\n"
                    "Generate a concise decision record. Attribute findings clearly "
                    "(CC vs Codex). Focus on decisions and reasoning, not process.",
                    project_dir=cwd,
                    reasoning_effort="medium",
                    timeout=300,
                    tool_name="codex_collab_recap",
                )
                # Save recap
                recap_path = _safe_claudex_path(cwd, "recaps", f"{params.session_id}_recap.md")
                if recap_path and not recap_result.startswith(ERROR_PREFIX):
                    try:
                        recap_path.parent.mkdir(parents=True, exist_ok=True)
                        recap_path.write_text(recap_result)
                        logger.info("Auto-recap saved: %s", recap_path.name)
                    except OSError as exc:
                        logger.warning("Auto-recap save failed: %s", exc)

                # Chain session
                new_session_id = _chain_session_id(params.session_id)
                params.session_id = new_session_id
                session_path = _safe_claudex_path(cwd, "sessions", f"{new_session_id}.md")
                if session_path is None:
                    return f"{ERROR_PREFIX}Invalid chained session_id — contains unsafe characters."
                session_context = ""
                logger.info("Session rolled over to '%s'.", new_session_id)
            else:
                session_context = _get_truncated_session(session_path)

    # --- Build prompt ---
    system_prompt = _build_collaborate_system(params.request_type)
    parts = [system_prompt, "\n---\n"]

    if params.user_prompt:
        parts.append(
            "## Original User Request (verbatim)\n"
            "```\n"
            f"{params.user_prompt}\n"
            "```\n"
        )

    parts.append(f"## Request Type\n{params.request_type.value}")
    parts.append(f"\n## Problem\n{params.problem}")
    parts.append(f"\n## Claude Code's Analysis\n{params.cc_analysis}")

    if params.files_involved:
        normalized = _normalize_file_list(params.files_involved, cwd)
        if normalized:
            parts.append(
                f"\n## Files Involved\n"
                f"Read these files for context: {', '.join(normalized)}"
            )

    if session_context:
        parts.append(f"\n## Previous Rounds\n{session_context}")

    git_ctx = await _get_git_context(cwd)
    if git_ctx:
        parts.append(f"\n{git_ctx}")

    parts.append(
        "\nRead the relevant code yourself, then respond based on the request type. "
        "Be specific — reference files, functions, and line-level details. "
        "End with 2-3 concrete next steps."
    )

    effective_model = params.model or DEFAULT_MODEL
    effective_timeout = params.timeout_seconds or TOOL_TIMEOUTS.get("codex_collab", EXEC_TIMEOUT_SECONDS)
    effective_summary = params.reasoning_summary or DEFAULT_REASONING_SUMMARY

    result = await _run_codex(
        "\n".join(parts),
        project_dir=cwd,
        model=effective_model,
        reasoning_effort=params.reasoning_effort.value,
        reasoning_summary=effective_summary,
        timeout=effective_timeout,
        tool_name="codex_collab",
    )

    # --- Update session document ---
    if params.session_id and session_path is not None and not result.startswith(ERROR_PREFIX):
        try:
            if not session_path.exists():
                _init_session(session_path, params.session_id)
            # Strip metadata footer before writing to session (avoid polluting context)
            session_result = result
            if "\n\n---\n_Codex:" in session_result:
                session_result = session_result.rsplit("\n\n---\n_Codex:", 1)[0]
            rounds = _read_session_rounds(session_path) + 1
            _append_to_session(session_path, rounds, params.cc_analysis, session_result)

            # --- Artifact-session linking ---
            if "## Artifacts Created" in result:
                try:
                    artifact_idx = result.index("## Artifacts Created")
                    # Find end of artifacts section (next double-newline or metadata footer)
                    artifact_end = result.find("\n\n---\n_Codex:", artifact_idx)
                    if artifact_end == -1:
                        artifact_section = result[artifact_idx:]
                    else:
                        artifact_section = result[artifact_idx:artifact_end]
                    # Append to session document
                    session_content = session_path.read_text()
                    session_content += f"\n\n### Artifacts (Round {rounds})\n{artifact_section}\n"
                    session_path.write_text(session_content)
                except (ValueError, OSError) as exc:
                    logger.warning("Artifact-session linking failed: %s", exc)

            session_line = (
                f"\n\nSession: {params.session_id} "
                f"(Round {rounds}/{MAX_SESSION_ROUNDS})"
            )
            if session_was_auto:
                session_line += " [auto-generated]"
            session_line += f"\nDocument: .claudex/sessions/{session_path.name}"
            result += session_line
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("Session update failed: %s", exc)

    return result


@mcp.tool(
    name="codex_review_files",
    annotations={
        "title": "Quick Code Review from Codex",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def codex_review_files(params: QuickReviewInput) -> str:
    """Get a focused code review from Codex on specific files.

    Codex reads the specified files (and surrounding codebase for context)
    and provides targeted feedback. Returns structured JSON findings by default
    (severity, file paths, line numbers, confidence scores, code suggestions)
    formatted as rich markdown. Set structured_output=False for free-text output.

    Codex may produce file artifacts (code, tests, analysis) in `.claudex/`.
    Read them when referenced in the output.
    """
    cwd = params.project_dir or os.getcwd()

    normalized = _normalize_file_list(params.files, cwd)
    if not normalized:
        return f"{ERROR_PREFIX}No valid files to review. Check the file paths and try again."

    use_structured = params.structured_output
    system_prompt = _build_review_system(REVIEW_FILES_SYSTEM_BASE, structured=use_structured)
    parts = [system_prompt, "\n---\n"]

    if params.user_prompt:
        parts.append(
            "## Original User Request (verbatim)\n"
            "```\n"
            f"{params.user_prompt}\n"
            "```\n"
        )

    parts.append(f"## Files to Review\n{', '.join(normalized)}")

    if params.focus:
        parts.append(f"\n## Review Focus\n{params.focus}")

    git_ctx = await _get_git_context(cwd)
    if git_ctx:
        parts.append(f"\n{git_ctx}")

    parts.append(
        "\nReview these files. Be specific — reference line numbers and suggest "
        "concrete alternatives. Don't list things that are fine — focus on what needs attention."
    )

    effective_model = params.model or DEFAULT_MODEL
    effective_timeout = params.timeout_seconds or TOOL_TIMEOUTS.get("codex_review_files", EXEC_TIMEOUT_SECONDS)
    effective_summary = params.reasoning_summary or DEFAULT_REASONING_SUMMARY

    schema = REVIEW_FILES_SCHEMA if use_structured else None

    # Build content parts separately (for clean fallback reconstruction)
    content_parts = parts[2:]  # Everything after [system_prompt, separator]

    start_time = time.monotonic()
    result = await _run_codex(
        "\n".join(parts),
        project_dir=cwd,
        model=effective_model,
        reasoning_effort=params.reasoning_effort.value,
        reasoning_summary=effective_summary,
        timeout=effective_timeout,
        tool_name="codex_review_files",
        output_schema=schema,
    )
    elapsed = time.monotonic() - start_time

    # Auto-fallback on CLI error when structured mode is on (e.g., old Codex CLI
    # that doesn't support --output-schema, or schema validation errors from the API).
    if use_structured and result.startswith(ERROR_PREFIX) and (
        "output-schema" in result.lower()
        or "invalid_json_schema" in result.lower()
        or "response_format" in result.lower()
    ):
        logger.warning("Codex CLI may not support --output-schema, retrying in text mode")
        fallback_parts = [_build_review_system(REVIEW_FILES_SYSTEM_BASE, structured=False), "\n---\n"]
        fallback_parts.extend(content_parts)
        return await _run_codex(
            "\n".join(fallback_parts),
            project_dir=cwd,
            model=effective_model,
            reasoning_effort=params.reasoning_effort.value,
            reasoning_summary=effective_summary,
            timeout=effective_timeout,
            tool_name="codex_review_files",
        )

    # If structured output, parse JSON and format as markdown
    if use_structured and not result.startswith(ERROR_PREFIX):
        try:
            data = json.loads(result)
            if not isinstance(data, dict):
                raise TypeError(f"Expected JSON object, got {type(data).__name__}")
            formatted = _format_review_files_json(data)
            # Append raw JSON in collapsed details block
            formatted += f"\n\n<details>\n<summary>Raw JSON</summary>\n\n```json\n{result}\n```\n</details>"
            formatted += f"\n\n---\n_Codex: {effective_model}, {params.reasoning_effort.value}, {elapsed:.0f}s_"
            return formatted
        except (json.JSONDecodeError, KeyError, TypeError, ValueError, AttributeError) as exc:
            logger.warning(
                "Structured output parse failed for codex_review_files, "
                "retrying in text mode: %s", exc,
            )
            # Auto-fallback: retry in text mode (costs 1 additional Codex call)
            fallback_parts = [_build_review_system(REVIEW_FILES_SYSTEM_BASE, structured=False), "\n---\n"]
            fallback_parts.extend(content_parts)
            fallback_result = await _run_codex(
                "\n".join(fallback_parts),
                project_dir=cwd,
                model=effective_model,
                reasoning_effort=params.reasoning_effort.value,
                reasoning_summary=effective_summary,
                timeout=effective_timeout,
                tool_name="codex_review_files",
            )
            return (
                "**Note:** Structured output failed; auto-retried in text mode "
                "(2 Codex messages used this call).\n\n"
            ) + fallback_result

    return result


@mcp.tool(
    name="codex_evaluate",
    annotations={
        "title": "Codex Evaluate — Tradeoff Analysis",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def codex_evaluate(params: EvaluateInput) -> str:
    """Codex analyzes tradeoffs between approaches so the USER can decide.

    Unlike other tools, this does NOT recommend — it illuminates tradeoffs.
    CC should present both its own analysis AND Codex's analysis to the user,
    who makes the final call. Never arbitrate on the user's behalf.

    Use this when there are 2+ viable approaches and the choice depends on
    priorities, constraints, or preferences that only the user knows.

    Codex may produce file artifacts (code, tests, analysis) in `.claudex/`.
    Read them when referenced in the output.
    """
    cwd = params.project_dir or os.getcwd()
    parts = [EVALUATE_SYSTEM, "\n---\n"]

    parts.append(f"## Decision to Evaluate\n{params.options}")

    if params.constraints:
        parts.append(f"\n## Constraints\n{params.constraints}")

    if params.priorities:
        parts.append(f"\n## Priorities\n{params.priorities}")

    if params.context:
        parts.append(f"\n## Context\n{params.context}")

    if params.focus_files:
        normalized = _normalize_file_list(params.focus_files, cwd)
        if normalized:
            parts.append(f"\n## Relevant Files\n{', '.join(normalized)}")

    git_ctx = await _get_git_context(cwd)
    if git_ctx:
        parts.append(f"\n{git_ctx}")

    parts.append(
        "\nAnalyze each option's tradeoffs. Do NOT recommend — "
        "illuminate the decision so the user can choose."
    )

    effective_model = params.model or DEFAULT_MODEL
    effective_timeout = params.timeout_seconds or TOOL_TIMEOUTS.get("codex_evaluate", EXEC_TIMEOUT_SECONDS)
    effective_summary = params.reasoning_summary or DEFAULT_REASONING_SUMMARY

    return await _run_codex(
        "\n".join(parts),
        project_dir=cwd,
        model=effective_model,
        reasoning_effort=params.reasoning_effort.value,
        reasoning_summary=effective_summary,
        timeout=effective_timeout,
        tool_name="codex_evaluate",
    )


@mcp.tool(
    name="codex_recap",
    annotations={
        "title": "Codex Recap — Decision Record",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def codex_recap(params: RecapInput) -> str:
    """Generate a concise decision record from a collaboration session.

    Use after multi-round debugging, planning, or evaluation sessions to
    capture what was discussed, what was decided, and why. The record
    attributes findings to each model (CC vs Codex) for accountability.

    Requires a session_id that corresponds to an existing session document
    in .claudex/sessions/. The generated recap is saved to .claudex/recaps/.
    """
    cwd = params.project_dir or os.getcwd()

    # Read session document
    session_path = _safe_claudex_path(cwd, "sessions", f"{params.session_id}.md")
    if session_path is None:
        return f"{ERROR_PREFIX}Invalid session_id — contains unsafe characters."
    if not session_path.exists():
        return f"{ERROR_PREFIX}Session '{params.session_id}' not found in .claudex/sessions/."

    session_content = _get_truncated_session(session_path)
    if not session_content:
        return f"{ERROR_PREFIX}Session '{params.session_id}' exists but is empty or unreadable."

    parts = [RECAP_SYSTEM, "\n---\n"]
    parts.append(f"## Session Log\n{session_content}")

    if params.additional_context:
        parts.append(f"\n## Additional Context\n{params.additional_context}")

    parts.append(
        "\nGenerate a concise decision record. Attribute findings clearly "
        "(CC vs Codex). Focus on decisions and reasoning, not process."
    )

    effective_model = params.model or DEFAULT_MODEL
    effective_timeout = params.timeout_seconds or TOOL_TIMEOUTS.get("codex_recap", EXEC_TIMEOUT_SECONDS)
    effective_summary = params.reasoning_summary or DEFAULT_REASONING_SUMMARY

    result = await _run_codex(
        "\n".join(parts),
        project_dir=cwd,
        model=effective_model,
        reasoning_effort=params.reasoning_effort.value,
        reasoning_summary=effective_summary,
        timeout=effective_timeout,
        tool_name="codex_recap",
    )

    # Save recap to .claudex/recaps/
    if not result.startswith(ERROR_PREFIX):
        recap_path = _safe_claudex_path(cwd, "recaps", f"{params.session_id}_recap.md")
        if recap_path:
            try:
                recap_path.parent.mkdir(parents=True, exist_ok=True)
                recap_path.write_text(result)
                result += f"\n\nDecision record saved to: .claudex/recaps/{recap_path.name}"
            except OSError as exc:
                logger.warning("Recap save failed: %s", exc)

    return result


async def _get_git_diff(project_dir: str, staged: bool = False) -> Optional[str]:
    """Get git diff content, filtering binary files, capped at DIFF_MAX_BYTES."""
    try:
        cmd = ["git", "diff"]
        if staged:
            cmd.append("--staged")
        cmd.extend(["--no-color", "--diff-filter=ACMRT"])  # exclude deleted-only

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=project_dir,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        if proc.returncode != 0:
            return None

        diff_text = stdout.decode(errors="replace")
        if not diff_text.strip():
            return None

        # Cap size
        if len(diff_text.encode("utf-8")) > DIFF_MAX_BYTES:
            diff_text = diff_text.encode("utf-8")[:DIFF_MAX_BYTES].decode("utf-8", errors="ignore") + "\n... [diff truncated at 50KB]"

        return diff_text
    except (asyncio.TimeoutError, OSError):
        return None


@mcp.tool(
    name="codex_review_diff",
    annotations={
        "title": "Codex Diff Review",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def codex_review_diff(params: ReviewDiffInput) -> str:
    """Get Codex to review your git diff — staged or unstaged changes.

    Codex reads the actual diff and surrounding codebase context to find
    bugs, risks, and issues introduced by the changes. Returns structured JSON
    findings by default with verdict (ship/fix_first/needs_discussion), severity,
    confidence scores, and code suggestions. Set structured_output=False for
    free-text output.

    Codex may produce file artifacts (code, tests, analysis) in `.claudex/`.
    """
    cwd = params.project_dir or os.getcwd()

    diff_text = await _get_git_diff(cwd, staged=params.staged)
    if not diff_text:
        diff_type = "staged" if params.staged else "unstaged"
        return f"{ERROR_PREFIX}No {diff_type} changes found. Nothing to review."

    use_structured = params.structured_output
    system_prompt = _build_review_system(REVIEW_DIFF_SYSTEM_BASE, structured=use_structured)
    parts = [system_prompt, "\n---\n"]

    if params.user_prompt:
        parts.append(
            "## Original User Request (verbatim)\n"
            "```\n"
            f"{params.user_prompt}\n"
            "```\n"
        )

    diff_type = "Staged" if params.staged else "Unstaged"
    parts.append(f"## {diff_type} Changes\n```diff\n{diff_text}\n```")

    if params.focus:
        parts.append(f"\n## Review Focus\n{params.focus}")

    if params.context:
        parts.append(f"\n## Context\n{params.context}")

    parts.append(
        "\nReview these changes. Focus on what the diff INTRODUCES — "
        "don't critique pre-existing code unless the changes make it worse."
    )

    effective_model = params.model or DEFAULT_MODEL
    effective_timeout = params.timeout_seconds or TOOL_TIMEOUTS.get("codex_review_diff", EXEC_TIMEOUT_SECONDS)
    effective_summary = params.reasoning_summary or DEFAULT_REASONING_SUMMARY

    schema = REVIEW_DIFF_SCHEMA if use_structured else None

    # Build content parts separately (for clean fallback reconstruction)
    content_parts = parts[2:]  # Everything after [system_prompt, separator]

    start_time = time.monotonic()
    result = await _run_codex(
        "\n".join(parts),
        project_dir=cwd,
        model=effective_model,
        reasoning_effort=params.reasoning_effort.value,
        reasoning_summary=effective_summary,
        timeout=effective_timeout,
        tool_name="codex_review_diff",
        output_schema=schema,
    )
    elapsed = time.monotonic() - start_time

    # Auto-fallback on CLI error when structured mode is on (e.g., old Codex CLI
    # that doesn't support --output-schema, or schema validation errors from the API).
    if use_structured and result.startswith(ERROR_PREFIX) and (
        "output-schema" in result.lower()
        or "invalid_json_schema" in result.lower()
        or "response_format" in result.lower()
    ):
        logger.warning("Codex CLI may not support --output-schema, retrying in text mode")
        fallback_parts = [_build_review_system(REVIEW_DIFF_SYSTEM_BASE, structured=False), "\n---\n"]
        fallback_parts.extend(content_parts)
        return await _run_codex(
            "\n".join(fallback_parts),
            project_dir=cwd,
            model=effective_model,
            reasoning_effort=params.reasoning_effort.value,
            reasoning_summary=effective_summary,
            timeout=effective_timeout,
            tool_name="codex_review_diff",
        )

    # If structured output, parse JSON and format as markdown
    if use_structured and not result.startswith(ERROR_PREFIX):
        try:
            data = json.loads(result)
            if not isinstance(data, dict):
                raise TypeError(f"Expected JSON object, got {type(data).__name__}")
            formatted = _format_review_diff_json(data)
            # Append raw JSON in collapsed details block
            formatted += f"\n\n<details>\n<summary>Raw JSON</summary>\n\n```json\n{result}\n```\n</details>"
            formatted += f"\n\n---\n_Codex: {effective_model}, {params.reasoning_effort.value}, {elapsed:.0f}s_"
            return formatted
        except (json.JSONDecodeError, KeyError, TypeError, ValueError, AttributeError) as exc:
            logger.warning(
                "Structured output parse failed for codex_review_diff, "
                "retrying in text mode: %s", exc,
            )
            # Auto-fallback: retry in text mode (costs 1 additional Codex call)
            fallback_parts = [_build_review_system(REVIEW_DIFF_SYSTEM_BASE, structured=False), "\n---\n"]
            fallback_parts.extend(content_parts)
            fallback_result = await _run_codex(
                "\n".join(fallback_parts),
                project_dir=cwd,
                model=effective_model,
                reasoning_effort=params.reasoning_effort.value,
                reasoning_summary=effective_summary,
                timeout=effective_timeout,
                tool_name="codex_review_diff",
            )
            return (
                "**Note:** Structured output failed; auto-retried in text mode "
                "(2 Codex messages used this call).\n\n"
            ) + fallback_result

    return result


@mcp.tool(
    name="codex_status",
    annotations={
        "title": "Claudex Status Dashboard",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def codex_status(params: StatusInput) -> str:
    """Show Claudex status: Codex CLI info, active sessions, recaps, artifacts, disk usage.

    This is a lightweight diagnostic tool that does NOT call Codex (zero subscription cost).
    Use it for situational awareness without consuming a ChatGPT message.
    """
    cwd = params.project_dir or os.getcwd()
    lines = ["Claudex Status", "\u2550" * 14]

    # --- Codex CLI ---
    codex_bin = _find_codex_bin()
    codex_version = "unknown"
    codex_location = codex_bin
    try:
        proc = await asyncio.create_subprocess_exec(
            codex_bin, "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=2)
        if proc.returncode == 0:
            codex_version = stdout.decode().strip() or "unknown"
    except (asyncio.TimeoutError, OSError):
        codex_version = "not found"
        codex_location = "not found"

    # Check for updates
    version_warning = await _check_codex_version()
    if version_warning:
        lines.append(f"Codex CLI:     {codex_location} ({codex_version}) — OUTDATED")
        lines.append(version_warning.strip())
    else:
        lines.append(f"Codex CLI:     {codex_location} ({codex_version})")
    lines.append(f"Default Model: {DEFAULT_MODEL}")
    lines.append(f"Effort:        {DEFAULT_REASONING_EFFORT}")
    lines.append(f"Timeout:       {EXEC_TIMEOUT_SECONDS}s (default, per-tool overrides available)")
    lines.append(f"Tools:         10 (8 Codex-calling + codex_status + codex_ping)")

    # Plugin version — resolve relative to server.py, not project dir
    plugin_json = Path(__file__).resolve().parent.parent / ".claude-plugin" / "plugin.json"
    plugin_version = "unknown"
    if plugin_json.is_file():
        try:
            plugin_version = json.loads(plugin_json.read_text()).get("version", "unknown")
        except (OSError, ValueError):
            pass
    lines.append(f"Plugin:        v{plugin_version}")
    lines.append(f"Author:        Omri Tal | botique.co.il | hello@botique.co.il")

    # --- Sessions ---
    claudex_dir = Path(cwd) / ".claudex"
    sessions_dir = claudex_dir / "sessions"
    session_entries = []
    if sessions_dir.is_dir():
        for f in sorted(sessions_dir.iterdir()):
            if f.is_file() and f.suffix == ".md":
                try:
                    rounds = _read_session_rounds(f)
                    stat = f.stat()
                    age_secs = time.time() - stat.st_mtime
                    if age_secs < 3600:
                        age_str = f"{int(age_secs / 60)}m ago"
                    else:
                        age_str = f"{age_secs / 3600:.1f}h ago"
                    size_kb = stat.st_size / 1024
                    name = f.stem
                    session_entries.append(
                        f"  {name:<20s} Round {rounds}/{MAX_SESSION_ROUNDS}  "
                        f"({age_str}, {size_kb:.1f} KB)"
                    )
                except OSError:
                    pass

    if session_entries:
        lines.append(f"\nSessions ({len(session_entries)} active):")
        lines.extend(session_entries)
    else:
        lines.append("\nSessions: none")

    # --- Recaps ---
    recaps_dir = claudex_dir / "recaps"
    recap_count = 0
    recap_bytes = 0
    if recaps_dir.is_dir():
        for f in recaps_dir.iterdir():
            if f.is_file():
                try:
                    recap_count += 1
                    recap_bytes += f.stat().st_size
                except OSError:
                    pass
    if recap_count:
        lines.append(f"Recaps: {recap_count} file(s) ({recap_bytes / 1024:.1f} KB)")
    else:
        lines.append("Recaps: none")

    # --- Artifact run dirs ---
    run_dir_count = 0
    run_dir_bytes = 0
    if claudex_dir.is_dir():
        for entry in claudex_dir.iterdir():
            if entry.is_dir() and entry.name.startswith("run-") and not entry.is_symlink():
                run_dir_count += 1
                for root_path, _dirs, files in os.walk(entry):
                    for fname in files:
                        try:
                            run_dir_bytes += os.path.getsize(os.path.join(root_path, fname))
                        except OSError:
                            pass
    if run_dir_count:
        lines.append(f"Artifacts: {run_dir_count} run dir(s) ({run_dir_bytes / 1024:.1f} KB)")
    else:
        lines.append("Artifacts: none")

    # --- Total .claudex/ disk usage ---
    total_bytes = 0
    if claudex_dir.is_dir():
        for root_path, _dirs, files in os.walk(claudex_dir):
            for fname in files:
                try:
                    total_bytes += os.path.getsize(os.path.join(root_path, fname))
                except OSError:
                    pass
        lines.append(f"\n.claudex/ total: {total_bytes / 1024:.1f} KB")

    # --- Metrics ---
    metrics_summary = _get_metrics_summary()
    lines.append(f"\nMetrics (this session):\n{metrics_summary}")

    return "\n".join(lines)


@mcp.tool(
    name="codex_ping",
    annotations={
        "title": "Test Codex Connection",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def codex_ping() -> str:
    """Test that Codex CLI is installed, authenticated, and working."""
    codex_path = _find_codex_bin()
    if codex_path == "codex" and not shutil.which("codex"):
        return (
            "Codex CLI not found in PATH.\n"
            "Install: npm i -g @openai/codex\n"
            "Auth:    codex login"
        )

    try:
        proc = await asyncio.create_subprocess_exec(
            codex_path, "exec",
            "--sandbox", "read-only",
            "--skip-git-repo-check",
            "--color", "never",
            "-m", DEFAULT_MODEL,
            "Say 'pong' and nothing else.",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except asyncio.TimeoutError:
        return "Codex CLI found but timed out. Check your internet connection."
    except (OSError, asyncio.TimeoutError) as e:
        return f"Codex CLI found at {codex_path} but failed to run: {e}"

    if proc.returncode == 0:
        return f"Codex CLI ready at {codex_path}\nResponse: {stdout.decode().strip()}"

    err = stderr.decode(errors="replace").strip()
    if "login" in err.lower() or "auth" in err.lower():
        return f"Codex CLI found but not authenticated.\nRun: codex login"

    return f"Codex CLI found but returned error:\n{err}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()

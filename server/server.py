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
import os
import re
import shutil
import logging
import time
import uuid
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
EXEC_TIMEOUT_SECONDS = 600  # 10 min max per Codex call

FINAL_ANSWER_DELIMITER = "---FINAL-ANSWER---"
ARTIFACT_MAX_BYTES = 100 * 1024  # 100 KB per artifact
ARTIFACT_TAG_RE = re.compile(
    r'<claudex-artifact\s+([^>]+)>(.*?)</claudex-artifact>',
    re.DOTALL,
)
ARTIFACT_ATTR_RE = re.compile(r'(\w+)="([^"]*)"')
RUN_DIR_MAX_AGE_SECONDS = 3600  # 1 hour

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
# System prompts for each mode
# ---------------------------------------------------------------------------

SECOND_OPINION_SYSTEM = """\
You are a senior engineer providing an independent second opinion on a proposed plan \
or implementation approach. You have full read access to the codebase.

Your job:
1. Analyze the proposed plan critically — look for blind spots, edge cases, and risks.
2. Suggest concrete alternatives where you see a better path.
3. Flag any architectural concerns, dependency issues, or maintainability risks.
4. Be direct and specific. No generic praise. If the plan is solid, say so briefly and \
   focus on what could still go wrong.

Format your response as:
## Assessment
(1-2 sentence overall verdict)

## What I'd Do Differently
(numbered list of concrete alternatives or improvements)

## Risks & Blind Spots
(things the plan doesn't account for)

## Verdict
(adopt / adapt / rethink)
""" + ARTIFACT_INSTRUCTIONS

PARALLEL_PLAN_SYSTEM = """\
You are a senior engineer. You have full read access to the codebase. \
Given a task description, produce YOUR OWN independent implementation plan. \
Do NOT just critique — actually plan how YOU would implement this from scratch.

Read the relevant files in the codebase first, then produce a plan with:

## Your Approach
(describe your strategy in 2-3 sentences)

## Step-by-Step Plan
(numbered steps with specific files, functions, and patterns you'd use)

## Key Design Decisions
(list the important choices you're making and why)

## Dependencies & Risks
(what could go wrong, what this depends on)

Be concrete — reference actual files, existing patterns, and real constraints \
from this codebase. No vague hand-waving.

If an "Original User Request" section is provided, treat it as the primary input. \
Form your OWN interpretation of what needs to be done — do not assume the "Task Context" \
section below it is the correct or only framing. The task context provides useful \
grounding (constraints, relevant files) but may reflect another engineer's interpretation.
""" + ARTIFACT_INSTRUCTIONS

BRAINSTORM_SYSTEM = """\
You are a senior engineer brainstorming with a peer. You have full read access to the \
codebase. Explore the problem space broadly — suggest creative approaches, weigh \
trade-offs, and think about what the ideal solution looks like if there were no \
constraints, then work backward to what's practical.

Be concrete. Reference actual files and patterns in the codebase when relevant.

If an "Original User Request" section is provided, treat it as the primary input. \
Form your OWN interpretation of the problem — do not assume the "Topic" section \
below it is the correct or only framing. The topic provides useful grounding \
but may reflect another engineer's interpretation. Think independently.
""" + ARTIFACT_INSTRUCTIONS

COLLABORATE_SYSTEM = """\
You are collaborating with another AI (Claude Code) to solve a problem together.
Claude has already analyzed the codebase and is sharing its findings with you.

Your job:
1. Read the relevant code yourself — don't just trust Claude's analysis
2. Based on request type:
   - feature_suggestion: Propose concrete features with implementation sketches
   - bug_approach: Suggest debugging strategies and potential root causes
   - code_critique: Flag issues in Claude's proposed methods/code
   - red_team: Challenge every assumption. Find weaknesses, edge cases, failure modes.
     Act as an adversary trying to break the implementation.
   - verification: Independently verify that the proposed implementation is correct.
     Check logic, data flow, error handling, and boundary conditions.
   - testing_strategy: Suggest a comprehensive testing approach — what to test,
     edge cases to cover, integration points to validate, and test structure.
   - general: Provide your independent analysis and suggestions
3. Be specific — reference files, functions, line-level details
4. Where you AGREE with Claude's analysis, say so briefly
5. Where you DISAGREE or see something Claude missed, explain in detail
6. End with 2-3 concrete next steps
""" + ARTIFACT_INSTRUCTIONS

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("claudex")

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


def _prepare_run_dir(project_dir: str) -> Path:
    """Create an isolated per-run artifact directory under .claudex/.

    Returns the Path to the run directory (e.g. <project>/.claudex/run-<uuid>/).
    Also performs best-effort cleanup of stale run dirs and warns if .claudex
    is not in .gitignore.
    """
    root = Path(project_dir)
    claudex_dir = root / ".claudex"

    # Best-effort cleanup of old runs
    _cleanup_old_run_dirs(claudex_dir)

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


# ---------------------------------------------------------------------------
# Enums & Input Models
# ---------------------------------------------------------------------------


class CodexModel(str, Enum):
    """Supported Codex models (current only, retired models removed)."""
    GPT5_3_CODEX = "gpt-5.3-codex"       # Best. Default.
    GPT5_2_CODEX = "gpt-5.2-codex"       # Previous flagship
    GPT5_1_CODEX = "gpt-5.1-codex"       # Long-horizon tasks
    GPT5_CODEX = "gpt-5-codex"           # Original Codex variant
    GPT5_CODEX_MINI = "gpt-5-codex-mini" # Cost-effective


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
    model: CodexModel = Field(
        default=CodexModel.GPT5_3_CODEX,
        description="Codex model to use. gpt-5.3-codex is best for deep architectural analysis.",
    )
    reasoning_effort: ReasoningEffort = Field(
        default=ReasoningEffort.HIGH,
        description="How deeply Codex should reason. Use 'xhigh' for maximum depth (slower).",
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
    model: CodexModel = Field(
        default=CodexModel.GPT5_3_CODEX,
        description="Codex model to use. gpt-5.3-codex recommended for planning.",
    )
    reasoning_effort: ReasoningEffort = Field(
        default=ReasoningEffort.HIGH,
        description="How deeply Codex should reason. Use 'xhigh' for maximum depth (slower).",
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
    model: CodexModel = Field(
        default=CodexModel.GPT5_3_CODEX,
        description="Codex model to use.",
    )
    reasoning_effort: ReasoningEffort = Field(
        default=ReasoningEffort.HIGH,
        description="How deeply Codex should reason. Use 'xhigh' for maximum depth (slower).",
    )


class CollaborateInput(BaseModel):
    """Input for interactive CC+Codex problem-solving collaboration."""
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
    files_involved: Optional[str] = Field(
        default=None,
        description="Comma-separated list of files relevant to this problem.",
    )
    project_dir: Optional[str] = Field(
        default=None,
        description="Absolute path to the project directory. Defaults to cwd.",
    )
    model: CodexModel = Field(
        default=CodexModel.GPT5_3_CODEX,
        description="Codex model to use.",
    )
    reasoning_effort: ReasoningEffort = Field(
        default=ReasoningEffort.HIGH,
        description="How deeply Codex should reason. Use 'xhigh' for maximum depth (slower).",
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
    focus: Optional[str] = Field(
        default=None,
        description=(
            "What to focus the review on: 'security', 'performance', 'correctness', "
            "'maintainability', or a custom focus area."
        ),
    )
    project_dir: Optional[str] = Field(
        default=None,
        description="Absolute path to the project directory. Defaults to cwd.",
    )
    model: CodexModel = Field(
        default=CodexModel.GPT5_3_CODEX,
        description="Codex model to use.",
    )
    reasoning_effort: ReasoningEffort = Field(
        default=ReasoningEffort.MEDIUM,
        description="Reasoning depth. 'medium' is usually fine for reviews.",
    )


# ---------------------------------------------------------------------------
# Core execution helper
# ---------------------------------------------------------------------------


async def _run_codex(
    prompt: str,
    *,
    project_dir: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    reasoning_effort: str = DEFAULT_REASONING_EFFORT,
    timeout: int = EXEC_TIMEOUT_SECONDS,
) -> str:
    """
    Run Codex CLI in non-interactive, read-only mode and return the output.

    Uses ``codex`` with:
      --sandbox read-only         -> Codex can read the repo but cannot edit or run commands
      --skip-git-repo-check       -> Works in non-git dirs too
      --color never               -> Prevents ANSI escape codes in captured output
      -m <model>                  -> Model selection
      -c model_reasoning_effort   -> Reasoning depth
      -c model_reasoning_summary  -> Gets chain-of-thought reasoning
      --cd <dir>                  -> Working directory (project root)
    """
    cwd = project_dir or os.getcwd()
    codex_bin = _find_codex_bin()

    cmd = [
        codex_bin, "exec",
        "--sandbox", "read-only",
        "--skip-git-repo-check",
        "--color", "never",
        "-m", model,
        "-c", f"model_reasoning_effort={reasoning_effort}",
        "-c", "model_reasoning_summary=detailed",
        "--cd", cwd,
        prompt,
    ]

    logger.info(
        "Running Codex: model=%s effort=%s cwd=%s",
        model, reasoning_effort, cwd,
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
        proc.kill()
        return (
            f"Error: Codex timed out after {timeout}s. "
            "Try simplifying the prompt or reducing reasoning_effort."
        )
    except FileNotFoundError:
        return (
            "Error: Codex CLI not found. Install it with:\n"
            "  npm i -g @openai/codex\n"
            "Then authenticate:\n"
            "  codex login"
        )

    if proc.returncode != 0:
        err_msg = stderr.decode(errors="replace").strip()
        if "not authenticated" in err_msg.lower() or "login" in err_msg.lower():
            return (
                "Error: Codex is not authenticated. Run:\n"
                "  codex login\n"
                "to sign in with your ChatGPT subscription."
            )
        if "rate limit" in err_msg.lower():
            return (
                "Error: Codex rate limit reached. Your ChatGPT subscription has "
                "per-window message limits. Wait a few minutes and try again, "
                "or consider using a lighter model (gpt-5-codex-mini)."
            )
        return f"Error: Codex exited with code {proc.returncode}.\nStderr: {err_msg}"

    output = stdout.decode(errors="replace").strip()
    if not output:
        fallback = stderr.decode(errors="replace").strip()
        if fallback:
            return fallback
        return "Codex returned no output. The prompt may need to be more specific."

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
    parts = [SECOND_OPINION_SYSTEM, "\n---\n", f"## Proposed Plan\n{params.plan}"]

    if params.context:
        parts.append(f"\n## Additional Context\n{params.context}")

    if params.focus_files:
        parts.append(
            f"\n## Key Files to Examine\n"
            f"Pay special attention to: {params.focus_files}"
        )

    parts.append(
        "\nNow read the codebase and provide your second opinion on this plan."
    )

    return await _run_codex(
        "\n".join(parts),
        project_dir=params.project_dir,
        model=params.model.value,
        reasoning_effort=params.reasoning_effort.value,
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
        parts.append(
            f"\n## Relevant Files\n"
            f"Start by reading: {params.focus_files}"
        )

    parts.append(
        "\nRead the codebase, then produce YOUR independent plan. "
        "Be specific — reference real files, functions, and patterns."
    )

    return await _run_codex(
        "\n".join(parts),
        project_dir=params.project_dir,
        model=params.model.value,
        reasoning_effort=params.reasoning_effort.value,
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

    parts.append(
        "\nExplore this broadly. Suggest multiple approaches with trade-offs. "
        "Reference specific files and patterns in the codebase."
    )

    return await _run_codex(
        "\n".join(parts),
        project_dir=params.project_dir,
        model=params.model.value,
        reasoning_effort=params.reasoning_effort.value,
    )


@mcp.tool(
    name="codex_collab",
    annotations={
        "title": "Collaborate with Codex on a Problem",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def codex_collab(params: CollaborateInput) -> str:
    """Collaborate with Codex to solve a problem together.

    Unlike other tools which are one-shot consultations, this is designed for
    CC to send a specific problem along with its own analysis, and get
    actionable suggestions back. Codex reads the code independently and
    responds based on the request type:

    - feature_suggestion: Propose concrete features with implementation sketches
    - bug_approach: Suggest debugging strategies and potential root causes
    - code_critique: Flag issues in CC's proposed methods/code
    - red_team: Challenge assumptions, find weaknesses and failure modes
    - verification: Independently verify implementation correctness
    - testing_strategy: Suggest what to test, edge cases, and test structure
    - general: Provide independent analysis and suggestions

    Codex may produce file artifacts (code, tests, analysis) in `.claudex/`.
    Read them when referenced in the output.
    """
    parts = [
        COLLABORATE_SYSTEM,
        "\n---\n",
        f"## Request Type\n{params.request_type.value}",
        f"\n## Problem\n{params.problem}",
        f"\n## Claude Code's Analysis\n{params.cc_analysis}",
    ]

    if params.files_involved:
        parts.append(
            f"\n## Files Involved\n"
            f"Read these files for context: {params.files_involved}"
        )

    parts.append(
        "\nRead the relevant code yourself, then respond based on the request type. "
        "Be specific — reference files, functions, and line-level details. "
        "End with 2-3 concrete next steps."
    )

    return await _run_codex(
        "\n".join(parts),
        project_dir=params.project_dir,
        model=params.model.value,
        reasoning_effort=params.reasoning_effort.value,
    )


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
    and provides targeted feedback. Useful for getting a different perspective
    on code you've just written or are about to refactor.

    Codex may produce file artifacts (code, tests, analysis) in `.claudex/`.
    Read them when referenced in the output.
    """
    focus_instruction = ""
    if params.focus:
        focus_instruction = f"Focus specifically on: {params.focus}\n"

    prompt = (
        f"Review the following files in this codebase: {params.files}\n"
        f"{focus_instruction}"
        "Provide specific, actionable feedback. Reference line numbers where possible. "
        "Don't list things that are fine — focus on what needs attention."
        + ARTIFACT_INSTRUCTIONS
    )

    return await _run_codex(
        prompt,
        project_dir=params.project_dir,
        model=params.model.value,
        reasoning_effort=params.reasoning_effort.value,
    )


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
    codex_path = shutil.which("codex")
    if not codex_path:
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
            "Say 'pong' and nothing else.",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except asyncio.TimeoutError:
        return "Codex CLI found but timed out. Check your internet connection."
    except Exception as e:
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

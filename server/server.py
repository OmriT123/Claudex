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

Requires:
  - Codex CLI installed: npm i -g @openai/codex
  - Codex authenticated: codex login (ChatGPT subscription)
  - Python 3.10+
"""

import asyncio
import os
import shutil
import logging
from enum import Enum
from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, ConfigDict

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MODEL = "gpt-5.3-codex"
DEFAULT_REASONING_EFFORT = "xhigh"
EXEC_TIMEOUT_SECONDS = 300  # 5 min max per Codex call

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
"""

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
"""

BRAINSTORM_SYSTEM = """\
You are a senior engineer brainstorming with a peer. You have full read access to the \
codebase. Explore the problem space broadly — suggest creative approaches, weigh \
trade-offs, and think about what the ideal solution looks like if there were no \
constraints, then work backward to what's practical.

Be concrete. Reference actual files and patterns in the codebase when relevant.
"""

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
"""

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("claudex")

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

mcp = FastMCP("claudex")

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
    """Collaboration request types for claudex_collab."""
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
        default=ReasoningEffort.XHIGH,
        description="How deeply Codex should reason. 'xhigh' recommended for plan review.",
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
        default=ReasoningEffort.XHIGH,
        description="How deeply Codex should reason. 'xhigh' recommended for planning.",
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
        default=ReasoningEffort.XHIGH,
        description="How deeply Codex should reason.",
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
        default=ReasoningEffort.XHIGH,
        description="How deeply Codex should reason.",
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

    return output


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool(
    name="claudex_review",
    annotations={
        "title": "Get Codex Second Opinion on a Plan",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def claudex_review(params: SecondOpinionInput) -> str:
    """Get an independent second opinion from Codex on your implementation plan.

    Codex reads your codebase in read-only mode and provides:
    - Critical assessment of the proposed approach
    - Concrete alternatives where it sees a better path
    - Risks and blind spots the plan doesn't account for
    - A verdict: adopt / adapt / rethink

    Use this during planning to stress-test your approach with a different AI
    architecture's perspective before committing to implementation.
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
    name="claudex_plan",
    annotations={
        "title": "Get Codex's Own Independent Plan",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def claudex_plan(params: ParallelPlanInput) -> str:
    """Have Codex generate its OWN independent implementation plan for a task.

    Unlike claudex_review (which critiques YOUR plan), claudex_plan gives
    Codex the same task description and lets it plan from scratch. You can
    then compare both plans side-by-side and synthesize the best of both.

    This is the "plan vs plan" approach — two different AI architectures
    independently planning for the same goal, then the best ideas win.
    """
    parts = [PARALLEL_PLAN_SYSTEM, "\n---\n", f"## Task\n{params.task}"]

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
    name="claudex_brainstorm",
    annotations={
        "title": "Brainstorm with Codex",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def claudex_brainstorm(params: BrainstormInput) -> str:
    """Brainstorm with Codex about a problem, feature, or architecture decision.

    Unlike claudex_review (which reviews a specific plan), brainstorm is
    open-ended — Codex explores the problem space, suggests creative approaches,
    and weighs trade-offs. It reads your codebase for context.

    Use this when you don't have a plan yet and want to explore options.
    """
    parts = [BRAINSTORM_SYSTEM, "\n---\n", f"## Topic\n{params.topic}"]

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
    name="claudex_collab",
    annotations={
        "title": "Collaborate with Codex on a Problem",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def claudex_collab(params: CollaborateInput) -> str:
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
    name="claudex_review_files",
    annotations={
        "title": "Quick Code Review from Codex",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def claudex_review_files(params: QuickReviewInput) -> str:
    """Get a focused code review from Codex on specific files.

    Codex reads the specified files (and surrounding codebase for context)
    and provides targeted feedback. Useful for getting a different perspective
    on code you've just written or are about to refactor.
    """
    focus_instruction = ""
    if params.focus:
        focus_instruction = f"Focus specifically on: {params.focus}\n"

    prompt = (
        f"Review the following files in this codebase: {params.files}\n"
        f"{focus_instruction}"
        "Provide specific, actionable feedback. Reference line numbers where possible. "
        "Don't list things that are fine — focus on what needs attention."
    )

    return await _run_codex(
        prompt,
        project_dir=params.project_dir,
        model=params.model.value,
        reasoning_effort=params.reasoning_effort.value,
    )


@mcp.tool(
    name="claudex_ping",
    annotations={
        "title": "Test Codex Connection",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def claudex_ping() -> str:
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

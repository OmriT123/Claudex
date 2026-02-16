---
name: claudex
description: "Use when entering plan mode for non-trivial tasks, when making architecture decisions, when debugging complex issues, when the user asks for a second opinion, when you need to verify correctness, or when collaborating with Codex. Auto-triggers during plan mode for complex implementations."
---

# Claudex — Claude Code Skill

## Purpose

Use this skill to consult **OpenAI Codex as a specialist teammate** during planning, decision-making, debugging, verification, and problem-solving. Codex reads the same codebase but uses a fundamentally different AI architecture (GPT/Codex vs Claude), which means it catches different patterns, risks, and opportunities. This creates a multi-perspective dynamic that stress-tests plans, red-teams implementations, and verifies correctness before you commit.

## When to Use

**Auto-trigger** (use without being asked when the situation fits):
- You're in **plan mode** and about to finalize a non-trivial implementation plan
- The user is making an **architecture decision** with multiple viable paths
- You're **uncertain** about the best approach and a second perspective would help
- You've just **finished implementing** something complex and want verification
- You're **debugging** a tricky issue and want a different angle

**User-triggered** (user explicitly asks):
- "brainstorm", "second opinion", "what does codex think", "ask codex"
- "red team this", "stress test this plan", "get another perspective"
- "plan vs plan", "compare approaches", "parallel plan"
- "collaborate with codex", "get codex's help", "work with codex on this"
- "verify this", "check my implementation", "is this correct"
- "what should I test", "testing strategy", "help me debug this"

**Don't use** for:
- Trivial changes (typos, renames, simple config)
- Questions Codex can't help with (it only reads code, no web access)
- When the user is in a hurry and latency matters (Codex calls take 30-120s)

## Quick Reference: Which Tool Should I Use?

```
What's your situation?
│
├─ "I have a task, no plan yet"
│   ├─ Want independent plans to compare → claudex_plan
│   └─ Want to explore options broadly   → claudex_brainstorm
│
├─ "I have a plan already"
│   └─ Want it stress-tested/critiqued   → claudex_review
│
├─ "I have a specific problem"
│   ├─ Need feature ideas               → claudex_collab (feature_suggestion)
│   ├─ Need debugging help              → claudex_collab (bug_approach)
│   ├─ Want my code critiqued           → claudex_collab (code_critique)
│   ├─ Want assumptions challenged      → claudex_collab (red_team)
│   ├─ Want correctness verified        → claudex_collab (verification)
│   └─ Want testing guidance            → claudex_collab (testing_strategy)
│
├─ "I want specific files reviewed"
│   └─ Targeted code review             → claudex_review_files
│
└─ "Is Codex even working?"
    └─ Connection test                   → claudex_ping
```

## Available Tools

| Tool | When | What It Does |
|------|------|-------------|
| `claudex_plan` | **You have a task, want both plans** | Codex generates its OWN independent plan. You compare and synthesize. |
| `claudex_review` | **You have YOUR plan, want it critiqued** | Codex reviews and stress-tests your specific approach. |
| `claudex_brainstorm` | **No plan yet, exploring options** | Open-ended exploration of the problem space. |
| `claudex_collab` | **You need help solving a specific problem** | Send your analysis + a request type, get targeted suggestions back. |
| `claudex_review_files` | **Want specific files reviewed** | Targeted code review from a different angle. |
| `claudex_ping` | **Setup/debug** | Verify Codex CLI is installed and working. |

## Workflow 1: Parallel Planning (Recommended for Complex Tasks)

This is the most powerful workflow. Both you and Codex independently plan for the same task, then you merge the best of both.

```
1. User describes a task/feature
2. You formulate YOUR plan as you normally would (don't present it yet)
3. Call `claudex_plan` with:
   - task: The same task description (WHAT, not HOW)
   - constraints: Tech stack, deadlines, backward compatibility requirements
   - focus_files: Key files/dirs involved
   - project_dir: The project root
4. Now you have TWO independent plans. Compare them:
   - Where do they agree? (high confidence — both architectures converge)
   - Where do they differ? (interesting — examine why)
   - What did Codex think of that you didn't? (potential blind spots)
   - What did you think of that Codex didn't? (your contextual advantage)
5. Synthesize into ONE plan, clearly showing:
   "Both CC and Codex agree on X.
    Codex suggested Y for [this part] — adopting because [reason].
    I'm keeping my approach for Z because [reason]."
```

## Workflow 2: Second Opinion (For Plans Already Formed)

Use when you already have a specific approach and want it stress-tested.

```
1. You've formed a plan during planning
2. Call `claudex_review` with your detailed plan
3. Evaluate Codex's critique:
   - Adopt suggestions that are genuinely better
   - Defer suggestions that are style preferences, not improvements
4. Present with attribution:
   "My plan is X. Codex flagged [risk] and suggested [alternative].
    I'm [adopting/deferring] because [reason]."
```

## Workflow 3: Brainstorming (Exploration Phase)

Use when the user describes a problem without a clear direction.

```
1. Call `claudex_brainstorm` with the topic and constraints
2. Review alongside your own thinking
3. Present a synthesized set of options with trade-offs
```

## Workflow 4: Collaboration (Interactive Problem-Solving)

Use when you need targeted help from Codex on a specific problem. Unlike the other workflows which are one-shot consultations, this is designed for you to share your analysis and get focused suggestions back.

```
1. Analyze the problem yourself first — form your own understanding
2. Determine what you need from Codex:
   - feature_suggestion: You need feature ideas or implementation approaches
   - bug_approach: You need help debugging or identifying root causes
   - code_critique: You want Codex to review your proposed solution
   - red_team: You want Codex to challenge every assumption and find weaknesses
   - verification: You want Codex to independently verify correctness
   - testing_strategy: You want Codex to suggest what and how to test
   - general: Open-ended analysis and suggestions
3. Call `claudex_collab` with:
   - problem: What you're trying to solve
   - cc_analysis: Your findings and current thinking (be honest about uncertainties)
   - request_type: The type from above
   - files_involved: Key files related to this problem
   - project_dir: The project root
4. Evaluate Codex's response:
   - Did it find something you missed? Investigate the specific files/lines
   - Does it disagree? Compare evidence, adopt what's stronger
   - Did it suggest next steps? Evaluate feasibility and priority
5. Present a unified analysis with clear attribution
```

**When to use `/claudex:collab` vs other tools:**
- Use `collab` when you already have partial analysis and need a second brain
- Use `claudex_plan` when starting fresh and want independent plans
- Use `claudex_review` when your plan is complete and needs stress-testing
- Use `claudex_brainstorm` when exploring open-ended problems without direction

## Reading Codex Artifacts

Codex may produce file artifacts (code snippets, tests, verification scripts, analysis docs) that the server writes to `.claudex/run-<uuid>/`. When Codex's output includes an **"Artifacts Created"** section:

1. **Read every artifact file** listed — they contain the code/evidence Codex references in its text
2. Use the exact paths shown (e.g. `.claudex/run-abc123/proposed_handler.py`)
3. Evaluate artifact contents with the same critical eye you apply to Codex's text output
4. Reference specific artifact files when presenting findings to the user

Artifacts are read-only evidence — Codex cannot write to your codebase. The server extracts artifacts from Codex's output and writes them to an isolated per-run directory.

## Critical Rules

### ALWAYS:
- Pass `project_dir` so Codex reads the correct codebase
- Be specific in plan/task descriptions — vague input = vague output
- Include constraints (perf requirements, deadlines, backward compat)
- Use `focus_files` to direct Codex's attention to relevant code
- Critically evaluate Codex's output — it's a perspective, not authority
- Show the user WHERE ideas came from (CC vs Codex) for transparency
- Note when Codex's reasoning chain (from `model_reasoning_summary=detailed`) reveals important context about WHY it suggests something

### NEVER:
- Pass credentials, API keys, or secrets in prompts
- Call Codex for trivial changes (rate limits are real)
- Blindly adopt everything Codex says without critical evaluation
- Call repeatedly on the same topic without new information
- Ignore Codex's output — if you called it, use the result

### Evaluating Disagreements:
When Codex disagrees with your approach:
1. Did it read a file you didn't consider? → Investigate
2. Is this architectural or just style? → Only adopt architectural improvements
3. Does it align with the project's existing patterns? → Prefer consistency
4. Would the user benefit from seeing both options? → Present both

## Model Selection

| Model | Best For | Speed |
|-------|----------|-------|
| gpt-5.3-codex (default) | Architecture, complex plans, deep analysis | ~30-60s |
| gpt-5.2-codex | Previous flagship, still excellent | ~30-60s |
| gpt-5.1-codex | Long-horizon tasks | ~30-60s |
| gpt-5-codex | Original Codex variant | ~30-60s |
| gpt-5-codex-mini | Quick checks, cost-effective | ~15-30s |

Default is **gpt-5.3-codex** with **xhigh** (extra high) reasoning effort for maximum depth.

## Rate Limit Awareness

Codex uses the user's ChatGPT subscription quota:
- **Plus ($20/mo)**: ~30-150 messages per 5-hour window
- **Pro ($200/mo)**: ~300-1,500 messages per 5-hour window
- Each tool call = 1 message from quota
- If rate-limited: tell the user, suggest waiting or using gpt-5-codex-mini

## Example: Full Parallel Planning Session

```
User: "Add a webhook system to our Express API so clients can subscribe
       to events like order.created, order.shipped, etc."

YOU (internal):
  1. Form your plan: event registry, webhook model, async dispatch queue...
  2. Call claudex_plan(
       task="Add a webhook system where API clients can subscribe to events
             (order.created, order.shipped, etc.) and receive HTTP POST
             notifications with retry logic",
       constraints="Express.js, PostgreSQL, deployed on Railway, ~50 concurrent users",
       focus_files="src/routes/,src/models/,src/services/",
       project_dir="/home/user/my-api"
     )
  3. Compare plans → Codex suggested using pg LISTEN/NOTIFY for event
     dispatch instead of your polling approach. Good catch.
  4. Merge: adopt pg LISTEN/NOTIFY, keep your retry logic design.

YOU (to user):
  "Here's the plan. Both CC and Codex converged on a webhook_subscriptions
   table with event filtering. For event dispatch, Codex suggested using
   PostgreSQL LISTEN/NOTIFY instead of a polling queue — I'm adopting that
   because it's lower latency and you're already on PostgreSQL. I'm keeping
   my exponential backoff retry design since Codex's linear retry would be
   less resilient for flaky endpoints."
```

---
name: codex
description: "Use when entering plan mode for non-trivial tasks, when making architecture decisions, when debugging complex issues, when the user asks for a second opinion, when you need to verify correctness, or when collaborating with Codex. Auto-triggers during plan mode for complex implementations."
---

# Codex — Claude Code Skill

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
│   ├─ Want independent plans to compare → codex_plan
│   └─ Want to explore options broadly   → codex_brainstorm
│
├─ "I have a plan already"
│   └─ Want it stress-tested/critiqued   → codex_review
│
├─ "I have a specific problem"
│   ├─ Need feature ideas               → codex_collab (feature_suggestion)
│   ├─ Need debugging help              → codex_collab (bug_approach)
│   ├─ Want my code critiqued           → codex_collab (code_critique)
│   ├─ Want assumptions challenged      → codex_collab (red_team)
│   ├─ Want correctness verified        → codex_collab (verification)
│   └─ Want testing guidance            → codex_collab (testing_strategy)
│
├─ "I want specific files reviewed"
│   └─ Targeted code review             → codex_review_files
│
└─ "Is Codex even working?"
    └─ Connection test                   → codex_ping
```

## Available Tools

| Tool | When | What It Does |
|------|------|-------------|
| `codex_plan` | **You have a task, want both plans** | Codex generates its OWN independent plan. You compare and synthesize. |
| `codex_review` | **You have YOUR plan, want it critiqued** | Codex reviews and stress-tests your specific approach. |
| `codex_brainstorm` | **No plan yet, exploring options** | Open-ended exploration of the problem space. |
| `codex_collab` | **You need help solving a specific problem** | Send your analysis + a request type, get targeted suggestions back. |
| `codex_review_files` | **Want specific files reviewed** | Targeted code review from a different angle. |
| `codex_ping` | **Setup/debug** | Verify Codex CLI is installed and working. |

## Workflow 1: Divergent — Parallel Planning & Brainstorming

Use when the user describes a task/feature and you need independent perspectives.
The key principle is **Immediate Verbatim Dispatch**: send the raw user prompt
to Codex without interpretation, so both models think independently.

```
1. User describes a task/feature
2. **Immediately** call `codex_plan` or `codex_brainstorm` with:
   - user_prompt: The user's EXACT words (verbatim, do NOT rephrase)
   - task/topic: Factual grounding only — tech stack, relevant files, constraints
   - Do NOT include your interpretation of HOW to solve it
3. **While waiting**, formulate YOUR plan/ideas independently
4. Compare the two perspectives:
   - Agreement → High confidence (independent convergence)
   - Disagreement → Examine why, adopt what's stronger
   - Codex found something you missed → Investigate (potential blind spot)
   - You found something Codex missed → Keep (you have conversation context)
5. Synthesize with clear attribution:
   "Both CC and Codex agree on X.
    Codex suggested Y — adopting because [reason].
    Keeping my approach for Z because [reason]."
```

## Workflow 2: Convergent — Review & Verification

Use when you have code or a plan that needs quality assurance.
The key principle is the **Double Strainer**: you self-review first (coarse filter),
then Codex reviews the polished output (fine filter).

```
1. Complete your implementation or plan
2. **Self-review first**: Check for obvious issues, edge cases, style
3. Fix anything you find — send Codex a clean version
4. Call `codex_review` or `codex_review_files` with the polished output
5. Evaluate Codex's critique:
   - Adopt suggestions that are genuinely better
   - Defer suggestions that are style preferences
   - Investigate anything Codex found that you missed in self-review
6. Present with attribution:
   "My plan is X. After self-review, I fixed [Y].
    Codex additionally flagged [risk] and suggested [alternative].
    Adopting/deferring because [reason]."
```

## Workflow 3: Iterative — Debugging & Problem-Solving

Use when debugging tricky issues or solving complex problems.
The key principle is **Accumulated Context**: each round builds on previous findings.
Claude stays the arbitrator — decides which hypotheses to test.

```
1. Analyze the problem yourself first
2. Call `codex_collab` with:
   - problem: What you're trying to solve
   - cc_analysis: Your findings and current thinking
   - request_type: bug_approach, red_team, verification, etc.
3. Evaluate Codex's response. Test its suggestions.
4. **If you need another round**, call `codex_collab` again with:
   - problem: Updated with new findings
   - cc_analysis: Include what Codex suggested + what you tried + results
   - This creates a shared understanding across rounds
5. Present unified analysis with clear attribution
```

**When to use which workflow:**
- **Divergent** (`codex_plan`, `codex_brainstorm`): Starting fresh, want independent thinking
- **Convergent** (`codex_review`, `codex_review_files`): Have a plan/code, want it stress-tested
- **Iterative** (`codex_collab`): Solving a specific problem, may need multiple rounds

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

Default is **gpt-5.3-codex** with **high** reasoning effort. Use `xhigh` for maximum depth when you have time to wait.

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
  1. **Immediately** dispatch to Codex with the raw user prompt:
     Call codex_plan(
       user_prompt="Add a webhook system to our Express API so clients can
                    subscribe to events like order.created, order.shipped, etc.",
       task="Express.js API on PostgreSQL, deployed on Railway, ~50 concurrent
             users. Relevant dirs: src/routes/, src/models/, src/services/",
       constraints="Express.js, PostgreSQL, deployed on Railway, ~50 concurrent users",
       focus_files="src/routes/,src/models/,src/services/",
       project_dir="/home/user/my-api"
     )
  2. **While waiting**, form YOUR plan: event registry, webhook model, async dispatch queue...
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

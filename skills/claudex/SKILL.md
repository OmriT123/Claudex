---
name: codex
description: "Use when entering plan mode for non-trivial tasks, when making architecture decisions, when debugging complex issues, when the user asks for a second opinion, when you need to verify correctness, or when evaluating tradeoffs between approaches. Auto-triggers during plan mode for complex implementations. Use for: auth changes, database schema, >100 LOC features, multi-file refactors, security-sensitive code."
---

# Codex — Claude Code Skill

## Purpose

Consult **OpenAI Codex as a specialist teammate** during planning, security-testing, debugging, verification, and decision-making. Codex runs in a **read-only sandbox** on the same codebase — a different AI architecture (GPT/Codex) that catches different patterns, risks, and opportunities. This creates multi-perspective collaboration that stress-tests your work before you commit.

**Codex always explores the codebase directly.** It has full read access to the project. You don't need to pre-digest context — Codex reads files, understands patterns, and forms its own mental model. Your job is to point it in the right direction with `focus_files` and constraints, not to summarize what you've already seen.

**Cost note:** Each tool call = 1 message from the user's ChatGPT quota. Don't call Codex for tasks you're confident about.

**Skip Codex when:** config-only changes, documentation edits, trivial renames, formatting-only commits, dependency version bumps with no code change, or when you're confident about a low-risk change.

---

## Tool Router — Which Tool Do I Use?

```
What's your situation?
│
├─ "I have a task, no plan yet"
│   ├─ Want independent plans to compare  → codex_plan
│   └─ Want to explore options broadly    → codex_brainstorm
│
├─ "I have a plan or code already"
│   ├─ Want it stress-tested/critiqued    → codex_critique
│   ├─ Want specific files reviewed       → codex_review
│   └─ Want my git diff reviewed          → codex_review_diff
│
├─ "I have a specific problem"
│   ├─ Need debugging help               → codex_collab (bug_approach)
│   ├─ Want assumptions challenged        → codex_collab (red_team)
│   ├─ Want correctness verified          → codex_collab (verification)
│   ├─ Want testing guidance              → codex_collab (testing_strategy)
│   ├─ Want my code critiqued             → codex_collab (code_critique)
│   └─ Need feature ideas                → codex_collab (feature_suggestion)
│
├─ "I need to choose between approaches"
│   └─ Want tradeoff analysis for MY decision → codex_evaluate
│
├─ "We just finished a multi-round session"
│   └─ Want a decision record / summary   → codex_recap
│
├─ "What's the current state of Claudex?"
│   └─ Sessions, artifacts, disk usage    → codex_status (zero cost)
│
└─ "Is Codex even working?"
    └─ Connection test                    → codex_ping
```

**Auto-trigger guidance:** Use Codex whenever a second perspective would **materially improve** the output — architecture decisions, complex implementations, subtle bugs, security-sensitive code, or anything the user will deploy to production. When in doubt, use it.

After completing a multi-round collab session → suggest `codex_recap` to generate a decision record.

---

## Tools Reference

| Tool | Purpose | Codex Persona |
|------|---------|---------------|
| `codex_plan` | Codex generates its OWN independent plan. You compare and synthesize. | **Creative Architect** — explore broadly, challenge conventions, suggest alternatives |
| `codex_critique` | Codex critiques your specific plan or implementation. | **Critical QA Engineer** — find bugs, edge cases, security holes, perf issues |
| `codex_brainstorm` | Open-ended exploration of a problem space. | **Innovation Consultant** — divergent thinking, cross-domain connections |
| `codex_collab` | Targeted collaboration on a specific problem. Persona varies by `request_type`. | See Collab Personas below |
| `codex_review` | Targeted code review of specific files. | **Senior Code Reviewer** — patterns, anti-patterns, maintainability, correctness |
| `codex_review_diff` | Review git diff (staged or unstaged) for bugs and risks. | **Diff Reviewer** — focus on what changed, not pre-existing issues |
| `codex_evaluate` | Codex analyzes tradeoffs between options. User makes the final call. | **Technical Advisor** — balanced analysis, explicit tradeoffs, no recommendation bias |
| `codex_recap` | Generate a decision record summarizing a session. | **Technical Writer** — clear, concise, decision-focused documentation |
| `codex_status` | Show Claudex diagnostics (no Codex call, zero subscription cost). | N/A |
| `codex_ping` | Verify Codex CLI is installed and working. | N/A |

### Collab Personas (by `request_type`)

| Type | Persona | Behavioral Instruction |
|------|---------|----------------------|
| `bug_approach` | **Diagnostic Specialist** | Systematic hypothesis testing. Rank hypotheses by likelihood. Suggest targeted tests. |
| `red_team` | **Adversarial Researcher** | Assume everything can break. Find failure modes, attack vectors, race conditions. |
| `verification` | **Formal Methods Engineer** | Prove correctness. Check invariants, data flow, boundary conditions, error paths. |
| `testing_strategy` | **Test Architect** | Coverage analysis, boundary testing, integration points, test structure. |
| `code_critique` | **Senior Developer** | Patterns, readability, maintainability, performance, idiomatic usage. |
| `feature_suggestion` | **Product Engineer** | Feasibility, user impact, implementation complexity, integration risks. |
| `general` | **Collaborative Engineer** | Concrete analysis, add missed perspectives, evidence-based suggestions. |

---

## Server-Side Features (v1.5)

These are handled automatically by the server. Understand them so you use them correctly.

### `user_prompt` — Preserve the User's Voice

`codex_plan`, `codex_critique`, `codex_brainstorm`, `codex_collab`, `codex_review`, and `codex_review_diff` accept an optional `user_prompt` field. **Always pass it** — it ensures Codex responds to the user's actual intent, not just your interpretation.

| Context | What to pass as `user_prompt` |
|---------|-------------------------------|
| Independent planning (`codex_plan`, `codex_brainstorm`) | The user's current message, verbatim — Codex forms its OWN interpretation |
| Single-shot review (`codex_critique`, `codex_review`, `codex_review_diff`) | The user's current message, verbatim |
| Iterative session (`codex_collab` with `session_id`) | The ORIGINAL task description from session start — same across all rounds |

### File Path Auto-Normalization

`focus_files`, `files_involved`, and `files` are auto-normalized server-side: resolved relative to `project_dir`, non-existent paths silently dropped. **You do NOT need to verify file existence before passing paths.** Just pass what seems relevant.

### Git Context Injection

All analysis tools (`codex_plan`, `codex_critique`, `codex_brainstorm`, `codex_collab`, `codex_review`, `codex_evaluate`) automatically include the current git branch, `git diff --stat` (capped at 20 lines), recent commit log (`git log --oneline -5`), and staged diff summary in the prompt sent to Codex. **You do NOT need to manually include git state** — it's injected server-side. `codex_recap`, `codex_review_diff`, and `codex_ping` skip this (`codex_review_diff` gets its own full diff; recap summarizes a session; ping is a connectivity test). If the project isn't a git repo, this is silently skipped.

### Response Metadata Footer

Every successful Codex response includes a footer: `_Codex: {model}, {effort}, {elapsed}s_`. Reference this for transparency when reporting to the user, e.g.: "Codex (gpt-5.4, high, 45s) suggests..."

---

## Workflow 1: Divergent — Parallel Planning & Brainstorming

Use when the user describes a task/feature and you need independent perspectives.

```
1. User describes a task/feature
2. IMMEDIATELY call codex_plan or codex_brainstorm:
   - user_prompt: The user's EXACT words (preserve original intent for independent interpretation)
   - task/topic: Factual grounding — tech stack, relevant files, constraints
   - focus_files: Point Codex WHERE to explore (it reads them directly)
   - Do NOT include your interpretation of HOW to solve it
   NOTE: Codex will explore the codebase itself. You're pointing, not summarizing.
3. AFTER receiving Codex's response, compare with YOUR independently formulated plan/ideas
4. Compare using these criteria:
   - Novelty: Did Codex find an approach you didn't consider?
   - Feasibility: Is the suggestion practical given constraints?
   - Architecture alignment: Does it fit existing patterns?
   - Risk: What could go wrong with each approach?
5. Synthesize with CLEAR attribution:
   "Both Claude Code and Codex agree on X.
    Codex suggested Y — adopting because [reason].
    Keeping my approach for Z because [reason]."
```

**Key principle:** Send the raw prompt. Let Codex form its OWN understanding by reading the code. Two models looking at the same codebase independently = genuine second opinion.

## Workflow 2: Convergent — Review & Verification

Use when you have code or a plan that needs quality assurance.

```
1. Complete your implementation or plan
2. Self-review first: check for obvious issues, edge cases, style
3. Fix anything you find — send Codex a clean version
4. Call codex_critique or codex_review with the polished output
   - user_prompt: The user's EXACT current message (preserves original intent)
5. Evaluate Codex's critique:
   - Adopt: genuinely better suggestions (architecture, correctness, security)
   - Defer: style preferences or matters of taste
   - Investigate: anything Codex found that you missed in self-review
6. Present with attribution:
   "After self-review, I fixed [Y].
    Codex flagged [risk] and suggested [alternative].
    Adopting/deferring because [reason]."
```

**Key principle (Double Strainer):** Your self-review is the coarse filter. Codex is the orthogonal filter — it catches *different types* of issues, not necessarily subtler ones.

## Workflow 3: Iterative — Debugging & Problem-Solving

Use when debugging tricky issues or solving complex problems. Uses session documents for shared memory.

```
1. Analyze the problem yourself first
2. Call codex_collab with:
   - problem: What you're trying to solve
   - cc_analysis: Your findings, hypotheses, what you've tried
   - request_type: bug_approach, red_team, verification, etc.
   - session_id: Pass "auto" to auto-generate a descriptive ID, or a custom slug for the session
   - user_prompt: The ORIGINAL task description from when the session started.
     Use the same user_prompt across all rounds — gives Codex consistent framing.
3. Server writes Codex's response to .claudex/sessions/{session_id}.md
4. Read the session document. Test Codex's top suggestions.
   **Verification gate — MANDATORY before presenting to user:**
   Read every file and line Codex referenced. Confirm or refute each specific claim.
   Do NOT relay Codex's findings without first-hand verification. This is what makes
   the output trustworthy — two models agreeing after independent investigation,
   not one model echoing the other.
5. IF another round is needed, call codex_collab again:
   - Update cc_analysis with: what Codex suggested + what you tried + results
   - Same session_id (accumulates context across rounds)
6. TERMINATION: After 4 rounds, the server auto-rolls over — generates a recap,
   creates a chained session (e.g. my-session → my-session-p2), and continues.
   The rollover is transparent; you'll see a note in the response.
```

**Key principle:** The session document IS the shared memory. Each round builds on all previous findings. Claude Code stays the arbitrator — you decide which hypotheses to test.

### Session Document Format

The server automatically manages `.claudex/sessions/{session_id}.md`:

```markdown
# Session: {session_id}
Started: {timestamp}
<!-- claudex:rounds=0 -->

## Round 1
### CC Analysis
{cc_analysis from the call}

### Codex Response
{codex output}

### Test Results
{added by CC after testing — update the file before next round}

## Round 2
...
```

## Workflow 4: Evaluate — Decision Support

Use when choosing between approaches. This is the ONLY tool where Claude Code does NOT arbitrate — the user decides.

```
1. Frame the decision:
   - Options being considered (2-4)
   - Constraints and priorities
   - What you're optimizing for
2. Call codex_evaluate:
   - options: List of approaches with brief descriptions
   - constraints: Non-negotiable requirements
   - priorities: What matters most (performance? maintainability? speed to ship?)
   - focus_files: Relevant codebase context
3. Codex analyzes each option:
   - Tradeoffs (explicit pros/cons)
   - Risk profile
   - Implementation complexity
   - Long-term maintenance implications
4. Present BOTH your analysis and Codex's to the user
5. The user decides. You execute their choice.
```

**Key principle:** `codex_evaluate` presents options. It does NOT recommend. The user has context you and Codex don't (business priorities, team skills, timeline pressure).

## Workflow 5: Pre-Commit Review

Use before committing to catch issues in your changes.

```
1. Make your changes (staged or unstaged)
2. Call codex_review_diff with:
   - staged: true if reviewing staged changes, false for all working tree changes
   - focus: 'security', 'correctness', 'performance', or leave empty for general review
   - context: Brief description of what the changes are for
   - user_prompt: The user's request if applicable
3. Evaluate Codex's findings:
   - Critical issues → fix before committing
   - Warnings → assess if they need fixing now
   - Suggestions → defer to post-commit if minor
4. Present verdict to user: ship / fix first / needs discussion
```

**Key principle:** `codex_review_diff` focuses on what CHANGED — it doesn't critique pre-existing code. Fast and targeted.

## Chaining Workflows — Evidence-Gated Transitions

Tools are most powerful when chained. Each transition carries context forward.

### Chain 1: Plan → Stress-Test → Debug
```
codex_plan → codex_critique → codex_collab (if issues found)
```
1. Generate independent plan with `codex_plan`
2. Stress-test the synthesized plan with `codex_critique`
3. If review surfaces issues: open a `codex_collab` session (`verification` or `red_team`) to resolve them

### Chain 2: Review → Fix → Verify
```
codex_review / codex_review_diff → fix criticals → codex_collab (if conflict)
```
1. Get structured review findings
2. Investigate and fix all critical-severity findings before presenting
3. If Codex's findings conflict with your self-review: escalate to `codex_collab` with `request_type=verification`, include conflict evidence

### Chain 3: Explore → Decide
```
codex_brainstorm → codex_evaluate (when multiple options emerge)
```
1. Brainstorm broadly to surface options
2. If 2+ viable approaches emerge, call `codex_evaluate` with the options
3. Present both analyses to the user for their decision

### Context Carrying — Claim Ledger

When transitioning between tools, structure the context you carry forward in `cc_analysis` or `context`:

- **Prior Codex output**: What Codex said (claim, suggestion, or finding)
- **File evidence**: Specific file:line you checked
- **CC verification**: Confirmed / refuted / inconclusive — what you found
- **Open question**: What still needs resolving in this next tool call

This prevents context loss across tool boundaries without requiring server-side session sharing.

---

## Reading Codex Artifacts

Codex may produce file artifacts (code snippets, tests, analysis docs) written by the server to `.claudex/run-<uuid>/`. When output includes an **"Artifacts Created"** section:

1. **Read every artifact file** — they contain the evidence Codex references
2. Use exact paths shown (e.g., `.claudex/run-abc123/proposed_handler.py`)
3. Evaluate with the same critical eye you apply to Codex's text
4. Reference specific artifacts when presenting findings to the user

## Error Handling

If Codex fails (timeout, rate limit, empty response, error):
1. **Inform the user** — be specific about what happened
2. **Proceed with Claude Code-only analysis** — don't block on Codex
3. **Flag it** — note the output was NOT dual-verified
4. For rate limits: suggest waiting for the 5-hour window reset

---

## Critical Rules

### ALWAYS:
- Pass `project_dir` so Codex reads the correct codebase
- Pass `user_prompt` on every tool that accepts it (see v1.5 features above)
- Use `focus_files` to direct Codex's exploration — paths are auto-normalized, no need to verify existence
- Let Codex read the codebase directly — don't pre-summarize context for it
- Critically evaluate Codex's output — it's a perspective, not authority
- Show the user WHERE ideas came from (Claude Code vs Codex) for transparency
- Include the response metadata footer when attributing Codex findings to the user
- When chaining tools, carry forward prior Codex output using the Claim Ledger format (see Workflow Chains)

### NEVER:
- Pass credentials, API keys, or secrets in prompts
- Blindly adopt everything Codex says without critical evaluation
- Call repeatedly on the same topic without new information between rounds
- Ignore Codex's output — if you called it, use the result
- Pre-digest codebase context into summaries for Codex (let it read directly)
- Manually inject git state into prompts (the server does this automatically)
- Omit `user_prompt` when calling any tool that accepts it — Codex needs the user's voice

### Evaluating Disagreements:
When Codex disagrees with your approach:
1. Did it read a file you didn't consider? → Investigate
2. Is this architectural or just style? → Only adopt architectural improvements
3. Does it align with the project's existing patterns? → Prefer consistency
4. Would the user benefit from seeing both options? → Present both with tradeoffs

---

## Model & Reasoning

**Model:** `gpt-5.4` by default. Overridable per-call via `model` parameter on any tool.

**Reasoning effort:** `high` by default. Override with `reasoning_effort` parameter:
- `low` — minimal reasoning, fast
- `medium` — faster, good for reviews and recaps
- `high` — default, good for most analysis
- `xhigh` — maximum depth, slower but more thorough

**Auto-retry on timeout:** If a call times out and the effort level is `xhigh` or `high`, the server automatically retries once with a lower effort (`xhigh` → `high`, `high` → `medium`). A note is prepended to the response when this happens.

**Reasoning summary:** `detailed` by default. Overridable per-call (`detailed`, `concise`, `none`).

**Timeout:** 1200s (20 min) for all tools.

**Metrics:** The server tracks per-tool stats (calls, successes, timeouts, errors, avg latency) in memory. View via `codex_status`.

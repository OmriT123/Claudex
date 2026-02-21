---
name: collab
description: "Collaborate with Codex — red-team, debug, verify, review code, suggest features, or get an independent analysis"
argument-hint: "[problem description]"
allowed-tools: Read, Glob, Grep
---

# Collaborate with Codex

**Problem**: $ARGUMENTS

## Your workflow:

1. **Analyze the problem yourself first**: Read relevant files, form your own understanding
2. **Determine what you need from Codex** — pick the most relevant request type:
   - `feature_suggestion` — you need feature ideas or implementation approaches
   - `bug_approach` — you need help debugging or identifying root causes
   - `code_critique` — you want Codex to review your proposed solution/methods
   - `red_team` — you want Codex to challenge assumptions and find weaknesses
   - `verification` — you want Codex to independently verify correctness
   - `testing_strategy` — you want Codex to suggest what and how to test
   - `general` — open-ended analysis and suggestions
3. **Call `codex_collab`** with:
   - problem: The problem description above
   - cc_analysis: Your own findings and current thinking (be honest about what you're unsure of)
   - request_type: The type from step 2
   - user_prompt: The user's exact words (copy "$ARGUMENTS" verbatim — do NOT rephrase)
   - session_id: A short descriptive slug for this problem (e.g. "fix-race-condition") — enables multi-round shared memory
   - files_involved: Key files related to this problem
   - project_dir: The current project root
4. **Evaluate Codex's response**:
   - Did Codex find something you missed? → Investigate the specific files/lines it references
   - Does Codex disagree with your approach? → Compare evidence, adopt what's stronger
   - Did Codex suggest concrete next steps? → Evaluate feasibility and priority
5. **Verify before presenting**: Before showing Codex's findings to the user:
   - Read every file/line Codex referenced — confirm or refute each claim
   - Test any specific hypothesis Codex proposed (run the code, check the logic)
   - Record what you verified and what you found
6. **Present a unified analysis** to the user:
   - Your findings + Codex's findings, clearly attributed
   - Where you agree and where you differ
   - Recommended next steps with rationale
7. **Follow up with Codex** (call `codex_collab` again, same `session_id`, same `user_prompt` as round 1) when ANY of these triggers fire:
   - Codex referenced files/lines you haven't inspected yet → investigate first, then report back
   - Codex's claim contradicts your analysis or test results → include both sides + evidence
   - A proposed fix touches a high-risk path (auth, data integrity, concurrency) → verify with `verification` request_type
   - Root cause is still unresolved after your first validation attempt
   - Update `cc_analysis` with: what Codex suggested + what you verified + results + open questions
8. **Stop iterating** when:
   - All critical claims are verified and findings are actionable
   - You and Codex converge on the same conclusion
   - The session hits 4 rounds (auto-rollover generates a recap)
   - The problem has rolled over to a second chained session without convergence — present all findings and let the user decide
   - Use `codex_recap` to generate a decision record for multi-round sessions

---
name: review-diff
description: "Get Codex to review your git diff — staged or unstaged changes"
argument-hint: "[security | performance | correctness | all]"
allowed-tools: Read, Glob, Grep
---

# Diff Review with Codex

**Focus**: $ARGUMENTS

## Your workflow:

1. **Check git state**: Run `git status` to understand what's changed.
   If there are NO staged or unstaged changes, tell the user there's nothing
   to review and stop — do NOT call `codex_review_diff` with an empty diff.
2. **Review the diff yourself first**: Note issues you see in the changes
3. **Call `codex_review_diff`** with:
   - user_prompt: The user's exact words (copy "$ARGUMENTS" verbatim — do NOT rephrase)
   - focus: The focus area from above (or infer: security, performance, correctness)
   - staged: Set to true if the user wants to review only staged changes
   - context: What these changes are for (from conversation context)
   - project_dir: The current project root
4. **Evaluate Codex's review**:
   - Focus on issues INTRODUCED by the diff, not pre-existing problems
   - Verify Codex's findings against the actual diff
5. **Act on critical findings**: Before presenting to the user:
   - For each critical/warning finding: verify against the actual diff — confirm the issue was introduced by these changes
   - Fix confirmed critical issues in the working tree
   - If a fix touches auth, crypto, data integrity, or concurrency code → do NOT auto-fix; present the finding with a proposed fix for user approval
   - If Codex flags something as `fix_first` but you disagree, investigate and note both perspectives
6. **Escalate conflicts**: If Codex's verdict conflicts with your assessment:
   - Call `codex_collab` with `request_type=verification` or `bug_approach`
   - Include the specific diff hunks and both analyses in `cc_analysis`
   - If the escalation call fails or is inconclusive: present both perspectives to the user and let them decide
7. **Present findings** to the user:
   - Your findings + Codex's findings, clearly attributed
   - Final verdict: ship / fix first / needs discussion
   - Note any findings you verified vs unverified Codex claims

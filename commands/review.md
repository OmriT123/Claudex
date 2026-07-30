---
name: review
description: "Get a focused code review from Codex on specific files"
argument-hint: "[file1, file2, ...]"
allowed-tools: Read, Glob, Grep, Bash(git diff:*), mcp__plugin_codex_codex__codex_review, mcp__plugin_codex_codex__codex_collab
---

# Code Review with Codex

**Files**: $ARGUMENTS

## Pre-check
If no files are specified above (empty or blank arguments):
1. Check `git diff --name-only` for recently modified files
2. If none, check `git diff --staged --name-only`
3. If still none, ask the user which files they want reviewed
4. Present the discovered files and confirm with the user before proceeding

## Your workflow:

1. **Read the files yourself first**: Understand what you're sending for review
2. **Self-review**: Note any issues you already see — fix obvious ones first
3. **Call `codex_review`** with:
   - files: The files listed above (comma-separated paths)
   - user_prompt: The user's exact words (copy "$ARGUMENTS" verbatim — do NOT rephrase)
   - focus: What to focus on (security, performance, correctness, maintainability, or all)
   - project_dir: The current project root
4. **Evaluate Codex's review**:
   - Investigate issues Codex found that you missed
   - Verify Codex's suggestions are correct (check referenced line numbers)
   - Separate genuine issues from style preferences
5. **Verify critical findings**: Before presenting to the user:
   - For each critical/warning finding: read the referenced file and line, confirm the issue exists
   - Fix confirmed critical issues yourself — present the fix alongside the finding
   - If a fix touches auth, crypto, data integrity, or concurrency code → do NOT auto-fix; present the finding with a proposed fix for user approval
   - If a finding conflicts with your self-review, note both perspectives with evidence
6. **Escalate unresolved conflicts**: If Codex and your self-review disagree on something important:
   - Call `codex_collab` with `request_type=verification`
   - Include both your finding and Codex's finding in `cc_analysis`
   - If the escalation call fails or is inconclusive: present both perspectives to the user and let them decide
7. **Present a unified review** to the user:
   - Issues you found + issues Codex found, clearly attributed
   - Mark which findings you verified vs which are unverified Codex claims
   - Prioritized: critical → warnings → suggestions

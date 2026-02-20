---
name: review-files
description: "Get a focused code review from Codex on specific files"
argument-hint: "[file1, file2, ...]"
allowed-tools: Read, Glob, Grep
---

# Code Review with Codex

**Files**: $ARGUMENTS

## Your workflow:

1. **Read the files yourself first**: Understand what you're sending for review
2. **Self-review**: Note any issues you already see — fix obvious ones first
3. **Call `codex_review_files`** with:
   - files: The files listed above (comma-separated paths)
   - user_prompt: The user's exact words (copy "$ARGUMENTS" verbatim — do NOT rephrase)
   - focus: What to focus on (security, performance, correctness, maintainability, or all)
   - project_dir: The current project root
4. **Evaluate Codex's review**:
   - Investigate issues Codex found that you missed
   - Verify Codex's suggestions are correct (check referenced line numbers)
   - Separate genuine issues from style preferences
5. **Present a unified review** to the user:
   - Issues you found + issues Codex found, clearly attributed
   - Prioritized: critical → warnings → suggestions

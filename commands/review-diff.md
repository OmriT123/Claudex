---
name: review-diff
description: "Get Codex to review your git diff — staged or unstaged changes"
argument-hint: "[focus area]"
allowed-tools: Read, Glob, Grep
---

# Diff Review with Codex

**Focus**: $ARGUMENTS

## Your workflow:

1. **Check git state**: Run `git status` to understand what's changed
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
5. **Present findings** to the user:
   - Your findings + Codex's findings, clearly attributed
   - Verdict: ship / fix first / needs discussion

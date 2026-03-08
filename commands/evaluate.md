---
name: evaluate
description: "Evaluate tradeoffs between approaches — Codex analyzes options so you can decide"
argument-hint: "[Option A vs Option B]"
allowed-tools: Read, Glob, Grep
---

# Evaluate with Codex

**Decision**: $ARGUMENTS

## Pre-check
If the arguments above do NOT contain at least 2 distinct options to compare
(e.g., "Redis vs PostgreSQL" or "Option A: X, Option B: Y"):
1. Read relevant code to understand the decision space
2. Identify 2-3 viable options from the codebase and context
3. Present the options to the user and ask them to confirm before proceeding
4. Once confirmed, continue with the workflow below using the identified options

## Your workflow:

1. **Understand the options**: Read relevant code to understand what's being compared
2. **Form your OWN analysis first**: Think through each option's tradeoffs independently
3. **Call `codex_evaluate`** with:
   - options: Clear description of each approach being considered
   - constraints: Non-negotiable requirements you've identified
   - priorities: What the user is optimizing for (ask if unclear)
   - context: Why this decision matters, what's driving it
   - focus_files: Key files Codex should read for codebase context
   - project_dir: The current project root
4. **Present BOTH analyses** to the user:
   - Your analysis of each option's tradeoffs
   - Codex's analysis with clear attribution
   - Where you agree and where you differ
5. **Let the user decide** — do NOT recommend. Present the tradeoffs and let them choose.

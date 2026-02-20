---
name: evaluate
description: "Evaluate tradeoffs between approaches — Codex analyzes options so you can decide"
argument-hint: "[options to evaluate]"
allowed-tools: Read, Glob, Grep
---

# Evaluate with Codex

**Decision**: $ARGUMENTS

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

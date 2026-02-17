---
name: plan
description: "Generate a parallel plan — CC and Claudex independently plan the same task, then CC synthesizes the best of both"
argument-hint: "[task description]"
allowed-tools: Read, Glob, Grep
---

# Parallel Planning with Claudex

**Task**: $ARGUMENTS

## Your workflow:

1. **Understand the task**: Read relevant files to understand the codebase context
2. **Form YOUR plan first**: Think through the implementation approach independently — do NOT present it yet
3. **Call `claudex_plan`** with:
   - user_prompt: The user's exact words (copy "$ARGUMENTS" verbatim — do NOT rephrase)
   - task: Factual context only — tech stack, relevant files, constraints you identified
   - project_dir: The current project root
   - focus_files: Key files relevant to this task
   - constraints: Hard constraints you've identified from the codebase
4. **Compare both plans**:
   - Where you AGREE → High confidence (independent convergence)
   - Where you DIFFER → Examine why — adopt what's stronger, defer what's weaker
   - What Codex caught that you missed → Investigate and potentially adopt
   - What you caught that Codex missed → Keep (you have conversation context advantage)
5. **Present the synthesized plan** with clear attribution:
   - "Both CC and Codex agree on..."
   - "Codex suggested X — adopting because..."
   - "Keeping my approach for Y because..."

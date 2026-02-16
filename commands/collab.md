---
name: collab
description: "Collaborate with Claudex to solve a problem — red-team, debug, verify, review code, suggest features, or get an independent analysis"
argument-hint: "[problem description]"
allowed-tools: Read, Glob, Grep
---

# Collaborate with Claudex

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
3. **Call `claudex_collab`** with:
   - problem: The problem description above
   - cc_analysis: Your own findings and current thinking (be honest about what you're unsure of)
   - request_type: The type from step 2
   - files_involved: Key files related to this problem
   - project_dir: The current project root
4. **Evaluate Codex's response**:
   - Did Codex find something you missed? → Investigate the specific files/lines it references
   - Does Codex disagree with your approach? → Compare evidence, adopt what's stronger
   - Did Codex suggest concrete next steps? → Evaluate feasibility and priority
5. **Present a unified analysis** to the user:
   - Your findings + Codex's findings, clearly attributed
   - Where you agree and where you differ
   - Recommended next steps with rationale

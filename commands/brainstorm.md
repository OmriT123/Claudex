---
name: brainstorm
description: "Brainstorm with Claudex — explore approaches to a problem from two AI perspectives"
argument-hint: "[topic or problem]"
allowed-tools: Read, Glob, Grep
---

# Brainstorm with Claudex

**Topic**: $ARGUMENTS

## Your workflow:

1. **Understand the problem**: Read relevant code to build context
2. **Call `claudex_brainstorm`** with:
   - topic: The topic above
   - context: Your understanding of constraints and requirements
   - project_dir: The current project root
3. **Synthesize**: Combine Codex's ideas with your own thinking
4. **Present 2-3 approaches** with trade-offs, marking which ideas came from which source

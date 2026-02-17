---
name: brainstorm
description: "Brainstorm with Codex — explore approaches to a problem from two AI perspectives"
argument-hint: "[topic or problem]"
allowed-tools: Read, Glob, Grep
---

# Brainstorm with Codex

**Topic**: $ARGUMENTS

## Your workflow:

1. **Understand the problem**: Read relevant code to build context
2. **Call `codex_brainstorm`** with:
   - user_prompt: The user's exact words (copy "$ARGUMENTS" verbatim — do NOT rephrase)
   - topic: Your understanding of the core question (factual grounding only)
   - context: Technical constraints and relevant files
   - project_dir: The current project root
3. **Synthesize**: Combine Codex's ideas with your own thinking
4. **Present 2-3 approaches** with trade-offs, marking which ideas came from which source

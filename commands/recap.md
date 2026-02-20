---
name: recap
description: "Generate a decision record from a Codex collaboration session"
argument-hint: "[session_id]"
allowed-tools: Read, Glob
---

# Recap a Session

**Session**: $ARGUMENTS

## Your workflow:

1. **Verify the session exists**: Check `.claudex/sessions/` for the session document
2. **Read the session document** to understand what was discussed
3. **Call `codex_recap`** with:
   - session_id: The session ID from above
   - additional_context: Any final outcomes or decisions made after the last round
   - project_dir: The current project root
4. **Present the decision record** to the user
5. **Note**: The recap is saved to `.claudex/recaps/{session_id}_recap.md`

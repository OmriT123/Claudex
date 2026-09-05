---
name: help
description: "Quick guide to Claudex — what it does, available commands, and how to get started"
argument-hint: ""
allowed-tools: []
---

# Claudex — Quick Start

Claudex gives Claude Code a Codex-powered teammate. Two AI architectures
collaborate on your codebase — planning, reviewing, debugging, and more.
Codex runs in a read-only sandbox and never modifies your files.

## Commands

| Command | What It Does | Cost |
|---------|-------------|------|
| `/codex:plan [task]` | Claude and Codex independently plan, then synthesize | 1 msg |
| `/codex:brainstorm [topic]` | Explore approaches from two AI perspectives | 1 msg |
| `/codex:collab [problem]` | Targeted collaboration — debug, security-test, verify | 1 msg |
| `/codex:evaluate [A vs B]` | Tradeoff analysis — you decide | 1 msg |
| `/codex:review [files]` | Focused code review on specific files | 1 msg |
| `/codex:review-diff [focus]` | Review your git diff before committing | 1 msg |
| `/codex:recap [session_id]` | Generate decision record from a session | 1 msg |
| `/codex:status` | Diagnostics dashboard | free |
| `/codex:help` | This guide | free |
| `/codex:doctor` | Diagnose & fix issues | free |

## Get Started

0. Set your allowed project folders first — Claudex is deny-by-default (v2.0):
   see README → "Workspace confinement (required)"
1. Run `/codex:status` to verify everything is connected
2. Try `/codex:plan [describe your task]` for your first collaboration
3. Each tool call = 1 message from your ChatGPT subscription quota

## Tips

- Codex reads your codebase directly — no need to paste code
- Use `focus_files` to point Codex at relevant files
- Claude always verifies Codex's claims before presenting them to you
- Depth is `high` on every tool by default; reserve `xhigh`/`max` for hard architectural decisions

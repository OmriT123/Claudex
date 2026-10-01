# Claudex privacy and data handling

Claudex is a local plugin. It runs on your computer (or in your Claude Code
cloud session) and has no server of its own: Botique AI Solutions receives no
data, telemetry or usage information from it.

## What leaves your computer, and to whom

Claudex starts OpenAI's **Codex CLI** on your machine. Codex sends data to
**OpenAI** under **your own Codex sign-in** (your ChatGPT account, or the
`CODEX_API_KEY` you set). OpenAI's terms and data policies for that account
apply. On each Codex-calling tool (`codex_plan`, `codex_critique`,
`codex_brainstorm`, `codex_collab`, `codex_review`, `codex_review_diff`,
`codex_evaluate`, `codex_recap`, and `codex_ping` with `model_test=true`),
Codex receives:

- the instructions Claudex builds for that tool (persona and rules);
- what Claude passes in: the task, plan, problem, analysis, options or
  context, and `user_prompt` (your message, when it is within the scope you
  allowed);
- for `codex_collab`: earlier rounds of the same session document;
- git context from the project: current branch, `git diff --stat` (up to 20
  lines), the last 5 commit subjects and the staged diff summary; for
  `codex_review_diff`, the full diff and the names of untracked files;
- **the contents of any files Codex chooses to read** inside the project
  directory. Codex runs read-only, but it can read files your user account can
  read, and what it reads is sent to OpenAI as part of its work.

Claudex switches off Codex's analytics, plugin and app features, so the model
endpoint is the only host Codex contacts for this work. `codex_login` contacts
OpenAI's sign-in service. The Claudex server itself makes no network calls.

**Before you use Claudex on a project, decide whether you are allowed to share
that code and context with OpenAI.** Don't point it at folders holding secrets
or other people's confidential material you may not share.

## What is stored, and for how long

In each project, under `.claudex/` (git-ignored: Claudex creates
`.claudex/.gitignore` with `*`, never overwrites one a repository already has,
and `codex_status` warns when that file does not ignore everything):

| Folder | Content | Kept |
|---|---|---|
| `run-<id>/` | Files Codex proposed (artifacts) | about 1 hour |
| `sessions/` | `codex_collab` session documents (Claude's analysis and Codex's replies) | about 24 hours |
| `recaps/` | Decision records from `codex_recap` | about 24 hours |
| `jobs/` | Results of background jobs (`codex_submit`) | about 24 hours |

Cleanup is opportunistic: it runs when a later text-mode Codex call starts, so
files can stay longer if Claudex isn't used again in that project. Delete
`.claudex/` at any time.

Per user, outside any project:

- `quota.db`: a daily count of Codex executions (no prompts, code or results);
- `config.json`: the folders you allowed with `--configure-roots`.

Both live in `~/Library/Application Support/Botique/Claudex/` on macOS
(`quota.db` may instead sit in Claude Code's plugin data folder, or in
`CLAUDEX_STATE_DIR` if you set it).

## Installing

On first start, `uv` downloads Python (if needed) and the server's packages
from PyPI, pinned to the versions in `server/server.py.lock`.

## Contact

Questions or a security report: hello@botique.co.il, or open an issue at
https://github.com/OmriT123/Claudex/issues.

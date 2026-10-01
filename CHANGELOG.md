# Changelog

All notable changes to Claudex. Versions follow the plugin manifest.

## 2.4.0 (2026-10-01) - folder setup for every Claude app, locked dependencies

**Before updating: run `uv self update`.** Claudex now needs uv 0.11.4 or
newer and refuses to start on older uv ("Required uv version `>=0.11.4`").
If you updated first: run `uv self update`, then reconnect the `codex` server
from `/mcp` (Claude Code holds a failed start for about 15 minutes, and
neither a restart nor `/reload-plugins` clears it).

### Added
- **One folder setup for every Claude app on a computer**: a terminal command
  writes a per-user config file that every Claudex server reads (Claude Code
  plugin, claude.ai account plugin, desktop extension); no restart needed.
  `server.py --configure-roots <folder>...`, `--show-roots`, and
  `--revoke-roots`, a kill switch that denies every folder until you
  configure again. No MCP tool can change it, and it is never read from a
  project.
- `/codex:setup`: walks through choosing folders and checking readiness.
- `codex_status` shows the version, build id, distribution (plugin,
  extension, cloud), server file, roots source and config file state;
  deny-all messages name the cause and the exact fix.
- README section "What Claudex sends, runs and stores" and `PRIVACY.md`.

### Security
- The roots config file is found from the OS account (the password database
  on macOS and Linux, the shell's known folders on Windows), not from `HOME`
  or `APPDATA`, which a project's Claude Code settings could set for the
  server. Protected-folder checks cover both the account's home and `HOME`.
- The config file and its folder must not be symlinks and must belong to
  you, and no folder traversed on the way to it (through every symlink hop)
  may be writable by other accounts (in a sticky shared folder like /tmp,
  every entry traversed must be yours or root's, and a missing one denies),
  so nobody else can move a revocation away;
  reads are bounded and non-blocking. A symlinked, dangling, oversized or
  otherwise unusable file, or an unsafe folder chain, denies every folder
  instead of counting as absent. The terminal command writes it relative to
  the folder's descriptor (0600 file, 0700 folder). macOS and Linux; on
  Windows only the file itself is checked until the Windows port.
- Strict format: `version` must be the integer 1 and `deny_all` a boolean;
  `"true"`, `1` or a path with a NUL byte make the file unusable (deny-all)
  rather than silently re-enabling other sources.
- `codex_result` re-checks the job's project against the current roots,
  before and after waiting, so revoking or narrowing them also withholds
  results already in memory and hides their paths in `job_id='list'`.

### Changed
- **Locked dependencies**: `server/server.py.lock` ships with the server and
  every launcher runs `uv run --locked --script` (plugin, desktop extension,
  installer, cloud setup). The desktop extension bundle carries the lockfile,
  and its build fails without it or when manifest validation fails.
- Roots precedence (first match): revoke, `--allowed-roots`,
  `CLAUDEX_ALLOWED_ROOTS`, config file, cloud default.
  An unusable value in the selected source denies everything.
- **The skill consults Codex only when you ask for it or have given standing
  permission** (for example in CLAUDE.md or your account instructions), and
  says once per conversation that consulting sends context to OpenAI. Your
  message is forwarded as `user_prompt` when it is within the permitted scope.
  To keep Codex as an always-on peer, add a line like this to your CLAUDE.md:
  "Codex (Claudex) is a standing independent peer: consult it on non-trivial
  technical work without asking."
- `install.sh` checks uv's version and prints the `--configure-roots` command.
- Prompts label the forwarded user request "as forwarded by Claude" (it may
  be an approved, task-specific version), not "verbatim".
- `codex_status` warns when `.claudex/.gitignore` is missing or has any rule
  other than `*`, read with git's own whitespace and comment rules (a
  repository can bring its own, which Claudex never overwrites).

### Upgrading
- Run `uv self update` first (all surfaces).
- Existing `CLAUDEX_ALLOWED_ROOTS` and desktop-extension folder choices keep
  working and take precedence over the new config file.
- Desktop extension: install `claudex.mcpb` 2.4.0, replacing the old one.

## 2.3.1 (2026-10-01) - security fixes

Update now. These fix a file-deletion bug in all earlier versions, including
the `.mcpb` desktop extension.

### Security
- **Cleanup could delete files outside your project.** If a repository
  contained `.claudex/sessions`, `.claudex/recaps` or `.claudex/jobs` as a
  symlink to another directory, the routine cleanup that runs after a
  successful text-mode Codex call (plan, critique, brainstorm, collab,
  evaluate, recap) followed it and deleted files older than 24 hours in that
  directory. Cleanup now opens `.claudex` and each subdirectory with
  `O_NOFOLLOW` and deletes relative to the directory's file descriptor, so a
  symlinked (or mid-cleanup swapped) directory is skipped.
- **Every operation inside `.claudex/` is anchored the same way**: session,
  recap and job reads and writes, artifact files, run-directory creation and
  removal, and the `.gitignore` Claudex writes. The project directory is opened
  once and `.claudex`, its subdirectory and the file are each opened relative
  to their parent with `O_NOFOLLOW`, so a symlink there, committed in the repo
  or swapped in while a call runs, can no longer redirect a read, write or
  delete. Paths are no longer `resolve()`d (resolving followed a swapped
  `.claudex`). `codex_collab` also re-validates its session path after the
  model call and reports when it could not update the session document.
- **Only regular files**: FIFOs and device files inside `.claudex/` are
  refused, and files are opened non-blocking, so a planted FIFO can no longer
  freeze the server (for example when `codex_result` reads a job file).
- Job records are written atomically with `renameat` on the jobs directory
  (Linux and macOS). The earlier fd-based writer never ran, because
  `os.replace` is never listed in `os.supports_dir_fd`.
- `_safe_claudex_path` checks the final path component before anything else
  (the old post-resolve symlink check could never fire).
- `codex_status` inventories `.claudex/` through the same anchored walk and
  never follows symlinks.
- A POSIX system without the fd-relative file calls now refuses `.claudex/`
  operations instead of falling back to path-based I/O (the fallback remains
  on Windows only).
- Session content cannot redirect a session: text from Claude, Codex or a
  recap is neutralized before it is stored, and only the server's own marker
  on the document's last line continues a session elsewhere.

### Fixed
- `codex_review_diff` reports git failures (not a repository, git missing,
  timeout, untracked-file listing failed, HEAD unresolvable) as errors.
  Previously they read as "No changes found. Nothing to review." A repository
  with no commits yet is still reviewed (attested as "no commits yet").
- Timed-out git commands, and Codex runs, are killed with their whole process
  group (a repository's filter programs included), also when the group's
  leader already exited and a background child still holds the output open.
- `codex_collab` keeps an existing session's content as context even when its
  round counter is missing. A rollover now carries the decisions forward into
  the new session document (the recap, or the earlier rounds if the recap
  failed, which the reply says), bounded to the session context limit, and
  marks the old session so later calls continue in the new one instead of
  rolling over again. Calls follow the chain under each session's lock in
  turn (a cancelled call never releases a lock it did not get), so calls
  naming an old and a newer session roll over once; a result
  that arrives after its session rolled over goes to the active session (the
  reply says so); a loop or a chain longer than 16 sessions is an error; an
  existing file at the successor's name is adopted only if it was created for
  that session; a missing successor is recreated from its predecessor's
  latest rounds. The carried decisions take at most a quarter of the session
  context and are built newest round first, so the latest decision always
  survives a failed recap or a recovery (an oversized round keeps its start
  and end). A session id is used in its stored form everywhere (`foo bar`
  and `foo_bar` are one session), so ids with spaces or punctuation continue
  in their successor instead of starting a new one.
- Session documents above 4 MB are refused instead of loaded into memory.
- `install.sh` uses Claude Code's own `plugin marketplace` / `plugin install`
  commands instead of editing `known_marketplaces.json`, updates an existing
  install, and prepares dependencies for the version that was actually
  installed with `uv sync --script`. The old warm-then-kill step aborted under
  `set -e` before printing "Done!".
- `cloud/setup.sh` prepares the installed version (from `claude plugin list
  --json`) instead of the lexically last cache entry, and logs failures.
- `codex_status` reports the server version in `.mcpb` installs (was
  "vunknown").

### Upgrading
- Claude Code: `/plugin update claudex@omri-plugins`, then `/reload-plugins`.
- claude.ai account plugin: updates from GitHub automatically when "Sync
  automatically" is on.
- Desktop extension: install `claudex.mcpb` 2.3.1 and restart the Claude app.
  Do not keep or reinstall an older `.mcpb`.

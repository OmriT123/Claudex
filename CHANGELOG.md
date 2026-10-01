# Changelog

All notable changes to Claudex. Versions follow the plugin manifest.

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

### Fixed
- `codex_review_diff` reports git failures (not a repository, git missing,
  timeout, untracked-file listing failed, HEAD unresolvable) as errors.
  Previously they read as "No changes found. Nothing to review." A repository
  with no commits yet is still reviewed (attested as "no commits yet").
- Timed-out git commands are killed with their whole process group (a
  repository's filter programs included) and reaped.
- `codex_collab` keeps an existing session's content as context even when its
  round counter is missing. A rollover now carries the decisions forward into
  the new session document (the recap, or the earlier rounds if the recap
  failed, which the reply says), bounded to the session context limit, and
  marks the old session so later calls continue in the new one instead of
  rolling over again. Rollovers of the same session are serialized.
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

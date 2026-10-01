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
- **Session, recap and job files** are read and written the same way, so a
  symlink at `.claudex`, its subdirectory or the file itself can no longer
  redirect a read or write. `codex_collab` re-validates its session path after
  the model call (which can take minutes) and reports when it could not update
  the session document.
- `_safe_claudex_path` now checks the final path component before resolving it
  (the old post-resolve symlink check could never fire).

### Fixed
- `codex_review_diff` reports git failures (not a repository, git missing,
  timeout, untracked-file listing failed) as errors. Previously they read as
  "No changes found. Nothing to review."
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

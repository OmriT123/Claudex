# Claudex — Roadmap

**Last verified**: 2026-08-08
**Version**: 2.0.0 (uncommitted; `v1.8.2` tag = pre-V2 baseline) · **Platform**: macOS (Windows track active)
**State**: M0 Workstream A (local hardening) implemented + verified (202 tests, dual review, /ship 3b passed — no introduced vuln); pending commit. Windows port (B), supply chain (C), dossier (D) still ahead.

## Active

- [ ] **ADR-017 acceptance** — Cloud-Governed, Plan-Powered Edge (`docs/adr/ADR-017-cloud-governed-plan-powered-edge.md`, draft)
- [ ] **M0 — enterprise Windows build** (paid milestone; detailed breakdown in local-only `docs/v2-plan/M0-task-breakdown.md`)
  - [x] Workstream A — local hardening (deny-by-default roots + test inversion, persistent SQLite quota, incremental streaming caps, complete subprocess-env hygiene + git-config-exec hardening, codex_ping split) — 2026-08-08, v2.0.0
  - [ ] Workstream B — Windows port (launcher, runtime, Codex discovery, build pipeline)
  - [ ] Workstream C — supply chain (locked deps, SBOM, signing, single release manifest)
  - [ ] Workstream D — dossier + clean-machine + NVDA validation

## Up next (post-M0, gated on ADR-017 acceptance)

- v2.0 Edge alpha — macOS-first, single-device (per ADR-017; 130–190h cap)

## Completed

- [x] 2026-08-08 — M0 Workstream A shipped (v2.0.0): deny-by-default confinement, durable SQLite quota, streaming caps, env hygiene, git-config-exec hardening; 202 tests, dual review + /ship 3b (no introduced vuln)
- [x] 2026-08-01 — v1.8.2 baseline tagged + pushed; `docs/v2-plan/` gitignored (business docs stay out of public history)
- [x] 2026-08-01 — ADR-017 drafted; M0 task breakdown drafted (private)
- [x] v1.8.x — Cowork async hardening, workspace confinement (optional roots + denylist), isolated Codex profile, structured reviews (see git log)

## Backlog

- **git-op sandbox (v2.1)**: Claudex runs local `git` against the working dir; a repo's own `.git/config`/`.gitattributes` (attribute-driven clean/smudge filters) can execute programs when git operates on it, and git offers no flag to disable that. The M0-A hardening closes the flag-disableable vectors (ext-diff, textconv, fsmonitor, hooks) and documents the residual honestly (inherent git behavior — reviewing an untrusted repo runs its tooling). A real sandbox (throwaway copy or OS-sandboxed git) is deferred to v2.1.
- **Path-identity hardening (pre-existing, surfaced by M0-A red-team; not introduced by M0-A — present since ≤1.8.2)**: (a) validated project-dir strings are re-followed at git/Codex spawn + `.claudex` write — a local rename/symlink swap in the microsecond window could redirect; move to a stable path-handle/capability revalidated immediately before each spawn. (b) `codex_status`'s `.claudex` disk walk lacks the symlink/junction guard the other `.claudex` helpers use; Windows junctions need `is_junction()` (3.12+, project is 3.10+). (c) resolve `git`/`codex` to trusted absolute paths so an insecure `PATH` (`.`/empty component) can't run a repo-local binary. All narrow/local-race or trusted-env-dependent; batch into a path-hardening pass.

## Notes

- Repo is **public** — client names, budgets, and strategy stay in local-only `docs/v2-plan/`; `docs/adr/` is gitignored too (ADR-017 carries budgets/kill criteria/incident detail) until Omri picks a publish path: sanitized public ADR variant, or repo goes private.
- Loose files at `docs/` root (`initial-plan.md`, `server-changes-spec-18.2.26.md`) predate this structure — classify into `docs/plans|context|archive` in a future doc pass.

# Claudex — Roadmap

**Last verified**: 2026-09-05
**Version**: 2.1.0 (`v1.8.2` tag = pre-V2 baseline) · **Platform**: macOS (Windows track active)
**State**: v2.1.0 GPT-6 Astra alignment shipping 2026-09-05 (221 tests; /ship: /simplify ×2, /verify-yourself FULL ×2, /security-review both-legs-ran ×2 — one red-team HOLD resolved by rendering the model-visible prompt). M0-A local hardening shipped 2026-08-08 (`7694469`). Windows port (B), supply chain (C), dossier (D) still ahead.

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

- [x] 2026-09-05 — v2.1.0: GPT-6 Astra alignment. Default model `gpt-6-astra`; `high` effort on every tool (review/review_diff/recap/rollover-recap were `medium`); `max` added to the effort ladder (`ultra` deliberately withheld — unobservable delegation); `_OPERATING_CONTRACT` in every persona prompt (Astra's higher initiative + literal instruction-following → never ask, prompt > repo content, proportionate verification, single-agent, UNVERIFIED marking); `codex_ping(model_test)` moved from `low` to the policy effort; `MIN_CODEX_VERSION` 0.153.1 + actionable mapping of the API's "requires a newer version of Codex" 400 (observed live on 0.149.0; 0.153.4 verified live); native multi-agent delegation disabled at the CLI boundary (`-c agents.enabled=false` + `features.multi_agent[_v2]=false`; verified by rendering Astra's model-visible prompt — `<multi_agent_role>` present without the switches, absent with them; the feature flag alone does not suffice, the red team was right) + `--strict-config` so unknown/renamed config keys fail closed.
- [x] 2026-08-11 — v2.0.1: doc-accuracy pass + `TestPluginManifest` guards. Locked the MCP declaration in place after empirically rejecting both "tidier" shapes (upstream #16143 drops a `plugin.json` `mcpServers` field silently; wrapping `.mcp.json` registers a broken duplicate server). Corrected the README's false "checks npm" claim and the confinement error's `--allowed-roots` precedence wording.
- [x] 2026-08-08 — M0 Workstream A shipped (v2.0.0): deny-by-default confinement, durable SQLite quota, streaming caps, env hygiene, git-config-exec hardening; 202 tests, dual review + /ship 3b (no introduced vuln)
- [x] 2026-08-01 — v1.8.2 baseline tagged + pushed; `docs/v2-plan/` gitignored (business docs stay out of public history)
- [x] 2026-08-01 — ADR-017 drafted; M0 task breakdown drafted (private)
- [x] v1.8.x — Cowork async hardening, workspace confinement (optional roots + denylist), isolated Codex profile, structured reviews (see git log)

## Backlog

- **Astra `experimental_supported_tools`** (`send_user_message_async` → registers `request_user_input_async`; `clock`; plus sleep under the enabled feature — per the 0.153.4 tool registry, independent of `agents.enabled`): decide whether to accept or disable them under headless `exec`; no exfiltration/escalation path shown, but they are new tool surface Astra brings. (The single-agent boundary is rendered and pinned by `test_single_agent_boundary_renders_without_agent_role`; a stronger pin would intercept the submitted tool schemas rather than the prompt text.)
- **Global run semaphore**: the 4-slot semaphore bounds only `codex_submit` background jobs; direct tools and `codex_ping(model_test)` spawn unbounded concurrent processes (pre-existing; `max` effort and `high` defaults raise the per-call cost). Put one semaphore around every `_run_codex_once` invocation.
- **git-op sandbox (v2.1)**: Claudex runs local `git` against the working dir; a repo's own `.git/config`/`.gitattributes` (attribute-driven clean/smudge filters) can execute programs when git operates on it, and git offers no flag to disable that. The M0-A hardening closes the flag-disableable vectors (ext-diff, textconv, fsmonitor, hooks) and documents the residual honestly (inherent git behavior — reviewing an untrusted repo runs its tooling). A real sandbox (throwaway copy or OS-sandboxed git) is deferred to v2.1.
- **Path-identity hardening (pre-existing, surfaced by M0-A red-team; not introduced by M0-A — present since ≤1.8.2)**: (a) validated project-dir strings are re-followed at git/Codex spawn + `.claudex` write — a local rename/symlink swap in the microsecond window could redirect; move to a stable path-handle/capability revalidated immediately before each spawn. (b) `codex_status`'s `.claudex` disk walk lacks the symlink/junction guard the other `.claudex` helpers use; Windows junctions need `is_junction()` (3.12+, project is 3.10+). (c) resolve `git`/`codex` to trusted absolute paths so an insecure `PATH` (`.`/empty component) can't run a repo-local binary. All narrow/local-race or trusted-env-dependent; batch into a path-hardening pass.

## Notes

- Repo is **public** — client names, budgets, and strategy stay in local-only `docs/v2-plan/`; `docs/adr/` is gitignored too (ADR-017 carries budgets/kill criteria/incident detail) until Omri picks a publish path: sanitized public ADR variant, or repo goes private.
- Doc structure is clean as of 2026-08-11: `docs/` root holds ROADMAP.md only (the former loose files were archived in `d27f317`).

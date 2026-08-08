# Claudex — Roadmap

**Last verified**: 2026-08-01
**Version**: 1.8.2 (tag `v1.8.2` = pre-V2 baseline) · **Platform**: macOS (Windows track active)
**State**: V2 architecture decided (ADR-017 draft) — M0 enterprise Windows milestone is next; no v2.0 cloud work until M0 is accepted.

## Active

- [ ] **ADR-017 acceptance** — Cloud-Governed, Plan-Powered Edge (`docs/adr/ADR-017-cloud-governed-plan-powered-edge.md`, draft)
- [ ] **M0 — enterprise Windows build** (paid milestone; detailed breakdown in local-only `docs/v2-plan/M0-task-breakdown.md`)
  - [ ] Workstream A — local hardening (deny-by-default roots + test inversion, persistent quota, streaming caps, ping sanitization)
  - [ ] Workstream B — Windows port (launcher, runtime, Codex discovery, build pipeline)
  - [ ] Workstream C — supply chain (locked deps, SBOM, signing, single release manifest)
  - [ ] Workstream D — dossier + clean-machine + NVDA validation

## Up next (post-M0, gated on ADR-017 acceptance)

- v2.0 Edge alpha — macOS-first, single-device (per ADR-017; 130–190h cap)

## Completed

- [x] 2026-08-01 — v1.8.2 baseline tagged + pushed; `docs/v2-plan/` gitignored (business docs stay out of public history)
- [x] 2026-08-01 — ADR-017 drafted; M0 task breakdown drafted (private)
- [x] v1.8.x — Cowork async hardening, workspace confinement (optional roots + denylist), isolated Codex profile, structured reviews (see git log)

## Notes

- Repo is **public** — client names, budgets, and strategy stay in local-only `docs/v2-plan/`; `docs/adr/` is gitignored too (ADR-017 carries budgets/kill criteria/incident detail) until Omri picks a publish path: sanitized public ADR variant, or repo goes private.
- Loose files at `docs/` root (`initial-plan.md`, `server-changes-spec-18.2.26.md`) predate this structure — classify into `docs/plans|context|archive` in a future doc pass.

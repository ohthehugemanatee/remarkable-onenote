# ADR-0001: rmfakecloud is the cloud layer; we build above it

Status: accepted (2026-05-21)

## Context

The initial plan for this project had rmsync implementing the reMarkable cloud protocol directly — endpoint impersonation in `xochitl.conf`, the 15+15 sync flow, root-hash bookkeeping, blob storage, pairing handshake, per-firmware compatibility tracking. Four planning rounds converged on a working design (three transport tiers, two-phase WAL, dual-hash blob store, generation overflow handling, toltec persistence recipes), and the design was judged implementable.

Late in planning, [ddvk/rmfakecloud](https://github.com/ddvk/rmfakecloud) was investigated. It is AGPL-3.0 Go, actively maintained through firmware 3.27.1 as of May 2026, and already implements every protocol concern we had designed: endpoint reconfiguration support, the 15+15 sync flow, root-hash bookkeeping, blob storage, pairing, a web UI, faithful `.rm` line-file passthrough. Third-party tools (reMarkableSync, the Obsidian rmfakecloud plugin) already consume rmfakecloud's data externally — the "watch the store from above" integration pattern is established and works.

## Decision

rmsync is reframed as an integration layer that sits **above** rmfakecloud, not parallel to it. rmfakecloud is a hard runtime dependency. rmsync depends on exactly two surfaces of rmfakecloud:

1. The filesystem layout of its user store (read-only access).
2. Its tablet-facing HTTP sync API (write path for inbound changes).

Everything else — the 15+15 protocol, endpoint reconfiguration, pairing, per-firmware compatibility — is rmfakecloud's responsibility.

## Consequences

### Positive

- Massive scope reduction. Deleted from the plan: three-tier transport model, bootstrap/impersonation flow, two-phase WAL, root-PUT durability SLO, dual-hash blob store, generation overflow logic, per-firmware compat probe, toltec recipes for CA/hosts persistence.
- We inherit rmfakecloud's existing firmware compatibility — they track new firmware releases, we don't.
- Faster path to a working v1 because the cloud-layer is already done by someone else.
- Clear separation of concerns: rmfakecloud handles tablet-protocol semantics; we handle backend routing semantics. Two layers, both replaceable.

### Negative

- Hard runtime dependency on a project we don't control. If rmfakecloud goes unmaintained, we have a problem.
- We don't get to pick the implementation language or design choices of the cloud layer. We work around any bugs by upstreaming fixes, not patching locally.
- Filesystem coupling is a real maintenance surface — if rmfakecloud changes its store layout, we update our reader. Mitigated by rmfakecloud's store being stable for years and by an established ecosystem of external readers.
- Deployments need to run *two* services (rmfakecloud + rmsync), not one. Compose and Helm examples bundle them so this isn't user-visible complexity in practice.

### Neutral

- The repo name `remarkable-onenote` becomes a misnomer (we ship NextCloud + OneDrive first, OneNote is P4). Name retained for continuity; renaming is a cosmetic future task.

## Considered alternatives

- **A: rmsync IS the cloud.** Reinvent everything rmfakecloud already does, then layer routing on top. Rejected for cost — four rounds of planning produced a design that would take many months to implement, against established prior art that already works.
- **B: Fork rmfakecloud and merge our integration in.** Tight coupling, hostile upstream relationship, Go for one half and Python for the other. Rejected.
- **C: Run rmfakecloud as a library inside rmsync (CGo/embed binary).** Process-boundary blurred, lifecycle harder to reason about, deployment a single binary but with two effective owners. Rejected for operational opacity.
- **D: This decision.** Loosely coupled, two processes, filesystem + HTTP boundary. Accepted.

## References

- rmfakecloud: https://github.com/ddvk/rmfakecloud
- reMarkableSync (external consumer pattern): https://github.com/peerdavid/rmapy
- Plan: `/root/.claude/plans/i-want-to-plan-giggly-bubble.md` §"Major architectural pivot"

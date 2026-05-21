# ADR-0003: Backends as Python entry-point plugins

Status: accepted (2026-05-21)

## Context

rmsync ships with two backends in v1 (NextCloud, OneDrive) and intends to add more (OneNote in P4, LocalDevice archive, hypothetical third-party). The architecture must support adding backends without modifying core code, and third-party backends without requiring upstream PRs.

## Decision

Backends are Python distributions that register a class implementing `rmsync.plugins.base.BackendPlugin` under the `rmsync.backends` entry-point group:

```toml
[project.entry-points."rmsync.backends"]
nextcloud = "rmsync.plugins.nextcloud:NextCloudBackend"
```

At startup, rmsync uses `importlib.metadata.entry_points(group="rmsync.backends")` to discover all installed backends. Each one is instantiated lazily on first reference from `rules.yaml`. A backend that fails to instantiate (missing credentials, import error) MUST surface a structured error in the UI without preventing other backends from loading.

Built-in backends (`nextcloud`, `onedrive`, `null`) ship in the main distribution. Third-party backends ship as separate distributions and become available simply by `pip install`-ing them into the rmsync image — operators using docker can `--mount` a sidecar Python venv, or the image's `entrypoint.sh` can `pip install` from a configured wheel directory at startup.

## Consequences

### Positive

- Standard Python plugin pattern, no custom registry.
- Third-party backends don't require upstream changes.
- Backend lifecycle (load, probe, push, pull, move, delete) is constrained by a single ABC — contract tests parametrise over the entry-point group, so every loaded backend is tested against the same suite.
- Capabilities (`preserves_strokes`, `supports_move`, `identity_stable`, etc.) live on the plugin, not in core config — config-load validates rules against the actual loaded plugin's flags.

### Negative

- Entry-point discovery is process-startup-only. Adding a backend requires a restart. Acceptable for the deployment model.
- Plugins can crash the host. Mitigated by isolating push/pull calls behind structured error handling and logging the plugin name on every error path.
- Plugins have full Python access (no sandbox). Operators trust the plugins they install; rmsync is not a multi-tenant service.

### Neutral

- This is the same pattern used by `pytest`, `mkdocs`, `flake8`, etc. — well-understood.

## References

- `docs/spec.md` §3 (backend plugin contract)

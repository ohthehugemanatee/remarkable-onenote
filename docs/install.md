# Installing rmsync

This document covers a generic installation for any reMarkable owner. The user's specific k3s deployment lives in [`homenet-documentation/remarkable-sync.md`](https://github.com/ohthehugemanatee/homenet-documentation/blob/master/remarkable-sync.md) as one example.

## What you're installing

You're installing **two** services that run together:

1. **rmfakecloud** — replaces the reMarkable cloud, talks the tablet protocol. Upstream: https://github.com/ddvk/rmfakecloud
2. **rmsync** (this project) — sits above rmfakecloud, routes documents to backends (NextCloud, OneDrive, eventually OneNote), preserves pen strokes.

rmsync does NOT replace rmfakecloud; the two work together. See [ADR-0001](adr/0001-rmfakecloud-as-cloud-layer.md) for why.

## Prerequisites

- A domain you own that the tablet can reach (e.g. `rm.example.com`). Required because the tablet needs a stable hostname to talk to rmfakecloud and rmsync's UI. Self-signed certificates and `xochitl.conf` overrides remain documented at rmfakecloud upstream but are not the recommended path here.
- A TLS certificate for that domain. The recommended path is automated ACME via Let's Encrypt; the docker-compose example uses Caddy to handle this automatically.
- A reMarkable tablet on firmware 2.15.x or 3.x. rmfakecloud's compatibility matrix covers specific firmwares: https://github.com/ddvk/rmfakecloud/wiki
- Persistent storage for both rmfakecloud's user store and rmsync's state DB + archive cache. Anything POSIX works (local disk, NFS, Longhorn, etc.).
- Credentials for the backends you plan to sync to (NextCloud app password, OneDrive OAuth app registration).

## Quick-start (docker-compose)

```bash
git clone https://github.com/ohthehugemanatee/remarkable-onenote
cd remarkable-onenote/examples
cp .env.example .env
# edit .env: set RMSYNC_DOMAIN, RMSYNC_RMFAKECLOUD_USER, RMSYNC_RMFAKECLOUD_PASSWORD, UI_USER, UI_PASSWORD
docker compose up -d
```

The compose stack brings up:

- `caddy` — front-end, terminates TLS via Let's Encrypt, routes `/api/*`, `/storage/*`, `/sync/*` to rmfakecloud and `/ui/*` to rmsync.
- `rmfakecloud` — the cloud layer.
- `rmsync` — this service. Mounts rmfakecloud's user store read-only.

Then on the tablet:

1. Follow the rmfakecloud setup guide to point `xochitl.conf` at `RMSYNC_DOMAIN`. Upstream docs: https://ddvk.github.io/rmfakecloud/install/
2. Pair the tablet against your rmfakecloud instance using rmfakecloud's web UI at `https://RMSYNC_DOMAIN`.
3. Open `https://RMSYNC_DOMAIN/ui` (rmsync's status UI). Log in with `UI_USER` / `UI_PASSWORD`.
4. Create a backend credential file under `secrets/<backend>.env` and a `rules.yaml`. See "Configuring backends" below.

## Configuring backends

### Rules file

Default location: `/etc/rmsync/rules.yaml` (mounted from `./config/rules.yaml` in the compose stack). The schema is documented in `docs/spec.md` §8.

Get a starting point from your existing tablet contents:

```bash
docker compose exec rmsync rmsync rules suggest > config/rules.yaml
```

This scans rmfakecloud's user store and emits a `rules.yaml` reflecting your current folder structure. Edit to taste.

### NextCloud backend

1. In NextCloud, create an app password for rmsync: Settings → Security → Devices & sessions.
2. Create `secrets/nextcloud.env`:
   ```
   NEXTCLOUD_URL=https://cloud.example.com
   NEXTCLOUD_USER=your-username
   NEXTCLOUD_APP_PASSWORD=xxxxx-xxxxx-xxxxx-xxxxx-xxxxx
   ```
3. Reference `nextcloud` in `rules.yaml`. Test connectivity: `docker compose exec rmsync rmsync backend nextcloud probe`.

### OneDrive backend

1. Register an OAuth app at https://entra.microsoft.com → App registrations. Required permissions: `Files.ReadWrite` (delegated). Redirect URI: `https://RMSYNC_DOMAIN/auth/onedrive/callback`.
2. Create `secrets/onedrive.env`:
   ```
   ONEDRIVE_CLIENT_ID=...
   ONEDRIVE_CLIENT_SECRET=...
   ONEDRIVE_TENANT=consumers   # or your tenant id
   ```
3. Visit `https://RMSYNC_DOMAIN/auth/onedrive/start` to complete the consent flow. The resulting refresh token is stored in `state.db`.
4. Test: `docker compose exec rmsync rmsync backend onedrive probe`.

### OneNote backend

Not available in v1. See [`docs/roadmap/onenote.md`](roadmap/onenote.md) for the P4 architecture.

## Status UI

`https://RMSYNC_DOMAIN/ui` shows live status — currently-loaded rules, recent sync events, conflict queue, tombstones, per-backend cursor lag, render-pipeline stats. The UI is read-only in v1; configuration happens through `rules.yaml` (which is hot-reloaded on change) and operator CLIs.

## Operational notes

### Backup what matters

- **`state.db`** — derived index, MAY be rebuilt by `rmsync rescan`. Back up for convenience, not necessity.
- **`mirror_pull_tombstones` table** — cannot be rebuilt. Back this up. The compose stack runs a daily `sqlite3 state.db ".dump mirror_pull_tombstones"` cron into the archive volume.
- **rmfakecloud user store** — the tablet's source of truth. Back up via rmfakecloud's documented procedure.
- **Archive cache** (`/var/lib/rmsync/archive`) — rendered representations. Reconstructible from `state.db` + rmfakecloud's store, but expensive to regenerate. Worth backing up.

### Updating rmsync

`docker compose pull && docker compose up -d`. The image's entrypoint runs schema migrations against `state.db` automatically. Rollback by pinning to an older image tag and restarting.

### Updating rmfakecloud

Follow rmfakecloud's upstream guidance. rmsync watches the filesystem store, so an rmfakecloud restart is invisible to rmsync (the watcher reconnects automatically on `IN_IGNORED`). If rmfakecloud changes its store layout between major versions, watch for a release note from rmsync.

### Troubleshooting

- **Tablet syncs, but documents don't appear on the backend.** Check `/ui/events` for the document — was it routed? If `routing_decisions` is empty, the document didn't match any rule. Check `/ui/rules` for the loaded rules.
- **Documents appear duplicated on the backend.** Likely a `rmsync rescan` was run while the backend had existing content — documented behaviour (ADR-0002). Manually deduplicate on the backend; rmsync's next push will not duplicate further.
- **Conflicts pile up.** Check `/ui/conflicts`. The default policy is `rm_wins` (ADR-0004), meaning backend-side edits diverge into `/Conflicts/`. Consider `branch` policy per-rule if you collaborate heavily on the backend.

### Where to get help

- rmsync issues: https://github.com/ohthehugemanatee/remarkable-onenote/issues
- rmfakecloud issues: https://github.com/ddvk/rmfakecloud/issues
- reMarkable firmware compatibility: see rmfakecloud's wiki, not this repo.

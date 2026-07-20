# Optional diagnostic-report receiver

This service accepts only reports that a PalPlus user explicitly previews and sends. It stores the fixed report schema in SQLite on a Railway Volume; it does not store IP addresses, user agents, player positions, game saves, local paths, or account identifiers.

## Deploy on Railway

1. Create a Railway service from this repository. Set its root directory to `support_server` so Railway uses its `Dockerfile`.
2. Attach a persistent Volume at `/data`. The service uses `RAILWAY_VOLUME_MOUNT_PATH` automatically and stores `palplus-reports.sqlite3` there.
3. Generate a public domain for the service. Its health endpoint is `https://<your-domain>/healthz`.
4. Set `PALPLUS_REPORT_ADMIN_TOKEN` to a long random value in Railway. Never add it to this repository or the desktop app.
5. Put `https://<your-domain>/v1/reports` in `src/palworld_companion/assets/reporting.json`, then release a patched desktop build.

Railway documents that a Volume is mounted only at runtime and exposes its mount path through `RAILWAY_VOLUME_MOUNT_PATH`; this receiver opens SQLite when the service starts for that reason. See [Railway Volumes](https://docs.railway.com/volumes).

## Read reports privately

Use the Railway service domain with the admin token; do not expose this URL with its authorization header in an issue:

```bash
curl -H "Authorization: Bearer $PALPLUS_REPORT_ADMIN_TOKEN" https://<your-domain>/v1/reports
```

Each row contains a report ID and the exact user-approved payload. Paste a selected report into a Codex task together with the user-visible report ID; do not ask users for raw logs, save files, or personal state unless they independently choose to share them.

## Operational limits

- The public submit route is schema-limited to 12 KB and rate-limited in memory to five reports per source address per hour. Source addresses are used only for that temporary limit and are not written to SQLite or application logs.
- Railway and network infrastructure necessarily process connection metadata to deliver a request. PalPlus itself does not include that metadata in its report payload.
- SQLite is suitable here for a low-volume support inbox with one service and one mounted Volume. Back up the Volume before destructive maintenance.

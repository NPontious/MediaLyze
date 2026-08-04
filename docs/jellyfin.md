# Jellyfin integration

Jellyfin is the first adapter for MediaLyze's [provider-neutral connector architecture](connectors.md). MediaLyze reads Jellyfin catalogs and never changes Jellyfin items. Multiple Jellyfin connections are supported for catalog import and matching.

## Connection and permissions

Create a dedicated Jellyfin API key and make the server reachable from the MediaLyze runtime. The key must read system information, virtual folders, items, and item images. The migrated standard connection also needs user and user-data access when playback synchronization is enabled.

Secrets entered in Settings are stored locally in MediaLyze's SQLite database but never returned by the API or written to logs. Protect `CONFIG_PATH` so only the MediaLyze service account can read it. `JELLYFIN_API_KEY_FILE` remains available for the migrated standard connection named `Jellyfin`; a file-backed key takes precedence there and does not apply to additional Jellyfin connections.

Connection configuration is separate from activation. Each connection has its own URL, schedule, enabled state, capabilities, status, catalog, mappings, and sync job. Removing a connection deletes only its connector-owned cache and mapping data. It cannot remove a secret maintained in an external file and never deletes MediaLyze libraries or files.

## Libraries, locations, and path mappings

An imported Jellyfin library may expose multiple filesystem locations. Logical links between Jellyfin and MediaLyze libraries are optional and many-to-many. Physical matching is controlled exclusively by location-to-root bindings in Connector Settings.

In the normal mapping view, assign a Jellyfin location to a MediaLyze root. Advanced options allow a narrower source prefix, target subpath, case mode, and priority. The longest valid prefix wins. Equal best rules are rejected instead of guessed. Migrated mappings use case-insensitive comparison for compatibility; new mappings can use case-sensitive comparison.

Jellyfin and MediaLyze may see the same hierarchy through different mount points, for example `/media/movies` and `/mnt/nas/films`. The binding removes the Jellyfin source prefix, applies the safe target subpath, and matches the remainder only against `LibraryRoot.id + MediaFile.relative_path`.

## Preferred connection and history

A MediaLyze library can link several external libraries and chooses `preferred_connector_connection_id` for scalar metadata, connector-added dates, and the compatibility Jellyfin fields. MediaLyze selects it automatically when exactly one connection is linked. With several linked connections, select it explicitly in the expanded library settings.

The old `jellyfin` added-date source migrates to `connector`. If the preferred connection has no matched creation date, history falls back to the MediaLyze date.

## Users, playback, and images

Users, per-user state, playback events, and provider image behavior remain Jellyfin-specific capabilities. During this compatibility release they are maintained by the established sync path of the migrated standard Jellyfin connection. Additional Jellyfin connections use the generic catalog adapter but do not yet supply the legacy playback timeline.

Only enabled users contribute playback information. Disabling a user removes that user's locally cached playback state. A successful standard sync removes obsolete provider rows. The legacy `/api/jellyfin/*` endpoints retain their response shapes for the first connector release and remain backed by the compatibility cache.

## Synchronization, migration, and Shadow Mode

Generic connections use persisted, single-flight, connection-scoped jobs. Validated pages are written to run-scoped staging tables and promoted atomically only after a complete successful fetch. Failure or cancellation discards the current run and preserves the last successful catalog. Different connections may synchronize concurrently.

On upgrade, MediaLyze idempotently creates the standard `provider=jellyfin`, `name=Jellyfin` connection and backfills the legacy catalog, locations, links, matches, credentials, and unambiguous path rules. Existing Jellyfin tables are intentionally retained. Standard legacy syncs mirror their catalog into connector tables and execute both matchers. Job summaries report `same_match`, `old_only`, `new_only`, `different_media_file`, `ambiguous`, and `unmapped` counters so unexplained differences can be diagnosed before the compatibility facade is removed in a later release.

Startup cancels orphaned connector jobs rather than promoting partial state. Remote deletions are applied only during a successful promote. The repository includes the large-catalog benchmark:

```bash
.venv/bin/python benchmarks/benchmark_jellyfin_bulk_promote.py
```

The automated contract tests cover Jellyfin 10.10 and 10.11 response shapes. A running Jellyfin server exposes its exact API documentation at `/api-docs/swagger/index.html`; verify other versions with Test connection and a complete sync.

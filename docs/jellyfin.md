# Jellyfin integration

MediaLyze treats Jellyfin as a read-only external metadata source. It imports the
catalog, virtual-library structure, image references, and playback state for the
users explicitly enabled in Settings. It does not change Jellyfin items.

## Connection and permissions

Create a dedicated Jellyfin API key and make the Jellyfin server reachable from
the MediaLyze runtime. The key must be able to read system information, users,
virtual folders, items, user data, and item images. Keep the key out of logs and
support bundles.

The key can be saved through Settings. It is then stored in plain text in the
MediaLyze SQLite database, so the config directory and database should only be
readable by the MediaLyze service account. As an alternative, set
`JELLYFIN_API_KEY_FILE` to a Docker/Kubernetes secret file. A key supplied by a
secret file takes precedence over the database value.

The UI separates configuration from activation: URL, key, and schedule can be
saved while the integration is disabled. “Remove connection” deletes the stored
connection plus the locally cached Jellyfin catalog, matches, mappings, users,
and playback data. It cannot delete a key managed through an external secret
file.

## Users and privacy

Only enabled users contribute playback information to catalog and file views.
Disabling a user removes that user's locally cached playback state. A completed
sync also removes per-user rows that Jellyfin no longer returns. Removing the
connection deletes all locally cached Jellyfin user data.

## Library assignments

Jellyfin Settings contains only the server connection, synchronization controls,
and playback-user selection. In Libraries settings, expand a MediaLyze library
and select the corresponding Jellyfin library from its association dropdown.
Each Jellyfin library can be combined with one existing MediaLyze library, or
used as the name and media-type template when creating a new MediaLyze library.
The Add Library dialog still asks for the local media path that MediaLyze should
scan and links the Jellyfin catalog after creation.

If Jellyfin and MediaLyze see the same files below different mount points, the
expanded association section also exposes an optional path mapping for every
source location reported by Jellyfin. These mappings use the existing global
prefix rules and queue asset matching again after they are saved or removed.

## Synchronization and compatibility

Manual and scheduled requests share one persisted background job. A restart
marks an interrupted job as canceled; the next sync starts from the last
successfully committed cache. Catalog pages are validated before stale data is
removed, so malformed or incomplete successful HTTP responses fail the import
without promoting partial state.

Each validated page is written with a native SQLite bulk UPSERT into sync-run
staging tables and committed independently. The visible catalog is changed only
after all catalog and user pages completed, in one short atomic promote. Failed
or canceled runs discard their staging rows and leave the previous live snapshot
untouched. Sync jobs persist their current phase, counters, detail, and heartbeat,
so status polling remains useful when requests and workers run in different
processes. Jellyfin overview totals and distributions are aggregated in SQL
instead of loading the complete item collection into Python memory.

For local performance checks, the repository includes a 100,000-item SQLite
benchmark:

```bash
.venv/bin/python benchmarks/benchmark_jellyfin_bulk_promote.py
```

The automated contract tests cover the API shapes used by Jellyfin 10.10 and
10.11. The running Jellyfin instance exposes its exact API documentation under
`/api-docs/swagger/index.html`. Other versions may work, but should be verified
with “Test connection” and a full sync before relying on playback history.

Known limitation: path comparison currently uses case-insensitive normalization
on every platform. This remains intentionally unchanged until path mappings can
carry an explicit case-sensitivity policy.

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

## Container path mappings

Jellyfin paths describe Jellyfin's filesystem view and commonly differ from the
paths mounted into MediaLyze. For example:

```text
Jellyfin item path:  /data/movies/Example.mkv
MediaLyze mount:     /media/movies/Example.mkv
Mapping:             /data/movies -> /media/movies
```

Use the diagnostic rows in Settings to add mappings. Mapping changes are saved
immediately and trigger one deduplicated background rematch.

## Synchronization and compatibility

Manual and scheduled requests share one persisted background job. A restart
marks an interrupted job as canceled; the next sync starts from the last
successfully committed cache. Catalog pages are validated before stale data is
removed, so malformed or incomplete successful HTTP responses fail the import
without promoting partial state.

The automated contract tests cover the API shapes used by Jellyfin 10.10 and
10.11. The running Jellyfin instance exposes its exact API documentation under
`/api-docs/swagger/index.html`. Other versions may work, but should be verified
with “Test connection” and a full sync before relying on playback history.

Known limitation: path comparison currently uses case-insensitive normalization
on every platform. This remains intentionally unchanged until path mappings can
carry an explicit case-sensitivity policy.

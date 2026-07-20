import {
  AlertTriangle,
  CheckCircle2,
  Link2,
  Plus,
  RefreshCw,
  Server,
  Trash2,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { AsyncPanel } from "./AsyncPanel";
import { TooltipTrigger } from "./TooltipTrigger";
import {
  api,
  type JellyfinConnection,
  type JellyfinLibrary,
  type JellyfinPathMapping,
  type JellyfinSyncStatus,
  type JellyfinUser,
  type LibrarySummary,
} from "../lib/api";
import { formatDate } from "../lib/format";

const EMPTY_CONNECTION: JellyfinConnection = {
  base_url: "",
  enabled: false,
  sync_interval_minutes: 60,
  api_key_configured: false,
  server_name: null,
  server_version: null,
  last_status: "never",
  last_error: null,
  last_sync_started_at: null,
  last_sync_finished_at: null,
  last_successful_sync_at: null,
  next_scheduled_sync_at: null,
};

const SYNC_PHASE_STEP: Record<string, number> = {
  connecting: 0,
  users: 1,
  libraries: 1,
  items: 2,
  saving: 2,
  cleanup: 2,
  matching: 3,
};

function normalizedPath(path: string) {
  return path.trim().replaceAll("\\", "/").replace(/\/+$/, "").toLocaleLowerCase();
}

function libraryMappingKey(libraryId: number, location: string) {
  return `${libraryId}:${location}`;
}

function normalizedSyncInterval(value: string) {
  if (!value.trim()) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.min(10080, Math.max(0, Math.round(parsed))) : null;
}

function validJellyfinBaseUrl(value: string) {
  if (!value.trim()) return true;
  try {
    const parsed = new URL(value.trim());
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    return false;
  }
}

function JellyfinLibraryMappingEditor({
  library,
  location,
  mapping,
  disabled,
  onSaved,
}: {
  library: JellyfinLibrary;
  location: string;
  mapping?: JellyfinPathMapping;
  disabled: boolean;
  onSaved: () => Promise<void>;
}) {
  const { t } = useTranslation();
  const [target, setTarget] = useState(mapping?.medialyze_path_prefix || "");
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [saveError, setSaveError] = useState<string | null>(null);
  const failedSignatureRef = useRef<string | null>(null);
  const feedbackTimerRef = useRef<number | null>(null);
  const savedTarget = mapping?.medialyze_path_prefix || "";
  const signature = JSON.stringify([mapping?.id || null, location, target.trim()]);

  useEffect(() => {
    setTarget(savedTarget);
  }, [savedTarget]);

  useEffect(() => {
    const nextTarget = target.trim();
    if (
      disabled
      || !nextTarget
      || nextTarget === savedTarget
      || failedSignatureRef.current === signature
    ) return;

    const timer = window.setTimeout(async () => {
      setSaveState("saving");
      setSaveError(null);
      if (feedbackTimerRef.current !== null) window.clearTimeout(feedbackTimerRef.current);
      try {
        if (mapping) {
          await api.updateJellyfinPathMapping(mapping.id, {
            medialyze_path_prefix: nextTarget,
            enabled: true,
          });
        } else {
          await api.createJellyfinPathMapping({
            jellyfin_path_prefix: location,
            medialyze_path_prefix: nextTarget,
            enabled: true,
          });
        }
        failedSignatureRef.current = null;
        await onSaved();
        setSaveState("saved");
        feedbackTimerRef.current = window.setTimeout(() => setSaveState("idle"), 2000);
      } catch (reason) {
        failedSignatureRef.current = signature;
        setSaveError((reason as Error).message);
        setSaveState("error");
      }
    }, 600);
    return () => window.clearTimeout(timer);
  }, [disabled, location, mapping, onSaved, savedTarget, signature, target]);

  useEffect(() => () => {
    if (feedbackTimerRef.current !== null) window.clearTimeout(feedbackTimerRef.current);
  }, []);

  return (
    <div className="jellyfin-library-mapping-editor">
      <code title={t("jellyfin.jellyfinPath")}>{location}</code>
      <span aria-hidden="true">→</span>
      <input
        value={target}
        placeholder={t("jellyfin.medialyzePathPlaceholder")}
        aria-label={t("jellyfin.libraryMappingTarget", { name: library.name })}
        disabled={disabled || saveState === "saving"}
        onChange={(event) => setTarget(event.target.value)}
      />
      {saveState !== "idle" ? (
        <span className={`jellyfin-library-mapping-status status-${saveState}`} role="status" aria-live="polite">
          {saveState === "saving" ? <RefreshCw className="is-spinning" aria-hidden="true" /> : null}
          {saveState === "saved" ? <CheckCircle2 aria-hidden="true" /> : null}
          {saveState === "error" ? <AlertTriangle aria-hidden="true" /> : null}
          {saveState === "saving" ? t("jellyfin.autoSave.saving") : null}
          {saveState === "saved" ? t("jellyfin.autoSave.saved") : null}
          {saveState === "error" ? saveError || t("jellyfin.autoSaveFailed") : null}
        </span>
      ) : null}
    </div>
  );
}

export function JellyfinSettingsPanel({
  highlightedLibraryId,
  onLibraryCreated,
  medialyzeLibraries = [],
  onLibrariesChanged,
}: {
  highlightedLibraryId?: string | null;
  onLibraryCreated?: () => void;
  medialyzeLibraries?: LibrarySummary[];
  onLibrariesChanged?: () => void;
}) {
  const { t } = useTranslation();
  const [connection, setConnection] = useState<JellyfinConnection>(EMPTY_CONNECTION);
  const [status, setStatus] = useState<JellyfinSyncStatus | null>(null);
  const [users, setUsers] = useState<JellyfinUser[]>([]);
  const [mappings, setMappings] = useState<JellyfinPathMapping[]>([]);
  const [libraries, setLibraries] = useState<JellyfinLibrary[]>([]);
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [syncInterval, setSyncInterval] = useState("60");
  const [mappingSource, setMappingSource] = useState("");
  const [mappingTarget, setMappingTarget] = useState("");
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState<string | null>(null);
  const [autoSaving, setAutoSaving] = useState(false);
  const [connectionSaveState, setConnectionSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [connectionSaveError, setConnectionSaveError] = useState<string | null>(null);
  const [apiKeyFocused, setApiKeyFocused] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const saveFeedbackTimerRef = useRef<number | null>(null);
  const failedAutoSaveSignatureRef = useRef<string | null>(null);
  const onLibrariesChangedRef = useRef(onLibrariesChanged);
  onLibrariesChangedRef.current = onLibrariesChanged;

  const load = useCallback(async () => {
    const [nextConnection, nextStatus, nextUsers, nextMappings, nextLibraries] = await Promise.all([
      api.jellyfinConnection(),
      api.jellyfinSyncStatus(),
      api.jellyfinUsers(),
      api.jellyfinPathMappings(),
      api.jellyfinLibraries(),
    ]);
    setConnection(nextConnection);
    setStatus(nextStatus);
    setUsers(nextUsers);
    setMappings(nextMappings);
    setLibraries(nextLibraries);
    setBaseUrl(nextConnection.base_url);
    setSyncInterval(String(nextConnection.sync_interval_minutes));
  }, []);

  const reloadAfterLibraryChange = useCallback(async () => {
    await load();
    onLibrariesChangedRef.current?.();
  }, [load]);

  useEffect(() => {
    setLoading(true);
    load()
      .then(() => setError(null))
      .catch((reason: Error) => setError(reason.message))
      .finally(() => setLoading(false));
  }, [load]);

  useEffect(() => {
    if (!highlightedLibraryId || loading) return;
    const element = document.getElementById(`jellyfin-library-${highlightedLibraryId}`);
    element?.focus({ preventScroll: true });
    element?.scrollIntoView({ behavior: "auto", block: "center" });
  }, [highlightedLibraryId, loading, libraries]);

  const syncRunning = pending === "sync" || connection.last_status === "running";
  const parsedSyncInterval = normalizedSyncInterval(syncInterval);
  const autoSaveSignature = JSON.stringify([baseUrl.trim(), apiKey.trim(), parsedSyncInterval]);
  const connectionDirty = (
    baseUrl.trim() !== connection.base_url
    || (parsedSyncInterval !== null && parsedSyncInterval !== connection.sync_interval_minutes)
    || Boolean(apiKey.trim())
  );

  useEffect(() => {
    if (
      loading
      || syncRunning
      || pending !== null
      || autoSaving
      || !connectionDirty
      || failedAutoSaveSignatureRef.current === autoSaveSignature
      || parsedSyncInterval === null
      || !validJellyfinBaseUrl(baseUrl)
      || (apiKeyFocused && Boolean(apiKey.trim()))
    ) return;

    const nextBaseUrl = baseUrl.trim();
    const nextApiKey = apiKey.trim();
    const nextInterval = parsedSyncInterval;
    const timer = window.setTimeout(async () => {
      setAutoSaving(true);
      setConnectionSaveState("saving");
      setConnectionSaveError(null);
      if (saveFeedbackTimerRef.current !== null) window.clearTimeout(saveFeedbackTimerRef.current);
      try {
        const updated = await api.updateJellyfinConnection({
          base_url: nextBaseUrl,
          ...(nextApiKey ? { api_key: nextApiKey } : {}),
          enabled: Boolean(nextBaseUrl && (connection.api_key_configured || nextApiKey)),
          sync_interval_minutes: nextInterval,
        });
        setConnection(updated);
        failedAutoSaveSignatureRef.current = null;
        setStatus((current) => current ? { ...current, ...updated } : current);
        if (nextApiKey) {
          setApiKey((current) => current.trim() === nextApiKey ? "" : current);
        }
        setConnectionSaveState("saved");
        saveFeedbackTimerRef.current = window.setTimeout(() => setConnectionSaveState("idle"), 2000);
      } catch (reason) {
        failedAutoSaveSignatureRef.current = autoSaveSignature;
        setConnectionSaveError((reason as Error).message);
        setConnectionSaveState("error");
      } finally {
        setAutoSaving(false);
      }
    }, 600);
    return () => window.clearTimeout(timer);
  }, [apiKey, apiKeyFocused, autoSaveSignature, autoSaving, baseUrl, connection.api_key_configured, connection.base_url, connection.sync_interval_minutes, connectionDirty, loading, parsedSyncInterval, pending, syncRunning]);

  useEffect(() => () => {
    if (saveFeedbackTimerRef.current !== null) window.clearTimeout(saveFeedbackTimerRef.current);
  }, []);

  useEffect(() => {
    if (!syncRunning) return;
    let active = true;
    const refreshStatus = async () => {
      try {
        const nextStatus = await api.jellyfinSyncStatus();
        if (!active) return;
        setStatus(nextStatus);
        setConnection(nextStatus);
      } catch {
        // The owning sync request reports actionable errors when it completes.
      }
    };
    void refreshStatus();
    const timer = window.setInterval(() => void refreshStatus(), 750);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [syncRunning]);

  async function testConnection() {
    setPending("test");
    setError(null);
    setNotice(null);
    try {
      const result = await api.testJellyfinConnection({
        base_url: baseUrl.trim(),
        ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
      });
      if (!result.ok) {
        setError(result.error || t("jellyfin.testFailed"));
      } else {
        setNotice(t("jellyfin.testSucceeded", { name: result.server_name || "Jellyfin", version: result.server_version || "" }));
      }
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setPending(null);
    }
  }

  async function syncNow() {
    setPending("sync");
    setConnection((current) => ({ ...current, last_status: "running", last_error: null }));
    setError(null);
    setNotice(null);
    try {
      const result = await api.syncJellyfin();
      setNotice(t("jellyfin.syncSucceeded", { items: result.items_synced, libraries: result.libraries_synced }));
      await reloadAfterLibraryChange();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setPending(null);
    }
  }

  const syncPhase = status?.sync_phase || "connecting";
  const syncStep = SYNC_PHASE_STEP[syncPhase] ?? 0;
  const syncPercent = status?.sync_total
    ? Math.min(100, Math.round((status.sync_current / status.sync_total) * 100))
    : null;
  const syncSteps = ["connection", "catalog", "items", "matching"];

  async function toggleUser(userId: string) {
    const enabledIds = users
      .filter((user) => user.enabled_for_sync !== (user.jellyfin_user_id === userId))
      .map((user) => user.jellyfin_user_id);
    setPending(`user-${userId}`);
    try {
      setUsers(await api.updateJellyfinUsers(enabledIds));
      setError(null);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setPending(null);
    }
  }

  async function addMapping() {
    if (!mappingSource.trim() || !mappingTarget.trim()) return;
    setPending("mapping-new");
    try {
      await api.createJellyfinPathMapping({
        jellyfin_path_prefix: mappingSource.trim(),
        medialyze_path_prefix: mappingTarget.trim(),
        enabled: true,
      });
      setMappingSource("");
      setMappingTarget("");
      await reloadAfterLibraryChange();
      setError(null);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setPending(null);
    }
  }

  async function deleteMapping(id: number) {
    setPending(`mapping-${id}`);
    try {
      await api.deleteJellyfinPathMapping(id);
      await reloadAfterLibraryChange();
      setError(null);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setPending(null);
    }
  }

  async function addLibrary(id: number) {
    setPending(`library-${id}`);
    try {
      await api.createLibraryFromJellyfin(id);
      await reloadAfterLibraryChange();
      onLibraryCreated?.();
      setError(null);
      setNotice(t("jellyfin.libraryCreated"));
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setPending(null);
    }
  }

  async function updateLibraryLink(jellyfinLibraryId: number, linkedLibraryId: number | null) {
    setPending(`library-link-${jellyfinLibraryId}`);
    try {
      await api.updateJellyfinLibraryLink(jellyfinLibraryId, linkedLibraryId);
      await reloadAfterLibraryChange();
      setError(null);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setPending(null);
    }
  }

  return (
    <AsyncPanel title={t("jellyfin.title")} loading={loading} error={error}>
      <div className="jellyfin-settings">
        <section className="jellyfin-settings-section" aria-labelledby="jellyfin-connection-heading">
          <div className="jellyfin-section-heading">
            <Server aria-hidden="true" />
            <div>
              <h3 id="jellyfin-connection-heading">{t("jellyfin.connection")}</h3>
              <p>{t("jellyfin.connectionDescription")}</p>
            </div>
          </div>
          <div className="jellyfin-form-grid">
            <label>
              <span>{t("jellyfin.serverUrl")}</span>
              <input type="url" value={baseUrl} placeholder="http://jellyfin:8096" onChange={(event) => setBaseUrl(event.target.value)} />
            </label>
            <label>
              <span>{t("jellyfin.apiKey")}</span>
              <input
                type="password"
                value={apiKey}
                placeholder={connection.api_key_configured ? t("jellyfin.apiKeyConfigured") : ""}
                autoComplete="new-password"
                onChange={(event) => setApiKey(event.target.value)}
                onFocus={() => setApiKeyFocused(true)}
                onBlur={() => setApiKeyFocused(false)}
              />
            </label>
            <div className="jellyfin-form-field">
              <span className="jellyfin-field-label">
                <label htmlFor="jellyfin-sync-interval">{t("jellyfin.syncInterval")}</label>
                <TooltipTrigger
                  ariaLabel={t("jellyfin.syncIntervalHelpAria")}
                  content={t("jellyfin.syncIntervalHelp")}
                >?</TooltipTrigger>
              </span>
              <input id="jellyfin-sync-interval" type="number" min="0" max="10080" value={syncInterval} onChange={(event) => setSyncInterval(event.target.value)} />
            </div>
          </div>
          <div className="jellyfin-actions">
            <button className="secondary small" type="button" disabled={pending !== null || syncRunning || autoSaving} onClick={() => void testConnection()}>
              <Link2 aria-hidden="true" /> {t("jellyfin.testConnection")}
            </button>
            <button className="secondary small" type="button" disabled={pending !== null || syncRunning || autoSaving || connectionDirty || !connection.enabled} onClick={() => void syncNow()}>
              <RefreshCw aria-hidden="true" className={pending === "sync" ? "is-spinning" : ""} /> {t("jellyfin.syncNow")}
            </button>
            {connectionSaveState !== "idle" ? (
              <span className={`jellyfin-auto-save-status status-${connectionSaveState}`} role="status" aria-live="polite">
                {connectionSaveState === "saving" ? <RefreshCw className="is-spinning" aria-hidden="true" /> : connectionSaveState === "error" ? <AlertTriangle aria-hidden="true" /> : <CheckCircle2 aria-hidden="true" />}
                {connectionSaveState === "error" ? connectionSaveError || t("jellyfin.autoSaveFailed") : t(`jellyfin.autoSave.${connectionSaveState}`)}
              </span>
            ) : null}
          </div>
          {notice ? <div className="notice success">{notice}</div> : null}
          {syncRunning ? (
            <div className="jellyfin-sync-progress" role="status" aria-live="polite">
              <div className="jellyfin-sync-progress-heading">
                <RefreshCw aria-hidden="true" className="is-spinning" />
                <div>
                  <strong>{t(`jellyfin.syncPhase.${syncPhase}`)}</strong>
                  {status?.sync_phase_detail ? <span>{status.sync_phase_detail}</span> : null}
                </div>
                {syncPercent !== null ? <b>{syncPercent}%</b> : null}
              </div>
              <div
                className={`jellyfin-sync-progress-track${syncPercent === null ? " is-indeterminate" : ""}`}
                role="progressbar"
                aria-label={t("jellyfin.syncProgress")}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={syncPercent ?? undefined}
              >
                <span style={syncPercent === null ? undefined : { width: `${syncPercent}%` }} />
              </div>
              {status?.sync_total ? (
                <span className="jellyfin-sync-progress-count">
                  {t("jellyfin.syncItemsProgress", { current: status.sync_current, total: status.sync_total })}
                </span>
              ) : null}
              <ol className="jellyfin-sync-steps" aria-label={t("jellyfin.syncSteps") }>
                {syncSteps.map((step, index) => (
                  <li key={step} className={index < syncStep ? "is-complete" : index === syncStep ? "is-active" : ""}>
                    <span aria-hidden="true">{index < syncStep ? "✓" : index + 1}</span>
                    {t(`jellyfin.syncStep.${step}`)}
                  </li>
                ))}
              </ol>
            </div>
          ) : null}
          <div className={`jellyfin-sync-summary status-${connection.last_status}`}>
            {connection.last_status === "error" ? <AlertTriangle aria-hidden="true" /> : connection.last_status === "running" ? <RefreshCw aria-hidden="true" className="is-spinning" /> : <CheckCircle2 aria-hidden="true" />}
            <div>
              <strong>{t(`jellyfin.status.${connection.last_status}`)}</strong>
              <span>{connection.last_successful_sync_at ? t("jellyfin.lastSync", { date: formatDate(connection.last_successful_sync_at) }) : t("jellyfin.notSynced")}</span>
              {connection.next_scheduled_sync_at ? <span>{t("jellyfin.nextSync", { date: formatDate(connection.next_scheduled_sync_at) })}</span> : null}
              {status ? <span>{t("jellyfin.matchSummary", { matched: status.matched_item_count, total: status.item_count })}</span> : null}
              {connection.last_error ? <span>{connection.last_error}</span> : null}
            </div>
          </div>
        </section>

        <section className="jellyfin-settings-section">
          <div className="jellyfin-section-heading"><h3>{t("jellyfin.users")}</h3></div>
          {!users.length ? <div className="notice">{t("jellyfin.usersEmpty")}</div> : (
            <div className="jellyfin-user-list">
              {users.map((user) => (
                <label key={user.jellyfin_user_id}>
                  <input type="checkbox" checked={user.enabled_for_sync} disabled={pending === `user-${user.jellyfin_user_id}`} onChange={() => void toggleUser(user.jellyfin_user_id)} />
                  <span>{user.name}</span>
                </label>
              ))}
            </div>
          )}
        </section>

        <section className="jellyfin-settings-section">
          <div className="jellyfin-section-heading"><h3>{t("jellyfin.pathMappings")}</h3></div>
          <div className="jellyfin-mapping-editor">
            <input value={mappingSource} placeholder={t("jellyfin.jellyfinPathPlaceholder")} aria-label={t("jellyfin.jellyfinPath")} onChange={(event) => setMappingSource(event.target.value)} />
            <span aria-hidden="true">→</span>
            <input value={mappingTarget} placeholder={t("jellyfin.medialyzePathPlaceholder")} aria-label={t("jellyfin.medialyzePath")} onChange={(event) => setMappingTarget(event.target.value)} />
            <button type="button" className="secondary icon-only-button" title={t("jellyfin.addMapping")} aria-label={t("jellyfin.addMapping")} disabled={pending !== null || !mappingSource.trim() || !mappingTarget.trim()} onClick={() => void addMapping()}><Plus aria-hidden="true" /></button>
          </div>
          <div className="jellyfin-mapping-list">
            {mappings.map((mapping) => (
              <div className="jellyfin-mapping-row" key={mapping.id}>
                <input
                  type="checkbox"
                  checked={mapping.enabled}
                  aria-label={t("jellyfin.mappingEnabled")}
                  onChange={() => {
                    setPending(`mapping-${mapping.id}`);
                    void api.updateJellyfinPathMapping(mapping.id, { enabled: !mapping.enabled })
                      .then(() => reloadAfterLibraryChange())
                      .catch((reason: Error) => setError(reason.message))
                      .finally(() => setPending(null));
                  }}
                />
                <code>{mapping.jellyfin_path_prefix}</code><span aria-hidden="true">→</span><code>{mapping.medialyze_path_prefix}</code>
                <button type="button" className="secondary icon-only-button" title={t("common.delete")} aria-label={t("common.delete")} disabled={pending === `mapping-${mapping.id}`} onClick={() => void deleteMapping(mapping.id)}><Trash2 aria-hidden="true" /></button>
              </div>
            ))}
          </div>
        </section>

        <section className="jellyfin-settings-section">
          <div className="jellyfin-section-heading"><h3>{t("jellyfin.libraries")}</h3></div>
          {!libraries.length ? <div className="notice">{t("jellyfin.librariesEmpty")}</div> : (
            <div className="jellyfin-library-list">
              {libraries.map((library) => (
                <article
                  id={`jellyfin-library-${library.id}`}
                  tabIndex={-1}
                  className={`jellyfin-library-card${library.mapped_status === "path_unmapped" || library.mapped_status === "path_not_accessible" ? " is-mappable" : ""}${highlightedLibraryId === String(library.id) ? " is-highlighted" : ""}`}
                  key={library.id}
                >
                  <div className="jellyfin-library-card-head">
                    <div><strong>{library.name}</strong><span>{library.collection_type || t("fileTable.na")}</span></div>
                    <span className={`jellyfin-status-badge status-${library.mapped_status}`}>{t(`jellyfin.libraryStatus.${library.mapped_status}`)}</span>
                  </div>
                  {library.mapped_status === "path_unmapped" || library.mapped_status === "path_not_accessible" ? (
                    <div className="jellyfin-library-mapping-list">
                      {library.locations.map((location) => {
                        const exactMapping = mappings.find(
                          (mapping) => normalizedPath(mapping.jellyfin_path_prefix) === normalizedPath(location),
                        );
                        return (
                          <JellyfinLibraryMappingEditor
                            key={libraryMappingKey(library.id, location)}
                            library={library}
                            location={location}
                            mapping={exactMapping}
                            disabled={pending !== null || syncRunning}
                            onSaved={reloadAfterLibraryChange}
                          />
                        );
                      })}
                    </div>
                  ) : (
                    <div className="jellyfin-library-paths">
                      {library.locations.map((path) => <code key={path}>{path}</code>)}
                      {library.mapped_locations.map((path) => <code className="mapped" key={path}>{path}</code>)}
                    </div>
                  )}
                  <div className="jellyfin-library-association">
                    <label htmlFor={`jellyfin-library-link-${library.id}`}>{t("jellyfin.associatedMedialyzeLibrary")}</label>
                    <select
                      id={`jellyfin-library-link-${library.id}`}
                      value={library.linked_library_id ?? ""}
                      disabled={pending !== null || syncRunning}
                      onChange={(event) => void updateLibraryLink(library.id, event.target.value ? Number(event.target.value) : null)}
                    >
                      <option value="">{t("jellyfin.noAssociatedLibrary")}</option>
                      {medialyzeLibraries.map((candidate) => (
                        <option key={candidate.id} value={candidate.id}>{candidate.name}</option>
                      ))}
                    </select>
                    {library.link_method ? <span>{t(`jellyfin.linkMethod.${library.link_method}`)}</span> : null}
                    {library.linked_library_id === null ? (
                      <button
                        type="button"
                        className="secondary small"
                        title={!library.can_create_medialyze_library ? t("jellyfin.addAsLibraryUnavailable") : undefined}
                        disabled={pending !== null || !library.can_create_medialyze_library}
                        onClick={() => void addLibrary(library.id)}
                      ><Plus aria-hidden="true" />{t("jellyfin.addAsLibrary")}</button>
                    ) : null}
                  </div>
                </article>
              ))}
            </div>
          )}
        </section>
      </div>
    </AsyncPanel>
  );
}

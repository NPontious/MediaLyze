import {
  AlertTriangle,
  CheckCircle2,
  CircleStop,
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
  type JellyfinMatchRecomputeStatus,
  type JellyfinPathMapping,
  type JellyfinSyncStatus,
  type JellyfinUser,
  type LibrarySummary,
} from "../lib/api";
import { formatDate } from "../lib/format";
import { useJellyfinSyncPolling } from "../hooks/useJellyfinSyncPolling";

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

const IDLE_MATCH_RECOMPUTE: JellyfinMatchRecomputeStatus = {
  status: "idle",
  active: false,
  rerun_pending: false,
  last_error: null,
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

function libraryShowsMappingEditor(library: JellyfinLibrary) {
  return library.linked_library_id === null && [
    "path_unmapped",
    "path_not_accessible",
    "updating",
  ].includes(library.mapped_status);
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
  onSaved: (mapping: JellyfinPathMapping) => void;
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
        let savedMapping: JellyfinPathMapping;
        if (mapping) {
          savedMapping = await api.updateJellyfinPathMapping(mapping.id, {
            medialyze_path_prefix: nextTarget,
            enabled: true,
          });
        } else {
          savedMapping = await api.createJellyfinPathMapping({
            jellyfin_path_prefix: location,
            medialyze_path_prefix: nextTarget,
            enabled: true,
          });
        }
        failedSignatureRef.current = null;
        onSaved(savedMapping);
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
          {saveState === "saving" ? t("jellyfin.operation.updatingMapping") : null}
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
  const [matchRecompute, setMatchRecompute] = useState<JellyfinMatchRecomputeStatus>(IDLE_MATCH_RECOMPUTE);
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
  const [loadError, setLoadError] = useState<string | null>(null);
  const [connectionActionError, setConnectionActionError] = useState<string | null>(null);
  const [usersError, setUsersError] = useState<string | null>(null);
  const [mappingError, setMappingError] = useState<string | null>(null);
  const [libraryErrors, setLibraryErrors] = useState<Record<number, string | null>>({});
  const [notice, setNotice] = useState<string | null>(null);
  const [cancelNotice, setCancelNotice] = useState<string | null>(null);
  const [cancelPending, setCancelPending] = useState(false);
  const saveFeedbackTimerRef = useRef<number | null>(null);
  const failedAutoSaveSignatureRef = useRef<string | null>(null);
  const activeSyncJobIdRef = useRef<number | null>(null);
  const onLibrariesChangedRef = useRef(onLibrariesChanged);
  onLibrariesChangedRef.current = onLibrariesChanged;

  const load = useCallback(async () => {
    const [nextStatus, nextUsers, nextMappings, nextLibraries] = await Promise.all([
      api.jellyfinSyncStatus(),
      api.jellyfinUsers(),
      api.jellyfinPathMappings(),
      api.jellyfinLibraries(),
    ]);
    setConnection(nextStatus);
    setStatus(nextStatus);
    activeSyncJobIdRef.current = nextStatus.sync_job_active ? nextStatus.sync_job_id : null;
    setUsers(nextUsers);
    setMappings(nextMappings);
    setLibraries(nextLibraries);
    setBaseUrl(nextStatus.base_url);
    setSyncInterval(String(nextStatus.sync_interval_minutes));
  }, []);

  const reloadAfterLibraryChange = useCallback(async () => {
    const [nextMappings, nextLibraries] = await Promise.all([
      api.jellyfinPathMappings(),
      api.jellyfinLibraries(),
    ]);
    setMappings(nextMappings);
    setLibraries(nextLibraries);
    onLibrariesChangedRef.current?.();
  }, []);

  const markMatchRecomputeQueued = useCallback(() => {
    setMatchRecompute({
      status: "queued",
      active: true,
      rerun_pending: false,
      last_error: null,
    });
    setLibraries((current) => current.map((library) => (
      library.linked_library_id === null
        ? { ...library, mapped_status: "updating" }
        : library
    )));
  }, []);

  const applySavedMapping = useCallback((savedMapping: JellyfinPathMapping) => {
    setMappings((current) => {
      const exists = current.some((mapping) => mapping.id === savedMapping.id);
      return exists
        ? current.map((mapping) => mapping.id === savedMapping.id ? savedMapping : mapping)
        : [...current, savedMapping];
    });
    markMatchRecomputeQueued();
  }, [markMatchRecomputeQueued]);

  useEffect(() => {
    setLoading(true);
    load()
      .then(() => setLoadError(null))
      .catch((reason: Error) => setLoadError(reason.message))
      .finally(() => setLoading(false));
  }, [load]);

  useEffect(() => {
    let active = true;
    api.jellyfinMatchRecomputeStatus()
      .then((nextStatus) => {
        if (active) setMatchRecompute(nextStatus);
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!matchRecompute.active) return;
    let active = true;
    let completionHandled = false;
    const refreshMatchStatus = async () => {
      try {
        const nextStatus = await api.jellyfinMatchRecomputeStatus();
        if (!active) return;
        setMatchRecompute(nextStatus);
        if (!nextStatus.active && !completionHandled) {
          completionHandled = true;
          await reloadAfterLibraryChange();
        }
      } catch (reason) {
        if (!active) return;
        setMatchRecompute({
          status: "error",
          active: false,
          rerun_pending: false,
          last_error: (reason as Error).message,
        });
      }
    };
    void refreshMatchStatus();
    const timer = window.setInterval(() => void refreshMatchStatus(), 750);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [matchRecompute.active, reloadAfterLibraryChange]);

  useEffect(() => {
    if (!highlightedLibraryId || loading) return;
    const element = document.getElementById(`jellyfin-library-${highlightedLibraryId}`);
    element?.focus({ preventScroll: true });
    element?.scrollIntoView({ behavior: "auto", block: "center" });
  }, [highlightedLibraryId, loading, libraries]);

  const syncRunning = pending === "sync" || Boolean(status?.sync_job_active) || connection.last_status === "running";
  const syncDisplayStatus = syncRunning ? "running" : connection.last_status;
  const connectionBusy = pending === "test" || pending === "sync";
  const usersBusy = pending?.startsWith("user-") ?? false;
  const mappingBusy = pending === "mapping-new" || (pending?.startsWith("mapping-") ?? false);
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

  const handleSyncStatus = useCallback((nextStatus: JellyfinSyncStatus) => {
    setStatus(nextStatus);
    setConnection(nextStatus);
  }, []);
  const handleSyncCompleted = useCallback(async (nextStatus: JellyfinSyncStatus) => {
    const items = Number(nextStatus.sync_summary.items_synced || 0);
    const syncedLibraries = Number(nextStatus.sync_summary.libraries_synced || 0);
    setNotice(t("jellyfin.syncSucceeded", { items, libraries: syncedLibraries }));
    await reloadAfterLibraryChange();
  }, [reloadAfterLibraryChange, t]);
  const handleSyncCanceled = useCallback(() => {
    setCancelNotice(t("jellyfin.syncCanceled"));
  }, [t]);
  const handleSyncFailed = useCallback((nextStatus: JellyfinSyncStatus) => {
    setConnectionActionError(
      nextStatus.sync_job_error || nextStatus.last_error || t("jellyfin.testFailed"),
    );
  }, [t]);
  useJellyfinSyncPolling({
    active: syncRunning,
    trackedJobId: activeSyncJobIdRef,
    onStatus: handleSyncStatus,
    onCompleted: handleSyncCompleted,
    onCanceled: handleSyncCanceled,
    onFailed: handleSyncFailed,
  });

  async function testConnection() {
    setPending("test");
    setConnectionActionError(null);
    setNotice(null);
    try {
      const result = await api.testJellyfinConnection({
        base_url: baseUrl.trim(),
        ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
      });
      if (!result.ok) {
        setConnectionActionError(result.error || t("jellyfin.testFailed"));
      } else {
        setNotice(t("jellyfin.testSucceeded", { name: result.server_name || "Jellyfin", version: result.server_version || "" }));
      }
    } catch (reason) {
      setConnectionActionError((reason as Error).message);
    } finally {
      setPending(null);
    }
  }

  async function toggleIntegration() {
    setPending("connection-toggle");
    setConnectionActionError(null);
    try {
      const updated = await api.updateJellyfinConnection({ enabled: !connection.enabled });
      setConnection(updated);
      setStatus((current) => current ? { ...current, ...updated } : current);
    } catch (reason) {
      setConnectionActionError((reason as Error).message);
    } finally {
      setPending(null);
    }
  }

  async function disconnectIntegration() {
    if (!window.confirm(t("jellyfin.disconnectConfirm"))) return;
    setPending("connection-disconnect");
    setConnectionActionError(null);
    try {
      await api.disconnectJellyfin();
      setConnection(EMPTY_CONNECTION);
      setStatus(null);
      setUsers([]);
      setMappings([]);
      setLibraries([]);
      setBaseUrl("");
      setApiKey("");
      setSyncInterval("60");
      setNotice(t("jellyfin.disconnected"));
    } catch (reason) {
      setConnectionActionError((reason as Error).message);
    } finally {
      setPending(null);
    }
  }

  async function syncNow() {
    setPending("sync");
    setConnectionActionError(null);
    setNotice(null);
    setCancelNotice(null);
    try {
      const result = await api.syncJellyfin();
      activeSyncJobIdRef.current = result.job_id;
      setStatus((current) => current ? {
        ...current,
        sync_job_id: result.job_id,
        sync_job_status: result.status,
        sync_trigger_source: result.trigger_source,
        sync_job_active: true,
        sync_job_error: null,
        sync_summary: {},
      } : current);
    } catch (reason) {
      setConnectionActionError((reason as Error).message);
    } finally {
      setPending(null);
    }
  }

  async function cancelSync() {
    setCancelPending(true);
    setConnectionActionError(null);
    try {
      const result = await api.cancelJellyfinSync(status?.sync_job_id);
      if (result.cancellation_requested) {
        setCancelNotice(t("jellyfin.cancelRequested"));
        setStatus((current) => current ? { ...current, cancellation_requested: true } : current);
      }
    } catch (reason) {
      setConnectionActionError((reason as Error).message);
    } finally {
      setCancelPending(false);
    }
  }

  const syncPhase = status?.sync_phase || "connecting";
  const syncStep = SYNC_PHASE_STEP[syncPhase] ?? 0;
  const syncPercent = status?.sync_total
    ? Math.min(100, Math.round((status.sync_current / status.sync_total) * 100))
    : null;
  const syncSteps = ["connection", "catalog", "items", "matching"];
  const cancellationRequested = cancelPending || Boolean(status?.cancellation_requested);

  async function toggleUser(userId: string) {
    const enabledIds = users
      .filter((user) => user.enabled_for_sync !== (user.jellyfin_user_id === userId))
      .map((user) => user.jellyfin_user_id);
    setPending(`user-${userId}`);
    setUsersError(null);
    try {
      setUsers(await api.updateJellyfinUsers(enabledIds));
    } catch (reason) {
      setUsersError((reason as Error).message);
    } finally {
      setPending(null);
    }
  }

  async function addMapping() {
    if (!mappingSource.trim() || !mappingTarget.trim()) return;
    setPending("mapping-new");
    setMappingError(null);
    try {
      const savedMapping = await api.createJellyfinPathMapping({
        jellyfin_path_prefix: mappingSource.trim(),
        medialyze_path_prefix: mappingTarget.trim(),
        enabled: true,
      });
      applySavedMapping(savedMapping);
      setMappingSource("");
      setMappingTarget("");
    } catch (reason) {
      setMappingError((reason as Error).message);
    } finally {
      setPending(null);
    }
  }

  async function deleteMapping(id: number) {
    const previousMappings = mappings;
    setPending(`mapping-${id}`);
    setMappingError(null);
    setMappings((current) => current.filter((mapping) => mapping.id !== id));
    try {
      await api.deleteJellyfinPathMapping(id);
      markMatchRecomputeQueued();
    } catch (reason) {
      setMappings(previousMappings);
      setMappingError((reason as Error).message);
    } finally {
      setPending(null);
    }
  }

  async function toggleMapping(mapping: JellyfinPathMapping) {
    const previousMappings = mappings;
    const nextEnabled = !mapping.enabled;
    setPending(`mapping-${mapping.id}`);
    setMappingError(null);
    setMappings((current) => current.map((candidate) => (
      candidate.id === mapping.id ? { ...candidate, enabled: nextEnabled } : candidate
    )));
    try {
      const updated = await api.updateJellyfinPathMapping(mapping.id, { enabled: nextEnabled });
      applySavedMapping(updated);
    } catch (reason) {
      setMappings(previousMappings);
      setMappingError((reason as Error).message);
    } finally {
      setPending(null);
    }
  }

  async function addLibrary(id: number) {
    setPending(`library-${id}`);
    setLibraryErrors((current) => ({ ...current, [id]: null }));
    try {
      await api.createLibraryFromJellyfin(id);
      await reloadAfterLibraryChange();
      onLibraryCreated?.();
      setNotice(t("jellyfin.libraryCreated"));
    } catch (reason) {
      setLibraryErrors((current) => ({ ...current, [id]: (reason as Error).message }));
    } finally {
      setPending(null);
    }
  }

  async function updateLibraryLink(jellyfinLibraryId: number, linkedLibraryId: number | null) {
    const previousLibraries = libraries;
    const linkedLibraryName = medialyzeLibraries.find((library) => library.id === linkedLibraryId)?.name ?? null;
    setPending(`library-link-${jellyfinLibraryId}`);
    setLibraryErrors((current) => ({ ...current, [jellyfinLibraryId]: null }));
    setLibraries((current) => current.map((library) => {
      if (library.id === jellyfinLibraryId) {
        return {
          ...library,
          linked_library_id: linkedLibraryId,
          linked_library_name: linkedLibraryName,
          link_method: "manual",
          mapped_status: linkedLibraryId === null ? "updating" : "linked",
          data_scope: linkedLibraryId === null ? "jellyfin_only" : "linked",
        };
      }
      if (linkedLibraryId !== null && library.linked_library_id === linkedLibraryId) {
        return {
          ...library,
          linked_library_id: null,
          linked_library_name: null,
          link_method: "manual",
          mapped_status: "updating",
          data_scope: "jellyfin_only",
        };
      }
      return library;
    }));
    try {
      const updated = await api.updateJellyfinLibraryLink(jellyfinLibraryId, linkedLibraryId);
      setLibraries((current) => current.map((library) => library.id === updated.id ? updated : library));
      markMatchRecomputeQueued();
    } catch (reason) {
      setLibraries(previousLibraries);
      setLibraryErrors((current) => ({ ...current, [jellyfinLibraryId]: (reason as Error).message }));
    } finally {
      setPending(null);
    }
  }

  return (
    <AsyncPanel title={t("jellyfin.title")} loading={loading} error={loadError}>
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
            <button
              className="secondary small"
              type="button"
              disabled={connectionBusy || syncRunning || autoSaving || connectionDirty || !connection.api_key_configured || !connection.base_url}
              onClick={() => void toggleIntegration()}
            >
              {connection.enabled ? t("jellyfin.disableIntegration") : t("jellyfin.enableIntegration")}
            </button>
            <button className="secondary small" type="button" disabled={connectionBusy || syncRunning || autoSaving} onClick={() => void testConnection()}>
              {pending === "test" ? <RefreshCw aria-hidden="true" className="is-spinning" /> : <Link2 aria-hidden="true" />} {t("jellyfin.testConnection")}
            </button>
            <button className="secondary small" type="button" disabled={connectionBusy || syncRunning || autoSaving || connectionDirty || !connection.enabled} onClick={() => void syncNow()}>
              <RefreshCw aria-hidden="true" className={pending === "sync" ? "is-spinning" : ""} /> {t("jellyfin.syncNow")}
            </button>
            {syncRunning ? (
              <button className="secondary small" type="button" disabled={cancellationRequested} onClick={() => void cancelSync()}>
                <CircleStop aria-hidden="true" /> {cancellationRequested ? t("jellyfin.cancelingSync") : t("jellyfin.cancelSync")}
              </button>
            ) : null}
            <button
              className="secondary small danger"
              type="button"
              disabled={connectionBusy || syncRunning || autoSaving || (!connection.api_key_configured && !connection.base_url)}
              onClick={() => void disconnectIntegration()}
            >
              <Trash2 aria-hidden="true" /> {t("jellyfin.disconnect")}
            </button>
            {connectionSaveState !== "idle" ? (
              <span className={`jellyfin-auto-save-status status-${connectionSaveState}`} role="status" aria-live="polite">
                {connectionSaveState === "saving" ? <RefreshCw className="is-spinning" aria-hidden="true" /> : connectionSaveState === "error" ? <AlertTriangle aria-hidden="true" /> : <CheckCircle2 aria-hidden="true" />}
                {connectionSaveState === "error" ? connectionSaveError || t("jellyfin.autoSaveFailed") : t(`jellyfin.autoSave.${connectionSaveState}`)}
              </span>
            ) : null}
            {pending === "test" ? <span className="jellyfin-operation-status" role="status"><RefreshCw aria-hidden="true" className="is-spinning" />{t("jellyfin.operation.testingConnection")}</span> : null}
          </div>
          {connectionActionError ? <div className="alert jellyfin-inline-error" role="alert">{connectionActionError}</div> : null}
          {notice ? <div className="notice success">{notice}</div> : null}
          {cancelNotice ? <div className="notice" role="status">{cancelNotice}</div> : null}
          {syncRunning ? (
            <div className={`jellyfin-sync-progress${cancellationRequested ? " is-canceling" : ""}`} role="status" aria-live="polite">
              <div className="jellyfin-sync-progress-heading">
                <RefreshCw aria-hidden="true" className="is-spinning" />
                <div>
                  <strong>{cancellationRequested ? t("jellyfin.cancelingSync") : t(`jellyfin.syncPhase.${syncPhase}`)}</strong>
                  {cancellationRequested ? <span>{t("jellyfin.cancelingSyncDetail")}</span> : status?.sync_phase_detail ? <span>{status.sync_phase_detail}</span> : null}
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
          <div className={`jellyfin-sync-summary status-${syncDisplayStatus}`}>
            {syncDisplayStatus === "error" ? <AlertTriangle aria-hidden="true" /> : syncDisplayStatus === "running" ? <RefreshCw aria-hidden="true" className="is-spinning" /> : syncDisplayStatus === "canceled" ? <CircleStop aria-hidden="true" /> : <CheckCircle2 aria-hidden="true" />}
            <div>
              <strong>{t(`jellyfin.status.${syncDisplayStatus}`)}</strong>
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
          {usersError ? <div className="alert jellyfin-inline-error" role="alert">{usersError}</div> : null}
          {usersBusy ? <div className="jellyfin-operation-status" role="status"><RefreshCw aria-hidden="true" className="is-spinning" />{t("jellyfin.operation.savingUsers")}</div> : null}
        </section>

        <section className="jellyfin-settings-section">
          <div className="jellyfin-section-heading"><h3>{t("jellyfin.pathMappings")}</h3></div>
          <div className="jellyfin-mapping-editor">
            <input value={mappingSource} placeholder={t("jellyfin.jellyfinPathPlaceholder")} aria-label={t("jellyfin.jellyfinPath")} onChange={(event) => setMappingSource(event.target.value)} />
            <span aria-hidden="true">→</span>
            <input value={mappingTarget} placeholder={t("jellyfin.medialyzePathPlaceholder")} aria-label={t("jellyfin.medialyzePath")} onChange={(event) => setMappingTarget(event.target.value)} />
            <button type="button" className="secondary icon-only-button" title={t("jellyfin.addMapping")} aria-label={t("jellyfin.addMapping")} disabled={mappingBusy || syncRunning || !mappingSource.trim() || !mappingTarget.trim()} onClick={() => void addMapping()}>{pending === "mapping-new" ? <RefreshCw aria-hidden="true" className="is-spinning" /> : <Plus aria-hidden="true" />}</button>
          </div>
          <div className="jellyfin-mapping-list">
            {mappings.map((mapping) => (
              <div className="jellyfin-mapping-row" key={mapping.id}>
                <input
                  type="checkbox"
                  checked={mapping.enabled}
                  disabled={mappingBusy || syncRunning}
                  aria-label={t("jellyfin.mappingEnabled")}
                  onChange={() => void toggleMapping(mapping)}
                />
                <code>{mapping.jellyfin_path_prefix}</code><span aria-hidden="true">→</span><code>{mapping.medialyze_path_prefix}</code>
                <button type="button" className="secondary icon-only-button" title={t("common.delete")} aria-label={t("common.delete")} disabled={mappingBusy || syncRunning} onClick={() => void deleteMapping(mapping.id)}>{pending === `mapping-${mapping.id}` ? <RefreshCw aria-hidden="true" className="is-spinning" /> : <Trash2 aria-hidden="true" />}</button>
              </div>
            ))}
          </div>
          {mappingBusy ? <div className="jellyfin-operation-status" role="status"><RefreshCw aria-hidden="true" className="is-spinning" />{t("jellyfin.operation.updatingMapping")}</div> : null}
          {matchRecompute.active ? <div className="jellyfin-operation-status jellyfin-operation-status-prominent" role="status"><RefreshCw aria-hidden="true" className="is-spinning" /><span><strong>{t("jellyfin.backgroundUpdate.title")}</strong>{t("jellyfin.backgroundUpdate.detail")}</span></div> : null}
          {!matchRecompute.active && matchRecompute.status === "error" ? <div className="alert jellyfin-inline-error" role="alert">{t("jellyfin.backgroundUpdate.error", { message: matchRecompute.last_error || t("fileTable.na") })}</div> : null}
          {mappingError ? <div className="alert jellyfin-inline-error" role="alert">{mappingError}</div> : null}
        </section>

        <section className="jellyfin-settings-section">
          <div className="jellyfin-section-heading"><h3>{t("jellyfin.libraries")}</h3></div>
          {!libraries.length ? <div className="notice">{t("jellyfin.librariesEmpty")}</div> : (
            <div className="jellyfin-library-list">
              {libraries.map((library) => (
                <article
                  id={`jellyfin-library-${library.id}`}
                  tabIndex={-1}
                  className={`jellyfin-library-card${libraryShowsMappingEditor(library) ? " is-mappable" : ""}${highlightedLibraryId === String(library.id) ? " is-highlighted" : ""}`}
                  key={library.id}
                >
                  <div className="jellyfin-library-card-head">
                    <div><strong>{library.name}</strong><span>{library.collection_type || t("fileTable.na")}</span></div>
                    <span className={`jellyfin-status-badge status-${library.mapped_status}`}>{t(`jellyfin.libraryStatus.${library.mapped_status}`)}</span>
                  </div>
                  {libraryShowsMappingEditor(library) ? (
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
                            disabled={mappingBusy || syncRunning}
                            onSaved={applySavedMapping}
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
                      disabled={pending === `library-link-${library.id}` || pending === `library-${library.id}` || syncRunning}
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
                        disabled={pending === `library-link-${library.id}` || pending === `library-${library.id}` || !library.can_create_medialyze_library}
                        onClick={() => void addLibrary(library.id)}
                      ><Plus aria-hidden="true" />{t("jellyfin.addAsLibrary")}</button>
                    ) : null}
                  </div>
                  {pending === `library-link-${library.id}` || pending === `library-${library.id}` ? <div className="jellyfin-operation-status" role="status"><RefreshCw aria-hidden="true" className="is-spinning" />{t("jellyfin.operation.updatingLibrary")}</div> : null}
                  {libraryErrors[library.id] ? <div className="alert jellyfin-inline-error" role="alert">{libraryErrors[library.id]}</div> : null}
                </article>
              ))}
            </div>
          )}
        </section>
      </div>
    </AsyncPanel>
  );
}

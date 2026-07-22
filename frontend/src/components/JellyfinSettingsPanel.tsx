import {
  AlertTriangle,
  CheckCircle2,
  CircleStop,
  Link2,
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
  type JellyfinSyncStatus,
  type JellyfinUser,
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

const SYNC_PHASE_STEP: Record<string, number> = {
  connecting: 0,
  users: 1,
  libraries: 1,
  items: 2,
  saving: 2,
  cleanup: 2,
  matching: 3,
};

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

export function JellyfinSettingsPanel({ onCatalogChanged }: { onCatalogChanged?: () => void }) {
  const { t } = useTranslation();
  const [connection, setConnection] = useState<JellyfinConnection>(EMPTY_CONNECTION);
  const [status, setStatus] = useState<JellyfinSyncStatus | null>(null);
  const [users, setUsers] = useState<JellyfinUser[]>([]);
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [syncInterval, setSyncInterval] = useState("60");
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState<string | null>(null);
  const [autoSaving, setAutoSaving] = useState(false);
  const [connectionSaveState, setConnectionSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [connectionSaveError, setConnectionSaveError] = useState<string | null>(null);
  const [apiKeyFocused, setApiKeyFocused] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [connectionActionError, setConnectionActionError] = useState<string | null>(null);
  const [usersError, setUsersError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [cancelNotice, setCancelNotice] = useState<string | null>(null);
  const [cancelPending, setCancelPending] = useState(false);
  const saveFeedbackTimerRef = useRef<number | null>(null);
  const failedAutoSaveSignatureRef = useRef<string | null>(null);
  const activeSyncJobIdRef = useRef<number | null>(null);
  const onCatalogChangedRef = useRef(onCatalogChanged);
  onCatalogChangedRef.current = onCatalogChanged;

  const load = useCallback(async () => {
    const [nextStatus, nextUsers] = await Promise.all([
      api.jellyfinSyncStatus(),
      api.jellyfinUsers(),
    ]);
    setConnection(nextStatus);
    setStatus(nextStatus);
    activeSyncJobIdRef.current = nextStatus.sync_job_active ? nextStatus.sync_job_id : null;
    setUsers(nextUsers);
    setBaseUrl(nextStatus.base_url);
    setSyncInterval(String(nextStatus.sync_interval_minutes));
  }, []);

  useEffect(() => {
    setLoading(true);
    load()
      .then(() => setLoadError(null))
      .catch((reason: Error) => setLoadError(reason.message))
      .finally(() => setLoading(false));
  }, [load]);

  const syncRunning = pending === "sync" || Boolean(status?.sync_job_active) || connection.last_status === "running";
  const syncDisplayStatus = syncRunning ? "running" : connection.last_status;
  const connectionBusy = pending === "test" || pending === "sync";
  const usersBusy = pending?.startsWith("user-") ?? false;
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
    onCatalogChangedRef.current?.();
  }, [t]);
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

      </div>
    </AsyncPanel>
  );
}

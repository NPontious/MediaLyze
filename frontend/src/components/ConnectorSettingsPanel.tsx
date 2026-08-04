import { Check, CircleStop, Plus, RefreshCw, Server, Trash2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { AsyncPanel } from "./AsyncPanel";
import { JellyfinSettingsPanel } from "./JellyfinSettingsPanel";
import {
  api,
  type ConnectorBinding,
  type ConnectorBindingWrite,
  type ConnectorConnection,
  type ConnectorLibrary,
  type LibrarySummary,
} from "../lib/api";

type ConnectorCatalog = {
  libraries: ConnectorLibrary[];
  bindings: ConnectorBinding[];
};

export function ConnectorSettingsPanel({ onCatalogChanged }: { onCatalogChanged?: () => void }) {
  const { t } = useTranslation();
  const [connections, setConnections] = useState<ConnectorConnection[]>([]);
  const [providers, setProviders] = useState<string[]>([]);
  const [libraries, setLibraries] = useState<LibrarySummary[]>([]);
  const [catalogs, setCatalogs] = useState<Record<number, ConnectorCatalog>>({});
  const [drafts, setDrafts] = useState<Record<number, Record<number, ConnectorBindingWrite>>>({});
  const [advanced, setAdvanced] = useState(false);
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [provider, setProvider] = useState("jellyfin");
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [secret, setSecret] = useState("");

  const load = useCallback(async () => {
    const [nextConnections, nextProviders, nextLibraries] = await Promise.all([
      api.connectors(),
      api.connectorProviders(),
      api.libraries(),
    ]);
    const entries = await Promise.all(nextConnections.map(async (connection) => {
      const [remoteLibraries, bindings] = await Promise.all([
        api.connectorLibraries(connection.id),
        api.connectorBindings(connection.id),
      ]);
      return [connection.id, { libraries: remoteLibraries, bindings }] as const;
    }));
    const nextCatalogs = Object.fromEntries(entries) as Record<number, ConnectorCatalog>;
    const nextDrafts: Record<number, Record<number, ConnectorBindingWrite>> = {};
    for (const connection of nextConnections) {
      const catalog = nextCatalogs[connection.id];
      nextDrafts[connection.id] = {};
      for (const remoteLibrary of catalog.libraries) {
        for (const location of remoteLibrary.locations) {
          const binding = catalog.bindings.find((candidate) => candidate.location_id === location.id);
          nextDrafts[connection.id][location.id] = binding
            ? {
                id: binding.id,
                location_id: binding.location_id,
                library_root_id: binding.library_root_id,
                source_prefix: binding.source_prefix,
                target_subpath: binding.target_subpath,
                case_mode: binding.case_mode,
                priority: binding.priority,
                active: binding.active,
              }
            : {
                location_id: location.id,
                library_root_id: 0,
                source_prefix: location.remote_path,
                target_subpath: "",
                case_mode: "sensitive",
                priority: 0,
                active: true,
              };
        }
      }
    }
    setConnections(nextConnections);
    setProviders(nextProviders);
    setLibraries(nextLibraries);
    setCatalogs(nextCatalogs);
    setDrafts(nextDrafts);
  }, []);

  useEffect(() => {
    setLoading(true);
    load().catch((reason: Error) => setError(reason.message)).finally(() => setLoading(false));
  }, [load]);

  const rootOptions = useMemo(() => libraries.flatMap((library) =>
    (library.roots ?? []).map((root) => ({
      id: root.id,
      label: `${library.name} · ${root.display_name}`,
      path: root.path,
    }))), [libraries]);

  function updateDraft(connectionId: number, locationId: number, update: Partial<ConnectorBindingWrite>) {
    setDrafts((current) => ({
      ...current,
      [connectionId]: {
        ...current[connectionId],
        [locationId]: { ...current[connectionId]?.[locationId], ...update },
      },
    }));
  }

  async function createConnection() {
    setPending("create");
    setError(null);
    try {
      await api.createConnector({
        provider,
        name: name.trim() || provider,
        base_url: baseUrl.trim(),
        secret: secret.trim(),
        enabled: false,
      });
      setName("");
      setBaseUrl("");
      setSecret("");
      setCreateOpen(false);
      await load();
      setNotice(t("connectors.created"));
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setPending(null);
    }
  }

  async function updateConnection(connection: ConnectorConnection, update: Parameters<typeof api.updateConnector>[1]) {
    setPending(`connection-${connection.id}`);
    setError(null);
    try {
      await api.updateConnector(connection.id, update);
      await load();
      setNotice(t("connectors.saved"));
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setPending(null);
    }
  }

  async function runAction(connection: ConnectorConnection, action: "test" | "sync" | "cancel" | "delete") {
    setPending(`${action}-${connection.id}`);
    setError(null);
    try {
      if (action === "test") {
        const result = await api.testConnector(connection.id);
        if (!result.success) throw new Error(result.error || t("connectors.testFailed"));
        setNotice(t("connectors.testSucceeded", { name: result.server_name || connection.name }));
      } else if (action === "sync") {
        await api.syncConnector(connection.id);
        setNotice(t("connectors.syncQueued"));
      } else if (action === "cancel") {
        await api.cancelConnectorSync(connection.id);
        setNotice(t("connectors.cancelRequested"));
      } else {
        if (!window.confirm(t("connectors.deleteConfirm", { name: connection.name }))) return;
        await api.deleteConnector(connection.id);
        setNotice(t("connectors.deleted"));
      }
      await load();
      onCatalogChanged?.();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setPending(null);
    }
  }

  async function saveBindings(connectionId: number) {
    setPending(`bindings-${connectionId}`);
    setError(null);
    try {
      const values = Object.values(drafts[connectionId] ?? {}).filter(
        (binding) => binding.library_root_id > 0,
      );
      await api.updateConnectorBindings(connectionId, values);
      await load();
      onCatalogChanged?.();
      setNotice(t("connectors.bindingsSaved"));
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setPending(null);
    }
  }

  return (
    <div className="settings-sidebar-stack connector-settings-stack">
      <AsyncPanel title={t("connectors.title")} loading={loading} error={error}>
        <div className="connector-panel-heading">
          <p>{t("connectors.description")}</p>
          <button type="button" className="secondary small" onClick={() => setCreateOpen((value) => !value)}>
            <Plus aria-hidden="true" /> {t("connectors.addConnection")}
          </button>
        </div>
        {notice ? <div className="notice success"><Check aria-hidden="true" /> {notice}</div> : null}
        {createOpen ? (
          <section className="connector-card connector-create-card">
            <div className="connector-form-grid">
              <label><span>{t("connectors.provider")}</span><select value={provider} onChange={(event) => setProvider(event.target.value)}>{providers.map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
              <label><span>{t("connectors.name")}</span><input value={name} onChange={(event) => setName(event.target.value)} /></label>
              <label><span>{t("connectors.serverUrl")}</span><input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} /></label>
              <label><span>{t("connectors.secret")}</span><input type="password" value={secret} onChange={(event) => setSecret(event.target.value)} /></label>
            </div>
            <button type="button" disabled={pending === "create" || !baseUrl.trim()} onClick={() => void createConnection()}>{t("connectors.create")}</button>
          </section>
        ) : null}
        <div className="connector-card-list">
          {connections.length === 0 ? <p className="field-hint">{t("connectors.empty")}</p> : null}
          {connections.map((connection) => {
            const catalog = catalogs[connection.id];
            return (
              <section className="connector-card" key={connection.id}>
                <header><div><span className="badge">{connection.provider}</span><h3><Server aria-hidden="true" /> {connection.name}</h3></div><span className={`connector-status status-${connection.last_status}`}>{connection.last_status}</span></header>
                <div className="connector-form-grid">
                  <label><span>{t("connectors.name")}</span><input defaultValue={connection.name} onBlur={(event) => event.target.value !== connection.name && void updateConnection(connection, { name: event.target.value })} /></label>
                  <label><span>{t("connectors.serverUrl")}</span><input defaultValue={connection.base_url} onBlur={(event) => event.target.value !== connection.base_url && void updateConnection(connection, { base_url: event.target.value })} /></label>
                  <label><span>{t("connectors.secret")}</span><input type="password" placeholder={connection.has_secret ? t("connectors.secretConfigured") : ""} onBlur={(event) => event.target.value && void updateConnection(connection, { secret: event.target.value })} /></label>
                  <label><span>{t("connectors.syncInterval")}</span><input type="number" min={5} defaultValue={connection.sync_interval_minutes} onBlur={(event) => Number(event.target.value) !== connection.sync_interval_minutes && void updateConnection(connection, { sync_interval_minutes: Number(event.target.value) })} /></label>
                </div>
                <div className="jellyfin-actions">
                  <button type="button" className="secondary small" onClick={() => void updateConnection(connection, { enabled: !connection.enabled })}>{connection.enabled ? t("connectors.disable") : t("connectors.enable")}</button>
                  <button type="button" className="secondary small" onClick={() => void runAction(connection, "test")}>{t("connectors.test")}</button>
                  <button type="button" className="secondary small" disabled={!connection.enabled} onClick={() => void runAction(connection, "sync")}><RefreshCw aria-hidden="true" /> {t("connectors.sync")}</button>
                  <button type="button" className="secondary small" onClick={() => void runAction(connection, "cancel")}><CircleStop aria-hidden="true" /> {t("connectors.cancel")}</button>
                  <button type="button" className="secondary small danger" onClick={() => void runAction(connection, "delete")}><Trash2 aria-hidden="true" /> {t("connectors.delete")}</button>
                </div>
                {catalog?.libraries.length ? (
                  <div className="connector-mapping-shell">
                    <div className="connector-mapping-heading"><div><h4>{t("connectors.mappingTitle")}</h4><p>{t("connectors.mappingDescription")}</p></div><label><input type="checkbox" checked={advanced} onChange={(event) => setAdvanced(event.target.checked)} /> {t("connectors.advanced")}</label></div>
                    <div className="connector-mapping-table-wrap">
                      <table className="connector-mapping-table"><thead><tr><th>{t("connectors.externalLibrary")}</th><th>{t("connectors.location")}</th><th>{t("connectors.localRoot")}</th>{advanced ? <><th>{t("connectors.sourcePrefix")}</th><th>{t("connectors.targetSubpath")}</th><th>{t("connectors.caseMode")}</th><th>{t("connectors.priority")}</th></> : null}<th>{t("connectors.status")}</th></tr></thead><tbody>
                        {catalog.libraries.flatMap((remoteLibrary) => remoteLibrary.locations.map((location) => {
                          const draft = drafts[connection.id]?.[location.id];
                          return <tr key={location.id}><td>{remoteLibrary.name}</td><td><code>{location.remote_path}</code></td><td><select value={draft?.library_root_id ?? 0} onChange={(event) => updateDraft(connection.id, location.id, { library_root_id: Number(event.target.value) })}><option value={0}>{t("connectors.unmapped")}</option>{rootOptions.map((root) => <option key={root.id} value={root.id}>{root.label}</option>)}</select></td>{advanced ? <><td><input value={draft?.source_prefix ?? location.remote_path} onChange={(event) => updateDraft(connection.id, location.id, { source_prefix: event.target.value })} /></td><td><input value={draft?.target_subpath ?? ""} onChange={(event) => updateDraft(connection.id, location.id, { target_subpath: event.target.value })} /></td><td><select value={draft?.case_mode ?? "sensitive"} onChange={(event) => updateDraft(connection.id, location.id, { case_mode: event.target.value as "sensitive" | "insensitive" })}><option value="sensitive">sensitive</option><option value="insensitive">insensitive</option></select></td><td><input type="number" value={draft?.priority ?? 0} onChange={(event) => updateDraft(connection.id, location.id, { priority: Number(event.target.value) })} /></td></> : null}<td><span className={`badge${draft?.library_root_id ? "" : " warning"}`}>{draft?.library_root_id ? t("connectors.bound") : t("connectors.unmapped")}</span></td></tr>;
                        }))}
                      </tbody></table>
                    </div>
                    <button type="button" disabled={pending === `bindings-${connection.id}`} onClick={() => void saveBindings(connection.id)}>{t("connectors.saveMappings")}</button>
                  </div>
                ) : <p className="field-hint">{t("connectors.syncForLibraries")}</p>}
              </section>
            );
          })}
        </div>
      </AsyncPanel>
      <details className="connector-legacy-details">
        <summary>{t("connectors.jellyfinCompatibility")}</summary>
        <JellyfinSettingsPanel onCatalogChanged={onCatalogChanged} />
      </details>
    </div>
  );
}

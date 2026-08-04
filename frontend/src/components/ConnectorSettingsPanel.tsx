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
  type ConnectorItem,
  type ConnectorLibrary,
  type ConnectorProviderDescriptor,
  type ConnectorSyncJob,
  type LibrarySummary,
} from "../lib/api";

type ConnectorCatalog = {
  libraries: ConnectorLibrary[];
  bindings: ConnectorBinding[];
  items: ConnectorItem[];
  itemTotal: number;
  statusSummary: Record<string, number>;
  job: ConnectorSyncJob | null;
};

export function ConnectorSettingsPanel({ onCatalogChanged }: { onCatalogChanged?: () => void }) {
  const { t } = useTranslation();
  const [connections, setConnections] = useState<ConnectorConnection[]>([]);
  const [providers, setProviders] = useState<string[]>([]);
  const [providerDescriptors, setProviderDescriptors] = useState<ConnectorProviderDescriptor[]>([]);
  const [libraries, setLibraries] = useState<LibrarySummary[]>([]);
  const [catalogs, setCatalogs] = useState<Record<number, ConnectorCatalog>>({});
  const [drafts, setDrafts] = useState<Record<number, Record<number, ConnectorBindingWrite>>>({});
  const [linkDrafts, setLinkDrafts] = useState<Record<number, Record<number, number[]>>>({});
  const [manualMatchIds, setManualMatchIds] = useState<Record<number, string>>({});
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
  const [createConfig, setCreateConfig] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    const [nextConnections, nextProviderDescriptors, nextLibraries] = await Promise.all([
      api.connectors(),
      api.connectorProviderDescriptors(),
      api.libraries(),
    ]);
    const entries = await Promise.all(nextConnections.map(async (connection) => {
      const [remoteLibraries, bindings, itemPage, statusSummary, job] = await Promise.all([
        api.connectorLibraries(connection.id),
        api.connectorBindings(connection.id),
        api.connectorItems(connection.id, undefined, 0, 50, true),
        api.connectorItemStatusSummary(connection.id),
        api.connectorSyncStatus(connection.id),
      ]);
      return [connection.id, {
        libraries: remoteLibraries,
        bindings,
        items: itemPage.items,
        itemTotal: itemPage.total,
        statusSummary,
        job,
      }] as const;
    }));
    const nextCatalogs = Object.fromEntries(entries) as Record<number, ConnectorCatalog>;
    const nextDrafts: Record<number, Record<number, ConnectorBindingWrite>> = {};
    const nextLinkDrafts: Record<number, Record<number, number[]>> = {};
    for (const connection of nextConnections) {
      const catalog = nextCatalogs[connection.id];
      nextDrafts[connection.id] = {};
      nextLinkDrafts[connection.id] = {};
      for (const remoteLibrary of catalog.libraries) {
        nextLinkDrafts[connection.id][remoteLibrary.id] = remoteLibrary.linked_library_ids;
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
    setProviderDescriptors(nextProviderDescriptors);
    setProviders(nextProviderDescriptors.map((descriptor) => descriptor.provider));
    setLibraries(nextLibraries);
    setCatalogs(nextCatalogs);
    setDrafts(nextDrafts);
    setLinkDrafts(nextLinkDrafts);
  }, []);

  useEffect(() => {
    setLoading(true);
    load().catch((reason: Error) => setError(reason.message)).finally(() => setLoading(false));
  }, [load]);

  useEffect(() => {
    if (connections.length === 0) return undefined;
    const timer = window.setInterval(() => {
      void Promise.all(connections.map(async (connection) => {
        const [job, statusSummary, itemPage] = await Promise.all([
          api.connectorSyncStatus(connection.id),
          api.connectorItemStatusSummary(connection.id),
          api.connectorItems(connection.id, undefined, 0, 50, true),
        ]);
        return [connection.id, job, statusSummary, itemPage] as const;
      })).then((updates) => {
        setCatalogs((current) => {
          const next = { ...current };
          for (const [connectionId, job, statusSummary, itemPage] of updates) {
            if (!next[connectionId]) continue;
            next[connectionId] = {
              ...next[connectionId],
              job,
              statusSummary,
              items: itemPage.items,
              itemTotal: itemPage.total,
            };
          }
          return next;
        });
      }).catch(() => undefined);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [connections]);

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
        config: createConfig,
        enabled: false,
      });
      setName("");
      setBaseUrl("");
      setSecret("");
      setCreateConfig({});
      setCreateOpen(false);
      await load();
      setNotice(t("connectors.created"));
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setPending(null);
    }
  }

  const selectedProviderDescriptor = providerDescriptors.find(
    (descriptor) => descriptor.provider === provider,
  );

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

  async function saveLinks(connectionId: number) {
    setPending(`links-${connectionId}`);
    setError(null);
    try {
      const links = Object.entries(linkDrafts[connectionId] ?? {}).map(
        ([connectorLibraryId, libraryIds]) => ({
          connector_library_id: Number(connectorLibraryId),
          library_ids: libraryIds,
        }),
      );
      await api.updateConnectorLibraryLinks(connectionId, links);
      await load();
      onCatalogChanged?.();
      setNotice(t("connectors.linksSaved"));
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setPending(null);
    }
  }

  async function updateItemMatch(connectionId: number, item: ConnectorItem, action: "ignore" | "automatic" | "manual") {
    setPending(`item-${item.id}`);
    setError(null);
    try {
      if (action === "ignore") {
        await api.ignoreConnectorItem(connectionId, item.id);
      } else if (action === "automatic") {
        await api.restoreAutomaticConnectorItemMatch(connectionId, item.id);
      } else {
        const mediaFileId = Number(manualMatchIds[item.id]);
        if (!Number.isInteger(mediaFileId) || mediaFileId < 1) return;
        await api.matchConnectorItem(connectionId, item.id, mediaFileId);
      }
      await load();
      onCatalogChanged?.();
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
              {(selectedProviderDescriptor?.configuration_fields ?? []).map((field) => (
                <label key={field.key}>
                  <span>{field.key === "base_url" ? t("connectors.serverUrl") : field.secret ? t("connectors.secret") : field.key}</span>
                  <input
                    type={field.input_type === "password" ? "password" : field.input_type === "url" ? "url" : "text"}
                    required={field.required}
                    value={field.key === "base_url" ? baseUrl : field.secret ? secret : createConfig[field.key] ?? ""}
                    onChange={(event) => {
                      if (field.key === "base_url") setBaseUrl(event.target.value);
                      else if (field.secret) setSecret(event.target.value);
                      else setCreateConfig((current) => ({ ...current, [field.key]: event.target.value }));
                    }}
                  />
                </label>
              ))}
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
                  {(providerDescriptors.find((descriptor) => descriptor.provider === connection.provider)?.configuration_fields ?? []).map((field) => field.key === "base_url" ? (
                    <label key={field.key}><span>{t("connectors.serverUrl")}</span><input type="url" defaultValue={connection.base_url} onBlur={(event) => event.target.value !== connection.base_url && void updateConnection(connection, { base_url: event.target.value })} /></label>
                  ) : field.secret ? (
                    <label key={field.key}><span>{t("connectors.secret")}</span><input type="password" placeholder={connection.has_secret ? t("connectors.secretConfigured") : ""} onBlur={(event) => event.target.value && void updateConnection(connection, { secret: event.target.value })} /></label>
                  ) : (
                    <label key={field.key}><span>{field.key}</span><input type={field.input_type} defaultValue={String(connection.config[field.key] ?? "")} onBlur={(event) => void updateConnection(connection, { config: { ...connection.config, [field.key]: event.target.value } })} /></label>
                  ))}
                  <label><span>{t("connectors.syncInterval")}</span><input type="number" min={5} defaultValue={connection.sync_interval_minutes} onBlur={(event) => Number(event.target.value) !== connection.sync_interval_minutes && void updateConnection(connection, { sync_interval_minutes: Number(event.target.value) })} /></label>
                </div>
                <div className="jellyfin-actions">
                  <button type="button" className="secondary small" onClick={() => void updateConnection(connection, { enabled: !connection.enabled })}>{connection.enabled ? t("connectors.disable") : t("connectors.enable")}</button>
                  <button type="button" className="secondary small" onClick={() => void runAction(connection, "test")}>{t("connectors.test")}</button>
                  <button type="button" className="secondary small" disabled={!connection.enabled} onClick={() => void runAction(connection, "sync")}><RefreshCw aria-hidden="true" /> {t("connectors.sync")}</button>
                  <button type="button" className="secondary small" onClick={() => void runAction(connection, "cancel")}><CircleStop aria-hidden="true" /> {t("connectors.cancel")}</button>
                  <button type="button" className="secondary small danger" onClick={() => void runAction(connection, "delete")}><Trash2 aria-hidden="true" /> {t("connectors.delete")}</button>
                </div>
                <div className="connector-capability-list" aria-label={t("connectors.capabilities")}>
                  {Object.entries(connection.capabilities).filter(([, enabled]) => enabled).length ? Object.entries(connection.capabilities).filter(([, enabled]) => enabled).map(([capability]) => <span className="badge" key={capability}>{capability}</span>) : <span className="field-hint">{t("connectors.catalogOnly")}</span>}
                </div>
                {catalog?.job ? (
                  <div className={`notice compact${["failed", "canceled"].includes(catalog.job.status) ? " warning" : ""}`}>
                    <strong>{catalog.job.job_type === "recompute" ? t("connectors.recompute") : t("connectors.sync")}</strong>
                    {` · ${catalog.job.status} · ${catalog.job.progress_phase ?? "-"}`}
                    {catalog.job.progress_total ? ` · ${catalog.job.progress_current}/${catalog.job.progress_total}` : ""}
                    {catalog.job.error ? <span>{` · ${catalog.job.error}`}</span> : null}
                  </div>
                ) : null}
                {catalog?.libraries.length ? (
                  <div className="connector-mapping-shell">
                    <div className="connector-logical-links">
                      <h4>{t("connectors.libraryLinks")}</h4>
                      <p>{t("connectors.libraryLinksDescription")}</p>
                      {catalog.libraries.map((remoteLibrary) => (
                        <label key={remoteLibrary.id}>
                          <span>{remoteLibrary.name}</span>
                          <select
                            multiple
                            value={(linkDrafts[connection.id]?.[remoteLibrary.id] ?? []).map(String)}
                            onChange={(event) => {
                              const libraryIds = Array.from(event.currentTarget.selectedOptions, (option) => Number(option.value));
                              setLinkDrafts((current) => ({
                                ...current,
                                [connection.id]: {
                                  ...current[connection.id],
                                  [remoteLibrary.id]: libraryIds,
                                },
                              }));
                            }}
                          >
                            {libraries.map((library) => <option key={library.id} value={library.id}>{library.name}</option>)}
                          </select>
                        </label>
                      ))}
                      <button type="button" disabled={pending === `links-${connection.id}`} onClick={() => void saveLinks(connection.id)}>{t("connectors.saveLibraryLinks")}</button>
                    </div>
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
                {catalog && Object.keys(catalog.statusSummary).length ? (
                  <div className="connector-item-diagnostics">
                    <h4>{t("connectors.itemDiagnostics")}</h4>
                    <div className="connector-capability-list">
                      {Object.entries(catalog.statusSummary).sort(([left], [right]) => left.localeCompare(right)).map(([status, count]) => <span className={`badge${status === "matched" ? "" : " warning"}`} key={status}>{status}: {count}</span>)}
                    </div>
                    <div className="connector-mapping-table-wrap">
                      <table className="connector-mapping-table">
                        <thead><tr><th>{t("connectors.item")}</th><th>{t("connectors.remotePath")}</th><th>{t("connectors.status")}</th><th>{t("connectors.manualFileId")}</th><th>{t("connectors.actions")}</th></tr></thead>
                        <tbody>{catalog.items.map((item) => (
                          <tr key={item.id}>
                            <td>{item.title}<small>{item.item_type}</small></td>
                            <td><code>{item.remote_path ?? "-"}</code></td>
                            <td><span className={`badge${item.match_status === "matched" ? "" : " warning"}`}>{item.match_status}</span>{item.mismatch_reason ? <small>{item.mismatch_reason}</small> : null}</td>
                            <td><input type="number" min={1} value={manualMatchIds[item.id] ?? ""} onChange={(event) => setManualMatchIds((current) => ({ ...current, [item.id]: event.target.value }))} /></td>
                            <td><div className="jellyfin-actions"><button type="button" className="secondary small" disabled={pending === `item-${item.id}`} onClick={() => void updateItemMatch(connection.id, item, "manual")}>{t("connectors.setMatch")}</button>{item.match_status === "ignored" ? <button type="button" className="secondary small" onClick={() => void updateItemMatch(connection.id, item, "automatic")}>{t("connectors.automatic")}</button> : <button type="button" className="secondary small" onClick={() => void updateItemMatch(connection.id, item, "ignore")}>{t("connectors.ignore")}</button>}</div></td>
                          </tr>
                        ))}</tbody>
                      </table>
                    </div>
                    {catalog.itemTotal > catalog.items.length ? <p className="field-hint">{t("connectors.showingItems", { shown: catalog.items.length, total: catalog.itemTotal })}</p> : null}
                  </div>
                ) : null}
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

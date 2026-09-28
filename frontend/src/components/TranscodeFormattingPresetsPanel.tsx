import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, Plus, Save, Search, SquarePen, Star, Trash2 } from "lucide-react";

import { api, type TranscodeFormattingDefinition, type TranscodeFormattingPreset } from "../lib/api";
import type { FormattingKind } from "../lib/transcode-formatting-presets";
import { FILENAME_METADATA_TOKENS, type FilenameMetadataToken, type FilenameMetadataTokenEntry } from "../lib/transcode-formatting-metadata";
import { PanelEmptyState } from "./PanelEmptyState";
import { TranscodeFormattingMetadataMenu } from "./TranscodeFormattingMetadataMenu";
import { TooltipTrigger } from "./TooltipTrigger";

const cleanupValues: TranscodeFormattingDefinition["cleanup_preset"][] = [
  "none", "square_brackets", "round_brackets", "square_and_round_brackets", "all_brackets", "custom",
];
const cleanupKeys = ["none", "squareBrackets", "roundBrackets", "squareAndRoundBrackets", "allBrackets", "custom"];
const connectorMetadataTokens = new Set<FilenameMetadataToken>([
  "movieTitle", "releaseYear", "seriesName", "seasonNumber", "episodeNumber", "episodeTitle",
]);
const exampleMetadataValues: Record<FilenameMetadataToken, string> = {
  sourceName: "Arrival (2016)",
  movieTitle: "Arrival",
  releaseYear: "2016",
  resolution: "1920x1080",
  resolutionCategory: "1080p",
  dynRange: "HDR10",
  codec: "HEVC",
  audioLanguages: "English, German",
  audioCodecs: "AAC",
  audioProfiles: "Dolby Atmos",
  audioChannels: "5.1",
  frameRate: "23.976 fps",
  bitDepth: "10-bit",
  subtitleLanguages: "English, German",
  subtitleFormats: "SRT",
  seriesName: "The Expanse",
  seasonNumber: "2",
  episodeNumber: "5",
  episodeTitle: "Home",
  contentCategory: "Movie",
  container: "MKV",
  videoBitrate: "12 Mbps",
  folderName: "Movies",
};

function exampleSourceName(definition: TranscodeFormattingDefinition): string {
  const patterns: Partial<Record<TranscodeFormattingDefinition["cleanup_preset"], string>> = {
    square_brackets: "\\[[^\\[\\]]*\\]",
    round_brackets: "\\([^()]*\\)",
    square_and_round_brackets: "\\[[^\\[\\]]*\\]|\\([^()]*\\)",
    all_brackets: "\\[[^\\[\\]]*\\]|\\([^()]*\\)|\\{[^{}]*\\}",
    custom: definition.cleanup_regex ?? undefined,
  };
  const pattern = patterns[definition.cleanup_preset];
  if (!pattern) return exampleMetadataValues.sourceName;
  try {
    return exampleMetadataValues.sourceName
      .replace(new RegExp(pattern, "g"), "")
      .replace(/\s+/g, " ")
      .trim()
      .replace(/^[ ._-]+|[ ._-]+$/g, "");
  } catch {
    return exampleMetadataValues.sourceName;
  }
}

function formattingExampleOutput(definition: TranscodeFormattingDefinition, kind: FormattingKind): string {
  const separator = definition.metadata_separator || ", ";
  const values = {
    ...exampleMetadataValues,
    sourceName: exampleSourceName(definition),
    audioLanguages: ["English", "German"].join(separator),
    subtitleLanguages: ["English", "German"].join(separator),
  };
  let template = definition.template;
  if (kind === "filename" && !definition.source_name_explicit && !template.includes("{sourceName}")) {
    template = `{sourceName} ${template}`;
  }
  const rendered = template
    .replace(/\{([^{}]+)\}/g, (token, name: string) => name in values ? values[name as FilenameMetadataToken] : token)
    .replace(/\[\s*[,;|+\-]*\s*\]/g, "")
    .replace(/([\[,;|+])\s*([,;|+])/g, "$1")
    .replace(/\s*,\s*(?=\])/g, "")
    .replace(/\[\s*,\s*/g, "[")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_");
  const output = rendered || (kind === "filename" ? "Example" : "Movies");
  return kind === "filename" ? `${output}.mkv` : output;
}

function emptyDefinition(kind: FormattingKind): TranscodeFormattingDefinition {
  return {
    enabled: kind === "filename",
    template: kind === "filename" ? "{sourceName} [{resolution}, {dynRange}, {codec}] [{audioLanguages}]" : "{folderName}",
    source_name_explicit: kind === "filename",
    metadata_separator: ", ",
    cleanup_preset: "none",
    cleanup_regex: null,
    include_subtitle_languages: false,
    language_code_format: "iso_639_1",
  };
}

function visibleTemplate(preset: TranscodeFormattingPreset): string {
  const template = preset.definition.template;
  return preset.kind === "filename" && !preset.definition.source_name_explicit && !template.includes("{sourceName}")
    ? `{sourceName} ${template}`
    : template;
}

export function TranscodeFormattingPresetsPanel({ kind, tabs }: { kind: FormattingKind; tabs: ReactNode }) {
  const { t } = useTranslation();
  const metadataMenuId = `formatting-metadata-${useId()}`;
  const templateInputId = `formatting-template-${kind}-${useId()}`;
  const templateInputRef = useRef<HTMLInputElement>(null);
  const [presets, setPresets] = useState<TranscodeFormattingPreset[]>([]);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [draftId, setDraftId] = useState<number | null | undefined>(undefined);
  const [name, setName] = useState("");
  const [definition, setDefinition] = useState<TranscodeFormattingDefinition>(() => emptyDefinition(kind));
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [metadataMenuOpen, setMetadataMenuOpen] = useState(false);

  useEffect(() => {
    let active = true;
    void api.transcodeFormattingPresets()
      .then((all) => { if (active) { setPresets(all.filter((preset) => preset.kind === kind)); setError(null); } })
      .catch((reason: Error) => { if (active) setError(reason.message); });
    return () => { active = false; };
  }, [kind]);

  function startNew() {
    setExpandedId(null);
    setDraftId(null);
    setName("");
    setDefinition(emptyDefinition(kind));
    setError(null);
    setMetadataMenuOpen(false);
  }

  function startEdit(preset: TranscodeFormattingPreset) {
    setExpandedId(preset.id);
    setDraftId(preset.id);
    setName(preset.name);
    setDefinition({ ...preset.definition, template: visibleTemplate(preset), source_name_explicit: kind === "filename" });
    setError(null);
    setMetadataMenuOpen(false);
  }

  function cancelEditing() {
    setDraftId(undefined);
    setMetadataMenuOpen(false);
    setError(null);
  }

  function insertMetadataToken(token: FilenameMetadataToken) {
    const input = templateInputRef.current;
    const currentTemplate = definition.template;
    const hasFocusedSelection = Boolean(input && document.activeElement === input);
    const selectionStart = hasFocusedSelection && input ? input.selectionStart ?? currentTemplate.length : currentTemplate.length;
    const selectionEnd = hasFocusedSelection && input ? input.selectionEnd ?? selectionStart : selectionStart;
    const insertion = `{${token}}`;
    const nextTemplate = `${currentTemplate.slice(0, selectionStart)}${insertion}${currentTemplate.slice(selectionEnd)}`;
    setDefinition((current) => ({ ...current, template: nextTemplate, source_name_explicit: kind === "filename" }));
    if (input) {
      window.requestAnimationFrame(() => {
        input.focus();
        input.setSelectionRange(selectionStart + insertion.length, selectionStart + insertion.length);
      });
    }
  }

  async function save() {
    if (!name.trim() || !definition.template.trim()) return;
    setBusy(true);
    try {
      const saved = draftId === null
        ? await api.createTranscodeFormattingPreset({ kind, name: name.trim(), definition })
        : await api.updateTranscodeFormattingPreset(draftId as number, { name: name.trim(), definition });
      setPresets((current) => [...current.filter((preset) => preset.id !== saved.id), saved].sort((a, b) => a.name.localeCompare(b.name)));
      setExpandedId(saved.id);
      setDraftId(undefined);
      setError(null);
      setMetadataMenuOpen(false);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function setDefault(preset: TranscodeFormattingPreset) {
    setBusy(true);
    try {
      const saved = await api.updateTranscodeFormattingPreset(preset.id, { is_default: !preset.is_default });
      setPresets((current) => current.map((item) => ({ ...item, is_default: item.id === saved.id ? saved.is_default : saved.is_default ? false : item.is_default })));
      setError(null);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function remove(preset: TranscodeFormattingPreset) {
    setBusy(true);
    try {
      await api.deleteTranscodeFormattingPreset(preset.id);
      setPresets((current) => current.filter((item) => item.id !== preset.id));
      if (expandedId === preset.id) setExpandedId(null);
      if (draftId === preset.id) setDraftId(undefined);
      setMetadataMenuOpen(false);
      setError(null);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const visible = presets.filter((preset) => preset.name.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
  const title = t(kind === "filename" ? "transcoding.presetSettingsTabs.filename" : "transcoding.presetSettingsTabs.folder");
  const metadataEntries = FILENAME_METADATA_TOKENS.filter((entry) => kind !== "filename" || entry.token !== "folderName");
  const metadataGroups = [
    { name: "MediaLyze", entries: metadataEntries.filter((entry) => !connectorMetadataTokens.has(entry.token)) },
    { name: "Connector", entries: metadataEntries.filter((entry) => connectorMetadataTokens.has(entry.token)) },
  ].filter((group) => group.entries.length);
  const exampleOutput = formattingExampleOutput(definition, kind);
  const editor = (
    <div className="compatibility-profile-details transcode-automation-details transcode-automation-editor">
      <div className="compatibility-profile-form-grid">
        <label><span>{t("transcoding.formattingPresets.name")}</span><input className="settings-choice-input" maxLength={255} value={name} onChange={(event) => setName(event.target.value)} /></label>
        <div className="compatibility-profile-field-wide">
          <label htmlFor={templateInputId}>{t(kind === "filename" ? "transcoding.filenameTemplate" : "transcoding.folderTemplate")}</label>
          <input ref={templateInputRef} id={templateInputId} className="settings-choice-input" maxLength={512} value={definition.template} onChange={(event) => setDefinition({ ...definition, template: event.target.value, source_name_explicit: kind === "filename" })} />
          <TranscodeFormattingMetadataMenu kind={kind} id={metadataMenuId} open={metadataMenuOpen} onToggle={() => setMetadataMenuOpen((current) => !current)}>
            {metadataGroups.map((group) => (
              <div className="transcode-filename-token-group" key={group.name}>
                <strong>{group.name}</strong>
                <div className="transcode-filename-token-group-items">
                  {group.entries.map((entry: FilenameMetadataTokenEntry) => {
                    const label = t(`transcoding.filenameMetadataTokenOptions.${entry.labelKey}`);
                    const description = t("transcoding.filenameMetadataTooltipDescription", { token: `{${entry.token}}`, label })
                      .replaceAll("{token}", `{${entry.token}}`)
                      .replaceAll("{label}", label);
                    const exampleValue = entry.token === "audioLanguages" || entry.token === "subtitleLanguages"
                      ? ["English", "German"].join(definition.metadata_separator || ", ")
                      : entry.token === "sourceName" ? exampleSourceName(definition) : exampleMetadataValues[entry.token];
                    return (
                      <TooltipTrigger
                        key={entry.token}
                        className="secondary small transcode-filename-token-pill"
                        ariaLabel={label}
                        tooltipClassName="transcode-filename-token-tooltip-portal"
                        align="start"
                        placement="auto"
                        maxWidth={360}
                        pinOnClick={false}
                        disabled={busy}
                        onMouseDown={(event) => event.preventDefault()}
                        onClick={() => insertMetadataToken(entry.token)}
                        content={(
                          <div className="transcode-filename-token-tooltip">
                            <div className="transcode-filename-token-tooltip-heading"><code>{`{${entry.token}}`}</code><strong>{label}</strong></div>
                            <p>{description}</p>
                            <div className="transcode-filename-token-tooltip-example"><span>{t("transcoding.filenameMetadataTooltipExample")}</span><code>{`{${entry.token}} → ${exampleValue}`}</code></div>
                          </div>
                        )}
                      >
                        {`{${entry.token}}`}
                      </TooltipTrigger>
                    );
                  })}
                </div>
              </div>
            ))}
          </TranscodeFormattingMetadataMenu>
          <div className="transcode-filename-preview is-prominent">
            <span>{t("transcoding.formattingPresets.exampleOutput")}</span>
            <code aria-live="polite">{exampleOutput}</code>
          </div>
        </div>
        <label><span>{t(kind === "filename" ? "transcoding.filenameMetadataSeparator" : "transcoding.folderMetadataSeparator")}</span><input className="settings-choice-input" maxLength={32} value={definition.metadata_separator} onChange={(event) => setDefinition({ ...definition, metadata_separator: event.target.value })} /></label>
        <label><span>{t(kind === "filename" ? "transcoding.filenameCleanupPreset" : "transcoding.folderCleanupPreset")}</span><select className="settings-choice-input" value={definition.cleanup_preset} onChange={(event) => setDefinition({ ...definition, cleanup_preset: event.target.value as TranscodeFormattingDefinition["cleanup_preset"] })}>{cleanupValues.map((value, index) => <option key={value} value={value}>{t(`transcoding.filenameCleanupOptions.${cleanupKeys[index]}`)}</option>)}</select></label>
        {definition.cleanup_preset === "custom" ? <label className="compatibility-profile-field-wide"><span>{t(kind === "filename" ? "transcoding.filenameCleanupRegex" : "transcoding.folderCleanupRegex")}</span><input className="settings-choice-input" maxLength={256} value={definition.cleanup_regex ?? ""} onChange={(event) => setDefinition({ ...definition, cleanup_regex: event.target.value })} /></label> : null}
        {kind === "filename" ? <label><span>{t("transcoding.languageCodeFormat")}</span><select className="settings-choice-input" value={definition.language_code_format} onChange={(event) => setDefinition({ ...definition, language_code_format: event.target.value as TranscodeFormattingDefinition["language_code_format"] })}><option value="iso_639_1">{t("transcoding.languageCodeFormats.iso_639_1")}</option><option value="iso_639_2">{t("transcoding.languageCodeFormats.iso_639_2")}</option></select></label> : null}
      </div>
      <div className="transcode-global-options">
        <label><input type="checkbox" checked={definition.enabled} onChange={(event) => setDefinition({ ...definition, enabled: event.target.checked })} /><span>{t(kind === "filename" ? "transcoding.filenameFormattingToggle" : "transcoding.folderFormattingToggle")}</span></label>
        {kind === "filename" ? <label><input type="checkbox" checked={definition.include_subtitle_languages} onChange={(event) => setDefinition({ ...definition, include_subtitle_languages: event.target.checked })} /><span>{t("transcoding.filenameIncludeSubtitleLanguages")}</span></label> : null}
      </div>
      <div className="compatibility-profile-card-actions transcode-automation-editor-actions"><button type="button" className="transcode-action-button" disabled={busy || !name.trim() || !definition.template.trim()} onClick={() => void save()}><Save size={16} aria-hidden="true" />{t("common.save")}</button><button type="button" className="secondary transcode-action-button" disabled={busy} onClick={cancelEditing}>{t("common.cancel")}</button></div>
    </div>
  );

  return <div className="compatibility-profile-list compatibility-profile-catalog-list" data-settings-search-target={`transcoding-presets-tab-${kind}`}>
    <div className="settings-profile-toggle-row transcode-automation-toggle-row"><div className="transcode-automation-tab-controls">{tabs}</div><div className="settings-profile-toggle-actions"><button type="button" className="secondary small settings-panel-header-action" onClick={startNew} disabled={busy}><Plus size={16} aria-hidden="true" />{t("transcoding.formattingPresets.new")}</button></div></div>
    {error ? <p className="notice error" role="alert">{error}</p> : null}
    <div className="compatibility-profile-search"><Search size={16} aria-hidden="true" className="compatibility-profile-search-icon" /><input type="search" value={search} aria-label={t("transcoding.automation.searchPresets")} placeholder={t("transcoding.automation.searchPresets")} onChange={(event) => setSearch(event.target.value)} /></div>
    {visible.map((preset) => <article className={`compatibility-profile-list-item${expandedId === preset.id ? " is-expanded" : ""}`} key={preset.id}>
      <div className="compatibility-profile-list-row quality-profile-list-row">
        <button type="button" className="compatibility-profile-list-trigger" aria-expanded={expandedId === preset.id} onClick={() => { setExpandedId((current) => current === preset.id ? null : preset.id); setDraftId(undefined); setMetadataMenuOpen(false); }}><span className="transcode-automation-list-copy compatibility-profile-list-copy"><strong>{preset.name}</strong>{preset.is_default ? <small>{t("transcoding.formattingPresets.default")}</small> : null}</span><ChevronDown aria-hidden="true" /></button>
        <div className="compatibility-profile-quick-actions transcode-automation-quick-actions">
          <button type="button" className={`secondary icon-only-button compatibility-profile-quick-action${preset.is_default ? " is-favorite" : ""}`} aria-label={t(preset.is_default ? "transcoding.formattingPresets.removeDefault" : "transcoding.formattingPresets.makeDefault", { name: preset.name })} aria-pressed={preset.is_default} title={t(preset.is_default ? "transcoding.formattingPresets.removeDefault" : "transcoding.formattingPresets.makeDefault", { name: preset.name })} disabled={busy} onClick={() => void setDefault(preset)}><Star size={18} fill={preset.is_default ? "currentColor" : "none"} aria-hidden="true" /></button>
          <button type="button" className="secondary icon-only-button compatibility-profile-quick-action" aria-label={`${t("transcoding.automation.edit")} ${preset.name}`} disabled={busy} onClick={() => startEdit(preset)}><SquarePen size={18} aria-hidden="true" /></button>
          <button type="button" className="secondary icon-only-button compatibility-profile-quick-action" aria-label={`${t("transcoding.automation.delete")} ${preset.name}`} disabled={busy} onClick={() => void remove(preset)}><Trash2 size={18} aria-hidden="true" /></button>
        </div>
      </div>
      {expandedId === preset.id ? draftId === preset.id ? editor : <div className="compatibility-profile-details transcode-automation-details"><div className="compatibility-profile-form-grid"><label className="compatibility-profile-field-wide"><span>{title}</span><input className="settings-choice-input" readOnly value={visibleTemplate(preset)} /></label><label><span>{t(kind === "filename" ? "transcoding.filenameMetadataSeparator" : "transcoding.folderMetadataSeparator")}</span><input className="settings-choice-input" readOnly value={preset.definition.metadata_separator} /></label><label><span>{t(kind === "filename" ? "transcoding.filenameCleanupPreset" : "transcoding.folderCleanupPreset")}</span><input className="settings-choice-input" readOnly value={t(`transcoding.filenameCleanupOptions.${cleanupKeys[cleanupValues.indexOf(preset.definition.cleanup_preset)]}`)} /></label></div></div> : null}
    </article>)}
    {draftId === null ? <article className="compatibility-profile-list-item is-expanded"><div className="compatibility-profile-list-row quality-profile-list-row"><div className="compatibility-profile-list-trigger is-static"><span className="transcode-automation-list-copy compatibility-profile-list-copy"><strong>{name || t("transcoding.formattingPresets.new")}</strong></span></div></div>{editor}</article> : null}
    {!presets.length && draftId === undefined ? <div className="transcode-preset-placeholder-body"><PanelEmptyState message={t(kind === "filename" ? "transcoding.presetSettingsTabs.filenameEmpty" : "transcoding.presetSettingsTabs.folderEmpty")} /></div> : null}
    {presets.length > 0 && !visible.length ? <p className="compatibility-profile-search-empty">{t("transcoding.automation.searchEmpty")}</p> : null}
  </div>;
}

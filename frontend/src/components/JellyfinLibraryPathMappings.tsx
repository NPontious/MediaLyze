import { Check, LoaderCircle, Save, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { api, type JellyfinLibrary, type JellyfinPathMapping } from "../lib/api";
import { TooltipTrigger } from "./TooltipTrigger";

function normalizedPath(path: string) {
  return path.trim().replaceAll("\\", "/").replace(/\/+$/, "").toLocaleLowerCase();
}

function JellyfinLibraryPathMappingRow({
  jellyfinLibrary,
  location,
  mapping,
  suggestedTarget,
  disabled,
  onChanged,
}: {
  jellyfinLibrary: JellyfinLibrary;
  location: string;
  mapping?: JellyfinPathMapping;
  suggestedTarget: string;
  disabled: boolean;
  onChanged: (mapping: JellyfinPathMapping | null, removedId?: number) => void;
}) {
  const { t } = useTranslation();
  const [target, setTarget] = useState(mapping?.medialyze_path_prefix ?? suggestedTarget);
  const [pendingAction, setPendingAction] = useState<"save" | "delete" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    setTarget(mapping?.medialyze_path_prefix ?? suggestedTarget);
    setError(null);
    setSaved(false);
  }, [mapping?.enabled, mapping?.id, mapping?.medialyze_path_prefix, suggestedTarget]);

  async function saveMapping() {
    const nextTarget = target.trim();
    if (!nextTarget) return;
    setPendingAction("save");
    setError(null);
    setSaved(false);
    try {
      const savedMapping = mapping
        ? await api.updateJellyfinPathMapping(mapping.id, {
            jellyfin_path_prefix: location,
            medialyze_path_prefix: nextTarget,
            enabled: true,
          })
        : await api.createJellyfinPathMapping({
            jellyfin_path_prefix: location,
            medialyze_path_prefix: nextTarget,
            enabled: true,
          });
      onChanged(savedMapping);
      setSaved(true);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setPendingAction(null);
    }
  }

  async function deleteMapping() {
    if (!mapping) return;
    setPendingAction("delete");
    setError(null);
    setSaved(false);
    try {
      await api.deleteJellyfinPathMapping(mapping.id);
      onChanged(null, mapping.id);
      setTarget("");
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setPendingAction(null);
    }
  }

  const normalizedTarget = target.trim();
  const hasChanges = Boolean(
    normalizedTarget
      && (
        !mapping
        || !mapping.enabled
        || normalizedPath(mapping.jellyfin_path_prefix) !== normalizedPath(location)
        || normalizedPath(mapping.medialyze_path_prefix) !== normalizedPath(normalizedTarget)
      ),
  );

  return (
    <div className="library-jellyfin-path-mapping-row">
      <div className="field library-jellyfin-path-source">
        <span className="field-label">{t("jellyfin.jellyfinPath")}</span>
        <code>{location}</code>
      </div>
      <span className="library-jellyfin-path-arrow" aria-hidden="true">→</span>
      <div className="field library-jellyfin-path-target">
        <label htmlFor={`jellyfin-path-target-${jellyfinLibrary.id}-${encodeURIComponent(location)}`}>
          {t("jellyfin.medialyzePath")}
        </label>
        <input
          id={`jellyfin-path-target-${jellyfinLibrary.id}-${encodeURIComponent(location)}`}
          className="settings-choice-input"
          value={target}
          placeholder={suggestedTarget || t("jellyfin.medialyzePathPlaceholder")}
          disabled={disabled || pendingAction !== null}
          onChange={(event) => {
            setTarget(event.target.value);
            setSaved(false);
            setError(null);
          }}
        />
      </div>
      <div className="library-jellyfin-path-mapping-actions">
        <button
          type="button"
          className={`secondary icon-only-button library-jellyfin-path-save-button${hasChanges ? " is-dirty" : ""}`}
          aria-label={t("libraries.sections.jellyfin.savePathMapping")}
          title={t("libraries.sections.jellyfin.savePathMapping")}
          disabled={disabled || pendingAction !== null || !hasChanges}
          onClick={() => void saveMapping()}
        >
          {pendingAction === "save" ? <LoaderCircle className="is-spinning" aria-hidden="true" /> : <Save aria-hidden="true" />}
        </button>
        {mapping ? (
          <button
            type="button"
            className="secondary icon-only-button danger"
            aria-label={t("libraries.sections.jellyfin.removePathMapping")}
            title={t("libraries.sections.jellyfin.removePathMapping")}
            disabled={disabled || pendingAction !== null}
            onClick={() => void deleteMapping()}
          >
            {pendingAction === "delete" ? <LoaderCircle className="is-spinning" aria-hidden="true" /> : <Trash2 aria-hidden="true" />}
          </button>
        ) : null}
        {saved ? (
          <span
            className="library-jellyfin-path-mapping-status"
            role="status"
            aria-label={t("jellyfin.autoSave.saved")}
            title={t("jellyfin.autoSave.saved")}
          >
            <Check aria-hidden="true" />
          </span>
        ) : null}
      </div>
      {error ? <div className="alert jellyfin-inline-error" role="alert">{error}</div> : null}
    </div>
  );
}

export function JellyfinLibraryPathMappings({
  jellyfinLibrary,
  mappings,
  suggestedTargets,
  disabled = false,
  loadError,
  onChanged,
}: {
  jellyfinLibrary: JellyfinLibrary;
  mappings: JellyfinPathMapping[];
  suggestedTargets: string[];
  disabled?: boolean;
  loadError?: string | null;
  onChanged: (mapping: JellyfinPathMapping | null, removedId?: number) => void;
}) {
  const { t } = useTranslation();
  const [togglePending, setTogglePending] = useState(false);
  const [toggleError, setToggleError] = useState<string | null>(null);
  const locationMappings = jellyfinLibrary.locations.map((location) => mappings.find(
    (candidate) => normalizedPath(candidate.jellyfin_path_prefix) === normalizedPath(location),
  ));
  const isEnabled = Boolean(
    jellyfinLibrary.locations.length
      && locationMappings.every((mapping) => mapping?.enabled),
  );

  async function togglePathMapping(nextEnabled: boolean) {
    if (nextEnabled === isEnabled) return;
    setTogglePending(true);
    setToggleError(null);
    try {
      for (const [index, location] of jellyfinLibrary.locations.entries()) {
        const mapping = locationMappings[index];
        if (mapping) {
          const updated = await api.updateJellyfinPathMapping(mapping.id, { enabled: nextEnabled });
          onChanged(updated);
          continue;
        }
        if (!nextEnabled) continue;
        const target = (suggestedTargets[index] ?? suggestedTargets[0] ?? "").trim();
        if (!target) throw new Error(t("libraries.sections.jellyfin.pathMappingTargetRequired"));
        const created = await api.createJellyfinPathMapping({
          jellyfin_path_prefix: location,
          medialyze_path_prefix: target,
          enabled: true,
        });
        onChanged(created);
      }
    } catch (reason) {
      setToggleError((reason as Error).message);
    } finally {
      setTogglePending(false);
    }
  }

  return (
    <div className="library-jellyfin-path-mappings">
      <div className="library-jellyfin-path-mappings-heading">
        <div className="library-jellyfin-path-mappings-title">
          <h5>{t("libraries.sections.jellyfin.pathMappingTitle")}</h5>
          <TooltipTrigger
            ariaLabel={t("libraries.sections.jellyfin.pathMappingDescriptionAria")}
            content={t("libraries.sections.jellyfin.pathMappingDescription")}
          >
            ?
          </TooltipTrigger>
        </div>
        <label
          className="library-jellyfin-path-mapping-switch"
          title={t(isEnabled
            ? "libraries.sections.jellyfin.disablePathMapping"
            : "libraries.sections.jellyfin.enablePathMapping")}
        >
          <input
            type="checkbox"
            role="switch"
            checked={isEnabled}
            disabled={disabled || togglePending || !jellyfinLibrary.locations.length}
            aria-label={t(isEnabled
              ? "libraries.sections.jellyfin.disablePathMapping"
              : "libraries.sections.jellyfin.enablePathMapping")}
            aria-busy={togglePending}
            onChange={(event) => void togglePathMapping(event.target.checked)}
          />
          <span className="library-jellyfin-path-mapping-switch-track" aria-hidden="true">
            <span className="library-jellyfin-path-mapping-switch-thumb" />
          </span>
        </label>
      </div>
      {loadError || toggleError ? (
        <div className="alert jellyfin-inline-error" role="alert">{loadError ?? toggleError}</div>
      ) : null}
      {!jellyfinLibrary.locations.length ? (
        <div className="notice">{t("libraries.sections.jellyfin.noJellyfinPaths")}</div>
      ) : isEnabled ? (
        <div className="library-jellyfin-path-mapping-list">
          {jellyfinLibrary.locations.map((location, index) => {
            const mapping = mappings.find(
              (candidate) => normalizedPath(candidate.jellyfin_path_prefix) === normalizedPath(location),
            );
            return (
              <JellyfinLibraryPathMappingRow
                key={`${jellyfinLibrary.id}:${location}`}
                jellyfinLibrary={jellyfinLibrary}
                location={location}
                mapping={mapping}
                suggestedTarget={suggestedTargets[index] ?? suggestedTargets[0] ?? ""}
                disabled={disabled || togglePending}
                onChanged={onChanged}
              />
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

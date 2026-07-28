import {
  ArrowUp,
  ChevronDown,
  ChevronRight,
  File,
  FileText,
  Folder,
  Info,
  Map as MapIcon,
  RefreshCw,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useSearchParams } from "react-router";

import { JellyfinIcon } from "../components/JellyfinIcon";
import { SlidingTogglePill } from "../components/SlidingTogglePill";
import { StatCard } from "../components/StatCard";
import { TooltipTrigger } from "../components/TooltipTrigger";
import { useAppData } from "../lib/app-data";
import { api, type LibraryStorageMap, type StorageMapNode } from "../lib/api";
import { formatBytes, formatCodecLabel } from "../lib/format";
import { formatHdrType } from "../lib/hdr";
import { LruCache } from "../lib/lru-cache";

type StorageMapColorMode = "codec" | "resolution" | "hdr" | "quality" | "size";
type StorageMapSortMode = "size" | "name" | "quality";
type StorageMapNameSource = "file" | "jellyfin";

type StorageMapRect = {
  node: StorageMapNode;
  x: number;
  y: number;
  width: number;
  height: number;
};

const COLOR_MODES: StorageMapColorMode[] = ["codec", "resolution", "hdr", "quality", "size"];
const SORT_MODES: StorageMapSortMode[] = ["size", "name", "quality"];
const UNKNOWN_COLOR = "#6b7280";
const CATEGORY_PALETTE = ["#1b998b", "#ff6b3d", "#4f6fcf", "#a967c7", "#d49b2f", "#397f9e"];

function isColorMode(value: string | null): value is StorageMapColorMode {
  return COLOR_MODES.includes(value as StorageMapColorMode);
}

function isSortMode(value: string | null): value is StorageMapSortMode {
  return SORT_MODES.includes(value as StorageMapSortMode);
}

function displayNameForNode(node: StorageMapNode, nameSource: StorageMapNameSource): string {
  return node.kind === "file" && nameSource === "jellyfin"
    ? (node.jellyfin_title ?? node.name)
    : node.name;
}

function stablePaletteColor(value: string | null | undefined): string {
  if (!value) return UNKNOWN_COLOR;
  let hash = 0;
  for (const character of value) {
    hash = (hash * 31 + character.charCodeAt(0)) >>> 0;
  }
  return CATEGORY_PALETTE[hash % CATEGORY_PALETTE.length];
}

function colorForNode(
  node: StorageMapNode,
  mode: StorageMapColorMode,
  maxSize: number,
): string {
  if (mode === "codec") {
    const codec = node.video_codec?.toLowerCase();
    if (codec === "hevc" || codec === "h265") return "#1b998b";
    if (codec === "h264") return "#4f6fcf";
    if (codec === "av1") return "#ff6b3d";
    return stablePaletteColor(codec);
  }
  if (mode === "resolution") {
    return stablePaletteColor(node.resolution_category_id ?? node.resolution);
  }
  if (mode === "hdr") {
    const hdr = node.hdr_type?.toLowerCase();
    if (!hdr || hdr === "sdr") return "#60717d";
    if (hdr.includes("dolby")) return "#a967c7";
    if (hdr.includes("hdr10+")) return "#ff6b3d";
    if (hdr.includes("hdr10")) return "#d49b2f";
    if (hdr.includes("hlg")) return "#397f9e";
    return stablePaletteColor(hdr);
  }
  if (mode === "quality") {
    if (node.quality_score === null) return UNKNOWN_COLOR;
    const normalized = Math.max(0, Math.min(1, node.quality_score / 100));
    return `hsl(${Math.round(8 + normalized * 154)} 48% ${Math.round(46 - normalized * 10)}%)`;
  }
  const normalized = Math.sqrt(Math.max(0, node.size_bytes) / Math.max(maxSize, 1));
  return `hsl(${Math.round(204 - normalized * 178)} 55% ${Math.round(48 - normalized * 10)}%)`;
}

function labelForNode(
  node: StorageMapNode,
  mode: StorageMapColorMode,
  unknownLabel: string,
): string {
  if (mode === "codec") {
    return node.video_codec ? formatCodecLabel(node.video_codec, "video") : unknownLabel;
  }
  if (mode === "resolution") {
    return node.resolution_category_label ?? node.resolution ?? unknownLabel;
  }
  if (mode === "hdr") {
    return node.hdr_type ? (formatHdrType(node.hdr_type) ?? unknownLabel) : unknownLabel;
  }
  if (mode === "quality") {
    return node.quality_score === null ? unknownLabel : `${Math.round(node.quality_score)}/100`;
  }
  return formatBytes(node.size_bytes);
}

function splitStorageMapNodes(
  nodes: StorageMapNode[],
  x: number,
  y: number,
  width: number,
  height: number,
  output: StorageMapRect[],
): void {
  if (nodes.length === 0) return;
  if (nodes.length === 1) {
    output.push({ node: nodes[0], x, y, width, height });
    return;
  }

  const total = nodes.reduce((sum, node) => sum + Math.max(node.size_bytes, 1), 0);
  let leftWeight = 0;
  let splitIndex = 1;
  for (let index = 0; index < nodes.length - 1; index += 1) {
    leftWeight += Math.max(nodes[index].size_bytes, 1);
    splitIndex = index + 1;
    if (leftWeight >= total / 2) break;
  }

  const ratio = Math.max(0.001, Math.min(0.999, leftWeight / total));
  if (width >= height) {
    const leftWidth = width * ratio;
    splitStorageMapNodes(nodes.slice(0, splitIndex), x, y, leftWidth, height, output);
    splitStorageMapNodes(nodes.slice(splitIndex), x + leftWidth, y, width - leftWidth, height, output);
  } else {
    const topHeight = height * ratio;
    splitStorageMapNodes(nodes.slice(0, splitIndex), x, y, width, topHeight, output);
    splitStorageMapNodes(nodes.slice(splitIndex), x, y + topHeight, width, height - topHeight, output);
  }
}

export function layoutStorageMapNodes(nodes: StorageMapNode[]): StorageMapRect[] {
  const output: StorageMapRect[] = [];
  splitStorageMapNodes(nodes, 0, 0, 100, 100, output);
  return output;
}

function StorageMapTile({
  node,
  colorMode,
  maxSize,
  rect,
  nameSource,
  onOpen,
}: {
  node: StorageMapNode;
  colorMode: StorageMapColorMode;
  maxSize: number;
  rect: Omit<StorageMapRect, "node">;
  nameSource: StorageMapNameSource;
  onOpen: (node: StorageMapNode) => void;
}) {
  const { t } = useTranslation();
  const tileRef = useRef<HTMLButtonElement>(null);
  const [labelsHidden, setLabelsHidden] = useState(false);
  const displayName = displayNameForNode(node, nameSource);
  const metricLabel = labelForNode(node, colorMode, t("storageMap.unknown"));
  const detail = `${displayName} · ${formatBytes(node.size_bytes)} · ${metricLabel}`;
  const style = {
    "--storage-map-tile-color": colorForNode(node, colorMode, maxSize),
    left: `${rect.x}%`,
    top: `${rect.y}%`,
    width: `${rect.width}%`,
    height: `${rect.height}%`,
  } as CSSProperties;

  useLayoutEffect(() => {
    const tile = tileRef.current;
    if (!tile) return undefined;

    const updateLabelVisibility = () => {
      const label = tile.querySelector<HTMLElement>(".storage-map-tile-copy");
      if (!label) return;
      const name = label.querySelector<HTMLElement>(".storage-map-tile-name");
      const meta = label.querySelector<HTMLElement>(".storage-map-tile-meta");
      const size = label.querySelector<HTMLElement>(".storage-map-tile-size");
      const labelStyles = window.getComputedStyle(label);
      const requiredHeight =
        (name?.scrollHeight ?? 0) +
        (meta?.scrollHeight ?? 0) +
        (size?.scrollHeight ?? 0) +
        Number.parseFloat(labelStyles.paddingTop) +
        Number.parseFloat(labelStyles.paddingBottom) +
        4;
      const shouldHide =
        tile.clientWidth < 74 ||
        tile.clientHeight < 48 ||
        label.scrollWidth > label.clientWidth ||
        requiredHeight > tile.clientHeight;
      setLabelsHidden((current) => (current === shouldHide ? current : shouldHide));
    };

    updateLabelVisibility();
    const animationFrame = window.requestAnimationFrame(updateLabelVisibility);
    const timeout = window.setTimeout(updateLabelVisibility, 0);
    void document.fonts?.ready.then(updateLabelVisibility);
    if (typeof ResizeObserver === "undefined") {
      return () => {
        window.cancelAnimationFrame(animationFrame);
        window.clearTimeout(timeout);
      };
    }
    const observer = new ResizeObserver(updateLabelVisibility);
    observer.observe(tile);
    return () => {
      window.cancelAnimationFrame(animationFrame);
      window.clearTimeout(timeout);
      observer.disconnect();
    };
  }, [colorMode, displayName, rect.height, rect.width]);

  return (
    <button
      ref={tileRef}
      type="button"
      className={`storage-map-tile storage-map-tile-${node.kind}${labelsHidden ? " labels-hidden" : ""}`}
      style={style}
      title={detail}
      aria-label={t(node.kind === "folder" ? "storageMap.folderAria" : "storageMap.fileAria", {
        name: displayName,
        size: formatBytes(node.size_bytes),
      })}
      onClick={() => onOpen(node)}
    >
      <span className="storage-map-tile-copy" aria-hidden="true">
        <span className="storage-map-tile-name">
          {node.kind === "folder" ? <Folder aria-hidden="true" /> : <File aria-hidden="true" />}
          <strong>{displayName}</strong>
        </span>
        <span className="storage-map-tile-meta">{metricLabel}</span>
        <span className="storage-map-tile-size">{formatBytes(node.size_bytes)}</span>
      </span>
    </button>
  );
}

function StorageMapTreemap({
  nodes,
  colorMode,
  nameSource,
  onOpen,
}: {
  nodes: StorageMapNode[];
  colorMode: StorageMapColorMode;
  nameSource: StorageMapNameSource;
  onOpen: (node: StorageMapNode) => void;
}) {
  const { t } = useTranslation();
  const maxSize = Math.max(...nodes.map((node) => node.size_bytes), 1);
  const rects = useMemo(() => layoutStorageMapNodes(nodes), [nodes]);

  return (
    <div className="storage-map-treemap" role="group" aria-label={t("storageMap.treemapAria")}>
      {rects.map(({ node, x, y, width, height }) => (
        <StorageMapTile
          key={`${node.kind}:${node.path}`}
          node={node}
          colorMode={colorMode}
          maxSize={maxSize}
          rect={{ x, y, width, height }}
          nameSource={nameSource}
          onOpen={onOpen}
        />
      ))}
    </div>
  );
}

export function StorageMapPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { libraries, librariesLoaded } = useAppData();
  const storageMapCacheRef = useRef(
    new LruCache<string, LibraryStorageMap>(32, { ttlMs: 2 * 60 * 1000 }),
  );
  const [data, setData] = useState<LibraryStorageMap | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const selectedLibraryId = Number(searchParams.get("library")) || libraries[0]?.id || null;
  const currentPath = searchParams.get("path") ?? "";
  const colorParam = searchParams.get("color");
  const sortParam = searchParams.get("sort");
  const selectedLibrary = libraries.find((library) => library.id === selectedLibraryId) ?? null;
  const supportsJellyfinNames = Boolean(selectedLibrary?.linked_jellyfin_library);
  const nameSource: StorageMapNameSource =
    supportsJellyfinNames && searchParams.get("names") === "jellyfin" ? "jellyfin" : "file";
  const colorMode: StorageMapColorMode = isColorMode(colorParam) ? colorParam : "codec";
  const sortMode: StorageMapSortMode = isSortMode(sortParam) ? sortParam : "size";

  const updateQuery = useCallback(
    (changes: Record<string, string | null>, replace = false) => {
      const next = new URLSearchParams(searchParams);
      for (const [key, value] of Object.entries(changes)) {
        if (value) next.set(key, value);
        else next.delete(key);
      }
      setSearchParams(next, { replace });
    },
    [searchParams, setSearchParams],
  );

  useEffect(() => {
    if (!librariesLoaded || libraries.length === 0) return;
    const selectedExists = libraries.some((library) => library.id === selectedLibraryId);
    if (!selectedExists) {
      updateQuery({ library: String(libraries[0].id), path: null }, true);
    } else if (!searchParams.get("library") && selectedLibraryId) {
      updateQuery({ library: String(selectedLibraryId) }, true);
    }
  }, [libraries, librariesLoaded, searchParams, selectedLibraryId, updateQuery]);

  const loadMap = useCallback((force = false) => {
    if (!selectedLibraryId) return () => undefined;
    const cacheKey = `${selectedLibraryId}:${currentPath}`;
    const cached = force ? undefined : storageMapCacheRef.current.get(cacheKey);
    if (cached) {
      setData(cached);
      setLoading(false);
      setError(null);
      return () => undefined;
    }
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    api
      .libraryStorageMap(selectedLibraryId, { path: currentPath, signal: controller.signal })
      .then((payload) => {
        storageMapCacheRef.current.set(cacheKey, payload);
        setData(payload);
      })
      .catch((requestError: unknown) => {
        if (controller.signal.aborted) return;
        setError(requestError instanceof Error ? requestError.message : t("storageMap.error"));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [currentPath, selectedLibraryId, t]);

  useEffect(() => loadMap(), [loadMap]);

  const sortedItems = useMemo(() => {
    if (!data) return [];
    return [...data.items].sort((left, right) => {
      if (sortMode === "name") {
        return displayNameForNode(left, nameSource).localeCompare(displayNameForNode(right, nameSource));
      }
      if (sortMode === "quality") {
        return (right.quality_score ?? -1) - (left.quality_score ?? -1) || right.size_bytes - left.size_bytes;
      }
      return (
        right.size_bytes - left.size_bytes ||
        displayNameForNode(left, nameSource).localeCompare(displayNameForNode(right, nameSource))
      );
    });
  }, [data, nameSource, sortMode]);
  const parentPath = currentPath.includes("/") ? currentPath.slice(0, currentPath.lastIndexOf("/")) : "";

  function openNode(node: StorageMapNode) {
    if (node.kind === "folder") {
      updateQuery({ path: node.path });
    } else if (node.file_id) {
      navigate(`/files/${node.file_id}`);
    }
  }

  return (
    <div className="storage-map-page">
      <section className="panel storage-map-panel">
        <div className="storage-map-header">
          <div className="storage-map-title-block">
            <h2>{t("storageMap.title")}</h2>
            <p className="subtitle">{t("storageMap.subtitle")}</p>
          </div>
          {data ? (
            <div className="card-grid grid storage-map-header-cards">
              <StatCard
                label={t("dashboard.storage")}
                value={formatBytes(data.total_size_bytes)}
                tone="blue"
              />
              <StatCard
                label={t("dashboard.files")}
                value={String(data.file_count)}
                tone="teal"
              />
            </div>
          ) : null}
        </div>

        {librariesLoaded && libraries.length === 0 ? (
          <div className="storage-map-empty">
            <MapIcon aria-hidden="true" />
            <h3>{t("storageMap.noLibrariesTitle")}</h3>
            <p>{t("storageMap.noLibrariesBody")}</p>
          </div>
        ) : (
          <div className="storage-map-explorer">
            <div className="storage-map-content">
              <div className={`storage-map-breadcrumb-row${currentPath ? "" : " is-root"}`}>
                <nav className="storage-map-breadcrumbs" aria-label={t("storageMap.breadcrumbAria")}>
                  {(data?.breadcrumbs ?? []).map((breadcrumb, index) => (
                    <span key={breadcrumb.path || "root"}>
                      {index > 0 ? <ChevronRight aria-hidden="true" /> : null}
                      <button type="button" onClick={() => updateQuery({ path: breadcrumb.path })}>
                        {breadcrumb.name}
                      </button>
                    </span>
                  ))}
                </nav>
              </div>

              <div className="storage-map-toolbar">
                <label className="storage-map-field storage-map-library-field">
                  <span>{t("storageMap.library")}</span>
                  <span className="storage-map-select-wrap">
                    <select
                      value={selectedLibraryId ?? ""}
                      disabled={!librariesLoaded}
                      onChange={(event) => updateQuery({ library: event.target.value, path: null })}
                    >
                      {libraries.map((library) => (
                        <option key={library.id} value={library.id}>
                          {library.name}
                        </option>
                      ))}
                    </select>
                    <ChevronDown aria-hidden="true" />
                  </span>
                </label>
                <label className="storage-map-field">
                  <span>{t("storageMap.colorBy")}</span>
                  <span className="storage-map-select-wrap">
                    <select
                      value={colorMode}
                      onChange={(event) => updateQuery({ color: event.target.value })}
                    >
                      {COLOR_MODES.map((mode) => (
                        <option key={mode} value={mode}>
                          {t(`storageMap.modes.${mode}`)}
                        </option>
                      ))}
                    </select>
                    <ChevronDown aria-hidden="true" />
                  </span>
                </label>
                <label className="storage-map-field">
                  <span>{t("storageMap.sortBy")}</span>
                  <span className="storage-map-select-wrap">
                    <select
                      value={sortMode}
                      onChange={(event) => updateQuery({ sort: event.target.value })}
                    >
                      {SORT_MODES.map((mode) => (
                        <option key={mode} value={mode}>
                          {t(`storageMap.sorts.${mode}`)}
                        </option>
                      ))}
                    </select>
                    <ChevronDown aria-hidden="true" />
                  </span>
                </label>
                {supportsJellyfinNames ? (
                  <div
                    className="distribution-chart-mode-toggle analyzed-file-name-source-toggle storage-map-name-source-toggle"
                    role="group"
                    aria-label={t("libraryDetail.fileNameSource.label")}
                  >
                    <SlidingTogglePill
                      activeKey={nameSource}
                      className="nav-active-pill distribution-chart-mode-pill"
                    />
                    <button
                      type="button"
                      data-toggle-key="file"
                      className={`distribution-chart-mode-button analyzed-file-name-source-button${
                        nameSource === "file" ? " active" : ""
                      }`}
                      aria-label={t("libraryDetail.fileNameSource.file")}
                      title={t("libraryDetail.fileNameSource.file")}
                      aria-pressed={nameSource === "file"}
                      onClick={() => updateQuery({ names: null })}
                    >
                      <span className="distribution-chart-mode-button-content">
                        <FileText aria-hidden="true" className="distribution-chart-mode-icon" />
                      </span>
                    </button>
                    <button
                      type="button"
                      data-toggle-key="jellyfin"
                      className={`distribution-chart-mode-button analyzed-file-name-source-button${
                        nameSource === "jellyfin" ? " active" : ""
                      }`}
                      aria-label={t("libraryDetail.fileNameSource.jellyfin")}
                      title={t("libraryDetail.fileNameSource.jellyfin")}
                      aria-pressed={nameSource === "jellyfin"}
                      onClick={() => updateQuery({ names: "jellyfin" })}
                    >
                      <span className="distribution-chart-mode-button-content">
                        <JellyfinIcon aria-hidden="true" className="distribution-chart-mode-icon" />
                      </span>
                    </button>
                  </div>
                ) : null}
                <span className="storage-map-area-hint">
                  <Info aria-hidden="true" />
                  {t("storageMap.areaHint")}
                </span>
              </div>

              <div className={`storage-map-stage${currentPath ? " has-up-overlay" : ""}`}>
                {currentPath ? (
                  <TooltipTrigger
                    ariaLabel={t("storageMap.up")}
                    content={t("storageMap.up")}
                    className="secondary icon-only-button storage-map-up-button storage-map-up-overlay"
                    pinOnClick={false}
                    onClick={() => updateQuery({ path: parentPath })}
                  >
                    <ArrowUp aria-hidden="true" />
                  </TooltipTrigger>
                ) : null}
                {loading && !data ? (
                  <div className="storage-map-empty" aria-busy="true">
                    <RefreshCw aria-hidden="true" className="storage-map-spinner" />
                    <p>{t("storageMap.loading")}</p>
                  </div>
                ) : error ? (
                  <div className="storage-map-empty" role="alert">
                    <Info aria-hidden="true" />
                    <h3>{t("storageMap.errorTitle")}</h3>
                    <p>{error}</p>
                    <button type="button" className="secondary small" onClick={() => loadMap(true)}>
                      <RefreshCw aria-hidden="true" />
                      {t("storageMap.retry")}
                    </button>
                  </div>
                ) : sortedItems.length === 0 ? (
                  <div className="storage-map-empty">
                    <Folder aria-hidden="true" />
                    <h3>{t("storageMap.emptyTitle")}</h3>
                    <p>{t("storageMap.emptyBody")}</p>
                  </div>
                ) : (
                  <StorageMapTreemap
                    nodes={sortedItems}
                    colorMode={colorMode}
                    nameSource={nameSource}
                    onOpen={openNode}
                  />
                )}
              </div>

              <div className="storage-map-footer">
                <span>
                  <i aria-hidden="true" />
                  {t("storageMap.tilesVisible", { count: sortedItems.length })}
                </span>
                <span>{t(`storageMap.modes.${colorMode}`)}</span>
              </div>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}

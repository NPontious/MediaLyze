import {
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Layers3,
  List,
  Search,
  Server,
  X,
} from "lucide-react";
import { type CSSProperties, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { formatDuration } from "../lib/format";
import {
  groupPlaybackEntries,
  type PlaybackDisplayEntry,
  type PlaybackHistoryEntry,
} from "../lib/playback-history";
import {
  HistoryRangeToggle,
  type HistoryRangeSelection,
} from "./LibraryHistoryPanel";
import { SlidingTogglePill } from "./SlidingTogglePill";

export type { PlaybackHistoryEntry } from "../lib/playback-history";

const USER_COLORS = ["#f05f2a", "#277a65", "#4f78c7", "#9a5cc2", "#9aaf1a", "#d48b20"];
const PAGE_SIZE = 8;
const RANGE_STORAGE_KEY = "medialyze-file-streaming-range-selection";

type PlaybackDisplayMode = "individual" | "grouped";

function dateKey(value: Date): string {
  return [
    value.getUTCFullYear(),
    String(value.getUTCMonth() + 1).padStart(2, "0"),
    String(value.getUTCDate()).padStart(2, "0"),
  ].join("-");
}

function parseDateKey(value: string | undefined, endOfDay = false): number | null {
  const match = value?.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!match) return null;
  return Date.UTC(
    Number(match[1]),
    Number(match[2]) - 1,
    Number(match[3]),
    endOfDay ? 23 : 0,
    endOfDay ? 59 : 0,
    endOfDay ? 59 : 0,
    endOfDay ? 999 : 0,
  );
}

function readRangeSelection(): HistoryRangeSelection {
  if (typeof window === "undefined") return { mode: "all" };
  try {
    const parsed = JSON.parse(window.localStorage.getItem(RANGE_STORAGE_KEY) ?? "null");
    if (!["7d", "30d", "1y", "all", "custom"].includes(parsed?.mode)) {
      return { mode: "all" };
    }
    return {
      mode: parsed.mode,
      startDate: typeof parsed.startDate === "string" ? parsed.startDate : undefined,
      endDate: typeof parsed.endDate === "string" ? parsed.endDate : undefined,
    };
  } catch {
    return { mode: "all" };
  }
}

function rangeBounds(entries: PlaybackHistoryEntry[], selection: HistoryRangeSelection): [number, number] | null {
  const timestamps = entries
    .map((entry) => Date.parse(entry.lastPlayedAt))
    .filter(Number.isFinite);
  if (!timestamps.length) return null;
  const earliest = Math.min(...timestamps);
  const latest = Math.max(...timestamps);
  if (selection.mode === "all") {
    return earliest === latest
      ? [earliest - 12 * 60 * 60 * 1000, latest + 12 * 60 * 60 * 1000]
      : [earliest, latest];
  }
  if (selection.mode === "custom") {
    const start = parseDateKey(selection.startDate);
    const end = parseDateKey(selection.endDate ?? selection.startDate, true);
    if (start === null || end === null) return [earliest, latest];
    return start <= end ? [start, end] : [end, start];
  }
  const days = selection.mode === "7d" ? 7 : selection.mode === "30d" ? 30 : 365;
  return [latest - (days - 1) * 24 * 60 * 60 * 1000, latest];
}

function formatTimestamp(value: string, locale: string): string {
  return new Intl.DateTimeFormat(locale, {
    dateStyle: "medium",
    timeStyle: "medium",
  }).format(new Date(value));
}

function formatTimelineLabel(value: number, locale: string): string {
  return new Intl.DateTimeFormat(locale, {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(new Date(value));
}

function csvCell(value: string | number): string {
  const text = String(value);
  return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

export function PlaybackHistoryPanel({
  entries: sourceEntries,
  durationSeconds,
  individualEventsAvailable = true,
}: {
  entries: PlaybackHistoryEntry[];
  durationSeconds?: number | null;
  individualEventsAvailable?: boolean;
}) {
  const { t, i18n } = useTranslation();
  const entries = useMemo<PlaybackHistoryEntry[]>(
    () =>
      sourceEntries
        .filter((entry) => entry.playCount > 0 && Number.isFinite(Date.parse(entry.lastPlayedAt)))
        .slice()
        .sort((left, right) => Date.parse(right.lastPlayedAt) - Date.parse(left.lastPlayedAt)),
    [sourceEntries],
  );
  const users = useMemo(
    () => Array.from(new Map(entries.map((entry) => [`${entry.provider}:${entry.userId}`, entry])).values()),
    [entries],
  );
  const providers = useMemo(() => [...new Set(entries.map((entry) => entry.provider))], [entries]);
  const [rangeSelection, setRangeSelection] = useState<HistoryRangeSelection>(readRangeSelection);
  const [selectedUsers, setSelectedUsers] = useState<Set<string>>(
    () => new Set(entries.map((entry) => `${entry.provider}:${entry.userId}`)),
  );
  const [selectedProvider, setSelectedProvider] = useState("all");
  const [displayMode, setDisplayMode] = useState<PlaybackDisplayMode>("individual");
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(entries[0]?.id ?? null);
  const [page, setPage] = useState(0);
  const knownUserKeysRef = useRef(new Set(entries.map((entry) => `${entry.provider}:${entry.userId}`)));

  useEffect(() => {
    const nextUserKeys = new Set(users.map((user) => `${user.provider}:${user.userId}`));
    setSelectedUsers((current) => {
      const next = new Set([...current].filter((id) => nextUserKeys.has(id)));
      nextUserKeys.forEach((id) => {
        if (!knownUserKeysRef.current.has(id)) next.add(id);
      });
      return next;
    });
    knownUserKeysRef.current = nextUserKeys;
  }, [users]);

  const bounds = useMemo(() => rangeBounds(entries, rangeSelection), [entries, rangeSelection]);
  const filteredSourceEntries = useMemo(() => {
    const normalizedSearch = search.trim().toLocaleLowerCase(i18n.language);
    return entries.filter((entry) => {
      const timestamp = Date.parse(entry.lastPlayedAt);
      const userKey = `${entry.provider}:${entry.userId}`;
      return (
        selectedUsers.has(userKey)
        && (selectedProvider === "all" || entry.provider === selectedProvider)
        && (!bounds || (timestamp >= bounds[0] && timestamp <= bounds[1]))
        && (!normalizedSearch
          || entry.userName.toLocaleLowerCase(i18n.language).includes(normalizedSearch)
          || entry.provider.toLocaleLowerCase(i18n.language).includes(normalizedSearch))
      );
    });
  }, [bounds, entries, i18n.language, search, selectedProvider, selectedUsers]);
  const filteredEntries = useMemo<PlaybackDisplayEntry[]>(
    () =>
      displayMode === "grouped"
        ? groupPlaybackEntries(filteredSourceEntries, durationSeconds)
        : filteredSourceEntries.map((entry) => ({
            ...entry,
            eventCount: 1,
            firstPlayedAt: entry.lastPlayedAt,
          })),
    [displayMode, durationSeconds, filteredSourceEntries],
  );
  const pageCount = Math.max(1, Math.ceil(filteredEntries.length / PAGE_SIZE));
  const visibleEntries = filteredEntries.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  const selectedEntry = selectedId === null
    ? null
    : filteredEntries.find((entry) => entry.id === selectedId) ?? filteredEntries[0] ?? null;
  const minimumDate = entries.length ? dateKey(new Date(Math.min(...entries.map((entry) => Date.parse(entry.lastPlayedAt))))) : null;
  const maximumDate = entries.length ? dateKey(new Date(Math.max(...entries.map((entry) => Date.parse(entry.lastPlayedAt))))) : null;
  const hasResumePosition = filteredEntries.some((entry) => entry.resumePositionSeconds > 0 && !entry.completed);

  useEffect(() => {
    setPage(0);
  }, [displayMode, rangeSelection, search, selectedProvider, selectedUsers]);

  useEffect(() => {
    if (page >= pageCount) setPage(pageCount - 1);
  }, [page, pageCount]);

  function updateRangeSelection(next: HistoryRangeSelection) {
    setRangeSelection(next);
    window.localStorage.setItem(RANGE_STORAGE_KEY, JSON.stringify(next));
  }

  function toggleUser(userKey: string) {
    setSelectedUsers((current) => {
      const next = new Set(current);
      if (next.has(userKey)) next.delete(userKey);
      else next.add(userKey);
      return next;
    });
  }

  function exportCsv() {
    const rows = [
      [
        t("jellyfin.playbackHistory.lastPlayback"),
        t("jellyfin.playbackHistory.user"),
        t("jellyfin.playbackHistory.provider"),
        t("jellyfin.playbackHistory.plays"),
        t("jellyfin.playbackHistory.state"),
        t("jellyfin.playbackHistory.resumePosition"),
      ],
      ...filteredEntries.map((entry) => [
        entry.lastPlayedAt,
        entry.userName,
        entry.provider,
        entry.playCount,
        entry.completed
          ? t("jellyfin.playbackHistory.completed")
          : t("jellyfin.playbackHistory.notCompleted"),
        entry.resumePositionSeconds > 0 ? formatDuration(entry.resumePositionSeconds) : "",
      ]),
    ];
    const blob = new Blob([rows.map((row) => row.map(csvCell).join(",")).join("\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "medialyze-playback-summary.csv";
    anchor.click();
    URL.revokeObjectURL(url);
  }

  if (!entries.length) {
    return <div className="notice">{t("jellyfin.noPlaybackData")}</div>;
  }

  return (
    <div className="playback-history">
      <div className="playback-history-controls">
        <div className="playback-history-control-block">
          <span className="playback-history-control-label">{t("jellyfin.playbackHistory.range")}</span>
          <HistoryRangeToggle
            selection={rangeSelection}
            onChange={updateRangeSelection}
            minimumDate={minimumDate}
            maximumDate={maximumDate}
            defaultStartDate={bounds ? dateKey(new Date(bounds[0])) : minimumDate}
            defaultEndDate={bounds ? dateKey(new Date(bounds[1])) : maximumDate}
            ariaLabel={t("jellyfin.playbackHistory.range")}
          />
        </div>
        {providers.length > 1 ? (
          <label className="playback-history-provider-filter">
            <span className="playback-history-control-label">{t("jellyfin.playbackHistory.provider")}</span>
            <select value={selectedProvider} onChange={(event) => setSelectedProvider(event.target.value)}>
              <option value="all">{t("jellyfin.playbackHistory.allProviders")}</option>
              {providers.map((provider) => <option key={provider}>{provider}</option>)}
            </select>
          </label>
        ) : null}
        <div className="playback-history-users">
          <span className="playback-history-control-label">{t("jellyfin.playbackHistory.users")}</span>
          <div className="playback-history-user-list">
            {users.map((user, index) => {
              const userKey = `${user.provider}:${user.userId}`;
              const active = selectedUsers.has(userKey);
              return (
                <button
                  key={userKey}
                  type="button"
                  className={`playback-history-user${active ? " is-active" : ""}`}
                  aria-pressed={active}
                  onClick={() => toggleUser(userKey)}
                >
                  <span
                    className="playback-history-user-dot"
                    style={{ "--playback-user-color": USER_COLORS[index % USER_COLORS.length] } as CSSProperties}
                  />
                  <span>{user.userName}</span>
                  <span className="playback-history-user-check">{active ? "✓" : ""}</span>
                </button>
              );
            })}
          </div>
        </div>
      </div>

      <p className="playback-history-scope-note">{t("jellyfin.playbackHistory.scopeNote")}</p>

      <div className={`playback-history-layout${selectedEntry ? " has-detail" : ""}`}>
        <div className="playback-history-main">
          <section className="playback-history-timeline" aria-label={t("jellyfin.playbackHistory.timeline")}>
            <div className="playback-history-timeline-summary">
              <div>
                <span>{t("jellyfin.playbackHistory.firstVisible")}</span>
                <strong>{bounds ? formatTimelineLabel(bounds[0], i18n.language) : "—"}</strong>
              </div>
              <div className="playback-history-timeline-count">
                <span>{t("jellyfin.playbackHistory.visibleRange")}</span>
                <strong>
                  {t(
                    displayMode === "grouped"
                      ? "jellyfin.playbackHistory.groupCount"
                      : "jellyfin.playbackHistory.latestCount",
                    { count: filteredEntries.length },
                  )}
                </strong>
                <div
                  className="playback-history-display-toggle"
                  role="group"
                  aria-label={t("jellyfin.playbackHistory.displayMode")}
                >
                  <SlidingTogglePill
                    activeKey={displayMode}
                    className="nav-active-pill playback-history-display-pill"
                  />
                  <button
                    type="button"
                    data-toggle-key="individual"
                    className={displayMode === "individual" ? "active" : ""}
                    aria-label={t(
                      individualEventsAvailable
                        ? "jellyfin.playbackHistory.showIndividual"
                        : "jellyfin.playbackHistory.availableTimestamps",
                    )}
                    title={t(
                      individualEventsAvailable
                        ? "jellyfin.playbackHistory.showIndividual"
                        : "jellyfin.playbackHistory.availableTimestamps",
                    )}
                    aria-pressed={displayMode === "individual"}
                    onClick={() => setDisplayMode("individual")}
                  >
                    <List aria-hidden="true" />
                  </button>
                  <button
                    type="button"
                    data-toggle-key="grouped"
                    className={displayMode === "grouped" ? "active" : ""}
                    aria-label={t(
                      individualEventsAvailable
                        ? "jellyfin.playbackHistory.groupNearby"
                        : "jellyfin.playbackHistory.groupUnavailable",
                    )}
                    title={t(
                      individualEventsAvailable
                        ? "jellyfin.playbackHistory.groupNearby"
                        : "jellyfin.playbackHistory.groupUnavailable",
                    )}
                    aria-pressed={displayMode === "grouped"}
                    disabled={!individualEventsAvailable}
                    onClick={() => setDisplayMode("grouped")}
                  >
                    <Layers3 aria-hidden="true" />
                  </button>
                </div>
              </div>
              <div>
                <span>{t("jellyfin.playbackHistory.latestVisible")}</span>
                <strong>{bounds ? formatTimelineLabel(bounds[1], i18n.language) : "—"}</strong>
              </div>
            </div>
            <div className="playback-history-timeline-track">
              <span className="playback-history-timeline-line" />
              {filteredEntries.map((entry) => {
                const userIndex = users.findIndex(
                  (user) => user.provider === entry.provider && user.userId === entry.userId,
                );
                const timestamp = Date.parse(entry.lastPlayedAt);
                const position = bounds && bounds[1] > bounds[0]
                  ? ((timestamp - bounds[0]) / (bounds[1] - bounds[0])) * 100
                  : 50;
                return (
                  <button
                    key={entry.id}
                    type="button"
                    className={[
                      "playback-history-timeline-event",
                      entry.eventCount > 1 ? "is-cluster" : "",
                      selectedEntry?.id === entry.id ? "is-selected" : "",
                    ].filter(Boolean).join(" ")}
                    data-event-count={entry.eventCount}
                    style={{
                      "--playback-event-position": `${Math.max(0, Math.min(100, position))}%`,
                      "--playback-user-color": USER_COLORS[userIndex % USER_COLORS.length],
                    } as CSSProperties}
                    aria-label={
                      entry.eventCount > 1
                        ? t("jellyfin.playbackHistory.groupedEventLabel", {
                            user: entry.userName,
                            count: entry.eventCount,
                            start: formatTimestamp(entry.firstPlayedAt, i18n.language),
                            end: formatTimestamp(entry.lastPlayedAt, i18n.language),
                          })
                        : `${entry.userName}, ${formatTimestamp(entry.lastPlayedAt, i18n.language)}`
                    }
                    title={`${entry.userName} · ${formatTimestamp(entry.lastPlayedAt, i18n.language)}`}
                    onClick={() => setSelectedId(entry.id)}
                  />
                );
              })}
            </div>
            <div className="playback-history-timeline-axis" aria-hidden="true">
              <span>{bounds ? formatTimelineLabel(bounds[0], i18n.language) : "—"}</span>
              <span>{bounds ? formatTimelineLabel(bounds[1], i18n.language) : "—"}</span>
            </div>
          </section>

          <div className="playback-history-table-toolbar">
            <label className="playback-history-search">
              <Search aria-hidden="true" />
              <span className="sr-only">{t("jellyfin.playbackHistory.search")}</span>
              <input
                type="search"
                value={search}
                placeholder={t("jellyfin.playbackHistory.search")}
                onChange={(event) => setSearch(event.target.value)}
              />
            </label>
            <span className="playback-history-result-count">
              {t(
                displayMode === "grouped"
                  ? "jellyfin.playbackHistory.groupCount"
                  : "jellyfin.playbackHistory.latestCount",
                { count: filteredEntries.length },
              )}
            </span>
            <button
              type="button"
              className="secondary small playback-history-export-button"
              disabled={!filteredEntries.length}
              onClick={exportCsv}
            >
              {t("jellyfin.playbackHistory.export")}
            </button>
          </div>

          {visibleEntries.length ? (
            <div className="playback-history-table-scroll">
              <table className="playback-history-table">
                <thead>
                  <tr>
                    <th>{t("jellyfin.playbackHistory.lastPlayback")}</th>
                    <th>{t("jellyfin.playbackHistory.user")}</th>
                    <th>{t("jellyfin.playbackHistory.provider")}</th>
                    <th>{t("jellyfin.playbackHistory.plays")}</th>
                    <th>{t("jellyfin.playbackHistory.state")}</th>
                    {hasResumePosition ? <th>{t("jellyfin.playbackHistory.resumePosition")}</th> : null}
                    <th><span className="sr-only">{t("jellyfin.playbackHistory.openDetail")}</span></th>
                  </tr>
                </thead>
                <tbody>
                  {visibleEntries.map((entry) => {
                    const userIndex = users.findIndex(
                      (user) => user.provider === entry.provider && user.userId === entry.userId,
                    );
                    return (
                      <tr
                        key={entry.id}
                        className={selectedEntry?.id === entry.id ? "is-selected" : ""}
                        onClick={() => setSelectedId(entry.id)}
                      >
                        <td>
                          <span className="playback-history-timestamp">
                            <span
                              className="playback-history-table-dot"
                              style={{ "--playback-user-color": USER_COLORS[userIndex % USER_COLORS.length] } as CSSProperties}
                            />
                            {formatTimestamp(entry.lastPlayedAt, i18n.language)}
                          </span>
                        </td>
                        <td>{entry.userName}</td>
                        <td><span className="playback-history-provider"><Server aria-hidden="true" />{entry.provider}</span></td>
                        <td>{entry.playCount}</td>
                        <td>
                          <span className={`playback-history-state${entry.completed ? " is-complete" : ""}`}>
                            {entry.completed ? <CheckCircle2 aria-hidden="true" /> : <span className="playback-history-state-box" />}
                            {entry.completed
                              ? t("jellyfin.playbackHistory.completed")
                              : t("jellyfin.playbackHistory.notCompleted")}
                          </span>
                        </td>
                        {hasResumePosition ? (
                          <td>{entry.resumePositionSeconds > 0 && !entry.completed ? formatDuration(entry.resumePositionSeconds) : "—"}</td>
                        ) : null}
                        <td><ChevronRight aria-hidden="true" /></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : <div className="notice">{t("jellyfin.playbackHistory.noResults")}</div>}

          {pageCount > 1 ? (
            <div className="playback-history-pagination">
              <button
                type="button"
                className="secondary icon-only-button"
                aria-label={t("jellyfin.playbackHistory.previous")}
                disabled={page === 0}
                onClick={() => setPage((current) => current - 1)}
              >
                <ChevronLeft aria-hidden="true" />
              </button>
              <span>{t("jellyfin.playbackHistory.page", { current: page + 1, total: pageCount })}</span>
              <button
                type="button"
                className="secondary icon-only-button"
                aria-label={t("jellyfin.playbackHistory.next")}
                disabled={page + 1 >= pageCount}
                onClick={() => setPage((current) => current + 1)}
              >
                <ChevronRight aria-hidden="true" />
              </button>
            </div>
          ) : null}
        </div>

        {selectedEntry ? (
          <aside className="playback-history-detail" aria-label={t("jellyfin.playbackHistory.detail")}>
            <div className="playback-history-detail-header">
              <div>
                <span className="playback-history-control-label">{t("jellyfin.playbackHistory.detail")}</span>
                <h3>{selectedEntry.userName}</h3>
              </div>
              <button
                type="button"
                className="secondary icon-only-button async-panel-toggle-icon-button-flat"
                aria-label={t("common.close")}
                onClick={() => setSelectedId(null)}
              >
                <X aria-hidden="true" />
              </button>
            </div>
            <dl className="playback-history-detail-list">
              <div>
                <dt>{t("jellyfin.playbackHistory.lastPlayback")}</dt>
                <dd>{formatTimestamp(selectedEntry.lastPlayedAt, i18n.language)}</dd>
              </div>
              <div>
                <dt>{t("jellyfin.playbackHistory.provider")}</dt>
                <dd><span className="playback-history-provider"><Server aria-hidden="true" />{selectedEntry.provider}</span></dd>
              </div>
              <div>
                <dt>{t("jellyfin.playbackHistory.plays")}</dt>
                <dd>{selectedEntry.playCount}</dd>
              </div>
              <div>
                <dt>{t("jellyfin.playbackHistory.state")}</dt>
                <dd>
                  {selectedEntry.completed
                    ? t("jellyfin.playbackHistory.completed")
                    : t("jellyfin.playbackHistory.notCompleted")}
                </dd>
              </div>
              {selectedEntry.resumePositionSeconds > 0 && !selectedEntry.completed ? (
                <div>
                  <dt>{t("jellyfin.playbackHistory.resumePosition")}</dt>
                  <dd>{formatDuration(selectedEntry.resumePositionSeconds)}</dd>
                  {durationSeconds && durationSeconds > 0 ? (
                    <span className="playback-history-progress">
                      <span
                        style={{
                          width: `${Math.min(100, (selectedEntry.resumePositionSeconds / durationSeconds) * 100)}%`,
                        }}
                      />
                    </span>
                  ) : null}
                </div>
              ) : null}
            </dl>
          </aside>
        ) : null}
      </div>
    </div>
  );
}

import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  CalendarDays,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CircleStop,
  Clock3,
  Download,
  FileAudio,
  FileJson,
  Film,
  Gauge,
  GitCompareArrows,
  HardDrive,
  History,
  House,
  Image,
  Info,
  Languages,
  Menu,
  Radio,
  Search,
  Server,
  Settings,
  SlidersHorizontal,
  Subtitles,
  Video,
  X,
} from "lucide-react";

const USERS = [
  { id: "frederik", name: "Frederik", color: "#1b998b" },
  { id: "louise", name: "Louise", color: "#ff9f1c" },
  { id: "mads", name: "Mads", color: "#4f83e3" },
  { id: "sara", name: "Sara", color: "#9b5cc1" },
  { id: "kids", name: "Kids", color: "#86b817" },
  { id: "other", name: "Other", color: "#8a8882" },
];

const PROVIDERS = ["Jellyfin", "Plex"];
const RANGE_OPTIONS = [
  { id: "7d", label: "7 days", days: 7 },
  { id: "30d", label: "30 days", days: 30 },
  { id: "1y", label: "1 year", days: 365 },
  { id: "all", label: "All", days: null },
  { id: "custom", label: "Custom", days: null },
];

const SESSION_BLUEPRINTS = [
  ["2026-07-27T22:41:13", "frederik", "Jellyfin", 4542, 76, "completed", "Web 10.9.6", "Chrome 126 · Windows", "192.168.1.42"],
  ["2026-07-27T20:18:07", "louise", "Jellyfin", 1351, 23, "stopped", "Jellyfin Mobile", "iOS 18 · iPhone", "192.168.1.73"],
  ["2026-07-27T09:03:22", "mads", "Jellyfin", 2108, 35, "stopped", "Android TV 0.18.2", "Google TV", "192.168.1.91"],
  ["2026-07-26T21:11:55", "frederik", "Plex", 3737, 63, "completed", "Plex Web 4.133", "Safari · macOS", "192.168.1.42"],
  ["2026-07-26T17:42:10", "sara", "Jellyfin", 1124, 18, "stopped", "Jellyfin Mobile", "Android 16", "192.168.1.58"],
  ["2026-07-25T14:27:33", "kids", "Jellyfin", 2823, 47, "completed", "Jellyfin for Android TV", "Nvidia Shield", "192.168.1.86"],
  ["2026-07-24T19:58:50", "louise", "Plex", 739, 12, "stopped", "Plex HTPC", "Windows 11", "192.168.1.73"],
  ["2026-07-22T13:16:41", "mads", "Jellyfin", 4265, 71, "completed", "Jellyfin Web 10.9.6", "Firefox 128 · Linux", "192.168.1.91"],
  ["2026-07-20T22:05:09", "frederik", "Jellyfin", 1540, 25, "stopped", "Jellyfin Web 10.9.6", "Chrome 126 · Windows", "192.168.1.42"],
  ["2026-07-19T16:44:02", "sara", "Plex", 1997, 33, "stopped", "Plex Mobile", "Android 16", "192.168.1.58"],
  ["2026-07-18T20:36:19", "kids", "Jellyfin", 5183, 86, "completed", "Jellyfin for Android TV", "Nvidia Shield", "192.168.1.86"],
  ["2026-07-17T23:12:44", "other", "Jellyfin", 602, 10, "stopped", "Jellyfin Web 10.9.6", "Edge · Windows", "192.168.1.109"],
  ["2026-07-16T18:28:31", "frederik", "Jellyfin", 5921, 99, "completed", "Apple TV 1.8.0", "tvOS 19", "192.168.1.42"],
  ["2026-07-16T12:41:05", "louise", "Jellyfin", 3477, 58, "completed", "Jellyfin Mobile", "iOS 18 · iPad", "192.168.1.73"],
  ["2026-07-15T21:08:53", "mads", "Plex", 2571, 43, "stopped", "Plex Web 4.133", "ChromeOS", "192.168.1.91"],
  ["2026-07-14T19:55:18", "sara", "Jellyfin", 4204, 70, "completed", "Jellyfin Mobile", "Android 16", "192.168.1.58"],
  ["2026-07-13T10:32:47", "kids", "Jellyfin", 5980, 100, "completed", "Jellyfin for Android TV", "Nvidia Shield", "192.168.1.86"],
  ["2026-07-12T22:20:14", "frederik", "Plex", 2286, 38, "stopped", "Plex Web 4.133", "Safari · macOS", "192.168.1.42"],
  ["2026-07-11T18:03:26", "other", "Jellyfin", 4909, 82, "completed", "Jellyfin Web 10.9.6", "Chrome · Linux", "192.168.1.109"],
  ["2026-07-10T16:19:38", "louise", "Jellyfin", 3195, 53, "completed", "Jellyfin Mobile", "iOS 18 · iPhone", "192.168.1.73"],
  ["2026-07-09T22:52:41", "mads", "Jellyfin", 1288, 21, "stopped", "Android TV 0.18.2", "Google TV", "192.168.1.91"],
  ["2026-07-09T14:14:16", "frederik", "Jellyfin", 5260, 88, "completed", "Jellyfin Web 10.9.6", "Chrome 126 · Windows", "192.168.1.42"],
  ["2026-07-08T20:33:09", "kids", "Plex", 5758, 96, "completed", "Plex for Android TV", "Nvidia Shield", "192.168.1.86"],
  ["2026-07-07T17:25:31", "sara", "Jellyfin", 896, 15, "stopped", "Jellyfin Mobile", "Android 16", "192.168.1.58"],
  ["2026-07-06T23:07:58", "louise", "Jellyfin", 3868, 64, "completed", "Jellyfin Mobile", "iOS 18 · iPad", "192.168.1.73"],
  ["2026-07-05T18:46:22", "mads", "Plex", 2964, 49, "stopped", "Plex Web 4.133", "ChromeOS", "192.168.1.91"],
  ["2026-07-04T21:36:47", "frederik", "Jellyfin", 5996, 100, "completed", "Apple TV 1.8.0", "tvOS 19", "192.168.1.42"],
  ["2026-07-03T16:10:04", "other", "Jellyfin", 744, 12, "stopped", "Jellyfin Web 10.9.6", "Edge · Windows", "192.168.1.109"],
  ["2026-07-02T19:24:38", "kids", "Jellyfin", 4566, 76, "completed", "Jellyfin for Android TV", "Nvidia Shield", "192.168.1.86"],
  ["2026-07-01T13:58:51", "sara", "Plex", 1884, 31, "stopped", "Plex Mobile", "Android 16", "192.168.1.58"],
  ["2026-06-30T22:19:45", "frederik", "Jellyfin", 3421, 57, "completed", "Jellyfin Web 10.9.6", "Chrome 126 · Windows", "192.168.1.42"],
  ["2026-06-29T18:33:26", "louise", "Jellyfin", 4800, 80, "completed", "Jellyfin Mobile", "iOS 18 · iPhone", "192.168.1.73"],
  ["2026-06-28T10:15:00", "mads", "Jellyfin", 901, 15, "stopped", "Android TV 0.18.2", "Google TV", "192.168.1.91"],
  ["2026-05-22T20:11:42", "frederik", "Jellyfin", 5100, 85, "completed", "Apple TV 1.8.0", "tvOS 19", "192.168.1.42"],
  ["2026-04-11T17:08:12", "sara", "Plex", 3020, 50, "stopped", "Plex Mobile", "Android 16", "192.168.1.58"],
  ["2026-02-17T21:46:30", "louise", "Jellyfin", 5820, 97, "completed", "Jellyfin Mobile", "iOS 18 · iPad", "192.168.1.73"],
  ["2025-12-26T14:03:52", "kids", "Jellyfin", 6000, 100, "completed", "Jellyfin for Android TV", "Nvidia Shield", "192.168.1.86"],
  ["2025-10-04T19:32:18", "mads", "Plex", 2790, 46, "stopped", "Plex Web 4.133", "ChromeOS", "192.168.1.91"],
  ["2025-07-19T22:18:06", "frederik", "Jellyfin", 4480, 74, "completed", "Jellyfin Web 10.9.6", "Chrome 126 · Windows", "192.168.1.42"],
  ["2025-03-12T18:27:44", "other", "Jellyfin", 1320, 22, "stopped", "Jellyfin Web 10.9.6", "Edge · Windows", "192.168.1.109"],
  ["2024-11-03T20:45:12", "sara", "Plex", 3870, 64, "completed", "Plex Mobile", "Android 15", "192.168.1.58"],
  ["2024-06-28T10:15:00", "frederik", "Jellyfin", 6000, 100, "completed", "Apple TV 1.7.3", "tvOS 18", "192.168.1.42"],
];

const EVENTS = SESSION_BLUEPRINTS.map((item, index) => ({
  id: index + 1,
  timestamp: new Date(item[0]),
  userId: item[1],
  provider: item[2],
  watchedSeconds: item[3],
  percent: item[4],
  status: item[5],
  client: item[6],
  device: item[7],
  ip: item[8],
  runtimeSeconds: 5998,
  startSeconds: 0,
}));

const NAV_ITEMS = [
  { label: "Overview", icon: Info },
  { label: "Video", icon: Video },
  { label: "Audio", icon: FileAudio },
  { label: "Subtitles", icon: Subtitles },
  { label: "Format", icon: HardDrive },
  { label: "Chapters", icon: Film },
  { label: "Compatibility", icon: Gauge },
  { label: "Streaming", icon: Radio, active: true },
  { label: "Cover", icon: Image },
  { label: "File history", icon: History },
  { label: "Raw ffprobe JSON", icon: FileJson },
];

const PAGE_SIZE = 8;

function formatDateTime(date) {
  return new Intl.DateTimeFormat("en-GB", {
    dateStyle: "medium",
    timeStyle: "medium",
    hour12: false,
  }).format(date);
}

function formatShortDateTime(date) {
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

function formatDuration(seconds) {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = Math.floor(seconds % 60);
  return [hours, minutes, secs].map((value) => String(value).padStart(2, "0")).join(":");
}

function formatRangeDate(date, includeYear = true) {
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    ...(includeYear ? { year: "numeric" } : {}),
  }).format(date);
}

function dateInputValue(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function userFor(event) {
  return USERS.find((user) => user.id === event.userId) ?? USERS[USERS.length - 1];
}

function Status({ status }) {
  const completed = status === "completed";
  return (
    <span className={`event-status ${completed ? "is-completed" : "is-stopped"}`}>
      {completed ? <CheckCircle2 aria-hidden="true" /> : <CircleStop aria-hidden="true" />}
      {completed ? "Completed" : "Stopped"}
    </span>
  );
}

function Provider({ name }) {
  return (
    <span className="provider-name">
      <Server aria-hidden="true" />
      {name}
    </span>
  );
}

function AppHeader() {
  return (
    <header className="panel hero-panel media-header">
      <div className="app-title-block">
        <a className="app-title-link" href="#" aria-label="MediaLyze Home">
          <h1>MediaLyze</h1>
        </a>
        <button type="button" className="app-version" aria-label="Development version">dev</button>
      </div>
      <nav className="media-nav-panel" aria-label="Primary navigation">
        <div className="media-nav-icons">
          <a className="icon-nav-button active" href="#" aria-label="Home"><House className="nav-icon" /></a>
          <button type="button" className="icon-nav-button" aria-label="Compare files"><GitCompareArrows className="nav-icon" /></button>
          <button type="button" className="icon-nav-button" aria-label="Settings"><Settings className="nav-icon" /></button>
        </div>
        <div className="media-nav-libraries">
          <button type="button" className="library-nav-link active">Movies</button>
        </div>
      </nav>
    </header>
  );
}

function FileDetailSidebar({ mobileOpen, onMobileToggle }) {
  return (
    <aside className="settings-navigation-panel file-detail-navigation-panel" aria-label="File detail menu">
      <button type="button" className="settings-mobile-menu-button" aria-expanded={mobileOpen} onClick={onMobileToggle}>
        <span><Radio className="nav-icon" />Streaming</span>
        <Menu className="nav-icon" />
      </button>
      <div className={`file-detail-sidebar-content${mobileOpen ? " is-mobile-open" : ""}`}>
        <div className="file-detail-navigation-actions">
          <button type="button" className="secondary file-detail-navigation-back-button">
            <ArrowLeft className="nav-icon" />
            <span>Back</span>
          </button>
        </div>
        <nav className="settings-navigation-list">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            return (
              <button type="button" className={`settings-navigation-item${item.active ? " active" : ""}`} key={item.label}>
                {item.active ? <span className="nav-active-pill" aria-hidden="true" /> : null}
                <span className="settings-navigation-item-content">
                  <Icon className="nav-icon" aria-hidden="true" />
                  <span>{item.label}</span>
                </span>
              </button>
            );
          })}
        </nav>
      </div>
    </aside>
  );
}

function RangeSelector({ value, onChange, customOpen, setCustomOpen, customStart, customEnd, onApplyCustom }) {
  const [draftStart, setDraftStart] = useState(customStart);
  const [draftEnd, setDraftEnd] = useState(customEnd);

  useEffect(() => {
    setDraftStart(customStart);
    setDraftEnd(customEnd);
  }, [customStart, customEnd]);

  return (
    <div className="range-control-wrap">
      <span className="control-label">History range</span>
      <div className="library-history-range-toggle" role="group" aria-label="History range">
        {RANGE_OPTIONS.map((option) => {
          const active = value === option.id;
          if (option.id === "custom") {
            return (
              <div className="library-history-range-custom-shell" key={option.id}>
                <button
                  type="button"
                  className={`library-history-range-button library-history-range-button-custom${active ? " active" : ""}`}
                  aria-pressed={active}
                  aria-expanded={customOpen}
                  onClick={() => {
                    onChange("custom");
                    setCustomOpen((current) => !current);
                  }}
                >
                  {active ? <span className="range-active-pill" aria-hidden="true" /> : null}
                  <span className="library-history-range-button-content"><CalendarDays />{option.label}</span>
                </button>
                {customOpen ? (
                  <div className="history-range-picker-popover" role="dialog" aria-label="Custom date range">
                    <div className="custom-range-heading">
                      <div>
                        <strong>Custom date range</strong>
                        <span>Filter playback events by day.</span>
                      </div>
                      <button type="button" className="icon-button" aria-label="Close custom range" onClick={() => setCustomOpen(false)}><X /></button>
                    </div>
                    <div className="custom-range-fields">
                      <label>From<input type="date" value={draftStart} max={draftEnd} onInput={(event) => setDraftStart(event.currentTarget.value)} /></label>
                      <label>To<input type="date" value={draftEnd} min={draftStart} onInput={(event) => setDraftEnd(event.currentTarget.value)} /></label>
                    </div>
                    <div className="history-range-picker-footer">
                      <button type="button" className="secondary small" onClick={() => setCustomOpen(false)}>Cancel</button>
                      <button type="button" className="small" disabled={!draftStart || !draftEnd} onClick={() => onApplyCustom(draftStart, draftEnd)}>Apply</button>
                    </div>
                  </div>
                ) : null}
              </div>
            );
          }
          return (
            <button type="button" className={`library-history-range-button${active ? " active" : ""}`} key={option.id} aria-pressed={active} onClick={() => onChange(option.id)}>
              {active ? <span className="range-active-pill" aria-hidden="true" /> : null}
              <span className="library-history-range-button-content">{option.label}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function UserFilters({ selected, onToggle }) {
  return (
    <div className="user-filter-area">
      <span className="control-label">Users</span>
      <div className="user-filter-list">
        {USERS.map((user) => (
          <label className="user-filter" key={user.id}>
            <input type="checkbox" checked={selected.includes(user.id)} onChange={() => onToggle(user.id)} />
            <span className="user-color" style={{ "--user-color": user.color }} aria-hidden="true" />
            <span>{user.name}</span>
          </label>
        ))}
      </div>
    </div>
  );
}

function Timeline({ events, selectedId, onSelect }) {
  const sorted = [...events].sort((a, b) => a.timestamp - b.timestamp);
  if (!sorted.length) {
    return <div className="timeline-empty">No playback events match the selected filters.</div>;
  }
  const first = sorted[0].timestamp;
  const last = sorted[sorted.length - 1].timestamp;
  const rawSpan = last.getTime() - first.getTime();
  const span = Math.max(rawSpan, 86_400_000);
  const ticks = Array.from({ length: 5 }, (_, index) => {
    const date = new Date(first.getTime() + (span * index) / 4);
    return { date, left: index * 25 };
  });
  return (
    <div className="timeline">
      <div className="timeline-summary">
        <div><span>First playback</span><strong>{formatShortDateTime(first)}</strong></div>
        <div className="timeline-range-center"><span>Visible range</span><strong>{formatRangeDate(first)} – {formatRangeDate(last)}</strong></div>
        <div className="timeline-summary-end"><span>Latest playback</span><strong>{formatShortDateTime(last)}</strong></div>
      </div>
      <div className="timeline-plot" aria-label={`${sorted.length} playback events from ${formatDateTime(first)} to ${formatDateTime(last)}`}>
        <div className="timeline-line" aria-hidden="true" />
        {ticks.map((tick) => (
          <div className="timeline-tick" style={{ left: `${tick.left}%` }} key={tick.left}>
            <span>{formatRangeDate(tick.date, first.getFullYear() !== last.getFullYear())}</span>
          </div>
        ))}
        {sorted.map((event, index) => {
          const user = userFor(event);
          const left = rawSpan === 0 ? 50 : ((event.timestamp.getTime() - first.getTime()) / rawSpan) * 100;
          const selected = event.id === selectedId;
          return (
            <button
              type="button"
              className={`timeline-event${selected ? " is-selected" : ""}`}
              style={{ "--event-left": `${Math.min(99, Math.max(1, left))}%`, "--event-color": user.color, "--event-offset": `${(index % 3) * 5 - 5}px` }}
              key={event.id}
              aria-label={`${user.name}, ${formatDateTime(event.timestamp)}, ${event.provider}`}
              onClick={() => onSelect(event.id)}
            >
              <span className="timeline-tooltip">
                <strong>{user.name}</strong>
                <span>{formatDateTime(event.timestamp)}</span>
                <span>{event.provider} · {event.percent}%</span>
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function EventDetail({ event, onClose }) {
  if (!event) return null;
  const user = userFor(event);
  return (
    <aside className="event-detail" aria-label="Event detail">
      <div className="event-detail-header">
        <div>
          <span className="eyebrow">Selected playback</span>
          <h3>Event detail</h3>
        </div>
        <button type="button" className="icon-button" aria-label="Close event detail" onClick={onClose}><X /></button>
      </div>
      <div className="selected-event-indicator"><span style={{ "--user-color": user.color }} />Selected event</div>
      <section className="event-detail-section">
        <span className="detail-label">Timestamp</span>
        <strong className="detail-primary">{formatDateTime(event.timestamp)}</strong>
        <span className="detail-secondary">Europe/Berlin</span>
      </section>
      <section className="event-detail-section detail-inline-grid">
        <div>
          <span className="detail-label">User</span>
          <strong className="detail-with-dot"><span style={{ "--user-color": user.color }} />{user.name}</strong>
        </div>
        <div>
          <span className="detail-label">Provider</span>
          <strong><Provider name={event.provider} /></strong>
        </div>
      </section>
      <section className="event-detail-section">
        <span className="detail-label">Watched duration</span>
        <strong className="detail-primary">{formatDuration(event.watchedSeconds)}</strong>
        <span className="detail-secondary">hh:mm:ss</span>
      </section>
      <section className="event-detail-section">
        <span className="detail-label">Percent completed</span>
        <strong className="detail-primary">{event.percent}%</strong>
        <div className="progress"><span style={{ width: `${event.percent}%` }} /></div>
      </section>
      <section className="event-detail-section">
        <span className="detail-label">Session status</span>
        <strong><Status status={event.status} /></strong>
        <span className="detail-secondary">{event.status === "completed" ? "Playback reached the completion threshold." : "Playback stopped before the completion threshold."}</span>
      </section>
      <section className="event-detail-section playback-range">
        <span className="detail-label">Playback range</span>
        <strong>{formatDuration(event.startSeconds)} – {formatDuration(event.watchedSeconds)}</strong>
        <span className="detail-secondary">{formatDuration(event.runtimeSeconds)} total runtime</span>
        <dl>
          <div><dt>Start position</dt><dd>{formatDuration(event.startSeconds)}</dd></div>
          <div><dt>End position</dt><dd>{formatDuration(event.watchedSeconds)}</dd></div>
        </dl>
      </section>
      <section className="event-detail-section">
        <span className="detail-label">Client</span>
        <strong>{event.client}</strong>
        <span className="detail-secondary">{event.device}</span>
        <dl>
          <div><dt>IP address</dt><dd>{event.ip}</dd></div>
        </dl>
      </section>
    </aside>
  );
}

function Pagination({ current, totalPages, onChange }) {
  if (totalPages <= 1) return null;
  const pages = Array.from({ length: totalPages }, (_, index) => index + 1);
  return (
    <nav className="pagination" aria-label="Playback event pages">
      <button type="button" className="secondary icon-button" aria-label="Previous page" disabled={current === 1} onClick={() => onChange(current - 1)}><ChevronLeft /></button>
      {pages.map((page) => (
        <button type="button" className={`secondary page-button${page === current ? " active" : ""}`} aria-current={page === current ? "page" : undefined} onClick={() => onChange(page)} key={page}>{page}</button>
      ))}
      <button type="button" className="secondary icon-button" aria-label="Next page" disabled={current === totalPages} onClick={() => onChange(current + 1)}><ChevronRight /></button>
    </nav>
  );
}

function EventTable({ events, selectedId, onSelect, search, onSearch, onExport, page, onPage }) {
  const [exported, setExported] = useState(false);
  const totalPages = Math.max(1, Math.ceil(events.length / PAGE_SIZE));
  const visibleEvents = events.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  return (
    <section className="events-section">
      <div className="events-toolbar">
        <label className="event-search">
          <Search aria-hidden="true" />
          <input type="search" placeholder="Search events" value={search} onInput={(event) => onSearch(event.currentTarget.value)} />
        </label>
        <div className="events-toolbar-actions">
          <span>{events.length} {events.length === 1 ? "event" : "events"}</span>
          <button
            type="button"
            className={`secondary small export-button${exported ? " is-success" : ""}`}
            onClick={() => {
              onExport();
              setExported(true);
              window.setTimeout(() => setExported(false), 1800);
            }}
          >
            {exported ? <Check /> : <Download />}
            {exported ? "CSV exported" : "Export CSV"}
          </button>
        </div>
      </div>
      {visibleEvents.length ? (
        <div className="events-table-wrap">
          <table className="events-table">
            <thead>
              <tr>
                <th>Timestamp <ChevronDown /></th>
                <th>User</th>
                <th>Provider</th>
                <th>Watched</th>
                <th>Completion</th>
                <th>Status</th>
                <th><span className="sr-only">Open</span></th>
              </tr>
            </thead>
            <tbody>
              {visibleEvents.map((event) => {
                const user = userFor(event);
                const selected = event.id === selectedId;
                return (
                  <tr className={selected ? "is-selected" : ""} key={event.id} onClick={() => onSelect(event.id)}>
                    <td><span className="event-user-dot" style={{ "--user-color": user.color }} />{formatShortDateTime(event.timestamp)}</td>
                    <td>{user.name}</td>
                    <td><Provider name={event.provider} /></td>
                    <td>{formatDuration(event.watchedSeconds)}</td>
                    <td>
                      <span className="completion-cell"><span>{event.percent}%</span><span className="completion-mini"><i style={{ width: `${event.percent}%` }} /></span></span>
                    </td>
                    <td><Status status={event.status} /></td>
                    <td><button type="button" className="row-open-button" aria-label={`Open event from ${formatDateTime(event.timestamp)}`} onClick={(clickEvent) => { clickEvent.stopPropagation(); onSelect(event.id); }}><ChevronRight /></button></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="events-empty"><Search /><strong>No playback events found</strong><span>Adjust the search, users, provider, or history range.</span></div>
      )}
      <Pagination current={Math.min(page, totalPages)} totalPages={totalPages} onChange={onPage} />
    </section>
  );
}

export function App() {
  const [range, setRange] = useState("30d");
  const [customOpen, setCustomOpen] = useState(false);
  const [customStart, setCustomStart] = useState("2026-07-01");
  const [customEnd, setCustomEnd] = useState("2026-07-27");
  const [provider, setProvider] = useState("all");
  const [selectedUsers, setSelectedUsers] = useState(USERS.map((user) => user.id));
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState(EVENTS[0].id);
  const [detailOpen, setDetailOpen] = useState(true);
  const [page, setPage] = useState(1);
  const [mobileOpen, setMobileOpen] = useState(false);
  const providerRef = useRef(null);

  useEffect(() => {
    document.documentElement.dataset.theme = "dark";
  }, []);

  const latestEventDate = useMemo(() => new Date(Math.max(...EVENTS.map((event) => event.timestamp.getTime()))), []);
  const filteredEvents = useMemo(() => {
    const query = search.trim().toLowerCase();
    const selectedRange = RANGE_OPTIONS.find((option) => option.id === range);
    let start = null;
    let end = latestEventDate;
    if (selectedRange?.days) {
      start = new Date(latestEventDate);
      start.setDate(start.getDate() - (selectedRange.days - 1));
      start.setHours(0, 0, 0, 0);
    } else if (range === "custom") {
      start = new Date(`${customStart}T00:00:00`);
      end = new Date(`${customEnd}T23:59:59`);
    }
    return EVENTS
      .filter((event) => (!start || event.timestamp >= start) && event.timestamp <= end)
      .filter((event) => provider === "all" || event.provider === provider)
      .filter((event) => selectedUsers.includes(event.userId))
      .filter((event) => {
        if (!query) return true;
        const user = userFor(event);
        return [user.name, event.provider, event.status, event.client, event.device, formatDateTime(event.timestamp)]
          .some((value) => value.toLowerCase().includes(query));
      })
      .sort((a, b) => b.timestamp - a.timestamp);
  }, [customEnd, customStart, latestEventDate, provider, range, search, selectedUsers]);

  useEffect(() => {
    setPage(1);
  }, [range, customStart, customEnd, provider, selectedUsers, search]);

  useEffect(() => {
    if (filteredEvents.length && !filteredEvents.some((event) => event.id === selectedId)) {
      setSelectedId(filteredEvents[0].id);
    }
  }, [filteredEvents, selectedId]);

  const selectedEvent = detailOpen ? EVENTS.find((event) => event.id === selectedId) ?? null : null;

  const toggleUser = (id) => {
    setSelectedUsers((current) => current.includes(id) ? current.filter((userId) => userId !== id) : [...current, id]);
  };

  const selectEvent = (id) => {
    setSelectedId(id);
    setDetailOpen(true);
  };

  const applyCustomRange = (start, end) => {
    setCustomStart(start);
    setCustomEnd(end);
    setRange("custom");
    setCustomOpen(false);
  };

  const exportCsv = () => {
    const header = ["Timestamp", "User", "Provider", "Watched", "Completion", "Status", "Client"];
    const rows = filteredEvents.map((event) => [
      event.timestamp.toISOString(),
      userFor(event).name,
      event.provider,
      formatDuration(event.watchedSeconds),
      `${event.percent}%`,
      event.status,
      event.client,
    ]);
    const csv = [header, ...rows].map((row) => row.map((value) => `"${String(value).replaceAll('"', '""')}"`).join(",")).join("\n");
    const anchor = document.createElement("a");
    anchor.href = `data:text/csv;charset=utf-8,${encodeURIComponent(csv)}`;
    anchor.download = "medialyze-playback-events.csv";
    anchor.style.display = "none";
    document.body.appendChild(anchor);
    anchor.click();
    window.setTimeout(() => anchor.remove(), 0);
  };

  return (
    <>
      <div className="bg-shapes" aria-hidden="true" />
      <div className="media-app-shell">
        <div className="layout">
          <AppHeader />
          <div className="settings-layout file-detail-layout">
            <FileDetailSidebar mobileOpen={mobileOpen} onMobileToggle={() => setMobileOpen((current) => !current)} />
            <main className="settings-main-column file-detail-main-column">
              <section className="panel file-detail-active-panel streaming-prototype-panel">
                <div className="panel-header streaming-panel-header">
                  <div>
                    <p className="eyebrow">File detail</p>
                    <h2>Streaming</h2>
                    <p className="subtitle">Playback activity for <strong>Arrival.2016.mkv</strong></p>
                  </div>
                  <div className="file-context-badges">
                    <span className="badge"><Film />Movie</span>
                    <span className="badge"><Clock3 />01:39:58</span>
                  </div>
                </div>
                <div className={`streaming-workspace${selectedEvent ? " has-detail" : ""}`}>
                  <div className="streaming-main">
                    <section className="streaming-controls">
                      <div className="streaming-controls-top">
                        <RangeSelector
                          value={range}
                          onChange={(value) => {
                            setRange(value);
                            if (value !== "custom") setCustomOpen(false);
                          }}
                          customOpen={customOpen}
                          setCustomOpen={setCustomOpen}
                          customStart={customStart}
                          customEnd={customEnd}
                          onApplyCustom={applyCustomRange}
                        />
                        <label className="provider-filter" ref={providerRef}>
                          <span className="control-label">Provider</span>
                          <span className="select-shell">
                            <Server aria-hidden="true" />
                            <select value={provider} onChange={(event) => setProvider(event.target.value)}>
                              <option value="all">All providers</option>
                              {PROVIDERS.map((name) => <option key={name} value={name}>{name}</option>)}
                            </select>
                            <ChevronDown aria-hidden="true" />
                          </span>
                        </label>
                      </div>
                      <UserFilters selected={selectedUsers} onToggle={toggleUser} />
                    </section>
                    <section className="timeline-section">
                      <Timeline events={filteredEvents} selectedId={selectedId} onSelect={selectEvent} />
                    </section>
                    <EventTable
                      events={filteredEvents}
                      selectedId={selectedId}
                      onSelect={selectEvent}
                      search={search}
                      onSearch={setSearch}
                      onExport={exportCsv}
                      page={page}
                      onPage={setPage}
                    />
                  </div>
                  <EventDetail event={selectedEvent} onClose={() => setDetailOpen(false)} />
                </div>
              </section>
            </main>
          </div>
        </div>
      </div>
    </>
  );
}

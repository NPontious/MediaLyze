import { ImageIcon, Server } from "lucide-react";
import { type ReactNode, useMemo } from "react";

import { api, type JellyfinFileOverlay, type JellyfinItem, type JellyfinItemDetail } from "../lib/api";
import { formatBytes, formatDate, formatDuration } from "../lib/format";
import { PlaybackHistoryPanel, type PlaybackHistoryEntry } from "./PlaybackHistoryPanel";

type Translate = (key: string, options?: Record<string, unknown>) => string;
type UserData = JellyfinFileOverlay["user_data"];

export function JellyfinOverviewBadges({
  item,
  t,
  className = "",
}: {
  item: JellyfinItem;
  t: Translate;
  className?: string;
}): ReactNode {
  const hierarchy = [
    item.series_name,
    item.season_name || (item.parent_index_number !== null ? t("jellyfin.seasonNumber", { number: item.parent_index_number }) : null),
    item.index_number !== null ? t("jellyfin.episodeNumber", { number: item.index_number }) : null,
  ].filter(Boolean);

  return (
    <div className={`jellyfin-overview-badge-group ${className}`.trim()}>
      <span className="badge"><Server aria-hidden="true" />Jellyfin</span>
      <span className="badge">{item.item_type}</span>
      {hierarchy.map((entry) => <span className="badge" key={String(entry)}>{entry}</span>)}
    </div>
  );
}

export function JellyfinOverviewDetails({
  item,
  sizeBytes,
  durationSeconds,
  showTitle = true,
  showBadges = true,
  t,
}: {
  item: JellyfinItem;
  sizeBytes?: number | null;
  durationSeconds?: number | null;
  showTitle?: boolean;
  showBadges?: boolean;
  t: Translate;
}): ReactNode {
  const rows = [
    item.original_title && item.original_title !== item.title
      ? { key: "originalTitle", label: t("jellyfin.originalTitle"), value: item.original_title }
      : null,
    item.production_year !== null
      ? { key: "productionYear", label: t("jellyfin.productionYear"), value: String(item.production_year) }
      : null,
    item.premiere_date
      ? { key: "release", label: t("jellyfin.catalog.release"), value: formatDate(item.premiere_date) }
      : null,
    item.date_created
      ? { key: "added", label: t("jellyfin.catalog.added"), value: formatDate(item.date_created) }
      : null,
    durationSeconds !== undefined && durationSeconds !== null
      ? { key: "duration", label: t("jellyfin.catalog.duration"), value: formatDuration(durationSeconds) }
      : null,
    sizeBytes !== undefined && sizeBytes !== null
      ? { key: "size", label: t("jellyfin.catalog.size"), value: formatBytes(sizeBytes) }
      : null,
  ].filter((row): row is { key: string; label: string; value: string } => row !== null);

  return (
    <div className="jellyfin-overview-details">
      {showTitle ? (
        <div className="file-detail-title-row">
          <h3 className="file-detail-title">{item.title}</h3>
        </div>
      ) : null}
      {showBadges ? <JellyfinOverviewBadges item={item} t={t} /> : null}
      {rows.length ? (
        <div className="stream-tooltip-content stream-tooltip-content-panel format-details-content">
          {rows.map((row) => (
            <div className="stream-tooltip-row" key={row.key}>
              <div className="stream-tooltip-head format-details-row">
                <span className="format-details-label">{row.label}</span>
                <strong className="format-details-value">{row.value}</strong>
              </div>
            </div>
          ))}
        </div>
      ) : null}
      {item.overview ? <p className="jellyfin-overview">{item.overview}</p> : null}
      {Object.keys(item.provider_ids).length ? (
        <dl className="jellyfin-provider-list">
          {Object.entries(item.provider_ids).map(([provider, id]) => (
            <div key={provider}><dt>{provider}</dt><dd>{id}</dd></div>
          ))}
        </dl>
      ) : null}
    </div>
  );
}

export function JellyfinStreamingDetails({
  userData,
  durationSeconds,
}: {
  userData: UserData;
  durationSeconds?: number | null;
}): ReactNode {
  const entries = useMemo(
    () =>
      userData
        .filter((user): user is UserData[number] & { last_played_date: string } =>
          Boolean(user.last_played_date) && user.play_count > 0,
        )
        .map<PlaybackHistoryEntry>((user) => ({
          id: `jellyfin:${user.jellyfin_user_id}`,
          provider: "Jellyfin",
          userId: user.jellyfin_user_id,
          userName: user.user_name,
          playCount: user.play_count,
          completed: user.played,
          resumePositionSeconds: user.playback_position_ticks / 10_000_000,
          lastPlayedAt: user.last_played_date,
        })),
    [userData],
  );

  return (
    <div className="jellyfin-file-panel jellyfin-streaming-panel">
      <PlaybackHistoryPanel entries={entries} durationSeconds={durationSeconds} />
    </div>
  );
}

export function JellyfinCoverDetails({
  item,
  t,
}: {
  item: JellyfinItem | JellyfinItemDetail["item"];
  t: Translate;
}): ReactNode {
  if (!item.image_tags.Primary) return null;
  return (
    <figure className="file-detail-cover-preview jellyfin-cover-preview">
      <figcaption><Server aria-hidden="true" />{t("jellyfin.coverSource")}</figcaption>
      <img src={api.jellyfinImageUrl(item.id)} alt={t("jellyfin.coverAlt", { title: item.title })} />
    </figure>
  );
}

export function JellyfinCoverPlaceholder({ t }: { t: Translate }): ReactNode {
  return <div className="jellyfin-cover-empty"><ImageIcon aria-hidden="true" /><span>{t("jellyfin.noCover")}</span></div>;
}

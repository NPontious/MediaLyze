import { CheckCircle2, ImageIcon, Server } from "lucide-react";
import type { ReactNode } from "react";

import { api, type JellyfinFileOverlay, type JellyfinItem, type JellyfinItemDetail } from "../lib/api";
import { formatBytes, formatDate, formatDuration } from "../lib/format";

type Translate = (key: string, options?: Record<string, unknown>) => string;
type UserData = JellyfinFileOverlay["user_data"];

export function JellyfinOverviewDetails({
  item,
  sizeBytes,
  durationSeconds,
  showTitle = true,
  t,
}: {
  item: JellyfinItem;
  sizeBytes?: number | null;
  durationSeconds?: number | null;
  showTitle?: boolean;
  t: Translate;
}): ReactNode {
  const hierarchy = [
    item.series_name,
    item.season_name || (item.parent_index_number !== null ? t("jellyfin.seasonNumber", { number: item.parent_index_number }) : null),
    item.index_number !== null ? t("jellyfin.episodeNumber", { number: item.index_number }) : null,
  ].filter(Boolean);
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
      <div className="meta-tags">
        <span className="badge"><Server aria-hidden="true" />Jellyfin</span>
        <span className="badge">{item.item_type}</span>
        {hierarchy.map((entry) => <span className="badge" key={String(entry)}>{entry}</span>)}
      </div>
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
  matchMethod,
  onRejectMatch,
  t,
}: {
  userData: UserData;
  matchMethod?: string | null;
  onRejectMatch?: () => void;
  t: Translate;
}): ReactNode {
  return (
    <div className="jellyfin-file-panel jellyfin-streaming-panel">
      {matchMethod ? (
        <span className="jellyfin-status-badge status-matched">
          <CheckCircle2 aria-hidden="true" />{t("jellyfin.matchedBy", { method: matchMethod })}
        </span>
      ) : null}
      {userData.length ? (
        <div className="jellyfin-playback-list">
          {userData.map((user) => (
            <div className="jellyfin-playback-row" key={user.jellyfin_user_id}>
              <strong>{user.user_name}</strong>
              <span>{t("jellyfin.playCount", { count: user.play_count })}</span>
              <span>{user.played ? t("jellyfin.catalog.played") : t("jellyfin.catalog.unplayed")}</span>
              {user.playback_position_ticks > 0 ? (
                <span>{t("jellyfin.position", { duration: formatDuration(user.playback_position_ticks / 10_000_000) })}</span>
              ) : null}
              <span>{user.last_played_date ? formatDate(user.last_played_date) : t("jellyfin.neverPlayed")}</span>
            </div>
          ))}
        </div>
      ) : <div className="notice">{t("jellyfin.noPlaybackData")}</div>}
      {onRejectMatch ? (
        <button type="button" className="secondary small danger" onClick={onRejectMatch}>{t("jellyfin.wrongMatch")}</button>
      ) : null}
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

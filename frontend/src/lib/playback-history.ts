export type PlaybackHistoryEntry = {
  id: string;
  provider: string;
  userId: string;
  userName: string;
  playCount: number;
  completed: boolean;
  resumePositionSeconds: number;
  lastPlayedAt: string;
};

export type PlaybackDisplayEntry = PlaybackHistoryEntry & {
  eventCount: number;
  firstPlayedAt: string;
};

const FALLBACK_GROUPING_GAP_MS = 5 * 60 * 60 * 1000;

function groupingGapMs(durationSeconds?: number | null): number {
  return durationSeconds && Number.isFinite(durationSeconds) && durationSeconds > 0
    ? durationSeconds * 1000
    : FALLBACK_GROUPING_GAP_MS;
}

export function groupPlaybackEntries(
  entries: PlaybackHistoryEntry[],
  durationSeconds?: number | null,
): PlaybackDisplayEntry[] {
  const threshold = groupingGapMs(durationSeconds);
  const entriesByUser = new Map<string, PlaybackHistoryEntry[]>();

  entries.forEach((entry) => {
    const key = `${entry.provider}:${entry.userId}`;
    entriesByUser.set(key, [...(entriesByUser.get(key) ?? []), entry]);
  });

  const groups: PlaybackDisplayEntry[] = [];
  entriesByUser.forEach((userEntries, userKey) => {
    const sorted = userEntries
      .slice()
      .sort((left, right) => Date.parse(left.lastPlayedAt) - Date.parse(right.lastPlayedAt));
    let current: PlaybackHistoryEntry[] = [];

    const flush = () => {
      if (!current.length) return;
      const first = current[0];
      const latest = current[current.length - 1];
      groups.push({
        ...latest,
        id: current.length === 1
          ? latest.id
          : `group:${userKey}:${first.id}:${latest.id}`,
        playCount: current.reduce((total, entry) => total + entry.playCount, 0),
        completed: current.every((entry) => entry.completed),
        eventCount: current.length,
        firstPlayedAt: first.lastPlayedAt,
      });
      current = [];
    };

    sorted.forEach((entry) => {
      const previous = current[current.length - 1];
      if (previous && Date.parse(entry.lastPlayedAt) - Date.parse(previous.lastPlayedAt) >= threshold) {
        flush();
      }
      current.push(entry);
    });
    flush();
  });

  return groups.sort((left, right) => Date.parse(right.lastPlayedAt) - Date.parse(left.lastPlayedAt));
}

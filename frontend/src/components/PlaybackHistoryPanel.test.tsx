import "../i18n";

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { groupPlaybackEntries } from "../lib/playback-history";
import { JellyfinStreamingDetails } from "./JellyfinMetadataDetails";
import {
  PlaybackHistoryPanel,
  type PlaybackHistoryEntry,
} from "./PlaybackHistoryPanel";

const playbackData = [
  {
    jellyfin_user_id: "user-1",
    user_name: "Frederik",
    play_count: 3,
    played: false,
    playback_position_ticks: 1_200_000_000,
    last_played_date: "2026-07-27T20:41:13Z",
    is_favorite: false,
  },
  {
    jellyfin_user_id: "user-2",
    user_name: "Louise",
    play_count: 2,
    played: true,
    playback_position_ticks: 0,
    last_played_date: "2026-06-01T10:00:00Z",
    is_favorite: false,
  },
  {
    jellyfin_user_id: "user-3",
    user_name: "No timestamp",
    play_count: 0,
    played: false,
    playback_position_ticks: 0,
    last_played_date: null,
    is_favorite: false,
  },
];

describe("PlaybackHistoryPanel", () => {
  beforeEach(() => window.localStorage.clear());
  afterEach(cleanup);

  it("renders only Jellyfin fields that have a real playback timestamp", () => {
    render(<JellyfinStreamingDetails userData={playbackData} durationSeconds={7200} />);

    expect(screen.getAllByText("Frederik")).not.toHaveLength(0);
    expect(screen.getAllByText("Louise")).not.toHaveLength(0);
    expect(screen.queryByText("No timestamp")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Provider")).not.toBeInTheDocument();
    expect(screen.getByText(/core API does not provide a complete list/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Individual event timestamps are required for grouping" }),
    ).toBeDisabled();

    const table = screen.getByRole("table");
    expect(within(table).getByText("2m")).toBeInTheDocument();
  });

  it("filters latest-playback rows with the shared historic-data range control", () => {
    render(<JellyfinStreamingDetails userData={playbackData} durationSeconds={7200} />);

    const range = screen.getByRole("group", { name: "History range" });
    fireEvent.click(within(range).getByRole("button", { name: "7d" }));

    const table = screen.getByRole("table");
    expect(within(table).getByText("Frederik")).toBeInTheDocument();
    expect(within(table).queryByText("Louise")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Frederik✓" }));
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.getByText("No playback data matches the selected range and filters.")).toBeInTheDocument();
  });

  it("groups only nearby events from the same provider and user using the media runtime", () => {
    const entries: PlaybackHistoryEntry[] = [
      {
        id: "frederik-1",
        provider: "Jellyfin",
        userId: "user-1",
        userName: "Frederik",
        playCount: 1,
        completed: true,
        resumePositionSeconds: 0,
        lastPlayedAt: "2026-07-20T10:00:00Z",
      },
      {
        id: "frederik-2",
        provider: "Jellyfin",
        userId: "user-1",
        userName: "Frederik",
        playCount: 1,
        completed: true,
        resumePositionSeconds: 0,
        lastPlayedAt: "2026-07-20T11:30:00Z",
      },
      {
        id: "frederik-3",
        provider: "Jellyfin",
        userId: "user-1",
        userName: "Frederik",
        playCount: 1,
        completed: true,
        resumePositionSeconds: 0,
        lastPlayedAt: "2026-07-22T11:30:00Z",
      },
      {
        id: "louise-1",
        provider: "Jellyfin",
        userId: "user-2",
        userName: "Louise",
        playCount: 1,
        completed: false,
        resumePositionSeconds: 120,
        lastPlayedAt: "2026-07-20T10:30:00Z",
      },
    ];

    const grouped = groupPlaybackEntries(entries, 7200);

    expect(grouped).toHaveLength(3);
    expect(grouped.find((entry) => entry.id.startsWith("group:"))).toMatchObject({
      userId: "user-1",
      eventCount: 2,
      playCount: 2,
      firstPlayedAt: "2026-07-20T10:00:00Z",
      lastPlayedAt: "2026-07-20T11:30:00Z",
    });
  });

  it("uses a five-hour grouping fallback when the runtime is unavailable", () => {
    const makeEntry = (id: string, lastPlayedAt: string): PlaybackHistoryEntry => ({
      id,
      provider: "Jellyfin",
      userId: "user-1",
      userName: "Frederik",
      playCount: 1,
      completed: true,
      resumePositionSeconds: 0,
      lastPlayedAt,
    });
    const grouped = groupPlaybackEntries(
      [
        makeEntry("event-1", "2026-07-20T10:00:00Z"),
        makeEntry("event-2", "2026-07-20T14:00:00Z"),
        makeEntry("event-3", "2026-07-20T20:00:00Z"),
      ],
      null,
    );

    expect(grouped.map((entry) => entry.eventCount).sort()).toEqual([1, 2]);
  });

  it("switches between every event and nearby groups and renders the icon-free export action", () => {
    const entries: PlaybackHistoryEntry[] = [
      {
        id: "event-1",
        provider: "Jellyfin",
        userId: "user-1",
        userName: "Frederik",
        playCount: 1,
        completed: true,
        resumePositionSeconds: 0,
        lastPlayedAt: "2026-07-20T10:00:00Z",
      },
      {
        id: "event-2",
        provider: "Jellyfin",
        userId: "user-1",
        userName: "Frederik",
        playCount: 1,
        completed: true,
        resumePositionSeconds: 0,
        lastPlayedAt: "2026-07-20T11:00:00Z",
      },
      {
        id: "event-3",
        provider: "Jellyfin",
        userId: "user-1",
        userName: "Frederik",
        playCount: 1,
        completed: true,
        resumePositionSeconds: 0,
        lastPlayedAt: "2026-07-23T11:00:00Z",
      },
    ];
    const { container } = render(<PlaybackHistoryPanel entries={entries} durationSeconds={7200} />);

    expect(container.querySelectorAll(".playback-history-timeline-event")).toHaveLength(3);
    fireEvent.click(screen.getByRole("button", { name: "Group nearby playback events" }));
    expect(container.querySelectorAll(".playback-history-timeline-event")).toHaveLength(2);
    expect(container.querySelector(".playback-history-timeline-event.is-cluster")).toHaveAttribute(
      "data-event-count",
      "2",
    );

    const exportButton = screen.getByRole("button", { name: "Export" });
    expect(exportButton.querySelector("svg")).toBeNull();
  });
});

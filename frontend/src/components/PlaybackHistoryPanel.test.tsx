import "../i18n";

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { groupPlaybackEntries } from "../lib/playback-history";
import { ConnectorStreamingDetails, JellyfinStreamingDetails } from "./JellyfinMetadataDetails";
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
    const { container } = render(
      <JellyfinStreamingDetails
        userData={playbackData}
        individualPlaybackHistoryStartAt="2026-05-15T08:00:00Z"
        durationSeconds={7200}
      />,
    );

    expect(screen.getAllByText("Frederik")).not.toHaveLength(0);
    expect(screen.getAllByText("Louise")).not.toHaveLength(0);
    expect(screen.queryByText("No timestamp")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Provider")).not.toBeInTheDocument();
    expect(screen.getByText(/Jellyfin currently provides only the latest timestamp/)).toBeInTheDocument();
    expect(screen.getByText("Timestamped: 2 of 5 · Without an individual timestamp: 3")).toBeInTheDocument();
    const undatedRegion = screen.getByRole("region", {
      name: "Playbacks without a determinable time",
    });
    expect(within(undatedRegion).getByText("2 playbacks")).toBeInTheDocument();
    expect(within(undatedRegion).getByText("1 playback")).toBeInTheDocument();
    const groupedButton = screen.getByRole("button", { name: "Group nearby playback events" });
    expect(groupedButton).toBeEnabled();
    fireEvent.click(groupedButton);
    expect(groupedButton).toHaveAttribute("aria-pressed", "true");

    const rangeToggle = screen.getByRole("group", { name: "History range" });
    const displayToggle = screen.getByRole("group", { name: "Timeline display" });
    expect(displayToggle.closest(".playback-history-display-control")).not.toBeNull();
    expect(
      displayToggle.closest(".playback-history-display-control")?.previousElementSibling,
    ).toBe(rangeToggle.parentElement);
    expect(displayToggle).toHaveClass("library-history-range-toggle");
    expect(within(displayToggle).getAllByRole("button")[0]).toHaveClass("library-history-range-button");
    expect(screen.getByText("Playback stacking")).toBeInTheDocument();

    expect(screen.queryByText("Range start")).not.toBeInTheDocument();
    expect(screen.queryByText("Visible data")).not.toBeInTheDocument();
    expect(screen.queryByText("Range end")).not.toBeInTheDocument();
    const timeline = container.querySelector(".playback-history-timeline");
    expect(timeline?.children[0]).toHaveClass("playback-history-timeline-axis");
    expect(timeline?.children[1]).toHaveClass("playback-history-timeline-track");
    expect(container.querySelector(".playback-history-availability-boundary")).not.toBeNull();
    expect(screen.getByText(/Individual playbacks available from/)).toBeInTheDocument();

    const table = screen.getByRole("table");
    expect(within(table).getByText("2m")).toBeInTheDocument();
  });

  it("keeps colliding events from different connections and labels both sources", () => {
    const { container } = render(
      <ConnectorStreamingDetails
        durationSeconds={7200}
        sources={[
          {
            connection_id: 1,
            connection_name: "Living Room",
            provider: "jellyfin",
            connector_item_id: 10,
            user_data: [],
            playback_events: [{ remote_event_id: "event-1", remote_user_id: "user-1", user_name: "Alex", played_at: "2026-07-27T20:41:13Z" }],
            individual_playback_history_start_at: "2026-07-01T00:00:00Z",
          },
          {
            connection_id: 2,
            connection_name: "Archive",
            provider: "jellyfin",
            connector_item_id: 20,
            user_data: [],
            playback_events: [{ remote_event_id: "event-1", remote_user_id: "user-1", user_name: "Alex", played_at: "2026-07-27T20:41:13Z" }],
            individual_playback_history_start_at: "2026-07-10T00:00:00Z",
          },
        ]}
      />,
    );

    expect(container.querySelectorAll(".playback-history-timeline-event")).toHaveLength(2);
    expect(screen.getAllByText("Living Room")).not.toHaveLength(0);
    expect(screen.getAllByText("Archive")).not.toHaveLength(0);
    expect(screen.getByText("Timestamped: 2 of 2 · Without an individual timestamp: 0")).toBeInTheDocument();
  });

  it("keeps a single connector source compact without an origin column", () => {
    render(
      <ConnectorStreamingDetails
        sources={[{
          connection_id: 1,
          connection_name: "Living Room",
          provider: "jellyfin",
          connector_item_id: 10,
          user_data: [],
          playback_events: [{ remote_event_id: "event-1", remote_user_id: "user-1", user_name: "Alex", played_at: "2026-07-27T20:41:13Z" }],
          individual_playback_history_start_at: "2026-07-01T00:00:00Z",
        }]}
      />,
    );

    expect(within(screen.getByRole("table")).queryByRole("columnheader", { name: "Provider" })).not.toBeInTheDocument();
    expect(screen.queryByText("Living Room")).not.toBeInTheDocument();
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
    expect(
      screen.getByText("No timestamped playback matches the selected range and filters."),
    ).toBeInTheDocument();
  });

  it("groups only nearby events from the same provider and user within a quarter runtime", () => {
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
        lastPlayedAt: "2026-07-20T10:20:00Z",
      },
      {
        id: "frederik-3",
        provider: "Jellyfin",
        userId: "user-1",
        userName: "Frederik",
        playCount: 1,
        completed: true,
        resumePositionSeconds: 0,
        lastPlayedAt: "2026-07-20T11:00:00Z",
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
      lastPlayedAt: "2026-07-20T10:20:00Z",
    });
  });

  it("uses a ten-minute grouping fallback when the runtime is unavailable", () => {
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
        makeEntry("event-2", "2026-07-20T10:09:00Z"),
        makeEntry("event-3", "2026-07-20T10:20:00Z"),
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
        lastPlayedAt: "2026-07-20T10:20:00Z",
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

  it("uses every synchronized Jellyfin playback event and omits unavailable event fields", () => {
    const playbackEvents = [
      {
        jellyfin_activity_id: 101,
        jellyfin_user_id: "user-1",
        user_name: "Frederik",
        played_at: "2026-07-20T10:00:00Z",
      },
      {
        jellyfin_activity_id: 102,
        jellyfin_user_id: "user-1",
        user_name: "Frederik",
        played_at: "2026-07-20T10:20:00Z",
      },
      {
        jellyfin_activity_id: 103,
        jellyfin_user_id: "user-1",
        user_name: "Frederik",
        played_at: "2026-07-23T11:00:00Z",
      },
    ];
    const { container } = render(
      <JellyfinStreamingDetails
        userData={playbackData}
        playbackEvents={playbackEvents}
        individualPlaybackHistoryStartAt="2026-07-19T08:00:00Z"
        durationSeconds={7200}
      />,
    );

    expect(container.querySelectorAll(".playback-history-timeline-event")).toHaveLength(4);
    expect(container.querySelector(".playback-history-availability-boundary")).not.toBeNull();
    expect(screen.getByText(/Individual playbacks available from/)).toBeInTheDocument();
    expect(within(screen.getByRole("table")).getAllByText("1")).toHaveLength(4);
    expect(screen.getByText("Timestamped: 4 of 5 · Without an individual timestamp: 1")).toBeInTheDocument();
    expect(
      within(screen.getByRole("region", { name: "Playbacks without a determinable time" }))
        .getByText("Louise"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Playback state")).not.toBeInTheDocument();
    expect(screen.queryByText("Resume position")).not.toBeInTheDocument();
    expect(screen.getByText(/Individual playback starts imported/)).toBeInTheDocument();
  });

  it("preserves every aggregate play and every user when only some events have timestamps", () => {
    const userData = [
      {
        jellyfin_user_id: "user-1",
        user_name: "Alice",
        play_count: 20,
        played: true,
        playback_position_ticks: 0,
        last_played_date: "2026-07-26T20:00:00Z",
        is_favorite: false,
      },
      {
        jellyfin_user_id: "user-2",
        user_name: "Bob",
        play_count: 10,
        played: false,
        playback_position_ticks: 0,
        last_played_date: "2026-07-25T20:00:00Z",
        is_favorite: false,
      },
      {
        jellyfin_user_id: "user-3",
        user_name: "Cara",
        play_count: 8,
        played: false,
        playback_position_ticks: 0,
        last_played_date: "2026-07-24T20:00:00Z",
        is_favorite: false,
      },
      {
        jellyfin_user_id: "user-4",
        user_name: "Dani",
        play_count: 5,
        played: false,
        playback_position_ticks: 0,
        last_played_date: null,
        is_favorite: false,
      },
    ];
    const playbackEvents = [
      {
        jellyfin_activity_id: 201,
        jellyfin_user_id: "user-1",
        user_name: "Alice",
        played_at: "2026-07-26T20:00:00Z",
      },
      {
        jellyfin_activity_id: 202,
        jellyfin_user_id: "user-2",
        user_name: "Bob",
        played_at: "2026-07-25T20:00:00Z",
      },
    ];

    const { container } = render(
      <JellyfinStreamingDetails
        userData={userData}
        playbackEvents={playbackEvents}
        individualPlaybackHistoryStartAt="2026-07-20T08:00:00Z"
      />,
    );

    expect(container.querySelectorAll(".playback-history-timeline-event")).toHaveLength(3);
    expect(
      screen.getByText("Timestamped: 3 of 43 · Without an individual timestamp: 40"),
    ).toBeInTheDocument();
    const users = container.querySelector(".playback-history-user-list");
    expect(users).not.toBeNull();
    expect(within(users as HTMLElement).getByText("Alice")).toBeInTheDocument();
    expect(within(users as HTMLElement).getByText("Bob")).toBeInTheDocument();
    expect(within(users as HTMLElement).getByText("Cara")).toBeInTheDocument();
    expect(within(users as HTMLElement).getByText("Dani")).toBeInTheDocument();

    const undated = screen.getByRole("region", {
      name: "Playbacks without a determinable time",
    });
    expect(within(undated).getByText("19 playbacks")).toBeInTheDocument();
    expect(within(undated).getByText("9 playbacks")).toBeInTheDocument();
    expect(within(undated).getByText("7 playbacks")).toBeInTheDocument();
    expect(within(undated).getByText("5 playbacks")).toBeInTheDocument();
  });

  it("shows every unstacked row without pagination when the feature flag is enabled", () => {
    const entries = Array.from({ length: 10 }, (_, index): PlaybackHistoryEntry => ({
      id: `event-${index}`,
      provider: "Jellyfin",
      userId: "user-1",
      userName: "Frederik",
      playCount: 1,
      lastPlayedAt: `2026-07-${String(index + 1).padStart(2, "0")}T10:00:00Z`,
    }));

    render(
      <PlaybackHistoryPanel
        entries={entries}
        durationSeconds={7200}
        showAllWhenUnstacked
      />,
    );

    expect(screen.getAllByRole("row")).toHaveLength(11);
    expect(screen.queryByText(/Page 1 of/)).not.toBeInTheDocument();
  });
});

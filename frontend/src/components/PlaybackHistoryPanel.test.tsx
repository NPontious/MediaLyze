import "../i18n";

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { JellyfinStreamingDetails } from "./JellyfinMetadataDetails";

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
});

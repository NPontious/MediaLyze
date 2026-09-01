import "../i18n";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router";

import { api, type LibrarySummary, type TranscodeJob } from "../lib/api";
import { TranscodeHistorySettingsPanel } from "./TranscodeHistorySettingsPanel";

const library = { id: 9, name: "Movies" } as LibrarySummary;
const historyJob = {
  id: 22,
  group_id: 2,
  library_id: 9,
  source_file_id: 1,
  result_file_id: 2,
  status: "completed",
  profile: "storage",
  plan_version: 1,
  plan: { version: 1, profile: "storage" },
  ffmpeg_arguments: ["ffmpeg", "-map", "0:0"],
  ffmpeg_command: "ffmpeg -map 0:0 output.mkv",
  warnings: [],
  source_path_snapshot: "C:/media/Movie.mkv",
  output_path_snapshot: "C:/media/Movie HEVC.mkv",
  output_relative_path: "Movie HEVC.mkv",
  progress_percent: 100,
  processed_seconds: 100,
  speed: "2x",
  eta_seconds: 0,
  error: null,
  created_at: "2026-09-01T10:00:00Z",
  updated_at: "2026-09-01T10:10:00Z",
  started_at: "2026-09-01T10:00:01Z",
  finished_at: "2026-09-01T10:10:00Z",
} as unknown as TranscodeJob;

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("TranscodeHistorySettingsPanel", () => {
  it("filters persisted jobs by library, status, and time and exposes full diagnostics", async () => {
    const history = vi.spyOn(api, "transcodeJobs").mockResolvedValue({ items: [historyJob], total: 1 });
    render(<MemoryRouter><TranscodeHistorySettingsPanel libraries={[library]} /></MemoryRouter>);

    expect(await screen.findByText("Movie HEVC.mkv")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Media library"), { target: { value: "9" } });
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "completed" } });
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-08-01T12:00" } });
    fireEvent.click(screen.getByText("Movie HEVC.mkv"));

    await waitFor(() => expect(history).toHaveBeenLastCalledWith(expect.objectContaining({
      libraryId: 9,
      status: "completed",
      startedAfter: expect.stringContaining("2026-08-01T"),
      limit: 200,
    })));
    expect(screen.getByText(historyJob.ffmpeg_command)).toBeInTheDocument();
    expect(screen.getByText(historyJob.source_path_snapshot)).toBeInTheDocument();
    expect(screen.getByText(historyJob.output_path_snapshot)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open source file" })).toHaveAttribute("href", "/files/1");
    expect(screen.getByRole("link", { name: "Open variant" })).toHaveAttribute("href", "/files/2");
    expect(screen.getByRole("link", { name: "Open metadata comparison" })).toHaveAttribute("href", "/files/compare?left=1&right=2");
  });
});

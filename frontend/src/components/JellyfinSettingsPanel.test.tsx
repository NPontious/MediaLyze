import "../i18n";

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api, type JellyfinConnection, type JellyfinSyncStatus } from "../lib/api";
import { JellyfinSettingsPanel } from "./JellyfinSettingsPanel";

const CONNECTION: JellyfinConnection = {
  base_url: "http://jellyfin:8096",
  enabled: true,
  sync_interval_minutes: 60,
  api_key_configured: true,
  server_name: "Jellyfin",
  server_version: "10.11",
  last_status: "running",
  last_error: null,
  last_sync_started_at: "2026-07-15T10:00:00Z",
  last_sync_finished_at: null,
  last_successful_sync_at: null,
  next_scheduled_sync_at: null,
};

const STATUS: JellyfinSyncStatus = {
  ...CONNECTION,
  sync_job_id: 41,
  sync_job_status: "running",
  sync_trigger_source: "manual",
  sync_job_active: true,
  sync_job_error: null,
  sync_summary: {},
  sync_phase: "items",
  sync_phase_detail: "Alice",
  sync_current: 250,
  sync_total: 1000,
  item_count: 900,
  matched_item_count: 700,
  unmatched_item_count: 200,
  library_count: 2,
  user_count: 1,
};

function idleSyncStatus(connection: JellyfinConnection): JellyfinSyncStatus {
  return {
    ...STATUS,
    ...connection,
    sync_job_id: 40,
    sync_job_status: "completed",
    sync_trigger_source: "scheduled",
    sync_job_active: false,
    sync_summary: { status: "success" },
    sync_phase: null,
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("JellyfinSettingsPanel", () => {
  it("shows only connection, synchronization, and playback-user settings", async () => {
    vi.spyOn(api, "jellyfinSyncStatus").mockResolvedValue(STATUS);
    vi.spyOn(api, "jellyfinUsers").mockResolvedValue([]);

    render(<JellyfinSettingsPanel />);

    expect(await screen.findByText("Fetching Jellyfin items")).toBeInTheDocument();
    expect(screen.getByText("Alice")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Connection" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Playback users" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Path mappings" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Jellyfin libraries" })).not.toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "25");
  });

  it("queues synchronization, reports completion, and refreshes the external catalog", async () => {
    const idleConnection = { ...CONNECTION, last_status: "success" };
    const idleStatus = idleSyncStatus(idleConnection);
    const queuedStatus = { ...idleStatus, sync_job_id: 42, sync_job_status: "queued", sync_job_active: true, sync_summary: {} } as JellyfinSyncStatus;
    const completedStatus = { ...idleStatus, sync_job_id: 42, sync_job_status: "completed", sync_job_active: false, sync_summary: { status: "success", items_synced: 120, libraries_synced: 3 } } as JellyfinSyncStatus;
    vi.spyOn(api, "jellyfinSyncStatus")
      .mockResolvedValueOnce(idleStatus)
      .mockResolvedValueOnce(queuedStatus)
      .mockResolvedValue(completedStatus);
    vi.spyOn(api, "jellyfinUsers").mockResolvedValue([]);
    vi.spyOn(api, "syncJellyfin").mockResolvedValue({ job_id: 42, status: "queued", trigger_source: "manual", accepted: true });
    const onCatalogChanged = vi.fn();

    render(<JellyfinSettingsPanel onCatalogChanged={onCatalogChanged} />);
    fireEvent.click(await screen.findByRole("button", { name: "Sync now" }));

    expect(await screen.findByText("Synchronized 120 items from 3 libraries.", {}, { timeout: 2500 })).toBeInTheDocument();
    expect(onCatalogChanged).toHaveBeenCalledTimes(1);
  });

  it("requests cancellation for a running synchronization", async () => {
    vi.spyOn(api, "jellyfinSyncStatus").mockResolvedValue(STATUS);
    vi.spyOn(api, "jellyfinUsers").mockResolvedValue([]);
    const cancelSync = vi.spyOn(api, "cancelJellyfinSync").mockResolvedValue({ job_id: 41, status: "running", cancellation_requested: true });

    render(<JellyfinSettingsPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "Cancel sync" }));

    await waitFor(() => expect(cancelSync).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/cancellation requested/i)).toBeInTheDocument();
  });

  it("uses zero minutes to disable only scheduled synchronization", async () => {
    const idleConnection = { ...CONNECTION, last_status: "success" };
    vi.spyOn(api, "jellyfinSyncStatus").mockResolvedValue(idleSyncStatus(idleConnection));
    vi.spyOn(api, "jellyfinUsers").mockResolvedValue([]);
    const updateConnection = vi.spyOn(api, "updateJellyfinConnection").mockResolvedValue({ ...idleConnection, sync_interval_minutes: 0 });

    render(<JellyfinSettingsPanel />);
    const interval = await screen.findByRole("spinbutton", { name: "Sync interval (minutes)" });
    fireEvent.change(interval, { target: { value: "0" } });

    await waitFor(() => expect(updateConnection).toHaveBeenCalledWith(expect.objectContaining({ sync_interval_minutes: 0 })), { timeout: 2000 });
    expect(await screen.findByText("Saved automatically")).toBeInTheDocument();
  });

  it("keeps connection and user settings visible when a connection action fails", async () => {
    const idleConnection = { ...CONNECTION, last_status: "success" };
    vi.spyOn(api, "jellyfinSyncStatus").mockResolvedValue(idleSyncStatus(idleConnection));
    vi.spyOn(api, "jellyfinUsers").mockResolvedValue([]);
    vi.spyOn(api, "testJellyfinConnection").mockResolvedValue({ ok: false, server_name: null, server_version: null, error: "Invalid Jellyfin URL" });

    render(<JellyfinSettingsPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "Test connection" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid Jellyfin URL");
    expect(screen.getByRole("heading", { name: "Playback users" })).toBeInTheDocument();
  });

  it("keeps activation explicit and can remove the connection", async () => {
    const idleConnection = { ...CONNECTION, last_status: "success" };
    vi.spyOn(api, "jellyfinSyncStatus").mockResolvedValue(idleSyncStatus(idleConnection));
    vi.spyOn(api, "jellyfinUsers").mockResolvedValue([]);
    const updateConnection = vi.spyOn(api, "updateJellyfinConnection").mockResolvedValue({ ...idleConnection, enabled: false });
    const disconnect = vi.spyOn(api, "disconnectJellyfin").mockResolvedValue(undefined);
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<JellyfinSettingsPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "Disable integration" }));
    await waitFor(() => expect(updateConnection).toHaveBeenCalledWith({ enabled: false }));

    fireEvent.click(screen.getByRole("button", { name: "Remove connection" }));
    await waitFor(() => expect(disconnect).toHaveBeenCalledTimes(1));
    await act(async () => undefined);
    expect(await screen.findByText(/connection and its cached data were removed/i)).toBeInTheDocument();
  });
});

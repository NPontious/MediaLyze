import "../i18n";

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, type JellyfinConnection, type JellyfinLibrary, type JellyfinMatchRecomputeStatus, type JellyfinSyncStatus, type LibrarySummary } from "../lib/api";
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

const IDLE_MATCH_RECOMPUTE: JellyfinMatchRecomputeStatus = {
  status: "idle",
  active: false,
  rerun_pending: false,
  last_error: null,
};

beforeEach(() => {
  vi.spyOn(api, "jellyfinMatchRecomputeStatus").mockResolvedValue(IDLE_MATCH_RECOMPUTE);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("JellyfinSettingsPanel", () => {
  it("shows live synchronization progress and prevents a second sync", async () => {
    vi.spyOn(api, "jellyfinConnection").mockResolvedValue(CONNECTION);
    vi.spyOn(api, "jellyfinSyncStatus").mockResolvedValue(STATUS);
    vi.spyOn(api, "jellyfinUsers").mockResolvedValue([]);
    vi.spyOn(api, "jellyfinPathMappings").mockResolvedValue([]);
    vi.spyOn(api, "jellyfinLibraries").mockResolvedValue([]);

    render(<JellyfinSettingsPanel />);

    expect(await screen.findByText("Fetching Jellyfin items")).toBeInTheDocument();
    expect(screen.getByText("Alice")).toBeInTheDocument();
    expect(screen.getByText("250 of 1000 items")).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "25");
    await waitFor(() => expect(screen.getByRole("button", { name: /sync now/i })).toBeDisabled());
  });

  it("queues a manual synchronization and reports completion through polling", async () => {
    const idleConnection = { ...CONNECTION, last_status: "success" };
    const idleStatus = idleSyncStatus(idleConnection);
    const queuedStatus: JellyfinSyncStatus = {
      ...idleStatus,
      sync_job_id: 42,
      sync_job_status: "queued",
      sync_trigger_source: "manual",
      sync_job_active: true,
      sync_summary: {},
    };
    const completedStatus: JellyfinSyncStatus = {
      ...idleStatus,
      sync_job_id: 42,
      sync_job_status: "completed",
      sync_trigger_source: "manual",
      sync_job_active: false,
      sync_summary: { status: "success", items_synced: 120, libraries_synced: 3 },
    };
    vi.spyOn(api, "jellyfinConnection").mockResolvedValue(idleConnection);
    vi.spyOn(api, "jellyfinSyncStatus")
      .mockResolvedValueOnce(idleStatus)
      .mockResolvedValueOnce(queuedStatus)
      .mockResolvedValue(completedStatus);
    vi.spyOn(api, "jellyfinUsers").mockResolvedValue([]);
    vi.spyOn(api, "jellyfinPathMappings").mockResolvedValue([]);
    vi.spyOn(api, "jellyfinLibraries").mockResolvedValue([]);
    const startSync = vi.spyOn(api, "syncJellyfin").mockResolvedValue({
      job_id: 42,
      status: "queued",
      trigger_source: "manual",
      accepted: true,
    });

    render(<JellyfinSettingsPanel />);

    fireEvent.click(await screen.findByRole("button", { name: "Sync now" }));
    await waitFor(() => expect(startSync).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("Synchronization running")).toBeInTheDocument();
    expect(await screen.findByText("Synchronized 120 items from 3 libraries.", {}, { timeout: 2500 })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "Sync now" })).toBeEnabled());
  });

  it("requests cancellation for a running synchronization", async () => {
    vi.spyOn(api, "jellyfinConnection").mockResolvedValue(CONNECTION);
    vi.spyOn(api, "jellyfinSyncStatus").mockResolvedValue(STATUS);
    vi.spyOn(api, "jellyfinUsers").mockResolvedValue([]);
    vi.spyOn(api, "jellyfinPathMappings").mockResolvedValue([]);
    vi.spyOn(api, "jellyfinLibraries").mockResolvedValue([]);
    const cancelSync = vi.spyOn(api, "cancelJellyfinSync").mockResolvedValue({
      job_id: 41,
      status: "running",
      cancellation_requested: true,
    });

    render(<JellyfinSettingsPanel />);

    fireEvent.click(await screen.findByRole("button", { name: "Cancel sync" }));

    await waitFor(() => expect(cancelSync).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/cancellation requested/i)).toBeInTheDocument();
  });

  it("creates a path mapping directly from an unmapped Jellyfin library", async () => {
    const idleConnection = { ...CONNECTION, last_status: "success" };
    const idleStatus = idleSyncStatus(idleConnection);
    vi.spyOn(api, "jellyfinConnection").mockResolvedValue(idleConnection);
    vi.spyOn(api, "jellyfinSyncStatus").mockResolvedValue(idleStatus);
    vi.spyOn(api, "jellyfinUsers").mockResolvedValue([]);
    vi.spyOn(api, "jellyfinPathMappings").mockResolvedValue([]);
    vi.spyOn(api, "jellyfinLibraries").mockResolvedValue([{
      id: 7,
      name: "Anime",
      collection_type: "tvshows",
      locations: ["/Mediathek/Anime"],
      mapped_locations: [],
      mapped_status: "path_unmapped",
      linked_library_id: null,
      linked_library_name: null,
      can_create_medialyze_library: false,
      data_scope: "jellyfin_only",
      item_count: 0,
      last_synced_at: "2026-07-15T10:00:00Z",
    }]);
    const createMapping = vi.spyOn(api, "createJellyfinPathMapping").mockResolvedValue({
      id: 12,
      jellyfin_path_prefix: "/Mediathek/Anime",
      medialyze_path_prefix: "/media/anime",
      enabled: true,
    });
    render(<JellyfinSettingsPanel />);

    const target = await screen.findByRole("textbox", { name: "MediaLyze path for Anime" });
    expect(target.closest(".jellyfin-library-card")).toHaveClass("is-mappable");
    expect(screen.getAllByText("/Mediathek/Anime")).toHaveLength(1);
    expect(screen.queryByText("Add a path mapping so MediaLyze can resolve this Jellyfin location.")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save library path mapping" })).not.toBeInTheDocument();
    fireEvent.change(target, { target: { value: "/media/anime" } });

    await waitFor(() => expect(createMapping).toHaveBeenCalledWith({
      jellyfin_path_prefix: "/Mediathek/Anime",
      medialyze_path_prefix: "/media/anime",
      enabled: true,
    }), { timeout: 2000 });
  });

  it("automatically updates an existing library path mapping", async () => {
    const idleConnection = { ...CONNECTION, last_status: "success" };
    const idleStatus = idleSyncStatus(idleConnection);
    vi.spyOn(api, "jellyfinConnection").mockResolvedValue(idleConnection);
    vi.spyOn(api, "jellyfinSyncStatus").mockResolvedValue(idleStatus);
    vi.spyOn(api, "jellyfinUsers").mockResolvedValue([]);
    vi.spyOn(api, "jellyfinPathMappings").mockResolvedValue([{
      id: 12,
      jellyfin_path_prefix: "/Mediathek/Anime",
      medialyze_path_prefix: "/media/anime",
      enabled: true,
    }]);
    vi.spyOn(api, "jellyfinLibraries").mockResolvedValue([{
      id: 7,
      name: "Anime",
      collection_type: "tvshows",
      locations: ["/Mediathek/Anime"],
      mapped_locations: ["/media/anime"],
      mapped_status: "path_not_accessible",
      linked_library_id: null,
      linked_library_name: null,
      can_create_medialyze_library: false,
      data_scope: "jellyfin_only",
      item_count: 0,
      last_synced_at: "2026-07-15T10:00:00Z",
    }]);
    const updateMapping = vi.spyOn(api, "updateJellyfinPathMapping").mockResolvedValue({
      id: 12,
      jellyfin_path_prefix: "/Mediathek/Anime",
      medialyze_path_prefix: "/mnt/anime",
      enabled: true,
    });

    render(<JellyfinSettingsPanel />);

    const target = await screen.findByRole("textbox", { name: "MediaLyze path for Anime" });
    expect(target).toHaveValue("/media/anime");
    fireEvent.change(target, { target: { value: "/mnt/anime" } });

    await waitFor(() => expect(updateMapping).toHaveBeenCalledWith(12, {
      medialyze_path_prefix: "/mnt/anime",
      enabled: true,
    }), { timeout: 2000 });
  });

  it("uses zero minutes to disable only scheduled synchronization", async () => {
    const idleConnection = { ...CONNECTION, last_status: "success" };
    const idleStatus = idleSyncStatus(idleConnection);
    vi.spyOn(api, "jellyfinConnection").mockResolvedValue(idleConnection);
    vi.spyOn(api, "jellyfinSyncStatus").mockResolvedValue(idleStatus);
    vi.spyOn(api, "jellyfinUsers").mockResolvedValue([]);
    vi.spyOn(api, "jellyfinPathMappings").mockResolvedValue([]);
    vi.spyOn(api, "jellyfinLibraries").mockResolvedValue([]);
    const updateConnection = vi.spyOn(api, "updateJellyfinConnection").mockResolvedValue({
      ...idleConnection,
      sync_interval_minutes: 0,
    });

    render(<JellyfinSettingsPanel />);

    const interval = await screen.findByRole("spinbutton", { name: "Sync interval (minutes)" });
    expect(screen.queryByRole("checkbox", { name: /scheduled synchronization/i })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Explain scheduled synchronization interval" }));
    expect(await screen.findByText(/enter 0 to disable scheduled synchronization/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
    fireEvent.change(interval, { target: { value: "0" } });

    await waitFor(() => expect(updateConnection).toHaveBeenCalledWith(expect.objectContaining({
      enabled: true,
      sync_interval_minutes: 0,
    })), { timeout: 2000 });
    expect(await screen.findByText("Saved automatically")).toBeInTheDocument();
  });

  it("does not repeatedly retry a failed automatic save without another edit", async () => {
    const idleConnection = { ...CONNECTION, last_status: "success" };
    const idleStatus = idleSyncStatus(idleConnection);
    vi.spyOn(api, "jellyfinConnection").mockResolvedValue(idleConnection);
    vi.spyOn(api, "jellyfinSyncStatus").mockResolvedValue(idleStatus);
    vi.spyOn(api, "jellyfinUsers").mockResolvedValue([]);
    vi.spyOn(api, "jellyfinPathMappings").mockResolvedValue([]);
    vi.spyOn(api, "jellyfinLibraries").mockResolvedValue([]);
    const updateConnection = vi.spyOn(api, "updateJellyfinConnection").mockRejectedValue(new Error("Connection unavailable"));

    render(<JellyfinSettingsPanel />);

    const interval = await screen.findByRole("spinbutton", { name: "Sync interval (minutes)" });
    fireEvent.change(interval, { target: { value: "30" } });

    expect(await screen.findByText("Connection unavailable", {}, { timeout: 2000 })).toBeInTheDocument();
    await new Promise((resolve) => window.setTimeout(resolve, 800));
    expect(updateConnection).toHaveBeenCalledTimes(1);

    fireEvent.change(interval, { target: { value: "31" } });
    await waitFor(() => expect(updateConnection).toHaveBeenCalledTimes(2), { timeout: 2000 });
  });

  it("keeps all settings visible when a connection action fails", async () => {
    const idleConnection = { ...CONNECTION, last_status: "success" };
    const idleStatus = idleSyncStatus(idleConnection);
    vi.spyOn(api, "jellyfinConnection").mockResolvedValue(idleConnection);
    vi.spyOn(api, "jellyfinSyncStatus").mockResolvedValue(idleStatus);
    vi.spyOn(api, "jellyfinUsers").mockResolvedValue([]);
    vi.spyOn(api, "jellyfinPathMappings").mockResolvedValue([]);
    vi.spyOn(api, "jellyfinLibraries").mockResolvedValue([]);
    vi.spyOn(api, "testJellyfinConnection").mockResolvedValue({
      ok: false,
      server_name: null,
      server_version: null,
      error: "Invalid Jellyfin URL",
    });

    render(<JellyfinSettingsPanel />);

    fireEvent.click(await screen.findByRole("button", { name: "Test connection" }));
    const inlineError = await screen.findByRole("alert");
    expect(inlineError).toHaveTextContent("Invalid Jellyfin URL");
    expect(inlineError.closest(".jellyfin-settings-section")).toHaveAttribute("aria-labelledby", "jellyfin-connection-heading");
    expect(screen.getByRole("heading", { name: "Playback users" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Path mappings" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Jellyfin libraries" })).toBeInTheDocument();
  });

  it("applies a path mapping before its match recalculation finishes", async () => {
    const idleConnection = { ...CONNECTION, last_status: "success" };
    const idleStatus = idleSyncStatus(idleConnection);
    const connectionRequest = vi.spyOn(api, "jellyfinConnection").mockResolvedValue(idleConnection);
    vi.spyOn(api, "jellyfinSyncStatus").mockResolvedValue(idleStatus);
    vi.spyOn(api, "jellyfinUsers").mockResolvedValue([]);
    vi.spyOn(api, "jellyfinPathMappings").mockResolvedValue([]);
    vi.spyOn(api, "jellyfinLibraries").mockResolvedValue([]);
    let resolveBackground: ((status: JellyfinMatchRecomputeStatus) => void) | undefined;
    vi.mocked(api.jellyfinMatchRecomputeStatus)
      .mockResolvedValueOnce(IDLE_MATCH_RECOMPUTE)
      .mockResolvedValueOnce({ ...IDLE_MATCH_RECOMPUTE, status: "running", active: true })
      .mockImplementationOnce(() => new Promise((resolve) => {
        resolveBackground = resolve;
      }));
    let resolveMapping: ((mapping: { id: number; jellyfin_path_prefix: string; medialyze_path_prefix: string; enabled: boolean }) => void) | undefined;
    vi.spyOn(api, "createJellyfinPathMapping").mockImplementation(() => new Promise((resolve) => {
      resolveMapping = resolve;
    }));

    render(<JellyfinSettingsPanel />);

    fireEvent.change(await screen.findByRole("textbox", { name: "Jellyfin path" }), { target: { value: "/jellyfin/movies" } });
    fireEvent.change(screen.getByRole("textbox", { name: "MediaLyze path" }), { target: { value: "/media/movies" } });
    fireEvent.click(screen.getByRole("button", { name: "Add path mapping" }));

    expect(await screen.findByText("Updating path mapping")).toBeInTheDocument();
    expect(screen.queryByText(/recalculating asset matches/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add path mapping" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Test connection" })).toBeEnabled();

    await act(async () => resolveMapping?.({
      id: 12,
      jellyfin_path_prefix: "/jellyfin/movies",
      medialyze_path_prefix: "/media/movies",
      enabled: true,
    }));
    await waitFor(() => expect(screen.queryByText("Updating path mapping")).not.toBeInTheDocument());
    expect(await screen.findByText("Updating Jellyfin data in the background")).toBeInTheDocument();
    expect(screen.getByText(/changes are already saved/i)).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Enable this path mapping" })).toBeEnabled();
    expect(connectionRequest).toHaveBeenCalledTimes(1);

    await waitFor(() => expect(resolveBackground).toBeDefined());
    await act(async () => resolveBackground?.({ ...IDLE_MATCH_RECOMPUTE, status: "success" }));
    await waitFor(() => expect(screen.queryByText("Updating Jellyfin data in the background")).not.toBeInTheDocument());
  });

  it("links a Jellyfin library to an existing MediaLyze library", async () => {
    const idleConnection = { ...CONNECTION, last_status: "success" };
    const idleStatus = idleSyncStatus(idleConnection);
    const remote = {
      id: 7,
      name: "Anime",
      collection_type: "tvshows",
      locations: ["/Mediathek/Anime"],
      mapped_locations: ["/media/anime"],
      mapped_status: "accessible",
      linked_library_id: null,
      linked_library_name: null,
      link_method: null,
      can_create_medialyze_library: true,
      data_scope: "jellyfin_only" as const,
      item_count: 12,
      last_synced_at: "2026-07-15T10:00:00Z",
    };
    vi.spyOn(api, "jellyfinConnection").mockResolvedValue(idleConnection);
    vi.spyOn(api, "jellyfinSyncStatus").mockResolvedValue(idleStatus);
    vi.spyOn(api, "jellyfinUsers").mockResolvedValue([]);
    vi.spyOn(api, "jellyfinPathMappings").mockResolvedValue([]);
    vi.spyOn(api, "jellyfinLibraries").mockResolvedValueOnce([remote]).mockResolvedValueOnce([{
      ...remote,
      linked_library_id: 3,
      linked_library_name: "Anime local",
      link_method: "manual",
      mapped_status: "linked",
      data_scope: "linked",
    }]);
    const linkedRemote: JellyfinLibrary = {
      ...remote,
      linked_library_id: 3,
      linked_library_name: "Anime local",
      link_method: "manual",
      mapped_status: "linked",
      data_scope: "linked",
    };
    let resolveLink: ((library: typeof linkedRemote) => void) | undefined;
    const updateLink = vi.spyOn(api, "updateJellyfinLibraryLink").mockImplementation(() => new Promise((resolve) => {
      resolveLink = resolve;
    }));
    vi.mocked(api.jellyfinMatchRecomputeStatus)
      .mockResolvedValueOnce(IDLE_MATCH_RECOMPUTE)
      .mockResolvedValue({ ...IDLE_MATCH_RECOMPUTE, status: "running", active: true });

    render(<JellyfinSettingsPanel medialyzeLibraries={[{ id: 3, name: "Anime local" } as LibrarySummary]} />);

    const select = await screen.findByRole("combobox", { name: "Associated MediaLyze library" });
    fireEvent.change(select, { target: { value: "3" } });
    await waitFor(() => expect(updateLink).toHaveBeenCalledWith(7, 3));
    expect(select).toHaveValue("3");
    expect(screen.queryByRole("button", { name: "Add as MediaLyze library" })).not.toBeInTheDocument();

    await act(async () => resolveLink?.(linkedRemote));
    await waitFor(() => expect(screen.getByText("Updating Jellyfin data in the background")).toBeInTheDocument());
  });
});

import "../i18n";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api, type JellyfinConnection, type JellyfinSyncStatus, type LibrarySummary } from "../lib/api";
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

  it("creates a path mapping directly from an unmapped Jellyfin library", async () => {
    const idleConnection = { ...CONNECTION, last_status: "success" };
    const idleStatus = { ...STATUS, ...idleConnection, sync_phase: null };
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
    const idleStatus = { ...STATUS, ...idleConnection, sync_phase: null };
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
    const idleStatus = { ...STATUS, ...idleConnection, sync_phase: null };
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
    const idleStatus = { ...STATUS, ...idleConnection, sync_phase: null };
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

  it("links a Jellyfin library to an existing MediaLyze library", async () => {
    const idleConnection = { ...CONNECTION, last_status: "success" };
    const idleStatus = { ...STATUS, ...idleConnection, sync_phase: null };
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
    const updateLink = vi.spyOn(api, "updateJellyfinLibraryLink").mockResolvedValue({
      ...remote,
      linked_library_id: 3,
      linked_library_name: "Anime local",
      link_method: "manual",
      mapped_status: "linked",
      data_scope: "linked",
    });

    render(<JellyfinSettingsPanel medialyzeLibraries={[{ id: 3, name: "Anime local" } as LibrarySummary]} />);

    const select = await screen.findByRole("combobox", { name: "Associated MediaLyze library" });
    fireEvent.change(select, { target: { value: "3" } });
    await waitFor(() => expect(updateLink).toHaveBeenCalledWith(7, 3));
    await waitFor(() => expect(select).toHaveValue("3"));
    expect(screen.queryByRole("button", { name: "Add as MediaLyze library" })).not.toBeInTheDocument();
  });
});

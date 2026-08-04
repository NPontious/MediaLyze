import "../i18n";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  api,
  type ConnectorConnection,
  type ConnectorLibrary,
  type LibrarySummary,
} from "../lib/api";
import { ConnectorSettingsPanel } from "./ConnectorSettingsPanel";

vi.mock("./JellyfinSettingsPanel", () => ({
  JellyfinSettingsPanel: () => <div>Legacy Jellyfin compatibility</div>,
}));

const CONNECTION: ConnectorConnection = {
  id: 7,
  provider: "jellyfin",
  name: "Living Room",
  base_url: "http://jellyfin.local",
  config: {},
  capabilities: { images: true },
  enabled: true,
  sync_interval_minutes: 60,
  server_name: "Jellyfin",
  server_version: "10.11",
  last_status: "success",
  last_error: null,
  last_sync_started_at: null,
  last_sync_finished_at: null,
  last_successful_sync_at: null,
  has_secret: true,
  created_at: "2026-08-04T00:00:00Z",
  updated_at: "2026-08-04T00:00:00Z",
};

const REMOTE_LIBRARY: ConnectorLibrary = {
  id: 11,
  connection_id: CONNECTION.id,
  remote_id: "movies",
  name: "Jellyfin Movies",
  media_type: "movies",
  provider_payload: {},
  last_synced_at: "2026-08-04T00:00:00Z",
  locations: [{
    id: 19,
    connector_library_id: 11,
    remote_path: "/srv/media/movies",
    normalized_path: "/srv/media/movies",
  }],
  linked_library_ids: [],
};

const LOCAL_LIBRARY = {
  id: 3,
  name: "Movies",
  roots: [{ id: 5, path: "/media/movies", display_name: "Primary", path_key: "/media/movies" }],
} as LibrarySummary;

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function mockCatalog() {
  vi.spyOn(api, "connectors").mockResolvedValue([CONNECTION]);
  vi.spyOn(api, "connectorProviderDescriptors").mockResolvedValue([{
    provider: "jellyfin",
    configuration_fields: [
      { key: "base_url", input_type: "url", required: true, secret: false },
      { key: "secret", input_type: "password", required: true, secret: true },
    ],
    optional_capabilities: ["images"],
  }]);
  vi.spyOn(api, "libraries").mockResolvedValue([LOCAL_LIBRARY]);
  vi.spyOn(api, "connectorLibraries").mockResolvedValue([REMOTE_LIBRARY]);
  vi.spyOn(api, "connectorBindings").mockResolvedValue([]);
  vi.spyOn(api, "connectorItems").mockResolvedValue({ total: 0, offset: 0, limit: 50, items: [] });
  vi.spyOn(api, "connectorItemStatusSummary").mockResolvedValue({});
  vi.spyOn(api, "connectorSyncStatus").mockResolvedValue(null);
}

describe("ConnectorSettingsPanel", () => {
  it("renders multiple-connection controls and the normal location-to-root mapping", async () => {
    mockCatalog();

    render(<ConnectorSettingsPanel />);

    expect(await screen.findByRole("heading", { name: "Living Room" })).toBeInTheDocument();
    expect(screen.getAllByText("Jellyfin Movies")).toHaveLength(2);
    expect(screen.getByText("/srv/media/movies")).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Movies · Primary" })).toBeInTheDocument();
    expect(screen.queryByText("Source prefix")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("checkbox", { name: "Advanced mode" }));
    expect(screen.getByText("Source prefix")).toBeInTheDocument();
    expect(screen.getByText("Target subpath")).toBeInTheDocument();
    expect(screen.getByText("Case mode")).toBeInTheDocument();
  });

  it("submits the selected root as one atomic binding batch", async () => {
    mockCatalog();
    const updateBindings = vi.spyOn(api, "updateConnectorBindings").mockResolvedValue([]);

    render(<ConnectorSettingsPanel />);
    const rootSelect = await screen.findByRole("combobox", { name: "" });
    fireEvent.change(rootSelect, { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save mappings" }));

    await waitFor(() => expect(updateBindings).toHaveBeenCalledWith(CONNECTION.id, [
      expect.objectContaining({
        location_id: REMOTE_LIBRARY.locations[0].id,
        library_root_id: 5,
        source_prefix: "/srv/media/movies",
      }),
    ]));
  });

  it("persists explicit many-to-many library links", async () => {
    mockCatalog();
    const updateLinks = vi.spyOn(api, "updateConnectorLibraryLinks").mockResolvedValue([
      { ...REMOTE_LIBRARY, linked_library_ids: [LOCAL_LIBRARY.id] },
    ]);

    render(<ConnectorSettingsPanel />);
    const linkSelect = await screen.findByRole("listbox", { name: "Jellyfin Movies" });
    fireEvent.change(linkSelect, { target: { value: String(LOCAL_LIBRARY.id) } });
    fireEvent.click(screen.getByRole("button", { name: "Save library links" }));

    await waitFor(() => expect(updateLinks).toHaveBeenCalledWith(CONNECTION.id, [{
      connector_library_id: REMOTE_LIBRARY.id,
      library_ids: [LOCAL_LIBRARY.id],
    }]));
  });

  it("offers durable ignore and automatic-match recovery for diagnostics", async () => {
    mockCatalog();
    vi.mocked(api.connectorItems).mockResolvedValue({
      total: 1,
      offset: 0,
      limit: 50,
      items: [{
        id: 41,
        connection_id: CONNECTION.id,
        connector_library_id: REMOTE_LIBRARY.id,
        remote_id: "missing",
        item_type: "Movie",
        remote_path: "/srv/media/movies/Missing.mkv",
        title: "Missing",
        size_bytes: null,
        duration_seconds: null,
        match_status: "no_local_file",
        mismatch_reason: "no_local_file",
        suggested_media_file_id: null,
        last_synced_at: null,
      }],
    });
    vi.mocked(api.connectorItemStatusSummary).mockResolvedValue({ no_local_file: 1 });
    const ignore = vi.spyOn(api, "ignoreConnectorItem").mockResolvedValue();

    render(<ConnectorSettingsPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "Ignore" }));

    await waitFor(() => expect(ignore).toHaveBeenCalledWith(CONNECTION.id, 41));
  });
});

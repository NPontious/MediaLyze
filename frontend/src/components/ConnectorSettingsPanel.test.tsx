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
  vi.spyOn(api, "connectorProviders").mockResolvedValue(["jellyfin"]);
  vi.spyOn(api, "libraries").mockResolvedValue([LOCAL_LIBRARY]);
  vi.spyOn(api, "connectorLibraries").mockResolvedValue([REMOTE_LIBRARY]);
  vi.spyOn(api, "connectorBindings").mockResolvedValue([]);
}

describe("ConnectorSettingsPanel", () => {
  it("renders multiple-connection controls and the normal location-to-root mapping", async () => {
    mockCatalog();

    render(<ConnectorSettingsPanel />);

    expect(await screen.findByRole("heading", { name: "Living Room" })).toBeInTheDocument();
    expect(screen.getByText("Jellyfin Movies")).toBeInTheDocument();
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
});

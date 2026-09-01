import "../i18n";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { VideoWipeCompare } from "./VideoWipeCompare";

afterEach(cleanup);

describe("VideoWipeCompare", () => {
  it("keeps seek, volume, and the accessible wipe control synchronized", () => {
    const { container } = render(
      <VideoWipeCompare
        first={{ src: "/api/files/1/media", label: "Original" }}
        second={{ src: "/api/files/2/media", label: "Variant" }}
      />,
    );
    const [first, second] = Array.from(container.querySelectorAll("video"));
    Object.defineProperty(first, "duration", { configurable: true, value: 120 });
    Object.defineProperty(second, "duration", { configurable: true, value: 121 });
    fireEvent.loadedMetadata(first);
    fireEvent.loadedMetadata(second);

    fireEvent.change(screen.getByRole("slider", { name: "Seek both videos" }), { target: { value: "42" } });
    expect(first.currentTime).toBe(42);
    expect(second.currentTime).toBe(42);

    fireEvent.change(screen.getByRole("slider", { name: "Volume for both videos" }), { target: { value: "0.35" } });
    expect(first.volume).toBe(0.35);
    expect(second.volume).toBe(0.35);

    fireEvent.change(screen.getByRole("slider", { name: "Visible share of the second video" }), { target: { value: "72" } });
    expect(container.querySelector(".video-wipe-second")).toHaveStyle({ clipPath: "inset(0 28% 0 0)" });
    expect(screen.getByText(/different durations/i)).toBeInTheDocument();
  });

  it("shows the browser playback fallback when either video fails", () => {
    const { container } = render(
      <VideoWipeCompare
        first={{ src: "/api/files/1/media", label: "Original" }}
        second={{ src: "/api/files/2/media", label: "Variant" }}
      />,
    );
    fireEvent.error(container.querySelectorAll("video")[1]);
    expect(screen.getByText(/cannot be played by this browser/i)).toBeInTheDocument();
  });
});

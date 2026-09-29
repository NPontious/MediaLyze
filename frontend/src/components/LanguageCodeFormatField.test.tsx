import "../i18n";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LanguageCodeFormatField } from "./LanguageCodeFormatField";

afterEach(() => cleanup());

describe("LanguageCodeFormatField", () => {
  it("shows filename formats and the compact compatibility matrix", async () => {
    render(<LanguageCodeFormatField value="iso_639_1" onChange={vi.fn()} />);
    const select = screen.getByRole("combobox", { name: "Language code format" });
    expect(select.querySelectorAll("option")).toHaveLength(5);
    fireEvent.focus(screen.getByRole("button", { name: "Explain language code formats" }));
    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip.querySelectorAll(".transcode-matrix-tooltip-row")).toHaveLength(6);
    expect(tooltip).toHaveTextContent("BCP 47");
    expect(tooltip).toHaveTextContent("Jellyfin");
    expect(tooltip).toHaveTextContent("Plex");
  });

  it("limits stream formats to the target muxer", () => {
    const { rerender } = render(<LanguageCodeFormatField container="mp4" value="container_default" onChange={vi.fn()} />);
    const select = screen.getByRole("combobox", { name: "Language code format" });
    expect(Array.from(select.querySelectorAll("option"), (option) => option.value)).toEqual(["container_default", "iso_639_2_t"]);
    rerender(<LanguageCodeFormatField container="mkv" value="container_default" onChange={vi.fn()} />);
    expect(Array.from(select.querySelectorAll("option"), (option) => option.value)).toEqual(["container_default", "iso_639_2", "iso_639_2_region"]);
  });
});

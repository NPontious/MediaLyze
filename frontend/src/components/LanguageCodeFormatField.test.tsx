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

  it("offers container default and a fallback explanation for source-container presets", async () => {
    render(<LanguageCodeFormatField container="source" value="container_default" onChange={vi.fn()} />);
    const select = screen.getByRole("combobox");
    expect(select).toHaveValue("container_default");
    expect(Array.from(select.querySelectorAll("option"), (option) => option.value)).toEqual(["container_default", "iso_639_2", "iso_639_2_region", "iso_639_2_t"]);
    fireEvent.focus(screen.getByRole("button", { name: "Explain language code formats" }));
    expect(await screen.findByRole("tooltip")).toHaveTextContent("If the target container does not support the selected format, Container default is used.");
  });

  it("shows mixed legacy settings without changing them until a shared format is selected", () => {
    const onChange = vi.fn();
    render(<LanguageCodeFormatField container="mkv" value="mixed" onChange={onChange} />);
    const select = screen.getByRole("combobox");
    expect(select).toHaveValue("mixed");
    expect(screen.getByRole("option", { name: "Mixed existing stream formats" })).toBeDisabled();
    expect(onChange).not.toHaveBeenCalled();
    fireEvent.change(select, { target: { value: "container_default" } });
    expect(onChange).toHaveBeenCalledWith("container_default");
  });
});

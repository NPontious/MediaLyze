import { describe, expect, it } from "vitest";

import type { TranscodeFormattingPreset, TranscodePlan } from "./api";
import { applyFormattingPreset, matchingFormattingPresetId } from "./transcode-formatting-presets";

describe("formatting presets", () => {
  it("applies legacy disabled presets through the current explicit token controls", () => {
    const plan = {
      profile: "expert",
      filename_format_enabled: false,
      folder_format_enabled: false,
      include_subtitle_languages: true,
    } as TranscodePlan;
    const base = {
      id: 1,
      definition: {
        enabled: false,
        template: "{sourceName} [{subtitleLanguages}]",
        source_name_explicit: true,
        metadata_separator: ", ",
        cleanup_preset: "none",
        cleanup_regex: null,
        include_subtitle_languages: true,
        language_code_format: "iso_639_2",
      },
    } as TranscodeFormattingPreset;

    const filenamePreset = { ...base, kind: "filename" } as TranscodeFormattingPreset;
    const withFilename = applyFormattingPreset(plan, filenamePreset);
    expect(withFilename).toMatchObject({ filename_format_enabled: true, include_subtitle_languages: false });
    expect(matchingFormattingPresetId(withFilename, [filenamePreset], "filename", filenamePreset.id)).toBe(filenamePreset.id);

    const folderPreset = { ...base, kind: "folder" } as TranscodeFormattingPreset;
    expect(applyFormattingPreset(plan, folderPreset).folder_format_enabled).toBe(true);
  });
});

export const FILENAME_METADATA_TOKENS = [
  { token: "sourceName", labelKey: "sourceName" },
  { token: "movieTitle", labelKey: "movieTitle" },
  { token: "releaseYear", labelKey: "releaseYear" },
  { token: "resolution", labelKey: "resolution" },
  { token: "resolutionCategory", labelKey: "resolutionCategory" },
  { token: "dynRange", labelKey: "dynRange" },
  { token: "codec", labelKey: "codec" },
  { token: "audioLanguages", labelKey: "audioLanguages" },
  { token: "audioCodecs", labelKey: "audioCodecs" },
  { token: "audioProfiles", labelKey: "audioProfiles" },
  { token: "audioChannels", labelKey: "audioChannels" },
  { token: "frameRate", labelKey: "frameRate" },
  { token: "bitDepth", labelKey: "bitDepth" },
  { token: "subtitleLanguages", labelKey: "subtitleLanguages" },
  { token: "subtitleFormats", labelKey: "subtitleFormats" },
  { token: "seriesName", labelKey: "seriesName" },
  { token: "seasonNumber", labelKey: "seasonNumber" },
  { token: "episodeNumber", labelKey: "episodeNumber" },
  { token: "episodeTitle", labelKey: "episodeTitle" },
  { token: "contentCategory", labelKey: "contentCategory" },
  { token: "container", labelKey: "container" },
  { token: "videoBitrate", labelKey: "videoBitrate" },
  { token: "folderName", labelKey: "folderName" },
] as const;

export type FilenameMetadataToken = typeof FILENAME_METADATA_TOKENS[number]["token"];
export type FilenameMetadataTokenEntry = typeof FILENAME_METADATA_TOKENS[number];

export const FILENAME_METADATA_TOKEN_PATTERN = new RegExp(
  `\\{(${FILENAME_METADATA_TOKENS.map(({ token }) => token).join("|")})\\}`,
  "g",
);

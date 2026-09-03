import type { AppSettings } from "./api";

export type PatternRecognitionSettings = NonNullable<AppSettings["pattern_recognition"]>;

export const DEFAULT_SHOW_SEASON_PATTERN_INPUTS = {
  recognition_mode: "folder_depth" as const,
  series_folder_depth: 1,
  season_folder_depth: 2,
  series_folder_regexes: [String.raw`^(?P<title>.+?)(?:\s+\((?P<year>\d{4})\))?(?:\s+\[[^\]]+\])?$`],
  season_folder_regexes: [String.raw`^(?:Season|Staffel)\s*(?P<season>\d{1,3})(?:\s+\([^)]*\))?(?:\s+\[[^\]]+\])*$`],
};

const DEFAULT_BONUS_FOLDER_NAMES = [
  "behind the scenes",
  "deleted scenes",
  "interviews",
  "scenes",
  "samples",
  "shorts",
  "featurettes",
  "clips",
  "other",
  "extras",
  "trailers",
  "theme-music",
  "backdrops",
  "Specials",
  "Season 00",
];

export const DEFAULT_DUPLICATE_FILENAME_SUFFIX_REGEXES = [
  String.raw`(?:\s+\(\d{4}\)|\s+\[[^\]]*\])+\s*$`,
];
export const DEFAULT_DUPLICATE_DURATION_TOLERANCE_SECONDS = 10;
export const DUPLICATE_DURATION_TOLERANCE_MIN = 0;
export const DUPLICATE_DURATION_TOLERANCE_MAX = 300;

export function defaultBonusFolderPatternInputs(): string[] {
  return DEFAULT_BONUS_FOLDER_NAMES.flatMap((name) => [`${name}/`, `${name}/*`, `*/${name}/`, `*/${name}/*`]);
}

export function defaultPatternRecognitionSettings(): PatternRecognitionSettings {
  const defaultFolderPatterns = defaultBonusFolderPatternInputs();
  return {
    analyze_bonus_content: true,
    duplicate_matching: {
      duration_tolerance_seconds: DEFAULT_DUPLICATE_DURATION_TOLERANCE_SECONDS,
      user_filename_suffix_regexes: [],
      default_filename_suffix_regexes: DEFAULT_DUPLICATE_FILENAME_SUFFIX_REGEXES,
      effective_filename_suffix_regexes: DEFAULT_DUPLICATE_FILENAME_SUFFIX_REGEXES,
    },
    show_season_patterns: DEFAULT_SHOW_SEASON_PATTERN_INPUTS,
    bonus_content: {
      user_folder_patterns: [],
      default_folder_patterns: defaultFolderPatterns,
      effective_folder_patterns: defaultFolderPatterns,
      user_file_patterns: [],
      default_file_patterns: [],
      effective_file_patterns: [],
    },
  };
}

import languageRegistry from "./language-registry.json";

const ISO_639_2_TO_1: Record<string, string> = {
  ara: "ar", bul: "bg", cat: "ca", ces: "cs", cze: "cs", dan: "da",
  deu: "de", ger: "de", ell: "el", gre: "el", eng: "en", spa: "es",
  est: "et", fas: "fa", per: "fa", fin: "fi", fra: "fr", fre: "fr",
  heb: "he", hin: "hi", hrv: "hr", hun: "hu", ind: "id", ice: "is",
  isl: "is", ita: "it", jpn: "ja", kor: "ko", lit: "lt", lav: "lv",
  msa: "ms", may: "ms", nld: "nl", dut: "nl", nob: "no", nno: "no",
  nor: "no", pol: "pl", por: "pt", pob: "pt", ron: "ro", rum: "ro",
  rus: "ru", slk: "sk", slo: "sk", slv: "sl", srp: "sr", swe: "sv",
  tha: "th", tur: "tr", ukr: "uk", vie: "vi", zho: "zh", chi: "zh",
};

const SPECIAL_LANGUAGE_NAMES: Record<string, Record<string, string>> = {
  und: { en: "Undetermined", de: "Unbestimmt", es: "Indeterminado", uk: "Невизначена" },
  mul: { en: "Multiple languages", de: "Mehrere Sprachen", es: "Varios idiomas", uk: "Кілька мов" },
  zxx: { en: "No linguistic content", de: "Kein Sprachinhalt", es: "Sin contenido lingüístico", uk: "Без мовного вмісту" },
};

export const COMMON_LANGUAGE_TAGS = [
  "und", "en", "de", "es", "fr", "it", "pt", "nl", "pl", "cs", "sk", "hu",
  "ro", "da", "sv", "no", "fi", "is", "el", "uk", "ru", "bg", "tr", "ar", "fa",
  "he", "hi", "ja", "ko", "zh", "vi", "id", "ms", "ca", "et", "lt", "lv", "hr",
  "sr", "sl", "th",
] as const;

function normalizeSubtag(subtag: string): string {
  if (subtag.length === 4 && /^[A-Za-z]+$/.test(subtag)) {
    return subtag[0].toUpperCase() + subtag.slice(1).toLowerCase();
  }
  if ((subtag.length === 2 && /^[A-Za-z]+$/.test(subtag)) || /^\d{3}$/.test(subtag)) {
    return subtag.toUpperCase();
  }
  return subtag.toLowerCase();
}

/** Normalize ISO 639 aliases and BCP 47 casing without discarding regions. */
export function normalizeLanguageTag(value: string | null | undefined): string {
  if (!value) return "";
  const rawParts = value.trim().replace(/_/g, "-").split("-");
  if (!rawParts.length || rawParts.some((part) => !part)) return "";
  const parts = [...rawParts];
  const primaryRaw = parts.shift()?.toLowerCase() ?? "";
  const primary = (languageRegistry.aliases as Record<string, string>)[primaryRaw]
    ?? (languageRegistry.preferred as Record<string, string>)[primaryRaw]
    ?? ISO_639_2_TO_1[primaryRaw]
    ?? primaryRaw;
  if (primary !== "i" && primary !== "x" && (!/^[a-z]{2,3}$/.test(primary))) return "";
  let extensionMode = false;
  let regionSeen = false;
  const normalizedParts = parts.map((part) => {
    if (extensionMode) return part.toLowerCase();
    if (part.length === 1 && /^[A-Za-z0-9]$/.test(part)) {
      extensionMode = true;
      return part.toLowerCase();
    }
    if (part.length === 4 && /^[A-Za-z]+$/.test(part)) {
      return part[0].toUpperCase() + part.slice(1).toLowerCase();
    }
    if (!regionSeen && ((part.length === 2 && /^[A-Za-z]+$/.test(part)) || /^\d{3}$/.test(part))) {
      regionSeen = true;
      return part.toUpperCase();
    }
    return normalizeSubtag(part);
  });
  return [primary, ...normalizedParts].join("-");
}

function languageDisplayTag(tag: string): string {
  const [primary, ...rest] = tag.split("-");
  return `${ISO_639_2_TO_1[primary] ?? primary}${rest.length ? `-${rest.join("-")}` : ""}`;
}

/** Filename language code conventions supported by the transcoding plan. */
export type FilenameLanguageCodeFormat = "iso_639_1" | "iso_639_2" | "iso_639_2_t" | "iso_639_3" | "bcp_47";
export type StreamLanguageCodeFormat = "container_default" | "iso_639_2" | "iso_639_2_region" | "iso_639_2_t";

export const FILENAME_LANGUAGE_CODE_FORMATS: FilenameLanguageCodeFormat[] = ["iso_639_1", "iso_639_2", "iso_639_2_t", "iso_639_3", "bcp_47"];

export function streamLanguageCodeFormats(container: "source" | "mkv" | "mp4" | "webm"): StreamLanguageCodeFormat[] {
  return container === "mp4"
    ? ["container_default", "iso_639_2_t"]
    : container === "source"
      ? ["container_default", "iso_639_2", "iso_639_2_region", "iso_639_2_t"]
      : ["container_default", "iso_639_2", "iso_639_2_region"];
}

export function sharedStreamLanguageCodeFormat(settings: {
  video_language_code_format?: StreamLanguageCodeFormat;
  audio_language_code_format?: StreamLanguageCodeFormat;
  subtitle_language_code_format?: StreamLanguageCodeFormat;
}): StreamLanguageCodeFormat | "mixed" {
  const formats = [settings.video_language_code_format, settings.audio_language_code_format, settings.subtitle_language_code_format]
    .map((format) => format ?? "container_default");
  return formats.every((format) => format === formats[0]) ? formats[0] : "mixed";
}

// ISO 639-2/B is the bibliographic/media convention used for three-letter
// filename codes (for example ger/eng rather than deu/eng). Unknown primary
// codes are kept unchanged so custom or future connector values remain visible.
const ISO_639_1_TO_2_B: Record<string, string> = {
  ar: "ara", bg: "bul", ca: "cat", cs: "cze", da: "dan", de: "ger", el: "gre",
  en: "eng", es: "spa", et: "est", fa: "per", fi: "fin", fr: "fre", he: "heb",
  hi: "hin", hr: "hrv", hu: "hun", id: "ind", is: "ice", it: "ita", ja: "jpn",
  ko: "kor", lt: "lit", lv: "lav", ms: "may", nl: "dut", no: "nor", pl: "pol",
  pt: "por", ro: "rum", ru: "rus", sk: "slo", sl: "slv", sr: "srp", sv: "swe",
  th: "tha", tr: "tur", uk: "ukr", vi: "vie", zh: "chi", und: "und", mul: "mul", zxx: "zxx",
};

/** Format a language tag for a filename while retaining regional subtags. */
export function formatFilenameLanguageCode(
  value: string | null | undefined,
  format: FilenameLanguageCodeFormat = "iso_639_1",
): string {
  const tag = normalizeLanguageTag(value);
  if (!tag) return "";
  if (format === "bcp_47") return tag;
  const [primary, ...rest] = tag.split("-");
  const map = format === "iso_639_1" ? languageRegistry.to_1
    : format === "iso_639_2" ? languageRegistry.to_2_b : languageRegistry.to_2_t;
  return [(map as Record<string, string>)[primary] ?? (format === "iso_639_2" ? ISO_639_1_TO_2_B[primary] : undefined) ?? primary, ...rest].join("-");
}

/** Return a localized language name without an appended code. */
export function formatLanguageName(value: string | null | undefined, locale = "en"): string {
  const tag = normalizeLanguageTag(value) || "und";
  const displayTag = languageDisplayTag(tag);
  const localeBase = locale.split("-")[0].toLowerCase();
  let name: string | undefined = SPECIAL_LANGUAGE_NAMES[tag]?.[localeBase];
  if (!name) {
    try {
      name = new Intl.DisplayNames([locale], { type: "language" }).of(displayTag) ?? undefined;
    } catch {
      name = undefined;
    }
  }
  if (!name || name.toLowerCase() === displayTag.toLowerCase()) {
    name = tag === "und" ? "Undetermined" : tag === "mul" ? "Multiple languages" : tag === "zxx" ? "No linguistic content" : displayTag;
  }
  return name;
}

/** Return a localized language name while retaining the exact normalized code. */
export function formatLanguageLabel(value: string | null | undefined, locale = "en"): string {
  const tag = normalizeLanguageTag(value) || "und";
  return `${formatLanguageName(tag, locale)} (${tag})`;
}

/** Build stable, localized select options from common and source-observed tags. */
export function languageOptions(
  observed: Array<string | null | undefined> = [],
  locale = "en",
): string[] {
  const tags = new Set<string>([
    ...COMMON_LANGUAGE_TAGS,
    ...observed.map(normalizeLanguageTag).filter(Boolean),
  ]);
  return [...tags].sort((left, right) => {
    if (left === "und") return -1;
    if (right === "und") return 1;
    return new Intl.Collator(locale, { sensitivity: "base" }).compare(
      formatLanguageLabel(left, locale),
      formatLanguageLabel(right, locale),
    );
  });
}

from __future__ import annotations

import re
import json
from collections import defaultdict
from pathlib import Path


LANGUAGE_ALIASES = {
    "und": "und",
    "mul": "mul",
    "zxx": "zxx",
    "ar": "ar",
    "ara": "ar",
    "arabic": "ar",
    "bg": "bg",
    "bul": "bg",
    "bulgarian": "bg",
    "ca": "ca",
    "cat": "ca",
    "catalan": "ca",
    "cs": "cs",
    "ces": "cs",
    "cze": "cs",
    "czech": "cs",
    "da": "da",
    "dan": "da",
    "danish": "da",
    "de": "de",
    "deu": "de",
    "ger": "de",
    "german": "de",
    "deutsch": "de",
    "el": "el",
    "ell": "el",
    "gre": "el",
    "greek": "el",
    "en": "en",
    "eng": "en",
    "english": "en",
    "es": "es",
    "spa": "es",
    "spanish": "es",
    "et": "et",
    "est": "et",
    "estonian": "et",
    "fa": "fa",
    "fas": "fa",
    "per": "fa",
    "persian": "fa",
    "fi": "fi",
    "fin": "fi",
    "finnish": "fi",
    "fr": "fr",
    "fra": "fr",
    "fre": "fr",
    "french": "fr",
    "he": "he",
    "heb": "he",
    "hebrew": "he",
    "hi": "hi",
    "hin": "hi",
    "hindi": "hi",
    "hr": "hr",
    "hrv": "hr",
    "croatian": "hr",
    "hu": "hu",
    "hun": "hu",
    "hungarian": "hu",
    "id": "id",
    "ind": "id",
    "indonesian": "id",
    "is": "is",
    "ice": "is",
    "isl": "is",
    "icelandic": "is",
    "it": "it",
    "ita": "it",
    "italian": "it",
    "ja": "ja",
    "jpn": "ja",
    "japanese": "ja",
    "ko": "ko",
    "kor": "ko",
    "korean": "ko",
    "lt": "lt",
    "lit": "lt",
    "lithuanian": "lt",
    "lv": "lv",
    "lav": "lv",
    "latvian": "lv",
    "ms": "ms",
    "may": "ms",
    "msa": "ms",
    "malay": "ms",
    "nl": "nl",
    "dut": "nl",
    "nld": "nl",
    "dutch": "nl",
    "no": "no",
    "nob": "no",
    "nno": "no",
    "nor": "no",
    "norwegian": "no",
    "pl": "pl",
    "pol": "pl",
    "polish": "pl",
    "pt": "pt",
    "pob": "pt",
    "por": "pt",
    "portuguese": "pt",
    "ro": "ro",
    "ron": "ro",
    "rum": "ro",
    "romanian": "ro",
    "ru": "ru",
    "rus": "ru",
    "russian": "ru",
    "sk": "sk",
    "slk": "sk",
    "slo": "sk",
    "slovak": "sk",
    "sl": "sl",
    "slv": "sl",
    "slovenian": "sl",
    "sr": "sr",
    "srp": "sr",
    "serbian": "sr",
    "sv": "sv",
    "swe": "sv",
    "swedish": "sv",
    "th": "th",
    "tha": "th",
    "thai": "th",
    "tr": "tr",
    "tur": "tr",
    "turkish": "tr",
    "uk": "uk",
    "ukr": "uk",
    "ukrainian": "uk",
    "vi": "vi",
    "vie": "vi",
    "vietnamese": "vi",
    "zh": "zh",
    "chi": "zh",
    "zho": "zh",
    "chinese": "zh",
}

_LANGUAGE_TAG_RE = re.compile(r"^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$")
_REGISTRY = json.loads(Path(__file__).with_name("language_registry.json").read_text(encoding="utf-8"))
_KNOWN_LANGUAGES = set(_REGISTRY["languages"])

# ISO 639-2/B is the bibliographic/media convention used for filename codes.
ISO_639_1_TO_2_B = {
    "ar": "ara", "bg": "bul", "ca": "cat", "cs": "cze", "da": "dan", "de": "ger", "el": "gre",
    "en": "eng", "es": "spa", "et": "est", "fa": "per", "fi": "fin", "fr": "fre", "he": "heb",
    "hi": "hin", "hr": "hrv", "hu": "hun", "id": "ind", "is": "ice", "it": "ita", "ja": "jpn",
    "ko": "kor", "lt": "lit", "lv": "lav", "ms": "may", "nl": "dut", "no": "nor", "pl": "pol",
    "pt": "por", "ro": "rum", "ru": "rus", "sk": "slo", "sl": "slv", "sr": "srp", "sv": "swe",
    "th": "tha", "tr": "tur", "uk": "ukr", "vi": "vie", "zh": "chi", "und": "und", "mul": "mul", "zxx": "zxx",
}


def _known_language_alias(value: str) -> str | None:
    candidate = value.strip().lower()
    if not candidate:
        return None

    direct = _REGISTRY["aliases"].get(candidate) or _REGISTRY["preferred"].get(candidate) or LANGUAGE_ALIASES.get(candidate)
    if direct:
        return direct

    for separator in ("-", "_"):
        if separator in candidate:
            base = candidate.split(separator, 1)[0]
            mapped = _REGISTRY["aliases"].get(base) or _REGISTRY["preferred"].get(base) or LANGUAGE_ALIASES.get(base)
            if mapped:
                return mapped

    return None


def normalize_language_code(value: str | None) -> str | None:
    if value is None:
        return None

    candidate = value.strip().lower()
    if not candidate:
        return None

    known_tag = recognized_language_tag(candidate)
    return _known_language_alias(candidate) or (known_tag.split("-", 1)[0] if known_tag else candidate)


def normalize_language_tag(value: str | None) -> str | None:
    """Normalize a user-facing language tag while retaining BCP 47 extensions.

    ffprobe commonly reports ISO 639-2/B or legacy aliases (``deu``, ``ger``),
    while a transcode plan may intentionally select a regional BCP 47 tag such
    as ``de-DE``.  The existing ``normalize_language_code`` function remains
    deliberately lossy for aggregate statistics; this helper is for stream
    metadata where retaining the region is important.
    """
    if value is None:
        return None

    candidate = value.strip().replace("_", "-")
    if not candidate or not _LANGUAGE_TAG_RE.fullmatch(candidate):
        return None

    parts = candidate.split("-")
    if not parts or any(not part for part in parts):
        return None

    primary = parts[0].lower()
    primary = _REGISTRY["aliases"].get(primary) or _REGISTRY["preferred"].get(primary) or LANGUAGE_ALIASES.get(primary, primary)
    if primary not in {"i", "x"} and (not primary.isalpha() or len(primary) not in {2, 3}):
        return None
    normalized = [primary]
    extension_mode = False
    region_seen = False
    for part in parts[1:]:
        if extension_mode:
            normalized.append(part.lower())
            continue
        if len(part) == 1 and part.isalnum():
            normalized.append(part.lower())
            extension_mode = True
        elif len(part) == 4 and part.isalpha():
            normalized.append(part[0].upper() + part[1:].lower())
        elif not region_seen and ((len(part) == 2 and part.isalpha()) or (len(part) == 3 and part.isdigit())):
            normalized.append(part.upper())
            region_seen = True
        else:
            normalized.append(part.lower())
    return "-".join(normalized)


def format_filename_language_code(value: str | None, format: str = "iso_639_1") -> str:
    """Render a recognized language in the selected filename convention."""
    normalized = normalize_language_tag(value)
    if not normalized:
        return ""
    if format in {"bcp_47", "source"}:
        return normalized
    primary, *rest = normalized.split("-")
    mapping = {
        "iso_639_1": _REGISTRY["to_1"],
        "iso_639_2": _REGISTRY["to_2_b"],
        "iso_639_2_t": _REGISTRY["to_2_t"],
        "iso_639_3": _REGISTRY["to_2_t"],
    }.get(format, _REGISTRY["to_1"])
    return "-".join([mapping.get(primary, primary), *rest])


def recognized_language_tag(value: str | None) -> str | None:
    """Accept registered language subtags and their common ISO aliases."""
    normalized = normalize_language_tag(value)
    if not normalized:
        return None
    raw_primary = str(value).strip().replace("_", "-").split("-", 1)[0].lower()
    if raw_primary not in _KNOWN_LANGUAGES and raw_primary not in _REGISTRY["aliases"]:
        return None
    return normalized


def format_stream_language_code(value: str | None, format: str, container: str) -> str:
    """Return a stream language that FFmpeg can retain in the target muxer."""
    normalized = normalize_language_tag(value) or "und"
    if format == "container_default":
        format = "iso_639_2_t" if container == "mp4" else "iso_639_2"
    if format not in {"iso_639_2", "iso_639_2_region", "iso_639_2_t"}:
        raise ValueError(f"Unknown stream language code format: {format}")
    if container == "mp4" and format != "iso_639_2_t":
        raise ValueError("MP4 stream language metadata requires ISO 639-2/T")
    if container != "mp4" and format == "iso_639_2_t":
        raise ValueError("Matroska and WebM stream language metadata requires ISO 639-2/B")
    primary, *rest = normalized.split("-")
    mapping = _REGISTRY["to_2_t" if format == "iso_639_2_t" else "to_2_b"]
    code = mapping.get(primary, primary)
    if len(code) != 3:
        raise ValueError(f"No three-letter language code is available for {value}")
    if format == "iso_639_2_region":
        region = next((part.upper() for part in rest if re.fullmatch(r"[A-Za-z]{2}", part)), None)
        return f"{code}-{region}" if region else code
    return code


def normalize_language_hint(value: str | None) -> str | None:
    if value is None:
        return None
    return recognized_language_tag(value)


def expand_language_search_terms(value: str | None) -> set[str]:
    if value is None:
        return set()

    candidate = value.strip().lower()
    if not candidate:
        return set()

    terms = {candidate}
    normalized = normalize_language_hint(candidate) or normalize_language_code(candidate)
    if normalized is None:
        return terms

    terms.add(normalized)
    for alias, mapped in LANGUAGE_ALIASES.items():
        if mapped == normalized:
            terms.add(alias)
    return terms


def merge_language_counts(
    rows: list[tuple[str | None, int]] | tuple[tuple[str | None, int], ...],
    *,
    fallback: str = "und",
) -> list[tuple[str, int]]:
    counts: dict[str, int] = defaultdict(int)
    for label, value in rows:
        key = normalize_language_code(label) or fallback
        counts[key] += value
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))

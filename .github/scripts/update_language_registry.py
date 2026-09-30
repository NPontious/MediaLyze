"""Refresh compact IANA language subtags used by stream and sidecar detection.

Source: https://www.iana.org/assignments/language-subtag-registry/language-subtag-registry
The same generated JSON is installed with the backend and bundled by Vite.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[2]
SOURCE = "https://www.iana.org/assignments/language-subtag-registry/language-subtag-registry"
ISO_SOURCE = "https://raw.githubusercontent.com/flyingcircusio/pycountry/master/src/pycountry/databases/iso639-3.json"


def main() -> None:
    with urlopen(SOURCE, timeout=30) as response:
        registry = response.read().decode("utf-8")
    date = registry.splitlines()[0].removeprefix("File-Date: ")
    languages: set[str] = set()
    preferred: dict[str, str] = {}
    for record in registry.split("%%"):
        fields: dict[str, str] = {}
        for line in record.splitlines():
            if ": " in line and not line.startswith(" "):
                key, value = line.split(": ", 1)
                fields[key] = value
        if fields.get("Type") != "language" or "Subtag" not in fields:
            continue
        code = fields["Subtag"].lower()
        languages.add(code)
        if fields.get("Preferred-Value"):
            preferred[code] = fields["Preferred-Value"].lower()
    # pycountry packages the ISO 639-3 table, including its ISO 639-1 and
    # bibliographic ISO 639-2 aliases. Keep this compact mapping in sync with
    # the IANA subtag snapshot rather than maintaining a short language list.
    with urlopen(ISO_SOURCE, timeout=30) as response:
        iso_rows = json.load(response)["639-3"]
    aliases: dict[str, str] = {}
    to_1: dict[str, str] = {}
    to_2_b: dict[str, str] = {}
    to_2_t: dict[str, str] = {}
    for row in iso_rows:
        primary = row["alpha_2"] if row.get("alpha_2") else row["alpha_3"]
        terminology = row["alpha_3"]
        bibliographic = row.get("bibliographic", terminology)
        for code in {primary, terminology, bibliographic}:
            if code != primary:
                aliases[code] = primary
            if row.get("alpha_2") and code != row["alpha_2"]:
                to_1[code] = row["alpha_2"]
            if code != bibliographic:
                to_2_b[code] = bibliographic
            if code != terminology:
                to_2_t[code] = terminology
    payload = {
        "date": date,
        "languages": sorted(languages),
        "preferred": preferred,
        "aliases": aliases,
        "to_1": to_1,
        "to_2_b": to_2_b,
        "to_2_t": to_2_t,
    }
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    for target in (
        ROOT / "backend/app/services/language_registry.json",
        ROOT / "frontend/src/lib/language-registry.json",
    ):
        target.write_text(data, encoding="utf-8")
    print(f"IANA {date}: {len(languages)} language subtags")


if __name__ == "__main__":
    main()

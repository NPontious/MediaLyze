import pytest

from backend.app.services.languages import (
    format_filename_language_code,
    format_stream_language_code,
    normalize_language_code,
    recognized_language_tag,
)


def test_iso_aliases_and_regional_tags_share_language_identity() -> None:
    assert recognized_language_tag("ger-DE") == "de-DE"
    assert recognized_language_tag("deu-DE") == "de-DE"
    assert recognized_language_tag("cmn-Hans-CN") == "cmn-Hans-CN"
    assert recognized_language_tag("nob") == "nb"
    assert recognized_language_tag("nno") == "nn"
    assert normalize_language_code("cmn-Hans-CN") == "cmn"
    assert recognized_language_tag("qqq") is None


def test_stream_codes_follow_container_language_fields() -> None:
    assert format_stream_language_code("de-DE", "container_default", "mkv") == "ger"
    assert format_stream_language_code("de-DE", "container_default", "mp4") == "deu"
    assert format_stream_language_code("ger-DE", "iso_639_2_t", "mp4") == "deu"
    assert format_stream_language_code("de-DE", "iso_639_2_region", "mkv") == "ger-DE"
    with pytest.raises(ValueError, match="MP4"):
        format_stream_language_code("de-DE", "iso_639_2", "mp4")
    with pytest.raises(ValueError, match="Matroska"):
        format_stream_language_code("de-DE", "iso_639_2_t", "mkv")
    with pytest.raises(ValueError, match="Unknown"):
        format_stream_language_code("de-DE", "bcp_47", "mkv")


def test_filename_formats_keep_regional_detail() -> None:
    assert format_filename_language_code("de-DE", "iso_639_2") == "ger-DE"
    assert format_filename_language_code("de-DE", "iso_639_2_t") == "deu-DE"
    assert format_filename_language_code("cmn-Hans-CN", "iso_639_3") == "cmn-Hans-CN"

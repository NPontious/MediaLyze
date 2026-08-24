from __future__ import annotations

import logging
from threading import Lock
from weakref import WeakKeyDictionary

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session


logger = logging.getLogger(__name__)

MEDIA_FILE_SEARCH_FTS_TABLE = "media_file_search_fts"
MEDIA_FILE_SEARCH_FTS_COLUMNS: tuple[str, ...] = (
    "filename",
    "relative_path",
    "extension",
    "primary_video_codec",
    "primary_video_hdr_type",
    "audio_codecs_search",
    "audio_spatial_profiles_search",
    "audio_languages_search",
    "audio_title",
    "audio_artist",
    "audio_album",
    "audio_album_artist",
    "audio_genre",
    "audio_date",
    "audio_disc",
    "audio_composer",
    "track_number",
    "bit_rate_mode",
    "chapter_titles_search",
    "audiobook_narrator",
    "audiobook_author",
    "audiobook_publisher",
    "audiobook_series",
    "audiobook_series_part",
    "audiobook_description",
    "audiobook_copyright",
    "audiobook_asin",
    "audiobook_isbn",
    "audiobook_language",
    "audiobook_abridged",
    "subtitle_languages_search",
    "subtitle_codecs_search",
    "subtitle_sources_search",
)

_availability_lock = Lock()
_availability_by_engine: WeakKeyDictionary[Engine, bool] = WeakKeyDictionary()
_ready_library_ids_by_engine: WeakKeyDictionary[Engine, set[int]] = WeakKeyDictionary()


def _set_availability(engine: Engine, available: bool) -> None:
    with _availability_lock:
        _availability_by_engine[engine] = available


def media_file_search_index_available(db: Session) -> bool:
    bind = db.get_bind()
    if not isinstance(bind, Engine) or bind.dialect.name != "sqlite":
        return False

    with _availability_lock:
        cached = _availability_by_engine.get(bind)
    if cached is not None:
        return cached

    available = bool(
        db.scalar(
            text(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = :table_name"
            ),
            {"table_name": MEDIA_FILE_SEARCH_FTS_TABLE},
        )
    )
    _set_availability(bind, available)
    return available


def library_search_fields_ready(db: Session, library_id: int) -> bool:
    bind = db.get_bind()
    if not isinstance(bind, Engine):
        return False
    with _availability_lock:
        return library_id in _ready_library_ids_by_engine.get(bind, set())


def mark_library_search_fields_ready(db: Session, library_id: int) -> None:
    bind = db.get_bind()
    if not isinstance(bind, Engine):
        return
    with _availability_lock:
        ready_library_ids = _ready_library_ids_by_engine.setdefault(bind, set())
        ready_library_ids.add(library_id)


def ensure_media_file_search_index(connection: Connection) -> bool:
    engine = connection.engine
    if engine.dialect.name != "sqlite":
        return False

    existing = connection.execute(
        text(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = :table_name"
        ),
        {"table_name": MEDIA_FILE_SEARCH_FTS_TABLE},
    ).scalar()
    if existing is not None:
        indexed_columns = tuple(
            str(row["name"])
            for row in connection.exec_driver_sql(
                f"PRAGMA table_info('{MEDIA_FILE_SEARCH_FTS_TABLE}')"
            ).mappings()
        )
        if indexed_columns != MEDIA_FILE_SEARCH_FTS_COLUMNS:
            connection.exec_driver_sql(
                "DROP TRIGGER IF EXISTS media_files_search_fts_insert"
            )
            connection.exec_driver_sql(
                "DROP TRIGGER IF EXISTS media_files_search_fts_delete"
            )
            connection.exec_driver_sql(
                "DROP TRIGGER IF EXISTS media_files_search_fts_update"
            )
            connection.exec_driver_sql(
                f"DROP TABLE IF EXISTS {MEDIA_FILE_SEARCH_FTS_TABLE}"
            )
            existing = None
    columns_sql = ", ".join(MEDIA_FILE_SEARCH_FTS_COLUMNS)
    new_values_sql = ", ".join(f"new.{column}" for column in MEDIA_FILE_SEARCH_FTS_COLUMNS)
    old_values_sql = ", ".join(f"old.{column}" for column in MEDIA_FILE_SEARCH_FTS_COLUMNS)

    try:
        connection.exec_driver_sql(
            f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS {MEDIA_FILE_SEARCH_FTS_TABLE}
            USING fts5(
              {columns_sql},
              content='media_files',
              content_rowid='id',
              tokenize='trigram'
            )
            """
        )
        connection.exec_driver_sql(
            f"""
            CREATE TRIGGER IF NOT EXISTS media_files_search_fts_insert
            AFTER INSERT ON media_files BEGIN
              INSERT INTO {MEDIA_FILE_SEARCH_FTS_TABLE}(rowid, {columns_sql})
              VALUES (new.id, {new_values_sql});
            END
            """
        )
        connection.exec_driver_sql(
            f"""
            CREATE TRIGGER IF NOT EXISTS media_files_search_fts_delete
            AFTER DELETE ON media_files BEGIN
              INSERT INTO {MEDIA_FILE_SEARCH_FTS_TABLE}(
                {MEDIA_FILE_SEARCH_FTS_TABLE},
                rowid,
                {columns_sql}
              )
              VALUES ('delete', old.id, {old_values_sql});
            END
            """
        )
        connection.exec_driver_sql(
            f"""
            CREATE TRIGGER IF NOT EXISTS media_files_search_fts_update
            AFTER UPDATE OF {columns_sql} ON media_files BEGIN
              INSERT INTO {MEDIA_FILE_SEARCH_FTS_TABLE}(
                {MEDIA_FILE_SEARCH_FTS_TABLE},
                rowid,
                {columns_sql}
              )
              VALUES ('delete', old.id, {old_values_sql});
              INSERT INTO {MEDIA_FILE_SEARCH_FTS_TABLE}(rowid, {columns_sql})
              VALUES (new.id, {new_values_sql});
            END
            """
        )
        if existing is None:
            connection.exec_driver_sql(
                f"INSERT INTO {MEDIA_FILE_SEARCH_FTS_TABLE}({MEDIA_FILE_SEARCH_FTS_TABLE}) "
                "VALUES ('rebuild')"
            )
    except OperationalError:
        logger.warning(
            "SQLite FTS5 trigram search is unavailable; falling back to LIKE-based media search",
            exc_info=True,
        )
        _set_availability(engine, False)
        return False

    _set_availability(engine, True)
    return True

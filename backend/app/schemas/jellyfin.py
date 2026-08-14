from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


class JellyfinConnectionUpdate(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    clear_api_key: bool = False
    enabled: bool | None = None
    sync_interval_minutes: int | None = Field(default=None, ge=0, le=10080)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return "" if value is not None else None
        parsed = HttpUrl(value.strip())
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("Jellyfin URL must use http or https")
        return value.strip().rstrip("/")


class JellyfinConnectionRead(BaseModel):
    base_url: str = ""
    enabled: bool = False
    sync_interval_minutes: int = 60
    api_key_configured: bool = False
    server_name: str | None = None
    server_version: str | None = None
    last_status: str = "never"
    last_error: str | None = None
    last_sync_started_at: datetime | None = None
    last_sync_finished_at: datetime | None = None
    last_successful_sync_at: datetime | None = None
    next_scheduled_sync_at: datetime | None = None


class JellyfinTestRequest(BaseModel):
    base_url: str | None = None
    api_key: str | None = None


class JellyfinTestRead(BaseModel):
    ok: bool
    server_name: str | None = None
    server_version: str | None = None
    error: str | None = None


class JellyfinUserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    jellyfin_user_id: str
    name: str
    enabled_for_sync: bool
    last_synced_at: datetime | None = None


class JellyfinUsersUpdate(BaseModel):
    enabled_user_ids: list[str] = Field(default_factory=list)


class JellyfinPathMappingCreate(BaseModel):
    jellyfin_path_prefix: str = Field(min_length=1, max_length=2048)
    medialyze_path_prefix: str = Field(min_length=1, max_length=2048)
    enabled: bool = True


class JellyfinPathMappingUpdate(BaseModel):
    jellyfin_path_prefix: str | None = Field(default=None, min_length=1, max_length=2048)
    medialyze_path_prefix: str | None = Field(default=None, min_length=1, max_length=2048)
    enabled: bool | None = None


class JellyfinPathMappingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    jellyfin_path_prefix: str
    medialyze_path_prefix: str
    enabled: bool


class JellyfinPathMappingBatchItem(BaseModel):
    id: int | None = Field(default=None, ge=1)
    jellyfin_path_prefix: str = Field(min_length=1, max_length=2048)
    medialyze_path_prefix: str = Field(min_length=1, max_length=2048)
    enabled: bool = True


class JellyfinPathMappingBatchUpdate(BaseModel):
    mappings: list[JellyfinPathMappingBatchItem] = Field(min_length=1, max_length=512)
    delete_ids: list[int] = Field(default_factory=list, max_length=512)

    @model_validator(mode="after")
    def validate_unique_targets(self) -> "JellyfinPathMappingBatchUpdate":
        mapping_ids = [mapping.id for mapping in self.mappings if mapping.id is not None]
        if len(mapping_ids) != len(set(mapping_ids)):
            raise ValueError("Path mapping ids must be unique")
        if len(self.delete_ids) != len(set(self.delete_ids)):
            raise ValueError("Deleted path mapping ids must be unique")
        if set(mapping_ids) & set(self.delete_ids):
            raise ValueError("A path mapping cannot be updated and deleted in the same batch")
        normalized_sources = [
            mapping.jellyfin_path_prefix.strip().replace("\\", "/").rstrip("/").casefold()
            for mapping in self.mappings
        ]
        if len(normalized_sources) != len(set(normalized_sources)):
            raise ValueError("Jellyfin path prefixes must be unique within a batch")
        return self


class JellyfinLibraryRead(BaseModel):
    id: int
    name: str
    collection_type: str | None = None
    locations: list[str] = Field(default_factory=list)
    mapped_locations: list[str] = Field(default_factory=list)
    mapped_status: str
    linked_library_id: int | None = None
    linked_library_name: str | None = None
    link_method: str | None = None
    can_create_medialyze_library: bool = False
    data_scope: str = "jellyfin_only"
    item_count: int = 0
    last_synced_at: datetime


class JellyfinLibraryLinkUpdate(BaseModel):
    linked_library_id: int | None = None


class JellyfinDistributionRead(BaseModel):
    label: str
    value: int


class JellyfinCatalogSummaryRead(BaseModel):
    library_count: int = 0
    item_count: int = 0
    known_size_bytes: int = 0
    size_known_count: int = 0
    known_duration_seconds: float = 0
    duration_known_count: int = 0
    last_synced_at: datetime | None = None


class JellyfinLibraryOverviewRead(BaseModel):
    library: JellyfinLibraryRead
    item_count: int = 0
    known_size_bytes: int = 0
    size_known_count: int = 0
    known_duration_seconds: float = 0
    duration_known_count: int = 0
    earliest_date_created: datetime | None = None
    latest_date_created: datetime | None = None
    item_type_distribution: list[JellyfinDistributionRead] = Field(default_factory=list)
    production_year_distribution: list[JellyfinDistributionRead] = Field(default_factory=list)
    added_month_distribution: list[JellyfinDistributionRead] = Field(default_factory=list)
    playback_distribution: list[JellyfinDistributionRead] = Field(default_factory=list)
    users: list[JellyfinUserRead] = Field(default_factory=list)


class JellyfinLibraryItemRead(BaseModel):
    id: int
    jellyfin_item_id: str
    title: str
    original_title: str | None = None
    item_type: str
    series_name: str | None = None
    season_name: str | None = None
    index_number: int | None = None
    parent_index_number: int | None = None
    date_created: datetime | None = None
    premiere_date: datetime | None = None
    production_year: int | None = None
    size_bytes: int | None = None
    duration_seconds: float | None = None
    has_primary_image: bool = False
    play_count: int = 0
    played: bool = False
    played_user_count: int = 0
    favorite_user_count: int = 0
    match_status: str
    media_file_id: int | None = None


class JellyfinLibraryItemPageRead(BaseModel):
    items: list[JellyfinLibraryItemRead] = Field(default_factory=list)
    total: int = 0
    offset: int = 0
    limit: int = 100


class JellyfinSyncProgressTrackRead(BaseModel):
    id: str
    label: str
    current: int = 0
    total: int | None = None
    status: str = "queued"


class JellyfinSyncStatusRead(JellyfinConnectionRead):
    sync_job_id: int | None = None
    sync_job_status: str | None = None
    sync_trigger_source: str | None = None
    sync_job_active: bool = False
    sync_job_error: str | None = None
    sync_heartbeat_at: datetime | None = None
    sync_summary: dict = Field(default_factory=dict)
    sync_phase: str | None = None
    sync_phase_detail: str | None = None
    sync_current: int = 0
    sync_total: int | None = None
    sync_progress_tracks: list[JellyfinSyncProgressTrackRead] = Field(default_factory=list)
    cancellation_requested: bool = False
    item_count: int = 0
    matched_item_count: int = 0
    unmatched_item_count: int = 0
    library_count: int = 0
    user_count: int = 0


class JellyfinMatchRead(BaseModel):
    id: int
    media_file_id: int
    jellyfin_item_id: int
    match_method: str
    confidence: float
    status: str
    mismatch_reason: str | None = None


class JellyfinUserItemDataRead(BaseModel):
    jellyfin_user_id: str
    user_name: str
    play_count: int
    played: bool
    playback_position_ticks: int
    last_played_date: datetime | None = None
    is_favorite: bool


class JellyfinPlaybackEventRead(BaseModel):
    jellyfin_activity_id: int
    jellyfin_user_id: str
    user_name: str
    played_at: datetime


class JellyfinItemRead(BaseModel):
    id: int
    jellyfin_item_id: str
    item_type: str
    path: str | None = None
    title: str
    original_title: str | None = None
    series_name: str | None = None
    season_name: str | None = None
    index_number: int | None = None
    parent_index_number: int | None = None
    date_created: datetime | None = None
    premiere_date: datetime | None = None
    production_year: int | None = None
    overview: str | None = None
    provider_ids: dict = Field(default_factory=dict)
    image_tags: dict = Field(default_factory=dict)
    backdrop_image_tags: list = Field(default_factory=list)
    match_status: str
    mismatch_reason: str | None = None


class JellyfinItemDetailRead(BaseModel):
    item: JellyfinItemRead
    library_id: int | None = None
    library_name: str | None = None
    size_bytes: int | None = None
    duration_seconds: float | None = None
    match: JellyfinMatchRead | None = None
    user_data: list[JellyfinUserItemDataRead] = Field(default_factory=list)


class JellyfinFileOverlayRead(BaseModel):
    match: JellyfinMatchRead | None = None
    item: JellyfinItemRead | None = None
    user_data: list[JellyfinUserItemDataRead] = Field(default_factory=list)
    playback_events: list[JellyfinPlaybackEventRead] = Field(default_factory=list)
    individual_playback_history_start_at: datetime | None = None


class JellyfinUnmatchedRead(BaseModel):
    item: JellyfinItemRead


class JellyfinSyncStartRead(BaseModel):
    job_id: int
    status: str
    trigger_source: str
    accepted: bool


class JellyfinSyncCancelRead(BaseModel):
    job_id: int | None = None
    status: str | None = None
    cancellation_requested: bool


class JellyfinMatchRecomputeStatusRead(BaseModel):
    status: str = "idle"
    active: bool = False
    rerun_pending: bool = False
    last_error: str | None = None

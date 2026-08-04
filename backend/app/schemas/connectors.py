from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.schemas._time import UtcDateTime


class ConnectorConnectionCreate(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    base_url: str = Field(default="", max_length=2048)
    secret: str = Field(default="", max_length=12000)
    config: dict = Field(default_factory=dict)
    enabled: bool = False
    sync_interval_minutes: int = Field(default=60, ge=5, le=10080)


class ConnectorConnectionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    base_url: str | None = Field(default=None, max_length=2048)
    secret: str | None = Field(default=None, max_length=12000)
    config: dict | None = None
    enabled: bool | None = None
    sync_interval_minutes: int | None = Field(default=None, ge=5, le=10080)


class ConnectorConnectionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider: str
    name: str
    base_url: str
    config: dict
    capabilities: dict
    enabled: bool
    sync_interval_minutes: int
    server_name: str | None
    server_version: str | None
    last_status: str
    last_error: str | None
    last_sync_started_at: UtcDateTime | None
    last_sync_finished_at: UtcDateTime | None
    last_successful_sync_at: UtcDateTime | None
    has_secret: bool = False
    created_at: UtcDateTime
    updated_at: UtcDateTime


class ConnectorTestRequest(BaseModel):
    base_url: str | None = Field(default=None, max_length=2048)
    secret: str | None = Field(default=None, max_length=12000)


class ConnectorTestRead(BaseModel):
    success: bool
    server_name: str | None = None
    server_version: str | None = None
    error: str | None = None


class ConnectorLocationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    connector_library_id: int
    remote_path: str
    normalized_path: str


class ConnectorLibraryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    connection_id: int
    remote_id: str
    name: str
    media_type: str | None
    provider_payload: dict
    last_synced_at: UtcDateTime | None
    locations: list[ConnectorLocationRead] = Field(default_factory=list)
    linked_library_ids: list[int] = Field(default_factory=list)


class ConnectorBindingWrite(BaseModel):
    id: int | None = Field(default=None, ge=1)
    location_id: int = Field(ge=1)
    library_root_id: int = Field(ge=1)
    source_prefix: str = Field(min_length=1, max_length=4096)
    target_subpath: str = Field(default="", max_length=2048)
    case_mode: Literal["sensitive", "insensitive"] = "sensitive"
    priority: int = Field(default=0, ge=-1000, le=1000)
    active: bool = True


class ConnectorBindingBatchUpdate(BaseModel):
    bindings: list[ConnectorBindingWrite] = Field(default_factory=list, max_length=10000)

    @model_validator(mode="after")
    def reject_duplicate_ids(self) -> "ConnectorBindingBatchUpdate":
        ids = [binding.id for binding in self.bindings if binding.id is not None]
        if len(ids) != len(set(ids)):
            raise ValueError("Binding ids must be unique")
        return self


class ConnectorBindingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    location_id: int
    library_root_id: int
    source_prefix: str
    normalized_source_prefix: str
    target_subpath: str
    case_mode: str
    priority: int
    active: bool


class ConnectorItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    connection_id: int
    connector_library_id: int | None
    remote_id: str
    item_type: str
    remote_path: str | None
    title: str
    size_bytes: int | None
    duration_seconds: float | None
    match_status: str
    mismatch_reason: str | None
    suggested_media_file_id: int | None
    last_synced_at: UtcDateTime | None


class ConnectorItemPageRead(BaseModel):
    total: int
    offset: int
    limit: int
    items: list[ConnectorItemRead]


class ConnectorManualMatchWrite(BaseModel):
    media_file_id: int = Field(ge=1)


class ConnectorMatchRead(BaseModel):
    connector_item_id: int
    media_file_id: int
    binding_id: int | None = None
    match_method: str
    confidence: float
    status: str


class ConnectorSyncStartRead(BaseModel):
    job_id: int
    status: str
    trigger_source: str
    accepted: bool


class ConnectorSyncCancelRead(BaseModel):
    job_id: int | None = None
    status: str | None = None
    cancellation_requested: bool


class ConnectorSyncJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    connection_id: int
    status: str
    trigger_source: str
    cancellation_requested: bool
    progress_phase: str | None
    progress_detail: str | None
    progress_current: int
    progress_total: int | None
    queued_at: UtcDateTime
    started_at: UtcDateTime | None
    finished_at: UtcDateTime | None
    error: str | None
    sync_summary: dict


class FileConnectorSourceRead(BaseModel):
    connection_id: int
    connection_name: str
    provider: str
    connector_item_id: int
    remote_id: str
    title: str
    item_type: str
    remote_path: str | None
    match_method: str
    provider_payload: dict = Field(default_factory=dict)

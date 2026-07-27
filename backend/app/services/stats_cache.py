from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from threading import Event, Lock
from typing import Generic, TypeVar

from backend.app.schemas.comparison import ComparisonFieldId, ComparisonResponse
from backend.app.schemas.library import LibraryStatistics, LibrarySummary
from backend.app.schemas.library_history import DashboardHistoryResponse, LibraryHistoryResponse
from backend.app.schemas.media import DashboardResponse

CacheValue = TypeVar("CacheValue")
PanelCacheKey = tuple[str, ...] | None


@dataclass
class _InFlight(Generic[CacheValue]):
    event: Event
    result: CacheValue | None = None
    error: BaseException | None = None


def _get_cached(cache: OrderedDict, key):
    value = cache.get(key)
    if value is not None:
        cache.move_to_end(key)
    return value


def _set_cached(cache: OrderedDict, key, value, *, limit: int) -> None:
    cache[key] = value
    cache.move_to_end(key)
    while len(cache) > limit:
        cache.popitem(last=False)


def _delete_matching(cache: OrderedDict, predicate) -> None:
    for key in list(cache):
        if predicate(key):
            cache.pop(key, None)


class StatsCache:
    _DASHBOARD_LIMIT = 32
    _DASHBOARD_HISTORY_LIMIT = 4
    _DASHBOARD_COMPARISON_LIMIT = 24
    _DASHBOARD_COMPARISON_SOURCE_LIMIT = 4
    _LIBRARIES_LIMIT = 4
    _LIBRARY_SUMMARY_LIMIT = 64
    _LIBRARY_HISTORY_LIMIT = 64
    _LIBRARY_STATISTICS_LIMIT = 64
    _LIBRARY_COMPARISON_LIMIT = 64
    _LIBRARY_COMPARISON_SOURCE_LIMIT = 64

    def __init__(self) -> None:
        self._lock = Lock()
        self._dashboard: OrderedDict[tuple[str, PanelCacheKey], DashboardResponse] = OrderedDict()
        self._dashboard_history: OrderedDict[str, DashboardHistoryResponse] = OrderedDict()
        self._dashboard_comparisons: OrderedDict[
            tuple[str, ComparisonFieldId, ComparisonFieldId],
            ComparisonResponse,
        ] = OrderedDict()
        self._dashboard_comparison_sources: OrderedDict[str, list[object]] = OrderedDict()
        self._libraries: OrderedDict[str, list[LibrarySummary]] = OrderedDict()
        self._library_summaries: OrderedDict[tuple[str, int], LibrarySummary] = OrderedDict()
        self._library_history: OrderedDict[tuple[str, int], LibraryHistoryResponse] = OrderedDict()
        self._library_statistics: OrderedDict[tuple[str, int, PanelCacheKey], LibraryStatistics] = OrderedDict()
        self._library_comparisons: OrderedDict[
            tuple[str, int, ComparisonFieldId, ComparisonFieldId],
            ComparisonResponse,
        ] = OrderedDict()
        self._library_comparison_sources: OrderedDict[tuple[str, int], list[object]] = OrderedDict()
        self._inflight: dict[tuple[str, object], _InFlight] = {}
        self._epochs: dict[str, int] = {}

    def _get_or_compute(
        self,
        *,
        namespace: str,
        cache: OrderedDict,
        key,
        limit: int,
        epoch_key: str,
        compute: Callable[[], CacheValue],
    ) -> CacheValue:
        with self._lock:
            cached = _get_cached(cache, key)
            if cached is not None:
                return cached
            epoch = self._epochs.get(epoch_key, 0)
            inflight_key = (namespace, (epoch, key))
            state = self._inflight.get(inflight_key)
            leader = state is None
            if state is None:
                state = _InFlight(event=Event())
                self._inflight[inflight_key] = state

        if not leader:
            state.event.wait()
            if state.error is not None:
                raise state.error
            return state.result  # type: ignore[return-value]

        try:
            result = compute()
        except BaseException as exc:
            with self._lock:
                state.error = exc
                self._inflight.pop(inflight_key, None)
                state.event.set()
            raise

        with self._lock:
            if self._epochs.get(epoch_key, 0) == epoch:
                _set_cached(cache, key, result, limit=limit)
            state.result = result
            self._inflight.pop(inflight_key, None)
            state.event.set()
        return result

    def get_dashboard(
        self,
        cache_key: str,
        panel_key: PanelCacheKey = None,
    ) -> DashboardResponse | None:
        with self._lock:
            return _get_cached(self._dashboard, (cache_key, panel_key))

    def set_dashboard(
        self,
        cache_key: str,
        payload: DashboardResponse,
        panel_key: PanelCacheKey = None,
    ) -> None:
        with self._lock:
            _set_cached(self._dashboard, (cache_key, panel_key), payload, limit=self._DASHBOARD_LIMIT)

    def get_or_compute_dashboard(
        self,
        cache_key: str,
        panel_key: PanelCacheKey,
        compute: Callable[[], DashboardResponse],
    ) -> DashboardResponse:
        return self._get_or_compute(
            namespace="dashboard",
            cache=self._dashboard,
            key=(cache_key, panel_key),
            limit=self._DASHBOARD_LIMIT,
            epoch_key=cache_key,
            compute=compute,
        )

    def get_dashboard_history(self, cache_key: str) -> DashboardHistoryResponse | None:
        with self._lock:
            return _get_cached(self._dashboard_history, cache_key)

    def set_dashboard_history(self, cache_key: str, payload: DashboardHistoryResponse) -> None:
        with self._lock:
            _set_cached(self._dashboard_history, cache_key, payload, limit=self._DASHBOARD_HISTORY_LIMIT)

    def get_or_compute_dashboard_history(
        self,
        cache_key: str,
        compute: Callable[[], DashboardHistoryResponse],
    ) -> DashboardHistoryResponse:
        return self._get_or_compute(
            namespace="dashboard_history",
            cache=self._dashboard_history,
            key=cache_key,
            limit=self._DASHBOARD_HISTORY_LIMIT,
            epoch_key=cache_key,
            compute=compute,
        )

    def get_dashboard_comparison(
        self,
        cache_key: str,
        x_field: ComparisonFieldId,
        y_field: ComparisonFieldId,
    ) -> ComparisonResponse | None:
        with self._lock:
            return _get_cached(self._dashboard_comparisons, (cache_key, x_field, y_field))

    def get_dashboard_comparison_source(self, cache_key: str) -> list[object] | None:
        with self._lock:
            return _get_cached(self._dashboard_comparison_sources, cache_key)

    def set_dashboard_comparison_source(self, cache_key: str, rows: list[object]) -> None:
        with self._lock:
            _set_cached(
                self._dashboard_comparison_sources,
                cache_key,
                rows,
                limit=self._DASHBOARD_COMPARISON_SOURCE_LIMIT,
            )

    def set_dashboard_comparison(
        self,
        cache_key: str,
        x_field: ComparisonFieldId,
        y_field: ComparisonFieldId,
        payload: ComparisonResponse,
    ) -> None:
        with self._lock:
            _set_cached(
                self._dashboard_comparisons,
                (cache_key, x_field, y_field),
                payload,
                limit=self._DASHBOARD_COMPARISON_LIMIT,
            )

    def get_libraries(self, cache_key: str) -> list[LibrarySummary] | None:
        with self._lock:
            return _get_cached(self._libraries, cache_key)

    def set_libraries(self, cache_key: str, payload: list[LibrarySummary]) -> None:
        with self._lock:
            _set_cached(self._libraries, cache_key, payload, limit=self._LIBRARIES_LIMIT)

    def get_library_summary(self, cache_key: str, library_id: int) -> LibrarySummary | None:
        with self._lock:
            return _get_cached(self._library_summaries, (cache_key, library_id))

    def get_library_history(self, cache_key: str, library_id: int) -> LibraryHistoryResponse | None:
        with self._lock:
            return _get_cached(self._library_history, (cache_key, library_id))

    def set_library_summary(self, cache_key: str, library_id: int, payload: LibrarySummary) -> None:
        with self._lock:
            _set_cached(self._library_summaries, (cache_key, library_id), payload, limit=self._LIBRARY_SUMMARY_LIMIT)

    def set_library_history(self, cache_key: str, library_id: int, payload: LibraryHistoryResponse) -> None:
        with self._lock:
            _set_cached(self._library_history, (cache_key, library_id), payload, limit=self._LIBRARY_HISTORY_LIMIT)

    def get_or_compute_library_history(
        self,
        cache_key: str,
        library_id: int,
        compute: Callable[[], LibraryHistoryResponse],
    ) -> LibraryHistoryResponse:
        return self._get_or_compute(
            namespace="library_history",
            cache=self._library_history,
            key=(cache_key, library_id),
            limit=self._LIBRARY_HISTORY_LIMIT,
            epoch_key=cache_key,
            compute=compute,
        )

    def get_library_statistics(
        self,
        cache_key: str,
        library_id: int,
        panel_key: PanelCacheKey = None,
    ) -> LibraryStatistics | None:
        with self._lock:
            return _get_cached(self._library_statistics, (cache_key, library_id, panel_key))

    def set_library_statistics(
        self,
        cache_key: str,
        library_id: int,
        payload: LibraryStatistics,
        panel_key: PanelCacheKey = None,
    ) -> None:
        with self._lock:
            _set_cached(
                self._library_statistics,
                (cache_key, library_id, panel_key),
                payload,
                limit=self._LIBRARY_STATISTICS_LIMIT,
            )

    def get_or_compute_library_statistics(
        self,
        cache_key: str,
        library_id: int,
        panel_key: PanelCacheKey,
        compute: Callable[[], LibraryStatistics],
    ) -> LibraryStatistics:
        return self._get_or_compute(
            namespace="library_statistics",
            cache=self._library_statistics,
            key=(cache_key, library_id, panel_key),
            limit=self._LIBRARY_STATISTICS_LIMIT,
            epoch_key=cache_key,
            compute=compute,
        )

    def get_library_comparison(
        self,
        cache_key: str,
        library_id: int,
        x_field: ComparisonFieldId,
        y_field: ComparisonFieldId,
    ) -> ComparisonResponse | None:
        with self._lock:
            return _get_cached(self._library_comparisons, (cache_key, library_id, x_field, y_field))

    def get_library_comparison_source(self, cache_key: str, library_id: int) -> list[object] | None:
        with self._lock:
            return _get_cached(self._library_comparison_sources, (cache_key, library_id))

    def set_library_comparison_source(self, cache_key: str, library_id: int, rows: list[object]) -> None:
        with self._lock:
            _set_cached(
                self._library_comparison_sources,
                (cache_key, library_id),
                rows,
                limit=self._LIBRARY_COMPARISON_SOURCE_LIMIT,
            )

    def set_library_comparison(
        self,
        cache_key: str,
        library_id: int,
        x_field: ComparisonFieldId,
        y_field: ComparisonFieldId,
        payload: ComparisonResponse,
    ) -> None:
        with self._lock:
            _set_cached(
                self._library_comparisons,
                (cache_key, library_id, x_field, y_field),
                payload,
                limit=self._LIBRARY_COMPARISON_LIMIT,
            )

    def invalidate(self, cache_key: str, library_id: int | None = None) -> None:
        with self._lock:
            self._epochs[cache_key] = self._epochs.get(cache_key, 0) + 1
            _delete_matching(self._dashboard, lambda key: key[0] == cache_key)
            self._dashboard_history.pop(cache_key, None)
            _delete_matching(self._dashboard_comparisons, lambda key: key[0] == cache_key)
            self._dashboard_comparison_sources.pop(cache_key, None)
            self._libraries.pop(cache_key, None)
            if library_id is None:
                _delete_matching(self._library_summaries, lambda key: key[0] == cache_key)
                _delete_matching(self._library_history, lambda key: key[0] == cache_key)
                _delete_matching(self._library_statistics, lambda key: key[0] == cache_key)
                _delete_matching(self._library_comparisons, lambda key: key[0] == cache_key)
                _delete_matching(self._library_comparison_sources, lambda key: key[0] == cache_key)
            else:
                _delete_matching(
                    self._library_summaries,
                    lambda key: key[0] == cache_key and key[1] == library_id,
                )
                self._library_history.pop((cache_key, library_id), None)
                _delete_matching(
                    self._library_statistics,
                    lambda key: key[0] == cache_key and key[1] == library_id,
                )
                _delete_matching(
                    self._library_comparisons,
                    lambda key: key[0] == cache_key and key[1] == library_id,
                )
                self._library_comparison_sources.pop((cache_key, library_id), None)


stats_cache = StatsCache()

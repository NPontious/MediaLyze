from __future__ import annotations

from datetime import UTC, datetime
from threading import Lock, Thread
from time import sleep

from backend.app.schemas.library_history import DashboardHistoryResponse
from backend.app.services.stats_cache import StatsCache


def test_dashboard_history_cache_coalesces_concurrent_misses() -> None:
    cache = StatsCache()
    compute_count = 0
    compute_lock = Lock()
    results: list[DashboardHistoryResponse] = []

    def compute() -> DashboardHistoryResponse:
        nonlocal compute_count
        with compute_lock:
            compute_count += 1
        sleep(0.05)
        return DashboardHistoryResponse(generated_at=datetime.now(UTC))

    def load() -> None:
        results.append(cache.get_or_compute_dashboard_history("engine", compute))

    threads = [Thread(target=load) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert compute_count == 1
    assert len(results) == 4
    assert all(result is results[0] for result in results)


def test_dashboard_statistics_cache_coalesces_concurrent_panel_misses() -> None:
    cache = StatsCache()
    compute_count = 0
    compute_lock = Lock()
    result = object()
    results: list[object] = []

    def compute():
        nonlocal compute_count
        with compute_lock:
            compute_count += 1
        sleep(0.05)
        return result

    def load() -> None:
        results.append(cache.get_or_compute_dashboard("engine", ("container",), compute))

    threads = [Thread(target=load) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert compute_count == 1
    assert results == [result] * 4


def test_invalidate_removes_all_dashboard_and_target_library_panel_variants() -> None:
    cache = StatsCache()
    dashboard_container = object()
    dashboard_video = object()
    library_container = object()
    library_video = object()
    other_library = object()

    cache.set_dashboard("engine", dashboard_container, ("container",))
    cache.set_dashboard("engine", dashboard_video, ("video_codec",))
    cache.set_library_statistics("engine", 1, library_container, ("container",))
    cache.set_library_statistics("engine", 1, library_video, ("video_codec",))
    cache.set_library_statistics("engine", 2, other_library, ("container",))

    cache.invalidate("engine", 1)

    assert cache.get_dashboard("engine", ("container",)) is None
    assert cache.get_dashboard("engine", ("video_codec",)) is None
    assert cache.get_library_statistics("engine", 1, ("container",)) is None
    assert cache.get_library_statistics("engine", 1, ("video_codec",)) is None
    assert cache.get_library_statistics("engine", 2, ("container",)) is other_library

import pytest
from backend.app.services.stats_cache import stats_cache


@pytest.fixture(autouse=True)
def _reset_stats_cache():
    stats_cache.clear()
    yield
    stats_cache.clear()

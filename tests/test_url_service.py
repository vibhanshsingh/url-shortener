"""
This is exactly the payoff of the service layer being framework- and
database-agnostic (Milestone 1's design pattern discussion, made
concrete): we test all the real business logic here — idempotency,
self-referential rejection, encoding, and now cache-aside behavior —
using fake in-memory repository and cache instead of real Postgres and
Redis connections. No Docker, no network. These tests run in
milliseconds.

FakeURLRepository and FakeURLCache below deliberately implement the
exact same method signatures as the real classes. That's the contract
the service layer depends on — as long as both honor it, the service
can't tell the difference between the fake and the real thing.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.repository.url_repository import DuplicateLongURLError
from app.services.url_service import (
    RedirectTarget,
    SelfReferentialURLError,
    URLGoneError,
    URLNotFoundError,
    URLService,
)


class FakeURLRepository:
    """In-memory stand-in for URLRepository. Mirrors its public method
    signatures exactly, so URLService can't tell it apart from the real
    thing."""

    def __init__(self):
        self._by_long_url: dict[str, SimpleNamespace] = {}
        self._by_short_code: dict[str, SimpleNamespace] = {}
        self._next_id_counter = 1
        # Lets tests assert the cache actually prevented a DB call,
        # not just that the returned value happened to be correct.
        self.db_lookup_count = 0
        # Milestone 13: simulates "another request already won the
        # race and created this row." Call simulate_race_loss() to
        # arm it — the NEXT create_with_id() call for that long_url
        # will insert the winning row instead of the caller's data,
        # then raise DuplicateLongURLError exactly once.
        self._race_loss_long_url: str | None = None
        self._race_winner: SimpleNamespace | None = None

    def simulate_race_loss(self, long_url: str, winner: SimpleNamespace) -> None:
        self._race_loss_long_url = long_url
        self._race_winner = winner

    async def get_active_by_long_url(self, long_url: str):
        return self._by_long_url.get(long_url)

    async def get_by_short_code_any_status(self, short_code: str):
        self.db_lookup_count += 1
        return self._by_short_code.get(short_code)

    async def reserve_next_id(self) -> int:
        id_ = self._next_id_counter
        self._next_id_counter += 1
        return id_

    async def create_with_id(self, *, id_, short_code, long_url, created_by_ip):
        if self._race_loss_long_url == long_url:
            self._by_long_url[long_url] = self._race_winner
            self._by_short_code[self._race_winner.short_code] = self._race_winner
            self._race_loss_long_url = None  # only trigger once
            raise DuplicateLongURLError(f"Simulated race loss for {long_url!r}")

        row = SimpleNamespace(
            id=id_,
            short_code=short_code,
            long_url=long_url,
            created_by_ip=created_by_ip,
            created_at=datetime.now(timezone.utc),
            is_active=True,
            expires_at=None,
        )
        self._by_long_url[long_url] = row
        self._by_short_code[short_code] = row
        return row


class FakeURLCache:
    """In-memory stand-in for URLCache. Same interface as the real
    Redis-backed one: get returns a dict or None, set stores a dict,
    invalidate removes it."""

    def __init__(self):
        self._store: dict[str, dict] = {}

    async def get(self, short_code: str) -> dict | None:
        return self._store.get(short_code)

    async def set(self, short_code: str, long_url: str, expires_at, ttl_seconds=None) -> None:
        self._store[short_code] = {
            "long_url": long_url,
            "expires_at": expires_at.isoformat() if expires_at else None,
        }

    async def invalidate(self, short_code: str) -> None:
        self._store.pop(short_code, None)


@pytest.fixture
def repository():
    return FakeURLRepository()


@pytest.fixture
def cache():
    return FakeURLCache()


@pytest.fixture
def service(repository, cache):
    return URLService(repository, cache)


class TestCreateShortUrl:
    async def test_creates_new_url_and_returns_already_existed_false(self, service):
        row, already_existed = await service.create_short_url(
            "https://example.com/article", created_by_ip="1.2.3.4"
        )
        assert already_existed is False
        assert row.long_url == "https://example.com/article"
        assert row.short_code != ""

    async def test_short_code_is_deterministic_from_encoder(self, service):
        # id=1 should encode via our Base62 encoder to "0001" — pins
        # the service layer's use of the encoder to a known value, so
        # a regression in either the service or the encoder shows up
        # here too, not just in test_encoding.py.
        row, _ = await service.create_short_url("https://example.com/a", created_by_ip=None)
        assert row.short_code == "0001"

    async def test_second_identical_url_returns_existing_code(self, service):
        first, first_existed = await service.create_short_url(
            "https://example.com/dup", created_by_ip="1.1.1.1"
        )
        second, second_existed = await service.create_short_url(
            "https://example.com/dup", created_by_ip="2.2.2.2"
        )
        assert first_existed is False
        assert second_existed is True
        assert first.short_code == second.short_code

    async def test_different_urls_get_different_codes(self, service):
        first, _ = await service.create_short_url("https://example.com/one", created_by_ip=None)
        second, _ = await service.create_short_url("https://example.com/two", created_by_ip=None)
        assert first.short_code != second.short_code

    async def test_creation_warms_the_cache(self, service, cache):
        # Cache warming (Milestone 7): the code should be cache-ready
        # immediately after creation, without waiting for a first
        # redirect/cache-miss to populate it.
        row, _ = await service.create_short_url("https://example.com/warm", created_by_ip=None)
        cached = await cache.get(row.short_code)
        assert cached is not None
        assert cached["long_url"] == "https://example.com/warm"


class TestSelfReferentialRejection:
    async def test_rejects_url_pointing_at_own_service(self, service):
        # settings.base_host defaults to "localhost:8000" (parsed from
        # BASE_URL). We don't need to monkeypatch it here since that's
        # exactly the default — but naming it explicitly documents the
        # assumption this test relies on.
        with pytest.raises(SelfReferentialURLError):
            await service.create_short_url(
                "http://localhost:8000/someCode", created_by_ip=None
            )

    async def test_allows_normal_external_url(self, service):
        row, _ = await service.create_short_url("https://github.com", created_by_ip=None)
        assert row.long_url == "https://github.com"


class TestResolveForRedirect:
    async def test_raises_not_found_for_unknown_code(self, service):
        with pytest.raises(URLNotFoundError):
            await service.resolve_for_redirect("doesNotExist")

    async def test_returns_target_for_active_code(self, service):
        created, _ = await service.create_short_url("https://example.com/x", created_by_ip=None)
        resolved = await service.resolve_for_redirect(created.short_code)
        assert isinstance(resolved, RedirectTarget)
        assert resolved.long_url == "https://example.com/x"

    async def test_raises_gone_for_deactivated_code(self, service, cache):
        created, _ = await service.create_short_url("https://example.com/y", created_by_ip=None)
        created.is_active = False  # simulate a soft delete happening in the DB

        # The cache has no concept of is_active (see URLCache docstring
        # for why) — a real deactivation flow must call cache.invalidate()
        # itself. We do that explicitly here to simulate that future
        # integration, rather than the test silently passing only
        # because the cache still holds stale "active" data.
        await cache.invalidate(created.short_code)

        with pytest.raises(URLGoneError):
            await service.resolve_for_redirect(created.short_code)

    async def test_raises_gone_for_expired_code(self, service, cache):
        created, _ = await service.create_short_url("https://example.com/z", created_by_ip=None)
        created.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        await cache.invalidate(created.short_code)  # see note above

        with pytest.raises(URLGoneError):
            await service.resolve_for_redirect(created.short_code)

    async def test_future_expiry_still_resolves_successfully(self, service, cache):
        created, _ = await service.create_short_url("https://example.com/w", created_by_ip=None)
        created.expires_at = datetime.now(timezone.utc) + timedelta(days=1)
        await cache.invalidate(created.short_code)

        resolved = await service.resolve_for_redirect(created.short_code)
        assert resolved.long_url == "https://example.com/w"


class TestCacheAsideBehavior:
    """Dedicated tests for the cache-aside mechanics themselves —
    separate from the business-rule tests above, so a cache regression
    and a business-logic regression fail with clearly different test
    names."""

    async def test_cache_hit_skips_the_database_entirely(self, service, repository, cache):
        created, _ = await service.create_short_url("https://example.com/hit", created_by_ip=None)
        lookups_after_create = repository.db_lookup_count  # creation itself does no lookup

        await service.resolve_for_redirect(created.short_code)

        # The cache was warmed at creation time, so this redirect
        # should be served entirely from the fake cache — the
        # repository's lookup method should not have been called again.
        assert repository.db_lookup_count == lookups_after_create

    async def test_cache_miss_falls_back_to_db_and_warms_cache(self, service, repository, cache):
        created, _ = await service.create_short_url("https://example.com/miss", created_by_ip=None)
        # Simulate the cache entry having expired out of Redis (TTL
        # elapsed) without the underlying data changing.
        await cache.invalidate(created.short_code)

        resolved = await service.resolve_for_redirect(created.short_code)

        assert resolved.long_url == "https://example.com/miss"
        assert repository.db_lookup_count == 1  # had to fall back to the DB
        # And the cache should be warm again after the fallback.
        assert await cache.get(created.short_code) is not None

    async def test_expired_cache_entry_is_invalidated_on_read(self, service, cache):
        created, _ = await service.create_short_url("https://example.com/exp2", created_by_ip=None)
        # Manually poke an expired value directly into the cache,
        # simulating a link whose expiry was set after it was cached.
        await cache.set(
            created.short_code,
            created.long_url,
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )

        with pytest.raises(URLGoneError):
            await service.resolve_for_redirect(created.short_code)

        # The stale entry should have been cleaned up, not left behind.
        assert await cache.get(created.short_code) is None


class TestConcurrentCreationRace:
    """
    Milestone 13: proves the fix for the race condition flagged back
    in Milestone 5. Two requests for the same brand-new long_url both
    pass the idempotency check (neither sees the other's row yet,
    because neither has committed). One wins the actual insert; the
    other must detect the conflict and gracefully return the winner's
    row instead of erroring or creating a duplicate.
    """

    async def test_losing_the_race_returns_the_winning_row_instead_of_erroring(
        self, service, repository, cache
    ):
        long_url = "https://example.com/race-condition-test"
        winning_row = SimpleNamespace(
            id=9999,
            short_code="9999",
            long_url=long_url,
            created_by_ip="9.9.9.9",
            created_at=datetime.now(timezone.utc),
            is_active=True,
            expires_at=None,
        )
        # At the moment our request's idempotency check ran, the
        # winner hadn't committed yet — so get_active_by_long_url must
        # currently return nothing for this URL (it's the default
        # empty state, nothing more to arrange there). Arm the
        # simulated conflict for the upcoming create_with_id() call:
        repository.simulate_race_loss(long_url, winning_row)

        row, already_existed = await service.create_short_url(long_url, created_by_ip="1.1.1.1")

        assert already_existed is True
        assert row.short_code == "9999"  # the WINNER's code, not a new one
        assert row.long_url == long_url

    async def test_race_loss_still_warms_the_cache_with_winning_code(
        self, service, repository, cache
    ):
        long_url = "https://example.com/race-cache-test"
        winning_row = SimpleNamespace(
            id=8888,
            short_code="8888",
            long_url=long_url,
            created_by_ip=None,
            created_at=datetime.now(timezone.utc),
            is_active=True,
            expires_at=None,
        )
        repository.simulate_race_loss(long_url, winning_row)

        await service.create_short_url(long_url, created_by_ip=None)

        cached = await cache.get("8888")
        assert cached is not None
        assert cached["long_url"] == long_url

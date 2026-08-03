"""
This is exactly the payoff of the service layer being framework- and
database-agnostic (Milestone 1's design pattern discussion, made
concrete): we test all the real business logic here — idempotency,
self-referential rejection, encoding — using a fake in-memory
repository instead of a real Postgres connection. No Docker, no test
database, no network. These tests run in milliseconds.

FakeURLRepository below deliberately implements the exact same method
signatures as the real URLRepository. That's the contract the service
layer depends on — as long as both honor it, the service can't tell
the difference between the fake and the real thing.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services.url_service import (
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

    async def get_active_by_long_url(self, long_url: str):
        return self._by_long_url.get(long_url)

    async def get_by_short_code_any_status(self, short_code: str):
        return self._by_short_code.get(short_code)

    async def reserve_next_id(self) -> int:
        id_ = self._next_id_counter
        self._next_id_counter += 1
        return id_

    async def create_with_id(self, *, id_, short_code, long_url, created_by_ip):
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


@pytest.fixture
def service():
    return URLService(FakeURLRepository())


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


class TestSelfReferentialRejection:
    async def test_rejects_url_pointing_at_own_service(self, service, monkeypatch):
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

    async def test_returns_row_for_active_code(self, service):
        created, _ = await service.create_short_url("https://example.com/x", created_by_ip=None)
        resolved = await service.resolve_for_redirect(created.short_code)
        assert resolved.long_url == "https://example.com/x"

    async def test_raises_gone_for_deactivated_code(self, service):
        created, _ = await service.create_short_url("https://example.com/y", created_by_ip=None)
        created.is_active = False  # simulate a soft delete
        with pytest.raises(URLGoneError):
            await service.resolve_for_redirect(created.short_code)

    async def test_raises_gone_for_expired_code(self, service):
        created, _ = await service.create_short_url("https://example.com/z", created_by_ip=None)
        created.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        with pytest.raises(URLGoneError):
            await service.resolve_for_redirect(created.short_code)

    async def test_future_expiry_still_resolves_successfully(self, service):
        created, _ = await service.create_short_url("https://example.com/w", created_by_ip=None)
        created.expires_at = datetime.now(timezone.utc) + timedelta(days=1)
        resolved = await service.resolve_for_redirect(created.short_code)
        assert resolved.long_url == "https://example.com/w"

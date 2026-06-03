"""
Unit tests for gateway/app/services/on_chain_blocklist.py.

Mocks the algod HTTP calls so tests run without a live node.
"""

from __future__ import annotations

import base64
import json
import struct
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.app.services.on_chain_blocklist import (
    _popcount,
    _decode_uint64,
    _box_name_b64,
    _BLOCKED_PREFIX,
    _BLOCK_VOTES_PREFIX,
    is_wallet_blocked,
    get_wallet_vote_counts,
    invalidate_cache,
    _BlockedCache,
)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def test_popcount():
    assert _popcount(0) == 0
    assert _popcount(0b1111) == 4
    assert _popcount(0b1010_1010) == 4
    assert _popcount(0xFFFF_FFFF_FFFF_FFFF) == 64


def test_decode_uint64():
    assert _decode_uint64(None) == 0
    assert _decode_uint64(b"\x00" * 8) == 0
    assert _decode_uint64(b"\x00\x00\x00\x00\x00\x00\x00\x01") == 1
    assert _decode_uint64(struct.pack(">Q", 255)) == 255


def test_box_name_b64_prefix_correct():
    # Known Algorand address for deterministic test
    addr = "7777777777777777777777777777777777777777777777777774MSJUVU"
    encoded = _box_name_b64(_BLOCKED_PREFIX, addr)
    assert encoded.startswith("b64:")
    raw = base64.b64decode(encoded[4:])
    assert raw[:2] == b"bl"
    assert len(raw) == 2 + 32  # prefix + 32-byte address


# ---------------------------------------------------------------------------
# TTL cache
# ---------------------------------------------------------------------------

def test_cache_ttl_expiry():
    import time
    cache = _BlockedCache(ttl=0.01)
    cache.set("ADDR", True)
    assert cache.get("ADDR") is True
    time.sleep(0.02)
    assert cache.get("ADDR") is None


def test_cache_invalidate():
    cache = _BlockedCache(ttl=60)
    cache.set("ADDR", True)
    cache.invalidate("ADDR")
    assert cache.get("ADDR") is None


# ---------------------------------------------------------------------------
# Cross-network: blocked on VOI → blocked overall
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_blocked_on_voi_blocks_overall():
    """A wallet blocked only on VOI should return True from is_wallet_blocked."""
    not_blocked_resp = MagicMock()
    not_blocked_resp.status_code = 404   # Algorand box absent

    blocked_value = base64.b64encode(struct.pack(">Q", 1)).decode()
    blocked_resp = MagicMock()
    blocked_resp.status_code = 200
    blocked_resp.json.return_value = {"value": blocked_value}
    blocked_resp.raise_for_status = MagicMock()

    call_count = 0

    async def fake_get(url, **kwargs):
        nonlocal call_count
        call_count += 1
        # First call hits Algorand algod → not blocked
        # Second call hits VOI algod → blocked
        return not_blocked_resp if call_count == 1 else blocked_resp

    mock_http = AsyncMock()
    mock_http.get = fake_get
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=None)

    addr = "7777777777777777777777777777777777777777777777777774MSJUVU"
    invalidate_cache(addr)

    with patch("gateway.app.services.on_chain_blocklist.settings") as ms, \
         patch("httpx.AsyncClient", return_value=mock_http):
        ms.BLOCKLIST_APP_ID = 111111
        ms.BLOCKLIST_VOI_APP_ID = 222222
        ms.BLOCKLIST_ALGOD_URL = "https://algo.example.com"
        ms.BLOCKLIST_VOI_ALGOD_URL = "https://voi.example.com"
        ms.BLOCKLIST_CACHE_SECONDS = 30

        result = await is_wallet_blocked(addr)

    assert result is True


@pytest.mark.asyncio
async def test_both_absent_returns_false():
    """Wallet absent from both Algorand and VOI blocklists."""
    absent_resp = MagicMock()
    absent_resp.status_code = 404

    mock_http = AsyncMock()
    mock_http.get = AsyncMock(return_value=absent_resp)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=None)

    addr = "7777777777777777777777777777777777777777777777777774MSJUVU"
    invalidate_cache(addr)

    with patch("gateway.app.services.on_chain_blocklist.settings") as ms, \
         patch("httpx.AsyncClient", return_value=mock_http):
        ms.BLOCKLIST_APP_ID = 111111
        ms.BLOCKLIST_VOI_APP_ID = 222222
        ms.BLOCKLIST_ALGOD_URL = "https://algo.example.com"
        ms.BLOCKLIST_VOI_ALGOD_URL = "https://voi.example.com"
        ms.BLOCKLIST_CACHE_SECONDS = 30

        result = await is_wallet_blocked(addr)

    assert result is False


# ---------------------------------------------------------------------------
# is_wallet_blocked — feature disabled
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_is_blocked_disabled_when_app_id_zero():
    with patch("gateway.app.services.on_chain_blocklist.settings") as mock_settings:
        mock_settings.BLOCKLIST_APP_ID = 0
        result = await is_wallet_blocked("ADDR7777777777777777777777777777777777777774MSJUVU")
    assert result is False


# ---------------------------------------------------------------------------
# is_wallet_blocked — box exists and wallet is blocked
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_is_blocked_returns_true_when_box_set():
    blocked_value = base64.b64encode(struct.pack(">Q", 1)).decode()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"value": blocked_value}
    mock_response.raise_for_status = MagicMock()

    mock_http_client = AsyncMock()
    mock_http_client.get.return_value = mock_response
    mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
    mock_http_client.__aexit__ = AsyncMock(return_value=None)

    addr = "7777777777777777777777777777777777777777777777777774MSJUVU"
    invalidate_cache(addr)

    with patch("gateway.app.services.on_chain_blocklist.settings") as mock_settings, \
         patch("httpx.AsyncClient", return_value=mock_http_client):
        mock_settings.BLOCKLIST_APP_ID = 999999
        mock_settings.BLOCKLIST_ALGOD_URL = "https://example.com"
        mock_settings.BLOCKLIST_CACHE_SECONDS = 30

        result = await is_wallet_blocked(addr)

    assert result is True


# ---------------------------------------------------------------------------
# is_wallet_blocked — box absent (wallet not blocked)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_is_blocked_returns_false_when_box_missing():
    mock_response = MagicMock()
    mock_response.status_code = 404

    mock_http_client = AsyncMock()
    mock_http_client.get.return_value = mock_response
    mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
    mock_http_client.__aexit__ = AsyncMock(return_value=None)

    addr = "7777777777777777777777777777777777777777777777777774MSJUVU"
    invalidate_cache(addr)

    with patch("gateway.app.services.on_chain_blocklist.settings") as mock_settings, \
         patch("httpx.AsyncClient", return_value=mock_http_client):
        mock_settings.BLOCKLIST_APP_ID = 999999
        mock_settings.BLOCKLIST_ALGOD_URL = "https://example.com"
        mock_settings.BLOCKLIST_CACHE_SECONDS = 30

        result = await is_wallet_blocked(addr)

    assert result is False


# ---------------------------------------------------------------------------
# is_wallet_blocked — fail-open on network error
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_is_blocked_fail_open_on_error():
    mock_http_client = AsyncMock()
    mock_http_client.get.side_effect = Exception("connection refused")
    mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
    mock_http_client.__aexit__ = AsyncMock(return_value=None)

    addr = "7777777777777777777777777777777777777777777777777774MSJUVU"
    invalidate_cache(addr)

    with patch("gateway.app.services.on_chain_blocklist.settings") as mock_settings, \
         patch("httpx.AsyncClient", return_value=mock_http_client):
        mock_settings.BLOCKLIST_APP_ID = 999999
        mock_settings.BLOCKLIST_ALGOD_URL = "https://example.com"
        mock_settings.BLOCKLIST_CACHE_SECONDS = 30

        result = await is_wallet_blocked(addr)

    # Must not raise; must return False (fail-open)
    assert result is False


# ---------------------------------------------------------------------------
# get_wallet_vote_counts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_vote_counts_parses_bitmasks():
    # block bitmask = 0b1111 = 4 votes; unblock bitmask = 0b0011 = 2 votes
    bv_b64 = base64.b64encode(struct.pack(">Q", 0b1111)).decode()
    uv_b64 = base64.b64encode(struct.pack(">Q", 0b0011)).decode()

    call_count = 0

    async def fake_get(url, **kwargs):
        nonlocal call_count
        call_count += 1
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        # First call = bv box, second = uv box
        mock_resp.json.return_value = {"value": bv_b64 if call_count == 1 else uv_b64}
        return mock_resp

    mock_http_client = AsyncMock()
    mock_http_client.get = fake_get
    mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
    mock_http_client.__aexit__ = AsyncMock(return_value=None)

    addr = "7777777777777777777777777777777777777777777777777774MSJUVU"

    with patch("gateway.app.services.on_chain_blocklist.settings") as mock_settings, \
         patch("httpx.AsyncClient", return_value=mock_http_client):
        mock_settings.BLOCKLIST_APP_ID = 999999
        mock_settings.BLOCKLIST_ALGOD_URL = "https://example.com"

        counts = await get_wallet_vote_counts(addr)

    assert counts["block_votes"] == 4
    assert counts["unblock_votes"] == 2

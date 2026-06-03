"""
Gateway service: on-chain wallet blocklist query.

Reads the WalletBlocklistConsensus AVM contract state via direct box reads
on both Algorand mainnet and VOI mainnet — no transaction fee, no signing
required. Uses the algod REST API:
    GET /v2/applications/{app_id}/box?name=b64:<name>

Both networks are checked in parallel. A wallet blocked on either network
is treated as blocked on both — the same 32-byte key format is used across
Algorand and VOI, and a sanctioned actor must not be able to circumvent a
block by switching networks.

Fail-open: if a contract is unreachable, that network's check returns False
so legitimate payments are never halted by an infra outage on our side.
This mirrors the pattern used by sanctions_service.screen_wallet.

Configuration (shared/config.py settings):
    BLOCKLIST_APP_ID            — Algorand mainnet app ID; 0 = disabled
    BLOCKLIST_ALGOD_URL         — Algorand algod URL (default: algonode)
    BLOCKLIST_VOI_APP_ID        — VOI mainnet app ID; 0 = disabled
    BLOCKLIST_VOI_ALGOD_URL     — VOI algod URL (default: voi.nodely.dev)
    BLOCKLIST_CACHE_SECONDS     — in-process cache TTL (default: 30s)

Integration:
    Call `is_wallet_blocked(wallet_address)` from checkout.py or any
    protected payment route immediately after the sanctions screen.
    A True result should be treated identically to a sanctions hit.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import algosdk
import httpx

from shared.config import settings

logger = logging.getLogger(__name__)

_DEFAULT_ALGOD_ALGORAND = "https://mainnet-api.algonode.cloud"
_DEFAULT_ALGOD_VOI = "https://mainnet-api.voi.nodely.dev"
_BLOCKED_PREFIX = b"bl"
_BLOCK_VOTES_PREFIX = b"bv"
_UNBLOCK_VOTES_PREFIX = b"uv"
_BOX_NOT_FOUND = 404


# ---------------------------------------------------------------------------
# Simple TTL cache — avoids hammering algod on every payment request
# ---------------------------------------------------------------------------

@dataclass
class _CacheEntry:
    blocked: bool
    fetched_at: float


class _BlockedCache:
    def __init__(self, ttl: float) -> None:
        self._ttl = ttl
        self._data: dict[str, _CacheEntry] = {}

    def get(self, key: str) -> Optional[bool]:
        entry = self._data.get(key)
        if entry is None:
            return None
        if time.monotonic() - entry.fetched_at > self._ttl:
            del self._data[key]
            return None
        return entry.blocked

    def set(self, key: str, blocked: bool) -> None:
        self._data[key] = _CacheEntry(blocked=blocked, fetched_at=time.monotonic())

    def invalidate(self, key: str) -> None:
        self._data.pop(key, None)


# Module-level cache; TTL comes from settings at first use
_cache: Optional[_BlockedCache] = None


def _get_cache() -> _BlockedCache:
    global _cache
    if _cache is None:
        ttl = float(getattr(settings, "BLOCKLIST_CACHE_SECONDS", 30))
        _cache = _BlockedCache(ttl)
    return _cache


# ---------------------------------------------------------------------------
# Core box-read logic
# ---------------------------------------------------------------------------

def _box_name_b64(prefix: bytes, wallet_address: str) -> str:
    """Base64-encode `prefix + decoded_address` for the algod box query."""
    raw = prefix + algosdk.encoding.decode_address(wallet_address)
    return "b64:" + base64.b64encode(raw).decode()


async def _read_box(
    http: httpx.AsyncClient,
    algod_url: str,
    app_id: int,
    box_name_b64: str,
) -> Optional[bytes]:
    """
    GET /v2/applications/{app_id}/box?name={box_name_b64}
    Returns raw value bytes, or None if the box does not exist.
    Raises on non-404 HTTP errors.
    """
    url = f"{algod_url}/v2/applications/{app_id}/box"
    resp = await http.get(url, params={"name": box_name_b64})
    if resp.status_code == _BOX_NOT_FOUND:
        return None
    resp.raise_for_status()
    data = resp.json()
    return base64.b64decode(data["value"])


def _decode_uint64(raw: Optional[bytes]) -> int:
    """Decode 8-byte big-endian UInt64. Returns 0 if raw is None."""
    if raw is None:
        return 0
    return int.from_bytes(raw, "big")


def _popcount(n: int) -> int:
    return bin(n).count("1")


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

async def _check_one_network(
    http: httpx.AsyncClient,
    wallet_address: str,
    app_id: int,
    algod_url: str,
    network_label: str,
) -> bool:
    """Check a single network's blocklist contract. Returns False on any error (fail-open)."""
    try:
        raw = await _read_box(
            http,
            algod_url,
            app_id,
            _box_name_b64(_BLOCKED_PREFIX, wallet_address),
        )
        return _decode_uint64(raw) > 0
    except Exception:
        logger.exception(
            "on_chain_blocklist.check_error",
            extra={"network": network_label, "wallet": wallet_address[:8] + "..."},
        )
        return False


async def is_wallet_blocked(wallet_address: str) -> bool:
    """
    Returns True if `wallet_address` is blocked on Algorand mainnet OR VOI mainnet.

    Both networks are checked in parallel. Fail-open per network — an unreachable
    contract never blocks a legitimate payment. A wallet blocked on either network
    is treated as blocked on both (same key space; cross-network sanction evasion
    is not permitted).
    """
    if not algosdk.encoding.is_valid_address(wallet_address):
        return False

    cache = _get_cache()
    cached = cache.get(wallet_address)
    if cached is not None:
        return cached

    algo_app_id = int(getattr(settings, "BLOCKLIST_APP_ID", 0))
    voi_app_id = int(getattr(settings, "BLOCKLIST_VOI_APP_ID", 0))

    if not algo_app_id and not voi_app_id:
        return False

    algo_url = getattr(settings, "BLOCKLIST_ALGOD_URL", _DEFAULT_ALGOD_ALGORAND).rstrip("/")
    voi_url = getattr(settings, "BLOCKLIST_VOI_ALGOD_URL", _DEFAULT_ALGOD_VOI).rstrip("/")

    try:
        async with httpx.AsyncClient(timeout=4.0) as http:
            checks = []
            if algo_app_id:
                checks.append(_check_one_network(http, wallet_address, algo_app_id, algo_url, "algorand"))
            if voi_app_id:
                checks.append(_check_one_network(http, wallet_address, voi_app_id, voi_url, "voi"))

            results = await asyncio.gather(*checks)

        blocked = any(results)
        cache.set(wallet_address, blocked)
        return blocked
    except Exception:
        logger.exception(
            "on_chain_blocklist.is_wallet_blocked_error",
            extra={"wallet": wallet_address[:8] + "..."},
        )
        return False


async def get_wallet_vote_counts(
    wallet_address: str,
    network: str = "algorand",
) -> dict:
    """
    Returns {"block_votes": N, "unblock_votes": N} for a wallet on the
    given network ("algorand" or "voi"). Used by admin/audit endpoints.
    """
    if network == "voi":
        app_id = int(getattr(settings, "BLOCKLIST_VOI_APP_ID", 0))
        algod_url = getattr(settings, "BLOCKLIST_VOI_ALGOD_URL", _DEFAULT_ALGOD_VOI).rstrip("/")
    else:
        app_id = int(getattr(settings, "BLOCKLIST_APP_ID", 0))
        algod_url = getattr(settings, "BLOCKLIST_ALGOD_URL", _DEFAULT_ALGOD_ALGORAND).rstrip("/")

    if not app_id or not algosdk.encoding.is_valid_address(wallet_address):
        return {"block_votes": 0, "unblock_votes": 0, "network": network}

    try:
        async with httpx.AsyncClient(timeout=4.0) as http:
            bv_raw, uv_raw = await asyncio.gather(
                _read_box(http, algod_url, app_id, _box_name_b64(_BLOCK_VOTES_PREFIX, wallet_address)),
                _read_box(http, algod_url, app_id, _box_name_b64(_UNBLOCK_VOTES_PREFIX, wallet_address)),
            )
        return {
            "block_votes": _popcount(_decode_uint64(bv_raw)),
            "unblock_votes": _popcount(_decode_uint64(uv_raw)),
            "network": network,
        }
    except Exception:
        logger.exception("on_chain_blocklist.get_vote_counts_error", extra={"network": network})
        return {"block_votes": 0, "unblock_votes": 0, "network": network}


def invalidate_cache(wallet_address: str) -> None:
    """
    Evict a wallet from the local cache. Call after a node runner client
    confirms that a vote pushed a wallet's block status over the threshold,
    so the next gateway check reflects the new state immediately.
    """
    _get_cache().invalidate(wallet_address)

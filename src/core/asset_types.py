"""Asset-type helpers shared by APIs, collectors, and the frontend contract."""

from __future__ import annotations


ASSET_TYPE_SECURITY = "security"
ASSET_TYPE_FUND = "fund"
ASSET_TYPE_UNKNOWN = "unknown"

VALID_ASSET_TYPES = {
    ASSET_TYPE_SECURITY,
    ASSET_TYPE_FUND,
    ASSET_TYPE_UNKNOWN,
}


def normalize_asset_type(value: str | None, *, default: str = ASSET_TYPE_SECURITY) -> str:
    """Return a supported asset type without leaking arbitrary client values."""
    normalized = str(value or "").strip().lower()
    return normalized if normalized in VALID_ASSET_TYPES else default


def supports_kline(asset_type: str | None) -> bool:
    """Only exchange-traded securities use the stock OHLCV/K-line pipeline."""
    return normalize_asset_type(asset_type, default=ASSET_TYPE_UNKNOWN) == ASSET_TYPE_SECURITY

from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.collectors.kline_collector import KlineCollector
from src.core.asset_types import ASSET_TYPE_SECURITY, normalize_asset_type, supports_kline
from src.models.market import MarketCode
from src.web.database import get_db
from src.web.models import Stock

router = APIRouter()


class KlineItem(BaseModel):
    symbol: str = Field(..., description="股票代码")
    market: str = Field(..., description="市场: CN/HK/US")
    days: int | None = Field(default=60, description="K线天数")
    interval: str | None = Field(default="1d", description="周期: 1d/1w/1m")


class KlineBatchRequest(BaseModel):
    items: list[KlineItem]


class KlineSummaryItem(BaseModel):
    symbol: str = Field(..., description="股票代码")
    market: str = Field(..., description="市场: CN/HK/US")


class KlineSummaryBatchRequest(BaseModel):
    items: list[KlineSummaryItem]


def _parse_market(market: str) -> MarketCode:
    try:
        return MarketCode(market)
    except ValueError:
        raise HTTPException(400, f"不支持的市场: {market}")


def _serialize_klines(klines) -> list[dict]:
    return [
        {
            "date": k.date,
            "open": k.open,
            "close": k.close,
            "high": k.high,
            "low": k.low,
            "volume": k.volume,
        }
        for k in klines
    ]


def _asset_type_for_symbol(db: Session, symbol: str, market: str) -> str:
    row = (
        db.query(Stock.asset_type)
        .filter(Stock.symbol == symbol, Stock.market == market)
        .first()
    )
    if not row:
        return ASSET_TYPE_SECURITY
    return normalize_asset_type(row[0], default="unknown")


def _aggregate_klines(klines, interval: str) -> list:
    """Aggregate daily klines to week/month."""

    iv = (interval or "1d").lower()
    if iv in ("1d", "day", "d"):
        return klines
    if iv not in ("1w", "1m", "week", "month", "w", "m"):
        return klines

    parsed = []
    for k in klines or []:
        try:
            dt = datetime.strptime(k.date, "%Y-%m-%d")
        except Exception:
            continue
        parsed.append((dt, k))

    parsed.sort(key=lambda x: x[0])
    buckets: dict[str, list] = {}
    for dt, k in parsed:
        if iv in ("1w", "week", "w"):
            y, w, _ = dt.isocalendar()
            key = f"{y:04d}-W{w:02d}"
        else:
            key = f"{dt.year:04d}-{dt.month:02d}"
        buckets.setdefault(key, []).append((dt, k))

    out = []
    for _, items in buckets.items():
        items.sort(key=lambda x: x[0])
        first = items[0][1]
        last = items[-1][1]
        high = max(it[1].high for it in items)
        low = min(it[1].low for it in items)
        vol = sum(it[1].volume for it in items)
        out.append(
            type(first)(
                date=items[-1][0].strftime("%Y-%m-%d"),
                open=first.open,
                close=last.close,
                high=high,
                low=low,
                volume=vol,
            )
        )
    out.sort(key=lambda k: k.date)
    return out


@router.get("/{symbol}")
def get_klines(
    symbol: str,
    market: str = "CN",
    days: int = 60,
    interval: str = "1d",
    db: Session = Depends(get_db),
):
    """获取单只股票K线数据"""
    market_code = _parse_market(market)
    asset_type = _asset_type_for_symbol(db, symbol, market_code.value)
    if not supports_kline(asset_type):
        return {
            "symbol": symbol,
            "market": market_code.value,
            "asset_type": asset_type,
            "days": days,
            "interval": interval,
            "klines": [],
            "unsupported_reason": "fund_nav_only",
        }
    collector = KlineCollector(market_code)
    klines = collector.get_klines(symbol, days=days)
    klines = _aggregate_klines(klines, interval)
    return {
        "symbol": symbol,
        "market": market_code.value,
        "asset_type": asset_type,
        "days": days,
        "interval": interval,
        "klines": _serialize_klines(klines),
    }


@router.post("/batch")
def get_klines_batch(payload: KlineBatchRequest, db: Session = Depends(get_db)):
    """批量获取K线数据"""
    if not payload.items:
        return []

    results = []
    for item in payload.items:
        market_code = _parse_market(item.market)
        asset_type = _asset_type_for_symbol(db, item.symbol, market_code.value)
        days = item.days or 60
        interval = item.interval or "1d"
        if supports_kline(asset_type):
            collector = KlineCollector(market_code)
            klines = collector.get_klines(item.symbol, days=days)
            klines = _aggregate_klines(klines, interval)
        else:
            klines = []
        results.append(
            {
                "symbol": item.symbol,
                "market": market_code.value,
                "asset_type": asset_type,
                "days": days,
                "interval": interval,
                "klines": _serialize_klines(klines),
                "unsupported_reason": (
                    None if supports_kline(asset_type) else "fund_nav_only"
                ),
            }
        )

    return results


@router.get("/{symbol}/summary")
def get_kline_summary(
    symbol: str,
    market: str = "CN",
    db: Session = Depends(get_db),
):
    """获取单只股票K线摘要"""
    market_code = _parse_market(market)
    asset_type = _asset_type_for_symbol(db, symbol, market_code.value)
    if not supports_kline(asset_type):
        return {
            "symbol": symbol,
            "market": market_code.value,
            "asset_type": asset_type,
            "summary": None,
            "unsupported_reason": "fund_nav_only",
        }
    collector = KlineCollector(market_code)
    summary = collector.get_kline_summary(symbol)
    return {
        "symbol": symbol,
        "market": market_code.value,
        "asset_type": asset_type,
        "summary": summary,
    }


@router.post("/summary/batch")
def get_kline_summary_batch(
    payload: KlineSummaryBatchRequest,
    db: Session = Depends(get_db),
):
    """批量获取K线摘要"""
    if not payload.items:
        return []

    results = []
    for item in payload.items:
        market_code = _parse_market(item.market)
        asset_type = _asset_type_for_symbol(db, item.symbol, market_code.value)
        if supports_kline(asset_type):
            collector = KlineCollector(market_code)
            summary = collector.get_kline_summary(item.symbol)
        else:
            summary = None
        results.append(
            {
                "symbol": item.symbol,
                "market": market_code.value,
                "asset_type": asset_type,
                "summary": summary,
                "unsupported_reason": (
                    None if supports_kline(asset_type) else "fund_nav_only"
                ),
            }
        )

    return results

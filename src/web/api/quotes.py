import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.core.providers import ProviderRequest, get_quote_orchestrator
from src.core.asset_types import ASSET_TYPE_FUND, ASSET_TYPE_SECURITY, normalize_asset_type
from src.models.market import MarketCode

router = APIRouter()
logger = logging.getLogger(__name__)


class QuoteItem(BaseModel):
    symbol: str = Field(..., description="股票代码")
    market: str = Field(..., description="市场: CN/HK/US")
    asset_type: str = Field(default=ASSET_TYPE_SECURITY, description="security/fund/unknown")


class QuoteBatchRequest(BaseModel):
    items: list[QuoteItem]


def _parse_market(market: str) -> MarketCode:
    try:
        return MarketCode(market)
    except ValueError:
        raise HTTPException(400, f"不支持的市场: {market}")


def _quote_to_response(
    symbol: str,
    market: MarketCode,
    quote: dict | None,
    asset_type: str = ASSET_TYPE_SECURITY,
) -> dict:
    normalized_type = normalize_asset_type(asset_type)
    if not quote:
        return {
            "symbol": symbol,
            "market": market.value,
            "asset_type": normalized_type,
            "name": None,
            "current_price": None,
            "change_pct": None,
            "change_amount": None,
            "prev_close": None,
            "open_price": None,
            "high_price": None,
            "low_price": None,
            "volume": None,
            "turnover": None,
            "turnover_rate": None,
            "pe_ratio": None,
            "total_market_value": None,
            "circulating_market_value": None,
        }

    return {
        "symbol": symbol,
        "market": market.value,
        "asset_type": normalized_type,
        "name": quote.get("name"),
        "current_price": quote.get("current_price"),
        "change_pct": quote.get("change_pct"),
        "change_amount": quote.get("change_amount"),
        "prev_close": quote.get("prev_close"),
        "open_price": quote.get("open_price"),
        "high_price": quote.get("high_price"),
        "low_price": quote.get("low_price"),
        "volume": quote.get("volume"),
        "turnover": quote.get("turnover"),
        "turnover_rate": quote.get("turnover_rate"),
        "pe_ratio": quote.get("pe_ratio"),
        "total_market_value": quote.get("total_market_value"),
        "circulating_market_value": quote.get("circulating_market_value"),
    }


def _unsupported_quote_response(
    symbol: str,
    market: str,
    asset_type: str = ASSET_TYPE_SECURITY,
) -> dict:
    """批量行情中的单项失败响应。

    批量接口不应因为一个无效市场中断其他标的的报价，因此保留原始
    symbol/market，并为该项返回空行情与可诊断的 error 字段。
    """
    return {
        "symbol": symbol,
        "market": market,
        "asset_type": normalize_asset_type(asset_type),
        "name": None,
        "current_price": None,
        "change_pct": None,
        "change_amount": None,
        "prev_close": None,
        "open_price": None,
        "high_price": None,
        "low_price": None,
        "volume": None,
        "turnover": None,
        "turnover_rate": None,
        "pe_ratio": None,
        "total_market_value": None,
        "circulating_market_value": None,
        "error": f"不支持的市场: {market}",
    }


@router.get("/{symbol}")
async def get_quote(
    symbol: str,
    market: str = "CN",
    asset_type: str = ASSET_TYPE_SECURITY,
):
    """获取单只股票实时行情"""
    market_code = _parse_market(market)
    normalized_type = normalize_asset_type(asset_type)
    if normalized_type == ASSET_TYPE_FUND:
        if market_code != MarketCode.CN:
            raise HTTPException(400, "场外基金净值目前仅支持 CN 市场")
        from src.collectors.akshare_collector import _fetch_eastmoney_fund_quotes

        items = await asyncio.to_thread(_fetch_eastmoney_fund_quotes, [symbol])
        quote = next((item for item in items if item.get("symbol") == symbol), None)
        if not quote:
            raise HTTPException(404, "基金净值不存在")
        return _quote_to_response(symbol, market_code, quote, normalized_type)

    orch = get_quote_orchestrator()
    resp = await orch.fetch(
        ProviderRequest(symbols=(symbol,), market=market_code.value)
    )
    if not resp.success or resp.is_empty:
        raise HTTPException(404, "行情不存在")
    quote_map = {item.get("symbol"): item for item in resp.data}
    quote = quote_map.get(symbol)
    if not quote:
        raise HTTPException(404, "行情不存在")
    return _quote_to_response(symbol, market_code, quote, normalized_type)


@router.post("/batch")
async def get_quotes_batch(payload: QuoteBatchRequest):
    """批量获取股票实时行情，单项失败不中断其他标的。"""
    if not payload.items:
        return []

    market_items: dict[MarketCode, list[str]] = {}
    fund_symbols: list[str] = []
    parsed_items: list[tuple[MarketCode | None, str]] = []
    for item in payload.items:
        asset_type = normalize_asset_type(item.asset_type)
        try:
            market_code = MarketCode(item.market)
        except ValueError:
            parsed_items.append((None, asset_type))
            logger.warning(f"跳过不支持的行情标的: {item.market}:{item.symbol}")
            continue
        parsed_items.append((market_code, asset_type))
        if asset_type == ASSET_TYPE_FUND:
            if market_code == MarketCode.CN:
                fund_symbols.append(item.symbol)
            continue
        market_items.setdefault(market_code, []).append(item.symbol)

    orch = get_quote_orchestrator()
    quotes_by_market: dict[MarketCode, dict[str, dict]] = {}
    for market_code, symbols in market_items.items():
        try:
            resp = await orch.fetch(
                ProviderRequest(symbols=tuple(symbols), market=market_code.value)
            )
        except Exception as e:
            logger.warning(
                f"获取 {market_code.value} 批量行情失败，继续其他市场: {e}"
            )
            quotes_by_market[market_code] = {}
            continue
        if resp.success and resp.data:
            quotes_by_market[market_code] = {item.get("symbol"): item for item in resp.data}
        else:
            quotes_by_market[market_code] = {}

        # 场外基金腾讯不支持，走东方财富单位净值兜底
        if market_code == MarketCode.CN:
            cn_quotes = quotes_by_market.get(MarketCode.CN, {})
            missing = [s for s in symbols if s not in cn_quotes]
            if missing:
                try:
                    from src.collectors.akshare_collector import _fetch_eastmoney_fund_quotes
                    fund_items = await asyncio.to_thread(_fetch_eastmoney_fund_quotes, missing)
                    for item in fund_items:
                        cn_quotes[item["symbol"]] = item
                except Exception as e:
                    logger.warning(f"东方财富基金净值获取失败: {e}")

    if fund_symbols:
        cn_quotes = quotes_by_market.setdefault(MarketCode.CN, {})
        try:
            from src.collectors.akshare_collector import _fetch_eastmoney_fund_quotes

            fund_quotes = await asyncio.to_thread(
                _fetch_eastmoney_fund_quotes,
                list(dict.fromkeys(fund_symbols)),
            )
            for quote in fund_quotes:
                cn_quotes[quote["symbol"]] = quote
        except Exception as e:
            logger.warning(f"场外基金净值批量获取失败: {e}")

    results = []
    for item, (market_code, asset_type) in zip(payload.items, parsed_items):
        if market_code is None:
            results.append(
                _unsupported_quote_response(item.symbol, item.market, asset_type)
            )
            continue
        quote = quotes_by_market.get(market_code, {}).get(item.symbol)
        results.append(_quote_to_response(item.symbol, market_code, quote, asset_type))

    return results

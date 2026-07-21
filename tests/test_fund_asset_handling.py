"""Regression tests for OTC-fund valuation and K-line isolation."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.collectors import akshare_collector
from src.core.asset_types import ASSET_TYPE_FUND, ASSET_TYPE_SECURITY
from src.web import models as M
from src.web.api import accounts as accounts_api
from src.web.api import klines as klines_api
from src.web.database import Base
from src.web.stock_list import (
    asset_type_from_search_classify,
    infer_asset_type_from_catalog,
)


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def test_eastmoney_otc_fund_is_classified_separately():
    """OTCFUND must not be mixed with exchange-traded Fund results."""
    assert asset_type_from_search_classify("OTCFUND") == ASSET_TYPE_FUND
    assert asset_type_from_search_classify("Fund") == ASSET_TYPE_SECURITY
    assert asset_type_from_search_classify("AStock") == ASSET_TYPE_SECURITY


def test_ambiguous_lof_catalog_uses_exact_name():
    """The same LOF code can represent exchange trading or OTC subscription."""
    catalog = [
        {
            "symbol": "161017",
            "name": "500增强LOF",
            "market": "CN",
            "asset_type": ASSET_TYPE_SECURITY,
        },
        {
            "symbol": "161017",
            "name": "富国中证500指数增强(LOF)A",
            "market": "CN",
            "asset_type": ASSET_TYPE_FUND,
        },
    ]

    assert (
        infer_asset_type_from_catalog(
            "161017", "富国中证500指数增强(LOF)A", "CN", catalog
        )
        == ASSET_TYPE_FUND
    )
    assert (
        infer_asset_type_from_catalog("161017", "500增强LOF", "CN", catalog)
        == ASSET_TYPE_SECURITY
    )


def test_missing_quote_is_excluded_from_portfolio_pnl(db, monkeypatch):
    """A missing quote must not be treated as a price of zero or a -100% loss."""
    account = M.Account(name="test", available_funds=0, enabled=True)
    priced = M.Stock(
        symbol="600519",
        name="priced",
        market="CN",
        asset_type=ASSET_TYPE_SECURITY,
    )
    missing = M.Stock(
        symbol="003156",
        name="missing",
        market="CN",
        asset_type=ASSET_TYPE_FUND,
    )
    db.add_all([account, priced, missing])
    db.flush()
    db.add_all(
        [
            M.Position(
                account_id=account.id,
                stock_id=priced.id,
                cost_price=10,
                quantity=100,
            ),
            M.Position(
                account_id=account.id,
                stock_id=missing.id,
                cost_price=20,
                quantity=100,
            ),
        ]
    )
    db.commit()

    monkeypatch.setattr(
        accounts_api,
        "_fetch_quotes_for_stocks",
        lambda stocks: {
            "600519": {
                "current_price": 11,
                "change_pct": 0,
                "prev_close": 11,
            }
        },
    )
    monkeypatch.setattr(accounts_api, "get_hkd_cny_rate", lambda: 1.0)
    monkeypatch.setattr(accounts_api, "get_usd_cny_rate", lambda: 1.0)

    result = accounts_api.get_portfolio_summary(include_quotes=True, db=db)

    assert result["total"]["total_cost"] == 3000
    assert result["total"]["priced_cost"] == 1000
    assert result["total"]["unpriced_cost"] == 2000
    assert result["total"]["unpriced_positions"] == 1
    assert result["total"]["valuation_complete"] is False
    assert result["total"]["total_market_value"] == 1100
    assert result["total"]["total_pnl"] == 100
    assert result["total"]["total_pnl_pct"] == 10

    missing_position = next(
        p
        for p in result["accounts"][0]["positions"]
        if p["symbol"] == "003156"
    )
    assert missing_position["market_value"] is None
    assert missing_position["pnl"] is None


def test_fund_summary_does_not_construct_kline_collector(db, monkeypatch):
    """OTC funds return a neutral response without touching stock K-line providers."""
    db.add(
        M.Stock(
            symbol="003156",
            name="招商招悦纯债A",
            market="CN",
            asset_type=ASSET_TYPE_FUND,
        )
    )
    db.commit()

    def fail_if_called(*args, **kwargs):
        raise AssertionError("KlineCollector must not be constructed for an OTC fund")

    monkeypatch.setattr(klines_api, "KlineCollector", fail_if_called)

    result = klines_api.get_kline_summary("003156", market="CN", db=db)

    assert result["summary"] is None
    assert result["asset_type"] == ASSET_TYPE_FUND
    assert result["unsupported_reason"] == "fund_nav_only"


def test_fund_batch_does_not_construct_kline_collector(db, monkeypatch):
    """Batch K-line refresh must also stop before constructing a stock collector."""
    db.add(
        M.Stock(
            symbol="003156",
            name="招商招悦纯债A",
            market="CN",
            asset_type=ASSET_TYPE_FUND,
        )
    )
    db.commit()

    def fail_if_called(*args, **kwargs):
        raise AssertionError("KlineCollector must not be constructed for an OTC fund")

    monkeypatch.setattr(klines_api, "KlineCollector", fail_if_called)

    result = klines_api.get_klines_batch(
        klines_api.KlineBatchRequest(
            items=[klines_api.KlineItem(symbol="003156", market="CN")]
        ),
        db=db,
    )

    assert result[0]["klines"] == []
    assert result[0]["asset_type"] == ASSET_TYPE_FUND
    assert result[0]["unsupported_reason"] == "fund_nav_only"


def test_fund_quote_path_skips_tencent_market_quote(monkeypatch):
    """Known OTC funds should go directly to the NAV provider."""
    fund = M.Stock(
        symbol="003156",
        name="招商招悦纯债A",
        market="CN",
        asset_type=ASSET_TYPE_FUND,
    )
    security = M.Stock(
        symbol="600519",
        name="贵州茅台",
        market="CN",
        asset_type=ASSET_TYPE_SECURITY,
    )
    requested_market_symbols: list[str] = []
    requested_fund_symbols: list[str] = []

    def fake_market(symbols):
        requested_market_symbols.extend(symbols)
        return [
            {
                "symbol": "600519",
                "current_price": 1500,
                "change_pct": 1,
                "prev_close": 1485,
            }
        ]

    def fake_funds(symbols):
        requested_fund_symbols.extend(symbols)
        return [
            {
                "symbol": "003156",
                "current_price": 1.2,
                "change_pct": 0,
                "prev_close": 1.2,
            }
        ]

    monkeypatch.setattr(accounts_api, "_fetch_tencent_quotes", fake_market)
    monkeypatch.setattr(
        "src.collectors.akshare_collector._fetch_eastmoney_fund_quotes",
        fake_funds,
    )

    result = accounts_api._fetch_quotes_for_stocks([fund, security])

    assert requested_market_symbols == ["sh600519"]
    assert requested_fund_symbols == ["003156"]
    assert set(result) == {"003156", "600519"}


def test_eastmoney_fund_nav_payload_uses_latest_two_values():
    """场外基金行情使用最新单位净值，并保留净值日期。"""
    result = akshare_collector._parse_eastmoney_fund_nav_payload(
        "003156",
        {
            "ErrCode": 0,
            "Data": {
                "LSJZList": [
                    {"FSRQ": "2026-07-21", "DWJZ": "1.1839", "JZZZL": "0.00"},
                    {"FSRQ": "2026-07-20", "DWJZ": "1.1837", "JZZZL": "-0.02"},
                ]
            },
        },
    )

    assert result is not None
    assert result["symbol"] == "003156"
    assert result["current_price"] == 1.1839
    assert result["prev_close"] == 1.1837
    assert result["change_amount"] == 0.0002
    assert result["change_pct"] == 0.0
    assert result["quote_date"] == "2026-07-21"
    assert result["asset_type"] == ASSET_TYPE_FUND


def test_eastmoney_fund_nav_payload_calculates_missing_change_pct():
    """上游未给涨跌幅时，使用相邻两期单位净值计算。"""
    result = akshare_collector._parse_eastmoney_fund_nav_payload(
        "006327",
        {
            "ErrCode": 0,
            "Data": {
                "LSJZList": [
                    {"FSRQ": "2026-07-20", "DWJZ": "0.9011", "JZZZL": ""},
                    {"FSRQ": "2026-07-17", "DWJZ": "0.8736", "JZZZL": "-3.36"},
                ]
            },
        },
    )

    assert result is not None
    assert result["change_pct"] == 3.15


def test_eastmoney_fund_nav_payload_rejects_empty_response():
    assert akshare_collector._parse_eastmoney_fund_nav_payload(
        "003156", {"ErrCode": 0, "Data": {"LSJZList": []}}
    ) is None


def test_fund_quote_fetch_uses_current_eastmoney_nav_api(monkeypatch):
    """基金净值不再请求已经返回 404 页面内容的旧 fundgz 接口。"""
    calls = []

    def fake_market_get(url, **kwargs):
        calls.append((url, kwargs))
        return {
            "ErrCode": 0,
            "Data": {
                "LSJZList": [
                    {"FSRQ": "2026-07-21", "DWJZ": "1.2000", "JZZZL": "0.84"},
                    {"FSRQ": "2026-07-20", "DWJZ": "1.1900", "JZZZL": "0.00"},
                ]
            },
        }

    akshare_collector._QUOTE_CACHE.clear()
    monkeypatch.setattr(akshare_collector, "market_get", fake_market_get)

    result = akshare_collector._fetch_eastmoney_fund_quotes(["009999"])

    assert result[0]["current_price"] == 1.2
    assert len(calls) == 1
    assert calls[0][0] == akshare_collector.EASTMONEY_FUND_NAV_URL
    assert calls[0][1]["params"]["fundCode"] == "009999"
    assert calls[0][1]["parse"] == "json"

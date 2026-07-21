"""真实账户卖出、分红、现金流与收益率。"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.core.portfolio_ledger import (
    PortfolioLedgerError,
    calculate_year_performance,
    record_cash_flow,
    record_dividend,
    record_sell,
)
from src.web.database import Base
from src.web.models import (
    Account,
    PortfolioTransaction,
    PortfolioValuationSnapshot,
    Position,
    Stock,
)


@pytest.fixture
def ledger_db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    account = Account(name="测试账户", available_funds=100.0, enabled=True)
    stock = Stock(symbol="600900", name="长江电力", market="CN", asset_type="security")
    db.add_all([account, stock])
    db.flush()
    position = Position(
        account_id=account.id,
        stock_id=stock.id,
        cost_price=10.0,
        quantity=100,
        invested_amount=1000.0,
    )
    db.add(position)
    db.commit()

    from src.web.api import accounts as accounts_api

    monkeypatch.setattr(
        accounts_api,
        "_fetch_quotes_for_stocks",
        lambda stocks: {
            s.symbol: {"symbol": s.symbol, "current_price": 10.0, "change_pct": 0.0}
            for s in stocks
        },
    )
    monkeypatch.setattr(accounts_api, "get_hkd_cny_rate", lambda: 0.92)
    monkeypatch.setattr(accounts_api, "get_usd_cny_rate", lambda: 7.25)

    yield db, account.id, stock.id, position.id
    db.close()


def test_partial_sell_updates_cash_position_and_ledger(ledger_db):
    """部分卖出同时更新现金、持仓和已实现收益，并保留前后估值。"""
    db, account_id, _stock_id, position_id = ledger_db
    row, remaining, repeated = record_sell(
        db,
        position_id=position_id,
        quantity=40,
        unit_price=Decimal("12"),
        net_amount=None,
        currency="CNY",
        occurred_at=datetime.now(timezone.utc),
        idempotency_key="sell-1",
    )

    assert repeated is False
    assert remaining == 60
    assert float(row.realized_pnl_base) == 80.0
    assert db.get(Account, account_id).available_funds == 580.0
    position = db.get(Position, position_id)
    assert position.quantity == 60
    assert position.invested_amount == 600.0
    assert db.query(PortfolioValuationSnapshot).count() == 2


def test_full_sell_closes_position_but_keeps_history(ledger_db):
    """全部卖出移除当前持仓，但卖出流水仍然存在。"""
    db, _account_id, _stock_id, position_id = ledger_db
    row, remaining, _ = record_sell(
        db,
        position_id=position_id,
        quantity=100,
        unit_price=Decimal("11"),
        net_amount=Decimal("1098"),
        currency="CNY",
        occurred_at=datetime.now(timezone.utc),
    )
    assert remaining == 0
    assert db.get(Position, position_id) is None
    assert db.get(PortfolioTransaction, row.id) is not None
    assert float(row.realized_pnl_base) == 98.0
    snapshots = (
        db.query(PortfolioValuationSnapshot)
        .order_by(PortfolioValuationSnapshot.valued_at)
        .all()
    )
    assert [float(item.total_value_base) for item in snapshots] == [1100.0, 1198.0]


def test_sell_rejects_more_than_current_holding(ledger_db):
    """卖出数量不能超过当前持仓。"""
    db, _account_id, _stock_id, position_id = ledger_db
    with pytest.raises(PortfolioLedgerError, match="不能超过"):
        record_sell(
            db,
            position_id=position_id,
            quantity=101,
            unit_price=Decimal("10"),
            net_amount=None,
            currency="CNY",
            occurred_at=datetime.now(timezone.utc),
        )


def test_dividend_is_income_without_changing_position(ledger_db):
    """现金分红增加现金和收益，但不改变持仓数量与成本。"""
    db, account_id, stock_id, position_id = ledger_db
    row, _ = record_dividend(
        db,
        account_id=account_id,
        stock_id=stock_id,
        amount=Decimal("50"),
        currency="CNY",
        occurred_at=datetime.now(timezone.utc),
    )
    assert db.get(Account, account_id).available_funds == 150.0
    assert db.get(Position, position_id).quantity == 100
    assert db.get(Position, position_id).cost_price == 10.0
    assert row.event_type == "DIVIDEND"
    result = calculate_year_performance(db, year=datetime.now().year, account_id=account_id)
    assert result["dividend_income"] == 50.0
    assert result["profit"] == 50.0


def test_deposit_does_not_create_twr_profit(ledger_db):
    """入金增加总资产，但时间加权收益率应保持为零。"""
    db, account_id, _stock_id, _position_id = ledger_db
    record_cash_flow(
        db,
        account_id=account_id,
        event_type="DEPOSIT",
        amount=Decimal("500"),
        currency="CNY",
        occurred_at=datetime.now(timezone.utc),
    )
    result = calculate_year_performance(
        db,
        year=datetime.now().year,
        account_id=account_id,
    )
    assert result["empty"] is False
    assert result["deposits"] == 500.0
    assert abs(result["profit"]) < 0.01
    assert abs(result["twr_pct"]) < 0.01

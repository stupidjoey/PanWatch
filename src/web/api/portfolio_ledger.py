"""真实账户投资流水与年度收益 API。"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.config import Settings
from src.core.portfolio_ledger import (
    EVENT_DEPOSIT,
    EVENT_DIVIDEND,
    EVENT_SELL,
    EVENT_WITHDRAWAL,
    PortfolioLedgerError,
    available_performance_years,
    calculate_year_performance,
    capture_all_valuations,
    record_cash_flow,
    record_dividend,
    record_sell,
    serialize_transaction,
)
from src.web.database import get_db
from src.web.models import PortfolioTransaction

router = APIRouter()


class SellRequest(BaseModel):
    position_id: int
    quantity: int = Field(gt=0)
    unit_price: Decimal = Field(gt=0)
    net_amount: Decimal | None = Field(default=None, gt=0)
    currency: str | None = None
    occurred_at: datetime | None = None
    note: str = ""
    idempotency_key: str | None = None


class DividendRequest(BaseModel):
    account_id: int
    stock_id: int
    amount: Decimal = Field(gt=0)
    currency: str | None = None
    occurred_at: datetime | None = None
    note: str = ""
    idempotency_key: str | None = None


class CashFlowRequest(BaseModel):
    account_id: int
    event_type: Literal["DEPOSIT", "WITHDRAWAL"]
    amount: Decimal = Field(gt=0)
    currency: str = "CNY"
    occurred_at: datetime | None = None
    note: str = ""
    idempotency_key: str | None = None


def _run_write(fn, db: Session, **kwargs):
    try:
        return fn(db, **kwargs)
    except PortfolioLedgerError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    except Exception:
        db.rollback()
        raise


@router.post("/transactions/sell")
def sell_position(data: SellRequest, db: Session = Depends(get_db)):
    row, remaining, idempotent = _run_write(
        record_sell,
        db,
        position_id=data.position_id,
        quantity=data.quantity,
        unit_price=data.unit_price,
        net_amount=data.net_amount,
        currency=data.currency,
        occurred_at=data.occurred_at,
        note=data.note,
        idempotency_key=data.idempotency_key,
    )
    return {
        "transaction": serialize_transaction(row),
        "remaining_quantity": remaining,
        "position_closed": remaining == 0,
        "idempotent": idempotent,
    }


@router.post("/transactions/dividend")
def add_dividend(data: DividendRequest, db: Session = Depends(get_db)):
    row, idempotent = _run_write(
        record_dividend,
        db,
        account_id=data.account_id,
        stock_id=data.stock_id,
        amount=data.amount,
        currency=data.currency,
        occurred_at=data.occurred_at,
        note=data.note,
        idempotency_key=data.idempotency_key,
    )
    return {"transaction": serialize_transaction(row), "idempotent": idempotent}


@router.post("/transactions/cash-flow")
def add_cash_flow(data: CashFlowRequest, db: Session = Depends(get_db)):
    row, idempotent = _run_write(
        record_cash_flow,
        db,
        account_id=data.account_id,
        event_type=data.event_type,
        amount=data.amount,
        currency=data.currency,
        occurred_at=data.occurred_at,
        note=data.note,
        idempotency_key=data.idempotency_key,
    )
    return {"transaction": serialize_transaction(row), "idempotent": idempotent}


@router.get("/transactions")
def list_transactions(
    account_id: int | None = None,
    stock_id: int | None = None,
    event_type: str | None = None,
    year: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    query = db.query(PortfolioTransaction)
    if account_id is not None:
        query = query.filter(PortfolioTransaction.account_id == account_id)
    if stock_id is not None:
        query = query.filter(PortfolioTransaction.stock_id == stock_id)
    if event_type:
        normalized = event_type.upper()
        if normalized not in {EVENT_SELL, EVENT_DIVIDEND, EVENT_DEPOSIT, EVENT_WITHDRAWAL}:
            raise HTTPException(400, "不支持的流水类型")
        query = query.filter(PortfolioTransaction.event_type == normalized)
    if year is not None:
        tz = ZoneInfo(Settings().app_timezone)
        start = datetime(year, 1, 1, tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
        end = datetime(year + 1, 1, 1, tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
        query = query.filter(
            PortfolioTransaction.occurred_at >= start,
            PortfolioTransaction.occurred_at < end,
        )
    total = query.count()
    rows = (
        query.order_by(PortfolioTransaction.occurred_at.desc(), PortfolioTransaction.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {"total": total, "items": [serialize_transaction(row) for row in rows]}


@router.get("/performance/years")
def performance_years(db: Session = Depends(get_db)):
    return {"years": available_performance_years(db, Settings().app_timezone)}


@router.get("/performance")
def yearly_performance(
    year: int | None = None,
    account_id: int | None = None,
    db: Session = Depends(get_db),
):
    settings = Settings()
    tz = ZoneInfo(settings.app_timezone)
    selected_year = year or datetime.now(tz).year
    if selected_year < 1970 or selected_year > datetime.now(tz).year:
        raise HTTPException(400, "年份不合法")
    try:
        capture_all_valuations(db, kind="performance_view")
        db.commit()
        return calculate_year_performance(
            db,
            year=selected_year,
            account_id=account_id,
            app_timezone=settings.app_timezone,
        )
    except Exception:
        db.rollback()
        raise

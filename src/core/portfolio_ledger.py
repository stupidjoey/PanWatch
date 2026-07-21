"""真实账户投资流水、估值快照与收益率计算。"""

from __future__ import annotations

import logging
import math
import threading
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.web.models import (
    Account,
    PortfolioTransaction,
    PortfolioValuationSnapshot,
    Position,
    Stock,
)

logger = logging.getLogger(__name__)

EVENT_SELL = "SELL"
EVENT_DIVIDEND = "DIVIDEND"
EVENT_DEPOSIT = "DEPOSIT"
EVENT_WITHDRAWAL = "WITHDRAWAL"
EXTERNAL_EVENTS = {EVENT_DEPOSIT, EVENT_WITHDRAWAL}
SUPPORTED_CURRENCIES = {"CNY", "HKD", "USD"}

_WRITE_LOCK = threading.RLock()
_MONEY = Decimal("0.00000001")


class PortfolioLedgerError(ValueError):
    """用户可理解的账本校验错误。"""


def _d(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value or 0))


def _money(value) -> Decimal:
    return _d(value).quantize(_MONEY, rounding=ROUND_HALF_UP)


def _utc_naive(value: datetime | None = None) -> datetime:
    dt = value or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def default_currency_for_market(market: str) -> str:
    return "HKD" if market == "HK" else "USD" if market == "US" else "CNY"


def resolve_fx_rate(currency: str) -> Decimal:
    currency = (currency or "CNY").upper()
    if currency not in SUPPORTED_CURRENCIES:
        raise PortfolioLedgerError(f"暂不支持币种 {currency}")
    if currency == "CNY":
        return Decimal("1")

    # 延迟导入，避免 app 路由加载阶段出现循环依赖。
    from src.web.api.accounts import get_hkd_cny_rate, get_usd_cny_rate

    rate = get_hkd_cny_rate() if currency == "HKD" else get_usd_cny_rate()
    return _d(rate)


def serialize_transaction(row: PortfolioTransaction) -> dict:
    return {
        "id": row.id,
        "account_id": row.account_id,
        "account_name": row.account.name if row.account else "",
        "stock_id": row.stock_id,
        "event_type": row.event_type,
        "stock_symbol": row.stock_symbol or "",
        "stock_name": row.stock_name or "",
        "stock_market": row.stock_market or "",
        "quantity": float(row.quantity) if row.quantity is not None else None,
        "unit_price": float(row.unit_price) if row.unit_price is not None else None,
        "gross_amount": float(row.gross_amount or 0),
        "net_amount": float(row.net_amount or 0),
        "cash_delta_base": float(row.cash_delta_base or 0),
        "cost_basis_base": float(row.cost_basis_base or 0),
        "realized_pnl_base": float(row.realized_pnl_base or 0),
        "currency": row.currency or "CNY",
        "fx_rate_to_base": float(row.fx_rate_to_base or 1),
        "occurred_at": _iso_utc(row.occurred_at),
        "recorded_at": _iso_utc(row.recorded_at),
        "note": row.note or "",
    }


def capture_all_valuations(
    db: Session,
    *,
    kind: str,
    valued_at: datetime | None = None,
) -> str:
    """在同一时刻为全部启用账户写估值快照。

    行情缺失时使用持仓成本作为保守占位，同时标记 valuation_complete=False。
    """
    stamp = _utc_naive(valued_at)
    batch_id = uuid.uuid4().hex
    accounts = db.query(Account).filter(Account.enabled == True).order_by(Account.id).all()  # noqa: E712
    if not accounts:
        return batch_id

    account_ids = [account.id for account in accounts]
    positions = (
        db.query(Position)
        .filter(Position.account_id.in_(account_ids))
        .order_by(Position.account_id, Position.id)
        .all()
    )
    positions_by_account: dict[int, list[Position]] = defaultdict(list)
    for position in positions:
        positions_by_account[position.account_id].append(position)

    stock_ids = {position.stock_id for position in positions}
    stocks = db.query(Stock).filter(Stock.id.in_(stock_ids)).all() if stock_ids else []
    stock_map = {s.id: s for s in stocks}

    from src.web.api.accounts import (
        _fetch_quotes_for_stocks,
        get_hkd_cny_rate,
        get_usd_cny_rate,
    )

    quotes = _fetch_quotes_for_stocks(stocks) if stocks else {}
    rates = {
        "CN": Decimal("1"),
        "HK": _d(get_hkd_cny_rate()),
        "US": _d(get_usd_cny_rate()),
    }

    for account in accounts:
        securities = Decimal("0")
        missing = 0
        # 清仓删除 Position 后必须从数据库重新取有效持仓，不能依赖已加载的
        # relationship 缓存，否则 event_after 快照可能误计刚删除的持仓。
        for position in positions_by_account[account.id]:
            stock = stock_map.get(position.stock_id)
            if not stock:
                continue
            quote = quotes.get(stock.symbol) or {}
            current_price = quote.get("current_price")
            if current_price is None:
                current_price = position.cost_price
                missing += 1
            rate = rates.get(stock.market, Decimal("1"))
            securities += _d(current_price) * _d(position.quantity) * rate

        cash = _d(account.available_funds)
        total = cash + securities
        db.add(
            PortfolioValuationSnapshot(
                account_id=account.id,
                batch_id=batch_id,
                kind=kind,
                cash_value_base=_money(cash),
                securities_value_base=_money(securities),
                total_value_base=_money(total),
                valuation_complete=missing == 0,
                missing_price_count=missing,
                valued_at=stamp,
                created_at=stamp,
            )
        )
    db.flush()
    return batch_id


def capture_event_valuation_pair(
    db: Session,
    *,
    account_id: int,
    cash_before_base: Decimal,
    cash_delta_base: Decimal,
    securities_delta_base: Decimal = Decimal("0"),
    recorded_at: datetime,
) -> tuple[str, str]:
    """无网络地写入流水前后估值，确保保存操作能立即完成。

    流水期间沿用各账户最近一次行情估值，只对目标账户应用已知的现金和
    证券变化。实时/收盘价格仍由后台每日估值任务更新。
    """
    stamp = _utc_naive(recorded_at)
    before_stamp = stamp - timedelta(microseconds=1)
    after_stamp = stamp + timedelta(microseconds=1)
    before_batch = uuid.uuid4().hex
    after_batch = uuid.uuid4().hex
    accounts = (
        db.query(Account)
        .filter((Account.enabled == True) | (Account.id == account_id))  # noqa: E712
        .order_by(Account.id)
        .all()
    )

    fallback_rates = {
        "CN": Decimal("1"),
        "HK": Decimal("0.92"),
        "US": Decimal("7.25"),
    }
    for account in accounts:
        latest = (
            db.query(PortfolioValuationSnapshot)
            .filter(PortfolioValuationSnapshot.account_id == account.id)
            .order_by(
                PortfolioValuationSnapshot.valued_at.desc(),
                PortfolioValuationSnapshot.id.desc(),
            )
            .first()
        )
        if latest:
            cash = _d(latest.cash_value_base)
            securities = _d(latest.securities_value_base)
            complete = bool(latest.valuation_complete)
            missing = int(latest.missing_price_count or 0)
        else:
            positions = (
                db.query(Position)
                .filter(Position.account_id == account.id)
                .all()
            )
            stocks = {
                stock.id: stock
                for stock in db.query(Stock)
                .filter(Stock.id.in_({position.stock_id for position in positions}))
                .all()
            } if positions else {}
            cash = _d(account.available_funds)
            securities = sum(
                _d(position.cost_price)
                * _d(position.quantity)
                * fallback_rates.get(stocks[position.stock_id].market, Decimal("1"))
                for position in positions
                if position.stock_id in stocks
            )
            complete = not positions
            missing = len(positions)

        if account.id == account_id:
            # 账户现金是强事实；用它校准可能较旧的估值快照。
            cash = _d(cash_before_base)

        before_total = cash + securities
        db.add(
            PortfolioValuationSnapshot(
                account_id=account.id,
                batch_id=before_batch,
                kind="event_before",
                cash_value_base=_money(cash),
                securities_value_base=_money(securities),
                total_value_base=_money(before_total),
                valuation_complete=complete,
                missing_price_count=missing,
                valued_at=before_stamp,
                created_at=stamp,
            )
        )

        after_cash = cash
        after_securities = securities
        if account.id == account_id:
            after_cash += _d(cash_delta_base)
            after_securities = max(
                Decimal("0"),
                after_securities + _d(securities_delta_base),
            )
        db.add(
            PortfolioValuationSnapshot(
                account_id=account.id,
                batch_id=after_batch,
                kind="event_after",
                cash_value_base=_money(after_cash),
                securities_value_base=_money(after_securities),
                total_value_base=_money(after_cash + after_securities),
                valuation_complete=complete,
                missing_price_count=missing,
                valued_at=after_stamp,
                created_at=stamp,
            )
        )
    db.flush()
    return before_batch, after_batch


def _idempotent_result(
    db: Session,
    key: str | None,
    event_type: str,
) -> PortfolioTransaction | None:
    if not key:
        return None
    existing = (
        db.query(PortfolioTransaction)
        .filter(PortfolioTransaction.idempotency_key == key)
        .first()
    )
    if existing and existing.event_type != event_type:
        raise PortfolioLedgerError("该幂等键已用于其他类型的流水")
    return existing


def record_sell(
    db: Session,
    *,
    position_id: int,
    quantity: int,
    unit_price: Decimal,
    net_amount: Decimal | None,
    currency: str | None,
    occurred_at: datetime | None,
    note: str = "",
    idempotency_key: str | None = None,
) -> tuple[PortfolioTransaction, int, bool]:
    """记录部分/全部卖出；返回 (流水, 剩余数量, 是否幂等命中)。"""
    with _WRITE_LOCK:
        existing = _idempotent_result(db, idempotency_key, EVENT_SELL)
        if existing:
            remaining = (
                db.query(Position.quantity)
                .filter(Position.id == existing.position_id_snapshot)
                .scalar()
            )
            return existing, int(remaining or 0), True

        position = db.query(Position).filter(Position.id == position_id).first()
        if not position:
            raise PortfolioLedgerError("持仓不存在，可能已经清仓")
        if quantity <= 0:
            raise PortfolioLedgerError("卖出数量必须大于 0")
        if quantity > int(position.quantity or 0):
            raise PortfolioLedgerError(f"卖出数量不能超过当前持仓 {position.quantity}")
        price = _d(unit_price)
        if price <= 0:
            raise PortfolioLedgerError("成交价格必须大于 0")

        account = position.account
        stock = position.stock
        if not account or not stock:
            raise PortfolioLedgerError("持仓关联的账户或股票不存在")

        currency_code = (currency or default_currency_for_market(stock.market)).upper()
        fx_rate = resolve_fx_rate(currency_code)
        gross = _money(price * _d(quantity))
        actual_net = _money(net_amount if net_amount is not None else gross)
        if actual_net <= 0:
            raise PortfolioLedgerError("实际到账金额必须大于 0")

        recorded_at = _utc_naive()
        cash_before_base = _d(account.available_funds)
        cost_basis_base = _money(_d(position.cost_price) * _d(quantity) * fx_rate)
        net_base = _money(actual_net * fx_rate)
        gross_base = _money(gross * fx_rate)
        realized_base = _money(net_base - cost_basis_base)
        previous_quantity = int(position.quantity)
        remaining = previous_quantity - quantity

        capture_event_valuation_pair(
            db,
            account_id=account.id,
            cash_before_base=cash_before_base,
            cash_delta_base=net_base,
            securities_delta_base=-gross_base,
            recorded_at=recorded_at,
        )

        row = PortfolioTransaction(
            account_id=account.id,
            stock_id=stock.id,
            position_id_snapshot=position.id,
            event_type=EVENT_SELL,
            stock_symbol=stock.symbol,
            stock_name=stock.name,
            stock_market=stock.market,
            quantity=_d(quantity),
            unit_price=_money(price),
            gross_amount=gross,
            net_amount=actual_net,
            cash_delta_base=net_base,
            cost_basis_base=cost_basis_base,
            realized_pnl_base=realized_base,
            currency=currency_code,
            fx_rate_to_base=fx_rate,
            occurred_at=_utc_naive(occurred_at),
            recorded_at=recorded_at,
            idempotency_key=idempotency_key or None,
            note=(note or "").strip(),
        )
        db.add(row)
        account.available_funds = float(_money(_d(account.available_funds) + net_base))

        if remaining == 0:
            db.delete(position)
        else:
            position.quantity = remaining
            if position.invested_amount is not None and previous_quantity > 0:
                position.invested_amount = float(
                    _money(_d(position.invested_amount) * _d(remaining) / _d(previous_quantity))
                )

        db.flush()
        db.commit()
        db.refresh(row)
        logger.info(
            "记录真实账户卖出: account=%s stock=%s qty=%s price=%s remaining=%s",
            account.name,
            stock.symbol,
            quantity,
            price,
            remaining,
        )
        return row, remaining, False


def record_dividend(
    db: Session,
    *,
    account_id: int,
    stock_id: int,
    amount: Decimal,
    currency: str | None,
    occurred_at: datetime | None,
    note: str = "",
    idempotency_key: str | None = None,
) -> tuple[PortfolioTransaction, bool]:
    with _WRITE_LOCK:
        existing = _idempotent_result(db, idempotency_key, EVENT_DIVIDEND)
        if existing:
            return existing, True

        account = db.query(Account).filter(Account.id == account_id).first()
        stock = db.query(Stock).filter(Stock.id == stock_id).first()
        if not account:
            raise PortfolioLedgerError("账户不存在")
        if not stock:
            raise PortfolioLedgerError("股票不存在")
        actual = _money(amount)
        if actual <= 0:
            raise PortfolioLedgerError("分红到账金额必须大于 0")

        currency_code = (currency or default_currency_for_market(stock.market)).upper()
        fx_rate = resolve_fx_rate(currency_code)
        amount_base = _money(actual * fx_rate)
        recorded_at = _utc_naive()
        capture_event_valuation_pair(
            db,
            account_id=account.id,
            cash_before_base=_d(account.available_funds),
            cash_delta_base=amount_base,
            recorded_at=recorded_at,
        )
        row = PortfolioTransaction(
            account_id=account.id,
            stock_id=stock.id,
            event_type=EVENT_DIVIDEND,
            stock_symbol=stock.symbol,
            stock_name=stock.name,
            stock_market=stock.market,
            gross_amount=actual,
            net_amount=actual,
            cash_delta_base=amount_base,
            currency=currency_code,
            fx_rate_to_base=fx_rate,
            occurred_at=_utc_naive(occurred_at),
            recorded_at=recorded_at,
            idempotency_key=idempotency_key or None,
            note=(note or "").strip(),
        )
        db.add(row)
        account.available_funds = float(_money(_d(account.available_funds) + amount_base))
        db.flush()
        db.commit()
        db.refresh(row)
        logger.info(
            "记录真实账户分红: account=%s stock=%s amount=%s %s",
            account.name,
            stock.symbol,
            actual,
            currency_code,
        )
        return row, False


def record_cash_flow(
    db: Session,
    *,
    account_id: int,
    event_type: str,
    amount: Decimal,
    currency: str,
    occurred_at: datetime | None,
    note: str = "",
    idempotency_key: str | None = None,
) -> tuple[PortfolioTransaction, bool]:
    with _WRITE_LOCK:
        existing = _idempotent_result(db, idempotency_key, event_type.upper())
        if existing:
            return existing, True
        event = event_type.upper()
        if event not in EXTERNAL_EVENTS:
            raise PortfolioLedgerError("资金流水类型必须是 DEPOSIT 或 WITHDRAWAL")
        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            raise PortfolioLedgerError("账户不存在")
        actual = _money(amount)
        if actual <= 0:
            raise PortfolioLedgerError("金额必须大于 0")
        currency_code = (currency or "CNY").upper()
        fx_rate = resolve_fx_rate(currency_code)
        amount_base = _money(actual * fx_rate)
        delta = amount_base if event == EVENT_DEPOSIT else -amount_base
        if _d(account.available_funds) + delta < 0:
            raise PortfolioLedgerError("出金金额不能超过账户可用资金")

        recorded_at = _utc_naive()
        capture_event_valuation_pair(
            db,
            account_id=account.id,
            cash_before_base=_d(account.available_funds),
            cash_delta_base=delta,
            recorded_at=recorded_at,
        )
        row = PortfolioTransaction(
            account_id=account.id,
            event_type=event,
            gross_amount=actual,
            net_amount=actual,
            cash_delta_base=delta,
            currency=currency_code,
            fx_rate_to_base=fx_rate,
            occurred_at=_utc_naive(occurred_at),
            recorded_at=recorded_at,
            idempotency_key=idempotency_key or None,
            note=(note or "").strip(),
        )
        db.add(row)
        account.available_funds = float(_money(_d(account.available_funds) + delta))
        db.flush()
        db.commit()
        db.refresh(row)
        return row, False


def _xirr(cash_flows: list[tuple[datetime, float]]) -> float | None:
    """求不规则现金流 IRR；常规个人账户现金流通常只有一个有效根。"""
    if len(cash_flows) < 2 or not any(v < 0 for _, v in cash_flows) or not any(v > 0 for _, v in cash_flows):
        return None
    cash_flows = sorted(cash_flows, key=lambda x: x[0])
    d0 = cash_flows[0][0]

    def f(rate: float) -> float:
        return sum(v / ((1 + rate) ** ((d - d0).days / 365.0)) for d, v in cash_flows)

    def df(rate: float) -> float:
        out = 0.0
        for day, value in cash_flows:
            years = (day - d0).days / 365.0
            if years:
                out -= years * value / ((1 + rate) ** (years + 1))
        return out

    for guess in (0.1, 0.0, 0.5, -0.5, 1.0, 5.0):
        rate = guess
        for _ in range(100):
            if rate <= -0.999999:
                break
            value = f(rate)
            slope = df(rate)
            if abs(value) < 1e-7:
                return rate if math.isfinite(rate) else None
            if abs(slope) < 1e-12:
                break
            next_rate = rate - value / slope
            if not math.isfinite(next_rate) or next_rate <= -0.999999:
                break
            if abs(next_rate - rate) < 1e-10:
                return next_rate
            rate = next_rate
    return None


def _aggregate_snapshot_points(
    rows: list[PortfolioValuationSnapshot], account_id: int | None
) -> list[dict]:
    grouped: dict[str, list[PortfolioValuationSnapshot]] = defaultdict(list)
    for row in rows:
        if account_id is None or row.account_id == account_id:
            grouped[row.batch_id].append(row)
    points: list[dict] = []
    for batch_rows in grouped.values():
        if not batch_rows:
            continue
        points.append(
            {
                "valued_at": min(r.valued_at for r in batch_rows),
                "total": sum(float(r.total_value_base or 0) for r in batch_rows),
                "cash": sum(float(r.cash_value_base or 0) for r in batch_rows),
                "securities": sum(float(r.securities_value_base or 0) for r in batch_rows),
                "complete": all(bool(r.valuation_complete) for r in batch_rows),
                "missing": sum(int(r.missing_price_count or 0) for r in batch_rows),
            }
        )
    points.sort(key=lambda x: x["valued_at"])
    return points


def calculate_year_performance(
    db: Session,
    *,
    year: int,
    account_id: int | None = None,
    app_timezone: str = "Asia/Shanghai",
) -> dict:
    """基于账本与估值快照计算自然年（或当前 YTD）收益。"""
    tz = ZoneInfo(app_timezone)
    start_local = datetime(year, 1, 1, tzinfo=tz)
    end_local = datetime(year + 1, 1, 1, tzinfo=tz)
    now_local = datetime.now(tz)
    report_end_local = min(end_local, now_local)
    start_utc = _utc_naive(start_local)
    end_utc = _utc_naive(report_end_local)

    q = db.query(PortfolioValuationSnapshot).filter(
        PortfolioValuationSnapshot.valued_at < end_utc
    )
    if account_id is not None:
        q = q.filter(PortfolioValuationSnapshot.account_id == account_id)
    rows = q.order_by(PortfolioValuationSnapshot.valued_at.asc()).all()
    all_points = _aggregate_snapshot_points(rows, account_id)
    if not all_points:
        return {
            "year": year,
            "empty": True,
            "reason": "no_valuation",
            "message": "尚无资产估值快照，请刷新后再试",
        }

    before = [p for p in all_points if p["valued_at"] <= start_utc]
    within = [p for p in all_points if start_utc < p["valued_at"] < end_utc]
    points = ([before[-1]] if before else []) + within
    if not points:
        return {
            "year": year,
            "empty": True,
            "reason": "no_valuation_in_year",
            "message": f"{year} 年没有可用估值数据",
        }
    # 同一快照不要因为它恰好同时落入 before/within 而重复。
    deduped: list[dict] = []
    for point in points:
        if deduped and point["valued_at"] == deduped[-1]["valued_at"]:
            deduped[-1] = point
        else:
            deduped.append(point)
    points = deduped
    first, last = points[0], points[-1]

    # 资产快照是在“录入”流水的前后生成的。补录几天前发生的交易时，
    # occurred_at 可能早于首个快照，所以这里必须按 recorded_at 将现金变化
    # 与前后估值配对；occurred_at 仍用于流水列表和自然年归属。
    tx_query = db.query(PortfolioTransaction).filter(
        PortfolioTransaction.recorded_at >= first["valued_at"],
        PortfolioTransaction.recorded_at <= last["valued_at"],
    )
    if account_id is not None:
        tx_query = tx_query.filter(PortfolioTransaction.account_id == account_id)
    tx_rows = tx_query.order_by(PortfolioTransaction.occurred_at.asc()).all()

    net_external = sum(
        float(t.cash_delta_base or 0) for t in tx_rows if t.event_type in EXTERNAL_EVENTS
    )
    deposits = sum(
        float(t.cash_delta_base or 0) for t in tx_rows if t.event_type == EVENT_DEPOSIT
    )
    withdrawals = -sum(
        float(t.cash_delta_base or 0) for t in tx_rows if t.event_type == EVENT_WITHDRAWAL
    )
    dividends = sum(
        float(t.cash_delta_base or 0) for t in tx_rows if t.event_type == EVENT_DIVIDEND
    )
    realized = sum(
        float(t.realized_pnl_base or 0) for t in tx_rows if t.event_type == EVENT_SELL
    )

    start_value = float(first["total"])
    end_value = float(last["total"])
    profit = end_value - start_value - net_external
    days = max(0, (last["valued_at"] - first["valued_at"]).days)

    investor_flows: list[tuple[datetime, float]] = [(first["valued_at"], -start_value)]
    for tx in tx_rows:
        if tx.event_type == EVENT_DEPOSIT:
            investor_flows.append((tx.recorded_at, -float(tx.cash_delta_base or 0)))
        elif tx.event_type == EVENT_WITHDRAWAL:
            investor_flows.append((tx.recorded_at, -float(tx.cash_delta_base or 0)))
    investor_flows.append((last["valued_at"], end_value))
    annualized_mwr = _xirr(investor_flows) if start_value > 0 and end_value >= 0 else None
    if annualized_mwr is not None and days > 0 and 1 + annualized_mwr > 0:
        period_mwr = (1 + annualized_mwr) ** (days / 365.0) - 1
    elif start_value > 0 and not tx_rows:
        period_mwr = end_value / start_value - 1
    elif start_value > 0 and net_external == 0:
        period_mwr = end_value / start_value - 1
    else:
        period_mwr = None

    # 时间加权收益：用流水录入前后的估值快照切分子期间。
    recorded_external = (
        db.query(PortfolioTransaction)
        .filter(
            PortfolioTransaction.event_type.in_(EXTERNAL_EVENTS),
            PortfolioTransaction.recorded_at > first["valued_at"],
            PortfolioTransaction.recorded_at <= last["valued_at"],
        )
    )
    if account_id is not None:
        recorded_external = recorded_external.filter(PortfolioTransaction.account_id == account_id)
    recorded_rows = recorded_external.order_by(PortfolioTransaction.recorded_at.asc()).all()

    twr_factor = 1.0
    curve = [
        {
            "date": _iso_utc(first["valued_at"]),
            "total_assets": round(start_value, 2),
            "return_index": 100.0,
        }
    ]
    for previous, current in zip(points, points[1:]):
        base = float(previous["total"])
        flow = sum(
            float(t.cash_delta_base or 0)
            for t in recorded_rows
            if previous["valued_at"] < t.recorded_at <= current["valued_at"]
        )
        if base > 0:
            sub_return = (float(current["total"]) - base - flow) / base
            if sub_return > -1:
                twr_factor *= 1 + sub_return
        curve.append(
            {
                "date": _iso_utc(current["valued_at"]),
                "total_assets": round(float(current["total"]), 2),
                "return_index": round(twr_factor * 100, 4),
            }
        )

    complete = all(bool(p["complete"]) for p in points)
    first_local = first["valued_at"].replace(tzinfo=timezone.utc).astimezone(tz)
    partial = first_local > start_local or report_end_local < end_local
    return {
        "year": year,
        "empty": False,
        "account_id": account_id,
        "period_start": _iso_utc(first["valued_at"]),
        "period_end": _iso_utc(last["valued_at"]),
        "period_days": days,
        "partial_period": partial,
        "start_value": round(start_value, 2),
        "end_value": round(end_value, 2),
        "profit": round(profit, 2),
        "deposits": round(deposits, 2),
        "withdrawals": round(withdrawals, 2),
        "net_external_flow": round(net_external, 2),
        "dividend_income": round(dividends, 2),
        "realized_pnl": round(realized, 2),
        "mwr_pct": round(period_mwr * 100, 2) if period_mwr is not None else None,
        "mwr_annualized_pct": round(annualized_mwr * 100, 2) if annualized_mwr is not None else None,
        "twr_pct": round((twr_factor - 1) * 100, 2),
        "valuation_complete": complete,
        "missing_price_count": max((int(p["missing"]) for p in points), default=0),
        "transaction_count": len(tx_rows),
        "curve": curve,
        "message": (
            f"收益从 {first_local.strftime('%Y-%m-%d')} 的首个估值快照开始统计"
            if partial
            else ""
        ),
    }


def available_performance_years(db: Session, app_timezone: str = "Asia/Shanghai") -> list[int]:
    tz = ZoneInfo(app_timezone)
    current = datetime.now(tz).year
    earliest_snapshot = db.query(func.min(PortfolioValuationSnapshot.valued_at)).scalar()
    earliest_tx = db.query(func.min(PortfolioTransaction.occurred_at)).scalar()
    candidates = [x for x in (earliest_snapshot, earliest_tx) if x is not None]
    if not candidates:
        return [current]
    earliest = min(candidates).replace(tzinfo=timezone.utc).astimezone(tz).year
    return list(range(current, earliest - 1, -1))


def capture_snapshot_job(kind: str = "scheduled") -> None:
    """调度器使用的独立 Session 入口。"""
    from src.web.database import SessionLocal

    db = SessionLocal()
    try:
        capture_all_valuations(db, kind=kind)
        db.commit()
        logger.info("真实账户估值快照已保存")
    except Exception:
        db.rollback()
        logger.exception("真实账户估值快照保存失败")
    finally:
        db.close()

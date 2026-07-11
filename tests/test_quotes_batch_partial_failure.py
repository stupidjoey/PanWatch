"""批量行情局部失败回归测试。"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from unittest.mock import patch

from src.core.providers import ProviderResponse


# src.web.api.__init__ 会急切导入所有 API 模块，使这个小型单元测试被
# SQLAlchemy/OpenAI 等无关重依赖绑定。直接加载待测模块，
# 保持测试可独立运行。
_QUOTES_PATH = Path(__file__).resolve().parents[1] / "src" / "web" / "api" / "quotes.py"
_SPEC = importlib.util.spec_from_file_location("panwatch_quotes_under_test", _QUOTES_PATH)
assert _SPEC and _SPEC.loader
quotes = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(quotes)

QuoteBatchRequest = quotes.QuoteBatchRequest
QuoteItem = quotes.QuoteItem
get_quotes_batch = quotes.get_quotes_batch


class _FakeQuoteOrchestrator:
    def __init__(
        self,
        failing_markets: set[str] | None = None,
        omitted_symbols: set[str] | None = None,
    ):
        self.failing_markets = failing_markets or set()
        self.omitted_symbols = omitted_symbols or set()
        self.requests = []

    async def fetch(self, request):
        self.requests.append(request)
        if request.market in self.failing_markets:
            raise RuntimeError(f"{request.market} provider failed")
        return ProviderResponse(
            success=True,
            data=[
                {
                    "symbol": symbol,
                    "name": symbol,
                    "current_price": 100.0,
                    "change_pct": 1.0,
                }
                for symbol in request.symbols
                if symbol not in self.omitted_symbols
            ],
        )


def test_unsupported_market_does_not_abort_supported_quotes():
    orch = _FakeQuoteOrchestrator()
    payload = QuoteBatchRequest(
        items=[
            QuoteItem(symbol="600519", market="CN"),
            QuoteItem(symbol="000660", market="KR"),
            QuoteItem(symbol="AAPL", market="US"),
        ]
    )

    with patch.object(quotes, "get_quote_orchestrator", return_value=orch):
        result = asyncio.run(get_quotes_batch(payload))

    assert [item["symbol"] for item in result] == ["600519", "000660", "AAPL"]
    assert result[0]["current_price"] == 100.0
    assert result[1]["current_price"] is None
    assert result[1]["error"] == "不支持的市场: KR"
    assert result[2]["current_price"] == 100.0
    assert [request.market for request in orch.requests] == ["CN", "US"]


def test_one_market_provider_exception_does_not_abort_other_markets():
    orch = _FakeQuoteOrchestrator(failing_markets={"CN"})
    payload = QuoteBatchRequest(
        items=[
            QuoteItem(symbol="600519", market="CN"),
            QuoteItem(symbol="AAPL", market="US"),
        ]
    )

    with patch.object(quotes, "get_quote_orchestrator", return_value=orch):
        result = asyncio.run(get_quotes_batch(payload))

    assert result[0]["current_price"] is None
    assert result[1]["current_price"] == 100.0
    assert [request.market for request in orch.requests] == ["CN", "US"]


def test_missing_symbol_does_not_drop_other_symbols_in_same_market():
    orch = _FakeQuoteOrchestrator(omitted_symbols={"MISSING"})
    payload = QuoteBatchRequest(
        items=[
            QuoteItem(symbol="AAPL", market="US"),
            QuoteItem(symbol="MISSING", market="US"),
        ]
    )

    with patch.object(quotes, "get_quote_orchestrator", return_value=orch):
        result = asyncio.run(get_quotes_batch(payload))

    assert result[0]["current_price"] == 100.0
    assert result[1]["current_price"] is None
    assert len(orch.requests) == 1

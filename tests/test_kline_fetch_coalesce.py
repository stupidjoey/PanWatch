"""K线获取:并发合并 + 失败负缓存。

复活的 Phase 0-4 批量消费者(entry_candidates/strategy_engine/backtest)+ 组合归因
会在收盘后并发地对同一批标的取 K 线。源短暂故障时,若失败结果既不缓存也不合并,
每个并发消费者都会各自打一次 eastmoney(出现 "Server disconnected" 日志风暴),
且空结果不缓存导致每轮重复打爆。这里固化两条防线:
  1) 同一标的的并发取数合并为一次联网;
  2) 取数失败后在冷却窗口内不再联网(负缓存)。
"""

from __future__ import annotations

import threading
import time

import pytest

from src.collectors import kline_collector as kc
from src.models.market import MarketCode


@pytest.fixture(autouse=True)
def _clear_caches():
    """每个用例前后清空进程级缓存,避免相互污染。"""
    for name in ("_KLINE_CACHE", "_EASTMONEY_CACHE", "_FAIL_UNTIL", "_FETCH_LOCKS"):
        d = getattr(kc, name, None)
        if isinstance(d, dict):
            d.clear()
    yield
    for name in ("_KLINE_CACHE", "_EASTMONEY_CACHE", "_FAIL_UNTIL", "_FETCH_LOCKS"):
        d = getattr(kc, name, None)
        if isinstance(d, dict):
            d.clear()


def test_failed_fetch_is_negative_cached(monkeypatch):
    """同一标的取数失败后,冷却窗口内再次调用不再联网(负缓存)。"""
    calls = {"n": 0}

    def fake_tencent(symbol, market, days):
        calls["n"] += 1
        return []

    monkeypatch.setattr(kc, "_fetch_tencent_klines", fake_tencent)
    monkeypatch.setattr(kc, "_fetch_eastmoney_klines", lambda *a, **k: [])

    col = kc.KlineCollector(MarketCode.CN)
    assert col.get_klines("600519") == []
    assert col.get_klines("600519") == []  # 冷却窗口内,应直接短路
    assert calls["n"] == 1, f"失败后应负缓存,实际联网 {calls['n']} 次"


def test_concurrent_same_symbol_fetches_coalesced(monkeypatch):
    """同一标的的并发取数应合并为一次联网(防突发打爆数据源)。"""
    calls = {"n": 0}
    guard = threading.Lock()

    def slow_tencent(symbol, market, days):
        with guard:
            calls["n"] += 1
        time.sleep(0.25)
        return []

    monkeypatch.setattr(kc, "_fetch_tencent_klines", slow_tencent)
    monkeypatch.setattr(kc, "_fetch_eastmoney_klines", lambda *a, **k: [])

    col = kc.KlineCollector(MarketCode.CN)
    threads = [threading.Thread(target=lambda: col.get_klines("600519")) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert calls["n"] == 1, f"5 并发应合并为 1 次联网,实际 {calls['n']} 次"


def test_different_symbols_not_blocked(monkeypatch):
    """不同标的使用不同锁,不应相互阻塞(各自联网一次)。"""
    calls = {"n": 0}
    guard = threading.Lock()

    def fake_tencent(symbol, market, days):
        with guard:
            calls["n"] += 1
        return []

    monkeypatch.setattr(kc, "_fetch_tencent_klines", fake_tencent)
    monkeypatch.setattr(kc, "_fetch_eastmoney_klines", lambda *a, **k: [])

    col = kc.KlineCollector(MarketCode.CN)
    col.get_klines("600519")
    col.get_klines("000001")
    assert calls["n"] == 2, f"两个不同标的各应联网一次,实际 {calls['n']} 次"

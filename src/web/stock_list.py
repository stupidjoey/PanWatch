"""股票列表缓存与模糊搜索"""
import json
import os
import time
import logging
import concurrent.futures

import httpx

from src.core.asset_types import (
    ASSET_TYPE_FUND,
    ASSET_TYPE_SECURITY,
    ASSET_TYPE_UNKNOWN,
    normalize_asset_type,
)

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
CACHE_FILE = os.path.join(DATA_DIR, "stock_list_cache.json")
CACHE_TTL = 86400 * 7  # 7 days
CACHE_SCHEMA_VERSION = 2

# 东方财富 A 股（使用 push2delay 域名，避免重定向）
EASTMONEY_URL = "http://80.push2delay.eastmoney.com/api/qt/clist/get"
EASTMONEY_PARAMS = {
    "po": "1",
    "np": "1",
    "fltt": "2",
    "invt": "2",
    "fid": "f12",
    "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:1+t:1",  # + 沪市场内基金(深市基金API不返回,搜索兜底)
    "fields": "f12,f14",
}

# 东方财富港股参数
EASTMONEY_HK_PARAMS = {
    "po": "1",
    "np": "1",
    "fltt": "2",
    "invt": "2",
    "fid": "f12",
    "fs": "m:128+t:3,m:128+t:4,m:128+t:1,m:128+t:2",  # 港股主板、创业板等
    "fields": "f12,f14",
}

# 东方财富美股参数
EASTMONEY_US_PARAMS = {
    "po": "1",
    "np": "1",
    "fltt": "2",
    "invt": "2",
    "fid": "f12",
    "fs": "m:105,m:106,m:107",  # 美股 NYSE, NASDAQ, AMEX
    "fields": "f12,f14",
}

# 东方财富北交所参数（北证A股）
EASTMONEY_BJ_PARAMS = {
    "po": "1",
    "np": "1",
    "fltt": "2",
    "invt": "2",
    "fid": "f12",
    "fs": "m:0+t:81",  # 北交所
    "fields": "f12,f14",
}

# 东方财富开放式基金参数（含 ETF 联接等场外基金）
EASTMONEY_FUND_PARAMS = {
    "po": "1",
    "np": "1",
    "fltt": "2",
    "invt": "2",
    "fid": "f12",
    "fs": "m:0+t:0,m:1+t:0",  # 深/沪开放式基金
    "fields": "f12,f14",
}
PAGE_SIZE = 100


def _load_cache() -> list[dict] | None:
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if (
            data.get("version") == CACHE_SCHEMA_VERSION
            and time.time() - data.get("ts", 0) < CACHE_TTL
        ):
            return data["stocks"]
    except (json.JSONDecodeError, KeyError):
        pass
    return None


def _save_cache(stocks: list[dict]):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {
                "version": CACHE_SCHEMA_VERSION,
                "ts": time.time(),
                "stocks": stocks,
            },
            f,
            ensure_ascii=False,
        )


def _catalog_item(
    item: dict,
    market: str,
    asset_type: str = ASSET_TYPE_SECURITY,
) -> dict:
    return {
        "symbol": str(item["f12"]),
        "name": str(item["f14"]),
        "market": market,
        "asset_type": asset_type,
    }


def asset_type_from_search_classify(classify: str | None) -> str:
    """Map Eastmoney search classification to the local valuation model."""
    return (
        ASSET_TYPE_FUND
        if str(classify or "").strip().upper() == "OTCFUND"
        else ASSET_TYPE_SECURITY
    )


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://quote.eastmoney.com/",
}


def _fetch_page(client: httpx.Client, page: int) -> list[dict]:
    """获取东方财富股票列表的单页"""
    params = {**EASTMONEY_PARAMS, "pn": str(page), "pz": str(PAGE_SIZE)}
    resp = client.get(EASTMONEY_URL, params=params, timeout=30, follow_redirects=True)
    data = resp.json()
    diff = data.get("data") or {}
    items = diff.get("diff") or []
    return [_catalog_item(item, "CN") for item in items]


def _fetch_from_eastmoney() -> list[dict]:
    """东方财富 A 股列表（HTTP 分页并发获取）"""
    with httpx.Client(follow_redirects=True, headers=HEADERS, timeout=30) as client:
        # 第一页: 获取总数
        params = {**EASTMONEY_PARAMS, "pn": "1", "pz": str(PAGE_SIZE)}
        resp = client.get(EASTMONEY_URL, params=params)
        data = resp.json()
        root = data.get("data") or {}
        total = root.get("total", 0)
        first_items = root.get("diff") or []

        stocks = [_catalog_item(item, "CN") for item in first_items]

        if total <= PAGE_SIZE:
            return stocks

        # 剩余页并发获取
        pages_needed = (total + PAGE_SIZE - 1) // PAGE_SIZE
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_fetch_page, client, pn): pn for pn in range(2, pages_needed + 1)}
            for future in concurrent.futures.as_completed(futures):
                try:
                    stocks.extend(future.result())
                except Exception as e:
                    logger.warning(f"东方财富第 {futures[future]} 页获取失败: {e}")

    return stocks


def _fetch_hk_page(client: httpx.Client, page: int) -> list[dict]:
    """获取东方财富港股列表的单页"""
    params = {**EASTMONEY_HK_PARAMS, "pn": str(page), "pz": str(PAGE_SIZE)}
    resp = client.get(EASTMONEY_URL, params=params, timeout=30, follow_redirects=True)
    data = resp.json()
    diff = data.get("data") or {}
    items = diff.get("diff") or []
    return [_catalog_item(item, "HK") for item in items]


def _fetch_hk_from_eastmoney() -> list[dict]:
    """东方财富港股列表"""
    with httpx.Client(follow_redirects=True, headers=HEADERS, timeout=30) as client:
        params = {**EASTMONEY_HK_PARAMS, "pn": "1", "pz": str(PAGE_SIZE)}
        resp = client.get(EASTMONEY_URL, params=params)
        data = resp.json()
        root = data.get("data") or {}
        total = root.get("total", 0)
        first_items = root.get("diff") or []

        stocks = [_catalog_item(item, "HK") for item in first_items]

        if total <= PAGE_SIZE:
            return stocks

        pages_needed = (total + PAGE_SIZE - 1) // PAGE_SIZE
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_fetch_hk_page, client, pn): pn for pn in range(2, pages_needed + 1)}
            for future in concurrent.futures.as_completed(futures):
                try:
                    stocks.extend(future.result())
                except Exception as e:
                    logger.warning(f"东方财富港股第 {futures[future]} 页获取失败: {e}")

    return stocks


def _fetch_bj_page(client: httpx.Client, page: int) -> list[dict]:
    """获取东方财富北交所列表的单页"""
    params = {**EASTMONEY_BJ_PARAMS, "pn": str(page), "pz": str(PAGE_SIZE)}
    resp = client.get(EASTMONEY_URL, params=params, timeout=30, follow_redirects=True)
    data = resp.json()
    diff = data.get("data") or {}
    items = diff.get("diff") or []
    return [_catalog_item(item, "CN") for item in items]


def _fetch_bj_from_eastmoney() -> list[dict]:
    """东方财富北交所列表（HTTP 分页并发获取）"""
    with httpx.Client(follow_redirects=True, headers=HEADERS, timeout=30) as client:
        # 第一页: 获取总数
        params = {**EASTMONEY_BJ_PARAMS, "pn": "1", "pz": str(PAGE_SIZE)}
        resp = client.get(EASTMONEY_URL, params=params)
        data = resp.json()
        root = data.get("data") or {}
        total = root.get("total", 0)
        first_items = root.get("diff") or []

        stocks = [_catalog_item(item, "CN") for item in first_items]

        if total <= PAGE_SIZE:
            return stocks

        # 剩余页并发获取
        pages_needed = (total + PAGE_SIZE - 1) // PAGE_SIZE
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_fetch_bj_page, client, pn): pn for pn in range(2, pages_needed + 1)}
            for future in concurrent.futures.as_completed(futures):
                try:
                    stocks.extend(future.result())
                except Exception as e:
                    logger.warning(f"东方财富北交所第 {futures[future]} 页获取失败: {e}")

    return stocks


def _fetch_fund_page(client: httpx.Client, page: int) -> list[dict]:
    """获取东方财富开放式基金列表的单页"""
    params = {**EASTMONEY_FUND_PARAMS, "pn": str(page), "pz": str(PAGE_SIZE)}
    resp = client.get(EASTMONEY_URL, params=params, timeout=30, follow_redirects=True)
    data = resp.json()
    diff = data.get("data") or {}
    items = diff.get("diff") or []
    return [_catalog_item(item, "CN", ASSET_TYPE_FUND) for item in items]


def _fetch_fund_from_eastmoney() -> list[dict]:
    """东方财富开放式基金列表（含 ETF 联接等场外基金）"""
    with httpx.Client(follow_redirects=True, headers=HEADERS, timeout=30) as client:
        params = {**EASTMONEY_FUND_PARAMS, "pn": "1", "pz": str(PAGE_SIZE)}
        resp = client.get(EASTMONEY_URL, params=params)
        data = resp.json()
        root = data.get("data") or {}
        total = root.get("total", 0)
        first_items = root.get("diff") or []

        funds = [_catalog_item(item, "CN", ASSET_TYPE_FUND) for item in first_items]

        if total <= PAGE_SIZE:
            return funds

        pages_needed = (total + PAGE_SIZE - 1) // PAGE_SIZE
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_fetch_fund_page, client, pn): pn for pn in range(2, pages_needed + 1)}
            for future in concurrent.futures.as_completed(futures):
                try:
                    funds.extend(future.result())
                except Exception as e:
                    logger.warning(f"东方财富基金第 {futures[future]} 页获取失败: {e}")

    return funds


def _fetch_us_page(client: httpx.Client, page: int) -> list[dict]:
    """获取东方财富美股列表的单页"""
    params = {**EASTMONEY_US_PARAMS, "pn": str(page), "pz": str(PAGE_SIZE)}
    resp = client.get(EASTMONEY_URL, params=params, timeout=30, follow_redirects=True)
    data = resp.json()
    diff = data.get("data") or {}
    items = diff.get("diff") or []
    return [_catalog_item(item, "US") for item in items]


def _fetch_us_from_eastmoney() -> list[dict]:
    """东方财富美股列表"""
    with httpx.Client(follow_redirects=True, headers=HEADERS, timeout=30) as client:
        params = {**EASTMONEY_US_PARAMS, "pn": "1", "pz": str(PAGE_SIZE)}
        resp = client.get(EASTMONEY_URL, params=params)
        data = resp.json()
        root = data.get("data") or {}
        total = root.get("total", 0)
        first_items = root.get("diff") or []

        stocks = [_catalog_item(item, "US") for item in first_items]

        if total <= PAGE_SIZE:
            return stocks

        pages_needed = (total + PAGE_SIZE - 1) // PAGE_SIZE
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_fetch_us_page, client, pn): pn for pn in range(2, pages_needed + 1)}
            for future in concurrent.futures.as_completed(futures):
                try:
                    stocks.extend(future.result())
                except Exception as e:
                    logger.warning(f"东方财富美股第 {futures[future]} 页获取失败: {e}")

    return stocks


def _fetch_from_akshare() -> list[dict]:
    """akshare 数据源（备用，可能有 SSL 问题）"""
    import akshare as ak

    df = ak.stock_info_a_code_name()
    stocks = []
    for _, row in df.iterrows():
        stocks.append({
            "symbol": str(row["code"]),
            "name": str(row["name"]),
            "market": "CN",
            "asset_type": ASSET_TYPE_SECURITY,
        })
    return stocks


def refresh_stock_list() -> list[dict]:
    """拉取 A 股和港股列表并缓存"""
    stocks = []

    # A 股: 东方财富优先，akshare 备用
    try:
        cn_stocks = _fetch_from_eastmoney()
        stocks.extend(cn_stocks)
        logger.info(f"东方财富获取 A 股列表成功: {len(cn_stocks)} 只")
    except Exception as e:
        logger.warning(f"东方财富获取 A 股失败: {e}")
        try:
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(_fetch_from_akshare)
                cn_stocks = future.result(timeout=15)
                stocks.extend(cn_stocks)
            logger.info(f"akshare 获取 A 股列表成功: {len(cn_stocks)} 只")
        except concurrent.futures.TimeoutError:
            logger.error("akshare 获取超时（15s）")
        except Exception as e2:
            logger.error(f"A 股数据源获取失败: {e2}")

    # 港股: 东方财富
    try:
        hk_stocks = _fetch_hk_from_eastmoney()
        stocks.extend(hk_stocks)
        logger.info(f"东方财富获取港股列表成功: {len(hk_stocks)} 只")
    except Exception as e:
        logger.warning(f"东方财富获取港股失败: {e}")

    # 美股: 东方财富
    try:
        us_stocks = _fetch_us_from_eastmoney()
        stocks.extend(us_stocks)
        logger.info(f"东方财富获取美股列表成功: {len(us_stocks)} 只")
    except Exception as e:
        logger.warning(f"东方财富获取美股失败: {e}")

    # 北交所: 东方财富
    try:
        bj_stocks = _fetch_bj_from_eastmoney()
        stocks.extend(bj_stocks)
        logger.info(f"东方财富获取北交所列表成功: {len(bj_stocks)} 只")
    except Exception as e:
        logger.warning(f"东方财富获取北交所失败: {e}")

    # 开放式基金: 东方财富（含 ETF 联接等场外基金）
    try:
        fund_stocks = _fetch_fund_from_eastmoney()
        stocks.extend(fund_stocks)
        logger.info(f"东方财富获取开放式基金列表成功: {len(fund_stocks)} 只")
    except Exception as e:
        logger.warning(f"东方财富获取开放式基金失败: {e}")

    if stocks:
        _save_cache(stocks)
    return stocks


def get_stock_list() -> list[dict]:
    """获取股票列表(优先缓存)"""
    cached = _load_cache()
    if cached:
        return cached
    return refresh_stock_list()


def _realtime_search(query: str, market: str = "", limit: int = 20) -> list[dict]:
    """东方财富实时搜索 API"""
    import urllib.parse
    # 提高 count 以覆盖更多候选项（包含北交所）
    url = f"https://searchapi.eastmoney.com/api/suggest/get?input={urllib.parse.quote(query)}&type=14&count={limit * 5}"

    try:
        with httpx.Client(timeout=5) as client:
            resp = client.get(url, headers=HEADERS)
            data = resp.json()
    except Exception as e:
        logger.warning(f"实时搜索失败: {e}")
        return []

    items = data.get("QuotationCodeTable", {}).get("Data", [])
    if not items:
        return []

    def _normalize_symbol(code: str, mkt: str) -> str:
        c = (code or "").strip().upper()
        # 去掉可能的市场前缀/后缀，如 SH000001 / SZ000001 / BJ830799 / 00700.HK / 836239.BJ
        for p in ("SH", "SZ", "BJ", "US", "HK"):
            if c.startswith(p):
                c = c[len(p):]
                break
        if "." in c:
            # 形如 00700.HK / 836239.BJ
            c = c.split(".")[0]
        if mkt == "HK":
            # 保证为 5 位代码
            c = c.zfill(5)
        return c

    results = []
    for item in items:
        classify = (item.get("Classify") or "").strip()
        security_type = (item.get("SecurityTypeName") or "").strip()
        code_raw = (item.get("Code") or "").strip().upper()

        # 判断市场
        if (
            classify in ("AStock", "BJStock", "Fund", "OTCFUND")
            or any(ch in security_type for ch in ("沪", "深", "北", "基金"))
            or code_raw.endswith(".BJ")
            or code_raw.startswith("BJ")
        ):
            stock_market = "CN"
        elif classify == "HKStock" or "港" in security_type:
            stock_market = "HK"
        elif classify == "UsStock" or "美" in security_type:
            stock_market = "US"
        else:
            continue  # 跳过其他类型（债券、基金等）

        # 市场筛选
        if market and stock_market != market:
            continue

        # 只保留股票（排除债券等）
        type_us = item.get("TypeUS", "")
        if stock_market == "US" and type_us and type_us not in ("1", "2", "3", "5"):  # 1=普通股 3=ADR 5=ETF
            continue

        code = item.get("Code", "")
        symbol = _normalize_symbol(code, stock_market)

        results.append({
            "symbol": symbol,
            "name": item.get("Name", ""),
            "market": stock_market,
            "asset_type": asset_type_from_search_classify(classify),
        })

        if len(results) >= limit:
            break

    return results


def search_stocks(query: str, market: str = "", limit: int = 20) -> list[dict]:
    """搜索股票 - 优先使用实时搜索，失败则使用缓存"""
    q = query.strip()
    if not q:
        return []

    # 尝试实时搜索
    results = _realtime_search(q, market, limit)
    if len(results) >= limit:
        return results[:limit]

    # 实时搜索结果不足时，用缓存补全（便于聚合多市场搜索结果）
    cached = _cached_search(q, market, limit)
    if not results:
        if cached:
            logger.info("实时搜索无结果，使用缓存搜索")
        return cached

    seen = {
        (r.get("market"), r.get("symbol"), r.get("asset_type"))
        for r in results
    }
    for r in cached:
        key = (r.get("market"), r.get("symbol"), r.get("asset_type"))
        if key in seen:
            continue
        results.append(r)
        seen.add(key)
        if len(results) >= limit:
            break
    return results


def _cached_search(query: str, market: str = "", limit: int = 20) -> list[dict]:
    """从缓存中模糊搜索股票"""
    stocks = get_stock_list()
    if not stocks:
        return []

    q = query.strip().upper()
    if not q:
        return []

    results = []
    for s in stocks:
        if market and s["market"] != market:
            continue
        code = s["symbol"].upper()
        name = s["name"].upper()
        # 代码前缀匹配优先
        if code.startswith(q):
            results.append((0, s))
        elif q in name:
            results.append((1, s))
        elif q in code:
            results.append((2, s))

        if len(results) >= limit * 2:
            break

    results.sort(key=lambda x: x[0])
    return [r[1] for r in results[:limit]]


def _normalize_asset_name(value: str | None) -> str:
    text = str(value or "").strip().upper()
    for ch in (" ", "\t", "\n", "(", ")", "（", "）", "-", "_"):
        text = text.replace(ch, "")
    return text


def infer_asset_type_from_catalog(
    symbol: str,
    name: str,
    market: str,
    catalog: list[dict],
) -> str:
    """Best-effort classification for rows created before asset_type existed.

    Duplicate LOF codes can have both an exchange-traded and an OTC entry. In
    that case an exact normalized name is required; ambiguous rows stay
    ``unknown`` so the stock K-line pipeline is not called accidentally.
    """
    candidates = [
        item
        for item in catalog
        if str(item.get("symbol") or "") == str(symbol)
        and str(item.get("market") or "") == str(market)
    ]
    if not candidates:
        return ASSET_TYPE_UNKNOWN

    normalized_name = _normalize_asset_name(name)
    exact_types = {
        normalize_asset_type(item.get("asset_type"), default=ASSET_TYPE_UNKNOWN)
        for item in candidates
        if _normalize_asset_name(item.get("name")) == normalized_name
    }
    exact_types.discard(ASSET_TYPE_UNKNOWN)
    if len(exact_types) == 1:
        return exact_types.pop()

    candidate_types = {
        normalize_asset_type(item.get("asset_type"), default=ASSET_TYPE_UNKNOWN)
        for item in candidates
    }
    candidate_types.discard(ASSET_TYPE_UNKNOWN)
    if len(candidate_types) == 1:
        return candidate_types.pop()
    return ASSET_TYPE_UNKNOWN


def sync_stock_asset_types(catalog: list[dict]) -> int:
    """Backfill legacy ``unknown`` stock rows from the refreshed catalog."""
    if not catalog:
        return 0

    from src.web.database import SessionLocal
    from src.web.models import Stock

    db = SessionLocal()
    updated = 0
    try:
        rows = db.query(Stock).filter(Stock.asset_type == ASSET_TYPE_UNKNOWN).all()
        for row in rows:
            inferred = infer_asset_type_from_catalog(
                row.symbol,
                row.name,
                row.market,
                catalog,
            )
            if inferred == ASSET_TYPE_UNKNOWN:
                continue
            row.asset_type = inferred
            updated += 1
        if updated:
            db.commit()
            logger.info("已回填 %s 个历史标的的资产类型", updated)
        return updated
    except Exception:
        db.rollback()
        logger.exception("历史标的资产类型回填失败")
        return 0
    finally:
        db.close()

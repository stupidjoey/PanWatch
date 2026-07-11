#!/usr/bin/env python3
"""批量导入富途自选股到 PanWatch (fly.io 部署版)。
用法:
  python scripts/batch_import_watchlist.py <username> <password>
  python scripts/batch_import_watchlist.py <username> <password> --dry-run  # 仅预览
"""

import json
import sys
import urllib.request
import urllib.error

PANWATCH_URL = "https://stupidjoey-panwatch.fly.dev"
AUTH_ENDPOINT = f"{PANWATCH_URL}/api/auth/login"
STOCKS_ENDPOINT = f"{PANWATCH_URL}/api/stocks"

# ---- 要导入的股票列表 (名称, 代码, 市场) ----
STOCKS: list[tuple[str, str, str]] = [
    # === A 股 ===
    ("贵州茅台", "600519", "CN"),
    ("招商银行", "600036", "CN"),
    ("中国东航", "600115", "CN"),
    ("东方电缆", "603606", "CN"),
    ("华利集团", "300979", "CN"),
    ("港股创新药ETF银华", "159567", "CN"),
    ("旅游ETF富国", "159766", "CN"),
    ("黄金ETF华安", "518880", "CN"),
    ("创新药ETF广发", "515120", "CN"),

    # === 港股 ===
    ("腾讯控股", "00700", "HK"),
    ("阿里巴巴-W", "09988", "HK"),
    ("美团-W", "03690", "HK"),
    ("小米集团-W", "01810", "HK"),
    ("快手-W", "01024", "HK"),
    ("网易-S", "09999", "HK"),
    ("百度集团-SW", "09888", "HK"),
    ("哔哩哔哩-W", "09626", "HK"),
    ("京东集团-SW", "09618", "HK"),
    ("携程集团-S", "09961", "HK"),
    ("香港交易所", "00388", "HK"),
    ("中国平安", "02318", "HK"),
    ("招商银行", "03968", "HK"),
    ("吉利汽车", "00175", "HK"),
    ("紫金矿业", "02899", "HK"),
    ("山东黄金", "01787", "HK"),
    ("宁德时代", "03750", "HK"),
    ("药明康德", "02359", "HK"),
    ("恒瑞医药", "01276", "HK"),
    ("中国南方航空股份", "01055", "HK"),
    ("海底捞", "06862", "HK"),
    ("泡泡玛特", "09992", "HK"),
    ("老铺黄金", "06181", "HK"),
    ("领展房产基金", "00823", "HK"),
    ("京东健康", "06618", "HK"),
    ("富途控股", "FUTU", "HK"),
    ("恒生指数", "800000", "HK"),
    ("GlobalX中国洁净能源", "02809", "HK"),
    ("南方两倍做多海力士", "07709", "HK"),
    ("南方两倍做多三星", "07747", "HK"),
    ("中慧生物-B", "02627", "HK"),
    ("银诺医药-B", "02591", "HK"),
    ("智谱", "02513", "HK"),
    ("MINIMAX-W", "00100", "HK"),
    ("铜", "LIST1077", "HK"),

    # === 美股 ===
    ("Apple", "AAPL", "US"),
    ("Amazon", "AMZN", "US"),
    ("NVIDIA", "NVDA", "US"),
    ("GE Vernova", "GEV", "US"),
    ("NextEra Energy", "NEE", "US"),
    ("Centrus Energy", "LEU", "US"),
    ("Powell Industries", "POWL", "US"),
    ("Hammond Power", "HPS.A", "US"),
    ("Cerebras Systems", "CBRS", "US"),
    ("Astera Labs", "ALAB", "US"),
    ("Fabrinet", "FN", "US"),
    ("Bitdeer Technologies", "BTDR", "US"),
    ("UnitedHealth", "UNH", "US"),
    ("JD.com", "JD", "US"),
    ("半导体ETF-iShares", "SOXX", "US"),
    ("半导体指数ETF-VanEck", "SMH", "US"),
    ("铀与核能ETF-VanEck", "NLR", "US"),
    ("SPDR 航空航天与国防ETF", "ROKT", "US"),
    ("Global X 数据中心ETF", "DTCR", "US"),
    ("工业精选行业ETF", "XLII", "US"),
    ("Global X 美国基建ETF", "PAVE", "US"),
    ("First Trust 智能电网ETF", "GRID", "US"),
    ("iShares 美国基建ETF", "IFRA", "US"),
    # 商品 (CFD/期货)
    ("黄金/美元", "XAUUSD", "US"),
    ("白银/美元", "XAGUSD", "US"),
    ("布伦特原油", "BZmain", "US"),
    ("豆粕期货", "ZMmain", "US"),
]


def login(username: str, password: str) -> str:
    """登录获取 JWT token。"""
    data = json.dumps({"username": username, "password": password}).encode("utf-8")
    req = urllib.request.Request(
        AUTH_ENDPOINT,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            return result.get("data", {}).get("token", "")
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        print(f"登录失败 ({e.code}): {err_body}")
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"无法连接 panwatch: {e.reason}")
        sys.exit(1)


def add_stock(symbol: str, name: str, market: str, token: str) -> dict:
    """通过 API 添加单只股票。"""
    data = json.dumps({"symbol": symbol, "name": name, "market": market}).encode("utf-8")
    req = urllib.request.Request(
        STOCKS_ENDPOINT,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        return {"error": True, "status": e.code, "body": err_body}
    except urllib.error.URLError as e:
        return {"error": True, "reason": str(e.reason)}


def main():
    if len(sys.argv) < 3:
        print("用法: python scripts/batch_import_watchlist.py <username> <password> [--dry-run]")
        sys.exit(1)

    username = sys.argv[1]
    password = sys.argv[2]
    dry_run = "--dry-run" in sys.argv

    print(f"登录 panwatch ({PANWATCH_URL})...")
    token = login(username, password)
    if not token:
        print("登录失败：未获取到 token")
        sys.exit(1)
    print("✓ 登录成功\n")

    if dry_run:
        print(f"--- 预览模式 (--dry-run) ---")
        print(f"将导入 {len(STOCKS)} 只股票:\n")
        for name, symbol, market in STOCKS:
            print(f"  {name:20s} {symbol:10s} ({market})")
        print(f"\n去掉 --dry-run 执行实际导入")
        return

    print(f"准备导入 {len(STOCKS)} 只股票...\n")
    success = 0
    skipped = 0
    failed = 0

    for name, symbol, market in STOCKS:
        result = add_stock(symbol, name, market, token)
        if result.get("error"):
            body = result.get("body", "")
            status = result.get("status", 0)
            if status == 400 and "已存在" in body:
                print(f"  ⏭ {name} ({symbol}) - 已存在，跳过")
                skipped += 1
            else:
                print(f"  ✗ {name} ({symbol}) - 失败: {body[:80]}")
                failed += 1
        else:
            print(f"  ✓ {name} ({symbol}) - 已添加")
            success += 1

    print(f"\n--- 完成 ---")
    print(f"新增: {success} | 已存在跳过: {skipped} | 失败: {failed}")


if __name__ == "__main__":
    main()

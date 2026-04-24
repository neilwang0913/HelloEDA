"""
MXP 机场航班信息爬虫
米兰马尔彭萨机场 (ICAO: LIMC / IATA: MXP) 到达航班数据采集

数据源优先级：
  1. OpenSky Network API  — 免费注册后可用，/flights/arrival 需登录账号
  2. AviationStack API    — 免费注册后可用，需 API Key（100次/月免费）
  3. MXP 官网 HTML 解析  — 备用（官网为 JS 动态渲染，需 Selenium）

注意：
  OpenSky 匿名访问（无账号）仅支持实时状态接口，
  历史航班 /flights/arrival 接口需免费注册账号。
  注册地址：https://opensky-network.org/

输出：
  - 终端表格
  - arrivals_mxp.csv（可直接替换 peak_window_planner.py 中的 FLIGHT_SCHEDULE）

依赖安装：
  pip install requests beautifulsoup4
"""

import time
import csv
import json
import datetime
import requests
from typing import List, Dict, Optional

# ─────────────────────────────────────────
# 配置
# ─────────────────────────────────────────

AIRPORT_ICAO = "LIMC"          # MXP 的 ICAO 代码
AIRPORT_IATA = "MXP"
AVG_PAX_INTL = 180             # 国际航班默认旅客数（无真实数据时估算）
OUTPUT_CSV   = "arrivals_mxp.csv"

OPENSKY_URL       = "https://opensky-network.org/api/flights/arrival"
AVIATIONSTACK_URL = "http://api.aviationstack.com/v1/flights"
MXP_URL           = "https://www.milanomalpensa-airport.com/en/flights/arrivals"

# ── 账号配置（填入后生效，留空则跳过该数据源）──
OPENSKY_USER      = ""   # OpenSky 注册账号（免费）：https://opensky-network.org/
OPENSKY_PASS      = ""   # OpenSky 密码
AVIATIONSTACK_KEY = ""   # AviationStack API Key（免费注册）：https://aviationstack.com/

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


# ─────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────

class Flight:
    def __init__(self, callsign: str, origin: str,
                 arr_hour: int, arr_min: int,
                 pax: int = AVG_PAX_INTL,
                 status: str = ""):
        self.callsign = callsign.strip()
        self.origin   = origin.strip()
        self.arr_hour = arr_hour
        self.arr_min  = arr_min
        self.pax      = pax
        self.status   = status

    def __repr__(self):
        return (f"{self.callsign:<10} {self.origin:<6} "
                f"{self.arr_hour:02d}:{self.arr_min:02d}  "
                f"PAX≈{self.pax}  {self.status}")

    def to_planner_tuple(self):
        """转换为 peak_window_planner.py 所需格式"""
        return (self.callsign, self.arr_hour, self.arr_min, self.pax)


# ─────────────────────────────────────────
# 数据源 1：OpenSky Network API
# ─────────────────────────────────────────

def fetch_opensky(date: datetime.date = None,
                  max_hours: int = 24) -> List[Flight]:
    """
    从 OpenSky Network 获取 MXP 到达航班。
    date: 查询日期，默认今天
    max_hours: 查询时间窗口（小时），匿名用户最大7天前数据
    """
    if date is None:
        date = datetime.date.today()

    begin = int(datetime.datetime(date.year, date.month, date.day,
                                   0, 0, 0).timestamp())
    end   = begin + max_hours * 3600

    print(f"[OpenSky] 查询 {AIRPORT_ICAO} 到达航班：{date} ...")

    auth = (OPENSKY_USER, OPENSKY_PASS) if OPENSKY_USER else None
    if not auth:
        print("[OpenSky] 未配置账号，/flights/arrival 接口需登录。")
        print("  → 免费注册：https://opensky-network.org/")
        print("  → 注册后在脚本顶部填写 OPENSKY_USER / OPENSKY_PASS")
        return []

    try:
        resp = requests.get(
            OPENSKY_URL,
            params={"airport": AIRPORT_ICAO, "begin": begin, "end": end},
            headers=HEADERS,
            auth=auth,
            timeout=20
        )
        if resp.status_code == 403:
            print("[OpenSky] 认证失败（账号或密码错误）。")
            return []
        if resp.status_code == 429:
            print("[OpenSky] 触发频率限制，请稍后重试。")
            return []
        if resp.status_code == 404:
            print("[OpenSky] 无数据（数据尚未入库，尝试查询昨天）。")
            return []
        resp.raise_for_status()

        records = resp.json()
        flights = []
        for r in records:
            callsign = (r.get("callsign") or "").strip()
            if not callsign:
                continue
            origin = r.get("estDepartureAirport") or "UNK"

            # lastSeen = 最后一次收到信号的时间（近似到达时刻）
            arr_ts = r.get("lastSeen") or r.get("firstSeen") or 0
            arr_dt = datetime.datetime.utcfromtimestamp(arr_ts)

            flights.append(Flight(
                callsign=callsign,
                origin=origin,
                arr_hour=arr_dt.hour,
                arr_min=arr_dt.minute,
                pax=AVG_PAX_INTL
            ))

        print(f"[OpenSky] 获取到 {len(flights)} 条到达记录。")
        return sorted(flights, key=lambda f: f.arr_hour * 60 + f.arr_min)

    except requests.exceptions.ConnectionError:
        print("[OpenSky] 网络连接失败，请检查网络。")
        return []
    except requests.exceptions.Timeout:
        print("[OpenSky] 请求超时。")
        return []
    except Exception as e:
        print(f"[OpenSky] 未知错误：{e}")
        return []


# ─────────────────────────────────────────
# 数据源 2：MXP 官网 HTML 解析（静态尝试）
# ─────────────────────────────────────────

def fetch_mxp_website() -> List[Flight]:
    """
    尝试爬取 MXP 官网到达页面。
    官网为 JS 动态渲染，静态请求通常只能获取骨架 HTML。
    如需完整数据，请改用 Selenium / Playwright（见注释）。
    """
    print(f"[MXP官网] 尝试静态抓取 {MXP_URL} ...")
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        print("[MXP官网] 缺少依赖，请执行：pip install beautifulsoup4")
        return []

    try:
        resp = requests.get(MXP_URL, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        flights = []

        # 尝试解析常见的航班表格结构
        # 官网实际渲染后的选择器请根据浏览器 DevTools 调整
        rows = (
            soup.select("table.flight-table tr") or
            soup.select("div.flight-row") or
            soup.select("[class*='flight']") or
            []
        )

        if not rows:
            print("[MXP官网] 未找到航班数据（页面为 JS 动态渲染）。")
            print("  → 建议改用 Selenium：pip install selenium")
            print("  → 参见脚本底部 fetch_mxp_selenium() 示例")
            return []

        for row in rows:
            cols = row.find_all(["td", "div"])
            if len(cols) < 3:
                continue
            try:
                time_text = cols[0].get_text(strip=True)  # e.g. "14:35"
                callsign  = cols[1].get_text(strip=True)
                origin    = cols[2].get_text(strip=True)
                h, m = map(int, time_text.split(":"))
                flights.append(Flight(callsign, origin, h, m))
            except (ValueError, IndexError):
                continue

        print(f"[MXP官网] 解析到 {len(flights)} 条记录。")
        return flights

    except Exception as e:
        print(f"[MXP官网] 抓取失败：{e}")
        return []


# ─────────────────────────────────────────
# 数据源 3：AviationStack API
# ─────────────────────────────────────────

def fetch_aviationstack(date: datetime.date = None) -> List[Flight]:
    """
    AviationStack 免费层：100 次/月，需注册获取 API Key。
    注册地址：https://aviationstack.com/
    """
    if not AVIATIONSTACK_KEY:
        print("[AviationStack] 未配置 API Key，跳过。")
        print("  → 免费注册：https://aviationstack.com/")
        print("  → 注册后在脚本顶部填写 AVIATIONSTACK_KEY")
        return []

    if date is None:
        date = datetime.date.today()

    print(f"[AviationStack] 查询 {AIRPORT_IATA} 到达航班：{date} ...")
    flights = []
    offset  = 0
    limit   = 100

    try:
        while True:
            resp = requests.get(
                AVIATIONSTACK_URL,
                params={
                    "access_key": AVIATIONSTACK_KEY,
                    "arr_iata":   AIRPORT_IATA,
                    "flight_date": str(date),
                    "flight_status": "landed",
                    "limit":  limit,
                    "offset": offset,
                },
                headers=HEADERS,
                timeout=20
            )
            resp.raise_for_status()
            data = resp.json()

            if "error" in data:
                print(f"[AviationStack] API 错误：{data['error'].get('message')}")
                break

            records = data.get("data", [])
            if not records:
                break

            for r in records:
                callsign = (
                    r.get("flight", {}).get("iata") or
                    r.get("flight", {}).get("icao") or ""
                ).strip()
                origin = (
                    r.get("departure", {}).get("iata") or
                    r.get("departure", {}).get("icao") or "UNK"
                )
                arr_time = (
                    r.get("arrival", {}).get("actual") or
                    r.get("arrival", {}).get("scheduled") or ""
                )
                status = r.get("flight_status", "")

                if not callsign or not arr_time:
                    continue

                # 解析时间字符串 "2025-04-23T14:35:00+00:00"
                try:
                    dt = datetime.datetime.fromisoformat(arr_time)
                    flights.append(Flight(callsign, origin,
                                          dt.hour, dt.minute,
                                          AVG_PAX_INTL, status))
                except ValueError:
                    continue

            total = data.get("pagination", {}).get("total", 0)
            offset += limit
            if offset >= total:
                break

        print(f"[AviationStack] 获取到 {len(flights)} 条记录。")
        return sorted(flights, key=lambda f: f.arr_hour * 60 + f.arr_min)

    except Exception as e:
        print(f"[AviationStack] 错误：{e}")
        return []


# ─────────────────────────────────────────
# Selenium 备用方案（需安装 selenium + chromedriver）
# ─────────────────────────────────────────

def fetch_mxp_selenium() -> List[Flight]:
    """
    使用 Selenium 渲染 MXP 官网动态页面。

    前置条件：
      pip install selenium
      下载对应版本 chromedriver：https://chromedriver.chromium.org/
    """
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from bs4 import BeautifulSoup
    except ImportError:
        print("[Selenium] 依赖未安装：pip install selenium beautifulsoup4")
        return []

    print("[Selenium] 启动 Chrome 渲染 MXP 官网...")
    opts = Options()
    opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")

    driver = webdriver.Chrome(options=opts)
    flights = []

    try:
        driver.get(MXP_URL)
        # 等待航班列表容器出现（选择器需根据实际页面调整）
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "[class*='flight']"))
        )
        time.sleep(2)   # 等待数据填充

        soup = BeautifulSoup(driver.page_source, "html.parser")
        rows = soup.select("[class*='flight-row'], tr[class*='flight']")

        for row in rows:
            cols = row.find_all(["td", "div"])
            if len(cols) < 3:
                continue
            try:
                time_text = cols[0].get_text(strip=True)
                callsign  = cols[1].get_text(strip=True)
                origin    = cols[2].get_text(strip=True)
                h, m = map(int, time_text.split(":"))
                flights.append(Flight(callsign, origin, h, m))
            except (ValueError, IndexError):
                continue

        print(f"[Selenium] 解析到 {len(flights)} 条记录。")
    finally:
        driver.quit()

    return flights


# ─────────────────────────────────────────
# 输出：终端打印 + CSV
# ─────────────────────────────────────────

def print_flights(flights: List[Flight]):
    print("\n" + "=" * 60)
    print(f"  MXP 到达航班列表（共 {len(flights)} 条）")
    print("=" * 60)
    print(f"{'航班号':<12} {'出发地':<8} {'到达时刻':<10} {'旅客数':<8} {'状态'}")
    print("-" * 60)
    for f in flights:
        print(f"{f.callsign:<12} {f.origin:<8} "
              f"{f.arr_hour:02d}:{f.arr_min:02d}     "
              f"{f.pax:<8} {f.status}")
    print("=" * 60)


def save_csv(flights: List[Flight], path: str = OUTPUT_CSV):
    with open(path, "w", newline="", encoding="utf-8") as fp:
        writer = csv.writer(fp)
        writer.writerow(["callsign", "origin", "arr_hour", "arr_min", "pax"])
        for f in flights:
            writer.writerow([f.callsign, f.origin,
                             f.arr_hour, f.arr_min, f.pax])
    print(f"\n[输出] 已保存至 {path}")


def print_planner_format(flights: List[Flight]):
    """打印可直接粘贴到 peak_window_planner.py 的 FLIGHT_SCHEDULE 格式"""
    print("\n# 粘贴以下内容替换 peak_window_planner.py 中的 FLIGHT_SCHEDULE：")
    print("FLIGHT_SCHEDULE = [")
    for f in flights:
        print(f'    ("{f.callsign}", {f.arr_hour}, {f.arr_min}, {f.pax}),')
    print("]")


# ─────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="MXP 机场到达航班爬虫"
    )
    parser.add_argument(
        "--source",
        choices=["opensky", "aviationstack", "website", "selenium"],
        default="opensky",
        help="数据源（默认 opensky；需账号/Key 见脚本顶部配置）"
    )
    parser.add_argument(
        "--date", type=str, default=None,
        help="查询日期，格式 YYYY-MM-DD（默认今天）"
    )
    parser.add_argument(
        "--planner", action="store_true",
        help="同时输出 peak_window_planner.py 格式"
    )
    parser.add_argument(
        "--no-csv", action="store_true",
        help="不保存 CSV 文件"
    )
    args = parser.parse_args()

    # 解析日期
    query_date = None
    if args.date:
        query_date = datetime.date.fromisoformat(args.date)

    # 选择数据源（自动降级）
    if args.source == "opensky":
        flights = fetch_opensky(date=query_date)
        if not flights:
            print("[主程序] OpenSky 无数据，降级到 AviationStack...")
            flights = fetch_aviationstack(date=query_date)
        if not flights:
            print("[主程序] AviationStack 无数据，降级到官网...")
            flights = fetch_mxp_website()
    elif args.source == "aviationstack":
        flights = fetch_aviationstack(date=query_date)
        if not flights:
            print("[主程序] AviationStack 无数据，降级到 OpenSky...")
            flights = fetch_opensky(date=query_date)
    elif args.source == "selenium":
        flights = fetch_mxp_selenium()
    else:
        flights = fetch_mxp_website()
        if not flights:
            print("[主程序] 官网解析失败，降级到 OpenSky...")
            flights = fetch_opensky(date=query_date)

    if not flights:
        print("\n[主程序] 未获取到任何航班数据。")
        print("  可能原因：")
        print("  1. OpenSky 当天数据尚未入库（建议查询昨天：--date YYYY-MM-DD）")
        print("  2. 网络限制或 API 额度耗尽")
        print("  3. 官网为 JS 动态渲染，需使用 --source selenium")
        return

    print_flights(flights)

    if not args.no_csv:
        save_csv(flights)

    if args.planner:
        print_planner_format(flights)


if __name__ == "__main__":
    main()

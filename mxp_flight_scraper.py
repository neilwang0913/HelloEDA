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
# Demo 数据（MXP 典型日航班结构，基于公开时刻表构建）
# ─────────────────────────────────────────

DEMO_FLIGHTS = [
    # 早班波 06:00-08:00
    ("FR1234",  "BCN",  6, 10, 189, "landed"),
    ("U24456",  "LGW",  6, 35, 156, "landed"),
    ("LH5678",  "FRA",  6, 50, 167, "landed"),
    ("AZ1102",  "FCO",  7,  5, 143, "landed"),
    ("TK1845",  "IST",  7, 20, 231, "landed"),
    ("FR2210",  "MAD",  7, 30, 189, "landed"),
    ("U23344",  "BRS",  7, 40, 156, "landed"),
    ("EK0206",  "DXB",  7, 55, 358, "landed"),
    # 高峰波 08:00-10:00
    ("LH1234",  "MUC",  8,  0, 167, "landed"),
    ("AF1300",  "CDG",  8, 15, 202, "landed"),
    ("BA0564",  "LHR",  8, 20, 178, "landed"),
    ("FR5566",  "STN",  8, 35, 189, "landed"),
    ("QR0126",  "DOH",  8, 40, 269, "landed"),
    ("U27890",  "AMS",  8, 55, 156, "landed"),
    ("IB3456",  "MAD",  9,  0, 174, "landed"),
    ("KL1632",  "AMS",  9, 10, 202, "landed"),
    ("LX0948",  "ZRH",  9, 20, 143, "landed"),
    ("FR3344",  "DUB",  9, 30, 189, "landed"),
    ("OS0512",  "VIE",  9, 45, 167, "landed"),
    ("U25566",  "GVA",  9, 55, 156, "landed"),
    # 午间低谷 10:00-13:00
    ("AZ0876",  "CAT", 10, 10, 143, "landed"),
    ("FR7788",  "RYG", 10, 40, 189, "landed"),
    ("TK0382",  "IST", 11, 15, 231, "landed"),
    ("EK0204",  "DXB", 11, 50, 358, "landed"),
    ("QR0124",  "DOH", 12, 20, 269, "landed"),
    ("LH0992",  "FRA", 12, 50, 167, "landed"),
    # 下午波 13:00-17:00
    ("AF1302",  "CDG", 13, 10, 202, "landed"),
    ("BA0562",  "LHR", 13, 30, 178, "landed"),
    ("FR9900",  "CRL", 13, 50, 189, "landed"),
    ("U29988",  "LTN", 14, 10, 156, "landed"),
    ("KL1634",  "AMS", 14, 30, 202, "landed"),
    ("LX0946",  "ZRH", 14, 55, 143, "landed"),
    ("IB3458",  "BCN", 15, 20, 174, "landed"),
    ("OS0514",  "VIE", 15, 40, 167, "landed"),
    ("FR2288",  "AGP", 16,  5, 189, "landed"),
    ("U23322",  "EDI", 16, 30, 156, "landed"),
    # 晚高峰 17:00-21:00
    ("LH1236",  "MUC", 17,  0, 167, "landed"),
    ("TK1847",  "IST", 17, 20, 231, "landed"),
    ("EK0208",  "DXB", 17, 40, 358, "landed"),
    ("AF1304",  "CDG", 18,  5, 202, "landed"),
    ("BA0566",  "LHR", 18, 20, 178, "landed"),
    ("FR4422",  "VLC", 18, 35, 189, "landed"),
    ("QR0128",  "DOH", 18, 50, 269, "landed"),
    ("U21100",  "FCO", 19,  5, 156, "landed"),
    ("KL1636",  "AMS", 19, 25, 202, "landed"),
    ("LX0950",  "ZRH", 19, 50, 143, "landed"),
    ("AZ0880",  "NAP", 20, 10, 143, "landed"),
    ("FR6644",  "ATH", 20, 35, 189, "landed"),
    # 夜班 21:00-23:00
    ("LH0994",  "FRA", 21, 10, 167, "landed"),
    ("TK1849",  "IST", 21, 40, 231, "landed"),
    ("EK0202",  "DXB", 22, 15, 358, "landed"),
    ("FR8810",  "PMI", 22, 50, 189, "landed"),
]


def get_demo_flights() -> List[Flight]:
    return [Flight(c, o, h, m, p, s) for c, o, h, m, p, s in DEMO_FLIGHTS]


# ─────────────────────────────────────────
# ANSI 颜色工具
# ─────────────────────────────────────────

class C:
    RED    = "\033[91m"
    YELLOW = "\033[93m"
    GREEN  = "\033[92m"
    CYAN   = "\033[96m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    RESET  = "\033[0m"

def colorize(text: str, *codes: str) -> str:
    return "".join(codes) + text + C.RESET


# ─────────────────────────────────────────
# 输出：终端打印 + CSV
# ─────────────────────────────────────────

SLOT_SIZE = 30   # 分钟，时段粒度

def _slot_key(f: Flight) -> int:
    return (f.arr_hour * 60 + f.arr_min) // SLOT_SIZE

def _pressure(pax_per_hr: float) -> tuple:
    """返回 (标签, 颜色, 进度条)"""
    bar_total = 10
    if pax_per_hr >= 500:
        filled = 10
        return "极高峰", C.RED,    "█" * filled
    elif pax_per_hr >= 350:
        filled = 8
        return "高  峰", C.YELLOW, "█" * filled + "░" * (bar_total - filled)
    elif pax_per_hr >= 180:
        filled = 5
        return "正  常", C.GREEN,  "█" * filled + "░" * (bar_total - filled)
    else:
        filled = 2
        return "低  峰", C.CYAN,   "█" * filled + "░" * (bar_total - filled)


def print_flights(flights: List[Flight]):
    today = datetime.date.today().strftime("%Y-%m-%d")
    total_pax = sum(f.pax for f in flights)

    # ── 标题 ──
    print()
    print(colorize("╔══════════════════════════════════════════════════════════════╗", C.BOLD))
    print(colorize(f"║   MXP 米兰马尔彭萨机场  到达航班报告  {today}          ║", C.BOLD))
    print(colorize(f"║   共 {len(flights)} 个航班  |  预计旅客总量：{total_pax:,} 人              ║", C.BOLD))
    print(colorize("╚══════════════════════════════════════════════════════════════╝", C.BOLD))

    # ── 按时段分组 ──
    from collections import defaultdict
    slots: Dict[int, List[Flight]] = defaultdict(list)
    for f in flights:
        slots[_slot_key(f)].append(f)

    print()
    for slot_key in sorted(slots.keys()):
        slot_flights = slots[slot_key]
        slot_min_abs = slot_key * SLOT_SIZE
        sh, sm = divmod(slot_min_abs, 60)
        eh, em = divmod(slot_min_abs + SLOT_SIZE, 60)
        slot_label = f"{sh:02d}:{sm:02d}－{eh:02d}:{em:02d}"

        # 计算该时段到达率（人/小时）
        pax_in_slot = sum(f.pax for f in slot_flights)
        pax_per_hr  = pax_in_slot * (60 / SLOT_SIZE)
        label, color, bar = _pressure(pax_per_hr)

        # 时段标题行
        print(colorize(
            f"  ┌─ {slot_label}  {label}  [{bar}]  "
            f"{pax_per_hr:>5.0f}人/h  ({len(slot_flights)}班次)",
            C.BOLD, color
        ))

        # 航班明细
        for f in sorted(slot_flights, key=lambda x: x.arr_min):
            status_color = C.GREEN if f.status == "landed" else C.YELLOW
            status_str   = colorize(f.status or "-", status_color)
            print(
                f"  │  {colorize(f.callsign, C.BOLD):<18}"
                f"{f.arr_hour:02d}:{f.arr_min:02d}   "
                f"出发: {f.origin:<6}"
                f"PAX: {f.pax:<6}"
                f"{status_str}"
            )
        print("  └" + "─" * 60)

    # ── 汇总统计 ──
    print()
    print(colorize("  ── 全天旅客流量概览 ──", C.BOLD))
    print()
    _print_hourly_bar(flights)
    print()
    _print_summary(flights)


def _print_hourly_bar(flights: List[Flight]):
    """按小时绘制 ASCII 旅客量柱状图"""
    from collections import defaultdict
    hourly: Dict[int, int] = defaultdict(int)
    for f in flights:
        hourly[f.arr_hour] += f.pax

    if not hourly:
        return

    max_pax = max(hourly.values()) or 1
    bar_width = 30

    print(f"  {'小时':>4}  {'旅客量':>6}  柱状图")
    print(f"  {'─'*4}  {'─'*6}  {'─'*bar_width}")

    for h in range(min(hourly), max(hourly) + 1):
        pax = hourly.get(h, 0)
        filled = int(pax / max_pax * bar_width)
        _, color, _ = _pressure(pax)
        bar = colorize("▓" * filled, color) + "░" * (bar_width - filled)
        print(f"  {h:02d}:xx  {pax:>6,}  {bar}  {pax:,}")


def _print_summary(flights: List[Flight]):
    """打印关键统计数据"""
    from collections import defaultdict, Counter
    if not flights:
        return

    hourly: Dict[int, int] = defaultdict(int)
    for f in flights:
        hourly[f.arr_hour] += f.pax

    peak_hour = max(hourly, key=hourly.get)
    low_hour  = min(hourly, key=hourly.get)
    top_origins = Counter(f.origin for f in flights).most_common(3)

    print(colorize("  ── 关键指标 ──", C.BOLD))
    print(f"  最高峰小时  : {peak_hour:02d}:00  ({hourly[peak_hour]:,} 人)")
    print(f"  最低谷小时  : {low_hour:02d}:00  ({hourly[low_hour]:,} 人)")
    print(f"  峰谷比      : {hourly[peak_hour]/hourly[low_hour]:.1f}x")
    print(f"  主要出发地  : " + "  ".join(f"{o}({n}班)" for o, n in top_origins))
    print(f"  平均旅客/班 : {sum(f.pax for f in flights)//len(flights)} 人")


def save_html(flights: List[Flight], path: str = "arrivals_mxp.html"):
    """生成自包含 HTML 报告，含 Chart.js 图表，可直接浏览器打开"""
    from collections import defaultdict, Counter

    today = datetime.date.today().strftime("%Y-%m-%d")
    total_pax = sum(f.pax for f in flights)

    # 按小时聚合
    hourly: Dict[int, int] = defaultdict(int)
    for f in flights:
        hourly[f.arr_hour] += f.pax
    hours     = list(range(min(hourly), max(hourly) + 1))
    pax_vals  = [hourly.get(h, 0) for h in hours]
    bar_colors = [
        "#ef4444" if v >= 500 else
        "#f59e0b" if v >= 350 else
        "#22c55e" if v >= 180 else
        "#06b6d4"
        for v in pax_vals
    ]

    # 峰值统计
    peak_hour  = max(hourly, key=hourly.get)
    low_hour   = min(hourly, key=hourly.get)
    ratio      = hourly[peak_hour] / max(hourly[low_hour], 1)
    top3       = Counter(f.origin for f in flights).most_common(3)
    avg_pax    = total_pax // len(flights)

    # 按时段分组（30 min）
    slots: Dict[int, List[Flight]] = defaultdict(list)
    for f in flights:
        key = (f.arr_hour * 60 + f.arr_min) // 30
        slots[key].append(f)

    def slot_html(key: int, flist: List[Flight]) -> str:
        sm = key * 30
        sh, smin = divmod(sm, 60)
        eh, emin = divmod(sm + 30, 60)
        slot_pax = sum(f.pax for f in flist)
        pax_hr   = slot_pax * 2
        if pax_hr >= 500:
            cls, label = "extreme", "极高峰"
        elif pax_hr >= 350:
            cls, label = "high",    "高 峰"
        elif pax_hr >= 180:
            cls, label = "normal",  "正 常"
        else:
            cls, label = "low",     "低 峰"

        rows = ""
        for f in sorted(flist, key=lambda x: x.arr_min):
            s_cls = "status-landed" if f.status == "landed" else "status-other"
            rows += f"""
            <tr>
              <td class="fn">{f.callsign}</td>
              <td>{f.arr_hour:02d}:{f.arr_min:02d}</td>
              <td>{f.origin}</td>
              <td>{f.pax:,}</td>
              <td><span class="{s_cls}">{f.status or '—'}</span></td>
            </tr>"""

        return f"""
        <div class="slot {cls}">
          <div class="slot-header">
            <span class="slot-time">{sh:02d}:{smin:02d} – {eh:02d}:{emin:02d}</span>
            <span class="slot-badge">{label}</span>
            <span class="slot-meta">{pax_hr:,.0f} 人/h &nbsp;|&nbsp; {len(flist)} 班次</span>
          </div>
          <table class="flight-table">
            <thead><tr>
              <th>航班号</th><th>到达时刻</th><th>出发地</th><th>旅客数</th><th>状态</th>
            </tr></thead>
            <tbody>{rows}</tbody>
          </table>
        </div>"""

    slots_html = "\n".join(
        slot_html(k, slots[k]) for k in sorted(slots.keys())
    )
    top3_html = "  ".join(f"<b>{o}</b> {n}班" for o, n in top3)

    html = f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MXP 航班到达报告 {today}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: "Segoe UI", system-ui, sans-serif; background: #0f172a; color: #e2e8f0; }}

  /* ── 顶部标题 ── */
  .header {{
    background: linear-gradient(135deg, #1e3a5f 0%, #0f172a 100%);
    border-bottom: 1px solid #334155;
    padding: 28px 40px;
    display: flex; align-items: center; gap: 24px;
  }}
  .airport-code {{ font-size: 56px; font-weight: 900; color: #38bdf8; letter-spacing: -2px; }}
  .header-info h1 {{ font-size: 20px; font-weight: 600; color: #94a3b8; }}
  .header-info h2 {{ font-size: 14px; color: #64748b; margin-top: 4px; }}
  .header-pills {{ margin-left: auto; display: flex; gap: 16px; }}
  .pill {{
    background: #1e293b; border: 1px solid #334155; border-radius: 12px;
    padding: 10px 20px; text-align: center;
  }}
  .pill .val {{ font-size: 24px; font-weight: 700; color: #f1f5f9; }}
  .pill .lbl {{ font-size: 11px; color: #64748b; margin-top: 2px; }}

  /* ── 布局 ── */
  .container {{ max-width: 1280px; margin: 0 auto; padding: 32px 24px; }}

  /* ── 统计卡片 ── */
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 32px; }}
  .card {{
    background: #1e293b; border: 1px solid #334155; border-radius: 12px;
    padding: 20px 24px;
  }}
  .card .c-label {{ font-size: 12px; color: #64748b; text-transform: uppercase; letter-spacing: .05em; }}
  .card .c-value {{ font-size: 28px; font-weight: 700; color: #f1f5f9; margin: 6px 0 2px; }}
  .card .c-sub   {{ font-size: 13px; color: #94a3b8; }}

  /* ── 图表区 ── */
  .chart-box {{
    background: #1e293b; border: 1px solid #334155; border-radius: 12px;
    padding: 24px; margin-bottom: 32px;
  }}
  .chart-box h3 {{ font-size: 15px; color: #94a3b8; margin-bottom: 16px; }}
  .chart-box canvas {{ max-height: 260px; }}

  /* ── 图例 ── */
  .legend {{ display: flex; gap: 20px; margin-bottom: 20px; flex-wrap: wrap; }}
  .legend-item {{ display: flex; align-items: center; gap: 8px; font-size: 13px; color: #94a3b8; }}
  .dot {{ width: 12px; height: 12px; border-radius: 3px; }}
  .dot.extreme {{ background: #ef4444; }}
  .dot.high    {{ background: #f59e0b; }}
  .dot.normal  {{ background: #22c55e; }}
  .dot.low     {{ background: #06b6d4; }}

  /* ── 时段块 ── */
  .slot {{ border-radius: 10px; margin-bottom: 12px; overflow: hidden; border: 1px solid #334155; }}
  .slot-header {{
    display: flex; align-items: center; gap: 16px;
    padding: 12px 20px; font-size: 14px; cursor: pointer;
  }}
  .slot.extreme .slot-header {{ background: rgba(239,68,68,.18); border-left: 4px solid #ef4444; }}
  .slot.high    .slot-header {{ background: rgba(245,158,11,.15); border-left: 4px solid #f59e0b; }}
  .slot.normal  .slot-header {{ background: rgba(34,197,94,.12);  border-left: 4px solid #22c55e; }}
  .slot.low     .slot-header {{ background: rgba(6,182,212,.10);  border-left: 4px solid #06b6d4; }}
  .slot-time  {{ font-weight: 700; color: #f1f5f9; font-size: 15px; min-width: 130px; }}
  .slot-badge {{
    font-size: 12px; font-weight: 600; padding: 3px 10px; border-radius: 20px;
  }}
  .slot.extreme .slot-badge {{ background: #7f1d1d; color: #fca5a5; }}
  .slot.high    .slot-badge {{ background: #78350f; color: #fcd34d; }}
  .slot.normal  .slot-badge {{ background: #14532d; color: #86efac; }}
  .slot.low     .slot-badge {{ background: #164e63; color: #67e8f9; }}
  .slot-meta {{ color: #64748b; font-size: 13px; margin-left: auto; }}

  /* ── 航班表格 ── */
  .flight-table {{ width: 100%; border-collapse: collapse; background: #0f172a; }}
  .flight-table th {{
    font-size: 11px; text-transform: uppercase; letter-spacing: .05em;
    color: #64748b; padding: 10px 20px; text-align: left;
    border-bottom: 1px solid #1e293b;
  }}
  .flight-table td {{ padding: 10px 20px; font-size: 13px; border-bottom: 1px solid #1e293b; }}
  .flight-table tr:last-child td {{ border-bottom: none; }}
  .flight-table tr:hover td {{ background: #1e293b; }}
  .fn {{ font-weight: 700; color: #38bdf8; font-family: monospace; font-size: 14px; }}
  .status-landed {{ background: #14532d; color: #86efac; padding: 2px 8px; border-radius: 4px; font-size: 12px; }}
  .status-other  {{ background: #1e293b; color: #94a3b8; padding: 2px 8px; border-radius: 4px; font-size: 12px; }}

  /* ── 页脚 ── */
  .footer {{ text-align: center; color: #334155; font-size: 12px; padding: 32px 0; }}
</style>
</head>
<body>

<div class="header">
  <div class="airport-code">MXP</div>
  <div class="header-info">
    <h1>米兰马尔彭萨国际机场</h1>
    <h2>Milan Malpensa Airport &nbsp;·&nbsp; ICAO: LIMC &nbsp;·&nbsp; {today} 到达报告</h2>
  </div>
  <div class="header-pills">
    <div class="pill">
      <div class="val">{len(flights)}</div>
      <div class="lbl">到达航班</div>
    </div>
    <div class="pill">
      <div class="val">{total_pax:,}</div>
      <div class="lbl">预计旅客</div>
    </div>
    <div class="pill">
      <div class="val">{avg_pax}</div>
      <div class="lbl">均旅客/班</div>
    </div>
  </div>
</div>

<div class="container">

  <!-- 统计卡片 -->
  <div class="cards">
    <div class="card">
      <div class="c-label">最高峰小时</div>
      <div class="c-value">{peak_hour:02d}:00</div>
      <div class="c-sub">{hourly[peak_hour]:,} 人到达</div>
    </div>
    <div class="card">
      <div class="c-label">最低谷小时</div>
      <div class="c-value">{low_hour:02d}:00</div>
      <div class="c-sub">{hourly[low_hour]:,} 人到达</div>
    </div>
    <div class="card">
      <div class="c-label">峰谷比</div>
      <div class="c-value">{ratio:.1f}×</div>
      <div class="c-sub">错峰潜力指标</div>
    </div>
    <div class="card">
      <div class="c-label">主要出发地</div>
      <div class="c-value" style="font-size:18px">{top3[0][0] if top3 else "—"}</div>
      <div class="c-sub">{top3_html}</div>
    </div>
  </div>

  <!-- 旅客流量图表 -->
  <div class="chart-box">
    <h3>每小时到达旅客量</h3>
    <canvas id="paxChart"></canvas>
  </div>

  <!-- 图例 -->
  <div class="legend">
    <div class="legend-item"><div class="dot extreme"></div>极高峰 ≥500人/h</div>
    <div class="legend-item"><div class="dot high"></div>高峰 350~499人/h</div>
    <div class="legend-item"><div class="dot normal"></div>正常 180~349人/h</div>
    <div class="legend-item"><div class="dot low"></div>低峰 &lt;180人/h</div>
  </div>

  <!-- 时段航班列表 -->
  {slots_html}

</div>

<div class="footer">
  MXP 机场海关错峰优化项目 &nbsp;·&nbsp; 数据仅供研究分析使用
</div>

<script>
const ctx = document.getElementById('paxChart');
new Chart(ctx, {{
  type: 'bar',
  data: {{
    labels: {[f'{h:02d}:00' for h in hours]},
    datasets: [{{
      label: '旅客人数',
      data: {pax_vals},
      backgroundColor: {bar_colors},
      borderRadius: 6,
      borderSkipped: false,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{
      legend: {{ display: false }},
      tooltip: {{
        callbacks: {{
          label: ctx => ` ${{ctx.parsed.y.toLocaleString()}} 人`
        }}
      }}
    }},
    scales: {{
      x: {{ grid: {{ color: '#1e293b' }}, ticks: {{ color: '#64748b' }} }},
      y: {{ grid: {{ color: '#1e293b' }}, ticks: {{ color: '#64748b',
            callback: v => v.toLocaleString() }} }}
    }}
  }}
}});
</script>
</body>
</html>"""

    with open(path, "w", encoding="utf-8") as fp:
        fp.write(html)
    print(f"\n  [HTML] 已保存至 {path}  →  用浏览器打开即可查看")


def save_csv(flights: List[Flight], path: str = OUTPUT_CSV):
    with open(path, "w", newline="", encoding="utf-8") as fp:
        writer = csv.writer(fp)
        writer.writerow(["callsign", "origin", "arr_hour", "arr_min", "pax", "status"])
        for f in flights:
            writer.writerow([f.callsign, f.origin,
                             f.arr_hour, f.arr_min, f.pax, f.status])
    print(f"\n  [CSV] 已保存至 {path}")


def print_planner_format(flights: List[Flight]):
    """打印可直接粘贴到 peak_window_planner.py 的 FLIGHT_SCHEDULE 格式"""
    print()
    print(colorize("  ── peak_window_planner.py 兼容格式 ──", C.BOLD))
    print("  FLIGHT_SCHEDULE = [")
    for f in flights:
        print(f'      ("{f.callsign}", {f.arr_hour}, {f.arr_min}, {f.pax}),')
    print("  ]")


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
    parser.add_argument(
        "--demo", action="store_true",
        help="使用内置 MXP 示例数据（无需网络/账号，用于演示呈现效果）"
    )
    parser.add_argument(
        "--html", action="store_true",
        help="生成 HTML 报告（arrivals_mxp.html），可在浏览器打开"
    )
    args = parser.parse_args()

    # Demo 模式
    if args.demo:
        flights = get_demo_flights()
        print_flights(flights)
        if args.html:
            save_html(flights)
        if not args.no_csv:
            save_csv(flights)
        if args.planner:
            print_planner_format(flights)
        return

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

    if args.html:
        save_html(flights)

    if not args.no_csv:
        save_csv(flights)

    if args.planner:
        print_planner_format(flights)


if __name__ == "__main__":
    main()

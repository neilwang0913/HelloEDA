"""
机场海关错峰开窗建议框架
基于 M/M/c (Erlang-C) 模型
输入：航班时刻表 + 服务参数
输出：全天各时段最优开窗数 + 错峰建议
"""

import math
from dataclasses import dataclass
from typing import List, Dict

# ─────────────────────────────────────────
# 1. 参数配置（按实际情况修改）
# ─────────────────────────────────────────

# 每名旅客平均边检时长（分钟），无数据时用经验估算值
AVG_SERVICE_MIN = 1.5

# 目标最大平均等待时间（分钟），超出即需增开窗口
WAIT_TARGET_MIN = 5.0

# 每架次航班平均旅客数（国际航班估算值）
AVG_PAX_PER_FLIGHT = 180

# 旅客从下机到抵达边检队列的时间范围（分钟）
# 用于将航班到达时刻扩散为旅客到达曲线
DEPLANE_SPREAD_MIN = 20

# 时间槽宽度（分钟）
SLOT_MIN = 30


# ─────────────────────────────────────────
# 2. 航班时刻表（示例数据，替换为真实数据）
#    格式：("航班号", 到达时刻小时, 到达时刻分钟, 旅客数)
# ─────────────────────────────────────────

FLIGHT_SCHEDULE = [
    ("CA101",  6, 30, 220),
    ("MU202",  7,  0, 180),
    ("CZ303",  7, 20, 200),
    ("FM404",  7, 45, 160),
    ("CA105",  8, 10, 240),
    ("MU206",  8, 30, 180),
    ("CZ307",  9,  0, 200),
    ("3U408",  9, 30, 150),
    ("CA109", 10,  0, 180),
    ("MU210", 10, 45, 160),
    ("CZ311", 11, 30, 200),
    ("CA113", 12,  0, 220),
    ("MU214", 13,  0, 180),
    ("CZ315", 14, 30, 160),
    ("FM416", 15,  0, 180),
    ("CA117", 16,  0, 200),
    ("MU218", 17, 30, 240),
    ("CZ319", 18,  0, 220),
    ("FM420", 18, 30, 200),
    ("CA121", 19,  0, 180),
    ("MU222", 19, 30, 200),
    ("CZ323", 20,  0, 180),
    ("FM424", 21,  0, 160),
    ("CA125", 22, 30, 140),
]


# ─────────────────────────────────────────
# 3. 核心计算函数
# ─────────────────────────────────────────

@dataclass
class SlotResult:
    slot_label: str       # 时间槽标签，如 "07:30"
    arrival_rate: float   # 旅客到达率（人/小时）
    min_windows: int      # 满足等待目标所需最少窗口数
    wait_min: float       # 该窗口数下的平均等待时间（分钟）
    utilization: float    # 单窗口利用率


def erlang_c(c: int, lam: float, mu: float) -> float:
    """计算 Erlang-C 概率（队列非空概率）"""
    rho = lam / mu          # 总流量强度
    a = lam / mu            # 每服务台流量
    if rho >= c:
        return 1.0          # 系统过载
    # 分子：a^c / c! * 1/(1 - rho/c)
    numerator = (a ** c / math.factorial(c)) * (1 / (1 - a / c))
    # 分母：sum_{k=0}^{c-1} a^k/k!  +  numerator
    denominator = sum(a ** k / math.factorial(k) for k in range(c)) + numerator
    return numerator / denominator


def avg_wait(c: int, lam: float, mu: float) -> float:
    """计算 M/M/c 平均等待时间（小时）"""
    if lam == 0:
        return 0.0
    ec = erlang_c(c, lam, mu)
    wait_hr = ec / (c * mu - lam)
    return wait_hr * 60  # 转换为分钟


def min_windows_needed(lam: float, mu: float, target_wait: float) -> int:
    """二分查找满足等待目标的最少窗口数"""
    if lam == 0:
        return 0
    # 最少需要 ceil(lambda/mu) 个窗口才能稳定
    c = max(1, math.ceil(lam / mu) + 1)
    for _ in range(50):
        w = avg_wait(c, lam, mu)
        if w <= target_wait:
            return c
        c += 1
    return c


def build_arrival_curve(flights: List, slot_min: int,
                         spread_min: int) -> Dict[int, float]:
    """
    将航班时刻表转换为各时间槽的旅客到达率（人/小时）
    旅客均匀分散在 [到达时刻, 到达时刻 + spread_min] 内抵达边检
    """
    total_minutes = 24 * 60
    pax_per_min = [0.0] * total_minutes

    for _, h, m, pax in flights:
        start = h * 60 + m
        end = min(start + spread_min, total_minutes)
        rate = pax / (end - start)
        for t in range(start, end):
            pax_per_min[t] += rate

    # 聚合到时间槽
    slots: Dict[int, float] = {}
    for slot_start in range(0, total_minutes, slot_min):
        pax_in_slot = sum(pax_per_min[slot_start:slot_start + slot_min])
        # 转换为到达率（人/小时）
        slots[slot_start] = pax_in_slot * (60 / slot_min)

    return slots


# ─────────────────────────────────────────
# 4. 主流程
# ─────────────────────────────────────────

def run():
    mu = 1.0 / (AVG_SERVICE_MIN / 60)   # 服务率（人/小时）
    arrival_curve = build_arrival_curve(
        FLIGHT_SCHEDULE, SLOT_MIN, DEPLANE_SPREAD_MIN
    )

    results: List[SlotResult] = []
    for slot_start, lam in sorted(arrival_curve.items()):
        h, m = divmod(slot_start, 60)
        label = f"{h:02d}:{m:02d}"
        c = min_windows_needed(lam, mu, WAIT_TARGET_MIN)
        w = avg_wait(c, lam, mu) if c > 0 and lam > 0 else 0.0
        util = (lam / mu / c) if c > 0 else 0.0
        results.append(SlotResult(label, lam, c, w, util))

    # ── 输出报告 ──
    print("=" * 70)
    print("  机场海关错峰开窗建议报告")
    print(f"  服务参数：平均服务时长 {AVG_SERVICE_MIN} 分钟/人")
    print(f"  等待目标：平均等待 ≤ {WAIT_TARGET_MIN} 分钟")
    print("=" * 70)
    print(f"{'时间槽':<8} {'到达率(人/h)':<14} {'最少开窗数':<12} {'预计等待(分)':<14} {'利用率':<8} {'压力评级'}")
    print("-" * 70)

    for r in results:
        if r.arrival_rate < 1:
            continue   # 跳过无航班时段
        bar = pressure_bar(r.utilization, r.min_windows)
        print(f"{r.slot_label:<8} {r.arrival_rate:<14.1f} {r.min_windows:<12d} "
              f"{r.wait_min:<14.1f} {r.utilization:<8.1%} {bar}")

    print("=" * 70)
    print_stagger_advice(results)


def pressure_bar(util: float, windows: int) -> str:
    """生成压力评级标签"""
    if util >= 0.9:
        return f"[{'█' * 5}] 极高峰 ⚠"
    elif util >= 0.75:
        return f"[{'█' * 4}░] 高峰"
    elif util >= 0.5:
        return f"[{'█' * 3}░░] 正常"
    else:
        return f"[{'█' * 2}░░░] 低峰"


def print_stagger_advice(results: List[SlotResult]):
    """输出错峰策略建议"""
    peak_slots = [r for r in results if r.utilization >= 0.75 and r.arrival_rate >= 1]
    low_slots  = [r for r in results if r.utilization < 0.4  and r.arrival_rate >= 1]

    print("\n【错峰建议】")
    if peak_slots:
        labels = ", ".join(r.slot_label for r in peak_slots)
        max_c  = max(r.min_windows for r in peak_slots)
        print(f"  高峰时段：{labels}")
        print(f"  → 至少提前 30 分钟预开 {max_c} 个窗口")
        print(f"  → 建议航班时刻表在上述时段前后各错开 ≥15 分钟")

    if low_slots:
        labels = ", ".join(r.slot_label for r in low_slots)
        print(f"\n  低峰时段：{labels}")
        print(f"  → 可缩减至 1～2 个窗口，安排员工轮休，降低疲劳")

    print("\n  通用原则：")
    print("  1. 相邻航班到达间隔 < 15 分钟时，合并为同一峰值处理")
    print("  2. 开窗数变化建议提前 20 分钟执行（等于旅客扩散时间）")
    print("  3. 员工连续工作不超过 90 分钟，高峰段后安排 20 分钟轮休")
    print("=" * 70)


if __name__ == "__main__":
    run()

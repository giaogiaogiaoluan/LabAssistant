"""核心时间计算逻辑（纯函数，便于测试）。

约定：
- 一天内时刻以“分钟”（0..1439）表示；
- 时间段 (start, end) 用“分钟时刻”，若 end <= start 表示跨午夜（如 22:00-01:00，
  属于起始日），内部会拆成两个片段参与合并去重；
- 合并重叠区间：把多个时间片段合并成不相交的并集后求和，防止重复计算。
"""

from __future__ import annotations

MIN_PER_DAY = 24 * 60


def clock_to_min(text: str) -> int:
    """'08:30' / '8:30' -> 510；格式错误或越界抛 ValueError。"""
    t = str(text).strip()
    if ":" not in t:
        raise ValueError(f"时间格式应为 HH:MM，收到：{text!r}")
    hh, mm = t.split(":", 1)
    try:
        h, m = int(hh), int(mm)
    except ValueError:
        raise ValueError(f"时间格式应为 HH:MM，收到：{text!r}") from None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"时间超出范围：{text!r}")
    return h * 60 + m


def min_to_clock(m: int) -> str:
    m = int(m) % MIN_PER_DAY
    return f"{m // 60:02d}:{m % 60:02d}"


def raw_duration(start_min: int, end_min: int) -> int:
    """单个时间段时长（分钟）。end<=start 视为跨午夜：23:00-01:00 -> 120。"""
    if end_min > start_min:
        return end_min - start_min
    return end_min - start_min + MIN_PER_DAY


def split_intervals(pairs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """把每个原始时间段展开为“不跨午夜”的片段。

    跨午夜区间 (1320, 60) -> [(1320,1440), (0,60)]；普通区间原样保留。
    这样合并运算不需要特殊处理跨天。
    """
    out: list[tuple[int, int]] = []
    for s, e in pairs:
        s, e = int(s), int(e)
        if e > s:
            out.append((s, e))
        else:
            # 跨午夜（含整日等价情况），高段 [s,1440) + 低段 [0,e)
            out.append((s, MIN_PER_DAY))
            if e > 0:
                out.append((0, e))
    return out


def merge_intervals(pairs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """合并所有重叠/相邻区间，返回不相交的并集区间列表。"""
    pieces = split_intervals(pairs)
    if not pieces:
        return []
    pieces.sort(key=lambda x: (x[0], x[1]))
    merged: list[tuple[int, int]] = []
    cs, ce = pieces[0]
    for s, e in pieces[1:]:
        if s <= ce:  # 重叠或相邻（相邻合并更符合“在场时间”直觉）
            ce = max(ce, e)
        else:
            merged.append((cs, ce))
            cs, ce = s, e
    merged.append((cs, ce))
    return merged


def merged_duration(pairs: list[tuple[int, int]]) -> int:
    """所有片段合并重叠后的总时长（分钟）。"""
    return sum(e - s for s, e in merge_intervals(pairs))


def effective_minutes(
    lab_pairs: list[tuple[int, int]],
    course_pairs: list[tuple[int, int]],
    manual_minutes: int = 0,
) -> int:
    """每日有效时间 = 实验室时间段与课程时间段合并去重 + 手动时长。"""
    union = merged_duration(list(lab_pairs) + list(course_pairs))
    return union + int(manual_minutes)

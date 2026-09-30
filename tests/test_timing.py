"""时间段合并 / 跨午夜 / 有效时间 核心计算测试。"""

from labassistant.services import timing
from labassistant.services.timing import (
    clock_to_min,
    effective_minutes,
    merge_intervals,
    merged_duration,
    min_to_clock,
    raw_duration,
)

M = 60


def test_clock_parse():
    assert clock_to_min("08:30") == 8 * M + 30
    assert clock_to_min("8:5") == 8 * M + 5
    assert min_to_clock(510) == "08:30"
    try:
        clock_to_min("25:00")
        assert False, "应抛错"
    except ValueError:
        pass
    try:
        clock_to_min("abc")
        assert False
    except ValueError:
        pass


def test_重叠_实验室与课程():
    # 实验室 08:00-12:00，课程 10:00-11:00 -> 4h 而非 5h
    lab = [(8 * M, 12 * M)]
    course = [(10 * M, 11 * M)]
    assert effective_minutes(lab, course) == 4 * M
    assert effective_minutes(course, lab) == 4 * M


def test_多个重叠区间():
    # 08-10 / 09-11 / 10:30-12 -> 合并成 08:00-12:00 = 4h
    pairs = [(8 * M, 10 * M), (9 * M, 11 * M), (10 * M + 30, 12 * M)]
    assert merged_duration(pairs) == 4 * M
    assert merge_intervals(pairs) == [(8 * M, 12 * M)]


def test_相邻区间合并():
    assert merged_duration([(8 * M, 10 * M), (10 * M, 12 * M)]) == 4 * M


def test_不重叠区间求和():
    assert merged_duration([(8 * M, 9 * M), (10 * M, 11 * M), (14 * M, 15 * M)]) == 3 * M


def test_手动时长直接计入():
    lab = [(8 * M, 12 * M)]
    assert effective_minutes(lab, [], 120) == 4 * M + 120


def test_验收场景_周一_课程与实验室重叠():
    # 课程 10:00-12:00；实验室 08:00-11:00、13:00-17:00 -> 8h
    lab = [(8 * M, 11 * M), (13 * M, 17 * M)]
    course = [(10 * M, 12 * M)]
    assert effective_minutes(lab, course) == 8 * M
    assert merged_duration(lab + course) == 8 * M


def test_跨午夜时间段():
    # 22:00-01:00 = 3h，不能算出负数
    assert raw_duration(22 * M, 1 * M) == 3 * M
    assert merged_duration([(22 * M, 1 * M)]) == 3 * M


def test_跨午夜与凌晨重叠():
    # 22:00-01:00（覆盖 0:00-1:00 与 22:00-24:00）与 00:30-02:00 合并 = 22:00-02:00 = 4h
    pairs = [(22 * M, 1 * M), (30, 2 * M)]
    assert merged_duration(pairs) == 4 * M


def test_正常整段时长():
    assert raw_duration(8 * M, 12 * M) == 4 * M


def test_多个课程与实验室全重叠():
    lab = [(8 * M, 10 * M)]
    c1 = [(8 * M, 9 * M)]
    c2 = [(9 * M, 10 * M)]
    assert effective_minutes(lab, c1 + c2) == 2 * M

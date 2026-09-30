"""工作日 / 周末 / 节假日 / 月度要求时间 测试。"""

from datetime import date

from labassistant.services import schedule as sch

MON_FRI = {0, 1, 2, 3, 4}


def _first_weekday_of(year, month, target_wd):
    d = date(year, month, 1)
    while d.weekday() != target_wd:
        d = date(year, month, d.day + 1)
    return d


def test_day_kind_基础():
    assert sch.day_kind(date(2026, 9, 7), MON_FRI, set()) == "workday"  # 周一
    assert sch.day_kind(date(2026, 9, 12), MON_FRI, set()) == "weekend"  # 周六


def test_day_kind_节假日优先():
    d = date(2026, 9, 7)  # 周一
    assert sch.day_kind(d, MON_FRI, {d}) == "holiday"
    sat = date(2026, 9, 12)
    assert sch.day_kind(sat, MON_FRI, {sat}) == "holiday"


def test_工作日计数_九月2026():
    y, m = 2026, 9
    expected = sum(
        1 for d in sch.iter_month_dates(y, m) if d.weekday() in MON_FRI
    )
    assert sch.month_workday_count(y, m, MON_FRI, set()) == expected
    assert expected == 22  # 2026年9月 周一~周五 共 22 天（人工核验）


def test_节假日减少工作日():
    y, m = 2026, 10
    holidays = {
        _first_weekday_of(y, m, 0),   # 10月第一个周一（示例节假日）
        date(2026, 10, 1),
    }
    base = sch.month_workday_count(y, m, MON_FRI, set())
    with_hol = sch.month_workday_count(y, m, MON_FRI, holidays)
    assert with_hol <= base


def test_月度要求时间_8小时():
    y, m = 2026, 9
    n = sch.month_workday_count(y, m, MON_FRI, set())
    assert sch.month_required_minutes(y, m, MON_FRI, set(), 480) == n * 480


def test_周末与节假日要求为零():
    assert sch.required_for_day(date(2026, 9, 12), MON_FRI, set(), 480) == 0
    d = date(2026, 9, 7)
    assert sch.required_for_day(d, MON_FRI, {d}, 480) == 0
    assert sch.required_for_day(d, MON_FRI, set(), 480) == 480


def test_自定义工作日():
    # 只把周日当工作日的情况
    wd = {6}
    sunday = date(2026, 9, 13)
    assert sch.required_for_day(sunday, wd, set(), 480) == 480
    assert sch.required_for_day(date(2026, 9, 14), wd, set(), 480) == 0


def test_week_dates():
    # 2026-09-07 是周一，所在周就是它本身
    assert date(2026, 9, 7).weekday() == 0
    dates = sch.week_dates(date(2026, 9, 9))
    assert dates[0] == date(2026, 9, 7)
    assert dates[-1] == date(2026, 9, 13)

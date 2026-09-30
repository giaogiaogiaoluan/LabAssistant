"""设计体系回归测试：圆角刻度统一 + 文字对比度达标。
这两条是人工最容易改回去的地方，所以固化成测试：
1. 全局 QSS 里出现的 border-radius 只能是主题刻度上的值；
2. 所有会被当作“文字颜色”使用的令牌，在玻璃底色上必须过 WCAG AA；
3. 任何样式表字符串里都不允许残留未插值的 `{T.xxx}`（写 f-string 时最容易漏）。
"""

from __future__ import annotations

import io
import re
import sys
import tokenize
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from labassistant.ui import theme as T     # noqa: E402

UI_DIR = SRC / "labassistant" / "ui"


# ---------------------------------------------------------------- 圆角
def test_qss_圆角只用主题刻度():
    allowed = {T.RADIUS_XS, T.RADIUS_SM, T.RADIUS_MD, T.RADIUS_LG, T.RADIUS_XL,
               T.RADIUS_INNER, 3, 4}      # 3/4 是 5px 细进度条与滚动条的半高
    found = {int(n) for n in re.findall(r"border-radius:\s*(\d+)px", T.QSS)}
    assert found <= allowed, f"QSS 出现刻度外圆角：{sorted(found - allowed)}"


def test_没有未插值的样式令牌残留():
    """f-string 前缀漏掉时，`{T.RADIUS_MD}` 会原样进 QSS，样式直接失效。"""
    offenders = []
    for f in sorted(UI_DIR.glob("*.py")):
        text = f.read_text()
        compile(text, str(f), "exec")           # 语法错也在这里暴露
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type != tokenize.STRING:
                continue
            s = tok.string
            prefix = s[:len(s) - len(s.lstrip("fFrRbBuU'\""))].lower()
            if "f" not in prefix and "{T." in s:
                offenders.append(f"{f.name}:{tok.start[0]}")
    assert not offenders, f"缺少 f 前缀的样式串：{offenders}"


# ---------------------------------------------------------------- 对比度
def _assert_ok(color: str, ratio: float, label: str):
    got = T.contrast(color, T.PANEL_BG)
    assert got >= ratio, f"{label} {color} 在玻璃上只有 {got:.2f}:1（要求 ≥{ratio}:1）"


@pytest.mark.parametrize("name", ["TEXT", "TEXT_SECONDARY", "MUTED"])
def test_正文色达标(name):
    _assert_ok(getattr(T, name), 4.5, f"正文色 {name}")


def test_状态文字全部达标():
    """日历里的日期数字与状态小字：以前 周末/未打卡 只有 2.2~2.7:1。"""
    for key in T.STATE_INK:
        _assert_ok(T.status_ink(key), 4.5, f"状态文字 {key}")


def test_类别文字全部达标():
    """网站页的域名/计数文字。"""
    for key in T.CATEGORY_ORDER:
        cs = T.category_style(key)
        _assert_ok(cs.ink_text, 4.5, f"类别文字 {key}")
        # 装饰色（圆点/瓦片底）不参与文字对比度要求，但必须仍然是鲜色
        assert cs.ink != cs.ink_text or T.contrast(cs.ink, T.PANEL_BG) >= 4.5


def test_大数字色压深后达标():
    for c in (T.AMBER, T.RED, T.PURPLE, T.ACCENT, T.INDIGO, T.GREEN):
        _assert_ok(T.ink_big(c), 3.2, f"大号数字 {c}")


def test_主按钮白字达标():
    """实色蓝底上的白字：#0A84FF 只有 3.65，所以按钮底要用 ACCENT_SOLID。"""
    assert T.contrast(T.ON_ACCENT, T.ACCENT_SOLID) >= 4.5
    assert T.contrast(T.ON_ACCENT, T.ACCENT_DARK) >= 4.5


def test_readable_不会改变已经达标的颜色():
    assert T.readable("#171E2B") == "#171E2B"
    dark = T.readable("#FF9F0A", T.PANEL_BG, 4.5)
    assert dark != "#FF9F0A"
    assert T.contrast(dark, T.PANEL_BG) >= 4.5

def test_图表不用白色画浅底文字():
    """柱状图的数值曾经用白字画在玻璃底上，等于看不见。"""
    src = (UI_DIR / "widgets.py").read_text()
    # 只允许 ON_ACCENT / 白色出现在“压在实色柱子上”的分支里
    for m in re.finditer(r"setPen\(T\.qcolor\((\"[^\"]+\"|T\.[A-Z_]+)\)\)", src):
        assert m.group(1) != '"#FFFFFF"', "图表文字请改用 T.ink(...) / T.ON_ACCENT，别写死白色"

#!/usr/bin/env python3
"""LabAssistant 图标流水线：从一张 AI 生成的 master PNG 产出一整套应用图标。

用法::

    .venv-mac/bin/python scripts/make_icons.py <master.png> [选项]

产出（默认写到 ``assets/``）::

    icon.png         1024x1024，squircle 之外四角透明
    icon.icns        macOS 应用图标（iconutil 生成，16/32/128/256/512 及 @2x）
    icon.ico         Windows 多尺寸图标（16/24/32/48/64/128/256）
    brand_mark.png   256x256 透明底品牌标，用于应用内侧栏
    aurora_tile.png  1600x1000 极光渐变（纯代码绘制，窗口壁纸备选）

同时把结果分别合成到浅色 (#FFFFFF) / 深色 (#1B1B24) 背景上，输出到
``/tmp/labassistant_icon_preview/`` 供肉眼验收。

抠图原理
--------
master 是纯黑底（实测背景 max(r,g,b) <= 6）上的亮色 squircle（实测形状内部
max(r,g,b) >= 93），明暗过渡只有 1~2px，所以「亮度 matte」最贴合真实轮廓：

1. 亮度 matte：``alpha = clamp((max(r,g,b) - T_LO) / (T_HI - T_LO))``，
   完整保留 AI 画出来的圆角轮廓，不会切坏角。
2. 几何保险：自动检测包围盒 + 拟合圆角半径，用 QPainterPath 画圆角矩形并向内
   收缩 GUARD_INSET，其内部强制不透明 —— 防止形状内部万一有暗色被打穿。
3. 羽化：对 alpha 通道做可分离 3 抽头盒式模糊，一次约 1px（``--feather`` 可调）。
4. 去黑边（关键）：凡 alpha < 255 的像素，RGB 一律不取原图值，而是沿「指向形状
   中心」的方向逐步推进，取第一个完全不透明像素的颜色。这样边缘半透明带拿到的是
   真实内部亮色而非黑色，合成到白底不留暗晕；全透明的四角也带上邻近亮色，缩到
   16px 时不会被透明像素的纯黑 RGB 拉暗。

只依赖 PySide6（QtGui/QtCore）+ 系统 iconutil，不需要 Pillow / numpy。
"""

from __future__ import annotations

import argparse
import math
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QRadialGradient,
)

# ------------------------------------------------------------------ matte 参数
T_LO = 6  # 背景噪声上限：亮度低于此值视为全透明
T_HI = 80  # 亮度高于此值视为完全不透明
DET_THRESH = 64  # 形状检测用的二值化阈值
GUARD_INSET = 5  # 几何保险矩形向内收缩像素数
FEATHER_PASSES = 1  # 羽化次数，1 次约 1px
# 向心取色的推进半径（px），从小到大试探直到碰到完全不透明像素
BLEED_STEPS = (2, 3, 4, 6, 9, 13, 18, 25, 34, 46, 62, 84, 115, 155, 200)

DEFAULT_MASTER = str(Path(__file__).resolve().parents[1] / "assets" / "icon_master.png")
ICON_SIZES = (16, 32, 128, 256, 512)  # icns 基准尺寸，另出各自 @2x
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
LIGHT_BG = "#FFFFFF"
DARK_BG = "#1B1B24"


# ------------------------------------------------------------------ 小工具
def _argb(img: QImage) -> QImage:
    """统一成 Format_ARGB32 —— 内存里就是 B,G,R,A 小端序，可直接按字节搬运。"""
    out = img.convertToFormat(QImage.Format_ARGB32)
    assert out.bytesPerLine() == out.width() * 4, "scanline 未紧凑排列，按字节搬运会错位"
    return out


def _read_png(path: Path) -> QImage:
    img = QImage(str(path))
    if img.isNull():
        raise RuntimeError(f"无法读取图片: {path}")
    return img


def _write_png(img: QImage, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = QByteArray()
    dev = QBuffer(buf)
    dev.open(QIODevice.WriteOnly)
    if not img.save(dev, "PNG"):
        raise RuntimeError(f"PNG 编码失败: {path}")
    dev.close()
    path.write_bytes(bytes(buf.data()))


def _scale(img: QImage, size: int) -> QImage:
    """多次半步降采样，避免 1024->16 直接缩放产生锯齿/细节糊掉。"""
    cur = img
    while cur.width() > size * 2:
        half = max(cur.width() // 2, size)
        cur = cur.scaled(half, half, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    if cur.width() == size and cur.height() == size:
        return cur
    return cur.scaled(size, size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)


def _lum_plane(argb: QImage) -> bytes:
    """每像素 max(r,g,b) 打平成 W*H 字节平面（BGRA 顺序，取 max 与顺序无关）。"""
    w, h = argb.width(), argb.height()
    src = bytes(argb.constBits()[: argb.sizeInBytes()])
    stride = w * 4
    plane = bytearray(w * h)
    for y in range(h):
        base = y * stride
        abase = y * w
        row = bytearray(w)
        for x in range(w):
            o = base + x * 4
            m = src[o]
            g = src[o + 1]
            b = src[o + 2]
            if g > m:
                m = g
            if b > m:
                m = b
            row[x] = m
        plane[abase : abase + w] = row
    return bytes(plane)


# ------------------------------------------------------------------ 形状检测
def detect_shape(lum: bytes, w: int, h: int) -> tuple[tuple[int, int, int, int], int]:
    """由亮度平面求 squircle 包围盒与圆角半径。"""
    stride = w
    x0, y0, x1, y1 = w, h, -1, -1
    for y in range(h):
        base = y * stride
        row = lum[base : base + stride]
        hit = _first_over(row, DET_THRESH)
        if hit is not None:
            last = _last_over(row, DET_THRESH)
            if hit < x0:
                x0 = hit
            if last > x1:
                x1 = last
            if y < y0:
                y0 = y
            if y > y1:
                y1 = y
    if x1 < 0 or y1 < 0:
        raise RuntimeError("master 里没检测到亮色形状（背景不是纯黑？阈值太严？）")

    def edge_top(x: int) -> int | None:
        for y in range(h):
            if lum[y * stride + x] > DET_THRESH:
                return y
        return None

    span = max(24, min(300, (x1 - x0) // 3))
    radii: list[int] = []
    for side in ("left", "right"):
        pts: list[tuple[int, int]] = []
        rng = range(x0, x0 + span) if side == "left" else range(x1, x1 - span, -1)
        for x in rng:
            y = edge_top(x)
            if y is not None and y > y0 + 1:
                pts.append((x, y))
        if len(pts) < 8:
            continue
        best: tuple[int, float] | None = None
        for r in range(20, min(x1 - x0, y1 - y0) // 2 + 1):
            cx = (x0 + r - 0.5) if side == "left" else (x1 - r + 0.5)
            cy = y0 + r - 0.5
            err = 0.0
            for px, py in pts:
                d = math.hypot(px - cx, py - cy) - r
                err += d * d
            err /= len(pts)
            if best is None or err < best[1]:
                best = (r, err)
        if best:
            radii.append(best[0])
    radius = (
        int(round(sum(radii) / len(radii)))
        if radii
        else min(x1 - x0, y1 - y0) // 4
    )
    return (x0, y0, x1, y1), radius


def _first_over(row: bytes, t: int) -> int | None:
    for x, v in enumerate(row):
        if v > t:
            return x
    return None


def _last_over(row: bytes, t: int) -> int:
    for x in range(len(row) - 1, -1, -1):
        if row[x] > t:
            return x
    return 0


def _guard_mask(bbox: tuple[int, int, int, int], radius: int, w: int, h: int) -> QImage:
    """圆角矩形保险遮罩（向内收缩 GUARD_INSET），白色不透明。"""
    x0, y0, x1, y1 = bbox
    mask = QImage(w, h, QImage.Format_ARGB32)
    mask.fill(Qt.transparent)
    p = QPainter(mask)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(QColor(255, 255, 255, 255)))
    rect = QRectF(
        x0 + GUARD_INSET - 0.5,
        y0 + GUARD_INSET - 0.5,
        (x1 - x0 + 1) - 2 * GUARD_INSET,
        (y1 - y0 + 1) - 2 * GUARD_INSET,
    )
    path = QPainterPath()
    path.addRoundedRect(rect, max(1, radius - GUARD_INSET), max(1, radius - GUARD_INSET))
    p.drawPath(path)
    p.end()
    return mask


# ------------------------------------------------------------------ matte 构建
def build_alpha(lum: bytes, w: int, h: int, bbox, radius: int) -> tuple[bytearray, int]:
    """亮度 matte ∪ 几何保险。返回 (alpha 平面, 被保险层补成不透明的像素数)。"""
    alpha = bytearray(w * h)
    inv = 255.0 / (T_HI - T_LO)
    for i, m in enumerate(lum):
        if m <= T_LO:
            continue
        alpha[i] = 255 if m >= T_HI else int((m - T_LO) * inv)

    guard = _guard_mask(bbox, radius, w, h)
    gbuf = bytes(guard.constBits()[: guard.sizeInBytes()])
    patched = 0
    for i in range(w * h):
        if gbuf[i * 4 + 3] > 127 and alpha[i] < 255:
            alpha[i] = 255
            patched += 1
    return alpha, patched


def feather(alpha: bytearray, w: int, h: int, passes: int) -> bytearray:
    """可分离 3 抽头盒式模糊（边缘 clamp），一次约 1px 羽化。"""
    a = bytes(alpha)
    for _ in range(max(0, passes)):
        tmp = bytearray(w * h)
        for y in range(h):
            base = y * w
            row = a[base : base + w]
            pad = bytes((row[0],)) + row + bytes((row[-1],))
            tmp[base : base + w] = bytes(
                (pad[i] + pad[i + 1] + pad[i + 2]) // 3 for i in range(w)
            )
        out = bytearray(w * h)
        for x in range(w):
            col = tmp[x::w]
            pad = bytes((col[0],)) + bytes(col) + bytes((col[-1],))
            out[x::w] = bytes(
                (pad[i] + pad[i + 1] + pad[i + 2]) // 3 for i in range(h)
            )
        a = bytes(out)
    return bytearray(a)


def apply_matte(argb: QImage, alpha: bytearray, bbox) -> QImage:
    """合成 alpha 到 RGB，并对所有 alpha<255 的像素做「向心取色」去黑边。

    源与目标都是 Format_ARGB32（内存 BGRA），所以整段像素可以直接切片搬运。
    """
    w, h = argb.width(), argb.height()
    src = bytes(argb.constBits()[: argb.sizeInBytes()])
    stride = w * 4
    x0, y0, x1, y1 = bbox
    cx, cy = (x0 + x1 + 1) / 2.0, (y0 + y1 + 1) / 2.0

    out = QImage(w, h, QImage.Format_ARGB32)
    out.fill(Qt.transparent)  # 全透明像素 RGB 已是 0，无需再写
    ob = out.bits()

    for y in range(h):
        abase = y * w
        prow = alpha[abase : abase + w]
        base = y * stride
        # 形状是凸的：每行不透明像素是一段连续区间，整段用切片批量拷贝
        try:
            xa = prow.index(255)
            xb = len(prow) - 1 - prow[::-1].index(255)
        except ValueError:
            xa, xb = 0, -1  # 整行都是半透明/全透明
        if xb >= xa:
            ob[base + xa * 4 : base + (xb + 1) * 4] = src[
                base + xa * 4 : base + (xb + 1) * 4
            ]
        for x in range(w):
            a = prow[x]
            if a == 255:
                continue
            o = base + x * 4
            if a == 0:
                continue  # 保持 fill() 的 (0,0,0,0)
            ob[o + 3] = a
            # 向心推进，取第一个完全不透明像素的颜色
            dx, dy = cx - x, cy - y
            norm = math.hypot(dx, dy)
            if norm < 1e-6:
                ob[o] = ob[o + 1] = ob[o + 2] = 255
                continue
            ux, uy = dx / norm, dy / norm
            for step in BLEED_STEPS:
                sx = int(round(x + ux * step))
                sy = int(round(y + uy * step))
                if sx < 0:
                    sx = 0
                elif sx >= w:
                    sx = w - 1
                if sy < 0:
                    sy = 0
                elif sy >= h:
                    sy = h - 1
                if alpha[sy * w + sx] == 255:
                    so = sy * stride + sx * 4
                    ob[o] = src[so]
                    ob[o + 1] = src[so + 1]
                    ob[o + 2] = src[so + 2]
                    break

    out.setDevicePixelRatio(1.0)
    return out


def recenter(img: QImage, bbox) -> QImage:
    """把形状包围盒中心对齐画布中心（偏移 >= 1px 才做，避免无谓重采样）。"""
    w, h = img.width(), img.height()
    x0, y0, x1, y1 = bbox
    dx = int(round((w - 1) / 2.0 - (x0 + x1) / 2.0))
    dy = int(round((h - 1) / 2.0 - (y0 + y1) / 2.0))
    if dx == 0 and dy == 0:
        return img
    canvas = QImage(w, h, QImage.Format_ARGB32)
    canvas.fill(Qt.transparent)
    p = QPainter(canvas)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    p.drawImage(dx, dy, img)
    p.end()
    return canvas


def matte_master(master_path: Path) -> tuple[QImage, dict]:
    argb = _argb(_read_png(master_path))
    w, h = argb.width(), argb.height()
    lum = _lum_plane(argb)
    bbox, radius = detect_shape(lum, w, h)
    alpha, patched = build_alpha(lum, w, h, bbox, radius)
    alpha = feather(alpha, w, h, FEATHER_PASSES)
    icon = recenter(apply_matte(argb, alpha, bbox), bbox)
    info = {"bbox": bbox, "radius": radius, "guard_patched": patched}
    return icon, info


# ------------------------------------------------------------------ icns / ico
def build_icns(icon1024: QImage, out_path: Path, work_root: Path) -> bool:
    """用系统 iconutil 从 iconset 目录生成 .icns。"""
    if not shutil.which("iconutil"):
        print("  ! 未找到 iconutil（非 macOS？），跳过 icns")
        return False
    set_dir = work_root / "iconbuild" / "LabAssistant.iconset"
    set_dir.mkdir(parents=True, exist_ok=True)
    for base in ICON_SIZES:
        _write_png(_scale(icon1024, base), set_dir / f"icon_{base}x{base}.png")
        _write_png(_scale(icon1024, base * 2), set_dir / f"icon_{base}x{base}@2x.png")
    tmp_out = work_root / "iconbuild" / "icon.icns"
    proc = subprocess.run(
        ["iconutil", "-c", "icns", "-o", str(tmp_out), str(set_dir)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not tmp_out.exists():
        print("  ! iconutil 失败:", (proc.stderr or proc.stdout).strip())
        return False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(tmp_out.read_bytes())
    print(f"  icns {out_path} ({out_path.stat().st_size // 1024} KB)")
    return True


def _dib_bytes(img: QImage) -> bytes:
    """32bpp BI_RGB DIB：BGRA 自下而上 + 全 0 AND mask（老 Windows 也能读）。"""
    w, h = img.width(), img.height()
    argb = _argb(img)
    src = bytes(argb.constBits()[: argb.sizeInBytes()])
    stride = w * 4
    rows = bytearray()
    for y in range(h - 1, -1, -1):
        rows += src[y * stride : (y + 1) * stride]
    mask_row = (w + 31) // 32 * 4
    header = struct.pack(
        "<IiiHHIIiiII",
        40, w, h * 2, 1, 32, 0, len(rows) + mask_row * h, 0, 0, 0, 0,
    )
    return header + bytes(rows) + bytes(mask_row * h)


def _png_bytes(img: QImage) -> bytes:
    buf = QByteArray()
    dev = QBuffer(buf)
    dev.open(QIODevice.WriteOnly)
    if not img.save(dev, "PNG"):
        raise RuntimeError("ICO 内嵌 PNG 编码失败")
    dev.close()
    return bytes(buf.data())


def build_ico(icon1024: QImage, out_path: Path) -> bool:
    """手写 ICO 容器：<=128 用 DIB，256 用内嵌 PNG。"""
    entries: list[tuple[int, bytes]] = []
    for size in ICO_SIZES:
        img = _scale(icon1024, size)
        entries.append((size, _png_bytes(img) if size >= 256 else _dib_bytes(img)))

    count = len(entries)
    base_off = 6 + 16 * count
    dirblock = bytearray()
    payload = bytearray()
    for size, data in entries:
        b = 0 if size >= 256 else size  # 256 在目录里用 0 表示
        dirblock += struct.pack(
            "<BBBBHHII", b, b, 0, 0, 1, 32, len(data), base_off + len(payload)
        )
        payload += data
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(struct.pack("<HHH", 0, 1, count) + bytes(dirblock) + bytes(payload))
    print(f"  ico  {out_path} ({count} 尺寸: {', '.join(str(s) for s, _ in entries)})")
    return True


# ------------------------------------------------------------------ 侧栏品牌标
def build_brand_mark(icon1024: QImage, size: int = 256) -> QImage:
    """侧栏品牌标：从 1024 master 多步降采样，34~40px 仍要清晰。"""
    return _scale(icon1024, size)


# ------------------------------------------------------------------ 极光壁纸
# 光斑用加法混合（CompositionMode_Plus）叠加在深底上，才能出极光的
# 「亮核 + 彩色衰减」；用普通 alpha 混合会被洗成一片奶蓝。
AURORA_BASE_TOP = "#0A1230"
AURORA_BASE_BOTTOM = "#132A55"
AURORA_BLOBS = (
    # (cx, cy, radius, color, peak_value)  归一化坐标，radius 相对短边
    (0.16, 0.10, 0.62, "#1E4ED8", 172),  # 主蓝，左上
    (0.44, 0.32, 0.42, "#5B3BE0", 104),  # 蓝紫过渡
    (0.20, 0.82, 0.54, "#8B32E8", 150),  # 紫，左下
    (0.62, 0.64, 0.30, "#3A6BF0", 74),   # 中蓝
    (0.90, 0.50, 0.54, "#12C8C8", 132),  # 青，右侧
    (0.98, 0.96, 0.42, "#0E9AA8", 108),  # 深青，右下
    (0.74, 0.06, 0.30, "#3E8CFF", 92),   # 亮蓝，右上
    (0.34, 0.46, 0.14, "#9FD8FF", 34),   # 小范围冷高光
)
AURORA_BANDS = (
    # (x0, y0, x1, y1, peak) 斜向极光「帘幕」亮带
    (0.00, 0.92, 0.46, 0.00, 26),
    (0.26, 1.04, 0.86, 0.08, 20),
    (0.62, 1.08, 1.10, 0.34, 24),
)


def build_aurora(w: int = 1600, h: int = 1000) -> QImage:
    """蓝 -> 紫 -> 青 极光渐变，纯 QRadialGradient 绘制。"""
    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(QColor(AURORA_BASE_TOP))
    p = QPainter(img)
    p.setPen(Qt.NoPen)

    base = QLinearGradient(QPointF(0, 0), QPointF(w * 0.6, h))
    base.setColorAt(0.0, QColor(AURORA_BASE_TOP))
    base.setColorAt(1.0, QColor(AURORA_BASE_BOTTOM))
    p.setBrush(base)
    p.drawRect(0, 0, w, h)

    short = float(min(w, h))
    p.setCompositionMode(QPainter.CompositionMode_Plus)
    for cx, cy, rad, color, peak in AURORA_BLOBS:
        grad = QRadialGradient(QPointF(cx * w, cy * h), rad * short * 1.6)
        core, mid, edge = QColor(color), QColor(color), QColor(color)
        core.setRgb(core.red(), core.green(), core.blue(), peak)
        mid.setRgb(mid.red(), mid.green(), mid.blue(), int(peak * 0.34))
        edge.setRgb(edge.red(), edge.green(), edge.blue(), 0)
        grad.setColorAt(0.0, core)
        grad.setColorAt(0.40, mid)
        grad.setColorAt(1.0, edge)
        p.setBrush(grad)
        p.drawRect(0, 0, w, h)

    for x0, y0, x1, y1, peak in AURORA_BANDS:
        grad = QLinearGradient(QPointF(x0 * w, y0 * h), QPointF(x1 * w, y1 * h))
        grad.setColorAt(0.0, QColor(150, 210, 255, 0))
        grad.setColorAt(0.44, QColor(150, 210, 255, peak))
        grad.setColorAt(0.58, QColor(190, 160, 255, int(peak * 0.7)))
        grad.setColorAt(1.0, QColor(150, 210, 255, 0))
        p.setBrush(grad)
        p.drawRect(0, 0, w, h)

    p.setCompositionMode(QPainter.CompositionMode_SourceOver)
    vig = QRadialGradient(QPointF(w / 2, h / 2), math.hypot(w, h) * 0.60)
    vig.setColorAt(0.50, QColor(4, 8, 28, 0))
    vig.setColorAt(1.0, QColor(4, 8, 28, 130))
    p.setBrush(vig)
    p.drawRect(0, 0, w, h)
    p.end()
    return img


# ------------------------------------------------------------------ 预览合成
def composite(img: QImage, bg: str, size: int) -> QImage:
    out = QImage(size, size, QImage.Format_ARGB32)
    out.fill(QColor(bg))
    p = QPainter(out)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    scaled = _scale(img, size)
    p.drawImage((size - scaled.width()) // 2, (size - scaled.height()) // 2, scaled)
    p.end()
    return out


def contact_sheet(tiles: list[QImage], bg: str, pad: int = 18) -> QImage:
    cell = max(t.width() for t in tiles)
    cols = len(tiles)
    out = QImage(cols * cell + (cols + 1) * pad, cell + 2 * pad, QImage.Format_ARGB32)
    out.fill(QColor(bg))
    p = QPainter(out)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    for i, t in enumerate(tiles):
        x = pad + i * (cell + pad) + (cell - t.width()) // 2
        y = pad + (cell - t.height()) // 2
        p.drawImage(x, y, t)
    p.end()
    return out


def seam_sheet(img: QImage, size: int, left_bg: str, right_bg: str) -> QImage:
    """把图标骑跨在浅/深两种背景的接缝上，专门用来看边缘有没有黑边。"""
    out = QImage(size + 200, size + 100, QImage.Format_ARGB32)
    p = QPainter(out)
    mid = out.width() // 2
    p.fillRect(0, 0, mid, out.height(), QColor(left_bg))
    p.fillRect(mid, 0, out.width() - mid, out.height(), QColor(right_bg))
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    p.drawImage(mid - size // 2, (out.height() - size) // 2, _scale(img, size))
    p.end()
    return out


def corner_zoom(img: QImage, bbox, radius: int, bg: str, tile: int = 64, zoom: int = 8) -> QImage:
    """把四个圆角处放大 8 倍平铺 —— 唯一能真正看出黑边/破角的视角。"""
    x0, y0, x1, y1 = bbox
    k = radius * math.sqrt(2) / 2.0  # 圆弧 45° 端点相对圆心的偏移
    spots = {
        "TL": (x0 + radius - k - tile / 2, y0 + radius - k - tile / 2),
        "TR": (x1 - radius + k - tile / 2, y0 + radius - k - tile / 2),
        "BL": (x0 + radius - k - tile / 2, y1 - radius + k - tile / 2),
        "BR": (x1 - radius + k - tile / 2, y1 - radius + k - tile / 2),
    }
    src = _argb(img)
    gap = 40
    side = tile * zoom
    sheet = QImage(side * 2 + gap, side * 2 + gap, QImage.Format_ARGB32)
    sheet.fill(QColor(bg))
    p = QPainter(sheet)
    p.setRenderHint(QPainter.SmoothPixmapTransform, False)
    for i, (cx, cy) in enumerate(spots.values()):
        cell = QImage(side, side, QImage.Format_ARGB32)
        q = QPainter(cell)
        q.fillRect(0, 0, side, side, QColor(bg))
        q.drawImage(QRect(0, 0, side, side), src,
                    QRect(int(round(cx)), int(round(cy)), tile, tile))
        q.end()
        p.drawImage((i % 2) * (side + gap), (i // 2) * (side + gap), cell)
    p.end()
    return sheet


def write_previews(icon: QImage, brand: QImage, aurora: QImage, out_dir: Path,
                   bbox, radius: int) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for tag, bg in (("light", LIGHT_BG), ("dark", DARK_BG)):
        _write_png(composite(icon, bg, 512), out_dir / f"icon_{tag}_512.png")
        _write_png(composite(brand, bg, 256), out_dir / f"brand_{tag}_256.png")
        _write_png(
            contact_sheet([_scale(icon, s) for s in (16, 24, 32, 40, 64, 128)], bg),
            out_dir / f"sizes_{tag}.png",
        )
        _write_png(seam_sheet(icon, 480, bg, DARK_BG if tag == "light" else LIGHT_BG),
                   out_dir / f"edge_seam_{tag}.png")
        _write_png(corner_zoom(icon, bbox, radius, bg),
                   out_dir / f"corners_zoom8x_{tag}.png")
    strip = QImage(560, 140, QImage.Format_ARGB32)
    strip.fill(QColor("#F4F6FC"))
    p = QPainter(strip)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    for i, s in enumerate((34, 36, 40, 48)):
        p.drawImage(28 + i * 130, (140 - s) // 2, _scale(brand, s))
    p.end()
    _write_png(strip, out_dir / "brand_sidebar_34_36_40_48.png")
    _write_png(aurora, out_dir / "aurora_tile_full.png")
    _write_png(_scale(aurora, 800), out_dir / "aurora_tile_preview.png")


# ------------------------------------------------------------------ 自检
def verify(icon: QImage) -> list[str]:
    """脚本层面的硬指标自检（视觉结论仍以预览图肉眼验收为准）。"""
    msgs: list[str] = []
    w, h = icon.width(), icon.height()
    if (w, h) != (1024, 1024):
        msgs.append(f"! icon.png 尺寸 {w}x{h} != 1024x1024")
    argb = _argb(icon)
    buf = bytes(argb.constBits()[: argb.sizeInBytes()])
    stride = w * 4
    # 四角必须透明
    for name, (x, y) in {
        "TL": (2, 2), "TR": (w - 3, 2), "BL": (2, h - 3), "BR": (w - 3, h - 3),
        "T-mid-corner": (60, 60), "B-mid-corner": (w - 61, h - 61),
    }.items():
        a = buf[y * stride + x * 4 + 3]
        msgs.append(f"  角 {name} alpha={a}")
        if a != 0:
            msgs.append(f"! 角 {name} 不透明 (alpha={a})")
    # 半透明带里不能藏暗色：alpha 在 1..254 的像素，其 RGB 亮度必须够高
    dark_fringe = 0
    checked = 0
    for y in range(h):
        base = y * stride
        for x in range(w):
            o = base + x * 4
            a = buf[o + 3]
            if 0 < a < 255:
                checked += 1
                m = max(buf[o], buf[o + 1], buf[o + 2])
                if m < 90:
                    dark_fringe += 1
    msgs.append(f"  半透明像素 {checked} 个，其中 RGB 偏暗(<90) {dark_fringe} 个")
    if dark_fringe:
        msgs.append(f"! 半透明带残留暗色像素 {dark_fringe} 个（可能有黑边）")
    # 中心不透明
    ca = buf[(h // 2) * stride + (w // 2) * 4 + 3]
    if ca != 255:
        msgs.append(f"! 中心 alpha={ca} 不是完全不透明")
    # 形状居中 & 未变形
    xs, ys = [], []
    for y in range(0, h, 2):
        base = y * stride
        for x in range(0, w, 2):
            if buf[base + x * 4 + 3] > 127:
                xs.append(x)
                ys.append(y)
    if xs:
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        bw, bh = max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
        msgs.append(f"  结果包围盒 {bw}x{bh} 中心 ({cx:.1f},{cy:.1f}) "
                    f"长宽比 {bw/bh:.4f}")
        if abs(cx - (w - 1) / 2) > 1.5 or abs(cy - (h - 1) / 2) > 1.5:
            msgs.append("! 图形未居中")
    return msgs


def verify_containers(out_dir: Path) -> list[str]:
    """用 Qt 自带的 icns/ico 插件把产物读回来，确认容器真的可解析。"""
    from PySide6.QtGui import QIcon

    msgs: list[str] = []
    for name in ("icon.icns", "icon.ico"):
        path = out_dir / name
        if not path.exists():
            msgs.append(f"! {name} 不存在")
            continue
        icon = QIcon(str(path))
        sizes = sorted({s.width() for s in icon.availableSizes()})
        head = bytes(path.read_bytes()[:12])
        msgs.append(f"  {name}: {path.stat().st_size} bytes, "
                    f"header={head.hex()}, Qt 可读尺寸={sizes}")
        if not sizes:
            msgs.append(f"! {name} Qt 读不出任何尺寸，容器可能写坏了")
    return msgs


# ------------------------------------------------------------------ 主流程
def main() -> int:
    global FEATHER_PASSES
    ap = argparse.ArgumentParser(description="生成 LabAssistant 全套图标")
    ap.add_argument("master", nargs="?", default=DEFAULT_MASTER,
                    help="AI 生成的纯黑底 squircle master PNG（1024x1024）")
    ap.add_argument("--out", default="assets", help="产物目录（默认 assets）")
    ap.add_argument("--preview-dir", default="/tmp/labassistant_icon_preview")
    ap.add_argument("--feather", type=int, default=FEATHER_PASSES,
                    help="羽化次数，1 次约 1px（默认 %(default)s）")
    ap.add_argument("--brand-size", type=int, default=256)
    ap.add_argument("--skip-icns", action="store_true")
    ap.add_argument("--skip-preview", action="store_true")
    args = ap.parse_args()
    FEATHER_PASSES = max(0, min(3, args.feather))

    master = Path(args.master).expanduser().resolve()
    if not master.exists():
        print(f"master 不存在: {master}", file=sys.stderr)
        return 2
    out_dir = Path(args.out).expanduser()
    if not out_dir.is_absolute():
        out_dir = (Path.cwd() / out_dir).resolve()
    preview_dir = Path(args.preview_dir).expanduser()

    print(f"master: {master}")
    icon, info = matte_master(master)
    b = info["bbox"]
    print(f"  检测 bbox=({b[0]},{b[1]})-({b[2]},{b[3]}) {b[2]-b[0]+1}x{b[3]-b[1]+1}px "
          f"圆角 r={info['radius']} 保险补洞={info['guard_patched']}px "
          f"羽化={FEATHER_PASSES}次")

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_png(icon, out_dir / "icon.png")
    print(f"  png  {out_dir/'icon.png'} {icon.width()}x{icon.height()}")

    brand = build_brand_mark(icon, args.brand_size)
    _write_png(brand, out_dir / "brand_mark.png")
    print(f"  brand {out_dir/'brand_mark.png'} {brand.width()}x{brand.height()}")

    aurora = build_aurora(1600, 1000)
    _write_png(aurora, out_dir / "aurora_tile.png")
    print(f"  aurora {out_dir/'aurora_tile.png'} {aurora.width()}x{aurora.height()}")

    ok_icns, ok_ico = True, False
    work_root = Path(tempfile.mkdtemp(prefix="labassistant_icons_"))
    try:
        if args.skip_icns:
            ok_icns = True
        else:
            ok_icns = build_icns(icon, out_dir / "icon.icns", work_root)
        ok_ico = build_ico(icon, out_dir / "icon.ico")
    finally:
        shutil.rmtree(work_root, ignore_errors=True)

    for line in verify(icon):
        print(line)
    for line in verify_containers(out_dir):
        print(line)

    if not args.skip_preview:
        write_previews(icon, brand, aurora, preview_dir, info["bbox"], info["radius"])
        print(f"  预览: {preview_dir}")

    print("结果:", "icns OK" if ok_icns else "icns SKIP", "|",
          "ico OK" if ok_ico else "ico FAILED")
    return 0 if (ok_icns and ok_ico) else 1


if __name__ == "__main__":
    _app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    raise SystemExit(main())

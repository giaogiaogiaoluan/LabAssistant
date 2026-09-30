#!/usr/bin/env python3
"""Build LabOS app assets from a transparent square logo master."""
from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"


def scaled(image: QImage, size: int) -> QImage:
    return image.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def png_bytes(image: QImage) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise RuntimeError("PNG encoding failed")
    buffer.close()
    return bytes(data)


def build(master: Path) -> None:
    source = QImage(str(master))
    if source.isNull() or source.width() != source.height() or not source.hasAlphaChannel():
        raise ValueError("Expected square transparent PNG master")
    ASSETS.mkdir(exist_ok=True)
    icon = scaled(source, 1024)
    icon.save(str(ASSETS / "icon.png"), "PNG")
    scaled(source, 256).save(str(ASSETS / "brand_mark.png"), "PNG")
    # Modern ICNS stores PNG-compressed representations in named chunks.
    chunks = []
    for kind, size in ((b"icp4", 16), (b"icp5", 32), (b"icp6", 64),
                       (b"ic07", 128), (b"ic08", 256), (b"ic09", 512),
                       (b"ic10", 1024)):
        payload = png_bytes(scaled(source, size))
        chunks.append(kind + struct.pack(">I", len(payload) + 8) + payload)
    body = b"".join(chunks)
    (ASSETS / "icon.icns").write_bytes(b"icns" + struct.pack(">I", len(body) + 8) + body)
    # ICO entries can contain PNG data (Windows Vista and later).
    sizes = (16, 24, 32, 48, 64, 128, 256)
    parts = [png_bytes(scaled(source, size)) for size in sizes]
    header = struct.pack("<HHH", 0, 1, len(parts))
    offset = 6 + len(parts) * 16
    entries = []
    for size, data in zip(sizes, parts):
        entries.append(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0,
                                   1, 32, len(data), offset))
        offset += len(data)
    (ASSETS / "icon.ico").write_bytes(header + b"".join(entries) + b"".join(parts))


if __name__ == "__main__":
    build(Path(sys.argv[1]))

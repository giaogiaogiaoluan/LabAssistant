"""LabAssistantServer 简易 GUI（PySide6）。

包含：启动/停止、端口与地址、复制地址/令牌、设备列表、最后同步、
备份、打开数据目录/日志、开机自启(Windows 注册表)、启动最小化到托盘。
"""

from __future__ import annotations

import os
import socket
import sys
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from labassistant_server.runner import SyncServer
from labassistant_server.store import backup_db_file, server_data_dir
from labassistant_shared.httpc import http_json
from labassistant.ui import theme as T


def _pick_address() -> str:
    """优先选 Tailscale 风格 100.x 地址，其次第一个内网 IP；找不到返回主机名。"""
    cands = []
    hostname = socket.gethostname()
    try:
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in cands:
                cands.append(ip)
    except OSError:
        pass
    for ip in cands:
        if ip.startswith("100."):
            return ip
    return cands[0] if cands else hostname


def _copy_text(text: str, parent) -> None:
    QApplication.clipboard().setText(text)
    QMessageBox.information(parent, "已复制", text)


class ServerGui(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("LabAssistant Sync Server")
        self.resize(760, 560)
        self._server: SyncServer | None = None
        self._data_dir: Path | None = None
        self._build()
        self._load_state()
        # 定时刷新设备与日志
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(3000)
        self._refresh()

    # ---------------- UI ----------------
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)

        head = QHBoxLayout()
        self.dot = QLabel("●")
        self.dot.setStyleSheet(f"color:{T.MUTED}; font-size:14pt;")
        self.status_lab = QLabel("已停止")
        self.status_lab.setStyleSheet(f"font-size:12pt; font-weight:700; color:{T.TEXT};")
        head.addWidget(self.dot)
        head.addWidget(self.status_lab)
        head.addStretch(1)
        self.addr_lab = QLabel("")
        self.addr_lab.setStyleSheet(f"color:{T.TEXT_SECONDARY};")
        head.addWidget(self.addr_lab)
        root.addLayout(head)

        row = QHBoxLayout()
        self.b_start = QPushButton("启动服务器")
        self.b_start.setStyleSheet(
            f"background:{T.GREEN_INK}; color:{T.ON_ACCENT}; padding:6px 18px;"
            f"border:1px solid {T.BORDER}; border-radius:{T.RADIUS_MD}px;")
        self.b_stop = QPushButton("停止服务器")
        self.b_stop.setObjectName("DangerText")
        self.b_copy_addr = QPushButton("复制服务器地址")
        self.b_copy_token = QPushButton("复制访问令牌")
        self.b_backup = QPushButton("备份数据库")
        self.b_dir = QPushButton("打开数据目录")
        self.b_log = QPushButton("打开日志")
        for b in (self.b_copy_addr, self.b_copy_token, self.b_backup, self.b_dir, self.b_log):
            b.setObjectName("Ghost")
        row.addWidget(self.b_start)
        row.addWidget(self.b_stop)
        row.addSpacing(14)
        row.addWidget(self.b_copy_addr)
        row.addWidget(self.b_copy_token)
        row.addWidget(self.b_backup)
        row.addStretch(1)
        row.addWidget(self.b_dir)
        row.addWidget(self.b_log)
        root.addLayout(row)

        info = QFrame()
        info.setStyleSheet(
            f"background:{T.CARD}; border:1px solid {T.BORDER}; border-radius:{T.RADIUS_LG}px;")
        layi = QVBoxLayout(info)
        self.detail_lab = QLabel("")
        self.detail_lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layi.addWidget(self.detail_lab)
        root.addWidget(info)

        t = QLabel("已连接设备")
        t.setStyleSheet("font-weight:600;")
        root.addWidget(t)
        self.dev_table = QTableWidget(0, 3)
        self.dev_table.setHorizontalHeaderLabels(["设备名", "设备 ID", "最后在线"])
        self.dev_table.verticalHeader().setVisible(False)
        from PySide6.QtWidgets import QAbstractItemView
        self.dev_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.dev_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        root.addWidget(self.dev_table, 1)

        foot = QHBoxLayout()
        self.auto_chk = QCheckBox("登录 Windows 后自动启动服务器")
        self.tray_chk = QCheckBox("启动后最小化到托盘")
        b_apply = QPushButton("应用开机自启设置")
        b_apply.clicked.connect(self._apply_autostart)
        foot.addWidget(self.auto_chk)
        foot.addStretch(1)
        foot.addWidget(self.tray_chk)
        foot.addWidget(b_apply)
        root.addLayout(foot)

        # 事件
        self.b_start.clicked.connect(self._on_start)
        self.b_stop.clicked.connect(self._on_stop)
        self.b_copy_addr.clicked.connect(self._on_copy_addr)
        self.b_copy_token.clicked.connect(self._on_copy_token)
        self.b_backup.clicked.connect(self._on_backup)
        self.b_dir.clicked.connect(lambda: self._open_path(self._data_dir))
        self.b_log.clicked.connect(lambda: self._open_path(self._log_file()))

    # ---------------- 状态 ----------------
    def _load_state(self):
        self._data_dir = server_data_dir()
        cfg_port = 8765
        try:
            from labassistant_server.store import load_config
            self._cfg = load_config(self._data_dir)
            cfg_port = int(self._cfg.get("port", 8765))
        except Exception:
            self._cfg = {}
        self.port = cfg_port
        ip = _pick_address()
        self.addr_lab.setText(f"端口 {self.port} · 本机地址 http://{ip}:{self.port}")
        self._refresh()

    def _log_file(self) -> Path:
        logs = self._data_dir / "logs"
        logs.mkdir(exist_ok=True)
        return logs / "server.log"

    def _refresh(self):
        if self._server is not None and self._server.running:
            self.dot.setStyleSheet(f"color:{T.GREEN_INK}; font-size:14pt;")
            self.status_lab.setText("● Running")
        else:
            err = getattr(self._server, "last_error", "") if self._server else ""
            if err:
                self.dot.setStyleSheet(f"color:{T.RED}; font-size:14pt;")
                self.status_lab.setText(f"⚠ 启动失败：{err}")
            else:
                self.dot.setStyleSheet(f"color:{T.MUTED}; font-size:14pt;")
                self.status_lab.setText("○ 已停止")
        self.b_start.setEnabled(self._server is None or not self._server.running)
        self.b_stop.setEnabled(self._server is not None and self._server.running)
        # 设备
        try:
            devs = self._read_devices()
        except Exception:
            devs = []
        self.dev_table.setRowCount(len(devs))
        for r, d in enumerate(devs):
            for c, val in enumerate([d.get("name", ""), d.get("device_id", ""), (d.get("last_seen") or "").replace("T", " ")[:19]]):
                self.dev_table.setItem(r, c, QTableWidgetItem(str(val)))
        # 详情（令牌与两端地址这里直接显示，方便复制）
        n_dev = len(devs)
        ls = "—"
        if devs:
            ls = (devs[0].get("last_seen") or "").replace("T", " ")[:19]
        token = str(self._cfg.get("token") or "")
        local_ip = _pick_address()
        self.detail_lab.setText(
            f"【访问令牌】（Windows 与 Mac 客户端都填这个，点上方“复制访问令牌”）\n"
            f"{token or '（未生成，请先点一次启动服务器）'}\n\n"
            f"【服务器地址】\n"
            f"· Windows 客户端（本机）填：http://127.0.0.1:{self.port}\n"
            f"· Mac / 其它设备填：http://{local_ip}:{self.port}\n\n"
            f"服务器数据目录：{self._data_dir}\n"
            f"数据库：{self._data_dir / 'server.db'}\n"
            f"已连接设备：{n_dev} 台   最后同步：{ls}")

    def _read_devices(self) -> list[dict]:
        # 独立短连接读取，避免与 uvicorn 线程争用同一个连接
        import sqlite3
        p = self._data_dir / "server.db"
        if not p.exists():
            return []
        con = sqlite3.connect(str(p))
        try:
            rows = con.execute(
                "SELECT device_id, name, last_seen FROM devices ORDER BY last_seen DESC"
            ).fetchall()
            return [{"device_id": r[0], "name": r[1], "last_seen": r[2]} for r in rows]
        finally:
            con.close()

    # ---------------- 动作 ----------------
    def _ensure_server(self) -> SyncServer:
        if self._server is None:
            self._server = SyncServer(port=self.port, data_dir=self._data_dir)
        return self._server

    def _on_start(self):
        try:
            srv = self._ensure_server()
            srv.start()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "启动失败", str(exc))
            return
        self._log(f"[server] started on port {self.port}")
        self._refresh()

    def _on_stop(self):
        if self._server is not None:
            self._server.stop()
        self._log("[server] stopped")
        self._refresh()

    def _on_copy_addr(self):
        _copy_text(f"http://{_pick_address()}:{self.port}", self)

    def _on_copy_token(self):
        cfg = self._cfg or {}
        _copy_text(str(cfg.get("token", "")), self)

    def _on_backup(self):
        try:
            p = self._data_dir / "backups"
            p.mkdir(exist_ok=True)
            dest = p / f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
            backup_db_file(self._data_dir / "server.db", dest)
            QMessageBox.information(self, "备份完成", f"已备份到：\n{dest}")
            self._log(f"[backup] wrote {dest.name}")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "备份失败", str(exc))

    def _open_path(self, p: Path | None):
        if not p:
            return
        try:
            if os.name == "nt":
                os.startfile(str(p))  # noqa: S606
            else:
                import subprocess
                subprocess.Popen(["open", str(p)])
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "无法打开", str(exc))

    # ---------------- 开机自启（Windows 注册表） ----------------
    def _apply_autostart(self):
        if os.name != "nt":
            QMessageBox.information(self, "开机自启", "开机自启仅 Windows 可用。")
            return
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        try:
            exe = Path(sys.executable if getattr(sys, "frozen", False) else __file__)
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as k:
                if self.auto_chk.isChecked():
                    winreg.SetValueEx(k, "LabAssistantServer", 0, winreg.REG_SZ, f'"{exe}" --headless')
                else:
                    try:
                        winreg.DeleteValue(k, "LabAssistantServer")
                    except FileNotFoundError:
                        pass
            QMessageBox.information(self, "开机自启", "已更新开机自启设置。")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "开机自启", f"设置失败：{exc}")

    # ---------------- 日志 ----------------
    def _log(self, msg: str):
        try:
            f = self._log_file()
            if f.exists() and f.stat().st_size > 512 * 1024:
                f.with_suffix(".log.1").write_text(f.read_text(encoding="utf-8", errors="replace"),
                                                   encoding="utf-8")
            with open(f, "a", encoding="utf-8") as fh:
                fh.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
        except Exception:
            pass

    def closeEvent(self, ev):
        self._timer.stop()
        super().closeEvent(ev)

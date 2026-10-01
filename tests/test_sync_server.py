"""同步端到端测试：真实 FastAPI 服务器（后台线程） + 两个本地客户端库。

覆盖：初次全量上传、下拉、双向修改、软删除不再复活、空客户端不删服务器、
网站模块双向同步、LWW 冲突收敛、离线等待同步计数。
"""

import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for p in (SRC, ROOT / ".pylibs"):
    if p not in sys.path:
        sys.path.insert(0, str(p))

from labassistant.db import Database
from labassistant.sync import client as sync_client
from labassistant.sync.engine import SyncEngine
from labassistant.services import attendance as att
from labassistant.services import holidays as hds
from labassistant.services import courses as crs
from labassistant.services import todos as tds
from labassistant.services import websites as ws
from labassistant_shared.httpc import http_json

M = 60
TMP = ROOT / "tests" / ".tmpdb"


def make_db(name: str) -> Database:
    TMP.mkdir(exist_ok=True)
    return Database(TMP / f"{name}_{uuid.uuid4().hex[:8]}.db")


class SyncServerFixture:
    """在后台线程启动真实 uvicorn，提供 (url, token)。"""

    def __init__(self):
        from labassistant_server.runner import SyncServer, _free_port
        self.port = _free_port()
        self.srv = SyncServer(port=self.port, data_dir=TMP / f"srv_{uuid.uuid4().hex[:6]}")
        self.srv.start()
        self.url = f"http://127.0.0.1:{self.port}"
        self.wait_online()

    def wait_online(self, timeout: float = 8.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            status, _body, err = http_json("GET", self.url + "/health", timeout=1)
            if status == 200:
                return
            time.sleep(0.2)
        raise RuntimeError("server did not come online")

    def wait_offline(self, timeout: float = 8.0):
        """等待服务器真正不可达（用于模拟离线）。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            status, _body, _err = http_json("GET", self.url + "/health", timeout=0.8)
            if status is None:
                return
            time.sleep(0.2)
        raise RuntimeError("server still online")

    def stop(self):
        try:
            self.srv.stop()
            self.srv.close()
        except Exception:
            pass


def enable(engine: SyncEngine, server_url: str, token: str):
    db = engine.db
    db.set_setting("sync_enabled", "1")
    db.set_setting("server_url", server_url)
    db.set_setting("sync_token", token)
    engine.set_revision(0)


def test_首次全量上传与下拉_删除不复活():
    fx = SyncServerFixture()
    try:
        a, b = make_db("a"), make_db("b")
        ea = SyncEngine(a, http_json)
        eb = SyncEngine(b, http_json)
        enable(ea, fx.url, fx.srv.token)
        enable(eb, fx.url, fx.srv.token)

        # A：新增 Todo + 打卡 + 课程
        tds.add_todo(a, "2026-09-08", "论文阅读", est_minutes=120, priority="高")
        att.add_block(a, "2026-09-08", 8 * M, 12 * M, "上午")
        cid = crs.add_course(a, "系统数理基础", 0, 9 * M + 50, 11 * M + 25,
                             __import__("datetime").date(2026, 9, 7),
                             __import__("datetime").date(2026, 12, 28))
        crs.upsert_exception(a, cid, "2026-09-14", "cancelled", note="放假")

        r1 = ea.run_once()
        assert r1["ok"] and r1["pushed"] >= 4, r1
        # 同步到 B
        r2 = eb.run_once()
        assert r2["ok"], r2
        assert len(tds.list_todos(b)) == 1
        assert len(att.list_blocks(b, "2026-09-08")) == 1
        assert len(crs.list_courses(b)) == 1
        assert len(crs.list_exceptions(b)) == 1

        # A 删除课程（软删除）→ 同步 → B 消失，且不复活
        crs.delete_course(a, cid)
        assert crs.list_courses(a) == []
        ea.run_once()
        eb.run_once()
        assert crs.list_courses(b) == []
        eb.run_once()  # 再次同步，不能复活
        assert crs.list_courses(b) == []
        a.close(); b.close()
    finally:
        fx.stop()


def test_补班规则和百分之一小时跨设备同步():
    from datetime import date
    from labassistant.services import aggregate as agg
    fx = SyncServerFixture()
    try:
        a, b = make_db("special_a"), make_db("special_b")
        ea, eb = SyncEngine(a, http_json), SyncEngine(b, http_json)
        enable(ea, fx.url, fx.srv.token)
        enable(eb, fx.url, fx.srv.token)
        hds.add_special_day(a, "2026-10-10", "makeup", "国庆调休")
        att.add_manual(a, "2026-10-10", 1.23 * 60, "补打卡")
        assert ea.run_once()["ok"]
        assert eb.run_once()["ok"]
        day = agg.day_summary(b, date(2026, 10, 10))
        assert day["required_min"] == 480
        assert day["special_type"] == "makeup"
        assert day["manual_min"] == 73.8
        a.close(); b.close()
    finally:
        fx.stop()


def test_空客户端不删除服务器数据():
    fx = SyncServerFixture()
    try:
        a, b = make_db("a_full"), make_db("b_empty")
        ea, eb = SyncEngine(a, http_json), SyncEngine(b, http_json)
        enable(ea, fx.url, fx.srv.token)
        enable(eb, fx.url, fx.srv.token)
        tds.add_todo(a, "2026-09-09", "服务器里的数据", est_minutes=30)
        ea.run_once()
        # 空 B 首次同步：应拿到服务器数据，而不是把服务器清空
        assert eb.run_once()["ok"]
        assert len(tds.list_todos(b)) == 1
        # 服务器数据仍在（A 再拉也一样）
        ea.run_once()
        assert len(tds.list_todos(a)) == 1
        a.close(); b.close()
    finally:
        fx.stop()


def test_双向修改与软删除_网站():
    fx = SyncServerFixture()
    try:
        a, b = make_db("a_web"), make_db("b_web")
        ea, eb = SyncEngine(a, http_json), SyncEngine(b, http_json)
        enable(ea, fx.url, fx.srv.token)
        enable(eb, fx.url, fx.srv.token)

        ws.add_website(a, "Google Scholar", "https://scholar.google.com", "academic", "检索")
        ea.run_once()
        eb.run_once()
        got = ws.list_websites(b)
        assert got and got[0]["name"] == "Google Scholar" and got[0]["category"] == "academic"

        # B 编辑分类并同步回 A
        wid = got[0]["id"]
        ws.update_website(b, wid, "Google Scholar", "https://scholar.google.com", "tools", "改备注")
        eb.run_once()
        ea.run_once()
        assert ws.list_websites(a)[0]["category"] == "tools"

        # B 删除 → A 同步后消失且不复活
        ws.delete_website(b, wid)
        eb.run_once()
        ea.run_once()
        assert ws.list_websites(a) == []
        ea.run_once()
        assert ws.list_websites(a) == []
        a.close(); b.close()
    finally:
        fx.stop()


def test_冲突_LastWriteWins():
    fx = SyncServerFixture()
    try:
        a, b = make_db("a_lww"), make_db("b_lww")
        ea, eb = SyncEngine(a, http_json), SyncEngine(b, http_json)
        enable(ea, fx.url, fx.srv.token)
        enable(eb, fx.url, fx.srv.token)
        tds.add_todo(a, "2026-09-10", "初始", est_minutes=60)
        ea.run_once()
        eb.run_once()
        tid_a = tds.list_todos(a)[0]["id"]
        tid_b = tds.list_todos(b)[0]["id"]
        # 服务器离线：两端分别修改同一条
        fx.srv.stop()
        fx.wait_offline()
        tds.update_todo(a, tid_a, "2026-09-10", "版本A", True, 60, "高", "", "")
        time.sleep(1.1)
        tds.update_todo(b, tid_b, "2026-09-10", "版本B", True, 60, "高", "", "")
        # 服务器恢复：A 先推、B 后推（B 更新，LWW 应取 B）
        fx.srv.start()
        fx.wait_online()
        assert ea.run_once()["ok"]
        assert eb.run_once()["ok"]
        # A 拉回后应看到 B 版本（三者一致）
        ea.run_once()
        titles = {tds.list_todos(a)[0]["title"], tds.list_todos(b)[0]["title"]}
        assert titles == {"版本B"}, titles
        a.close(); b.close()
    finally:
        fx.stop()


def test_快照兜底_客户端游标超前():
    """服务器事件日志比客户端游标旧（如服务器库重建）时，应快照补全而不是永远拉不到。"""
    fx = SyncServerFixture()
    try:
        a, b = make_db("a_snap"), make_db("b_snap")
        ea, eb = SyncEngine(a, http_json), SyncEngine(b, http_json)
        enable(ea, fx.url, fx.srv.token)
        enable(eb, fx.url, fx.srv.token)
        tds.add_todo(a, "2026-09-20", "快照数据", est_minutes=45)
        assert ea.run_once()["ok"]
        # 模拟 B 的游标已超前（例如 B 曾连过一个被重置的服务器）
        eb.set_revision(99999)
        res = eb.run_once()
        assert res["ok"], res
        titles = [t["title"] for t in tds.list_todos(b)]
        assert "快照数据" in titles, titles
        # 快照后游标被校正，后续增量正常
        assert eb.revision() < 99999
        a.close(); b.close()
    finally:
        fx.stop()


def test_todo_删除跨设备_不复活():
    fx = SyncServerFixture()
    try:
        a, b = make_db("a_tdel"), make_db("b_tdel")
        ea, eb = SyncEngine(a, http_json), SyncEngine(b, http_json)
        enable(ea, fx.url, fx.srv.token)
        enable(eb, fx.url, fx.srv.token)
        tds.add_todo(a, "2026-09-21", "要删除的Todo", est_minutes=30)
        assert ea.run_once()["ok"]
        assert eb.run_once()["ok"]
        assert len(tds.list_todos(b)) == 1
        tid = tds.list_todos(a)[0]["id"]
        tds.delete_todo(a, tid)          # 软删除
        assert tds.list_todos(a) == []
        assert ea.run_once()["ok"]
        assert eb.run_once()["ok"]
        assert tds.list_todos(b) == []   # 另一台也消失
        eb.run_once()                    # 再同步不得复活
        assert tds.list_todos(b) == []
        a.close(); b.close()
    finally:
        fx.stop()


def test_离线时本地可用_等待计数():
    fx = SyncServerFixture()
    try:
        a = make_db("a_off")
        ea = SyncEngine(a, http_json)
        enable(ea, fx.url, fx.srv.token)
        fx.srv.stop()  # 服务器离线
        fx.wait_offline()
        tds.add_todo(a, "2026-09-11", "离线新增", est_minutes=45)
        assert len(tds.list_todos(a)) == 1          # 离线仍可写
        res = ea.run_once()                          # 推送失败 -> 保留 dirty
        assert res["ok"] is False
        assert ea.pending_count() >= 1
        fx.srv.start()
        fx.wait_online()
        assert ea.run_once()["ok"]
        assert ea.pending_count() == 0
        a.close()
    finally:
        fx.stop()

"""随手记规则 + 加密密码本的回归测试。"""

from __future__ import annotations

from datetime import date

import pytest

from labassistant.db import Database
from labassistant.services import capture as cap
from labassistant.services import vault

TODAY = date(2026, 9, 22)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """每个用例一套独立库 + 独立密钥文件，绝不碰真实数据目录。"""
    monkeypatch.setattr(vault, "_data_key_cache", None, raising=False)
    monkeypatch.setattr(vault, "keyfile_path", lambda: tmp_path / "vault.key")
    return Database(tmp_path / "t.db")


# ---------------------------------------------------------------- 规则
@pytest.mark.parametrize("text,kind", [
    ("明天记得交实验报告", "todo"),
    ("9/25 14:00-17:00 实验室", "lab"),
    ("在实验室待了3小时", "lab"),
    ("收藏 https://arxiv.org/list/cs.CV/recent", "website"),
    ("示例网站 账号 user@example.com 密码 Example123", "credential"),
    ("记一下 github 用户名 demo_user 口令 Example456", "credential"),
    ("周五调休一天", "holiday"),
    ("买牛奶", "note"),
    ("", "note"),
])
def test_classify(text, kind):
    assert cap.classify(text, TODAY)["kind"] == kind


def test_date_tokens():
    assert cap.find_date("明天交报告", TODAY)[0] == "2026-09-23"
    assert cap.find_date("9/25 打卡", TODAY)[0] == "2026-09-25"
    assert cap.find_date("10月3日交材料", TODAY)[0] == "2026-10-03"
    assert cap.find_date("周五开会", TODAY)[0] == "2026-09-25"
    # 没有时间词就落到今天
    assert cap.find_date("随便一句话", TODAY)[0] == "2026-09-22"


def test_time_range_and_duration():
    s, e, _ = cap.find_time_range("14:00-17:00")
    assert (s, e) == (14 * 60, 17 * 60)
    assert cap.find_duration("3小时") == 180
    assert cap.find_duration("90分钟") == 90
    assert cap.find_duration("半小时") == 30


def test_priority_and_title_cleaning():
    v = cap.classify("10月3日 记得交报销材料，预计2小时，紧急", TODAY)
    assert v["kind"] == "todo"
    assert v["fields"]["priority"] == "高"
    assert v["fields"]["est_minutes"] == 120
    assert "紧急" not in v["fields"]["title"]
    assert "记得" not in v["fields"]["title"]


def test_website_category_guess():
    assert cap._guess_category("arxiv.org") == "academic"
    assert cap._guess_category("yjsbtbu.yuketang.cn") == "school"
    assert cap._guess_category("platform.qianwenai.com") == "ai"
    assert cap._guess_category("totally-unknown.example") == "other"


# ---------------------------------------------------------------- 落库
def test_capture_creates_todo(db):
    row = cap.capture(db, "明天记得交实验报告，预计2小时", TODAY)
    assert row["kind"] == "todo"
    assert row["target_table"] == "todos"
    t = db.query_one("SELECT * FROM todos WHERE id=?", (row["target_id"],))
    assert t["date"] == "2026-09-23"
    assert t["est_minutes"] == 120
    assert "实验报告" in t["title"]


def test_capture_creates_attendance_block(db):
    row = cap.capture(db, "9/25 14:00-17:00 实验室", TODAY)
    assert row["target_table"] == "attendance_blocks"
    b = db.query_one("SELECT * FROM attendance_blocks WHERE id=?", (row["target_id"],))
    assert (b["start_min"], b["end_min"]) == (840, 1020)


def test_capture_creates_manual_hours(db):
    row = cap.capture(db, "在实验室待了3小时", TODAY)
    assert row["target_table"] == "manual_hours"
    m = db.query_one("SELECT * FROM manual_hours WHERE id=?", (row["target_id"],))
    assert m["minutes"] == 180


def test_capture_creates_website(db):
    row = cap.capture(db, "收藏 https://arxiv.org/list/cs.CV/recent", TODAY)
    assert row["target_table"] == "websites"
    w = db.query_one("SELECT * FROM websites WHERE id=?", (row["target_id"],))
    assert w["category"] == "academic"
    assert w["url"].startswith("https://arxiv.org")


def test_capture_note_only_stays_in_captures(db):
    row = cap.capture(db, "买牛奶", TODAY)
    assert row["kind"] == "note"
    assert row["target_table"] == ""
    assert db.query_one("SELECT count(*) FROM captures")[0] == 1


def test_undo_removes_created_record(db):
    row = cap.capture(db, "明天记得交实验报告", TODAY)
    assert cap.undo_capture(db, row["id"]) is True
    # 待办是软删除（要留墓碑给同步），所以断言 deleted_at 被置上而不是行消失
    t = db.query_one("SELECT deleted_at FROM todos WHERE id=?", (row["target_id"],))
    assert t is not None and t["deleted_at"]
    assert db.query_one("SELECT count(*) FROM captures")[0] == 0


def test_capture_is_not_synced(db):
    """两张新表都不能进同步协议，否则老版 LabTimeServer 会收到不认识的实体。"""
    from labassistant_shared.protocol import ENTITIES
    tables = {v["table"] for v in ENTITIES.values()}
    assert "captures" not in tables
    assert "vault_items" not in tables


# ---------------------------------------------------------------- 密码本
def test_vault_roundtrip_and_no_plaintext(db, tmp_path):
    vault.setup(db, None)
    vid = vault.add_item(db, title="example", username="alice", secret="S3cr3t!x")
    assert vault.get_secret(db, vid) == "S3cr3t!" + "x"
    blob = db.query_one("SELECT secret_enc FROM vault_items WHERE id=?", (vid,))["secret_enc"]
    assert blob.startswith("v1.")
    assert "S3cr3t!x" not in blob
    # 整库（含 WAL 边车文件）里都搜不到明文
    for f in sorted(tmp_path.glob("t.db*")):
        if f.is_file():
            assert b"S3cr3t!x" not in f.read_bytes(), f.name


def test_vault_lock_blocks_read(db):
    vault.setup(db, None)
    vid = vault.add_item(db, title="x", username="u", secret="topsecret")
    vault.lock()
    assert vault.get_secret(db, vid) == ""
    assert vault.unlock(db) is True
    assert vault.get_secret(db, vid) == "topsecret"


def test_vault_passphrase_mode(db):
    vault.setup(db, None)
    vid = vault.add_item(db, title="x", username="u", secret="pw-value")
    vault.setup(db, "correct horse battery")
    vault.lock()
    assert vault.unlock(db, "wrong") is False
    assert vault.get_secret(db, vid) == ""
    assert vault.unlock(db, "correct horse battery") is True
    assert vault.get_secret(db, vid) == "pw-value"


def test_vault_update_keeps_secret_when_none(db):
    vault.setup(db, None)
    vid = vault.add_item(db, title="a", username="u", secret="keep-me")
    vault.update_item(db, vid, title="b", username="u2", secret=None)
    assert vault.get_secret(db, vid) == "keep-me"
    row = db.query_one("SELECT title FROM vault_items WHERE id=?", (vid,))
    assert row["title"] == "b"


def test_vault_delete_is_physical(db):
    vault.setup(db, None)
    vid = vault.add_item(db, title="a", username="u", secret="s")
    vault.delete_item(db, vid)
    assert db.query_one("SELECT count(*) FROM vault_items WHERE id=?", (vid,))[0] == 0


def test_capture_credential_goes_to_vault(db):
    row = cap.capture(db, "示例网站 账号 user@example.com 密码 Example123", TODAY)
    assert row["kind"] == "credential"
    assert row["target_table"] == "vault_items"
    assert vault.get_secret(db, row["target_id"]) == "Example123"
    assert db.query_one("SELECT username FROM vault_items WHERE id=?",
                        (row["target_id"],))["username"] == "user@example.com"


def test_capture_credential_undo_removes_vault_row(db):
    row = cap.capture(db, "网盘 账号 demo_user 密码 Example456", TODAY)
    cap.undo_capture(db, row["id"])
    assert db.query_one("SELECT count(*) FROM vault_items")[0] == 0

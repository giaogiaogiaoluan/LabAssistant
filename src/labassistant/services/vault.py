"""密码本：账号密码的加密存储。

安全模型（写清楚，避免误解）：
- 密码**永远不以明文入库**。每条 secret 用一把随机 data key 做 AES-256-GCM 加密，
  密文形如 `v1.<base64(nonce)>.<base64(ct||tag)>`。
- data key 本身也不落明文，而是被一把 KEK 包裹后存在 settings 里：
    * keyfile 模式（默认，零打扰）：KEK = `<数据目录>/vault.key` 里的 32 字节随机密钥，
      文件权限 0600。能读到这个文件的人 = 已经能登录你的账户，属于可接受边界。
    * passphrase 模式（手动开启）：KEK = PBKDF2-HMAC-SHA256(主密码, 随机盐, 310k 轮)。
      此时数据库被拷走也解不开，代价是每次启动要解锁一次。
- 换主密码 / 两种模式互切都只需要重新包裹 data key，不用重加密全部条目。
- 密码本**不参与跨设备同步**（vault_items 不在 protocol.ENTITIES 里）。
"""

from __future__ import annotations

import base64
import os
import secrets as _secrets
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

from labassistant import constants as C
from labassistant.db import Database, now_text

ITERATIONS = 310_000
_CHECK_PLAIN = b"labassistant-vault-ok"

_data_key_cache: bytes | None = None


# ---------------------------------------------------------------- base64 工具
def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _seal(kek: bytes, plain: bytes) -> str:
    """AES-256-GCM 封装：随机 12 字节 nonce 必须与密文一起存，否则解不开。"""
    nonce = _secrets.token_bytes(12)
    return _b64e(nonce + AESGCM(kek).encrypt(nonce, plain, None))


def _open(kek: bytes, blob_b64: str) -> bytes:
    raw = _b64d(blob_b64)
    return AESGCM(kek).decrypt(raw[:12], raw[12:], None)


# ---------------------------------------------------------------- KEK / data key
def keyfile_path() -> Path:
    return C.app_data_dir() / "vault.key"


def _kek_keyfile() -> bytes:
    path = keyfile_path()
    if path.exists():
        raw = path.read_bytes()
        if len(raw) == 32:
            return raw
    key = _secrets.token_bytes(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


def _kek_passphrase(passphrase: str, salt: bytes, iterations: int) -> bytes:
    return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                      iterations=iterations).derive(passphrase.encode("utf-8"))


def mode(db: Database) -> str:
    """'none'（还没建过密码本） | 'keyfile' | 'passphrase'"""
    return db.get_setting("vault_mode") or "none"


def is_unlocked() -> bool:
    return _data_key_cache is not None


def lock() -> None:
    """清掉进程内的 data key（passphrase 模式下生效）。"""
    global _data_key_cache
    _data_key_cache = None


def setup(db: Database, passphrase: str | None = None) -> dict:
    """初始化或切换保护方式。passphrase=None 表示用 keyfile（零打扰）。"""
    global _data_key_cache
    data_key = _data_key_cache or _secrets.token_bytes(32)
    if passphrase:
        salt = _secrets.token_bytes(16)
        kek = _kek_passphrase(passphrase, salt, ITERATIONS)
        check = _seal(kek, _CHECK_PLAIN)
        db.set_setting("vault_salt", _b64e(salt))
        db.set_setting("vault_iters", str(ITERATIONS))
        db.set_setting("vault_check", check)
        m = "passphrase"
    else:
        kek = _kek_keyfile()
        db.set_setting("vault_salt", "")
        db.set_setting("vault_iters", "")
        db.set_setting("vault_check", "")
        m = "keyfile"
    wrapped = _seal(kek, data_key)
    db.set_setting("vault_wrapped_key", wrapped)
    db.set_setting("vault_mode", m)
    _data_key_cache = data_key
    return {"mode": m, "created": True}


def unlock(db: Database, passphrase: str = "") -> bool:
    """解锁并缓存 data key；keyfile 模式无需口令。"""
    global _data_key_cache
    m = mode(db)
    if m == "none":
        setup(db, None)
        return True
    wrapped = db.get_setting("vault_wrapped_key") or ""
    if not wrapped:
        setup(db, None)
        return True
    try:
        if m == "passphrase":
            salt = _b64d(db.get_setting("vault_salt") or "")
            iters = int(db.get_setting("vault_iters") or ITERATIONS)
            kek = _kek_passphrase(passphrase or "", salt, iters)
            check = db.get_setting("vault_check") or ""
            if _open(kek, check) != _CHECK_PLAIN:
                return False
        else:
            kek = _kek_keyfile()
        _data_key_cache = _open(kek, wrapped)
        return True
    except Exception:  # noqa: BLE001 口令错/密钥文件损坏都算解锁失败
        return False


def _require_key() -> bytes:
    if _data_key_cache is None:
        raise RuntimeError("密码本未解锁")
    return _data_key_cache


# ---------------------------------------------------------------- 加解密
def encrypt_secret(plain: str) -> str:
    if not plain:
        return ""
    nonce = _secrets.token_bytes(12)
    ct = AESGCM(_require_key()).encrypt(nonce, plain.encode("utf-8"), None)
    return f"v1.{_b64e(nonce)}.{_b64e(ct)}"   # nonce 单独一段，这里本来就是对的


def decrypt_secret(blob: str) -> str:
    if not blob:
        return ""
    if not blob.startswith("v1."):
        return blob                      # 历史明文（本版本不会产生）原样返回
    try:
        _, n, c = blob.split(".", 2)
        return AESGCM(_require_key()).decrypt(_b64d(n), _b64d(c), None).decode("utf-8")
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------- CRUD
def add_item(db: Database, *, title: str, username: str = "", secret: str = "",
             url: str = "", note: str = "") -> int:
    if _data_key_cache is None:
        unlock(db)
    now = now_text()
    return db.execute(
        "INSERT INTO vault_items(title, username, secret_enc, url, note, created, updated) "
        "VALUES(?,?,?,?,?,?,?)",
        (title.strip(), username.strip(), encrypt_secret(secret), url.strip(),
         note.strip(), now, now))


def update_item(db: Database, item_id: int, *, title: str, username: str = "",
                secret: str | None = None, url: str = "", note: str = "") -> None:
    row = db.query_one("SELECT secret_enc FROM vault_items WHERE id=?", (item_id,))
    if not row:
        return
    # secret=None 表示“保持原值不动”（界面上留空不等于清空密码）
    secret_enc = row["secret_enc"] if secret is None else encrypt_secret(secret)
    db.execute(
        "UPDATE vault_items SET title=?, username=?, secret_enc=?, url=?, note=?, updated=? "
        "WHERE id=?",
        (title.strip(), username.strip(), secret_enc, url.strip(), note.strip(),
         now_text(), item_id))


def list_items(db: Database) -> list[dict]:
    rows = db.query(
        "SELECT id, title, username, url, note, created, updated, "
        "(length(secret_enc) > 0) AS has_secret "
        "FROM vault_items WHERE deleted_at IS NULL ORDER BY id DESC")
    return [dict(r) for r in rows]


def get_secret(db: Database, item_id: int) -> str:
    row = db.query_one("SELECT secret_enc FROM vault_items WHERE id=?", (item_id,))
    return decrypt_secret(row["secret_enc"]) if row else ""


def delete_item(db: Database, item_id: int) -> None:
    """密码条目直接物理删除：软删除会把密文一直留在库里。"""
    db.execute("DELETE FROM vault_items WHERE id=?", (item_id,))

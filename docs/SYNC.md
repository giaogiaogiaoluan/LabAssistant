# LabAssistant 跨平台同步（docs/SYNC.md）

> 维护者须知：本文件描述 Windows LabAssistant.exe ↔ LabAssistantServer.exe ↔ macOS LabAssistant.app
> 之间的记录级同步协议与实现位置。改动协议前务必同步更新本文件与 `src/labassistant_shared/protocol.py`。

---

## 1. 架构总览

```
Windows LabAssistant.exe  ──┐                        ┌── macOS LabAssistant.app
  local.db (OfflineFirst)                        local.db
        │                │                        │
        │  HTTPS/HTTP + Bearer Token              │
        ▼                ▼                        ▼
              LabAssistantServer.exe (Windows 笔记本)
              FastAPI + SQLite, Tailscale 私有网络
```

- 客户端永远是 **Offline First**：先写本地 SQLite，再后台增量同步。
- 服务器只做“同步中继 + 权威 Last-Write-Wins 裁决”，不要求客户端联网才能使用。
- 不使用 OneDrive/Syncthing/SMB/共享文件夹直接同步 db 文件。

## 2. 数据位置

| 角色 | 默认目录 | 覆盖方式 |
| --- | --- | --- |
| Windows Client | exe 同级 `data\`（便携）→ `%APPDATA%\LabAssistant` | `LABASSISTANT_DATA_DIR` |
| Windows Server | exe 同级 `data\`（便携）→ `%LOCALAPPDATA%\LabAssistantServer` | `LABASSISTANTSERVER_DATA_DIR` |
| macOS Client | `.app` 内只读 → `~/Library/Application Support/LabAssistant` | `LABASSISTANT_DATA_DIR` |
| 服务器内部 | `server.db` / `config.json` / `logs/` / `backups/` | 见上 |

## 3. 实体与同步字段

`src/labassistant_shared/protocol.py` 定义参与同步的实体：

- `attendance_block`（打卡时间段）、`manual_hour`（手动时长）、`course`（课程）、
  `course_exception`（单次取消/调整）、`todo`、`holiday`、`website`（网站收藏）、`setting`（业务设置）。

每条记录追加元字段：`sync_uuid`（跨设备唯一，保留本地整数 id 不变）、`created_at`、
`updated_at`、`deleted_at`（软删除墓碑）、`device_id`、`sync_dirty`（本地待上传标记）。
时间统一 **UTC ISO8601（含微秒）**，比较用字符串字典序；展示时才转本地时区。
示例数据（`is_sample=1`）不参与同步；窗口尺寸、服务器地址、device_id 等本地设置不跨设备同步。

## 4. 客户端库

- `src/labassistant/sync/client.py`：dirty 收集、pull 应用（LWW）、墓碑处理、幂等。
- `src/labassistant/sync/engine.py`：一次完整 push+pull 周期、`last_server_revision` 管理。
- `src/labassistant/sync/controller.py`：后台线程执行 + 定时/手动/退出触发 + 状态信号（不阻塞 UI）。
- `src/labassistant/db_migration.py`：v1→v2 迁移（自动备份、加列、回填 uuid、建 `websites`）。

## 5. 服务器库

- `src/labassistant_server/store.py`：实体表 + `sync_events`（自增 revision 事件流）+ `devices`；
  启动自动生成 Token（config.json）。
- `src/labassistant_server/api.py`：FastAPI 应用。
- `src/labassistant_server/runner.py`：后台线程 uvicorn（`SyncServer`）。
- `src/labassistant_server/gui.py`：简单控制台（启动/停止/复制地址与令牌/设备列表/备份/日志/托盘）。

## 6. API

| 方法 | 路径 | 认证 | 说明 |
| --- | --- | --- | --- |
| GET | `/health` | 无 | 版本/协议探测（客户端“测试连接”） |
| POST | `/sync/push` | Bearer | 上传本地 dirty 变更（记录级，事务，LWW 裁决） |
| POST | `/sync` | Bearer | = `/sync/push` 别名 |
| POST | `/sync/pull` | Bearer | 按 `last_server_revision` 增量拉取事件 |
| GET | `/devices` | Bearer | 已连接设备列表 |

请求示例（push）：

```json
{
  "device_id": "…", "device_name": "Example-Mac",
  "protocol_version": 1, "app_version": "1.1.0",
  "changes": [
    {"entity": "todo", "sync_uuid": "…", "action": "upsert",
     "updated_at": "2026-09-08T08:30:00.123456Z", "deleted_at": null,
     "device_id": "…", "data": {"date": "…", "title": "…", "…": "…"}}
  ]
}
```

pull 响应：`{"ok":true,"revision":N,"changes":[{revision, entity, sync_uuid, action, updated_at, deleted_at, device_id, data}]}`。

## 7. revision / 增量

- 服务器每次接受一条变更追加一条 `sync_events`，`revision` 自增。
- 客户端保存 `last_server_revision`，pull 时只取 `revision > last` 的事件。
- 同步成功后写回新 revision（异常中断时重复拉取按幂等规则 no-op）。

## 8. 软删除 / 墓碑

- 客户端删除一律 `deleted_at=now` + `sync_dirty=1`（不物理 DELETE）。
- 列表/统计查询统一过滤 `deleted_at IS NULL`。
- 服务器收到墓碑后保存并广播；其他端应用墓碑后，同记录再次出现会被幂等忽略——删除不会复活。
- 恢复操作 = 把 `deleted_at` 清空并作为新 upsert（节假日重建等场景会生成新 uuid 以免冲突）。

## 9. 冲突：Last Write Wins

- 双方（客户端 push、客户端 pull 应用）统一比较 `updated_at`（UTC，含微秒）。
- 服务器裁决冲突时：较新胜出；`updated_at` 相等则按 `device_id` 字典序取大者（保证最终一致）。
- 被裁决为旧的一方会收到 `stale/dup_or_tie` 结果并清除本地 dirty；随后从服务器 pull 收敛到权威版本。

## 10. 幂等 / 事务

- UUID 是主键语义，同一 `sync_uuid` 重放不会重复建行。
- 服务器每批 push 在**同一事务**中处理，任一条失败整体回滚、返回错误，客户端保留 dirty 下次重试。

## 11. 首次同步 / 新客户端

- Windows 已有数据：迁移时给每条老数据生成 `sync_uuid` 且置 `sync_dirty=1`，首次启用后整体上传。
- 空的新 Mac：首次 pull 只应用服务器事件（不含删除指令时不会删除服务器任何数据）；
  空本地库**永远不会**被解释成“删除服务器数据”。
- 两端同时已有数据：通过各自 uuid + 时间戳合并；无法判定的同一语义记录宁保留两条并靠日志排查。

## 12. Token 与网络

- 服务器首次启动在 `config.json` 自动生成随机 Bearer Token；GUI 可“复制访问令牌”。
- 客户端在 设置→同步 填写 服务器地址 + 令牌。
- 校园网/NAT 场景：两端装 Tailscale 登录同一账号，服务器地址填 `http://100.x.x.x:8765`
  （或 MagicDNS 设备名）；无需公网 IP / 端口映射。请勿把服务直接暴露公网。

## 13. 客户端同步时机与状态

- 启动后约 2.5s 自动后台同步一次；本地数据改动后 4s 防抖同步；默认每 5 分钟定时（可改 1/5/10/30/仅手动）；
  设置页“立即同步”；退出前尝试同步但不阻塞关闭。
- 状态显示在左侧导航底部与 设置→同步：●已同步 / ◐同步中 / ○离线·N项等待 / ⚠失败。
- 服务器离线：变更保留 `sync_dirty=1`（等待计数=待上传数），恢复后自动补传。

## 14. 日志

- 客户端：数据目录 `logs/client.log`（写入 LabAssistantServer GUI 与客户端各自目录；客户端未单独落盘时记录于 `errors.log` 与状态 last_error）。
- 服务器：`data/logs/server.log`，>512KB 轮转到 `.1`。
- 绝不记录 Token/密码。

## 15. 版本兼容

- `/health` 与同步请求带 `protocol_version`；不匹配时客户端提示
  “同步协议不兼容，请升级 LabAssistant 客户端 / 服务器”，不会静默损坏数据。

## 16. 备份

- 迁移 v1→v2 前自动整库备份：`data/backups/backup_before_sync_migration_*.db`。
- 客户端设置页保留原有“备份/恢复”。
- 服务器 GUI“备份数据库”→ `data/backups/backup_*.db`。
- 三处库（Windows local / server / Mac local）均建议定期手动备份。

## 17. 测试

- `tests/test_sync_server.py`：真实 uvicorn + 双客户端——首次全量、双向增改、软删不复活、
  网站双向、LWW 冲突收敛、离线等待补传、空客户端不删服务器。
- `tests/test_migration_websites.py`：老库升级不丢数据/自动备份、网站 CRUD/URL、节假日软删重建。
- 回归：`scripts/run_tests.py` 全量绿（含原有计时/日历/统计功能测试）。

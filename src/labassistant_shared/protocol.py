"""同步实体定义：哪些表参与同步、可同步业务字段、元字段命名。

每条记录追加的同步元字段：
    sync_uuid      TEXT UNIQUE      跨设备唯一标识（保留原整数 id 不变）
    created_at     TEXT             创建（UTC）
    updated_at     TEXT             最近修改（UTC，LWW 依据）
    deleted_at     TEXT             软删除时间（UTC），NULL=未删除
    device_id      TEXT             最后修改设备
    sync_dirty     INTEGER          1=本地有未上传改动（软删除同样置 1）
"""

PROTOCOL_VERSION = 1

META_FIELDS = ("sync_uuid", "created_at", "updated_at", "deleted_at", "device_id")

# entity -> (表名, 可同步业务字段, 是否有 is_sample 列)
# 注意：course_exception 用 course_uuid 关联课程（跨设备），不用本地整数 course_id。
ENTITIES: dict[str, dict] = {
    "attendance_block": {
        "table": "attendance_blocks",
        "fields": ("date", "start_min", "end_min", "note"),
        "has_sample": True,
    },
    "manual_hour": {
        "table": "manual_hours",
        "fields": ("date", "minutes", "note"),
        "has_sample": True,
    },
    "course": {
        "table": "courses",
        "fields": ("name", "weekday", "start_min", "end_min", "start_date", "end_date",
                   "location", "teacher", "note", "count_attendance"),
        "has_sample": True,
    },
    "course_exception": {
        "table": "course_exceptions",
        "fields": ("course_uuid", "date", "action", "start_min", "end_min", "note"),
        "has_sample": False,
    },
    "todo": {
        "table": "todos",
        "fields": ("date", "title", "done", "est_minutes", "priority", "deadline", "note"),
        "has_sample": True,
    },
    "holiday": {
        "table": "holidays",
        "fields": ("date", "name"),
        "has_sample": True,
    },
    "website": {
        "table": "websites",
        "fields": ("name", "url", "category", "note"),
        "has_sample": False,
    },
}

# 参与跨设备同步的业务设置键（其余设置留本地：窗口、服务器地址、设备名、device_id 等）
SYNCED_SETTINGS = ("daily_minutes", "workdays", "default_course_counts")

SETTING_ENTITY = "setting"


def entity_table(entity: str) -> str:
    return ENTITIES[entity]["table"]


def fields_of(entity: str) -> tuple[str, ...]:
    return ENTITIES[entity]["fields"]


def has_sample_col(entity: str) -> bool:
    return bool(ENTITIES[entity]["has_sample"])


def all_entities() -> tuple[str, ...]:
    return tuple(ENTITIES.keys())

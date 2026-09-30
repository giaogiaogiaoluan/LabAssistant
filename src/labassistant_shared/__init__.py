"""LabAssistant 跨端共享同步协议（客户端与服务器共用，纯标准库，不依赖 Qt/FastAPI）。"""

__version__ = "1.1.1"
PROTOCOL_VERSION = 1
APP_VERSION = "1.1.1"

# 同步协议兼容性错误码/消息
ERR_PROTOCOL = "protocol_incompatible"
MSG_PROTOCOL = "同步协议不兼容，请升级 LabAssistant 客户端 / 服务器。"

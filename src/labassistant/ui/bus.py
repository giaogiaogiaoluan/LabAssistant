"""全局数据变更信号总线：任何写库操作后 emit，供各页面刷新。"""

from PySide6.QtCore import QObject, Signal

_bus: "DataBus | None" = None


class DataBus(QObject):
    changed = Signal()
    page_switch = Signal(int)


def get_bus() -> DataBus:
    global _bus
    if _bus is None:
        _bus = DataBus()
    return _bus

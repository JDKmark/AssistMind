"""对话上下文管理（DialogManager）。消费方统一从本包 import：

    from app.core.dialog import trim_history, extract_query, format_history
    from app.core.dialog import extract_slots, required_slots, missing_slots
"""

from app.core.dialog.manager import extract_query, format_history, trim_history
from app.core.dialog.state import REQUIRED_SLOTS, extract_slots, missing_slots, required_slots

__all__ = [
    "trim_history",
    "extract_query",
    "format_history",
    "REQUIRED_SLOTS",
    "extract_slots",
    "required_slots",
    "missing_slots",
]

"""对话上下文管理（DialogManager）。消费方统一从本包 import：

    from app.core.dialog import trim_history, extract_query, format_history
"""

from app.core.dialog.manager import extract_query, format_history, trim_history

__all__ = ["trim_history", "extract_query", "format_history"]

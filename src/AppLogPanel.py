import logging
import threading

from rich.logging import RichHandler
from textual.widgets import RichLog

logger = logging.getLogger(__name__)


class AppLogPanel(RichLog):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.file = False
        self.border_title = "日志"

    def print(self, content):
        self.write(content)


class AppLogHandler(RichHandler):
    def __init__(self, widget: AppLogPanel, *args, **kwargs):
        self._widget = widget
        super().__init__(
            console=widget,
            rich_tracebacks=False,
            level=logging.ERROR,
            show_level=False,
            show_path=False,
            *args,
            **kwargs
        )
        formatter = logging.Formatter("%(message)s")
        self.setFormatter(formatter)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            app = getattr(self._widget, "app", None)
            if app is not None and getattr(app, "_thread_id", None) not in (None, threading.get_ident()):
                app.call_from_thread(super().emit, record)
                return
            super().emit(record)
        except Exception:
            pass

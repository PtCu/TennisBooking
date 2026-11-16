from textual import on
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Label


class ConfirmScreen(ModalScreen[bool]):
    DEFAULT_CSS = """
    ConfirmScreen { align: center middle; }
    #confirm-dialog { width: 80; max-width: 95%; height: auto; max-height: 95%; overflow-y: auto; padding: 1 2; border: round $accent; background: $surface; }
    #confirm-dialog Horizontal { height: auto; }
    """

    def __init__(self, summary):
        super().__init__()
        self.summary = summary

    def compose(self):
        with Vertical(id="confirm-dialog"):
            yield Label("确认订场任务")
            yield Label(self.summary, markup=False)
            yield Label("匹配后将创建一笔真实订单，请到久事小程序完成付款。")
            with Horizontal():
                yield Button("确认并开始", id="confirm-start", variant="primary")
                yield Button("返回修改", id="confirm-back")

    @on(Button.Pressed)
    def finish(self, event):
        self.dismiss(event.button.id == "confirm-start")

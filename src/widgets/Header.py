from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Label, Button


class Header(Horizontal):
    def compose(self) -> ComposeResult:
        yield Label("[b]久事网球助手[/]", id="app-title")
        yield Button("微信提醒", id="phone-settings")
        yield Button("订单 / 付款", id="orders-button")

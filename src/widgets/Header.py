from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, Label


class Header(Horizontal):
    def compose(self) -> ComposeResult:
        yield Label("[b]Grounder[/]", id="app-title")
        yield Button("手机提醒", id="phone-settings")

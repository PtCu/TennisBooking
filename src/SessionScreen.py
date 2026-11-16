from pathlib import Path

from textual import on
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label

from TokenGetter import TokenGetter, validate_token


class SessionScreen(ModalScreen[str | None]):
    DEFAULT_CSS = """
    SessionScreen { align: center middle; }
    #session-dialog { width: 78; height: auto; padding: 1 2; border: round $accent; background: $surface; }
    #session-dialog Input { margin: 1 0; }
    #session-dialog Horizontal { height: auto; }
    #session-error { color: $error; height: auto; }
    """

    def compose(self):
        with Vertical(id="session-dialog"):
            yield Label("登录久事")
            yield Label("粘贴本人登录凭证，或填写 HAR / JSON 会话文件路径。")
            yield Input(placeholder="登录凭证", password=True, id="session-token")
            yield Input(placeholder="会话文件完整路径", id="session-path")
            yield Label("", id="session-error", markup=False)
            with Horizontal():
                yield Button("验证并登录", id="session-confirm", variant="primary")
                yield Button("取消", id="session-cancel")

    @on(Button.Pressed, "#session-confirm")
    def confirm(self):
        try:
            raw_token = self.query_one("#session-token", Input).value.strip()
            token = validate_token(raw_token) if raw_token else TokenGetter.token_from_file(
                Path(self.query_one("#session-path", Input).value.strip().strip('"'))
            )
        except (OSError, ValueError, TypeError):
            self.query_one("#session-error", Label).update("凭证或文件无效，请检查输入后重试。")
            return
        self.dismiss(token)

    @on(Button.Pressed, "#session-cancel")
    def cancel(self):
        self.dismiss(None)

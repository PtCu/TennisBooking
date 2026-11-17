from pathlib import Path
import asyncio
import threading

from textual import on, work
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Collapsible

from TokenGetter import TokenGetter, validate_token
from FilePickerScreen import FilePickerScreen
from wechat_session import read_wechat_candidates, SessionConnectionError
from JSApi import JSApi, TokenExpiredError


class SessionScreen(ModalScreen[str | None]):
    DEFAULT_CSS = """
    SessionScreen { align: center middle; }
    #session-dialog { width: 78; max-width: 95%; max-height: 95%; overflow-y: auto; height: auto; padding: 1 2; border: round $accent; background: $surface; }
    #session-dialog Input { margin: 1 0; }
    #session-dialog Horizontal { height: auto; }
    #session-dialog Collapsible { height: auto; }
    #session-error { color: $error; height: auto; }
    """

    def compose(self):
        self.cancelled = threading.Event()
        with Vertical(id="session-dialog"):
            yield Label("登录久事")
            yield Label("在电脑微信打开「久事体育」，登录后进入场馆页。")
            yield Button("连接微信中的久事", id="session-connect", variant="primary")
            with Collapsible(title="高级选项：导入已有会话", collapsed=True):
                yield Input(placeholder="登录凭证", password=True, id="session-token")
                yield Input(placeholder="会话文件完整路径", id="session-path")
                yield Button("选择会话文件", id="session-browse")
                yield Button("验证并登录", id="session-confirm")
            yield Label("", id="session-error", markup=False)
            with Horizontal():
                yield Button("取消", id="session-cancel")

    @on(Button.Pressed, "#session-connect")
    @work(exclusive=True, group="wechat-connect")
    async def connect(self):
        button = self.query_one("#session-connect", Button)
        status = self.query_one("#session-error", Label)
        button.disabled = True
        status.update("正在连接久事，请稍候…")
        try:
            tokens = await asyncio.to_thread(read_wechat_candidates, self.cancelled)
            valid = []
            for token in tokens:
                if self.cancelled.is_set():
                    return
                api = JSApi()
                api.headers["token"] = token
                try:
                    await api.get_user_info()
                except TokenExpiredError:
                    continue
                valid.append(token)
            if self.cancelled.is_set():
                return
            if len(valid) == 1:
                self.dismiss(valid[0])
                return
            if valid:
                status.update("检测到多个有效会话，请关闭其他久事窗口，再打开要使用的账号。")
            else:
                status.update("久事会话已失效，请在小程序重新登录后再连接。")
        except SessionConnectionError as error:
            status.update(str(error))
        except Exception:
            status.update("连接失败，请确认网络正常后重试。")
        finally:
            if self.is_mounted:
                button.disabled = False

    def on_unmount(self):
        self.cancelled.set()

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

    @on(Button.Pressed, "#session-browse")
    @work(exclusive=True, group="file-picker")
    async def browse(self):
        selected = await self.app.push_screen_wait(FilePickerScreen())
        if selected:
            self.query_one("#session-path", Input).value = str(selected)

    @on(Button.Pressed, "#session-cancel")
    def cancel(self):
        self.cancelled.set()
        self.dismiss(None)

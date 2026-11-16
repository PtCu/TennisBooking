import logging

from textual import on, work
from textual.containers import Horizontal, ScrollableContainer
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Button, Label, Select

from JSApi import JSApi, TokenExpiredError
from SessionScreen import SessionScreen

logger = logging.getLogger(__name__)


class UserInfoPanel(ScrollableContainer):
    logged_status = reactive(False)

    class LoggedStatusChanged(Message):
        def __init__(self, sender, logged_status):
            self.sender = sender
            self.logged_status = logged_status
            super().__init__()

        @property
        def control(self):
            return self.sender

    def compose(self):
        self.border_title = "久事账号"
        with Horizontal():
            yield Label("未登录", id="user-status-icon-label")
            yield Label("", id="user-id-num-label")
            yield Button("登录 / 更新凭证", id="user-login-button")
        with Horizontal():
            yield Label("包场人")
            yield Select([], prompt="登录后选择包场人", id="user-contact-select")

    async def app_load_done(self):
        self.js_api = self.app.js_api
        token = self.js_api.token_getter.read_token()
        if token:
            await self.verify_session(token, save=False)

    def watch_logged_status(self, logged_status):
        self.query_one("#user-status-icon-label", Label).update("已登录" if logged_status else "未登录")

    @on(Button.Pressed, "#user-login-button")
    @work(exclusive=True, group="login")
    async def login(self):
        token = await self.app.push_screen_wait(SessionScreen())
        if token:
            await self.verify_session(token, save=True)

    async def verify_session(self, token, *, save):
        status = self.query_one("#user-status-icon-label", Label)
        status.update("正在验证账号…")
        candidate = JSApi()
        candidate.headers["token"] = token
        try:
            user_info = await candidate.get_user_info()
            contacts = await candidate.get_contact()
            choices = []
            for contact in contacts:
                name = candidate.remove_non_printable(candidate.aes_decrypt(contact["fullName"], key=candidate.aes_key2))
                choices.append((name, str(contact["id"])))
            if save:
                self.js_api.token_getter.save_token(token)
        except TokenExpiredError:
            status.update("凭证已失效，请更新" if not self.logged_status else "更新失败，原账号仍可用")
            logger.error("登录凭证已失效，请重新获取后再试")
            return
        except Exception:
            status.update("验证失败，请重试" if not self.logged_status else "更新失败，原账号仍可用")
            logger.error("账号验证失败，请检查凭证和网络后重试")
            return
        self.js_api.headers = candidate.headers.copy()
        self.js_api.user_info = user_info
        self.js_api.phone = candidate.phone
        self.js_api.contacts = contacts
        self.query_one("#user-contact-select", Select).set_options(choices)
        if len(choices) == 1:
            self.query_one("#user-contact-select", Select).value = choices[0][1]
        self.query_one("#user-id-num-label", Label).update(f"用户 {user_info['uid']}")
        self.logged_status = True
        status.update("已登录")
        logger.info("账号验证成功" if choices else "账号已登录，请在久事添加包场人")

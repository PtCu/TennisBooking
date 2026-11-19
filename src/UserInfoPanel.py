# %%
import asyncio
import logging

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Middle, ScrollableContainer
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Button, Label, Select

from JSApi import JSApi, TokenExpiredError

logger = logging.getLogger(__name__)


class UserInfoPanel(ScrollableContainer):
    LOG_IN: str = "✅ 已登录"
    LOG_OUT: str = "❌ 未登录"
    logged_status = reactive(False)

    class LoggedStatusChanged(Message):
        def __init__(self, sender: "UserInfoPanel", logged_status: bool) -> None:
            self.sender = sender
            self.logged_status = logged_status
            super().__init__()

        @property
        def control(self) -> "UserInfoPanel":
            return self.sender

    def __init__(
        self,
        *children,
        name=None,
        id=None,
        classes=None,
        disabled=False,
        can_focus=None,
        can_focus_children=None,
        can_maximize=None,
    ):
        super().__init__(
            *children,
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
            can_focus=can_focus,
            can_focus_children=can_focus_children,
            can_maximize=can_maximize,
        )
        self.login_busy = False

    def compose(self) -> ComposeResult:
        self.border_title = "用户信息"
        with Horizontal():
            yield Middle(Label("[b]状态：[/]", id="user-status-label"))
            yield Middle(Label(self.LOG_OUT, id="user-status-icon-label"))
            yield Middle(Button("登录", id="user-login-button"))
            yield Middle(Label("[b]用户ID：[/] ", id="user-id-label"))
            yield Middle(Label("", id="user-id-num-label"))
        with Horizontal():
            yield Middle(Label("[b]包场人信息：[/]", id="user-contact-label"))
            yield Middle(Select([], prompt="请选择包场人", id="user-contact-select"))

    async def app_load_done(self) -> None:
        self.js_api: JSApi = self.app.js_api
        self.login(allow_proxy=False)

    def watch_logged_status(self, logged_status: bool) -> None:
        status_icon: Label = self.query_one("#user-status-icon-label")
        status_icon.update(self.LOG_IN if logged_status else self.LOG_OUT)

    @on(Button.Pressed, "#user-login-button")
    def login_pressed(self) -> None:
        if self.login_busy:
            logger.error("正在登录中，请稍候...")
            return
        self.login(allow_proxy=False, force_scan=True)

    @work(group="user-login")
    async def login(self, allow_proxy: bool = False, force_scan: bool = False) -> None:
        if self.login_busy:
            return
        self.login_busy = True
        try:
            if force_scan or not self.js_api.read_token():
                ok = await asyncio.to_thread(self.js_api.get_token, allow_proxy)
                if not ok:
                    self.logged_status = False
                    return
            await self.get_user_info(allow_retry_scan=not force_scan)
        finally:
            self.login_busy = False

    async def get_user_info(self, allow_retry_scan: bool = False) -> None:
        try:
            await self.js_api.get_user_info()
            contacts = await self.js_api.get_contact()
        except TokenExpiredError:
            if allow_retry_scan:
                logger.error("本地缓存 Token 已失效，正在自动从微信内存读取最新 Token...")
                if await asyncio.to_thread(self.js_api.get_token, False):
                    await self.get_user_info(allow_retry_scan=False)
                    return
            logger.error("Token 已过期，请点「登录」重新获取")
            self.post_message(self.LoggedStatusChanged(self, False))
            return
        except Exception as e:
            self.logged_status = False
            logger.error(f"获取用户信息失败: {e}")
            logger.debug(e, exc_info=True)
            return

        self.logged_status = True
        self.contact_list = []
        for contact in contacts:
            name = self.js_api.aes_decrypt(contact["fullName"], key=self.js_api.aes_key2)
            name = self.js_api.remove_non_printable(name)
            phone = self.js_api.aes_decrypt(contact["phoneNumber"], key=self.js_api.aes_key2)
            phone = self.js_api.remove_non_printable(phone)
            cid = contact["id"]
            self.contact_list.append((f"{name}_{phone}", cid))

        select: Select = self.query_one("#user-contact-select")
        select.set_options(self.contact_list)
        if self.contact_list:
            select.value = self.contact_list[0][1]

        id_label: Label = self.query_one("#user-id-num-label")
        id_label.update(self.js_api.user_info["uid"])
        logger.error(f"登录成功: uid={self.js_api.user_info['uid']}，已加载 {len(self.contact_list)} 位包场人")

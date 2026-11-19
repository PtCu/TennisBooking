# %%
import logging

from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Button

from AppLogPanel import AppLogHandler, AppLogPanel
from GroundInfoPanel import GroundInfoPanel
from GroundSelectScreen import GroundSelectScreen
from JSApi import JSApi
from PhoneScreen import PhoneScreen
from Theme import default_theme
from UserInfoPanel import UserInfoPanel
from widgets.Header import Header

# %%
logger = logging.getLogger(__name__)
httpx_logger = logging.getLogger("httpx")
httpx_logger.setLevel(logging.ERROR)


class Grounder(App):
    CSS_PATH = "grounder.css"

    def compose(self) -> ComposeResult:
        with Vertical(id="main"):
            yield Header()
            yield UserInfoPanel(classes="panel")
            yield GroundInfoPanel(classes="panel")
            yield AppLogPanel(id="app-log", classes="panel")

    def on_mount(self) -> None:
        self.register_theme(default_theme)
        self.theme = "default"

    async def on_ready(self) -> None:
        root_logger = logging.getLogger()
        log_widgt = self.query_one("#app-log")
        root_logger.addHandler(AppLogHandler(log_widgt))

        logger.error("加载 JSApi（延迟启动滑块浏览器，秒开）")
        self.js_api = JSApi()

        user_info_panel = self.query_one(UserInfoPanel)
        await user_info_panel.app_load_done()

        ground_info_panel = self.query_one(GroundInfoPanel)
        ground_info_panel.app_load_done()

    @work
    @on(Button.Pressed, "#phone-settings")
    async def open_phone_settings(self) -> None:
        await self.push_screen_wait(PhoneScreen(self.js_api))

    @on(UserInfoPanel.LoggedStatusChanged)
    @on(GroundInfoPanel.LoggedStatusChanged)
    @on(GroundSelectScreen.LoggedStatusChanged)
    def update_logged_status(
        self,
        message: (
            UserInfoPanel.LoggedStatusChanged
            | GroundInfoPanel.LoggedStatusChanged
            | GroundSelectScreen.LoggedStatusChanged
        ),
    ) -> None:
        user_info_panel = self.query_one(UserInfoPanel)
        user_info_panel.logged_status = message.logged_status
        if message.logged_status is False:
            self.js_api.clear_token()

    def on_unmount(self) -> None:
        try:
            self.query_one(GroundInfoPanel).stop_event.set()
        except Exception:
            pass
        if getattr(self, "js_api", None) is not None:
            self.js_api.close()

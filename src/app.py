# %%
import logging

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Button
from NotificationScreen import NotificationScreen
from OrdersScreen import OrdersScreen

from AppLogPanel import AppLogHandler, AppLogPanel
from GroundInfoPanel import GroundInfoPanel
from GroundSelectScreen import GroundSelectScreen
from JSApi import JSApi
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

    @on(Button.Pressed, "#phone-settings")
    def phone_settings(self):
        self.push_screen(NotificationScreen())

    @on(Button.Pressed, "#orders-button")
    def orders(self):
        self.push_screen(OrdersScreen())

    async def on_ready(self) -> None:
        root_logger = logging.getLogger()
        log_widgt = self.query_one("#app-log")
        self.ui_log_handler = AppLogHandler(log_widgt)
        root_logger.addHandler(self.ui_log_handler)

        self.js_api = JSApi()

        ground_info_panel = self.query_one(GroundInfoPanel)
        ground_info_panel.app_load_done()

        user_info_panel = self.query_one(UserInfoPanel)
        await user_info_panel.app_load_done()

    def on_unmount(self):
        handler = getattr(self, "ui_log_handler", None)
        if handler:
            logging.getLogger().removeHandler(handler)
            handler.close()

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

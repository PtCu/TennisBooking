import asyncio

from textual import on, work
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label

from phone_notify import PhoneSettings, PhoneNotifier, NotificationError
from storage import DATA_DIR


class NotificationScreen(ModalScreen):
    DEFAULT_CSS = """
    NotificationScreen { align: center middle; }
    #phone-dialog { width: 78; max-width: 95%; height: auto; max-height: 95%; overflow-y: auto; padding: 1 2; border: round $accent; background: $surface; }
    #phone-dialog Horizontal { height: auto; }
    #phone-dialog Input { margin: 1 0; }
    """

    def compose(self):
        with Vertical(id="phone-dialog"):
            yield Label("微信提醒 · Server酱 Turbo")
            yield Label("在 sct.ftqq.com 绑定微信通道，填写 SCT 开头的 SendKey。")
            yield Input(placeholder="SendKey，留空使用已保存设置", password=True, id="phone-key")
            yield Label("", id="phone-status", markup=False)
            with Horizontal():
                yield Button("保存并测试", id="phone-test", variant="primary")
                yield Button("关闭", id="phone-close")

    def on_mount(self):
        try:
            configured = bool(PhoneSettings.load(DATA_DIR).key)
            self.query_one("#phone-status", Label).update("已配置" if configured else "尚未配置")
        except NotificationError as error:
            self.query_one("#phone-status", Label).update(str(error))

    @on(Button.Pressed, "#phone-test")
    @work(exclusive=True)
    async def test_notification(self):
        button = self.query_one("#phone-test", Button)
        button.disabled = True
        status = self.query_one("#phone-status", Label)
        try:
            field = self.query_one("#phone-key", Input)
            settings = PhoneSettings(field.value.strip()) if field.value.strip() else PhoneSettings.load(DATA_DIR)
            settings.validate().save(DATA_DIR)
            field.value = ""
            status.update("正在发送测试提醒…")
            await asyncio.to_thread(PhoneNotifier(settings).send, "久事网球助手 · 提醒测试", "微信提醒已连接。预约后请到久事体育 → 我的订单 → 待支付中核对并付款。")
            status.update("服务已接受测试消息，请在微信确认收到。")
        except (NotificationError, OSError):
            status.update("发送失败，请检查 SendKey、网络和服务额度后重试。")
        finally:
            button.disabled = False

    @on(Button.Pressed, "#phone-close")
    def close(self):
        self.dismiss()

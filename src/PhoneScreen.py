from textual import on, work
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static


class PhoneScreen(ModalScreen[bool]):
    DEFAULT_CSS = """
    PhoneScreen { align: center middle; }
    #phone-dialog {
        width: 84;
        max-width: 94%;
        height: auto;
        max-height: 90%;
        padding: 1 2;
        border: round $accent;
        background: $surface;
        overflow-y: auto;
    }
    #phone-dialog Input { height: 3; margin: 1 0; }
    #phone-actions { height: auto; }
    #phone-actions Button { margin: 1 2; min-width: 12; }
    #phone-result { height: auto; min-height: 2; color: $warning; }
    """

    def __init__(self, js_api):
        super().__init__()
        self.js_api = js_api
        self.busy = False

    def compose(self):
        with Vertical(id="phone-dialog"):
            yield Static(
                "手机微信付款提醒（Server酱 Turbo）\n\n"
                "抢场下单成功后，自动将场馆、时段、订单号与付款截止时间推送到你的微信。\n"
                "配置方法：在 sct.ftqq.com 绑定微信后复制 SCT 开头的 SendKey。\n"
                "点击「保存并测试」可立刻发一条测试消息到微信确认连通性。",
                markup=False,
            )
            yield Input(
                placeholder="输入 SCT 开头的 SendKey（留空则测试已保存的 Key）",
                password=True,
                id="phone-key",
            )
            yield Static("尚未测试。", id="phone-result", markup=False)
            with Horizontal(id="phone-actions"):
                yield Button("保存并测试", id="phone-test", variant="primary")
                yield Button("返回", id="phone-close")

    def on_mount(self):
        existing = self.js_api.token_getter.load_serverchan_key()
        if existing:
            masked = f"{existing[:6]}...{existing[-4:]}" if len(existing) > 12 else "***"
            self.query_one("#phone-result", Static).update(
                f"已保存 SendKey（{masked}）。留空点「保存并测试」可直接重测。"
            )

    @work(group="phone-test")
    @on(Button.Pressed, "#phone-test")
    async def test_connection(self):
        if self.busy:
            return
        self.busy = True
        buttons = list(self.query(Button))
        for b in buttons:
            b.disabled = True
        res = self.query_one("#phone-result", Static)
        res.update("正在通过 Server酱发送微信测试消息...")
        key_input = self.query_one("#phone-key", Input)
        try:
            raw = key_input.value.strip()
            if raw:
                self.js_api.token_getter.save_serverchan_key(raw)
                key_input.value = ""
            ok, msg = await self.js_api.notify_serverchan(
                "Grounder 久事抢场提醒测试",
                "这是一条来自 Grounder 的 Server酱测试通知，收到即表示微信提醒已就绪。",
            )
            res.update(("✅ " if ok else "❌ ") + msg)
        finally:
            self.busy = False
            for b in buttons:
                b.disabled = False

    @on(Button.Pressed, "#phone-close")
    def close_dialog(self):
        if not self.busy:
            self.dismiss(True)

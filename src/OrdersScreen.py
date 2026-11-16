import logging

from textual import on, work
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Select

from JSApi import TokenExpiredError
from orders import trackable_records, reconcile, track_payment, OrderReviewError

logger = logging.getLogger(__name__)


class OrdersScreen(ModalScreen):
    DEFAULT_CSS = """
    OrdersScreen { align: center middle; }
    #orders-dialog { width: 90; max-width: 95%; height: auto; max-height: 90%; overflow-y: auto; padding: 1 2; border: round $accent; background: $surface; }
    #orders-dialog Horizontal { height: auto; }
    #order-state { height: auto; margin: 1 0; }
    """

    def compose(self):
        self.records = trackable_records()
        choices = [(record.get("summary", path.stem), str(path)) for path, record in self.records]
        with Vertical(id="orders-dialog"):
            yield Label("订单与付款")
            yield Select(choices, prompt="请选择待核对或待付款记录", id="order-record")
            yield Label("请选择一条记录继续核对。" if choices else "目前没有待核对或待付款记录。", id="order-state", markup=False)
            yield Label("付款请使用下单账号打开久事体育 → 我的订单 → 待支付。")
            with Horizontal():
                yield Button("继续核对 / 跟踪", id="order-resume", disabled=not choices, variant="primary")
                yield Button("停止跟踪", id="order-stop")
                yield Button("关闭", id="orders-close")

    def on_mount(self):
        if self.records:
            self.query_one("#order-record", Select).value = str(self.records[0][0])

    @on(Button.Pressed, "#order-resume")
    @work(exclusive=True, group="payment")
    async def resume(self):
        choice = self.query_one("#order-record", Select).value
        record = next(((path, value) for path, value in self.records if str(path) == choice), None)
        if record is None:
            return
        path, value = record
        status = self.query_one("#order-state", Label)
        button = self.query_one("#order-resume", Button)
        button.disabled = True
        try:
            api = self.app.js_api.session_copy()
            status.update("正在查询官方订单…")
            if not value.get("order_id"):
                await reconcile(api, path)
            status.update("官方订单已核对，正在跟踪付款，具体状态见主界面日志。")
            result = await track_payment(api, path, on_update=status.update)
            status.update(result)
        except TokenExpiredError:
            status.update("登录凭证已失效，请先更新登录后继续跟踪。")
        except OrderReviewError as error:
            status.update(str(error))
        except Exception:
            status.update("查询失败，请检查网络并在久事官方核对订单。")
        finally:
            button.disabled = False

    @on(Button.Pressed, "#order-stop")
    def stop(self):
        self.workers.cancel_group(self, "payment")
        self.query_one("#order-state", Label).update("跟踪已停止，订单记录已保留。")

    @on(Button.Pressed, "#orders-close")
    def close(self):
        self.workers.cancel_group(self, "payment")
        self.dismiss()

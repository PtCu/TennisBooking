import asyncio
import json
import logging
import threading
from datetime import datetime

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Middle, ScrollableContainer
from textual.message import Message
from textual.widgets import Button, Input, Label, Select, Switch

from GroundSelectScreen import GroundSelectScreen
from JSApi import JSApi, TokenExpiredError, retry
from widgets.TimeSelect import TimeSelect

logger = logging.getLogger(__name__)


class GroundInfoPanel(ScrollableContainer):
    class LoggedStatusChanged(Message):
        def __init__(self, sender: "GroundInfoPanel", logged_status: bool) -> None:
            self.sender = sender
            self.logged_status = logged_status
            super().__init__()

        @property
        def control(self) -> "GroundInfoPanel":
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
        self.stop_event = threading.Event()
        self.task_busy = False

    def compose(self) -> ComposeResult:
        self.border_title = "场馆预定"
        with Horizontal():
            yield Middle(Label("[b]选择运动：[/]", id="ground-sports-label"))
            yield Middle(
                Select(
                    [("羽毛球", "羽毛球"), ("网球", "网球")],
                    prompt="请选择运动项目",
                    id="ground-sports-select",
                )
            )
            yield Middle(Label("[b]选择场馆：[/]", id="ground-venue-label"))
            yield Middle(Select([], prompt="请选择场馆", id="ground-venue-select"))
            yield Middle(Button("选择场地", id="ground-button"))

        with Horizontal():
            yield Middle(Label("开抢时间: "))
            yield Middle(TimeSelect(id="time-select"))
            yield Middle(Button("开始预定", id="order-button"))
            yield Middle(Button("立即开抢", id="order-now-button"))
            yield Middle(Button("停止", id="order-stop-button"))

        with Horizontal():
            yield Middle(Label("重试次数: "), id="retry-label")
            yield Middle(Input(value="10", type="integer", id="retry-input"))
            yield Middle(Label("重试间隔: "), id="delay-label")
            yield Middle(Input(value="250", type="integer", id="delay-input"))
            yield Middle(Label("毫秒"), id="delay-unit-label")
            yield Middle(Label("随机偏差: "), id="jitter-label")
            yield Middle(Input(value="10", type="integer", id="jitter-input"))
            yield Middle(Label("%"), id="jitter-unit-label")
            yield Middle(Label(" 仅演练: "))
            yield Middle(Switch(value=False, id="dry-run-switch"))

        yield Label("未选择场地（选择运动与场馆后点「选择场地」）", id="ground-selected-summary")

    def app_load_done(self) -> None:
        self.js_api: JSApi = self.app.js_api

    def _update_summary(self):
        lbl = self.query_one("#ground-selected-summary", Label)
        selected = getattr(self, "grounds_selected", None) or []
        if not selected:
            lbl.update("未选择场地（选择运动与场馆后点「选择场地」）")
            return
        desc = " | ".join(
            f"{datetime.fromtimestamp(int(g['startTime'])/1000):%m-%d} {g['groundName']} "
            f"{datetime.fromtimestamp(int(g['startTime'])/1000):%H:%M}-{datetime.fromtimestamp(int(g['endTime'])/1000):%H:%M}"
            for g in selected
        )
        lbl.update("已选目标: " + desc)

    @work
    @on(Button.Pressed, "#ground-button")
    async def show_ground_select_screen(self):
        res = await self.app.push_screen_wait(GroundSelectScreen())
        if res is not None:
            self.grounds_selected = res
            self._update_summary()

    @on(Button.Pressed, "#order-stop-button")
    def stop_order(self):
        if self.task_busy:
            self.stop_event.set()
            logger.error("正在停止预定任务...")
        else:
            logger.error("当前没有等待或执行中的预定任务")

    @on(Button.Pressed, "#order-button")
    def start_scheduled(self):
        self._run_order(immediate=False)

    @on(Button.Pressed, "#order-now-button")
    def start_immediate(self):
        self._run_order(immediate=True)

    @work(group="book-order")
    async def _run_order(self, immediate: bool = False):
        if self.task_busy:
            logger.error("已有预定任务在执行，如需取消请先点「停止」")
            return

        hour_expected = int(self.query_one("#hour-select", Select).value)
        minute_expected = int(self.query_one("#minute-select", Select).value)
        second_expected = int(self.query_one("#second-select", Select).value)
        max_retries = max(1, int(self.query_one("#retry-input", Input).value or "10"))
        delay = max(50, int(self.query_one("#delay-input", Input).value or "250"))
        jitter_percent = max(0, int(self.query_one("#jitter-input", Input).value or "10"))
        dry_run = self.query_one("#dry-run-switch", Switch).value

        contact_select: Select = self.app.query_one("#user-contact-select")
        venue_select: Select = self.query_one("#ground-venue-select")
        contact_id = contact_select.value

        try:
            venue_info = json.loads(venue_select.value)
        except Exception:
            logger.error("请确认场馆信息是否正确")
            return

        ground_list = getattr(self, "grounds_selected", None) or []
        if not ground_list:
            logger.error("请先选择场地")
            return
        if contact_select.is_blank() or not isinstance(contact_id, str):
            logger.error("请先选择联系人")
            return

        self.task_busy = True
        self.stop_event.clear()
        try:
            if not immediate:
                now = datetime.now()
                target_time = now.replace(
                    hour=hour_expected,
                    minute=minute_expected,
                    second=second_expected,
                    microsecond=0,
                )
                if target_time < now:
                    logger.error("预定时间早于当前时间；若要立即尝试请点击「立即开抢」")
                    return
                wait_seconds = (target_time - now).total_seconds()
                logger.error(
                    f"将在 {hour_expected:02}:{minute_expected:02}:{second_expected:02} 发送订单（距离现在 {wait_seconds:.2f} 秒）"
                )
                warmed = False
                while not self.stop_event.is_set():
                    rem = (target_time - datetime.now()).total_seconds()
                    if rem <= 0.05:
                        break
                    if rem <= 60 and not warmed:
                        logger.error("距离开抢 ≤ 60 秒：正在自动刷新验真微信 Token...")
                        await asyncio.to_thread(self.js_api.get_token, False)
                        warmed = True
                    await asyncio.sleep(min(0.5, max(0.02, rem - 0.05)))

                if self.stop_event.is_set():
                    logger.error("已取消定时预定")
                    return
                logger.error("到点，开始发送订单！")

            book_with_retry = retry(
                max_retries=max_retries, delay=delay, jitter_percent=jitter_percent
            )(self.js_api.post_book)
            await book_with_retry(
                contact_id=contact_id,
                venue_id=venue_info["venue_id"],
                venue_name=venue_info["venue_name"],
                agency_id=venue_info["agency_id"],
                agency_name=venue_info["agency_name"],
                sports_name=venue_info["sports_name"],
                ground_list=ground_list,
                dry_run=dry_run,
            )
        except TokenExpiredError:
            logger.error("Token 已过期，请重新登录")
            self.post_message(self.LoggedStatusChanged(self, False))
        except Exception as e:
            logger.error(f"预定失败: {e}")
            logger.debug(e, exc_info=True)
        finally:
            self.task_busy = False

    @work
    @on(Select.Changed, "#ground-sports-select")
    async def update_venue(self, event: Select.Changed):
        sports_name = event.value
        if not isinstance(sports_name, str):
            return
        try:
            venues = await self.js_api.get_venue_list(sports_name)
        except TokenExpiredError:
            logger.error("Token 已过期，请重新登录")
            self.post_message(self.LoggedStatusChanged(self, False))
            return
        except Exception as e:
            logger.error(f"查询场馆信息失败: {e}")
            logger.debug(e, exc_info=True)
            return

        venue_list = []
        for venue in venues:
            venue_dict = {
                "agency_id": str(venue["agencyId"]),
                "agency_name": venue["agencyName"],
                "venue_id": str(venue["venueId"]),
                "venue_name": venue["venueName"],
                "sports_name": sports_name,
            }
            venue_list.append((venue["venueName"], json.dumps(venue_dict, ensure_ascii=False)))

        venue_select: Select = self.query_one("#ground-venue-select")
        venue_select.set_options(venue_list)
        for label, val in venue_list:
            if "Wilson" in label:
                venue_select.value = val
                break
        logger.error(f"已加载 {len(venue_list)} 个{sports_name}场馆")

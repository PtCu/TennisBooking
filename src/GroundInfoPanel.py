import asyncio
import json
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Middle, ScrollableContainer
from textual.message import Message
from textual.widgets import Button, Label, Select, Input

from GroundSelectScreen import GroundSelectScreen
from JSApi import JSApi, TokenExpiredError, retry
from booking_flow import (
    BookingRequest,
    Plan,
    candidate_keys,
    choose_plan,
    flatten_ground_infos,
    selection_summary,
)
from widgets.TimeSelect import TimeSelect

logger = logging.getLogger(__name__)


PLAN_OPTIONS = [
    ("连续场次", "contiguous_bundle"),
    ("单个场次", "single_slot"),
    ("不连续组合", "split_bundle"),
]
PLAN_COUNT_OPTIONS = [("1段", "1"), ("2段", "2"), ("3段", "3"), ("4段", "4")]
FALLBACK_OPTIONS = [("不启用", "disabled"), *PLAN_OPTIONS]


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
        self.grounds_selected = []

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
            yield Middle(Button("选择候选场次", id="ground-button"))

        yield Label("已选场次会作为候选范围，开抢时程序会重新查询库存。", id="candidate-hint")
        yield Label("尚未选择候选场次", id="candidate-summary")

        with Horizontal(classes="plan-row"):
            yield Middle(Label("主方案"))
            yield Middle(Select(PLAN_OPTIONS, value="contiguous_bundle", id="primary-plan-type"))
            yield Middle(Select(PLAN_COUNT_OPTIONS, value="2", id="primary-plan-count"))

        with Horizontal(classes="plan-row"):
            yield Middle(Label("备选方案1"))
            yield Middle(Select(FALLBACK_OPTIONS, value="disabled", id="fallback-one-type"))
            yield Middle(Select(PLAN_COUNT_OPTIONS, value="1", id="fallback-one-count"))

        with Horizontal(classes="plan-row"):
            yield Middle(Label("备选方案2"))
            yield Middle(Select(FALLBACK_OPTIONS, value="disabled", id="fallback-two-type"))
            yield Middle(Select(PLAN_COUNT_OPTIONS, value="1", id="fallback-two-count"))

        with Horizontal():
            yield Middle(Label("总预算/元"))
            yield Middle(Input(value="450", type="number", id="booking-budget"))
            yield Middle(Label("开抢时间"))
            yield Middle(TimeSelect(id="time-select"))
            yield Middle(Button("开始预定", id="order-button"))

        with Horizontal():
            yield Middle(Label("重试次数"), id="retry-label")
            yield Middle(Input(value="10", type="integer", id="retry-input"))
            yield Middle(Label("重试间隔"), id="delay-label")
            yield Middle(Input(value="200", type="integer", id="delay-input"))
            yield Middle(Label("毫秒"), id="delay-unit-label")
            yield Middle(Label("随机偏差"), id="jitter-label")
            yield Middle(Input(value="10", type="integer", id="jitter-input"))
            yield Middle(Label("%"), id="jitter-unit-label")

    def app_load_done(self) -> None:
        self.js_api: JSApi = self.app.js_api

    @work
    @on(Button.Pressed, "#ground-button")
    async def show_ground_select_screen(self):
        selected = await self.app.push_screen_wait(GroundSelectScreen())
        if selected is not None:
            self.grounds_selected = selected
            self._update_candidate_summary()

    def _update_candidate_summary(self) -> None:
        summary = self.query_one("#candidate-summary", Label)
        if not self.grounds_selected:
            summary.update("尚未选择候选场次")
            return
        courts = sorted({str(item["groundName"]) for item in self.grounds_selected})
        summary.update(f"候选范围：{'、'.join(courts)}，共 {len(self.grounds_selected)} 段")

    def _read_plan(self, prefix: str, name: str, *, optional: bool) -> Plan | None:
        kind = self.query_one(f"#{prefix}-type", Select).value
        if optional and kind == "disabled":
            return None
        count = int(self.query_one(f"#{prefix}-count", Select).value)
        return Plan(kind=str(kind), count=count, name=name)

    def _build_request(self) -> BookingRequest:
        try:
            maximum = Decimal(self.query_one("#booking-budget", Input).value)
        except (InvalidOperation, ValueError):
            raise ValueError("总预算格式无效") from None

        plans = [
            self._read_plan("primary-plan", "主方案", optional=False),
            self._read_plan("fallback-one", "备选方案1", optional=True),
            self._read_plan("fallback-two", "备选方案2", optional=True),
        ]
        return BookingRequest(
            candidate_slots=candidate_keys(self.grounds_selected),
            plans=tuple(plan for plan in plans if plan is not None),
            max_total=maximum,
        ).validate()

    @staticmethod
    def _day_timestamp(request: BookingRequest) -> int:
        first_start = min(int(key[1]) for key in request.candidate_slots)
        day = datetime.fromtimestamp(first_start / 1000).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return int(day.timestamp() * 1000)

    async def _submit_request(
        self,
        request: BookingRequest,
        venue_info: dict,
        contact_id: str,
        max_retries: int,
        delay: int,
        jitter_percent: int,
    ) -> bool:
        timestamp = self._day_timestamp(request)
        for plan in request.plans:
            logger.error("正在检查%s", plan.name or plan.kind)
            ground_infos = await self.js_api.get_ground(venue_info["venue_id"], timestamp)
            available_slots = flatten_ground_infos(ground_infos)
            chosen = choose_plan(
                BookingRequest(request.candidate_slots, (plan,), request.max_total),
                available_slots,
            )
            if chosen is None:
                logger.error("%s没有匹配的实时库存", plan.name or plan.kind)
                continue

            selected_plan, selected_slots = chosen
            logger.error(selection_summary(selected_plan, selected_slots))
            book_with_retry = retry(
                max_retries=max_retries,
                delay=delay,
                jitter_percent=jitter_percent,
            )(self.js_api.post_book)
            success = await book_with_retry(
                contact_id=contact_id,
                venue_id=venue_info["venue_id"],
                venue_name=venue_info["venue_name"],
                agency_id=venue_info["agency_id"],
                agency_name=venue_info["agency_name"],
                sports_name=venue_info["sports_name"],
                ground_list=[slot.to_payload() for slot in selected_slots],
            )
            if success is True:
                logger.error("%s提交成功，请前往小程序付款", plan.name or plan.kind)
                return True
            logger.error("%s提交未成功，继续尝试下一方案", plan.name or plan.kind)
        return False

    @work(thread=True)
    @on(Button.Pressed, "#order-button")
    async def send_order(self):
        hour_select: Select = self.query_one("#hour-select")
        minute_select: Select = self.query_one("#minute-select")
        second_select: Select = self.query_one("#second-select")
        retry_input: Input = self.query_one("#retry-input")
        delay_input: Input = self.query_one("#delay-input")
        jitter_input: Input = self.query_one("#jitter-input")
        contact_select: Select = self.app.query_one("#user-contact-select")
        venue_select: Select = self.query_one("#ground-venue-select")

        try:
            request = self._build_request()
            venue_info = json.loads(venue_select.value)
            max_retries = int(retry_input.value)
            delay = int(delay_input.value)
            jitter_percent = int(jitter_input.value)
        except (ValueError, TypeError, json.JSONDecodeError):
            logger.error("订场条件无效，请检查候选场次、方案和预算")
            return

        contact_id = contact_select.value
        if contact_id is Select.BLANK:
            logger.error("请先选择联系人")
            return

        now = datetime.now()
        target_time = now.replace(
            hour=int(hour_select.value),
            minute=int(minute_select.value),
            second=int(second_select.value),
            microsecond=0,
        )
        if target_time < now:
            logger.error("预定时间无法早于当前时间")
            return

        wait_seconds = (target_time - now).total_seconds()
        logger.error(
            f"将在 {target_time:%H:%M:%S} 开始检查，距离预定时间还有 {wait_seconds:.3f} 秒"
        )
        await asyncio.sleep(wait_seconds)
        logger.error("开始检查候选方案")

        try:
            await self._submit_request(
                request,
                venue_info,
                str(contact_id),
                max_retries,
                delay,
                jitter_percent,
            )
        except TokenExpiredError:
            logger.error("Token 已过期，请重新登录")
            self.post_message(self.LoggedStatusChanged(self, False))
        except Exception as error:
            logger.error("预定失败")
            logger.debug(error, exc_info=True)

    @work
    @on(Select.Changed, "#ground-sports-select")
    async def update_venue(self, event: Select.Changed):
        sports_name = event.value
        if sports_name != Select.BLANK:
            try:
                venues = await self.js_api.get_venue_list(sports_name)
            except TokenExpiredError:
                logger.error("Token 已过期，请重新登录")
                self.post_message(self.LoggedStatusChanged(self, False))
                return
            except Exception as error:
                logger.error("查询场馆信息失败")
                logger.debug(error, exc_info=True)
                return

            venue_list = []
            for venue in venues:
                venue_dict = {
                    "agency_id": venue["agencyId"],
                    "agency_name": venue["agencyName"],
                    "venue_id": venue["venueId"],
                    "venue_name": venue["venueName"],
                    "sports_name": sports_name,
                }
                venue_list.append((venue["agencyName"], json.dumps(venue_dict)))

            venue_select: Select = self.query_one("#ground-venue-select")
            venue_select.set_options(venue_list)

    @on(Select.Changed, "#ground-venue-select")
    def clear_previous_selection(self):
        self.grounds_selected = []
        self._update_candidate_summary()
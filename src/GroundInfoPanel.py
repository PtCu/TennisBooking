import asyncio
import json
import logging
import random
import threading
from datetime import datetime

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Middle, ScrollableContainer, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static, Switch

from GroundSelectScreen import GROUP_BADGES, GroundSelectScreen
from JSApi import JSApi, TokenExpiredError
from widgets.TimeSelect import TimeSelect

logger = logging.getLogger(__name__)


class WaitlistPromptScreen(ModalScreen[bool]):
    DEFAULT_CSS = """
    WaitlistPromptScreen { align: center middle; }
    #wl-dialog {
        width: 68;
        max-width: 92%;
        height: auto;
        padding: 1 2;
        border: round $warning;
        background: $surface;
    }
    #wl-actions { height: auto; margin-top: 1; }
    #wl-actions Button { margin: 0 2; }
    """

    def __init__(self, summary: str):
        super().__init__()
        self.summary = summary

    def compose(self) -> ComposeResult:
        with Vertical(id="wl-dialog"):
            yield Static(
                f"目标场次已售罄 / 暂未抢到！\n\n"
                f"方案: {self.summary}\n\n"
                f"是否像 12306 一样转入「候补捡漏」？\n"
                f"开启后将持续随机间隔监控上述场次，一旦有人退场释放，立即自动下单并推送微信提醒。",
                markup=False,
            )
            with Horizontal(id="wl-actions"):
                yield Button("立即转入候补", id="wl-yes", variant="primary")
                yield Button("暂不候补", id="wl-no")

    @on(Button.Pressed, "#wl-yes")
    def accept(self):
        self.dismiss(True)

    @on(Button.Pressed, "#wl-no")
    def decline(self):
        self.dismiss(False)


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
        self.selected_plans: list[dict] = []

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
            yield Middle(Button("定时开抢", id="order-button"))
            yield Middle(Button("立即抢/候补", id="order-now-button"))
            yield Middle(Button("停止", id="order-stop-button"))

        with Horizontal():
            yield Middle(Label("重试:"), id="retry-label")
            yield Middle(Input(value="10", type="integer", id="retry-input"))
            yield Middle(Label("间隔(ms):"), id="delay-label")
            yield Middle(Input(value="250", type="integer", id="delay-input"))
            yield Middle(Label("偏差(%):"), id="jitter-label")
            yield Middle(Input(value="10", type="integer", id="jitter-input"))
            yield Middle(Label("预算上限:"))
            yield Middle(Input(value="", placeholder="不限", type="integer", id="budget-input"))
            yield Middle(Label("演练模式:"))
            yield Middle(Switch(value=False, id="dry-run-switch"))

        yield Label(
            "未选择场地（选好场馆后点「选择场地」，可涂色分组或点「智能配组」）",
            id="ground-selected-summary",
        )

    def app_load_done(self) -> None:
        self.js_api: JSApi = self.app.js_api

    def _format_plans_summary(self, plans: list[dict]) -> str:
        parts = []
        for p in plans:
            badge = GROUP_BADGES.get(p["group_id"], f"[{p['group_id']}]")
            tag = "(候补)" if p.get("is_waitlist") else ""
            sdesc = "+".join(
                f"{s['groundName']} {datetime.fromtimestamp(int(s['startTime'])/1000):%m-%d %H:%M}"
                for s in p["slots"]
            )
            ptxt = f" ¥{p['total_price']:g}" if p.get("total_price", 0) > 0 else ""
            parts.append(f"{badge}{tag}[{sdesc}{ptxt}]")
        return " → ".join(parts)

    def _update_summary(self):
        lbl = self.query_one("#ground-selected-summary", Label)
        if not self.selected_plans:
            lbl.update("未选择场地（选好场馆后点「选择场地」，可涂色分组或用「智能配组」）")
            return
        lbl.update("备选队列: " + self._format_plans_summary(self.selected_plans))

    def _read_budget_limit(self) -> float:
        raw = self.query_one("#budget-input", Input).value.strip()
        if raw.isdigit() and int(raw) > 0:
            return float(raw)
        return float("inf")

    @work
    @on(Button.Pressed, "#ground-button")
    async def show_ground_select_screen(self):
        b = self._read_budget_limit()
        res = await self.app.push_screen_wait(
            GroundSelectScreen(default_budget=(0.0 if b == float("inf") else b))
        )
        if res is not None:
            self.selected_plans = res
            self._update_summary()

    @on(Button.Pressed, "#order-stop-button")
    def stop_order(self):
        if self.task_busy:
            self.stop_event.set()
            logger.error("正在停止抢场 / 候补任务...")
        else:
            logger.error("当前没有运行中的任务")

    @on(Button.Pressed, "#order-button")
    def start_scheduled(self):
        self._run_order(mode="scheduled")

    @on(Button.Pressed, "#order-now-button")
    def start_immediate(self):
        self._run_order(mode="immediate")

    async def _interruptible_sleep(self, seconds: float) -> bool:
        end = asyncio.get_event_loop().time() + seconds
        while not self.stop_event.is_set():
            rem = end - asyncio.get_event_loop().time()
            if rem <= 0:
                return True
            await asyncio.sleep(min(0.3, rem))
        return False

    async def _run_waitlist_loop(
        self,
        plans: list[dict],
        venue_info: dict,
        contact_id: str,
        budget_limit: float,
        dry_run: bool,
    ) -> bool:
        logger.error(
            f"进入 12306 式【候补捡漏】模式，共盯防 {len(plans)} 组方案（点击「停止」可随时退出）..."
        )
        round_num = 0
        refreshed_once = False
        day_timestamps = sorted({int(p["book_date_ts"]) for p in plans})

        while not self.stop_event.is_set():
            round_num += 1
            try:
                # 拉取目标日期的实时场地状态
                live_map: dict[tuple[str, str], dict] = {}
                for day_ts in day_timestamps:
                    if self.stop_event.is_set():
                        return False
                    rows = await self.js_api.get_ground(venue_info["venue_id"], day_ts)
                    for row in rows or []:
                        st = str(row["startTime"])
                        for blk in row.get("blockModel", []):
                            live_map[(str(blk["groundId"]), st)] = blk

                # 按 ①→②→③→④ 优先顺序检查是否整组全部释放且不超预算
                for plan in plans:
                    if self.stop_event.is_set():
                        return False
                    ready_slots = []
                    total_p = 0.0
                    all_ok = True
                    for s in plan["slots"]:
                        blk = live_map.get((str(s["groundId"]), str(s["startTime"])))
                        if (
                            not blk
                            or str(blk.get("status")) != "1"
                            or float(blk.get("price", -1)) < 0
                        ):
                            all_ok = False
                            break
                        total_p += float(blk["price"])
                        ready_slots.append(
                            {
                                "groundId": str(s["groundId"]),
                                "groundName": s["groundName"],
                                "startTime": str(s["startTime"]),
                                "endTime": str(s["endTime"]),
                                "sportsType": str(s["sportsType"]),
                            }
                        )
                    if not all_ok:
                        continue
                    if total_p > budget_limit:
                        logger.error(
                            f"候补发现退票但总价 ¥{total_p:g} 超过预算 ¥{budget_limit:g}，跳过"
                        )
                        continue

                    badge = GROUP_BADGES.get(plan["group_id"], "")
                    logger.error(f"🎯 候补第 {round_num} 轮命中退票！正在秒抢 {badge} (¥{total_p:g})...")
                    booked = await self.js_api.post_book(
                        contact_id=contact_id,
                        venue_id=venue_info["venue_id"],
                        venue_name=venue_info["venue_name"],
                        agency_id=venue_info["agency_id"],
                        agency_name=venue_info["agency_name"],
                        sports_name=venue_info["sports_name"],
                        ground_list=ready_slots,
                        dry_run=dry_run,
                    )
                    if booked:
                        return True
                    logger.error("该退票刚被别人先一步锁走，继续候补盯防...")

                if round_num % 10 == 1:
                    logger.error(f"候补盯防中（第 {round_num} 轮刷新完成，暂无退票释放）...")
                refreshed_once = False
            except TokenExpiredError:
                if not refreshed_once:
                    refreshed_once = True
                    logger.error("候补期间检测到 Token 过期，正在尝试从微信内存自动续期...")
                    if await asyncio.to_thread(self.js_api.get_token, False):
                        continue
                logger.error("Token 已过期且自动续期失败，请点「登录」后重启候补")
                self.post_message(self.LoggedStatusChanged(self, False))
                return False
            except Exception as e:
                logger.error(f"候补轮询异常（稍后继续）: {e}")

            wait_s = random.uniform(3.5, 7.0)
            if dry_run:
                logger.error("[仅演练] 候补轮询单轮验证完成，演练自动结束")
                return False
            if not await self._interruptible_sleep(wait_s):
                return False
        return False

    @work(group="book-order")
    async def _run_order(self, mode: str = "scheduled"):
        if self.task_busy:
            logger.error("已有任务在运行，如需重设请先点「停止」")
            return

        hour_expected = int(self.query_one("#hour-select", Select).value)
        minute_expected = int(self.query_one("#minute-select", Select).value)
        second_expected = int(self.query_one("#second-select", Select).value)
        max_retries = max(1, int(self.query_one("#retry-input", Input).value or "10"))
        delay = max(50, int(self.query_one("#delay-input", Input).value or "250"))
        jitter_percent = max(0, int(self.query_one("#jitter-input", Input).value or "10"))
        budget_limit = self._read_budget_limit()
        dry_run = self.query_one("#dry-run-switch", Switch).value

        contact_select: Select = self.app.query_one("#user-contact-select")
        venue_select: Select = self.query_one("#ground-venue-select")
        contact_id = contact_select.value

        try:
            venue_info = json.loads(venue_select.value)
        except Exception:
            logger.error("请确认场馆信息是否正确")
            return

        if not self.selected_plans:
            logger.error("请先点击「选择场地」涂选方案或点「智能配组」")
            return
        if contact_select.is_blank() or not isinstance(contact_id, str):
            logger.error("请先选择联系人")
            return

        # 按预算过滤一次方案
        active_plans = []
        for p in self.selected_plans:
            if p.get("total_price", 0) > budget_limit:
                logger.error(
                    f"方案 {GROUP_BADGES[p['group_id']]} 总价 ¥{p['total_price']:g} 超过预算 ¥{budget_limit:g}，已跳过"
                )
            else:
                active_plans.append(p)
        if not active_plans:
            logger.error("所有所选方案均超出预算上限，请调整预算或重新选场")
            return

        self.task_busy = True
        self.stop_event.clear()
        try:
            if mode == "waitlist":
                await self._run_waitlist_loop(active_plans, venue_info, contact_id, budget_limit, dry_run)
                return

            if mode == "scheduled":
                now = datetime.now()
                target_time = now.replace(
                    hour=hour_expected,
                    minute=minute_expected,
                    second=second_expected,
                    microsecond=0,
                )
                if target_time < now:
                    logger.error("当前已过设定时间；若要现在开抢或候补，请直接点「立即抢/候补」")
                    return
                wait_seconds = (target_time - now).total_seconds()
                logger.error(
                    f"将在 {hour_expected:02}:{minute_expected:02}:{second_expected:02} 按 ①→②→③→④ 顺序开抢（距离现在 {wait_seconds:.2f} 秒）"
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
                logger.error("到点，开始按备选队列发送订单！")

            bookable_plans = [p for p in active_plans if not p.get("is_waitlist")]
            booked_any = False

            if bookable_plans:
                for attempt in range(1, max_retries + 1):
                    if self.stop_event.is_set():
                        logger.error("已停止抢场")
                        return
                    for plan in bookable_plans:
                        if self.stop_event.is_set():
                            return
                        badge = GROUP_BADGES[plan["group_id"]]
                        payload_slots = [
                            {
                                "groundId": s["groundId"],
                                "groundName": s["groundName"],
                                "startTime": s["startTime"],
                                "endTime": s["endTime"],
                                "sportsType": s["sportsType"],
                            }
                            for s in plan["slots"]
                        ]
                        logger.error(f"第 {attempt}/{max_retries} 轮尝试方案 {badge}...")
                        ok = await self.js_api.post_book(
                            contact_id=contact_id,
                            venue_id=venue_info["venue_id"],
                            venue_name=venue_info["venue_name"],
                            agency_id=venue_info["agency_id"],
                            agency_name=venue_info["agency_name"],
                            sports_name=venue_info["sports_name"],
                            ground_list=payload_slots,
                            dry_run=dry_run,
                        )
                        if ok:
                            booked_any = True
                            return
                    if attempt < max_retries:
                        jitter_ms = random.uniform(
                            -jitter_percent / 100.0 * delay,
                            jitter_percent / 100.0 * delay,
                        )
                        await self._interruptible_sleep(max(0.05, (delay + jitter_ms) / 1000.0))

            if not booked_any and not self.stop_event.is_set():
                if not bookable_plans:
                    logger.error("所选方案当前全部已售罄，直接为您开启 12306 式【候补捡漏】蹲退票...")
                    await self._run_waitlist_loop(active_plans, venue_info, contact_id, budget_limit, dry_run)
                else:
                    logger.error("本轮备选队列均未抢到！")
                    summary = self._format_plans_summary(active_plans)
                    go_wl = await self.app.push_screen_wait(WaitlistPromptScreen(summary))
                    if go_wl:
                        await self._run_waitlist_loop(active_plans, venue_info, contact_id, budget_limit, dry_run)
                    else:
                        logger.error("已结束本轮抢票")
        except TokenExpiredError:
            logger.error("Token 已过期，请重新登录")
            self.post_message(self.LoggedStatusChanged(self, False))
        except Exception as e:
            logger.error(f"预定过程异常: {e}")
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

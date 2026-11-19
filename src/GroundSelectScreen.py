import json
import logging
from datetime import datetime

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, TabbedContent, TabPane

from JSApi import JSApi, TokenExpiredError
from widgets.Table import TabelCell, TableRow

logger = logging.getLogger(__name__)

GROUP_BADGES = {1: "①", 2: "②", 3: "③", 4: "④"}
GROUP_NAMES = {1: "①首选", 2: "②备选", 3: "③备选", 4: "④备选"}


class GroundButton(Button):
    def __init__(self, variant="default", ground_info=None, preview: bool = False, *args, **kwargs):
        self.ground_info = ground_info
        self.preview = preview
        self.group_id: int | None = None
        self.available = (
            preview
            or (
                str(ground_info.get("status")) == "1"
                and float(ground_info.get("price", -1)) >= 0
            )
        )
        self.is_waitlist_slot = not self.available

        super().__init__(self._format_label(), variant, *args, **kwargs)
        self._refresh_style()

    def _format_label(self) -> str:
        badge = GROUP_BADGES[self.group_id] if self.group_id else ""
        if self.preview:
            p = self.ground_info.get("price", "")
            base = f"待放¥{float(p):g}" if str(p) not in ("", "-1", "-1.0") else "待放"
            return f"{badge}{base}" if badge else base
        if self.available:
            price_str = f"¥{float(self.ground_info['price']):g}"
            return f"{badge}{price_str}" if badge else price_str
        return f"补{badge}" if badge else "已订"

    def _refresh_style(self) -> None:
        for cls in ("booked", "preview", "waitlist-selected", "grp-1", "grp-2", "grp-3", "grp-4"):
            self.remove_class(cls)
        if self.group_id:
            self.add_class(f"grp-{self.group_id}")
            if self.is_waitlist_slot:
                self.add_class("waitlist-selected")
        else:
            if self.preview:
                self.add_class("preview")
            elif not self.available:
                self.add_class("booked")

    def set_group(self, group_id: int | None) -> None:
        self.group_id = group_id
        self.label = self._format_label()
        self._refresh_style()

    @on(Button.Pressed)
    def ground_button_pressed(self):
        screen = self.screen
        active_group = getattr(screen, "active_group", 1)
        if self.group_id == active_group:
            self.set_group(None)
        else:
            self.set_group(active_group)
        if hasattr(screen, "_update_group_preview"):
            screen._update_group_preview()


class GroundTable(ScrollableContainer):
    def __init__(self, timestamp, id=None):
        super().__init__(id=id)
        self.timestamp = timestamp


class GroundSelectScreen(ModalScreen):
    BINDINGS = [
        ("1", "pick_group(1)", "画笔1"),
        ("2", "pick_group(2)", "画笔2"),
        ("3", "pick_group(3)", "画笔3"),
        ("4", "pick_group(4)", "画笔4"),
    ]

    class LoggedStatusChanged(Message):
        def __init__(self, sender: "GroundSelectScreen", logged_status: bool) -> None:
            self.sender = sender
            self.logged_status = logged_status
            super().__init__()

        @property
        def control(self) -> "GroundSelectScreen":
            return self.sender

    def __init__(self, default_budget: float = 0.0, default_time_range: str = "18:00-22:00", *children, **kwargs):
        super().__init__(*children, **kwargs)
        self.js_api: JSApi = self.app.js_api
        self._template_rows = None
        self.active_group: int = 1
        self.default_budget = default_budget
        self.default_time_range = default_time_range

    def action_pick_group(self, gid: int) -> None:
        self.active_group = gid
        for i in (1, 2, 3, 4):
            btn = self.query_one(f"#brush-{i}", Button)
            if i == gid:
                btn.add_class("brush-active")
            else:
                btn.remove_class("brush-active")

    def timestamp_to_label(self, timestamp: int):
        dt = datetime.fromtimestamp(timestamp / 1000)
        date_str = f"{dt.month:02}-{dt.day:02}"
        if dt.date() == datetime.today().date():
            weekday_str = "今天"
        else:
            weekday_str = {
                1: "星期一",
                2: "星期二",
                3: "星期三",
                4: "星期四",
                5: "星期五",
                6: "星期六",
                7: "星期日",
            }[dt.isoweekday()]
        return f"{date_str}\n{weekday_str}"

    def compose(self) -> ComposeResult:
        with Vertical(id="ground-select-container"):
            yield Label(
                "涂色规则: 同色(同组)=打包一起下单；异色(①→②→③→④)=备选方案(抢中任一组即停)；「已订」格子也可涂色作候补盯防",
                id="ground-select-tip",
            )
            with Horizontal(id="ground-brush-bar"):
                for i in (1, 2, 3, 4):
                    cls = f"brush-btn brush-{i}" + (" brush-active" if i == 1 else "")
                    yield Button(GROUP_NAMES[i], id=f"brush-{i}", classes=cls)
                yield Label(" 时段:")
                yield Input(value=self.default_time_range, placeholder="18:00-22:00", id="smart-time-input")
                yield Label(" 预算:")
                yield Input(
                    value=str(int(self.default_budget)) if self.default_budget > 0 else "",
                    placeholder="不限",
                    type="integer",
                    id="smart-budget-input",
                )
                yield Button("智能配组", id="smart-recommend-button")
                yield Button("清空", id="clear-groups-button")

            with TabbedContent(id="ground-tabs"):
                timestamps = self.js_api.get_timestamps_from_now(days=8)
                for timestamp in timestamps:
                    tab_label = self.timestamp_to_label(timestamp)
                    with TabPane(tab_label, id=f"ground-tab-{timestamp}"):
                        yield Vertical(id="ground-time-container")
                        yield GroundTable(timestamp, id=f"ground-table-{timestamp}")

            yield Label("当前未涂选任何方案（可点「智能配组」或手动点选格子）", id="ground-group-preview")

            with Horizontal(id="ground-buttons-container"):
                yield Button("确定", id="ground-confirm-button")
                yield Button("取消", id="ground-cancel-button")

    @on(Button.Pressed, "#brush-1")
    def _b1(self):
        self.action_pick_group(1)

    @on(Button.Pressed, "#brush-2")
    def _b2(self):
        self.action_pick_group(2)

    @on(Button.Pressed, "#brush-3")
    def _b3(self):
        self.action_pick_group(3)

    @on(Button.Pressed, "#brush-4")
    def _b4(self):
        self.action_pick_group(4)

    @on(Button.Pressed, "#clear-groups-button")
    def clear_all_selections(self):
        for btn in self.query(GroundButton):
            if btn.group_id is not None:
                btn.set_group(None)
        self._update_group_preview()

    def _parse_time_window(self, text: str) -> tuple[int, int]:
        try:
            parts = text.strip().replace("：", ":").split("-")
            if len(parts) == 2:
                h1 = int(parts[0].split(":")[0])
                h2 = int(parts[1].split(":")[0])
                return h1, h2
        except Exception:
            pass
        return 0, 24

    @on(Button.Pressed, "#smart-recommend-button")
    def smart_recommend(self):
        tabs = self.query_one("#ground-tabs", TabbedContent)
        pane = tabs.active_pane
        if pane is None:
            return
        buttons = list(pane.query(GroundButton))
        if not buttons:
            return

        for b in buttons:
            if b.group_id is not None:
                b.set_group(None)

        h_start, h_end = self._parse_time_window(self.query_one("#smart-time-input", Input).value)
        budget_raw = self.query_one("#smart-budget-input", Input).value.strip()
        max_budget = float(budget_raw) if budget_raw.isdigit() and int(budget_raw) > 0 else float("inf")

        def _hour_of(btn: GroundButton) -> int:
            return datetime.fromtimestamp(int(btn.ground_info["start_time"]) / 1000).hour

        def _price_of(btn: GroundButton) -> float:
            try:
                p = float(btn.ground_info.get("price", 0))
                return p if p >= 0 else 0.0
            except Exception:
                return 0.0

        in_window = [b for b in buttons if h_start <= _hour_of(b) < h_end]
        if not in_window:
            in_window = buttons

        avail_btns = [b for b in in_window if b.available]
        pool = avail_btns if avail_btns else in_window

        by_court: dict[str, list[GroundButton]] = {}
        for b in pool:
            by_court.setdefault(str(b.ground_info["groundName"]), []).append(b)
        for lst in by_court.values():
            lst.sort(key=lambda x: int(x.ground_info["start_time"]))

        candidate_groups: list[tuple[int, float, int, list[GroundButton]]] = []
        for _court_name, lst in by_court.items():
            for a, b in zip(lst[:-1], lst[1:]):
                if int(a.ground_info["end_time"]) == int(b.ground_info["start_time"]):
                    total_p = _price_of(a) + _price_of(b)
                    if total_p <= max_budget:
                        score_h = abs(_hour_of(a) - 19)
                        candidate_groups.append((0, score_h, int(total_p), [a, b]))

        if len(candidate_groups) < 4:
            used_ids = {id(btn) for _, _, _, grp in candidate_groups for btn in grp}
            for b in pool:
                if id(b) in used_ids:
                    continue
                p = _price_of(b)
                if p <= max_budget:
                    candidate_groups.append((1, abs(_hour_of(b) - 19), int(p), [b]))

        candidate_groups.sort(key=lambda x: (x[0], x[1], x[2]))
        assigned = 0
        used_btns: set[int] = set()
        for _, _, _, grp_btns in candidate_groups:
            if any(id(b) in used_btns for b in grp_btns):
                continue
            assigned += 1
            if assigned > 4:
                break
            for b in grp_btns:
                b.set_group(assigned)
                used_btns.add(id(b))

        self._update_group_preview()
        if assigned == 0:
            self.query_one("#ground-group-preview", Label).update(
                "当前预算或时段内没有匹配场次，请放宽时段或提高预算后再试"
            )
        elif not avail_btns:
            logger.error(f"目标时段当前已售罄：已智能圈选 {assigned} 组【候补盯防】目标")

    def _day_midnight_ts(self, ts_ms: int) -> int:
        dt = datetime.fromtimestamp(ts_ms / 1000).replace(hour=0, minute=0, second=0, microsecond=0)
        return int(dt.timestamp()) * 1000

    def _collect_groups(self) -> list[dict]:
        grouped: dict[int, list[GroundButton]] = {}
        for btn in self.query(GroundButton):
            if btn.group_id is not None:
                grouped.setdefault(btn.group_id, []).append(btn)

        plans: list[dict] = []
        for gid in sorted(grouped.keys()):
            btns = sorted(grouped[gid], key=lambda b: int(b.ground_info["start_time"]))
            slots = []
            total_price = 0.0
            has_waitlist = False
            for b in btns:
                info = b.ground_info
                if b.is_waitlist_slot:
                    has_waitlist = True
                try:
                    p = float(info.get("price", 0))
                    if p > 0:
                        total_price += p
                except Exception:
                    pass
                slots.append(
                    {
                        "groundId": str(info["groundId"]),
                        "groundName": info["groundName"],
                        "startTime": str(info["start_time"]),
                        "endTime": str(info["end_time"]),
                        "sportsType": str(info.get("sportsType", "11")),
                        "price": str(info.get("price", "0")),
                    }
                )
            plans.append(
                {
                    "group_id": gid,
                    "slots": slots,
                    "is_waitlist": has_waitlist,
                    "total_price": total_price,
                    "book_date_ts": self._day_midnight_ts(int(slots[0]["startTime"])),
                }
            )
        return plans

    def _update_group_preview(self) -> None:
        plans = self._collect_groups()
        lbl = self.query_one("#ground-group-preview", Label)
        if not plans:
            lbl.update("当前未涂选任何方案（可点「智能配组」或手动点选格子）")
            return
        parts = []
        for p in plans:
            badge = GROUP_BADGES[p["group_id"]]
            tag = "(候补)" if p["is_waitlist"] else ""
            slot_desc = "+".join(
                f"{s['groundName']} {datetime.fromtimestamp(int(s['startTime'])/1000):%H:%M}"
                for s in p["slots"]
            )
            price_txt = f" ¥{p['total_price']:g}" if p["total_price"] > 0 else ""
            parts.append(f"{badge}{tag}[{slot_desc}{price_txt}]")
        lbl.update("备选顺序: " + "  →  ".join(parts))

    @on(Button.Pressed, "#ground-confirm-button")
    def return_grounds_selected(self) -> None:
        plans = self._collect_groups()
        if not plans:
            logger.warning("未选择任何场地")
        else:
            desc = " | ".join(
                f"{GROUP_BADGES[p['group_id']]}"
                + ("(候补)" if p["is_waitlist"] else "")
                + "+".join(
                    f"{s['groundName']} {datetime.fromtimestamp(int(s['startTime'])/1000):%m-%d %H:%M}"
                    for s in p["slots"]
                )
                for p in plans
            )
            logger.error(f"已选方案（按顺序抢中即停）: {desc}")
        self.dismiss(plans)

    @on(Button.Pressed, "#ground-cancel-button")
    def cancel(self) -> None:
        self.dismiss(None)

    @work
    @on(TabbedContent.TabActivated)
    async def show_grounds_info(self, event: TabbedContent.TabActivated) -> None:
        table: GroundTable = event.pane.query_one(GroundTable)
        table.remove_children()

        time_container: Vertical = event.pane.query_one("#ground-time-container")
        time_container.remove_children()

        venue_select: Select = self.app.query_one("#ground-venue-select")
        if venue_select.is_blank() or not isinstance(venue_select.value, str):
            table.mount(Label("请先在主界面选择场馆"))
            return
        venue_info = json.loads(venue_select.value)
        venue_id = venue_info["venue_id"]

        dt_starts, ground_dict, is_preview = await self.get_ground_dict(venue_id, table.timestamp)
        if not dt_starts or not ground_dict:
            table.mount(Label("当前日期暂无可显示时段"))
            return

        ground_name_labels = [
            Label(ground_name, classes="ground-name")
            for ground_name in ground_dict.keys()
        ]
        table.mount(TableRow(*ground_name_labels, id="ground-name-row"))

        button_containers: dict[datetime, dict[str, TabelCell]] = {}
        for dt_start in dt_starts:
            button_containers.setdefault(dt_start, {})
            time_container.mount(Label(dt_start.strftime("%H:%M"), classes="ground-time"))
            row = TableRow()
            table.mount(row)
            for ground_name in ground_dict.keys():
                button_container = TabelCell()
                button_containers[dt_start][ground_name] = button_container
                row.mount(button_container)

        for ground_name, grounds in ground_dict.items():
            for ground in grounds:
                dt_start = datetime.fromtimestamp(ground["start_time"] / 1000)
                cell = button_containers.get(dt_start, {}).get(ground_name)
                if cell is not None:
                    cell.mount(GroundButton(ground_info=ground, preview=is_preview))

    async def get_ground_dict(self, venue_id, timestamp):
        grounds_dict = {}
        dt_starts = []

        try:
            ground_infos = await self.js_api.get_ground(venue_id, timestamp)
        except TokenExpiredError:
            logger.error("Token 已过期，请重新登录")
            self.post_message(self.LoggedStatusChanged(self, False))
            return dt_starts, grounds_dict, False
        except Exception as e:
            logger.error("获取场地信息失败")
            logger.debug(e, exc_info=True)
            return dt_starts, grounds_dict, False

        if ground_infos:
            self._template_rows = ground_infos
            is_preview = False
        else:
            is_preview = True
            if not self._template_rows:
                tomorrow_ts = self.js_api.get_timestamps_from_now(days=8)[1]
                try:
                    self._template_rows = await self.js_api.get_ground(venue_id, tomorrow_ts)
                except Exception:
                    self._template_rows = []
            if self._template_rows:
                tpl_day = datetime.fromtimestamp(int(self._template_rows[0]["startTime"]) / 1000).date()
                target_day = datetime.fromtimestamp(timestamp / 1000).date()
                delta_ms = (target_day - tpl_day).days * 86400 * 1000
                ground_infos = []
                for row in self._template_rows:
                    copied = dict(row)
                    copied["startTime"] = str(int(row["startTime"]) + delta_ms)
                    copied["endTime"] = str(int(row["endTime"]) + delta_ms)
                    copied["blockModel"] = [dict(b) for b in row.get("blockModel", [])]
                    ground_infos.append(copied)

        now = datetime.now()
        for info in ground_infos or []:
            start_time = int(info["startTime"])
            end_time = int(info["endTime"])
            dt_start = datetime.fromtimestamp(start_time / 1000)
            if not is_preview and dt_start < now:
                continue
            dt_starts.append(dt_start)
            for ground in info["blockModel"]:
                g_copy = dict(ground)
                g_copy["start_time"] = start_time
                g_copy["end_time"] = end_time
                grounds_dict.setdefault(g_copy["groundName"], []).append(g_copy)
        return dt_starts, grounds_dict, is_preview
